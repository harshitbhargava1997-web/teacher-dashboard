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


def wait_for_ui_stabilization(page, max_wait_sec=25):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
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
    out_name = calculated_full.loc[empty_full]
    df.loc[empty_full, "FullName"] = out_name
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
    print("1. Opening Admin Portal ([https://admin.onelern.school](https://admin.onelern.school))...", flush=True)
    page.goto("[https://admin.onelern.school](https://admin.onelern.school)", wait_until="domcontentloaded", timeout=60000)
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
    
    print("   Waiting for Dashboard to load...", flush=True)
    time.sleep(5)
    wait_for_ui_stabilization(page, max_wait_sec=25)
    page.screenshot(path="debug_screenshots/01_authenticated_dashboard.png")
    print("   ✅ Authenticated successfully.", flush=True)


def navigate_to_module(page, module_name):
    print(f"2. Navigating to Reports -> {module_name}...", flush=True)
    path = "content" if module_name.lower() == "content" else "platform-reports"
    target_url = f"[https://admin.onelern.school/reports/](https://admin.onelern.school/reports/){path}"
    
    try:
        # Check if direct link or UI menu is visible
        menu_link = page.locator(f'a[href*="{path}"], text="{module_name}"').first
        if menu_link.is_visible():
            menu_link.click()
        else:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

    time.sleep(4)
    wait_for_ui_stabilization(page, max_wait_sec=25)
    page.screenshot(path=f"debug_screenshots/02_module_{module_name}.png")


def extract_school_names(page):
    print("3. Scanning assigned schools from UI dropdown...", flush=True)
    schools = []
    try:
        dropdown = page.locator('mat-select, div[class*="select"], div[role="combobox"], select, [aria-label*="School" i]').first
        dropdown.wait_for(state="visible", timeout=25000)
        dropdown.click()
        time.sleep(2)

        options = page.locator('mat-option, div[role="option"], li[role="option"], option, div[class*="option"]').all_text_contents()
        page.keyboard.press("Escape")
        time.sleep(1)
        
        for opt in options:
            clean = opt.strip()
            if clean and "AY2" not in clean and "Academic" not in clean and "Grade" not in clean and "Select" not in clean:
                schools.append(clean)
    except Exception as e:
        print(f"   School scanning notice: {e}", flush=True)
        page.screenshot(path="debug_screenshots/03_school_dropdown_state.png")

    schools = list(dict.fromkeys(schools))
    if not schools:
        schools = ["Current School"]
    print(f"   Detected schools ({len(schools)}): {schools}", flush=True)
    return schools


def select_school_and_ay(page, school_name, target_ay="AY26-27"):
    if school_name != "Current School":
        try:
            dropdown = page.locator('mat-select, div[class*="select"], div[role="combobox"]').first
            dropdown.click()
            time.sleep(1)
            target = page.locator(f'mat-option:has-text("{school_name}"), text="{school_name}"').first
            if target.is_visible():
                target.click()
                time.sleep(2)
            else:
                page.keyboard.press("Escape")
        except Exception:
            pass

    try:
        ay_box = page.locator('mat-select[aria-label*="AY" i], div:has-text("AY")').first
        if ay_box.is_visible():
            ay_box.click()
            time.sleep(1)
            ay_opt = page.locator(f'mat-option:has-text("{target_ay}"), text="{target_ay}"').first
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
        date_inputs = page.locator('input[placeholder*="202" i], input[value*="/"], input[placeholder*="Date" i], input[type="date"]').all()
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
        teacher_pill = page.locator('button:has-text("Teachers"), div:has-text("Teachers"), span:has-text("Teachers"), a:has-text("Teachers"), [role="tab"]:has-text("Teachers")').first
        teacher_pill.wait_for(state="visible", timeout=20000)
        teacher_pill.click()
        wait_for_ui_stabilization(page, max_wait_sec=20)
        return True
    except Exception as e:
        print(f"       Teachers tab notice: {e}", flush=True)
    return False


def export_report_data(page, school_name, consultant_name, state_zone, report_name):
    try:
        export_btn = page.locator('button:has-text("Export"), div:has-text("Export"), a:has-text("Export"), [title*="Export" i], mat-icon:has-text("download
