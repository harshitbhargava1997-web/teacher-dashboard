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
# 3. INTERACTIVE PORTAL ACTIONS
# ==============================================================================
def set_date_filter_july_to_today(page):
    """Sets date filter from July 1st of current year to today."""
    current_year = datetime.now().year
    start_date_str = f"01-07-{current_year}"
    end_date_str = datetime.now().strftime("%d-%m-%Y")
    
    try:
        date_pickers = page.locator('input[placeholder*="Date" i], input[placeholder*="DD" i], input[type="date"], [class*="datepicker"], [class*="date-range"]').all()
        if len(date_pickers) >= 2:
            date_pickers[0].fill(f"{current_year}-07-01")
            date_pickers[1].fill(datetime.now().strftime("%Y-%m-%d"))
            time.sleep(1)
        elif len(date_pickers) == 1:
            date_pickers[0].click()
            time.sleep(0.5)
            date_pickers[0].fill(f"{start_date_str} - {end_date_str}")
            page.keyboard.press("Enter")
            time.sleep(1)
    except Exception as e_dt:
        print(f"       Date filter note: {e_dt}")


def switch_school(page, school_name):
    """Selects a specific school from the active dropdown."""
    if school_name == "Current School":
        return
    try:
        # Standard select
        select_tag = page.locator('select').first
        if select_tag.count() > 0 and select_tag.is_visible():
            select_tag.select_option(label=school_name)
            time.sleep(2)
            return

        # Modern UI dropdown trigger
        dropdown_triggers = page.locator('.ant-select-selector, .MuiSelect-select, div[role="combobox"], [class*="school-select"], [class*="select__control"]').all()
        for trg in dropdown_triggers:
            if trg.is_visible():
                trg.click()
                time.sleep(1)
                page.locator(f'text="{school_name}"').first.click()
                time.sleep(2)
                return
    except Exception as e_sch:
        print(f"       School switch note for {school_name}: {e_sch}")


def click_teacher_tab(page):
    """Clicks the 'Teacher' or 'Teachers' view tab."""
    try:
        teacher_tab_locators = [
            'div[role="tab"]:has-text("Teacher")',
            'button:has-text("Teacher")',
            'span:has-text("Teacher")',
            'a:has-text("Teacher")',
            'li:has-text("Teacher")'
        ]
        for t_loc in teacher_tab_locators:
            tab = page.locator(t_loc).first
            if tab.count() > 0 and tab.is_visible():
                tab.click()
                time.sleep(2)
                return True
    except Exception as e_tab:
        print(f"       Teacher tab selection note: {e_tab}")
    return False


def click_export_download(page, school_name, consultant_name, state_zone, report_type):
    """Clicks Export, captures the downloaded Excel file, and returns a cleaned DataFrame."""
    export_locators = [
        'button:has-text("Export")',
        'a:has-text("Export")',
        'span:has-text("Export")',
        'button:has-text("Download")',
        '[aria-label*="export" i]',
        '[title*="export" i]',
        '.export-btn'
    ]
    
    for exp_loc in export_locators:
        btn = page.locator(exp_loc).first
        if btn.count() > 0 and btn.is_visible():
            try:
                with page.expect_download(timeout=45000) as download_info:
                    btn.click()
                download = download_info.value
                df_raw = pd.read_excel(download.path())
                cleaned = clean_report_dataframe(
                    df_raw, 
                    school_name=school_name, 
                    consultant_name=consultant_name, 
                    state_zone=state_zone, 
                    report_type_default=report_type
                )
                print(f"       ✅ Successfully exported {len(cleaned)} rows for {school_name}.")
                return cleaned
            except Exception as e_dl:
                print(f"       Export download failed for {school_name}: {e_dl}")
                page.screenshot(path=f"debug_screenshots/export_error_{school_name}_{report_type}.png")
                return None

    print(f"       ⚠️ Export button not visible for {school_name} ({report_type}).")
    page.screenshot(path=f"debug_screenshots/no_export_{school_name}_{report_type}.png")
    return None


