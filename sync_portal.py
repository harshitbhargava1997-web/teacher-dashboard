import os
import re
import time
import json
from datetime import datetime
from io import BytesIO
import pandas as pd
import numpy as np
from playwright.sync_api import sync_playwright
from supabase import create_client

# ==============================================================================
# 1. CLOUD STORAGE & CONFIGURATION
# ==============================================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip('/')
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BUCKET_NAME = os.getenv("BUCKET_NAME", "academic-dashboard")
PARQUET_FILE_NAME = "master_database.parquet"
CONSULTANTS_JSON = os.getenv("CONSULTANTS_JSON", "[]")

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Supabase Client Notice: {e}")
    supabase = None

os.makedirs("debug_screenshots", exist_ok=True)


# ==============================================================================
# 2. DATA NORMALIZATION & SANITIZATION
# ==============================================================================
def normalize_identity_columns(df, consultant_name, state_zone):
    out = df.copy()
    for col in ["Institution", "Center", "FirstName", "LastName", "FullName", "Role", "Uploaded_By", "State_Zone"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str).str.strip()

    out.loc[out["State_Zone"].eq(""), "State_Zone"] = state_zone
    out.loc[out["Uploaded_By"].eq(""), "Uploaded_By"] = consultant_name

    calculated_full = (out["FirstName"].fillna("") + " " + out["LastName"].fillna("")).str.strip()
    empty_full = out["FullName"].eq("")
    out.loc[empty_full, "FullName"] = calculated_full.loc[empty_full]
    out.loc[out["FullName"].eq(""), "FullName"] = "Unknown Teacher"
    return out


def clean_report_dataframe(df_raw, school_name, consultant_name, state_zone, report_type_default="lessonDelivery"):
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    df = df_raw.copy()
    if 'TEACHER' in df.columns and 'FullName' not in df.columns:
        df['FullName'] = df['TEACHER']

    if 'Institution' not in df.columns or df['Institution'].fillna('').eq('').all():
        df['Institution'] = school_name
    else:
        df['Institution'] = df['Institution'].fillna(school_name).replace('', school_name)

    df = normalize_identity_columns(df, consultant_name, state_zone)

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

    if 'Type' not in df.columns:
        df['Type'] = report_type_default
    else:
        df['Type'] = df['Type'].fillna(report_type_default).astype(str)

    for dt_col in ['StartTime', 'EndTime']:
        if dt_col in df.columns:
            df[dt_col] = pd.to_datetime(df[dt_col], errors='coerce')

    for qual_col in [
        'Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 
        'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 
        'Phonics_Evidence_Link', 'Portfolio_Evidence_Link', 'Assessment_Score_Pct'
    ]:
        if qual_col not in df.columns:
            df[qual_col] = None

    return df


# ==============================================================================
# 3. PATIENT INTERACTIVE PORTAL ACTIONS (INFINITE WAIT CAPABLE)
# ==============================================================================
def human_like_login(page, email, password):
    print("1. Opening Admin Portal...")
    page.goto("https://admin.onelern.school", wait_until="domcontentloaded", timeout=0)
    
    email_field = page.locator('input[placeholder*="email" i], input[type="email"], input[name="email"]').first
    email_field.wait_for(state="visible", timeout=0)
    email_field.click()
    email_field.fill(email)
    email_field.press("Tab")
    time.sleep(1)

    pass_field = page.locator('input[placeholder*="password" i], input[type="password"], input[name="password"]').first
    pass_field.wait_for(state="visible", timeout=0)
    pass_field.click()
    pass_field.fill(password)
    pass_field.press("Tab")
    time.sleep(1)

    login_btn = page.locator('button:has-text("Login")').first
    login_btn.wait_for(state="visible", timeout=0)
    login_btn.click()
    print("   Login button clicked. Patiently waiting for dashboard elements to load...")

    # Wait indefinitely for dashboard elements (Welcome banner, Reports menu, or School cards)
    dashboard_indicator = page.locator('text="Welcome", text="Reports", text="User Management", text="Home"').first
    dashboard_indicator.wait_for(state="visible", timeout=0)
    print("   ✅ Successfully authenticated and dashboard loaded!")
    time.sleep(3)


