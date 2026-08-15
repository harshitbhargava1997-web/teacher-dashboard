import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import os
import glob
import re
import json
import urllib.parse
from io import BytesIO
from supabase import create_client

# ReportLab PDF Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration (Must be the first Streamlit command)
st.set_page_config(page_title="Academic Manager Portfolio & Teacher KPI Review Dashboard", layout="wide")

# --- SUPABASE CLOUD STORAGE SETUP ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"].rstrip('/')
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    BUCKET_NAME = st.secrets["supabase"]["bucket_name"]
    PARQUET_FILE_NAME = "master_database.parquet"

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"Supabase credentials missing or misconfigured in Streamlit Secrets: {e}")

@st.cache_data(ttl=5, show_spinner=False)
def fetch_master_db_from_supabase():
    """Reads base master parquet file AND merges all isolated teacher JSON submissions in memory."""
    base_df = pd.DataFrame()
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            base_df = pd.read_parquet(BytesIO(response))
    except Exception:
        pass

    # Read all standalone JSON teacher submissions from the submissions/ folder
    sub_records = []
    try:
        file_list = supabase.storage.from_(BUCKET_NAME).list("submissions")
        if file_list:
            for item in file_list:
                fname = item.get('name', '')
                if fname.endswith('.json'):
                    raw_data = supabase.storage.from_(BUCKET_NAME).download(f"submissions/{fname}")
                    if raw_data:
                        sub_records.append(json.loads(raw_data.decode('utf-8')))
    except Exception:
        pass

    if sub_records:
        subs_df = pd.DataFrame(sub_records)
        combined = pd.concat([base_df, subs_df], ignore_index=True) if not base_df.empty else subs_df
        return combined

    return base_df


def _norm_text(value):
    """Normalize a single text value without converting missing values to the string 'nan'."""
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _norm_key(value):
    """Case-insensitive comparison key for school/name matching."""
    return _norm_text(value).casefold()


def normalize_identity_columns(df):
    """Normalize identity fields while preserving an existing valid FullName."""
    out = df.copy()

    for col in ["Institution", "Center", "FirstName", "LastName", "FullName", "Role"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].map(_norm_text)

    # Prefer the already-populated FullName. Only construct it when it is missing.
    calculated_full = (
        out["FirstName"].fillna("") + " " + out["LastName"].fillna("")
    ).map(_norm_text)
    empty_full = out["FullName"].eq("")
    out.loc[empty_full, "FullName"] = calculated_full.loc[empty_full]

    # Keep the existing application's sentinel for genuinely missing teacher names.
    out.loc[out["FullName"].eq(""), "FullName"] = "Unknown Teacher"
    return out


def build_teacher_roster(df):
    """Build a stable school -> teacher roster from identity-bearing rows only."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["Institution", "Center", "FirstName", "LastName", "FullName", "Role"])

    roster = normalize_identity_columns(df)

    role_key = roster["Role"].map(_norm_key)
    teacher_mask = role_key.isin({"teacher", "teachers"})
    if teacher_mask.any():
        candidate = roster.loc[teacher_mask].copy()
    else:
        candidate = roster.copy()

    candidate = candidate[
        candidate["Institution"].ne("")
        & ~candidate["Institution"].map(_norm_key).isin({"nan", "unknown school", "default school"})
        & candidate["FullName"].ne("")
        & ~candidate["FullName"].map(_norm_key).isin({"nan", "unknown teacher", "none"})
    ]

    candidate["_institution_key"] = candidate["Institution"].map(_norm_key)
    candidate["_teacher_key"] = candidate["FullName"].map(_norm_key)
    candidate = candidate.drop_duplicates(
        subset=["_institution_key", "_teacher_key"], keep="last"
    ).sort_values(["Institution", "FullName"], kind="stable")

    return candidate.reset_index(drop=True)

# 0. PDF Generator Helper Function
def generate_pdf_report(title_text, subtitle_text, summary_metrics, dataframe=None, custom_sections=None):
    """Generates a professional PDF document in memory and returns a downloadable BytesIO buffer."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=15, leading=18, textColor=colors.HexColor('#1F77B4'))
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.gray)
    sec_head_style = ParagraphStyle('SecHead', parent=styles['Heading2'], fontSize=11, leading=14, textColor=colors.HexColor('#1F77B4'))
    normal_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=8.5, leading=12)
    card_header = ParagraphStyle('CardHead', parent=styles['Normal'], fontSize=8, leading=10, textColor=colors.HexColor('#555555'), fontName='Helvetica-Bold')
    card_value = ParagraphStyle('CardVal', parent=styles['Normal'], fontSize=12, leading=14, textColor=colors.HexColor('#1F77B4'), fontName='Helvetica-Bold')
    
    story.append(Paragraph(f"<b>{title_text}</b>", title_style))
    story.append(Paragraph(subtitle_text, subtitle_style))
    story.append(Spacer(1, 8))

    if summary_metrics:
        headers_row = [Paragraph(f"<b>{k}</b>", card_header) for k in summary_metrics.keys()]
        values_row = [Paragraph(f"<b>{v}</b>", card_value) for v in summary_metrics.values()]
        kpi_table = Table([headers_row, values_row], colWidths=[552 / len(summary_metrics)] * len(summary_metrics))
        kpi_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F4F6F9')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 10))

    if custom_sections:
        for heading, body_items in custom_sections.items():
            story.append(Paragraph(f"<b>{heading}</b>", sec_head_style))
            story.append(Spacer(1, 3))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#E2E8F0'), spaceAfter=6))
            for item in body_items:
                story.append(Paragraph(f"• {item}", normal_style))
            story.append(Spacer(1, 10))

    if dataframe is not None and not dataframe.empty:
        raw_data = [dataframe.columns.tolist()] + dataframe.astype(str).values.tolist()
        cell_style = ParagraphStyle('TableCell', parent=styles['Normal'], fontSize=8, leading=10)
        header_style = ParagraphStyle('TableHeader', parent=styles['Normal'], fontSize=8, leading=10, textColor=colors.white, fontName='Helvetica-Bold')

        formatted_data = []
        for i, row in enumerate(raw_data):
            formatted_row = []
            for cell in row:
                st_to_use = header_style if i == 0 else cell_style
                formatted_row.append(Paragraph(str(cell), st_to_use))
            formatted_data.append(formatted_row)

        pdf_table = Table(formatted_data, repeatRows=1)
        pdf_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F77B4')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9F9F9')]),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(pdf_table)

    doc.build(story)
    buffer.seek(0)
    return buffer


def get_working_days(start_date, end_date, excluded_dates_list, exclude_sundays=True):
    """Calculate working days with configurable Sunday and holiday exclusions."""
    try:
        start_np = np.datetime64(start_date)
        end_np = np.datetime64(end_date) + np.timedelta64(1, 'D')
        holidays_np = [np.datetime64(d) for d in excluded_dates_list] if excluded_dates_list else []
        w_mask = '1111110' if exclude_sundays else '1111111'
        return max(1, int(np.busday_count(start_np, end_np, weekmask=w_mask, holidays=holidays_np)))
    except Exception:
        return 1

# Page layout title
st.title("🏫 Academic Manager Portfolio & Teacher KPI Review Dashboard")
st.markdown("Track **School Portfolio Management**, **School WoW Velocity**, **Teacher Execution Tiers**, **Quantitative KPIs (Lesson Prep / Library)**, and **360° Qualitative Evidences & Artifact Compliance**.")

# 1. Supabase Parquet Database Manager Function
def load_or_update_master_db(new_upload_dfs=None):
    master_df = fetch_master_db_from_supabase()

    if not new_upload_dfs:
        return normalize_identity_columns(master_df) if not master_df.empty else master_df

    combined_new = pd.concat(new_upload_dfs, ignore_index=True)
    all_data = pd.concat([master_df, combined_new], ignore_index=True) if not master_df.empty else combined_new
    all_data = normalize_identity_columns(all_data)

    dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution']
    available_dedup_cols = [c for c in dedup_cols if c in all_data.columns]
    master_df = all_data.drop_duplicates(subset=available_dedup_cols, keep='last')

    try:
        parquet_buffer = BytesIO()
        master_df.to_parquet(parquet_buffer, index=False)
        parquet_buffer.seek(0)

        supabase.storage.from_(BUCKET_NAME).upload(
            path=PARQUET_FILE_NAME,
            file=parquet_buffer.getvalue(),
            file_options={"upsert": "true", "content-type": "application/octet-stream"}
        )
        fetch_master_db_from_supabase.clear()
        st.sidebar.success("Successfully synced database to Supabase Cloud!")
    except Exception as e:
        st.sidebar.error(f"Error saving Parquet Database to Supabase: {e}")

    return master_df

# 2. Sidebar Data Upload Manager
st.sidebar.header("📁 Data Upload & Database Sync")
uploaded_files = st.sidebar.file_uploader("Upload UserMetrics Excel (.xlsx)", type=["xlsx"], accept_multiple_files=True)

