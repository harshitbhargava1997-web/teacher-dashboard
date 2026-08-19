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
# 1. CLOUD STORAGE & MULTI-USER CONFIGURATION
# ==============================================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip('/')
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BUCKET_NAME = os.getenv("BUCKET_NAME", "academic-dashboard")
PARQUET_FILE_NAME = "master_database.parquet"

# JSON array containing all consultants' credentials and region info
CONSULTANTS_JSON = os.getenv("CONSULTANTS_JSON", "[]")

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"⚠️ Supabase client initialization notice: {e}")
    supabase = None


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
# 3. PLAYWRIGHT AUTOMATION FOR A SINGLE CONSULTANT ACCOUNT
# ==============================================================================
def download_data_for_consultant(browser, consultant_info):
    consultant_name = consultant_info.get("name", "Unknown Consultant")
    state_zone = consultant_info.get("state_zone", "Madhya Pradesh (MP)")
    email = consultant_info.get("email", "")
    password = consultant_info.get("password", "")

    if not email or not password:
        print(f"⚠️ Skipping {consultant_name}: Email or Password not provided.")
        return []

    print(f"\n========================================================")
    print(f"👤 Logging in for Consultant: {consultant_name} ({state_zone})")
    print(f"========================================================")

    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    consultant_dfs = []

    try:
        # Step 1: Login
        page.goto("https://admin.onelern.school", wait_until="networkidle")
        page.fill('input[type="email"], input[name="email"], input[placeholder*="Email"]', email)
        page.fill('input[type="password"], input[name="password"]', password)
        page.click('button[type="submit"], button:has-text("Login"), button:has-text("Sign In")')
        page.wait_for_load_state("networkidle")
        time.sleep(3)

        # Step 2: Extract visible schools for this consultant
        def get_all_school_options():
            page.goto("https://admin.onelern.school/reports/content", wait_until="networkidle")
            time.sleep(2)
            dropdown_trigger = page.locator('div[class*="select"], div[role="combobox"], div[class*="dropdown"]').first
            dropdown_trigger.click()
            time.sleep(1)
            options = page.locator('div[role="option"], li[role="option"], div[class*="menu-item"]').all_text_contents()
            page.keyboard.press("Escape")
            school_names = [opt.strip() for opt in options if opt.strip()]
            return list(dict.fromkeys(school_names))

        try:
            school_list = get_all_school_options()
            print(f"🏫 Found {len(school_list)} school(s) assigned to {consultant_name}: {school_list}")
        except Exception as e_sch_list:
            print(f"⚠️ Could not enumerate schools for {consultant_name}: {e_sch_list}")
            school_list = ["Current School"]

        # Step 3: Loop through Content and Platform sections
        sections = [
            {"name": "Content (Library)", "url": "https://admin.onelern.school/reports/content", "type": "library"},
            {"name": "Platform (Lesson Prep)", "url": "https://admin.onelern.school/platform-reports", "type": "lessonDelivery"}
        ]

        for school in school_list:
            print(f"\n  🏫 Processing School: {school}")
            for sec in sections:
                print(f"     📂 Fetching {sec['name']} [Teachers View]...")
                try:
                    page.goto(sec['url'], wait_until="networkidle")
                    time.sleep(2)

                    # Switch School
                    if school != "Current School":
                        try:
                            dropdown_trigger = page.locator('div[class*="select"], div[role="combobox"], div[class*="dropdown"]').first
                            dropdown_trigger.click()
                            time.sleep(0.5)
                            page.click(f'text="{school}"')
                            page.wait_for_load_state("networkidle")
                            time.sleep(1.5)
                        except Exception as e_drop:
                            print(f"        ⚠️ Dropdown switch notice: {e_drop}")

                    # Click 'Teachers' Tab
                    try:
                        page.click('button:has-text("Teachers"), div:has-text("Teachers"), span:has-text("Teachers")')
                        page.wait_for_load_state("networkidle")
                        time.sleep(1.5)
                    except Exception as e_tab:
                        print(f"        ⚠️ Teachers tab toggle notice: {e_tab}")

                    # Export
                    try:
                        with page.expect_download(timeout=45000) as download_info:
                            page.click('button:has-text("Export")')
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
                        print(f"        ✅ Downloaded {len(cleaned_df)} rows for {school} ({sec['name']}).")
                    except Exception as e_exp:
                        print(f"        ⚠️ Export failed for {school}: {e_exp}")

                except Exception as e_nav:
                    print(f"        ❌ Navigation failed: {e_nav}")

    except Exception as e_user:
        print(f"❌ Failed processing for {consultant_name}: {e_user}")
    finally:
        context.close()

    return consultant_dfs


# ==============================================================================
# 4. SUPABASE CLOUD SYNC & DEDUPLICATION
# ==============================================================================
def update_supabase_master_db(new_dfs):
    if not new_dfs:
        print("\n❌ No data collected across any consultant. Supabase update aborted.")
        return

    if supabase is None:
        print("\n❌ Supabase client not available.")
        return

    print("\n☁️ Merging all consultants' records into Supabase Master Parquet...")
    base_df = pd.DataFrame()
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)[cite: 1]
        if response:
            base_df = pd.read_parquet(BytesIO(response))[cite: 1]
            print(f"   -> Existing cloud database records: {len(base_df)}")
    except Exception:
        print("   -> Creating fresh master database.")

    combined_new = pd.concat(new_dfs, ignore_index=True)
    all_data = pd.concat([base_df, combined_new], ignore_index=True) if not base_df.empty else combined_new[cite: 1]

    # Deduplicate across teachers, timestamps, features, books, and schools
    dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution'][cite: 1]
    avail_cols = [c for c in dedup_cols if c in all_data.columns][cite: 1]
    master_df = all_data.drop_duplicates(subset=avail_cols, keep='last')[cite: 1]

    parquet_buffer = BytesIO()[cite: 1]
    master_df.to_parquet(parquet_buffer, index=False)[cite: 1]
    parquet_buffer.seek(0)[cite: 1]

    try:
        supabase.storage.from_(BUCKET_NAME).upload([cite: 1]
            path=PARQUET_FILE_NAME,[cite: 1]
            file=parquet_buffer.getvalue(),[cite: 1]
            file_options={"upsert": "true", "content-type": "application/octet-stream"}[cite: 1]
        )
        print(f"\n🎉 SUCCESS: All consultants' schools successfully synced to Supabase! Total records: {len(master_df)}")
    except Exception as e:
        print(f"\n❌ Error uploading Parquet to Supabase: {e}")


# ==============================================================================
# 5. MAIN ORCHESTRATOR
# ==============================================================================
if __name__ == "__main__":
    t0 = time.time()
    try:
        consultants_list = json.loads(CONSULTANTS_JSON)
        if not consultants_list:
            print("⚠️ No consultants defined in CONSULTANTS_JSON environment variable.")
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
        print(f"\n💥 Script execution error: {err}")
    finally:
        print(f"⏱️ Total runtime: {time.time() - t0:.2f} seconds.\n")