def navigate_sidebar_module(page, module_name):
    print(f"2. Navigating to Reports -> {module_name}...")
    reports_menu = page.locator('text="Reports"').first
    reports_menu.wait_for(state="visible", timeout=0)
    reports_menu.click()
    time.sleep(2)

    sub_link = page.locator(f'a:has-text("{module_name}"), span:has-text("{module_name}"), div:has-text("{module_name}")').first
    sub_link.wait_for(state="visible", timeout=0)
    sub_link.click()
    
    # Wait for the report top bar controls to appear
    page.locator('text="Grade", text="Subject", text="Teachers"').first.wait_for(state="visible", timeout=0)
    print(f"   ✅ Loaded {module_name} report view!")
    time.sleep(3)


def extract_all_schools(page):
    school_dropdown = page.locator('div[class*="select"], div[role="combobox"]').first
    school_dropdown.wait_for(state="visible", timeout=0)
    school_dropdown.click()
    time.sleep(2)

    options_locator = page.locator('div[role="option"], li[role="option"], div[class*="option"]')
    options_locator.first.wait_for(state="visible", timeout=0)
    raw_options = options_locator.all_text_contents()
    page.keyboard.press("Escape")
    time.sleep(1)

    schools = []
    for opt in raw_options:
        clean = opt.strip()
        if clean and "AY2" not in clean and "Academic" not in clean and "Grade" not in clean:
            schools.append(clean)

    schools = list(dict.fromkeys(schools))
    if not schools:
        schools = ["Current School"]
    return schools


def select_school_and_ay(page, school_name, target_ay="AY26-27"):
    if school_name != "Current School":
        school_dropdown = page.locator('div[class*="select"], div[role="combobox"]').first
        school_dropdown.wait_for(state="visible", timeout=0)
        school_dropdown.click()
        time.sleep(1)
        
        target_school_opt = page.locator(f'text="{school_name}"').first
        target_school_opt.wait_for(state="visible", timeout=0)
        target_school_opt.click()
        time.sleep(3)

    # Set Academic Year if available
    try:
        ay_box = page.locator('div:has-text("AY")').first
        if ay_box.is_visible():
            ay_box.click()
            time.sleep(1)
            target = page.locator(f'text="{target_ay}", div:has-text("{target_ay}")').first
            if target.is_visible():
                target.click()
            else:
                page.keyboard.press("Escape")
            time.sleep(2)
    except Exception:
        pass


def apply_date_filter_to_today(page, start_date_str="07/01/2026"):
    end_date_str = datetime.now().strftime("%m/%d/%Y")
    date_inputs = page.locator('input[type="text"][placeholder*="202" i], input[type="text"][value*="/"]').all()
    if len(date_inputs) >= 2:
        date_inputs[0].wait_for(state="visible", timeout=0)
        date_inputs[0].click()
        date_inputs[0].fill(start_date_str)
        date_inputs[0].press("Enter")
        time.sleep(1)

        date_inputs[1].wait_for(state="visible", timeout=0)
        date_inputs[1].click()
        date_inputs[1].fill(end_date_str)
        date_inputs[1].press("Enter")
        time.sleep(2)
        print(f"       Date set: {start_date_str} to {end_date_str}")


def click_teachers_tab_and_wait(page):
    teacher_pill = page.locator('button:has-text("Teachers"), div:has-text("Teachers"), span:has-text("Teachers")').first
    teacher_pill.wait_for(state="visible", timeout=0)
    teacher_pill.click()
    
    # Wait for the Teacher table or table headers to render
    page.locator('text="TEACHER", text="HOURS", text="MINUTES", text="Teacher-Wise Reports"').first.wait_for(state="visible", timeout=0)
    time.sleep(3)
    print("       Switched to Teachers view tab.")


def export_and_capture_file(page, school_name, consultant_name, state_zone, report_type):
    export_btn = page.locator('button:has-text("Export"), div:has-text("Export")').first
    export_btn.wait_for(state="visible", timeout=0)
    
    with page.expect_download(timeout=0) as download_info:
        export_btn.click()
    
    download = download_info.value
    df_raw = pd.read_excel(download.path())
    cleaned = clean_report_dataframe(df_raw, school_name, consultant_name, state_zone, report_type)
    print(f"       ✅ Successfully exported and parsed {len(cleaned)} rows for {school_name}.")
    return cleaned