new_processed_dfs = []
if uploaded_files:
    for file in uploaded_files:
        try:
            temp_df = pd.read_excel(file, sheet_name="UserMetrics")
            temp_df = normalize_identity_columns(temp_df)
            if temp_df['Institution'].eq('').all():
                temp_df['Institution'] = "Default School"
            else:
                temp_df['Institution'] = temp_df['Institution'].replace('', 'Unknown School')

            for col in ['Grade', 'Subject', 'Book']:
                if col not in temp_df.columns:
                    temp_df[col] = ''
                else:
                    temp_df[col] = temp_df[col].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())

            def parse_time_mins(t_str):
                try:
                    parts = str(t_str).split(':')
                    return int(parts[0])*60 + int(parts[1]) + float(parts[2])/60.0
                except:
                    return 0.0

            if 'Duration (HH:MM:SS)' in temp_df.columns:
                temp_df['Duration_Min'] = temp_df['Duration (HH:MM:SS)'].apply(parse_time_mins)
            elif 'Duration (Minutes)' in temp_df.columns:
                temp_df['Duration_Min'] = pd.to_numeric(temp_df['Duration (Minutes)'], errors='coerce').fillna(0.0)
            else:
                temp_df['Duration_Min'] = 0.0

            if 'Type' in temp_df.columns:
                temp_df['Type'] = temp_df['Type'].fillna('Other').astype(str)

            if 'StartTime' in temp_df.columns:
                temp_df['StartTime'] = pd.to_datetime(temp_df['StartTime'], errors='coerce')

            for qual_col in ['Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 'Assessment_Score_Pct']:
                if qual_col not in temp_df.columns:
                    temp_df[qual_col] = None

            new_processed_dfs.append(temp_df)
        except Exception as e:
            st.sidebar.error(f"Error reading {file.name}: {e}")

if new_processed_dfs:
    df = load_or_update_master_db(new_processed_dfs)
    st.sidebar.success(f"Synced {len(uploaded_files)} file(s) into Supabase Parquet DB!")
else:
    df = load_or_update_master_db()

# 3. Cloud Database Status & Storage Controls
st.sidebar.markdown("---")
st.sidebar.header("🗄️ Supabase Cloud Database Status")

if st.sidebar.button("🔄 Sync Latest Teacher Submissions"):
    fetch_master_db_from_supabase.clear()
    st.rerun()

current_db_check = fetch_master_db_from_supabase()

if not current_db_check.empty:
    st.sidebar.metric("Cloud DB Total Records", len(current_db_check))
    if st.sidebar.button("🚨 Clear Cloud Database"):
        try:
            supabase.storage.from_(BUCKET_NAME).remove([PARQUET_FILE_NAME])
            fetch_master_db_from_supabase.clear()
            st.sidebar.success("Cloud database cleared!")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Could not delete database from cloud: {e}")

if df.empty:
    st.info("👋 Upload your raw daily or weekly `UserMetrics.xlsx` files in the sidebar to populate your permanent Supabase database.")
else:
    if 'FullName' not in df.columns:
        if 'FirstName' in df.columns and 'LastName' in df.columns:
            df['FullName'] = (df['FirstName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip()) + " " + df['LastName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
        else:
            df['FullName'] = 'Unknown Teacher'

    if 'StartTime' in df.columns:
        df['StartTime'] = pd.to_datetime(df['StartTime'], errors='coerce')
        df['Date'] = df['StartTime'].dt.date
        df['Month_Name'] = df['StartTime'].dt.strftime('%B %Y')
        df['Month_Sort'] = df['StartTime'].dt.strftime('%Y-%m')
        
        def get_week_of_month(dt):
            try:
                first_day = dt.replace(day=1)
                dom = dt.day
                adjusted_dom = dom + first_day.weekday()
                return int(np.ceil(adjusted_dom / 7.0))
            except:
                return 1

        df['Week_Num'] = df['StartTime'].apply(get_week_of_month)
        
        week_ranges = df.groupby(['Month_Name', 'Week_Num'])['Date'].agg(['min', 'max']).reset_index()
        week_ranges['Week_Date_Range'] = (
            week_ranges['min'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '') + " to " + 
            week_ranges['max'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '')
        )
        
        df = df.merge(week_ranges[['Month_Name', 'Week_Num', 'Week_Date_Range']], on=['Month_Name', 'Week_Num'], how='left')
        df['Month_Week_Label'] = df['StartTime'].dt.strftime('%b %Y') + " - Week " + df['Week_Num'].astype(str) + " (" + df['Week_Date_Range'] + ")"
        df['Week'] = df['Month_Week_Label']
    else:
        df['Date'] = None
        df['Month_Name'] = "N/A"
        df['Week'] = "N/A"

    master_teacher_roster = build_teacher_roster(df)
    if master_teacher_roster.empty:
        master_teacher_roster = pd.DataFrame(columns=['Institution', 'FullName'])
    else:
        master_teacher_roster = master_teacher_roster[['Institution', 'FullName']].drop_duplicates()

    # --- 1. GLOBAL SCHOOL FILTER ---
    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Global Filters")
    all_schools = sorted([str(s) for s in df['Institution'].unique() if str(s).strip() and str(s).lower() not in ['nan', 'none']])
    selected_schools = st.sidebar.multiselect("Select School(s)", options=all_schools, default=all_schools)

    school_master_roster = master_teacher_roster[master_teacher_roster['Institution'].isin(selected_schools)]
    school_filtered_df = df[df['Institution'].isin(selected_schools)]

    # --- 2. GLOBAL CALENDAR & HOLIDAY MANAGER ---
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Calendar & Holiday Manager")
    
    available_months_df = school_filtered_df[['Month_Sort', 'Month_Name']].dropna().drop_duplicates().sort_values(by='Month_Sort', ascending=False)
    month_options = available_months_df['Month_Name'].tolist()
    
    selected_month = st.sidebar.selectbox("Select Review Month:", options=month_options if month_options else ["No Month Data"])
    month_filtered_df = school_filtered_df[school_filtered_df['Month_Name'] == selected_month]
    
    exclude_sundays_flag = st.sidebar.checkbox("🗓️ Exclude Sundays from KPIs", value=True)

    user_excluded_dates = []
    if not month_filtered_df['Date'].isna().all() and not month_filtered_df.empty:
        m_min_date = month_filtered_df['Date'].min()
        m_max_date = month_filtered_df['Date'].max()
        all_month_possible_dates = [d.date() for d in pd.date_range(start=m_min_date, end=m_max_date)]
        
        user_excluded_dates = st.sidebar.multiselect(
            f"🗓️ Punch Holidays for {selected_month}:",
            options=all_month_possible_dates,
            format_func=lambda x: x.strftime('%Y-%m-%d')
        )

    # --- 3. DYNAMIC KPI BENCHMARK CONTROLS (QUANTITATIVE & QUALITATIVE) ---
    st.sidebar.markdown("---")
    st.sidebar.header("🎯 Quantitative KPI Controls")
    enable_quant_kpi = st.sidebar.checkbox("Enable Quantitative KPI Benchmarks", value=True, help="Toggle on/off quantitative minutes benchmark targets.")
    
    if enable_quant_kpi:
        daily_ld_target = st.sidebar.number_input("Lesson Prep Target (Mins/Day)", min_value=0.0, max_value=60.0, value=10.0, step=5.0)
        daily_lib_target = st.sidebar.number_input("Library Usage Target (Mins/Day)", min_value=0.0, max_value=120.0, value=30.0, step=5.0)
    else:
        daily_ld_target = 0.0
        daily_lib_target = 0.0

    st.sidebar.markdown("---")
    st.sidebar.header("🎨 Qualitative Artifact KPI Controls")
    enable_qual_kpi = st.sidebar.checkbox("Enable Qualitative KPI Benchmarks", value=True, help="Toggle on/off qualitative submission target tracking across reports.")
    
    if enable_qual_kpi:
        target_vid_count = st.sidebar.number_input("Min. Activity Videos Required", min_value=1, max_value=20, value=3, step=1)
        target_writing_count = st.sidebar.number_input("Min. Writing Practice Required", min_value=1, max_value=20, value=3, step=1)
        target_lp_combo_count = st.sidebar.number_input("Min. Lesson Plan / Voice Note Submissions", min_value=1, max_value=20, value=3, step=1, help="Combined entity: satisfied by either Lesson Plan Pictures or Voice Notes.")
    else:
        target_vid_count = 0
        target_writing_count = 0
        target_lp_combo_count = 0

    # --- 4. GRANULARITY & CUSTOM DATE RANGE SELECTOR ---
    st.sidebar.subheader("🔍 Review View Level")
    available_month_weeks = sorted(month_filtered_df['Month_Week_Label'].dropna().unique())
    available_dates = sorted(month_filtered_df['Date'].dropna().unique(), reverse=True)
    
    view_mode = st.sidebar.radio("Granularity:", ["Full Month Summary", "Specific Week of Month", "Single Day Review", "Custom Date Range"])
    
    if month_filtered_df.empty and view_mode != "Custom Date Range":
        filtered_df = month_filtered_df
        selected_num_days = 1
        filter_description_text = f"Full Month: {selected_month} (0 Records)"
    elif view_mode == "Full Month Summary":
        filtered_df = month_filtered_df
        selected_num_days = get_working_days(month_filtered_df['Date'].min(), month_filtered_df['Date'].max(), user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Full Month: {selected_month} ({selected_num_days} Working Day(s))"
    elif view_mode == "Specific Week of Month":
        selected_week_label = st.sidebar.selectbox("Select Week:", options=available_month_weeks)
        filtered_df = month_filtered_df[month_filtered_df['Month_Week_Label'] == selected_week_label]
        w_start = filtered_df['Date'].min() if not filtered_df.empty else selected_month
        w_end = filtered_df['Date'].max() if not filtered_df.empty else selected_month
        selected_num_days = get_working_days(w_start, w_end, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"{selected_week_label} ({selected_num_days} Working Day(s))"
    elif view_mode == "Single Day Review":
        selected_date = st.sidebar.selectbox("Select Day:", options=available_dates)
        filtered_df = month_filtered_df[month_filtered_df['Date'] == selected_date]
        selected_num_days = get_working_days(selected_date, selected_date, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Single Date: {selected_date} ({selected_num_days} Working Day(s))"
    else:  # Custom Date Range
        min_avail = school_filtered_df['Date'].dropna().min() if not school_filtered_df['Date'].dropna().empty else pd.Timestamp.now().date()
        max_avail = school_filtered_df['Date'].dropna().max() if not school_filtered_df['Date'].dropna().empty else pd.Timestamp.now().date()
        
        custom_date_range = st.sidebar.date_input("Select Custom Date Range:", value=(min_avail, max_avail), min_value=min_avail, max_value=max_avail)
        if isinstance(custom_date_range, (tuple, list)) and len(custom_date_range) == 2:
            c_start, c_end = custom_date_range
        elif isinstance(custom_date_range, (tuple, list)) and len(custom_date_range) == 1:
            c_start = c_end = custom_date_range[0]
        else:
            c_start = c_end = custom_date_range
            
        filtered_df = school_filtered_df[(school_filtered_df['Date'] >= c_start) & (school_filtered_df['Date'] <= c_end)]
        selected_num_days = get_working_days(c_start, c_end, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Custom Range: {c_start} to {c_end} ({selected_num_days} Working Day(s))"

    calc_ld_kpi = daily_ld_target * selected_num_days
    calc_lib_kpi = daily_lib_target * selected_num_days

    # --- 5. GLOBAL TEACHER FILTER ---
    available_teachers = sorted([str(t) for t in school_master_roster['FullName'].unique() if str(t).strip()])
    selected_teachers = st.sidebar.multiselect("Select Teacher(s)", options=available_teachers, default=available_teachers)
    
    filtered_roster = school_master_roster[school_master_roster['FullName'].isin(selected_teachers)]
    filtered_df = filtered_df[filtered_df['FullName'].isin(selected_teachers)]

    # 7 Dedicated Active Tabs
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📘 1. Lesson Plan Preparation Tracker", 
        "📚 2. Library Usage Tracker", 
        "📖 3. Content & Chapters", 
        "👤 4. Teacher 360° Profile Report",
        "🏛️ 5. Manager Portfolio Quadrants",
        "🏫 6. School Teacher Progression",
        "📬 7. Live Evidence Submissions Feed"
    ])

    # TAB 1: LESSON PLAN PREPARATION TRACKER
    with tab1:
        st.header("📘 Lesson Plan Preparation Tracker")
        if enable_quant_kpi and calc_ld_kpi > 0:
            st.caption(f"Benchmark Standard: **At least {calc_ld_kpi:.0f} Minutes** ({daily_ld_target:.0f} mins/day across {selected_num_days} working day(s)).")
        else:
            st.caption(f"Reviewing cumulative minutes prepared across {selected_num_days} working day(s).")

        ld_df = filtered_df[filtered_df['Type'] == 'lessonDelivery']
        ld_usage = ld_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index()
        ld_daily = filtered_roster.merge(ld_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
        
        def get_ld_status(x):
            if not enable_quant_kpi or calc_ld_kpi == 0: 
                return 'Activity Logged' if x > 0 else 'No Activity Logged'
            if x >= calc_ld_kpi: 
                return f'✅ Met KPI (>= {calc_ld_kpi:.0f}m)'
            elif x > 0.0: 
                return f'⚠️ Below KPI (< {calc_ld_kpi:.0f}m)'
            else: 
                return '❌ Inactive (0 Mins)'
        
        ld_daily['KPI Status'] = ld_daily['Duration_Min'].apply(get_ld_status)

        c1, c2, c3, c4 = st.columns(4)
        total_teachers = len(ld_daily)
        met_count = len(ld_daily[ld_daily['Duration_Min'] >= calc_ld_kpi]) if (enable_quant_kpi and calc_ld_kpi > 0) else len(ld_daily[ld_daily['Duration_Min'] > 0])
        inactive_count = len(ld_daily[ld_daily['Duration_Min'] == 0.0])
        
        c1.metric("Total Roster Teachers", total_teachers)
        c2.metric(f"Met Standard ({calc_ld_kpi:.0f}m)" if enable_quant_kpi else "Active Teachers", f"{met_count} / {total_teachers}")
        c3.metric("Inactive Teachers (0m)", inactive_count, delta=f"{-inactive_count}" if inactive_count > 0 else "0", delta_color="inverse")
        c4.metric("Compliance Rate", f"{(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%")

        fig_ld = px.bar(
            ld_daily, x="FullName", y="Duration_Min", color="KPI Status",
            title=f"Lesson Prep Minutes per Teacher" + (f" vs. {calc_ld_kpi:.0f} Min Standard" if enable_quant_kpi else ""),
            labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Prepared"},
            text_auto=".1f"
        )
        if enable_quant_kpi and calc_ld_kpi > 0:
            fig_ld.add_hline(y=calc_ld_kpi, line_dash="dash", line_color="black", annotation_text=f"Guideline ({calc_ld_kpi:.0f} mins)")
        st.plotly_chart(fig_ld, use_container_width=True)

        st.subheader("📋 Lesson Plan Preparation Table")
        display_ld_table = ld_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
        st.dataframe(display_ld_table, use_container_width=True)

        pdf_tab1 = generate_pdf_report(
            title_text="📘 Lesson Plan Preparation Report",
            subtitle_text=f"Filter: {filter_description_text} | Total Teachers: {total_teachers}",
            summary_metrics={
                "Total Teachers": total_teachers,
                "Active Teachers": f"{met_count} / {total_teachers}",
                "Compliance Rate": f"{(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%"
            },
            dataframe=display_ld_table[['School', 'Teacher Name', 'Minutes Logged', 'KPI Status']]
        )
        st.download_button(
            label="📄 Download Tab 1 Report (PDF)",
            data=pdf_tab1,
            file_name=f"Lesson_Plan_Prep_Report_{selected_month.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )

    # TAB 2: LIBRARY USAGE TRACKER
    with tab2:
        st.header("📚 Library Usage Tracker")
        if enable_quant_kpi and calc_lib_kpi > 0:
            st.caption(f"Benchmark Standard: **At least {calc_lib_kpi:.0f} Minutes** ({daily_lib_target:.0f} mins/day across {selected_num_days} working day(s)).")
        else:
            st.caption(f"Reviewing cumulative library usage minutes across {selected_num_days} working day(s).")

        lib_df = filtered_df[filtered_df['Type'] == 'library']
        lib_usage = lib_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index()
        lib_daily = filtered_roster.merge(lib_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
        
        def get_lib_status(x):
            if not enable_quant_kpi or calc_lib_kpi == 0: 
                return 'Activity Logged' if x > 0 else 'No Activity Logged'
            if x >= calc_lib_kpi: 
                return f'✅ Met KPI (>= {calc_lib_kpi:.0f}m)'
            elif x > 0.0: 
                return f'⚠️ Below KPI (< {calc_lib_kpi:.0f}m)'
            else: 
                return '❌ Inactive (0 Mins)'

        lib_daily['KPI Status'] = lib_daily['Duration_Min'].apply(get_lib_status)

        m1, m2, m3, m4 = st.columns(4)
        lib_total_teachers = len(lib_daily)
        lib_met_count = len(lib_daily[lib_daily['Duration_Min'] >= calc_lib_kpi]) if (enable_quant_kpi and calc_lib_kpi > 0) else len(lib_daily[lib_daily['Duration_Min'] > 0])
        lib_inactive_count = len(lib_daily[lib_daily['Duration_Min'] == 0.0])
        
        m1.metric("Total Roster Teachers", lib_total_teachers)
        m2.metric(f"Met Standard ({calc_lib_kpi:.0f}m)" if enable_quant_kpi else "Active Teachers", f"{lib_met_count} / {lib_total_teachers}")
        m3.metric("Inactive Teachers (0m)", lib_inactive_count, delta=f"{-lib_inactive_count}" if lib_inactive_count > 0 else "0", delta_color="inverse")
        m4.metric("Engagement Rate", f"{(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%")

        fig_lib = px.bar(
            lib_daily, x="FullName", y="Duration_Min", color="KPI Status",
            title=f"Library Usage Minutes per Teacher" + (f" vs. {calc_lib_kpi:.0f} Min Standard" if enable_quant_kpi else ""),
            labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Logged"},
            text_auto=".1f"
        )
        if enable_quant_kpi and calc_lib_kpi > 0:
            fig_lib.add_hline(y=calc_lib_kpi, line_dash="dash", line_color="black", annotation_text=f"Guideline ({calc_lib_kpi:.0f} mins)")
        st.plotly_chart(fig_lib, use_container_width=True)

        st.subheader("📋 Library Usage Table")
        display_lib_table = lib_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
        st.dataframe(display_lib_table, use_container_width=True)

        pdf_tab2 = generate_pdf_report(
            title_text="📚 Library Usage Report",
            subtitle_text=f"Filter: {filter_description_text} | Total Teachers: {lib_total_teachers}",
            summary_metrics={
                "Total Teachers": lib_total_teachers,
                "Active Teachers": f"{lib_met_count} / {lib_total_teachers}",
                "Engagement Rate": f"{(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%"
            },
            dataframe=display_lib_table[['School', 'Teacher Name', 'Minutes Logged', 'KPI Status']]
        )
        st.download_button(
            label="📄 Download Tab 2 Report (PDF)",
            data=pdf_tab2,
            file_name=f"Library_Usage_Report_{selected_month.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )

    # TAB 3: CONTENT & CHAPTERS
    with tab3:
        st.header("📖 Content & Chapters")
        st.caption(f"Track specific books, subjects, and instructional modules opened during `{filter_description_text}`.")

        content_df = filtered_df[filtered_df['Book'].str.len() > 0]

        if content_df.empty:
            st.info("No specific chapter/book access logs found in the uploaded data for the selected global filters.")
        else:
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                t3_school_opt = ["All Selected Schools"] + sorted(content_df['Institution'].unique().tolist())
                t3_school = st.selectbox("🏫 Select School:", t3_school_opt, key="t3_school")
                
            t3_df = content_df if t3_school == "All Selected Schools" else content_df[content_df['Institution'] == t3_school]

            with col_f2:
                t3_teacher_opt = ["All Teachers"] + sorted(t3_df['FullName'].unique().tolist())
                t3_teacher = st.selectbox("👤 Select Teacher:", t3_teacher_opt, key="t3_teacher")
                
            if t3_teacher != "All Teachers":
                t3_df = t3_df[t3_df['FullName'] == t3_teacher]

            with col_f3:
                t3_subject_opt = ["All Subjects"] + sorted(t3_df['Subject'].unique().tolist())
                t3_subject = st.selectbox("📚 Select Subject:", t3_subject_opt, key="t3_subject")

            if t3_subject != "All Subjects":
                t3_df = t3_df[t3_df['Subject'] == t3_subject]

            st.markdown("---")

            if t3_df.empty:
                st.warning("No data matches these specific drill-down filters.")
            else:
                k1, k2, k3 = st.columns(3)
                k1.metric("Chapters / Books Opened", t3_df['Book'].nunique())
                k2.metric("Subjects Taught", t3_df['Subject'].nunique())
                k3.metric("Total Content Access Time", f"{t3_df['Duration_Min'].sum():.1f} Mins")

                col_c1, col_c2 = st.columns(2)
                with col_c1:
                    if t3_teacher != "All Teachers":
                        ch_summary = t3_df.groupby(['Book', 'Grade'])['Duration_Min'].sum().reset_index()
                        fig_ch = px.bar(
                            ch_summary, x="Duration_Min", y="Book", color="Grade", orientation="h",
                            title=f"Chapters Opened by {t3_teacher} (Mins)",
                            labels={"Duration_Min": "Minutes", "Book": "Book / Chapter"},
                            text_auto=".1f"
                        )
                        fig_ch.update_layout(yaxis={'categoryorder':'total ascending'})
                    else:
                        ch_summary = t3_df.groupby(['FullName', 'Book'])['Duration_Min'].sum().reset_index()
                        fig_ch = px.bar(
                            ch_summary, x="FullName", y="Duration_Min", color="Book",
                            title="Chapters / Books Opened per Teacher (Mins)",
                            labels={"FullName": "Teacher", "Duration_Min": "Minutes", "Book": "Book / Chapter"},
                            barmode="stack", text_auto=".1f"
                        )
                    st.plotly_chart(fig_ch, use_container_width=True)

                with col_c2:
                    subj_summary = t3_df.groupby('Subject')['Duration_Min'].sum().reset_index()
                    fig_sub = px.pie(
                        subj_summary, names="Subject", values="Duration_Min",
                        title="Subject / Theme Distribution (Minutes)"
                    )
                    st.plotly_chart(fig_sub, use_container_width=True)

                st.subheader("📋 Filtered Granular Class Log")
                log_cols = ['Institution', 'FullName', 'Grade', 'Subject', 'Book', 'StartTime', 'Duration (HH:MM:SS)', 'Duration_Min']
                available_cols = [c for c in log_cols if c in t3_df.columns]
                
                display_content_log = t3_df[available_cols].rename(columns={
                    'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes'
                }).sort_values(by='StartTime', ascending=False)
                display_content_log['Minutes'] = display_content_log['Minutes'].round(1)
                st.dataframe(display_content_log, use_container_width=True)

                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    csv_t3 = display_content_log.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Download Content Log (CSV)",
                        data=csv_t3,
                        file_name=f"Content_Log_{selected_month.replace(' ', '_')}.csv",
                        mime="text/csv"
                    )
                with col_d2:
                    pdf_tab3 = generate_pdf_report(
                        title_text="📖 Chapters & Digital Content Usage Report",
                        subtitle_text=f"School: {t3_school} | Teacher: {t3_teacher} | Subject: {t3_subject}",
                        summary_metrics={
                            "Chapters Opened": t3_df['Book'].nunique(),
                            "Subjects Taught": t3_df['Subject'].nunique(),
                            "Total Duration": f"{t3_df['Duration_Min'].sum():.1f} Mins"
                        },
                        dataframe=display_content_log[['School', 'Teacher Name', 'Grade', 'Subject', 'Book', 'Minutes']].head(30)
                    )
                    st.download_button(
                        label="📄 Download Tab 3 Content Report (PDF)",
                        data=pdf_tab3,
                        file_name=f"Content_Usage_Report_{selected_month.replace(' ', '_')}.pdf",
                        mime="application/pdf"
                    )

    # TAB 4: SINGLE TEACHER 360° PROFILE REPORT (EXECUTIVE SUMMARY SCOPE)
    with tab4:
        st.header("👤 Teacher 360° Performance Profile")
        st.caption("Review quantitative lesson metrics, digital content logs, and structured qualitative performance evidence for executive leadership and school owner reporting.")

        all_roster_teachers = sorted(school_master_roster['FullName'].unique())
        
        if not all_roster_teachers:
            st.info("No teachers found in roster for the selected school(s).")
        else:
            target_teacher = st.selectbox("Select Teacher to Audit:", options=all_roster_teachers)
            
            teacher_all_data = school_filtered_df[school_filtered_df['FullName'] == target_teacher]
            teacher_date_data = filtered_df[filtered_df['FullName'] == target_teacher]
            teacher_school = school_master_roster[school_master_roster['FullName'] == target_teacher]['Institution'].values[0] if not school_master_roster[school_master_roster['FullName'] == target_teacher].empty else "N/A"

            st.markdown(f"### 📋 Audit Profile: **{target_teacher}** | School: **{teacher_school}**")

            # SECTION 1: PERFORMANCE INDICATOR SUMMARY
            st.subheader("1. Quantitative Performance Indicator Summary")
            st.info(f"📅 **Active Filter**: `{filter_description_text}` | **KPI Duration**: `{selected_num_days} Working Day(s)`")

            t_day_ld = teacher_date_data[teacher_date_data['Type'] == 'lessonDelivery']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
            t_day_lib = teacher_date_data[teacher_date_data['Type'] == 'library']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
            
            ld_pct = (t_day_ld / calc_ld_kpi) * 100 if calc_ld_kpi > 0 else (100.0 if t_day_ld >= 0 else 0)
            lib_pct = (t_day_lib / calc_lib_kpi) * 100 if calc_lib_kpi > 0 else (100.0 if t_day_lib >= 0 else 0)

            if calc_ld_kpi > 0:
                ld_advice = f"🌟 Steady Execution ({t_day_ld:.1f}m logged)" if t_day_ld >= calc_ld_kpi else (f"⚠️ In-Progress ({t_day_ld:.1f}m logged)" if t_day_ld > 0 else "❌ Pending Activity")
            else:
                ld_advice = "✅ Holiday / Scheduled Break"

            if calc_lib_kpi > 0:
                lib_advice = f"🌟 Steady Execution ({t_day_lib:.1f}m logged)" if t_day_lib >= calc_lib_kpi else (f"⚠️ In-Progress ({t_day_lib:.1f}m logged)" if t_day_lib > 0 else "❌ Pending Activity")
            else:
                lib_advice = "✅ Holiday / Scheduled Break"

            col_sum1, col_sum2 = st.columns([1, 1.2])

            with col_sum1:
                st.markdown("##### 📌 Quantitative KPI Overview")
                s1, s2 = st.columns(2)
                s1.metric("Lesson Prep Mins", f"{t_day_ld:.1f} mins", delta=f"{ld_pct:.0f}% of Standard" if enable_quant_kpi else None)
                s2.metric("Library Usage Mins", f"{t_day_lib:.1f} mins", delta=f"{lib_pct:.0f}% of Standard" if enable_quant_kpi else None)
                
                st.markdown("##### 💡 Academic Consultant Observation")
                if calc_ld_kpi == 0 and calc_lib_kpi == 0:
                    st.info(f"🏖️ **Break Period**: Active filter falls on an excluded calendar break.")
                elif t_day_ld >= calc_ld_kpi and t_day_lib >= calc_lib_kpi:
                    st.success(f"👏 **Consistent Delivery**: {target_teacher} maintained steady curriculum prep and library engagement across this period.")
                elif t_day_ld < calc_ld_kpi and t_day_lib < calc_lib_kpi:
                    st.warning(f"💡 **Growth Opportunity**: Focus on structured digital planning hours and regular library exploration.")
                else:
                    st.info(f"📌 **Balanced Usage**: Progress noted in digital usage with potential to scale library integration.")

                st.write(f"• **Lesson Plan Preparation**: {ld_advice}")
                st.write(f"• **Library Usage Engagement**: {lib_advice}")

            with col_sum2:
                st.markdown("##### 📊 KPI Achievement Comparison")
                ach_df = pd.DataFrame({
                    'KPI Category': [f'Lesson Prep ({calc_ld_kpi:.0f}m)' if enable_quant_kpi else 'Lesson Prep', 
                                     f'Library Usage ({calc_lib_kpi:.0f}m)' if enable_quant_kpi else 'Library Usage'],
                    'Logged Minutes': [t_day_ld, t_day_lib],
                    'KPI Standard': [calc_ld_kpi, calc_lib_kpi]
                })
                
                fig_ach = go.Figure()
                fig_ach.add_trace(go.Bar(
                    x=ach_df['KPI Category'], y=ach_df['Logged Minutes'],
                    name='Logged Minutes', marker_color='#2CA02C', text=[f"{v:.1f} mins" for v in ach_df['Logged Minutes']], textposition='auto'
                ))
                if enable_quant_kpi:
                    fig_ach.add_trace(go.Bar(
                        x=ach_df['KPI Category'], y=ach_df['KPI Standard'],
                        name='Standard Guideline', marker_color='#E5E5E5', opacity=0.6, text=[f"{v:.1f} mins" for v in ach_df['KPI Standard']], textposition='auto'
                    ))
                fig_ach.update_layout(
                    barmode='group', title=f"Logged Minutes vs. Standard Guideline ({selected_num_days} Working Day(s))",
                    height=280, margin=dict(l=20, r=20, t=40, b=20)
                )
                st.plotly_chart(fig_ach, use_container_width=True)

            st.markdown("---")

            # SECTION 2: DIGITAL CONTENT & BOOK USAGE REPORT (WITH ALL-TIME FALLBACK)
            st.subheader("2. Book & Grade Digital Content Usage Report")
            teacher_books = teacher_date_data[teacher_date_data['Book'].str.len() > 0]
            if teacher_books.empty:
                teacher_books = teacher_all_data[teacher_all_data['Book'].str.len() > 0]
            
            if teacher_books.empty:
                st.info(f"No digital chapters or modules recorded for **{target_teacher}**.")
            else:
                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    t_book_summary = teacher_books.groupby(['Book', 'Grade'])['Duration_Min'].sum().reset_index()
                    fig_tb_bar = px.bar(
                        t_book_summary, x="Duration_Min", y="Book", color="Grade", orientation="h",
                        title=f"Books & Chapters Opened by {target_teacher} (Minutes)",
                        labels={"Duration_Min": "Minutes", "Book": "Book / Chapter"},
                        text_auto=".1f"
                    )
                    fig_tb_bar.update_layout(yaxis={'categoryorder':'total ascending'}, height=320)
                    st.plotly_chart(fig_tb_bar, use_container_width=True)
                    
                with col_b2:
                    t_grade_summary = teacher_books.groupby('Grade')['Duration_Min'].sum().reset_index()
                    fig_tg_pie = px.pie(
                        t_grade_summary, names="Grade", values="Duration_Min",
                        title=f"Grade-Level Digital Time Share & Duration for {target_teacher}"
                    )
                    fig_tg_pie.update_traces(
                        textinfo='value+percent',
                        texttemplate='%{label}: %{value:.1f} Mins<br>(%{percent})',
                        hovertemplate='<b>%{label}</b><br>Time Spent: %{value:.1f} Mins<br>Share: %{percent}'
                    )
                    fig_tg_pie.update_layout(height=320)
                    st.plotly_chart(fig_tg_pie, use_container_width=True)

            st.markdown("---")

            # SECTION 3: QUALITATIVE PERFORMANCE EVIDENCE (ORDERED: 1. LP/VN -> 2. ACTIVITIES -> 3. WRITING)
            st.subheader("3. Qualitative Evidences & Artifact Hub")

            evidence_source = teacher_date_data if not teacher_date_data.empty else teacher_all_data
            
            def extract_evidence_items(df_src, col_name):
                if col_name not in df_src.columns:
                    return []
                items = []
                for _, r in df_src.iterrows():
                    val = str(r[col_name]).strip()
                    if re.match(r'^https?://', val, re.IGNORECASE):
                        d_str = str(r['Date']) if 'Date' in r and pd.notna(r['Date']) else "Recent"
                        g_str = f"Grade {r['Grade']}" if 'Grade' in r and str(r['Grade']).strip() else "Grade N/A"
                        s_str = str(r['Subject']).strip() if 'Subject' in r and str(r['Subject']).strip() else "General Subject"
                        b_str = str(r['Book']).strip() if 'Book' in r and str(r['Book']).strip() else "Lesson Plan"
                        items.append({
                            'url': val,
                            'date': d_str,
                            'grade': g_str,
                            'subject': s_str,
                            'lesson': b_str
                        })
                seen = set()
                deduped = []
                for item in items:
                    if item['url'] not in seen:
                        seen.add(item['url'])
                        deduped.append(item)
                return deduped

            v_voice = extract_evidence_items(evidence_source, 'Voice_Note_Link')
            v_pic = extract_evidence_items(evidence_source, 'Lesson_Plan_Picture')
            v_writing = extract_evidence_items(evidence_source, 'Writing_Sample_Link')

            v_vid = []
            for col in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                v_vid.extend(extract_evidence_items(evidence_source, col))
            seen_v = set()
            deduped_v = []
            for item in v_vid:
                if item['url'] not in seen_v:
                    seen_v.add(item['url'])
                    deduped_v.append(item)
            v_vid = deduped_v

            # Single unified LP entity calculation (satisfied by either Voice Notes or LP Pictures)
            lp_combo_total = len(v_voice) + len(v_pic)

            # Metric Cards in requested structured order
            v_cols = st.columns(3)
            v_cols[0].metric("📖 1. Lesson Plans / Audio Notes", f"{lp_combo_total} Submissions", delta=f"{len(v_voice)} Audio | {len(v_pic)} Picture")
            v_cols[1].metric("🎥 2. Classroom Activity Videos", f"{len(v_vid)} Uploaded")
            v_cols[2].metric("📝 3. Student Writing Practices", f"{len(v_writing)} Samples")

            # Structured Artifact Inspection Grid
            st.markdown("##### 📌 Qualitative Artifact Review Hub")
            q_cols1, q_cols2, q_cols3 = st.columns(3)
            
            with q_cols1:
                st.markdown("###### 📖 1. Lesson Plans & Pre-Class Voice Notes")
                combined_lp_items = []
                for item in v_voice:
                    combined_lp_items.append(f"🎧 [Audio Note]({item['url']}) - **{item['grade']}** | *{item['subject']}* ({item['lesson']}, {item['date']})")
                for item in v_pic:
                    combined_lp_items.append(f"🖼️ [LP Picture]({item['url']}) - **{item['grade']}** | *{item['subject']}* ({item['lesson']}, {item['date']})")
                
                if combined_lp_items:
                    for line in combined_lp_items:
                        st.markdown(f"• {line}")
                else:
                    st.caption("No lesson plans or voice reflections submitted in this window.")

            with q_cols2:
                st.markdown("###### 🎥 2. Classroom Activity Execution Videos")
                if v_vid:
                    for item in v_vid:
                        st.markdown(f"• 🎥 [Watch Video]({item['url']}) - **{item['grade']}** | *{item['subject']}* ({item['lesson']}, {item['date']})")
                else:
                    st.caption("No classroom activity videos uploaded in this window.")

            with q_cols3:
                st.markdown("###### 📝 3. Student Writing Practice Samples")
                if v_writing:
                    for item in v_writing:
                        st.markdown(f"• 📝 [View Writing]({item['url']}) - **{item['grade']}** | *{item['subject']}* ({item['lesson']}, {item['date']})")
                else:
                    st.caption("No student writing samples uploaded in this window.")

            st.markdown("---")

            # --- WHATSAPP EXECUTIVE SUMMARY EXPORT (SECTIONS 1 TO 3 ONLY) ---
            st.subheader("📲 WhatsApp Executive Summary Export (Sections 1–3)")
            st.caption("Generate a clean executive review for School Owners and Leadership containing Teacher Profile details, Quantitative Highlights, Digital Book Logs, and Qualitative Evidence Summaries (excluding granular logs).")

            pdf_custom_sections = {
                "1. Quantitative Delivery & Planning Highlights": [
                    f"Lesson Preparation Duration: {t_day_ld:.1f} Minutes" + (f" ({ld_pct:.0f}% of Academic Benchmark)" if enable_quant_kpi else ""),
                    f"Library & Digital Resources Duration: {t_day_lib:.1f} Minutes" + (f" ({lib_pct:.0f}% of Academic Benchmark)" if enable_quant_kpi else ""),
                    f"Consultant Assessment: {ld_advice} in lesson preparation, {lib_advice} in library integration."
                ],
                "2. Digital Content & Curriculum Pacing": [
                    f"Distinct Books/Chapters Opened: {teacher_books['Book'].nunique() if not teacher_books.empty else 0}",
                    f"Grade Levels Covered: {', '.join(teacher_books['Grade'].unique().tolist()) if not teacher_books.empty else 'General'}"
                ],
                "3. Qualitative Evidence & Pedagogy Verification": [
                    f"Lesson Plans & Audio Reflections: {lp_combo_total} Artifact(s) Submitted ({len(v_voice)} voice notes, {len(v_pic)} board/plan photos)",
                    f"Classroom Activity Videos: {len(v_vid)} Video(s) Audited",
                    f"Student Writing Practice Samples: {len(v_writing)} Verified Artifact(s)"
                ]
            }

            pdf_tab4_summary = generate_pdf_report(
                title_text=f"🏫 Academic Performance Profile: {target_teacher}",
                subtitle_text=f"Institution: {teacher_school} | Observation Window: {filter_description_text}",
                summary_metrics={
                    "Teacher": target_teacher,
                    "School": teacher_school,
                    "Lesson Prep": f"{t_day_ld:.1f}m",
                    "Library Usage": f"{t_day_lib:.1f}m",
                    "Qualitative Artifacts": f"{lp_combo_total + len(v_vid) + len(v_writing)}"
                },
                dataframe=None,
                custom_sections=pdf_custom_sections
            )

            col_wa1, col_wa2 = st.columns([1, 1.5])
            with col_wa1:
                st.download_button(
                    label="📄 Download WhatsApp Executive Summary (PDF)",
                    data=pdf_tab4_summary,
                    file_name=f"{target_teacher.replace(' ', '_')}_Executive_Summary.pdf",
                    mime="application/pdf"
                )

            with col_wa2:
                whatsapp_text_template = (
                    f"🏫 *ACADEMIC CONSULTANT REVIEW - TEACHER 360° PROFILE*\n"
                    f"👤 *Teacher:* {target_teacher}\n"
                    f"🏛️ *School:* {teacher_school}\n"
                    f"📅 *Review Period:* {filter_description_text}\n\n"
                    f"📊 *1. Quantitative Engagement:*\n"
                    f"• Lesson Prep: {t_day_ld:.1f} Mins\n"
                    f"• Library Usage: {t_day_lib:.1f} Mins\n\n"
                    f"📖 *2. Curriculum & Digital Coverage:*\n"
                    f"• Books & Modules Opened: {teacher_books['Book'].nunique() if not teacher_books.empty else 0}\n\n"
                    f"🎨 *3. Qualitative Evidence & Pedagogy:*\n"
                    f"• Lesson Plans & Audio Reflections: {lp_combo_total} verified\n"
                    f"• Classroom Activity Videos: {len(v_vid)} recorded\n"
                    f"• Student Writing Practice Samples: {len(v_writing)} submitted\n\n"
                    f"💡 *Consultant Feedback:* {target_teacher} is progressing well with curriculum delivery and digital classroom integration."
                )
                with st.expander("📋 Click to View & Copy WhatsApp Message Text"):
                    st.text_area("WhatsApp Copyable Message Text", value=whatsapp_text_template, height=180)

            st.markdown("---")

            # SECTION 4: CLASSROOM AUDIT LOG
            col_log_head, col_log_filt = st.columns([2, 1])
            with col_log_head:
                st.subheader(f"4. Granular Classroom Audit Log for {target_teacher}")
            with col_log_filt:
                available_types = ["All Types"] + sorted(teacher_all_data['Type'].dropna().unique().tolist())
                selected_type_filter = st.selectbox("Filter Audit Log by Type:", options=available_types)

            if selected_type_filter == "All Types":
                filtered_audit_log = teacher_all_data
            else:
                filtered_audit_log = teacher_all_data[teacher_all_data['Type'] == selected_type_filter]

            t_log_cols = ['Date', 'Type', 'Grade', 'Subject', 'Book', 'StartTime', 'Duration (HH:MM:SS)', 'Duration_Min']
            t_avail_cols = [c for c in t_log_cols if c in filtered_audit_log.columns]
            
            if filtered_audit_log.empty:
                st.info(f"No logs found for type `{selected_type_filter}` during `{filter_description_text}`.")
            else:
                t_display_log = filtered_audit_log[t_avail_cols].rename(columns={'Duration_Min': 'Minutes'}).sort_values(by='StartTime', ascending=False)
                t_display_log['Minutes'] = t_display_log['Minutes'].round(1)
                st.dataframe(t_display_log, use_container_width=True)

                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    csv_profile = t_display_log.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label=f"📥 Download Full CSV Audit for {target_teacher}",
                        data=csv_profile,
                        file_name=f"{target_teacher.replace(' ', '_')}_{selected_type_filter}_Audit.csv",
                        mime="text/csv"
                    )

    # TAB 5: MANAGER PORTFOLIO & SCHOOL QUADRANTS (WITH CRM & FILTERED DISCUSSION LOGS)
    with tab5:
        st.header("🏛️ Academic Manager Portfolio Overview")
        st.caption("High-level classification, Quantitative indicators, and Week-on-Week Velocity tracking across your school portfolio.")

        if school_filtered_df.empty:
            st.warning("No data available for the selected school filter.")
        else:
            school_stats = school_filtered_df.groupby(['Institution', 'Type'])['Duration_Min'].sum().unstack(fill_value=0.0).reset_index()
            
            if 'lessonDelivery' not in school_stats.columns: school_stats['lessonDelivery'] = 0.0
            if 'library' not in school_stats.columns: school_stats['library'] = 0.0
            
            school_roster_count = school_master_roster.groupby('Institution')['FullName'].nunique().reset_index().rename(columns={'FullName': 'Roster_Teachers'})
            school_stats = school_stats.merge(school_roster_count, on='Institution', how='left').fillna(1)

            school_stats['Avg_Lesson_Prep_Mins'] = (school_stats['lessonDelivery'] / school_stats['Roster_Teachers'] / selected_num_days).round(1)
            school_stats['Avg_Library_Usage_Mins'] = (school_stats['library'] / school_stats['Roster_Teachers'] / selected_num_days).round(1)

            # Compute Qualitative Artifact Counts per School
            qual_agg = []
            for s_name in school_stats['Institution'].unique():
                s_data = filtered_df[filtered_df['Institution'] == s_name]
                s_vids = 0
                for vc in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                    if vc in s_data.columns:
                        s_vids += len([l for l in s_data[vc].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])
                s_w = len([l for l in s_data['Writing_Sample_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Writing_Sample_Link' in s_data.columns else 0
                s_lp = len([l for l in s_data['Lesson_Plan_Picture'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in s_data.columns else 0
                s_vn = len([l for l in s_data['Voice_Note_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in s_data.columns else 0
                
                qual_agg.append({
                    'Institution': s_name,
                    'Activity_Videos': s_vids,
                    'Writing_Samples': s_w,
                    'LP_Audio_Submissions': s_lp + s_vn
                })
            
            qual_df_school = pd.DataFrame(qual_agg)
            school_stats = school_stats.merge(qual_df_school, on='Institution', how='left').fillna(0)

            def classify_school(row):
                if not enable_quant_kpi:
                    return 'Active Portfolio'
                ld_ok = row['Avg_Lesson_Prep_Mins'] >= daily_ld_target
                lib_ok = row['Avg_Library_Usage_Mins'] >= daily_lib_target
                
                qual_ok = True
                if enable_qual_kpi:
                    qual_ok = (row['Activity_Videos'] >= target_vid_count) or (row['Writing_Samples'] >= target_writing_count)

                if ld_ok and lib_ok and qual_ok:
                    return '🌟 Pace Setters'
                elif ld_ok and not lib_ok:
                    return '📘 Lesson Focused'
                elif not ld_ok and lib_ok:
                    return '📚 Library Focused'
                else:
                    return '🚨 Priority Focus'

            school_stats['Classification'] = school_stats.apply(classify_school, axis=1)

            # --- 2x2 QUADRANT MATRIX GRID ---
            st.subheader("🖼️ 2x2 Portfolio Classification Matrix")
            
            pace_setters = school_stats[school_stats['Classification'] == '🌟 Pace Setters']['Institution'].tolist()
            lesson_focused = school_stats[school_stats['Classification'] == '📘 Lesson Focused']['Institution'].tolist()
            library_focused = school_stats[school_stats['Classification'] == '📚 Library Focused']['Institution'].tolist()
            priority_focus = school_stats[school_stats['Classification'] == '🚨 Priority Focus']['Institution'].tolist()

            col_top1, col_top2 = st.columns(2)
            with col_top1:
                st.success(f"🌟 **Pace Setters ({len(pace_setters)} Schools)**\n\n*Met Lesson Prep, Library & Qualitative Artifact Standards*\n\n" + (", ".join(pace_setters) if pace_setters else "None"))
            with col_top2:
                st.info(f"📘 **Lesson Focused ({len(lesson_focused)} Schools)**\n\n*Met Lesson Prep, Below Library/Artifact Targets*\n\n" + (", ".join(lesson_focused) if lesson_focused else "None"))

            col_bot1, col_bot2 = st.columns(2)
            with col_bot1:
                st.warning(f"📚 **Library Focused ({len(library_focused)} Schools)**\n\n*Met Library, Below Lesson Prep Targets*\n\n" + (", ".join(library_focused) if library_focused else "None"))
            with col_bot2:
                st.error(f"🚨 **Priority Focus ({len(priority_focus)} Schools)**\n\n*Below Quantitative & Qualitative Standards*\n\n" + (", ".join(priority_focus) if priority_focus else "None"))

            st.markdown("---")
            st.subheader("📋 Complete School Performance Leaderboard (Quantitative & Qualitative)")
            display_qtable = school_stats[['Institution', 'Roster_Teachers', 'Avg_Lesson_Prep_Mins', 'Avg_Library_Usage_Mins', 'LP_Audio_Submissions', 'Activity_Videos', 'Writing_Samples', 'Classification']].rename(columns={
                'Institution': 'School Name',
                'Roster_Teachers': 'Active Teachers',
                'Avg_Lesson_Prep_Mins': 'Prep (m/day)',
                'Avg_Library_Usage_Mins': 'Library (m/day)',
                'LP_Audio_Submissions': 'LP/Voice Notes',
                'Activity_Videos': 'Activity Videos',
                'Writing_Samples': 'Writing Samples'
            })
            st.dataframe(display_qtable, use_container_width=True)

            pdf_tab5 = generate_pdf_report(
                title_text="🏛️ Academic Manager Portfolio Review",
                subtitle_text=f"Portfolio Performance Leaderboard ({selected_num_days} Working Days)",
                summary_metrics={
                    "Total Schools": len(school_stats),
                    "Pace Setters": len(pace_setters),
                    "Priority Focus": len(priority_focus)
                },
                dataframe=display_qtable
            )
            st.download_button(
                label="📄 Download Portfolio Overview Report (PDF)",
                data=pdf_tab5,
                file_name=f"Manager_Portfolio_Overview_{selected_month.replace(' ', '_')}.pdf",
                mime="application/pdf"
            )

            st.markdown("---")

            # --- SCHOOL OWNER CRM CONTACT DIRECTORY & FILTER-AWARE CALL LOG ---
            st.subheader("📞 School Owner CRM, Call Script & Discussion Notes Log")
            st.caption(f"Active Observation Window: `{filter_description_text}`. Generate a data-driven talking script, call owners, and record date-stamped discussions.")

            if "school_contacts_directory" not in st.session_state:
                st.session_state["school_contacts_directory"] = {
                    "Pragyanam International School": "+91 98260XXXXX"
                }

            if "school_call_logs_store" not in st.session_state:
                st.session_state["school_call_logs_store"] = []

            all_portfolio_schools = sorted(school_stats['Institution'].unique().tolist())

            with st.expander("⚙️ Manage School Owner Contact Numbers (CRM Directory)", expanded=False):
                st.markdown("Update or punch phone numbers for your school portfolio:")
                for sch in all_portfolio_schools:
                    current_num = st.session_state["school_contacts_directory"].get(sch, "")
                    new_num = st.text_input(f"Owner Phone for {sch}:", value=current_num, key=f"dir_phone_{sch}")
                    st.session_state["school_contacts_directory"][sch] = new_num

            st.markdown("---")

            selected_call_school = st.selectbox("Select School for Owner Discussion & CRM Call:", options=all_portfolio_schools, key="call_school_select")
            owner_phone = st.session_state["school_contacts_directory"].get(selected_call_school, "Not Provided")

            sch_row = school_stats[school_stats['Institution'] == selected_call_school]
            sch_prep = float(sch_row['Avg_Lesson_Prep_Mins'].values[0]) if not sch_row.empty else 0.0
            sch_lib = float(sch_row['Avg_Library_Usage_Mins'].values[0]) if not sch_row.empty else 0.0
            sch_vids = int(sch_row['Activity_Videos'].values[0]) if not sch_row.empty else 0
            sch_writing = int(sch_row['Writing_Samples'].values[0]) if not sch_row.empty else 0
            sch_lp = int(sch_row['LP_Audio_Submissions'].values[0]) if not sch_row.empty else 0
            sch_class = sch_row['Classification'].values[0] if not sch_row.empty else "Active Portfolio"

            col_call_info1, col_call_info2 = st.columns([1, 2])
            with col_call_info1:
                st.markdown(f"**Selected School:** `{selected_call_school}`")
                st.markdown(f"**Owner Contact:** `{owner_phone}`")
                st.markdown(f"**Portfolio Status:** `{sch_class}`")
                
                if owner_phone and owner_phone != "Not Provided":
                    clean_phone = re.sub(r'[^0-9+]', '', owner_phone)
                    wa_msg = urllib.parse.quote(f"Hello, checking in from Academic Management regarding recent portfolio execution metrics for {selected_call_school} during {filter_description_text}.")
                    
                    st.markdown(f'<a href="tel:{owner_phone}" target="_blank" style="text-decoration:none;"><button style="background-color:#2CA02C;color:white;padding:8px 14px;border:none;border-radius:4px;cursor:pointer;font-weight:bold;margin-bottom:6px;width:100%;">📞 Call School Owner</button></a>', unsafe_allow_html=True)
                    st.markdown(f'<a href="https://wa.me/{clean_phone}?text={wa_msg}" target="_blank" style="text-decoration:none;"><button style="background-color:#25D366;color:white;padding:8px 14px;border:none;border-radius:4px;cursor:pointer;font-weight:bold;width:100%;">📱 Send WhatsApp Message</button></a>', unsafe_allow_html=True)
                else:
                    st.warning("Please punch a valid phone number in the CRM Directory above to enable direct calling/WhatsApp buttons.")

            with col_call_info2:
                # --- AUTOMATED AI CONSULTANT CALL SCRIPT GENERATOR ---
                with st.expander(f"🤖 View Generated Talking Script for {selected_call_school}", expanded=True):
                    st.markdown(f"""
                    **Phase 1 & 2: Data-Driven Talking Points ({filter_description_text})**
                    1. **Opening:** *"Hi [Principal/Owner Name], I was reviewing our portfolio dashboard for {selected_call_school} this week. Overall status is currently classified as **{sch_class}**."*
                    2. **Lesson Prep Review:** *"Teachers averaged **{sch_prep:.1f} mins/day** in lesson preparation. {'Great consistency!' if sch_prep >= daily_ld_target else 'Let us discuss how we can support teachers in locking in prep times.'}"*
                    3. **Library Usage Review:** *"Library engagement stands at **{sch_lib:.1f} mins/day**."*
                    4. **Lesson Plans & Voice Notes:** *"{sch_lp} verified pre-class voice reflections and lesson plan pictures have been logged."*
                    5. **Activity Videos:** *"{sch_vids} classroom activity execution video(s) have been audited."*
                    6. **Student Writing Practice:** *"{sch_writing} writing sample submission(s) recorded. Let us ensure notebook checks remain consistent next week."*
                    
                    **Phase 3: Closing & Action Item**
                    * *"To summarize, our main focus for next week will be balancing preparation with consistent artifact submissions. I will record our action items here."*
                    """)

                existing_entry = next((item for item in st.session_state["school_call_logs_store"] if item["School"] == selected_call_school and item["Review Period"] == filter_description_text), None)
                default_note_text = existing_entry["Discussion & Action Items"] if existing_entry else ""

                new_discussion_note = st.text_area(f"Discussion Notes & Action Items ({filter_description_text}):", value=default_note_text, height=110, key=f"note_{selected_call_school}_{filter_description_text}")
                
                col_save_b1, col_save_b2 = st.columns([1, 1])
                with col_save_b1:
                    punched_date = st.date_input("Call Date Stamp:", value=pd.Timestamp.now().date(), key=f"date_{selected_call_school}_{filter_description_text}")
                with col_save_b2:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("💾 Save Discussion Log", key=f"save_note_{selected_call_school}_{filter_description_text}"):
                        if new_discussion_note.strip():
                            st.session_state["school_call_logs_store"] = [
                                item for item in st.session_state["school_call_logs_store"] 
                                if not (item["School"] == selected_call_school and item["Review Period"] == filter_description_text)
                            ]

                            st.session_state["school_call_logs_store"].append({
                                "School": selected_call_school,
                                "Review Period": filter_description_text,
                                "Call Date": str(punched_date),
                                "Discussion & Action Items": new_discussion_note.strip()
                            })
                            st.success(f"✅ Discussion log saved for {selected_call_school} ({filter_description_text})!")
                        else:
                            st.warning("Please enter discussion notes before saving.")

            if st.session_state["school_call_logs_store"]:
                st.markdown(f"##### 📋 Discussion Logs for Active View (`{filter_description_text}`)")
                
                current_school_names = school_stats['Institution'].tolist()
                filtered_logs = [
                    item for item in st.session_state["school_call_logs_store"] 
                    if item["School"] in current_school_names and item["Review Period"] == filter_description_text
                ]

                if filtered_logs:
                    notes_summary_df = pd.DataFrame(filtered_logs)
                    st.dataframe(notes_summary_df[['School', 'Call Date', 'Review Period', 'Discussion & Action Items']], use_container_width=True)
                    
                    csv_notes = notes_summary_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Download Filtered Call & Discussion Log (CSV)",
                        data=csv_notes,
                        file_name=f"School_Call_Logs_{selected_month.replace(' ', '_')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.info(f"No discussion logs recorded yet for the active filter window (`{filter_description_text}`).")

            st.markdown("---")

            st.subheader("🚀 Week-on-Week (WoW) Portfolio Velocity")
            
            if 'Week' in school_filtered_df.columns and school_filtered_df['Week'].nunique() >= 2:
                weeks_sorted = sorted(school_filtered_df['Week'].unique())
                latest_week = weeks_sorted[-1]
                prev_week = weeks_sorted[-2]

                st.caption(f"Comparing `{latest_week}` vs. `{prev_week}`")

                weekly_school = school_filtered_df.groupby(['Week', 'Institution'])['Duration_Min'].sum().unstack(level=0, fill_value=0.0).reset_index()
                
                if latest_week in weekly_school.columns and prev_week in weekly_school.columns:
                    weekly_school['WoW_Growth_Mins'] = weekly_school[latest_week] - weekly_school[prev_week]
                    weekly_school['WoW_Growth_Pct'] = np.where(
                        weekly_school[prev_week] > 0, 
                        (weekly_school['WoW_Growth_Mins'] / weekly_school[prev_week]) * 100, 
                        100.0
                    )

                    col_v1, col_v2 = st.columns(2)

                    with col_v1:
                        st.success("🔥 Top 5 Most Improved Schools (Highest WoW Growth)")
                        top_improved = weekly_school.sort_values(by='WoW_Growth_Mins', ascending=False).head(5)
                        fig_top = px.bar(
                            top_improved, x="WoW_Growth_Mins", y="Institution", orientation="h",
                            title="Top Accelerated Schools (+Mins)",
                            labels={"WoW_Growth_Mins": "Added Minutes Logged", "Institution": "School"},
                            color_discrete_sequence=['#2CA02C'], text_auto=".1f"
                        )
                        fig_top.update_layout(yaxis={'categoryorder':'total ascending'})
                        st.plotly_chart(fig_top, use_container_width=True)

                    with col_v2:
                        st.error("🚨 Top 5 Priority Intervention Schools (Highest Usage Drop)")
                        top_declining = weekly_school.sort_values(by='WoW_Growth_Mins', ascending=True).head(5)
                        fig_bot = px.bar(
                            top_declining, x="WoW_Growth_Mins", y="Institution", orientation="h",
                            title="Highest Usage Drop Schools (-Mins)",
                            labels={"WoW_Growth_Mins": "Dropped Minutes Logged", "Institution": "School"},
                            color_discrete_sequence=['#D62728'], text_auto=".1f"
                        )
                        fig_bot.update_layout(yaxis={'categoryorder':'total descending'})
                        st.plotly_chart(fig_bot, use_container_width=True)

            else:
                st.info("Upload data covering at least 2 weeks to unlock Week-on-Week Velocity rankings.")

    # TAB 6: SCHOOL-LEVEL TEACHER PROGRESSION & EXECUTION TIERS
    with tab6:
        st.header("🏫 School-Level Teacher Progression & Execution Tiers")
        st.caption("Drill down into any individual school to classify teachers into execution tiers based on benchmark standards (🌟 Achiever >= 100%, ⚠️ Fluctuating 40%-99%, ❌ Inactive < 40%).")

        all_schools_list_t6 = sorted(school_master_roster['Institution'].unique())
        
        if not all_schools_list_t6:
            st.info("No schools found in roster.")
        else:
            target_school_t6 = st.selectbox("Select School to Inspect:", options=all_schools_list_t6)

            school_t6_roster = school_master_roster[school_master_roster['Institution'] == target_school_t6]
            school_t6_data = school_filtered_df[school_filtered_df['Institution'] == target_school_t6]

            st.markdown(f"### 🏫 School Audit: **{target_school_t6}** | Active Roster: **{len(school_t6_roster)} Teachers**")

            st.subheader("1. Teacher Execution Tiers")

            t6_ld = school_t6_data[school_t6_data['Type'] == 'lessonDelivery'].groupby('FullName')['Duration_Min'].sum().reset_index()
            t6_lib = school_t6_data[school_t6_data['Type'] == 'library'].groupby('FullName')['Duration_Min'].sum().reset_index()

            t6_teachers = school_t6_roster.merge(t6_ld.rename(columns={'Duration_Min': 'Lesson_Mins'}), on='FullName', how='left').fillna(0.0)
            t6_teachers = t6_teachers.merge(t6_lib.rename(columns={'Duration_Min': 'Library_Mins'}), on='FullName', how='left').fillna(0.0)

            def tier_teacher(row):
                ld_pct = (row['Lesson_Mins'] / calc_ld_kpi) if calc_ld_kpi > 0 else 1.0
                lib_pct = (row['Library_Mins'] / calc_lib_kpi) if calc_lib_kpi > 0 else 1.0

                if ld_pct >= 1.0 and lib_pct >= 1.0:
                    return '🌟 Consistent Achiever (>= 100%)'
                elif ld_pct < 0.40 and lib_pct < 0.40:
                    return '❌ Persistent Inactive (< 40%)'
                else:
                    return '⚠️ Fluctuating / Partial (40%-99%)'

            t6_teachers['Execution_Tier'] = t6_teachers.apply(tier_teacher, axis=1)

            e1, e2, e3 = st.columns(3)
            num_ach = len(t6_teachers[t6_teachers['Execution_Tier'].str.startswith('🌟')])
            num_fluc = len(t6_teachers[t6_teachers['Execution_Tier'].str.startswith('⚠️')])
            num_inact = len(t6_teachers[t6_teachers['Execution_Tier'].str.startswith('❌')])

            e1.metric("🌟 Consistent Achievers", num_ach)
            e2.metric("⚠️ Fluctuating / Partial", num_fluc)
            e3.metric("❌ Persistent Inactive", num_inact)

            fig_t6_bar = px.bar(
                t6_teachers, x="FullName", y=["Lesson_Mins", "Library_Mins"],
                title=f"Teacher Usage Breakdown for {target_school_t6} (Mins)",
                labels={"FullName": "Teacher Name", "value": "Logged Minutes", "variable": "Feature"},
                barmode="group", text_auto=".1f"
            )
            st.plotly_chart(fig_t6_bar, use_container_width=True)

            st.subheader("📋 Teacher Execution Tier Table")
            display_t6_table = t6_teachers.rename(columns={'FullName': 'Teacher Name', 'Lesson_Mins': 'Lesson Prep (m)', 'Library_Mins': 'Library Usage (m)', 'Execution_Tier': 'Execution Tier'})
            st.dataframe(display_t6_table, use_container_width=True)

            pdf_tab6 = generate_pdf_report(
                title_text=f"🏫 School Inspection Report: {target_school_t6}",
                subtitle_text=f"Period: {filter_description_text} | Total Roster: {len(school_t6_roster)} Teachers",
                summary_metrics={
                    "Consistent Achievers": num_ach,
                    "Fluctuating/Partial": num_fluc,
                    "Persistent Inactive": num_inact
                },
                dataframe=display_t6_table[['Teacher Name', 'Lesson Prep (m)', 'Library Usage (m)', 'Execution Tier']]
            )
            st.download_button(
                label=f"📄 Download {target_school_t6} Inspection Report (PDF)",
                data=pdf_tab6,
                file_name=f"{target_school_t6.replace(' ', '_')}_Execution_Report.pdf",
                mime="application/pdf"
            )

            st.markdown("---")

            st.subheader("2. Grade & Subject Digital Content Coverage")

            if school_t6_data.empty or school_t6_data['Book'].str.len().sum() == 0:
                st.info("No chapter or book usage logs recorded for this school.")
            else:
                col_t6_g1, col_t6_g2 = st.columns(2)

                with col_t6_g1:
                    grade_t6 = school_t6_data[school_t6_data['Book'].str.len() > 0].groupby('Grade')['Duration_Min'].sum().reset_index()
                    fig_g6 = px.bar(
                        grade_t6, x="Grade", y="Duration_Min", color="Grade",
                        title="Digital Classroom Time by Grade Level (Mins)",
                        text_auto=".1f"
                    )
                    st.plotly_chart(fig_g6, use_container_width=True)

                with col_t6_g2:
                    subj_t6 = school_t6_data[school_t6_data['Book'].str.len() > 0].groupby('Subject')['Duration_Min'].sum().reset_index()
                    fig_s6 = px.pie(
                        subj_t6, names="Subject", values="Duration_Min",
                        title="Subject / Module Distribution in School"
                    )
                    st.plotly_chart(fig_s6, use_container_width=True)

    # TAB 7: GLOBAL LIVE EVIDENCE SUBMISSIONS FEED & QUALITATIVE KPI TRACKER
    with tab7:
        st.header("📬 Live Evidence Submissions Feed & Qualitative KPI Tracker")
        if enable_qual_kpi:
            st.caption(f"Track individual teacher qualitative evidence submissions and compliance against mandatory Qualitative KPIs (Min. {target_vid_count} Activity Videos, Min. {target_writing_count} Writing Samples, Min. {target_lp_combo_count} LP / Voice Notes).")
        else:
            st.caption("Complete log of all qualitative evidence submissions from the Teacher Portal across the filtered database.")

        evidence_cols = ['Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link']
        avail_ev_cols = [c for c in evidence_cols if c in filtered_df.columns]

        def has_valid_evidence(row):
            for col in avail_ev_cols:
                val = str(row[col]).strip()
                if re.match(r'^https?://', val, re.IGNORECASE):
                    return True
            return False

        all_submissions_df = filtered_df[filtered_df.apply(has_valid_evidence, axis=1)].copy() if not filtered_df.empty and avail_ev_cols else pd.DataFrame()

        if all_submissions_df.empty:
            st.info("No teacher evidence submissions match the currently selected global filter criteria.")
        else:
            col_t7_f1, col_t7_f2, col_t7_f3 = st.columns(3)
            with col_t7_f1:
                t7_schools = ["All Schools"] + sorted([s for s in all_submissions_df['Institution'].unique() if str(s).strip()])
                t7_selected_school = st.selectbox("Filter by School:", t7_schools, key="t7_school")
            
            t7_filtered = all_submissions_df if t7_selected_school == "All Schools" else all_submissions_df[all_submissions_df['Institution'] == t7_selected_school]

            with col_t7_f2:
                t7_teachers = ["All Teachers"] + sorted([t for t in t7_filtered['FullName'].unique() if str(t).strip()])
                t7_selected_teacher = st.selectbox("Filter by Teacher:", t7_teachers, key="t7_teacher")

            if t7_selected_teacher != "All Teachers":
                t7_filtered = t7_filtered[t7_filtered['FullName'] == t7_selected_teacher]

            with col_t7_f3:
                t7_grades = ["All Grades"] + sorted([g for g in t7_filtered['Grade'].unique() if str(g).strip()])
                t7_selected_grade = st.selectbox("Filter by Grade:", t7_grades, key="t7_grade")

            if t7_selected_grade != "All Grades":
                t7_filtered = t7_filtered[t7_filtered['Grade'] == t7_selected_grade]

            st.markdown("---")

            # Metrics for Filtered Submissions
            tot_subs = len(t7_filtered)
            tot_audios = sum([1 for l in t7_filtered['Voice_Note_Link'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in t7_filtered.columns else 0
            tot_pics = sum([1 for l in t7_filtered['Lesson_Plan_Picture'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in t7_filtered.columns else 0
            
            tot_vids = 0
            for vc in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                if vc in t7_filtered.columns:
                    tot_vids += sum([1 for l in t7_filtered[vc] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])

            tot_writing = sum([1 for l in t7_filtered['Writing_Sample_Link'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Writing_Sample_Link' in t7_filtered.columns else 0

            m_c1, m_c2, m_c3, m_c4, m_c5 = st.columns(5)
            m_c1.metric("📋 Total Submission Logs", tot_subs)
            m_c2.metric("🎧 Audio Voice Notes", tot_audios)
            m_c3.metric("🖼️ LP Pictures", tot_pics)
            m_c4.metric("🎥 Videos Uploaded", tot_vids)
            m_c5.metric("📝 Writing Samples", tot_writing)

            st.markdown("---")

            # --- SECTION 1: QUALITATIVE EVIDENCE KPI COMPLIANCE TRACKER (IF ENABLED) ---
            if enable_qual_kpi:
                st.subheader("🎯 Teacher Qualitative Evidence KPI Compliance")
                st.caption(f"Configured Benchmark: **Min. {target_vid_count} Videos**, **Min. {target_writing_count} Writing Samples**, **Min. {target_lp_combo_count} LP / Voice Notes** per Teacher.")

                teacher_kpi_records = []
                target_roster = filtered_roster if t7_selected_school == "All Schools" else filtered_roster[filtered_roster['Institution'] == t7_selected_school]
                if t7_selected_teacher != "All Teachers":
                    target_roster = target_roster[target_roster['FullName'] == t7_selected_teacher]

                for _, t_row in target_roster.iterrows():
                    t_name = t_row['FullName']
                    t_inst = t_row['Institution']
                    sub_t_data = t7_filtered[(t7_filtered['FullName'] == t_name) & (t7_filtered['Institution'] == t_inst)]
                    
                    v_count = 0
                    for vc in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                        if vc in sub_t_data.columns:
                            v_count += len([l for l in sub_t_data[vc].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])
                    
                    w_count = 0
                    if 'Writing_Sample_Link' in sub_t_data.columns:
                        w_count = len([l for l in sub_t_data['Writing_Sample_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])
                    
                    lp_pic_count = len([l for l in sub_t_data['Lesson_Plan_Picture'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in sub_t_data.columns else 0
                    vn_count = len([l for l in sub_t_data['Voice_Note_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in sub_t_data.columns else 0
                    lp_combo_total = lp_pic_count + vn_count

                    v_status = f"✅ Met (>={target_vid_count})" if v_count >= target_vid_count else f"⚠️ Pending ({v_count}/{target_vid_count})"
                    w_status = f"✅ Met (>={target_writing_count})" if w_count >= target_writing_count else f"⚠️ Pending ({w_count}/{target_writing_count})"
                    lp_status = f"✅ Met (>={target_lp_combo_count})" if lp_combo_total >= target_lp_combo_count else f"⚠️ Pending ({lp_combo_total}/{target_lp_combo_count})"
                    
                    if v_count >= target_vid_count and w_count >= target_writing_count and lp_combo_total >= target_lp_combo_count:
                        overall_status = "🌟 Fully Compliant"
                    elif v_count >= target_vid_count or w_count >= target_writing_count or lp_combo_total >= target_lp_combo_count:
                        overall_status = "⚠️ Partial Compliance"
                    else:
                        overall_status = "❌ Needs Attention"
                    
                    teacher_kpi_records.append({
                        'School': t_inst,
                        'Teacher Name': t_name,
                        'Activity Videos': v_count,
                        'Video KPI Status': v_status,
                        'Writing Samples': w_count,
                        'Writing KPI Status': w_status,
                        'LP / Voice Notes': lp_combo_total,
                        'LP Combo Status': lp_status,
                        'Overall Qualitative Status': overall_status
                    })

                kpi_summary_df = pd.DataFrame(teacher_kpi_records)
                
                if not kpi_summary_df.empty:
                    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
                    total_teachers_kpi = len(kpi_summary_df)
                    fully_compliant = len(kpi_summary_df[kpi_summary_df['Overall Qualitative Status'] == "🌟 Fully Compliant"])
                    video_met = len(kpi_summary_df[kpi_summary_df['Activity Videos'] >= target_vid_count])
                    writing_met = len(kpi_summary_df[kpi_summary_df['Writing Samples'] >= target_writing_count])
                    lp_met = len(kpi_summary_df[kpi_summary_df['LP / Voice Notes'] >= target_lp_combo_count])

                    kpi_col1.metric("🌟 Fully Compliant", f"{fully_compliant} / {total_teachers_kpi}")
                    kpi_col2.metric(f"🎥 Video KPI (>={target_vid_count})", f"{video_met} / {total_teachers_kpi}", f"{(video_met/total_teachers_kpi*100):.1f}% Compliance")
                    kpi_col3.metric(f"📝 Writing KPI (>={target_writing_count})", f"{writing_met} / {total_teachers_kpi}", f"{(writing_met/total_teachers_kpi*100):.1f}% Compliance")
                    kpi_col4.metric(f"📖 LP/VN KPI (>={target_lp_combo_count})", f"{lp_met} / {total_teachers_kpi}", f"{(lp_met/total_teachers_kpi*100):.1f}% Compliance")

                    st.dataframe(kpi_summary_df, use_container_width=True)

                    pdf_kpi = generate_pdf_report(
                        title_text="🎯 Qualitative Evidence KPI Compliance Report",
                        subtitle_text=f"Filter Period: {filter_description_text} | Total Teachers: {total_teachers_kpi}",
                        summary_metrics={
                            "Total Teachers": total_teachers_kpi,
                            "Fully Compliant": f"{fully_compliant} / {total_teachers_kpi}",
                            "Video KPI Met": f"{video_met} / {total_teachers_kpi}",
                            "Writing KPI Met": f"{writing_met} / {total_teachers_kpi}",
                            "LP / VN Combo Met": f"{lp_met} / {total_teachers_kpi}"
                        },
                        dataframe=kpi_summary_df[['School', 'Teacher Name', 'Activity Videos', 'Video KPI Status', 'Writing Samples', 'Writing KPI Status', 'LP / Voice Notes', 'LP Combo Status', 'Overall Qualitative Status']]
                    )
                    st.download_button(
                        label="📄 Download Qualitative KPI Summary (PDF)",
                        data=pdf_kpi,
                        file_name=f"Qualitative_KPI_Summary_{selected_month.replace(' ', '_')}.pdf",
                        mime="application/pdf"
                    )

                st.markdown("---")

            # --- SECTION 2: GRANULAR SUBMISSIONS TABLE ---
            st.subheader("📋 Granular Qualitative Submissions Log")
            t7_display_cols = ['StartTime', 'Institution', 'FullName', 'Grade', 'Subject', 'Book', 'Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link']
            t7_avail = [c for c in t7_display_cols if c in t7_filtered.columns]
            
            t7_table = t7_filtered[t7_avail].sort_values(by='StartTime', ascending=False)
            st.dataframe(t7_table, use_container_width=True)

            csv_t7 = t7_table.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Evidence Submissions Log (CSV)",
                data=csv_t7,
                file_name=f"Teacher_Evidence_Submissions_{selected_month.replace(' ', '_')}.csv",
                mime="application/pdf"
            )
