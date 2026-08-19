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
# 2. DATA NORMALIZATION
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
# 3. UI AUTOMATION FUNCTIONS
# ==============================================================================
def select_academic_year(page, target_ay="AY26-27"):
    try:
        ay_dropdown = page.locator('div:has-text("AY"), [class*="select"]:has-text("AY")').first
        if ay_dropdown.count() > 0 and ay_dropdown.is_visible():
            ay_dropdown.click()
            time.sleep(1)
            target_opt = page.locator(f'text="{target_ay}", div:has-text("{target_ay}"), li:has-text("{target_ay}")').first
            if target_opt.count() > 0 and target_opt.is_visible():
                target_opt.click()
            else:
                alt_opt = page.locator('text="26-27", div:has-text("26-27"), li:has-text("26-27")').first
                if alt_opt.count() > 0 and alt_opt.is_visible():
                    alt_opt.click()
            time.sleep(2)
            print(f"       ✅ Academic Year set to: {target_ay}")
    except Exception as e_ay:
        print(f"       Academic Year select note: {e_ay}")


def apply_date_filter(page, start_date_str="07/01/2026"):
    end_date_str = datetime.now().strftime("%m/%d/%Y")
    try:
        date_inputs = page.locator('input[type="text"][placeholder*="202" i], input[type="text"][value*="/"], input[placeholder*="MM" i], input[placeholder*="DD" i]').all()
        if len(date_inputs) >= 2:
            date_inputs[0].click()
            date_inputs[0].fill(start_date_str)
            date_inputs[0].press("Enter")
            time.sleep(0.5)

            date_inputs[1].click()
            date_inputs[1].fill(end_date_str)
            date_inputs[1].press("Enter")
            time.sleep(1)
            print(f"       ✅ Date filter applied: {start_date_str} to {end_date_str}")
    except Exception as e_dt:
        print(f"       Date filter note: {e_dt}")


def get_all_schools_from_dropdown(page):
    school_names = []
    try:
        school_dropdown = page.locator('div[class*="select"], div[role="combobox"], button[class*="dropdown"]').first
        school_dropdown.click()
        time.sleep(1.5)

        options = page.locator('div[role="option"], li[role="option"], div[class*="option"], div[class*="menu"] div').all_text_contents()
        page.keyboard.press("Escape")

        for opt in options:
            clean_opt = opt.strip()
            if clean_opt and "AY2" not in clean_opt and "Academic" not in clean_opt:
                school_names.append(clean_opt)

        school_names = list(dict.fromkeys(school_names))
    except Exception as e_sch:
        print(f"       School discovery note: {e_sch}")

    if not school_names:
        school_names = ["Current School"]
    return school_names


def select_school(page, school_name):
    if school_name == "Current School":
        return
    try:
        school_dropdown = page.locator('div[class*="select"], div[role="combobox"], button[class*="dropdown"]').first
        school_dropdown.click()
        time.sleep(1)
        page.locator(f'text="{school_name}"').first.click()
        time.sleep(2)
    except Exception as e:
        print(f"       Error selecting school {school_name}: {e}")


def click_teachers_tab(page):
    try:
        teacher_pill = page.locator('button:has-text("Teachers"), div:has-text("Teachers"), span:has-text("Teachers")').first
        teacher_pill.click()
        time.sleep(3)
        return True
    except Exception as e:
        print(f"       Teachers tab click note: {e}")
        return False


def click_export_download(page, school_name, consultant_name, state_zone, report_type):
    try:
        export_btn = page.locator('button:has-text("Export")').first
        with page.expect_download(timeout=45000) as download_info:
            export_btn.click()
        
        download = download_info.value
        df_raw = pd.read_excel(download.path())
        cleaned = clean_report_dataframe(
            df_raw, 
            school_name=school_name, 
            consultant_name=consultant_name, 
            state_zone=state_zone, 
            report_type_default=report_type
        )
        print(f"       ✅ Exported {len(cleaned)} rows for {school_name} ({report_type}).")
        return cleaned
    except Exception as e:
        print(f"       Export notice for {school_name} ({report_type}): {e}")
        page.screenshot(path=f"debug_screenshots/export_fail_{school_name}_{report_type}.png")
        return None


