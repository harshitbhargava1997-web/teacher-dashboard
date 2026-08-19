import os
import re
import time
import json
import sys
from datetime import datetime
from io import BytesIO
import pandas as pd
import numpy as np
from playwright.sync_api import sync_playwright
from supabase import create_client

sys.stdout.reconfigure(line_buffering=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip('/')
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BUCKET_NAME = os.getenv("BUCKET_NAME", "academic-dashboard")
PARQUET_FILE_NAME = "master_database.parquet"
CONSULTANTS_JSON = os.getenv("CONSULTANTS_JSON", "[]")

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Supabase Client Notice: {e}", flush=True)
    supabase = None

os.makedirs("debug_screenshots", exist_ok=True)


def wait_for_ui_stabilization(page, max_wait_sec=20):
    """Waits for Angular/React animations, loading skeletons, and HTTP requests to settle."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    spinner_selectors = [
        'div[class*="spinner"]', 'div[class*="loader"]', 
        'div[class*="loading"]', 'mat-spinner', 'mat-progress-spinner', 
        '[role="progressbar"]', '.ngx-spinner-overlay'
    ]
    for _ in range(max_wait_sec):
        is_spinning = False
        for s in spinner_selectors:
            try:
                el = page.locator(s).first
                if el.is_visible():
                    is_spinning = True
                    break
            except Exception:
                pass
        if not is_spinning:
            break
        time.sleep(1)
    time.sleep(2)


def normalize_exported_dataframe(df_raw, school_name, consultant_name, state_zone):
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    df = df_raw.copy()
    if 'TEACHER' in df.columns and 'FullName' not in df.columns:
        df['FullName'] = df['TEACHER']

    if 'Institution' not in df.columns or df['Institution'].fillna('').eq('').all():
        df['Institution'] = school_name
    else:
        df['Institution'] = df['Institution'].fillna(school_name).replace('', school_name)

    for col in ["Institution", "Center", "FirstName", "LastName", "FullName", "Role", "Uploaded_By", "State_Zone"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()

    df.loc[df["State_Zone"].eq(""), "State_Zone"] = state_zone
    df.loc[df["Uploaded_By"].eq(""), "Uploaded_By"] = consultant_name

    calculated_full = (df["FirstName"].fillna("") + " " + df["LastName"].fillna("")).str.strip()
    empty_full = df["FullName"].eq("")
    df.loc[empty_full, "FullName"] = calculated_full.loc[empty_full]
    df.loc[df["FullName"].eq(""), "FullName"] = "Unknown Teacher"

    for col in ['Grade', 'Subject', 'Book']:
        if col not in df.columns:
            df[col] = ''
        else:
            df[col] = df[col].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())

    def parse_time_mins(t_str):
        try:
            parts = str(t_str).split(':')
            return int(parts[0]) * 60 + int(parts[1]) + float(parts[2]) / 60.0
        except Exception:
            return 0.0

    if 'Duration (HH:MM:SS)' in df.columns:
        df['Duration_Min'] = df['Duration (HH:MM:SS)'].apply(parse_time_mins)
    elif 'Duration (Minutes)' in df.columns:
        df['Duration_Min'] = pd.to_numeric(df['Duration (Minutes)'], errors='coerce').fillna(0.0)
    elif 'HOURS' in df.columns and 'MINUTES' in df.columns and 'SECONDS' in df.columns:
        h = pd.to_numeric(df['HOURS'], errors='coerce').fillna(0.0)
        m = pd.to_numeric(df['MINUTES'], errors='coerce').fillna(0.0)
        s = pd.to_numeric(df['SECONDS'], errors='coerce').fillna(0.0)
        df['Duration_Min'] = (h * 60.0) + m + (s / 60.0)
    else:
        df['Duration_Min'] = 0.0

    for dt_col in ['StartTime', 'EndTime']:
        if dt_col in df.columns:
            df[dt_col] = pd.to_datetime(df[dt_col], errors='coerce')

    return df


def perform_portal_login(page, email, password):
    print("1. Opening Admin Portal (https://admin.onelern.school)...", flush=True)
    page.goto("https://admin.onelern.school", wait_until="domcontentloaded", timeout=60000)
    wait_for_ui_stabilization(page)

    email_field = page.locator('input[placeholder*="email" i], input[type="email"], input[name="email"]').first
    email_field.wait_for(state="visible", timeout=30000)
    print(f"   Entering Email: {email}...", flush=True)
    email_field.click()
    email_field.fill(email)
    email_field.press("Tab")
    time.sleep(1)

    pass_field = page.locator('input[placeholder*="password" i], input[type="password"], input[name="password"]').first
    pass_field.wait_for(state="visible", timeout=30000)
    print("   Entering Password...", flush=True)
    pass_field.click()
    pass_field.fill(password)
    pass_field.press("Tab")
    time.sleep(1)

    login_btn = page.locator('button:has-text("Login"), button[type="submit"]').first
    login_btn.click()
    
    print("   Waiting for authenticated Dashboard...", flush=True)
    wait_for_ui_stabilization(page, max_wait_sec=25)
    page.screenshot(path="debug_screenshots/01_authenticated_dashboard.png")
    print("   ✅ Authenticated successfully.", flush=True)


def navigate_to_module(page, module_name):
    print(f"2. Navigating to Reports -> {module_name}...", flush=True)
    path = "content" if module_name.lower() == "content" else "platform-reports"
    target_url = f"https://admin.onelern.school/reports/{path}"
    
    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
    wait_for_ui_stabilization(page, max_wait_sec=20)
    page.screenshot(path=f"debug_screenshots/02_module_{module_name}.png")


def extract_school_names(page):
    print("3. Scanning assigned schools from UI dropdown...", flush=True)
    schools = []
    try:
        dropdown = page.locator('div[class*="select"], div[role="combobox"], mat-select, select').first
        dropdown.wait_for(state="visible", timeout=20000)
        dropdown.click()
        time.sleep(2)

        options = page.locator('div[role="option"], li[role="option"], mat-option, option, div[class*="option"]').all_text_contents()
        page.keyboard.press("Escape")
        time.sleep(1)
        
        for opt in options:
            clean = opt.strip()
            if clean and "AY2" not in clean and "Academic" not in clean and "Grade" not in clean and "Select" not in clean:
                schools.append(clean)
    except Exception as e:
        print(f"   School scanning notice: {e}", flush=True)

    schools = list(dict.fromkeys(schools))
    if not schools:
        schools = ["Current School"]
    print(f"   Detected schools ({len(schools)}): {schools}", flush=True)
    return schools


def select_school_and_ay(page, school_name, target_ay="AY26-27"):
    if school_name != "Current School":
        try:
            dropdown = page.locator('div[class*="select"], div[role="combobox"], mat-select').first
            dropdown.click()
            time.sleep(1)
            target = page.locator(f'text="{school_name}"').first
            if target.is_visible():
                target.click()
                time.sleep(2)
            else:
                page.keyboard.press("Escape")
        except Exception:
            pass

    try:
        ay_box = page.locator('div:has-text("AY"), mat-select:has-text("AY")').first
        if ay_box.is_visible():
            ay_box.click()
            time.sleep(1)
            ay_opt = page.locator(f'text="{target_ay}"').first
            if ay_opt.is_visible():
                ay_opt.click()
            else:
                page.keyboard.press("Escape")
            time.sleep(1)
    except Exception:
        pass


def apply_date_range(page, start_date_str="07/01/2026"):
    end_date_str = datetime.now().strftime("%m/%d/%Y")
    try:
        date_inputs = page.locator('input[type="text"][placeholder*="202" i], input[type="text"][value*="/"], input[placeholder*="Date" i]').all()
        if len(date_inputs) >= 2:
            date_inputs[0].click()
            date_inputs[0].fill(start_date_str)
            date_inputs[0].press("Enter")
            time.sleep(0.5)

            date_inputs[1].click()
            date_inputs[1].fill(end_date_str)
            date_inputs[1].press("Enter")
            time.sleep(1)
            print(f"       Date filter applied: {start_date_str} to {end_date_str}", flush=True)
    except Exception as e:
        print(f"       Date filter notice: {e}", flush=True)


def switch_to_teachers_tab(page):
    try:
        teacher_pill = page.locator('button:has-text("Teachers"), div:has-text("Teachers"), span:has-text("Teachers"), a:has-text("Teachers")').first
        if teacher_pill.is_visible():
            teacher_pill.click()
            wait_for_ui_stabilization(page, max_wait_sec=15)
            return True
    except Exception as e:
        print(f"       Teachers tab notice: {e}", flush=True)
    return False


def export_report_data(page, school_name, consultant_name, state_zone, report_name):
    try:
        export_btn = page.locator('button:has-text("Export"), div:has-text("Export"), a:has-text("Export")').first
        export_btn.wait_for(state="visible", timeout=25000)

        with page.expect_download(timeout=60000) as download_info:
            export_btn.click()
        download = download_info.value
        df_raw = pd.read_excel(download.path())
        cleaned = normalize_exported_dataframe(df_raw, school_name, consultant_name, state_zone)
        print(f"       ✅ Successfully exported {len(cleaned)} records for {school_name} ({report_name}).", flush=True)
        return cleaned
    except Exception as e:
        print(f"       Export notice for {school_name} ({report_name}): {e}", flush=True)
        page.screenshot(path=f"debug_screenshots/export_failed_{school_name}_{report_name}.png")
        return None


def download_data_for_consultant(browser, consultant_info):
    consultant_name = consultant_info.get("name", "Consultant")
    state_zone = consultant_info.get("state_zone", "Madhya Pradesh (MP)")
    email = consultant_info.get("email", "").strip()
    password = consultant_info.get("password", "").strip()

    if not email or not password:
        print(f"Skipping {consultant_name}: missing credentials.", flush=True)
        return []

    print(f"\n========================================================", flush=True)
    print(f"Extracting Raw Reports for: {consultant_name}", flush=True)
    print(f"========================================================", flush=True)

    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    consultant_dfs = []

    try:
        perform_portal_login(page, email, password)
        navigate_to_module(page, "Content")
        schools = extract_school_names(page)

        modules = ["Content", "Platform"]

        for school in schools:
            print(f"\n--- Ingesting School: {school} ---", flush=True)
            for mod in modules:
                print(f"  -> Module: {mod}...", flush=True)
                navigate_to_module(page, mod)
                select_school_and_ay(page, school, target_ay="AY26-27")
                apply_date_range(page, start_date_str="07/01/2026")
                switch_to_teachers_tab(page)
                
                df_res = export_report_data(page, school, consultant_name, state_zone, mod)
                if df_res is not None and not df_res.empty:
                    consultant_dfs.append(df_res)

    except Exception as e:
        print(f"Extraction exception for {consultant_name}: {e}", flush=True)
        page.screenshot(path=f"debug_screenshots/error_{consultant_name}.png")
    finally:
        context.close()

    return consultant_dfs


def update_supabase_master_db(new_dfs):
    if not new_dfs:
        print("⚠️ No data rows collected across consultants. Review debug_screenshots artifact.", flush=True)
        return

    if supabase is None:
        raise RuntimeError("Supabase client is not initialized.")

    print("\nMerging raw datasets into Supabase Master Parquet...", flush=True)
    base_df = pd.DataFrame()
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            base_df = pd.read_parquet(BytesIO(response))
            print(f"Existing cloud database rows: {len(base_df)}", flush=True)
    except Exception:
        print("Initializing fresh master database.", flush=True)

    combined_new = pd.concat(new_dfs, ignore_index=True)
    all_data = pd.concat([base_df, combined_new], ignore_index=True) if not base_df.empty else combined_new

    dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution']
    avail_cols = [c for c in dedup_cols if c in all_data.columns]
    master_df = all_data.drop_duplicates(subset=avail_cols, keep='last') if avail_cols else all_data.drop_duplicates()

    parquet_buffer = BytesIO()
    master_df.to_parquet(parquet_buffer, index=False)
    parquet_buffer.seek(0)

    try:
        supabase.storage.from_(BUCKET_NAME).upload(
            path=PARQUET_FILE_NAME,
            file=parquet_buffer.getvalue(),
            file_options={"upsert": "true", "content-type": "application/octet-stream"}
        )
        print(f"\n🎉 SUCCESS: Direct raw reports synced to Supabase! Total records: {len(master_df)}", flush=True)
    except Exception as e:
        raise RuntimeError(f"Supabase upload error: {e}")


if __name__ == "__main__":
    t0 = time.time()
    try:
        consultants_list = json.loads(CONSULTANTS_JSON)
        if not consultants_list:
            print("CONSULTANTS_JSON is empty.", flush=True)
        else:
            all_batches = []
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                )
                for consultant in consultants_list:
                    batch = download_data_for_consultant(browser, consultant)
                    all_batches.extend(batch)
                browser.close()

            update_supabase_master_db(all_batches)
    finally:
        print(f"Total pipeline runtime: {time.time() - t0:.2f} seconds.\n", flush=True)