# ==============================================================================
# 4. FULL AUTOMATION WORKFLOW PER CONSULTANT
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
    print(f"Executing Complete Portal Ingestion: {consultant_name}")
    print(f"========================================================")

    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1600, "height": 1000},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    consultant_dfs = []

    try:
        # STEP 1: Login
        print("1. Logging into Admin Portal...")
        page.goto("https://admin.onelern.school", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        email_input = page.locator('input[type="email"], input[name="email"], input[placeholder*="Email" i], input[type="text"]').first
        email_input.wait_for(state="visible", timeout=20000)
        email_input.fill(email)

        password_input = page.locator('input[type="password"], input[name="password"]').first
        password_input.fill(password)

        page.locator('button[type="submit"], button:has-text("Login"), button:has-text("Sign In")').first.click()
        print("2. Authenticating session...")
        time.sleep(6)

        # STEP 2: Navigate: Home -> Reports -> Content
        print("3. Navigating to Reports -> Content...")
        reports_nav = page.locator('text="Reports", a:has-text("Reports"), span:has-text("Reports")').first
        if reports_nav.count() > 0 and reports_nav.is_visible():
            reports_nav.click()
            time.sleep(2)
        
        page.goto("https://admin.onelern.school/reports/content", wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)

        # STEP 3: Enumerate all assigned schools
        school_list = []
        dropdown_selectors = ['.ant-select-selector', '.MuiSelect-select', 'div[role="combobox"]', '.select__control', '[class*="school-select"]']
        for selector in dropdown_selectors:
            trg = page.locator(selector).first
            if trg.count() > 0 and trg.is_visible():
                try:
                    trg.click()
                    time.sleep(1.5)
                    opts = page.locator('.ant-select-item-option-content, [role="option"], li[role="option"], .select__option').all_text_contents()
                    page.keyboard.press("Escape")
                    found = [o.strip() for o in opts if o.strip() and "select" not in o.lower()]
                    if found:
                        school_list = found
                        break
                except Exception:
                    pass

        if not school_list:
            select_tag = page.locator('select').first
            if select_tag.count() > 0 and select_tag.is_visible():
                opts = select_tag.locator('option').all_text_contents()
                school_list = [o.strip() for o in opts if o.strip() and "select" not in o.lower()]

        if not school_list:
            school_list = ["Current School"]

        school_list = list(dict.fromkeys(school_list))
        print(f"Schools detected ({len(school_list)}): {school_list}")

        # STEP 4: Ingest Content & Platform Reports for every school
        modules = [
            {"title": "Content (Library)", "url": "https://admin.onelern.school/reports/content", "type": "library"},
            {"title": "Platform (Lesson Prep)", "url": "https://admin.onelern.school/platform-reports", "type": "lessonDelivery"}
        ]

        for school in school_list:
            print(f"\n--- Ingesting Data for School: {school} ---")
            for mod in modules:
                print(f"  -> Opening {mod['title']}...")
                page.goto(mod['url'], wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)

                # Select School
                switch_school(page, school)

                # Select Date Range (1st July to Today)
                set_date_filter_july_to_today(page)

                # Click Teacher View
                click_teacher_tab(page)

                # Export & Ingest
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
# 5. MERGE & DIRECT SUPABASE SYNC
# ==============================================================================
def update_supabase_master_db(new_dfs):
    if not new_dfs:
        print("\n⚠️ No new data collected across consultants. Supabase update aborted.")
        return

    if supabase is None:
        print("\nSupabase client unavailable.")
        return

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

    # Deduplicate
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
        print(f"\n🎉 SUCCESS: All schools successfully synced to Supabase! Total records: {len(master_df)}")
    except Exception as e:
        print(f"\nSupabase upload error: {e}")


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
    except Exception as err:
        print(f"Pipeline execution error: {err}")
    finally:
        print(f"Total pipeline runtime: {time.time() - t0:.2f} seconds.\n")