# ==============================================================================
# 4. INGESTION PIPELINE (CYCLES THROUGH ALL SCHOOLS & BOTH MODULES)
# ==============================================================================
def download_data_for_consultant(browser, consultant_info):
    consultant_name = consultant_info.get("name", "Consultant")
    state_zone = consultant_info.get("state_zone", "Madhya Pradesh (MP)")
    email = consultant_info.get("email", "").strip()
    password = consultant_info.get("password", "").strip()

    if not email or not password:
        print(f"Skipping {consultant_name}: Credentials missing.")
        return []

    print(f"\n========================================================")
    print(f"Executing Ingestion for: {consultant_name} ({state_zone})")
    print(f"========================================================")

    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1600, "height": 1000},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    consultant_dfs = []

    try:
        # 1. Login
        print("1. Opening Admin Portal...")
        page.goto("https://admin.onelern.school", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        email_input = page.locator('input[placeholder*="email" i], input[type="email"]').first
        email_input.wait_for(state="visible", timeout=20000)
        email_input.fill(email)

        password_input = page.locator('input[placeholder*="password" i], input[type="password"]').first
        password_input.fill(password)

        page.locator('button:has-text("Login")').first.click()
        print("2. Authenticating session (waiting 30s for full dashboard load)...")
        time.sleep(25)

        # 2. Open Reports -> Content
        print("3. Navigating to Reports -> Content...")
        reports_menu = page.locator('text="Reports"').first
        if reports_menu.count() > 0 and reports_menu.is_visible():
            reports_menu.click()
            time.sleep(1)

        content_link = page.locator('a:has-text("Content"), span:has-text("Content")').first
        if content_link.count() > 0 and content_link.is_visible():
            content_link.click()
            time.sleep(3)
        else:
            page.goto("https://admin.onelern.school/reports/content", wait_until="domcontentloaded")
            time.sleep(4)

        # 3. Detect all schools assigned to consultant
        school_list = get_all_schools_from_dropdown(page)
        print(f"Schools detected to process ({len(school_list)}): {school_list}")

        # 4. Modules definition
        modules = [
            {"title": "Content (Library)", "sidebar_text": "Content", "url_fallback": "https://admin.onelern.school/reports/content", "type": "library"},
            {"title": "Platform (Lesson Prep)", "sidebar_text": "Platform", "url_fallback": "https://admin.onelern.school/platform-reports", "type": "lessonDelivery"}
        ]

        # 5. Dual-Module Loop per School
        for school in school_list:
            print(f"\n--- Processing School: {school} ---")
            for mod in modules:
                print(f"  -> Opening {mod['title']}...")
                try:
                    nav_btn = page.locator(f'text="{mod["sidebar_text"]}"').first
                    if nav_btn.count() > 0 and nav_btn.is_visible():
                        nav_btn.click()
                        time.sleep(3)
                    else:
                        page.goto(mod["url_fallback"], wait_until="domcontentloaded")
                        time.sleep(3)
                except Exception:
                    page.goto(mod["url_fallback"], wait_until="domcontentloaded")
                    time.sleep(3)

                # Step A: Select School
                select_school(page, school)

                # Step B: Select Academic Year AY26-27
                select_academic_year(page, target_ay="AY26-27")

                # Step C: Set Date Range (July 1st, 2026 to Today)
                apply_date_filter(page, start_date_str="07/01/2026")

                # Step D: Click Teachers View Tab
                click_teachers_tab(page)

                # Step E: Export Data
                df_out = click_export_download(page, school, consultant_name, state_zone, mod['type'])
                if df_out is not None and not df_out.empty:
                    consultant_dfs.append(df_out)

    except Exception as e_user:
        print(f"Pipeline error for {consultant_name}: {e_user}")
        page.screenshot(path=f"debug_screenshots/pipeline_error_{consultant_name}.png")
    finally:
        context.close()

    return consultant_dfs


# ==============================================================================
# 5. MERGE & CLOUD SUPABASE PARQUET SYNC
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
