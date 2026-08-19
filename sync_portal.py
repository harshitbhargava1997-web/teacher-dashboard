import os
import re
import time
import json
from io import BytesIO
import pandas as pd
import numpy as np
from playwright.sync_api import sync_playwright
from supabase import create_client

# ==============================================================================
# 1. CLOUD STORAGE CONFIGURATION
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
# 2. DATA NORMALIZATION & CLEANING
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
# 3. PLAYWRIGHT AUTOMATION ENGINE
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
        viewport={"width": 1440, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    consultant_dfs = []

    try:
        # STEP 1: Login
        print("1. Navigating to login portal...")
        page.goto("https://admin.onelern.school", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        email_input = page.locator('input[type="email"], input[name="email"], input[placeholder*="Email" i], input[type="text"]').first
        email_input.wait_for(state="visible", timeout=20000)
        email_input.fill(email)

        password_input = page.locator('input[type="password"], input[name="password"]').first
        password_input.fill(password)

        submit_btn = page.locator('button[type="submit"], button:has-text("Login"), button:has-text("Sign In"), input[type="submit"]').first
        submit_btn.click()

        print("2. Authenticating session...")
        time.sleep(6)
        page.screenshot(path=f"debug_screenshots/post_login_{consultant_name}.png")

        # STEP 2: Extract Schools
        print("3. Navigating to Reports...")
        page.goto("https://admin.onelern.school/reports/content", wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        page.screenshot(path=f"debug_screenshots/reports_page_{consultant_name}.png")

        school_list = []
        select_el = page.locator('select').first
        if select_el.count() > 0 and select_el.is_visible():
            opts = select_el.locator('option').all_text_contents()
            school_list = [o.strip() for o in opts if o.strip() and "select" not in o.lower()]
        else:
            triggers = page.locator('.ant-select, .MuiSelect-select, div[role="combobox"], [class*="school-select"], [class*="schoolSelect"], [class*="select"]').all()
            for trg in triggers:
                if trg.is_visible():
                    try:
                        trg.click()
                        time.sleep(1.5)
                        options = page.locator('.ant-select-item-option, [role="option"], li[role="option"], .menu-item').all_text_contents()
                        page.keyboard.press("Escape")
                        found = [o.strip() for o in options if o.strip()]
                        if found:
                            school_list = found
                            break
                    except Exception:
                        pass

        if not school_list:
            school_list = ["Current School"]

        school_list = list(dict.fromkeys(school_list))
        print(f"Schools detected to process ({len(school_list)}): {school_list}")

        # STEP 3: Multi-Section Ingestion
        sections = [
            {"name": "Content (Library)", "url": "https://admin.onelern.school/reports/content", "type": "library"},
            {"name": "Platform (Lesson Prep)", "url": "https://admin.onelern.school/platform-reports", "type": "lessonDelivery"}
        ]

        for school in school_list:
            print(f"\n--- Processing School: {school} ---")
            for sec in sections:
                print(f"  -> Fetching {sec['name']}...")
                try:
                    page.goto(sec['url'], wait_until="domcontentloaded", timeout=60000)
                    time.sleep(3)

                    # Switch school if applicable
                    if school != "Current School":
                        try:
                            select_elem = page.locator('select').first
                            if select_elem.count() > 0 and select_elem.is_visible():
                                select_elem.select_option(label=school)
                            else:
                                trg = page.locator('.ant-select, .MuiSelect-select, div[role="combobox"], [class*="select"]').first
                                if trg.count() > 0 and trg.is_visible():
                                    trg.click()
                                    time.sleep(1)
                                    page.click(f'text="{school}"')
                            time.sleep(2)
                        except Exception as e_switch:
                            print(f"     Notice switching school: {e_switch}")

                    # Switch to Teachers Tab
                    try:
                        teacher_btn = page.locator('text="Teachers", button:has-text("Teacher"), div[role="tab"]:has-text("Teacher")').first
                        if teacher_btn.count() > 0 and teacher_btn.is_visible():
                            teacher_btn.click()
                            time.sleep(2)
                    except Exception as e_tab:
                        print(f"     Notice selecting Teachers tab: {e_tab}")

                    # Export
                    try:
                        export_trigger = page.locator('button:has-text("Export"), [aria-label*="export" i], [title*="export" i], button:has-text("Download")').first
                        with page.expect_download(timeout=30000) as download_info:
                            export_trigger.click()
                        download = download_info.value
                        df_raw = pd.read_excel(download.path())
                        cleaned_df = clean_report_dataframe(
                            df_raw, 
                            school_name=school, 
                            consultant_name=consultant_name, 
                            state_zone=state_zone, 
                            report_type_default=sec['type']
                        )
                        consultant_dfs.append(cleaned_df)
                        print(f"     ✅ Downloaded {len(cleaned_df)} rows for {school} ({sec['name']}).")
                    except Exception as e_dl:
                        print(f"     Notice during export: {e_dl}")
                        page.screenshot(path=f"debug_screenshots/export_fail_{school}_{sec['type']}.png")

                except Exception as e_sec:
                    print(f"     Section load error: {e_sec}")

    except Exception as e_user:
        print(f"Consultant pipeline exception: {e_user}")
        page.screenshot(path=f"debug_screenshots/error_{consultant_name}.png")
    finally:
        context.close()

    return consultant_dfs


# ==============================================================================
# 4. SUPABASE CLOUD SYNC & DEDUPLICATION
# ==============================================================================
def update_supabase_master_db(new_dfs):
    if not new_dfs:
        print("\n⚠️ No new data rows collected across consultants. Supabase update aborted.")
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
        print(f"\n🎉 SUCCESS: Master DB synced to Supabase! Total records: {len(master_df)}")
    except Exception as e:
        print(f"\nSupabase upload error: {e}")


# ==============================================================================
# 5. ENTRYPOINT
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
        print(f"Pipeline error: {err}")
    finally:
        print(f"Total execution time: {time.time() - t0:.2f} seconds.\n")
