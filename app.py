import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import os
import glob
import re
from io import BytesIO
from supabase import create_client

# ReportLab PDF Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration (Must be the first Streamlit command)
st.set_page_config(page_title="Academic Manager Portfolio & Teacher KPI Review Dashboard", layout="wide")

# --- SUPABASE CLOUD STORAGE SETUP ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    BUCKET_NAME = st.secrets["supabase"]["bucket_name"]
    PARQUET_FILE_NAME = "master_database.parquet"

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"Supabase credentials missing or misconfigured in Streamlit Secrets: {e}")

@st.cache_data(ttl=5, show_spinner=False)
def fetch_master_db_from_supabase():
    """Downloads and reads the master parquet file from Supabase storage into memory with caching."""
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            return pd.read_parquet(BytesIO(response))
    except Exception:
        pass
    return pd.DataFrame()


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

    # Prefer rows that are explicitly teachers when Role is available; otherwise fall back
    # to all valid named identity rows so an imperfect UserMetrics export still works.
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

    # Keep the original display spelling, but deduplicate using normalized keys.
    candidate["_institution_key"] = candidate["Institution"].map(_norm_key)
    candidate["_teacher_key"] = candidate["FullName"].map(_norm_key)
    candidate = candidate.drop_duplicates(
        subset=["_institution_key", "_teacher_key"], keep="last"
    ).sort_values(["Institution", "FullName"], kind="stable")

    return candidate.reset_index(drop=True)