# ==============================================================================
# 4. ORCHESTRATION PIPELINE
# ==============================================================================
def download_data_for_consultant(browser, consultant_info):
    consultant_name = consultant_info.get("name", "Consultant")
    state_zone = consultant_info.get("state_zone", "Madhya Pradesh (MP)")
    email = consultant_info.get("email", "").strip()
    password = consultant_info.get("password", "").strip()

    if not email or not password:
        return []

    print(f"\n========================================================")
    print(f"Executing Multi-School Extraction for: {consultant_name}")
    print(f"========================================================")

    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1600, "height": 1000},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    page.set_default_timeout(0)  # Infinite default timeout for all page operations
    consultant_dfs = []

    try:
        # Step 1: Login & wait for complete load
        human_like_login(page, email, password)

        # Step 2: Open Content report to extract school list
        navigate_sidebar_module(page, "Content")
        schools = extract_all_schools(page)
        print(f"Assigned Schools Detected ({len(schools)}): {schools}")

        # Step 3: Loop modules and schools
        modules = [
            {"name": "Content", "type": "library"},
            {"name": "Platform", "type": "lessonDelivery"}
        ]

        for school in schools:
            print(f"\n--- Ingesting School: {school} ---")
            for mod in modules:
                print(f"  -> Module: {mod['name']}...")
                navigate_sidebar_module(page, mod['name'])
                select_school_and_ay(page, school, target_ay="AY26-27")
                apply_date_filter_to_today(page, start_date_str="07/01/2026")
                click_teachers_tab_and_wait(page)
                
                df_res = export_and_capture_file(page, school, consultant_name, state_zone, mod['type'])
                if df_res is not None and not df_res.empty:
                    consultant_dfs.append(df_res)

    except Exception as e:
        print(f"Extraction Exception: {e}")
        page.screenshot(path=f"debug_screenshots/error_{consultant_name}.png")
    finally:
        context.close()

    return consultant_dfs


# ==============================================================================
# 5. MERGE & SUPABASE SYNC
# ==============================================================================
def update_supabase_master_db(new_dfs):
    if not new_dfs:
        raise RuntimeError("No data rows collected across consultants. Review debug_screenshots artifact.")

    if supabase is None:
        raise RuntimeError("Supabase client is not initialized.")

    print("\nMerging all datasets into Supabase Master Parquet...")
    base_df = pd.DataFrame()
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            base_df = pd.read_parquet(BytesIO(response))
            print(f"Existing cloud database rows: {len(base_df)}")
    except Exception:
        print("Initializing fresh master database.")

    combined_new = pd.concat(new_dfs, ignore_index=True)
    all_data = pd.concat([base_df, combined_new], ignore_index=True) if not base_df.empty else combined_new

    dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution']
    avail_cols = [c for c in dedup_cols if c in all_data.columns]
    master_df = all_data.drop_duplicates(subset=avail_cols, keep='last')

    parquet_buffer = BytesIO()
    master_df.to_parquet(parquet_buffer, index=False)
    parquet_buffer.seek(0)

    try:
        supabase.storage.from_(BUCKET_NAME).upload(
            path=PARQUET_FILE_NAME,
            file=parquet_buffer.getvalue(),
            file_options={"upsert": "true", "content-type": "application/octet-stream"}
        )
        print(f"\n🎉 SUCCESS: All schools synced to Supabase! Total records: {len(master_df)}")
    except Exception as e:
        raise RuntimeError(f"Supabase upload error: {e}")


# ==============================================================================
# 6. ENTRYPOINT
# ==============================================================================
if __name__ == "__main__":
    t0 = time.time()
    try:
        consultants_list = json.loads(CONSULTANTS_JSON)
        if not consultants_list:
            print("CONSULTANTS_JSON is empty.")
        else:
            all_batches = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for consultant in consultants_list:
                    batch = download_data_for_consultant(browser, consultant)
                    all_batches.extend(batch)
                browser.close()

            update_supabase_master_db(all_batches)
    finally:
        print(f"Total pipeline runtime: {time.time() - t0:.2f} seconds.\n")