# 0. PDF Generator Helper Function
def generate_pdf_report(title_text, subtitle_text, summary_metrics, dataframe):
    """
    Generates a professional PDF document in memory and returns a downloadable BytesIO buffer.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=15, leading=18, textColor=colors.HexColor('#1F77B4'))
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.gray)
    
    story.append(Paragraph(f"<b>{title_text}</b>", title_style))
    story.append(Paragraph(subtitle_text, subtitle_style))
    story.append(Spacer(1, 12))

    if summary_metrics:
        metric_text = " &nbsp;&nbsp;|&nbsp;&nbsp; ".join([f"<b>{k}:</b> {v}" for k, v in summary_metrics.items()])
        metric_style = ParagraphStyle('MetricBlock', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor('#2CA02C'))
        story.append(Paragraph(metric_text, metric_style))
        story.append(Spacer(1, 12))

    if not dataframe.empty:
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
        return int(np.busday_count(start_np, end_np, weekmask=w_mask, holidays=holidays_np))
    except Exception:
        return 1

# Page layout title
st.title("🏫 Academic Manager Portfolio & Teacher KPI Review Dashboard")
st.markdown("Track **School Portfolio Management**, **School WoW Velocity**, **Teacher Execution Tiers**, **Daily KPIs (Lesson Prep / Library)**, **360° Qualitative Evidences**, and **Assessment Outcomes**.")

# 1. Supabase Parquet Database Manager Function
def load_or_update_master_db(new_upload_dfs=None):
    """Load master database, merge UserMetrics uploads, preserve teacher identity, and sync Supabase."""
    master_df = fetch_master_db_from_supabase()

    if not new_upload_dfs:
        return normalize_identity_columns(master_df) if not master_df.empty else master_df

    combined_new = pd.concat(new_upload_dfs, ignore_index=True)
    all_data = pd.concat([master_df, combined_new], ignore_index=True) if not master_df.empty else combined_new
    all_data = normalize_identity_columns(all_data)

    # Existing admin dedup logic is preserved, with Book included so multiple lesson
    # submissions by the same teacher on the same day do not collapse into one row.
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
            
            # --- IDENTITY NORMALIZATION ---
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

            # Optional Qualitative Link Columns
            for qual_col in ['Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 'Assessment_Score_Pct']:
                if qual_col not in temp_df.columns:
                    temp_df[qual_col] = None

            new_processed_dfs.append(temp_df)
        except Exception as e:
            st.sidebar.error(f"Error reading {file.name}: {e}")

# Load or Sync Supabase Parquet Database
if new_processed_dfs:
    df = load_or_update_master_db(new_processed_dfs)
    st.sidebar.success(f"Synced {len(uploaded_files)} file(s) into Supabase Parquet DB!")
else:
    df = load_or_update_master_db()

# 3. Cloud Database Status & Storage Controls
st.sidebar.markdown("---")
st.sidebar.header("🗄️ Supabase Cloud Database Status")

# Add Sync & Refresh Controls
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
    # Ensure FullName is present in main df if loaded from cloud
    if 'FullName' not in df.columns:
        if 'FirstName' in df.columns and 'LastName' in df.columns:
            df['FullName'] = (df['FirstName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip()) + " " + df['LastName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
        else:
            df['FullName'] = 'Unknown Teacher'

    # Build Date, Month, and Enhanced Month-Based Week Columns
    if 'StartTime' in df.columns:
        df['Date'] = pd.to_datetime(df['StartTime'], errors='coerce').dt.date
        df['Month_Name'] = pd.to_datetime(df['StartTime'], errors='coerce').dt.strftime('%B %Y')
        df['Month_Sort'] = pd.to_datetime(df['StartTime'], errors='coerce').dt.strftime('%Y-%m')
        
        def get_week_of_month(dt):
            try:
                first_day = dt.replace(day=1)
                dom = dt.day
                adjusted_dom = dom + first_day.weekday()
                return int(np.ceil(adjusted_dom / 7.0))
            except:
                return 1
                
        df['Week_Num'] = pd.to_datetime(df['StartTime'], errors='coerce').apply(get_week_of_month)
        
        week_ranges = df.groupby(['Month_Name', 'Week_Num'])['Date'].agg(['min', 'max']).reset_index()
        week_ranges['Week_Date_Range'] = (
            week_ranges['min'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '') + " to " + 
            week_ranges['max'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '')
        )
        
        df = df.merge(week_ranges[['Month_Name', 'Week_Num', 'Week_Date_Range']], on=['Month_Name', 'Week_Num'], how='left')
        df['Month_Week_Label'] = pd.to_datetime(df['StartTime'], errors='coerce').dt.strftime('%b %Y') + " - Week " + df['Week_Num'].astype(str) + " (" + df['Week_Date_Range'] + ")"
        df['Week'] = df['Month_Week_Label']
    else:
        df['Date'] = "N/A"
        df['Month_Name'] = "N/A"
        df['Week'] = "N/A"

    # Build the master teacher roster independently from activity totals.
    # This ensures teachers with zero activity still appear in the admin filters.
    master_teacher_roster = build_teacher_roster(df)
    if master_teacher_roster.empty:
        master_teacher_roster = pd.DataFrame(columns=['Institution', 'FullName'])
    else:
        master_teacher_roster = master_teacher_roster[['Institution', 'FullName']].drop_duplicates()

    # Sidebar Review Filters
    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Review Filters")
    all_schools = sorted([str(s) for s in df['Institution'].unique() if str(s).strip() and str(s).lower() not in ['nan', 'none']])
    selected_schools = st.sidebar.multiselect("Select School(s)", options=all_schools, default=all_schools)

    # Filter Master Roster and Data by selected Schools
    school_master_roster = master_teacher_roster[master_teacher_roster['Institution'].isin(selected_schools)]
    school_filtered_df = df[df['Institution'].isin(selected_schools)]

    # --- MONTH-FIRST & CALENDAR HOLIDAY MANAGER ---
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Calendar & Holiday Manager")
    
    available_months_df = school_filtered_df[['Month_Sort', 'Month_Name']].dropna().drop_duplicates().sort_values(by='Month_Sort', ascending=False)
    month_options = available_months_df['Month_Name'].tolist()
    
    selected_month = st.sidebar.selectbox("Select Review Month:", options=month_options if month_options else ["No Month Data"])
    month_filtered_df = school_filtered_df[school_filtered_df['Month_Name'] == selected_month]
    
    # Sunday Exclusion Toggle
    exclude_sundays_flag = st.sidebar.checkbox("🗓️ Exclude Sundays from KPIs", value=True)

    # Global Monthly Holiday Punch-In Multiselect
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
        if user_excluded_dates:
            st.sidebar.caption(f"{len(user_excluded_dates)} holiday date(s) deducted from {selected_month} KPIs.")

    # --- DYNAMIC KPI BENCHMARK MANAGER ---
    st.sidebar.markdown("---")
    st.sidebar.header("🎯 KPI Benchmark Controls")

    daily_ld_target = st.sidebar.number_input(
        "Lesson Prep Target (Mins/Day)",
        min_value=0.0,
        max_value=60.0,
        value=10.0,
        step=5.0,
        help="Default benchmark is 10 minutes per working day."
    )

    daily_lib_target = st.sidebar.number_input(
        "Library Usage Target (Mins/Day)",
        min_value=0.0,
        max_value=120.0,
        value=30.0,
        step=5.0,
        help="Default benchmark is 30 minutes per working day."
    )

    # View Mode Selector
    st.sidebar.subheader("🔍 Review View Level")
    available_month_weeks = sorted(month_filtered_df['Month_Week_Label'].dropna().unique())
    available_dates = sorted(month_filtered_df['Date'].dropna().unique(), reverse=True)
    
    view_mode = st.sidebar.radio("Granularity:", ["Full Month Summary", "Specific Week of Month", "Single Day Review"])
    
    if month_filtered_df.empty:
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
        
    else:
        selected_date = st.sidebar.selectbox("Select Day:", options=available_dates)
        filtered_df = month_filtered_df[month_filtered_df['Date'] == selected_date]
        selected_num_days = get_working_days(selected_date, selected_date, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Single Date: {selected_date} ({selected_num_days} Working Day(s))"

    calc_ld_kpi = daily_ld_target * selected_num_days
    calc_lib_kpi = daily_lib_target * selected_num_days

    # Teacher Filter
    available_teachers = sorted([str(t) for t in school_master_roster['FullName'].unique() if str(t).strip()])
    selected_teachers = st.sidebar.multiselect("Select Teacher(s)", options=available_teachers, default=available_teachers)
    
    filtered_roster = school_master_roster[school_master_roster['FullName'].isin(selected_teachers)]
    filtered_df = filtered_df[filtered_df['FullName'].isin(selected_teachers)]

    # 7 Dedicated Meeting Review Tabs
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📘 1. Daily Lesson Plan KPI", 
        "📚 2. Daily Library KPI", 
        "📖 3. Daily Content & Chapters", 
        "👤 4. Teacher 360° Profile Report",
        "🏛️ 5. Manager Portfolio Quadrants",
        "🏫 6. School Teacher Progression",
        "📊 7. Student Assessment Outcomes"
    ])

    # TAB 1: DAILY LESSON PLAN COMPLIANCE
    with tab1:
        st.header("📘 Daily Lesson Plan Preparation Tracker")
        st.caption(f"KPI Benchmark: **At least {calc_ld_kpi:.0f} Minutes** ({daily_ld_target:.0f} mins/day across {selected_num_days} working day(s)).")

        ld_df = filtered_df[filtered_df['Type'] == 'lessonDelivery']
        ld_usage = ld_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index()
        ld_daily = filtered_roster.merge(ld_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
        
        def get_ld_status(x):
            if calc_ld_kpi == 0: return '✅ Holiday / No-Class (0m Req)'
            if x >= calc_ld_kpi: return f'✅ Met KPI (>= {calc_ld_kpi:.0f}m)'
            elif x > 0.0: return f'⚠️ Below KPI (< {calc_ld_kpi:.0f}m)'
            else: return '❌ Inactive (0 Mins)'
        
        ld_daily['KPI Status'] = ld_daily['Duration_Min'].apply(get_ld_status)

        c1, c2, c3, c4 = st.columns(4)
        total_teachers = len(ld_daily)
        met_count = len(ld_daily[ld_daily['Duration_Min'] >= calc_ld_kpi]) if calc_ld_kpi > 0 else total_teachers
        inactive_count = len(ld_daily[ld_daily['Duration_Min'] == 0.0])
        
        c1.metric("Total Roster Teachers", total_teachers)
        c2.metric(f"Met {calc_ld_kpi:.0f}m KPI", f"{met_count} / {total_teachers}")
        c3.metric("Inactive Teachers (0m)", inactive_count, delta=f"{-inactive_count}" if inactive_count > 0 else "0", delta_color="inverse")
        c4.metric("KPI Compliance Rate", f"{(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%")

        fig_ld = px.bar(
            ld_daily, x="FullName", y="Duration_Min", color="KPI Status",
            title=f"Lesson Prep Minutes per Teacher vs. {calc_ld_kpi:.0f} Min KPI Standard",
            labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Prepared"},
            text_auto=".1f"
        )
        fig_ld.add_hline(y=calc_ld_kpi, line_dash="dash", line_color="black", annotation_text=f"KPI Standard ({calc_ld_kpi:.0f} mins)")
        st.plotly_chart(fig_ld, use_container_width=True)

        st.subheader("📋 Lesson Plan KPI Review Table")
        display_ld_table = ld_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
        st.dataframe(display_ld_table, use_container_width=True)

        pdf_tab1 = generate_pdf_report(
            title_text="📘 Daily Lesson Plan Preparation Report",
            subtitle_text=f"Filter: {filter_description_text} | Total Teachers: {total_teachers}",
            summary_metrics={
                "Total Teachers": total_teachers,
                "Met KPI Standard": f"{met_count} / {total_teachers}",
                "Compliance Rate": f"{(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%"
            },
            dataframe=display_ld_table[['School', 'Teacher Name', 'Minutes Logged', 'KPI Status']]
        )
        st.download_button(
            label="📄 Download Tab 1 Report (PDF)",
            data=pdf_tab1,
            file_name=f"Lesson_Plan_KPI_Report_{selected_month.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )

    # TAB 2: DAILY LIBRARY KPI
    with tab2:
        st.header("📚 Daily Library Usage Tracker")
        st.caption(f"KPI Benchmark: **At least {calc_lib_kpi:.0f} Minutes** ({daily_lib_target:.0f} mins/day across {selected_num_days} working day(s)).")

        lib_df = filtered_df[filtered_df['Type'] == 'library']
        lib_usage = lib_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index()
        lib_daily = filtered_roster.merge(lib_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
        
        def get_lib_status(x):
            if calc_lib_kpi == 0: return '✅ Holiday / No-Class (0m Req)'
            if x >= calc_lib_kpi: return f'✅ Met KPI (>= {calc_lib_kpi:.0f}m)'
            elif x > 0.0: return f'⚠️ Below KPI (< {calc_lib_kpi:.0f}m)'
            else: return '❌ Inactive (0 Mins)'

        lib_daily['KPI Status'] = lib_daily['Duration_Min'].apply(get_lib_status)

        m1, m2, m3, m4 = st.columns(4)
        lib_total_teachers = len(lib_daily)
        lib_met_count = len(lib_daily[lib_daily['Duration_Min'] >= calc_lib_kpi]) if calc_lib_kpi > 0 else lib_total_teachers
        lib_inactive_count = len(lib_daily[lib_daily['Duration_Min'] == 0.0])
        
        m1.metric("Total Roster Teachers", lib_total_teachers)
        m2.metric(f"Met {calc_lib_kpi:.0f}m KPI", f"{lib_met_count} / {lib_total_teachers}")
        m3.metric("Inactive Teachers (0m)", lib_inactive_count, delta=f"{-lib_inactive_count}" if lib_inactive_count > 0 else "0", delta_color="inverse")
        m4.metric("Library KPI Compliance Rate", f"{(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%")

        fig_lib = px.bar(
            lib_daily, x="FullName", y="Duration_Min", color="KPI Status",
            title=f"Library Minutes per Teacher vs. {calc_lib_kpi:.0f} Min KPI Standard",
            labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Logged"},
            text_auto=".1f"
        )
        fig_lib.add_hline(y=calc_lib_kpi, line_dash="dash", line_color="black", annotation_text=f"KPI Standard ({calc_lib_kpi:.0f} mins)")
        st.plotly_chart(fig_lib, use_container_width=True)

        st.subheader("📋 Library KPI Review Table")
        display_lib_table = lib_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
        st.dataframe(display_lib_table, use_container_width=True)

        pdf_tab2 = generate_pdf_report(
            title_text="📚 Daily Library Usage Report",
            subtitle_text=f"Filter: {filter_description_text} | Total Teachers: {lib_total_teachers}",
            summary_metrics={
                "Total Teachers": lib_total_teachers,
                "Met KPI Standard": f"{lib_met_count} / {lib_total_teachers}",
                "Compliance Rate": f"{(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%"
            },
            dataframe=display_lib_table[['School', 'Teacher Name', 'Minutes Logged', 'KPI Status']]
        )
        st.download_button(
            label="📄 Download Tab 2 Report (PDF)",
            data=pdf_tab2,
            file_name=f"Library_KPI_Report_{selected_month.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )

    # TAB 3: CHAPTERS & RESOURCE BREAKDOWN
    with tab3:
        st.header("📖 Chapters & Content Modules Opened")
        st.caption(f"Track specific books, subjects, and themes during `{filter_description_text}`.")

        content_df = filtered_df[filtered_df['Book'].str.len() > 0]

        if content_df.empty:
            st.info("No specific chapter/book access logs found in the uploaded data for the selected global filters.")
        else:
            st.markdown("#### 🎯 Drill-Down Filters")
            col_f1, col_f2, col_f3 = st.columns(3)
            
            with col_f1:
                t3_school_opt = ["All Selected Schools"] + sorted(content_df['Institution'].unique().tolist())
                t3_school = st.selectbox("🏫 Select School:", t3_school_opt, key="t3_school")
                
            if t3_school != "All Selected Schools":
                t3_df = content_df[content_df['Institution'] == t3_school]
            else:
                t3_df = content_df

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

    # TAB 4: SINGLE TEACHER 360° PROFILE REPORT
    with tab4:
        st.header("👤 Teacher 360° Performance Profile")
        st.caption("Teacher evidence submitted from the Teacher Portal is synchronized into the shared master database and appears here automatically for the selected teacher and period.")

        all_roster_teachers = sorted(school_master_roster['FullName'].unique())
        
        if not all_roster_teachers:
            st.info("No teachers found in roster for the selected school(s).")
        else:
            target_teacher = st.selectbox("Select Teacher to Audit:", options=all_roster_teachers)
            
            # Period-scoped data for benchmarks vs all-time records for submitted evidences
            teacher_all_data = school_filtered_df[school_filtered_df['FullName'] == target_teacher]
            teacher_date_data = filtered_df[filtered_df['FullName'] == target_teacher]
            teacher_school = school_master_roster[school_master_roster['FullName'] == target_teacher]['Institution'].values[0] if not school_master_roster[school_master_roster['FullName'] == target_teacher].empty else "N/A"

            st.markdown(f"### 📋 Audit Profile: **{target_teacher}** | School: **{teacher_school}**")

            # SECTION 1: PERFORMANCE INDICATOR SUMMARY
            st.subheader("1. Performance Indicator Summary")
            st.info(f"📅 **Active Filter**: `{filter_description_text}` | **KPI Duration**: `{selected_num_days} Working Day(s)`")

            t_day_ld = teacher_date_data[teacher_date_data['Type'] == 'lessonDelivery']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
            t_day_lib = teacher_date_data[teacher_date_data['Type'] == 'library']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
            
            ld_pct = (t_day_ld / calc_ld_kpi) * 100 if calc_ld_kpi > 0 else (100.0 if t_day_ld >= 0 else 0)
            lib_pct = (t_day_lib / calc_lib_kpi) * 100 if calc_lib_kpi > 0 else (100.0 if t_day_lib >= 0 else 0)

            if calc_ld_kpi > 0:
                ld_advice = f"🌟 Doing Great! (Met {calc_ld_kpi:.0f}m KPI Standard)" if t_day_ld >= calc_ld_kpi else (f"⚠️ Needs Improvement (Below {calc_ld_kpi:.0f}m KPI Standard)" if t_day_ld > 0 else "❌ Action Required (0 Mins Logged)")
            else:
                ld_advice = "✅ Holiday / No-Class (No KPI Required)"

            if calc_lib_kpi > 0:
                lib_advice = f"🌟 Doing Great! (Met {calc_lib_kpi:.0f}m KPI Standard)" if t_day_lib >= calc_lib_kpi else (f"⚠️ Needs Improvement (Below {calc_lib_kpi:.0f}m KPI Standard)" if t_day_lib > 0 else "❌ Action Required (0 Mins Logged)")
            else:
                lib_advice = "✅ Holiday / No-Class (No KPI Required)"

            col_sum1, col_sum2 = st.columns([1, 1.2])

            with col_sum1:
                st.markdown("##### 📌 KPI Overview")
                s1, s2 = st.columns(2)
                s1.metric("Lesson Prep Mins", f"{t_day_ld:.1f} mins", delta=f"{ld_pct:.0f}% of KPI")
                s2.metric("Library Usage Mins", f"{t_day_lib:.1f} mins", delta=f"{lib_pct:.0f}% of KPI")
                
                st.markdown("##### 💡 Feedback & Recommendations")
                if calc_ld_kpi == 0 and calc_lib_kpi == 0:
                    st.info(f"🏖️ **Rest Day**: {target_teacher} selected filter falls on an excluded Holiday.")
                elif t_day_ld >= calc_ld_kpi and t_day_lib >= calc_lib_kpi:
                    st.success(f"👏 **Excellent Work**: {target_teacher} achieved all KPIs for this {selected_num_days}-working day period!")
                elif t_day_ld < calc_ld_kpi and t_day_lib < calc_lib_kpi:
                    st.error(f"⚠️ **Attention Needed**: {target_teacher} is below KPI standards for both Lesson Prep and Library Usage.")
                else:
                    st.warning(f"💡 **Mixed Usage**: {target_teacher} met one benchmark but requires coaching in the other.")

                st.write(f"• **Lesson Plan Status**: {ld_advice}")
                st.write(f"• **Library Usage Status**: {lib_advice}")

            with col_sum2:
                st.markdown("##### 📊 KPI Achievement Comparison")
                ach_df = pd.DataFrame({
                    'KPI Category': [f'Lesson Prep ({calc_ld_kpi:.0f}m)', f'Library Usage ({calc_lib_kpi:.0f}m)'],
                    'Logged Minutes': [t_day_ld, t_day_lib],
                    'KPI Standard': [calc_ld_kpi, calc_lib_kpi]
                })
                
                fig_ach = go.Figure()
                fig_ach.add_trace(go.Bar(
                    x=ach_df['KPI Category'], y=ach_df['Logged Minutes'],
                    name='Logged Minutes', marker_color='#2CA02C', text=[f"{v:.1f} mins" for v in ach_df['Logged Minutes']], textposition='auto'
                ))
                fig_ach.add_trace(go.Bar(
                    x=ach_df['KPI Category'], y=ach_df['KPI Standard'],
                    name='KPI Standard', marker_color='#E5E5E5', opacity=0.6, text=[f"{v:.1f} mins" for v in ach_df['KPI Standard']], textposition='auto'
                ))
                fig_ach.update_layout(
                    barmode='group', title=f"Logged Minutes vs. KPI Standard ({selected_num_days} Working Day(s))",
                    height=280, margin=dict(l=20, r=20, t=40, b=20)
                )
                st.plotly_chart(fig_ach, use_container_width=True)

            st.markdown("---")

            # SECTION 2: DIGITAL CONTENT & BOOK USAGE REPORT
            st.subheader("2. Book & Grade Digital Content Usage Report")
            
            teacher_books = teacher_date_data[teacher_date_data['Book'].str.len() > 0]
            
            if teacher_books.empty:
                st.info(f"No specific digital books or chapters were accessed by **{target_teacher}** during `{filter_description_text}`.")
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

            # SECTION 3: QUALITATIVE EVIDENCE HUB
            st.subheader("3. Qualitative Evidences & Artifact Hub")
            st.caption("Review authentic teacher pre-class voice notes, lesson plan pictures, in-class classroom activity videos, and student writing samples.")

            v_cols = st.columns(4)
            
            # Use teacher_all_data so all uploaded files are visible regardless of date filters
            evidence_source = teacher_all_data if not teacher_all_data.empty else teacher_date_data
            
            voice_links = [l for l in evidence_source['Voice_Note_Link'].dropna().unique().tolist() if str(l).strip() and str(l).lower() != 'none'] if 'Voice_Note_Link' in evidence_source.columns else []
            v_cols[0].metric("🎧 Voice Notes", len(voice_links))

            pic_links = [l for l in evidence_source['Lesson_Plan_Picture'].dropna().unique().tolist() if str(l).strip() and str(l).lower() != 'none'] if 'Lesson_Plan_Picture' in evidence_source.columns else []
            v_cols[1].metric("🖼️ LP Pictures", len(pic_links))
            
            video_cols_exist = [c for c in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3'] if c in evidence_source.columns]
            video_count = 0
            if video_cols_exist:
                video_count = evidence_source[video_cols_exist].notna().sum().sum()
            v_cols[2].metric("🎥 Videos", video_count)

            writing_links = [l for l in evidence_source['Writing_Sample_Link'].dropna().unique().tolist() if str(l).strip() and str(l).lower() != 'none'] if 'Writing_Sample_Link' in evidence_source.columns else []
            v_cols[3].metric("📝 Writing Samples", len(writing_links))

            with st.expander("🔍 View & Audit Submitted Artifact Files & Links", expanded=True):
                q_cols1, q_cols2, q_cols3, q_cols4 = st.columns(4)
                
                with q_cols1:
                    st.markdown("##### 🎧 Voice Notes")
                    if voice_links:
                        for idx, link in enumerate(voice_links, 1):
                            if str(link).startswith("http"):
                                st.markdown(f"• [Listen #{idx}]({link})")
                            else:
                                st.text(f"• File #{idx}")
                    else:
                        st.caption("None uploaded.")

                with q_cols2:
                    st.markdown("##### 🖼️ Lesson Pictures")
                    if pic_links:
                        for idx, link in enumerate(pic_links, 1):
                            if str(link).startswith("http"):
                                st.markdown(f"• [View Pic #{idx}]({link})")
                            else:
                                st.text(f"• File #{idx}")
                    else:
                        st.caption("None uploaded.")

                with q_cols3:
                    st.markdown("##### 🎥 Activity Videos")
                    if video_count > 0:
                        for col in video_cols_exist:
                            v_list = [l for l in evidence_source[col].dropna().unique().tolist() if str(l).strip() and str(l).lower() != 'none']
                            for idx, link in enumerate(v_list, 1):
                                if str(link).startswith("http"):
                                    st.markdown(f"• [Video #{idx}]({link})")
                                else:
                                    st.text(f"• Video #{idx}")
                    else:
                        st.caption("None uploaded.")

                with q_cols4:
                    st.markdown("##### 📝 Writing Samples")
                    if writing_links:
                        for idx, link in enumerate(writing_links, 1):
                            if str(link).startswith("http"):
                                st.markdown(f"• [Sample #{idx}]({link})")
                            else:
                                st.text(f"• Sample #{idx}")
                    else:
                        st.caption("None uploaded.")

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
                        label=f"📥 Download Audit CSV for {target_teacher}",
                        data=csv_profile,
                        file_name=f"{target_teacher.replace(' ', '_')}_{selected_type_filter}_Audit.csv",
                        mime="text/csv"
                    )
                with col_p2:
                    pdf_tab4 = generate_pdf_report(
                        title_text=f"👤 Teacher 360° Audit Report: {target_teacher}",
                        subtitle_text=f"School: {teacher_school} | Filter Period: {filter_description_text}",
                        summary_metrics={
                            "Lesson Prep": f"{t_day_ld:.1f} mins ({ld_pct:.0f}% KPI)",
                            "Library Usage": f"{t_day_lib:.1f} mins ({lib_pct:.0f}% KPI)",
                            "Books Opened": teacher_books['Book'].nunique() if not teacher_books.empty else 0
                        },
                        dataframe=t_display_log[['Date', 'Type', 'Grade', 'Subject', 'Book', 'Minutes']].head(25)
                    )
                    st.download_button(
                        label="📄 Download 360° Profile Report (PDF)",
                        data=pdf_tab4,
                        file_name=f"{target_teacher.replace(' ', '_')}_360_Audit_Report.pdf",
                        mime="application/pdf"
                    )

    # TAB 5: MANAGER PORTFOLIO & SCHOOL QUADRANTS
    with tab5:
        st.header("🏛️ Academic Manager Portfolio Overview")
        st.caption("High-level classification and Week-on-Week Velocity tracking across your school portfolio.")

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

            def classify_school(row):
                ld_ok = row['Avg_Lesson_Prep_Mins'] >= daily_ld_target
                lib_ok = row['Avg_Library_Usage_Mins'] >= daily_lib_target
                if ld_ok and lib_ok:
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
                st.success(f"🌟 **Pace Setters ({len(pace_setters)} Schools)**\n\n*Met both Lesson Prep (>={daily_ld_target:.0f}m) & Library (>={daily_lib_target:.0f}m) KPIs*\n\n" + (", ".join(pace_setters) if pace_setters else "None"))
            with col_top2:
                st.info(f"📘 **Lesson Focused ({len(lesson_focused)} Schools)**\n\n*Met Lesson Prep (>={daily_ld_target:.0f}m), Below Library (<{daily_lib_target:.0f}m)*\n\n" + (", ".join(lesson_focused) if lesson_focused else "None"))

            col_bot1, col_bot2 = st.columns(2)
            with col_bot1:
                st.warning(f"📚 **Library Focused ({len(library_focused)} Schools)**\n\n*Met Library (>={daily_lib_target:.0f}m), Below Lesson Prep (<{daily_ld_target:.0f}m)*\n\n" + (", ".join(library_focused) if library_focused else "None"))
            with col_bot2:
                st.error(f"🚨 **Priority Focus ({len(priority_focus)} Schools)**\n\n*Below KPI Standards on both features*\n\n" + (", ".join(priority_focus) if priority_focus else "None"))

            st.markdown("---")
            st.subheader("📋 Complete School Performance Leaderboard")
            display_qtable = school_stats[['Institution', 'Roster_Teachers', 'Avg_Lesson_Prep_Mins', 'Avg_Library_Usage_Mins', 'Classification']].rename(columns={
                'Institution': 'School Name',
                'Roster_Teachers': 'Active Teachers',
                'Avg_Lesson_Prep_Mins': 'Prep (m/day)',
                'Avg_Library_Usage_Mins': 'Library (m/day)'
            })
            st.dataframe(display_qtable, use_container_width=True)

            pdf_tab5 = generate_pdf_report(
                title_text="🏛️ Academic Manager Portfolio Review",
                subtitle_text=f"Portfolio Classification for {selected_month} ({selected_num_days} Working Days)",
                summary_metrics={
                    "Pace Setters": len(pace_setters),
                    "Lesson Focused": len(lesson_focused),
                    "Library Focused": len(library_focused),
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

    # TAB 7: STUDENT ASSESSMENT OUTCOMES & ACADEMIC IMPACT
    with tab7:
        st.header("📊 Student Assessment Outcomes & Impact Analysis")
        st.caption("Track student assessment scores (periodic, monthly, summative) and analyze impact across schools, grades, subjects, and teacher execution tiers.")

        if 'Assessment_Score_Pct' not in school_filtered_df.columns or school_filtered_df['Assessment_Score_Pct'].dropna().empty:
            st.info("👋 No student assessment score data uploaded yet. When you upload files containing `Assessment_Score_Pct`, outcome analytics will automatically render here.")
        else:
            assess_df = school_filtered_df.dropna(subset=['Assessment_Score_Pct'])

            a_col1, a_col2, a_col3 = st.columns(3)
            avg_score = assess_df['Assessment_Score_Pct'].mean()
            pass_rate = (len(assess_df[assess_df['Assessment_Score_Pct'] >= 40.0]) / len(assess_df)) * 100 if len(assess_df) > 0 else 0
            high_rate = (len(assess_df[assess_df['Assessment_Score_Pct'] >= 75.0]) / len(assess_df)) * 100 if len(assess_df) > 0 else 0

            a_col1.metric("Average Assessment Score", f"{avg_score:.1f}%")
            a_col2.metric("Pass Rate (>= 40%)", f"{pass_rate:.1f}%")
            a_col3.metric("High Performers (>= 75%)", f"{high_rate:.1f}%")

            st.markdown("---")

            col_a1, col_a2 = st.columns(2)

            with col_a1:
                st.subheader("📚 Subject-Wise Assessment Scores")
                subj_score = assess_df.groupby('Subject')['Assessment_Score_Pct'].mean().reset_index()
                fig_as = px.bar(
                    subj_score, x="Subject", y="Assessment_Score_Pct", color="Subject",
                    title="Average Assessment Score by Subject (%)", text_auto=".1f"
                )
                fig_as.add_hline(y=75.0, line_dash="dash", line_color="green", annotation_text="Distinction Goal (75%)")
                st.plotly_chart(fig_as, use_container_width=True)

            with col_a2:
                st.subheader("🏫 Grade-Level Performance Impact")
                grade_score = assess_df.groupby('Grade')['Assessment_Score_Pct'].mean().reset_index()
                fig_ag = px.bar(
                    grade_score, x="Grade", y="Assessment_Score_Pct", color="Grade",
                    title="Average Assessment Score by Grade Level (%)", text_auto=".1f"
                )
                st.plotly_chart(fig_ag, use_container_width=True)

            st.markdown("---")
            st.subheader("📋 Granular Assessment Leaderboard")
            display_assess_table = assess_df[['Institution', 'FullName', 'Grade', 'Subject', 'Assessment_Score_Pct']].rename(columns={
                'Institution': 'School', 'FullName': 'Teacher Name', 'Assessment_Score_Pct': 'Average Score (%)'
            })
            st.dataframe(display_assess_table, use_container_width=True)

            pdf_tab7 = generate_pdf_report(
                title_text="📊 Student Assessment Outcomes Report",
                subtitle_text=f"Period: {filter_description_text} | Total Evaluated Records: {len(assess_df)}",
                summary_metrics={
                    "Average Score": f"{avg_score:.1f}%",
                    "Pass Rate": f"{pass_rate:.1f}%",
                    "High Performers": f"{high_rate:.1f}%"
                },
                dataframe=display_assess_table
            )
            st.download_button(
                label="📄 Download Assessment Outcomes Report (PDF)",
                data=pdf_tab7,
                file_name=f"Assessment_Outcomes_Report_{selected_month.replace(' ', '_')}.pdf",
                mime="application/pdf"
            )
