import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
from datetime import datetime

# Supabase Client
from supabase import create_client, Client

# ReportLab PDF Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout
st.set_page_config(page_title="Academic Manager Portfolio & Teacher KPI Dashboard", layout="wide")

# 0. Initialize Supabase Connection
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Failed to connect to Supabase. Check your .streamlit/secrets.toml configuration: {e}")
        return None

supabase = init_supabase()

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

st.title("🏫 Academic Manager Portfolio & Teacher KPI Review Dashboard")
st.markdown("Track **School Portfolio Management**, **School WoW Velocity**, **Teacher Execution Tiers**, **Daily KPIs (10m Lesson / 30m Library)**, **360° Qualitative Evidences**, and **Assessment Outcomes** via Supabase Cloud.")

# 1. Load Data from Supabase Cloud Database
@st.cache_data(ttl=60)
def load_supabase_data():
    if not supabase:
        return pd.DataFrame()
    try:
        # Fetching records from 'user_metrics' table in Supabase
        response = supabase.table("user_metrics").select("*").execute()
        data = response.data
        if data:
            df_cloud = pd.DataFrame(data)
            return df_cloud
        return pd.DataFrame()
    except Exception as e:
        # If table doesn't exist yet or query fails, return empty df gracefully
        return pd.DataFrame()

df = load_supabase_data()

# 2. Sidebar Data Upload Manager & Sync to Supabase
st.sidebar.header("📁 Cloud Data Sync & Excel Upload")
uploaded_files = st.sidebar.file_uploader("Upload UserMetrics Excel (.xlsx)", type=["xlsx"], accept_multiple_files=True, key="user_metrics_uploader")

if uploaded_files and supabase:
    if st.sidebar.button("☁️ Push Uploaded Data to Supabase"):
        with st.spinner("Syncing records to Supabase..."):
            total_inserted = 0
            for file in uploaded_files:
                try:
                    temp_df = pd.read_excel(file, sheet_name="UserMetrics")

                    # Cleaning & Data Normalization
                    temp_df['FirstName'] = temp_df['FirstName'].fillna('').astype(str).str.strip() if 'FirstName' in temp_df.columns else ''
                    temp_df['LastName'] = temp_df['LastName'].fillna('').astype(str).str.strip() if 'LastName' in temp_df.columns else ''
                    temp_df['FullName'] = (temp_df['FirstName'] + " " + temp_df['LastName']).str.strip()
                    temp_df.loc[temp_df['FullName'] == '', 'FullName'] = 'Unknown Teacher'

                    if 'Institution' not in temp_df.columns:
                        temp_df['Institution'] = "Default School"
                    else:
                        temp_df['Institution'] = temp_df['Institution'].fillna('Unknown School').astype(str).str.strip()

                    for col in ['Grade', 'Subject', 'Book']:
                        if col not in temp_df.columns:
                            temp_df[col] = ''
                        else:
                            temp_df[col] = temp_df[col].fillna('').astype(str).str.strip()

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
                        temp_df['StartTime'] = pd.to_datetime(temp_df['StartTime'], errors='coerce').astype(str)

                    # Keep essential columns for cloud sync
                    sync_cols = [c for c in ['Institution', 'FullName', 'Grade', 'Subject', 'Book', 'Type', 'Duration_Min', 'StartTime', 'Assessment_Score_Pct'] if c in temp_df.columns]
                    records_to_insert = temp_df[sync_cols].to_dict(orient='records')

                    # Insert to Supabase 'user_metrics' table
                    supabase.table("user_metrics").upsert(records_to_insert).execute()
                    total_inserted += len(records_to_insert)
                except Exception as e:
                    st.sidebar.error(f"Error processing {file.name}: {e}")
            
            st.sidebar.success(f"Successfully synced {total_inserted} records to Supabase!")
            st.cache_data.clear()
            st.rerun()

# 3. Database Status & Controls
st.sidebar.markdown("---")
st.sidebar.header("🗄️ Supabase Cloud Status")

if not df.empty:
    st.sidebar.metric("Cloud DB Total Records", len(df))
    if st.sidebar.button("🔄 Refresh Cloud Cache"):
        st.cache_data.clear()
        st.rerun()
else:
    st.info("👋 No data found in Supabase. Upload your raw daily or weekly `UserMetrics.xlsx` files in the sidebar and click **Push Uploaded Data to Supabase**.")

if df.empty:
    st.warning("Please populate your Supabase `user_metrics` table using the sidebar file uploader to render the dashboard analytics.")
else:
    # Build Date, Month, and Enhanced Month-Based Week Columns
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
            week_ranges['min'].apply(lambda x: x.strftime('%b %d') if pd.notnull(x) else '') + " to " + 
            week_ranges['max'].apply(lambda x: x.strftime('%b %d') if pd.notnull(x) else '')
        )

        df = df.merge(week_ranges[['Month_Name', 'Week_Num', 'Week_Date_Range']], on=['Month_Name', 'Week_Num'], how='left')
        df['Month_Week_Label'] = df['StartTime'].dt.strftime('%b %Y') + " - Week " + df['Week_Num'].astype(str) + " (" + df['Week_Date_Range'] + ")"
        df['Week'] = df['Month_Week_Label']
    else:
        df['Date'] = "N/A"
        df['Month_Name'] = "N/A"
        df['Week'] = "N/A"

    # Build Master Teacher Roster across database records
    master_teacher_roster = df[['Institution', 'FullName']].dropna().drop_duplicates()

    # Sidebar Review Filters
    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Review Filters")
    all_schools = sorted([str(s) for s in df['Institution'].unique() if pd.notnull(s)])
    selected_schools = st.sidebar.multiselect("Select School(s)", options=all_schools, default=all_schools)

    school_master_roster = master_teacher_roster[master_teacher_roster['Institution'].isin(selected_schools)]
    school_filtered_df = df[df['Institution'].isin(selected_schools)]

    # MONTH-FIRST & CALENDAR HOLIDAY MANAGER
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Calendar & Holiday Manager")

    available_months_df = school_filtered_df[['Month_Sort', 'Month_Name']].dropna().drop_duplicates().sort_values(by='Month_Sort', ascending=False)
    month_options = available_months_df['Month_Name'].tolist()

    if month_options:
        selected_month = st.sidebar.selectbox("Select Review Month:", options=month_options)
        month_filtered_df = school_filtered_df[school_filtered_df['Month_Name'] == selected_month]
    else:
        selected_month = "Current Period"
        month_filtered_df = school_filtered_df

    exclude_sundays_flag = st.sidebar.checkbox("🗓️ Exclude Sundays from KPIs", value=True)

    user_excluded_dates = []
    if not month_filtered_df.empty and not month_filtered_df['Date'].isna().all():
        m_min_date = month_filtered_df['Date'].min()
        m_max_date = month_filtered_df['Date'].max()
        if pd.notnull(m_min_date) and pd.notnull(m_max_date):
            all_month_possible_dates = [d.date() for d in pd.date_range(start=m_min_date, end=m_max_date)]
            user_excluded_dates = st.sidebar.multiselect(
                f"🗓️ Punch Holidays for {selected_month}:",
                options=all_month_possible_dates,
                format_func=lambda x: x.strftime('%Y-%m-%d')
            )

    view_mode = st.sidebar.radio("Granularity:", ["Full Month Summary", "Specific Week of Month", "Single Day Review"])
    available_month_weeks = sorted(month_filtered_df['Month_Week_Label'].dropna().unique()) if not month_filtered_df.empty else []
    available_dates = sorted(month_filtered_df['Date'].dropna().unique(), reverse=True) if not month_filtered_df.empty else []

    if view_mode == "Full Month Summary":
        filtered_df = month_filtered_df
        min_d = month_filtered_df['Date'].min() if not month_filtered_df.empty else datetime.today().date()
        max_d = month_filtered_df['Date'].max() if not month_filtered_df.empty else datetime.today().date()
        selected_num_days = get_working_days(min_d, max_d, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Full Month: {selected_month} ({selected_num_days} Working Day(s))"

    elif view_mode == "Specific Week of Month" and available_month_weeks:
        selected_week_label = st.sidebar.selectbox("Select Week:", options=available_month_weeks)
        filtered_df = month_filtered_df[month_filtered_df['Month_Week_Label'] == selected_week_label]
        w_start = filtered_df['Date'].min()
        w_end = filtered_df['Date'].max()
        selected_num_days = get_working_days(w_start, w_end, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"{selected_week_label} ({selected_num_days} Working Day(s))"

    elif view_mode == "Single Day Review" and available_dates:
        selected_date = st.sidebar.selectbox("Select Day:", options=available_dates)
        filtered_df = month_filtered_df[month_filtered_df['Date'] == selected_date]
        selected_num_days = get_working_days(selected_date, selected_date, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Single Date: {selected_date} ({selected_num_days} Working Day(s))"
    else:
        filtered_df = month_filtered_df
        selected_num_days = 1
        filter_description_text = "Review Period"

    calc_ld_kpi = 10.0 * selected_num_days
    calc_lib_kpi = 30.0 * selected_num_days

    available_teachers = sorted([str(t) for t in school_master_roster['FullName'].unique() if pd.notnull(t)])
    selected_teachers = st.sidebar.multiselect("Select Teacher(s)", options=available_teachers, default=available_teachers)

    filtered_roster = school_master_roster[school_master_roster['FullName'].isin(selected_teachers)]
    filtered_df = filtered_df[filtered_df['FullName'].isin(selected_teachers)] if not filtered_df.empty else filtered_df

    # 8 Dedicated Review & Submission Tabs
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "📘 1. Daily Lesson Plan KPI", 
        "📚 2. Daily Library KPI", 
        "📖 3. Daily Content & Chapters", 
        "👤 4. Teacher 360° Profile Report",
        "🏛️ 5. Manager Portfolio Quadrants",
        "🏫 6. School Teacher Progression",
        "📊 7. Student Assessment Outcomes",
        "📝 8. Teacher Submission Portal"
    ])

    # TAB 1: DAILY LESSON PLAN COMPLIANCE
    with tab1:
        st.header("📘 Daily Lesson Plan Preparation Tracker")
        st.caption(f"KPI Benchmark: **At least {calc_ld_kpi:.0f} Minutes** ({10} mins/day across {selected_num_days} working day(s)).")

        ld_df = filtered_df[filtered_df['Type'] == 'lessonDelivery'] if not filtered_df.empty else pd.DataFrame()
        ld_usage = ld_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index() if not ld_df.empty else pd.DataFrame(columns=['Institution', 'FullName', 'Duration_Min'])
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

        if not ld_daily.empty:
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
        st.caption(f"KPI Benchmark: **At least {calc_lib_kpi:.0f} Minutes** ({30} mins/day across {selected_num_days} working day(s)).")

        lib_df = filtered_df[filtered_df['Type'] == 'library'] if not filtered_df.empty else pd.DataFrame()
        lib_usage = lib_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index() if not lib_df.empty else pd.DataFrame(columns=['Institution', 'FullName', 'Duration_Min'])
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

        if not lib_daily.empty:
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

        content_df = filtered_df[filtered_df['Book'].str.len() > 0] if not filtered_df.empty and 'Book' in filtered_df.columns else pd.DataFrame()

        if content_df.empty:
            st.info("No specific chapter/book access logs found for the selected global filters.")
        else:
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
                            title=f"Chapters Opened by {t3_teacher} (Mins)", text_auto=".1f"
                        )
                    else:
                        ch_summary = t3_df.groupby(['FullName', 'Book'])['Duration_Min'].sum().reset_index()
                        fig_ch = px.bar(
                            ch_summary, x="FullName", y="Duration_Min", color="Book",
                            title="Chapters / Books Opened per Teacher (Mins)", barmode="stack", text_auto=".1f"
                        )
                    st.plotly_chart(fig_ch, use_container_width=True)

                with col_c2:
                    subj_summary = t3_df.groupby('Subject')['Duration_Min'].sum().reset_index()
                    fig_sub = px.pie(subj_summary, names="Subject", values="Duration_Min", title="Subject / Theme Distribution (Minutes)")
                    st.plotly_chart(fig_sub, use_container_width=True)

                st.subheader("📋 Filtered Granular Class Log")
                display_content_log = t3_df.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes'}).sort_values(by='StartTime', ascending=False)
                st.dataframe(display_content_log, use_container_width=True)

    # TAB 4: SINGLE TEACHER 360° PROFILE REPORT
    with tab4:
        st.header("👤 Teacher 360° Performance Profile")
        all_roster_teachers = sorted(school_master_roster['FullName'].unique())

        if not all_roster_teachers:
            st.info("No teachers found in roster for the selected school(s).")
        else:
            target_teacher = st.selectbox("Select Teacher to Audit:", options=all_roster_teachers)
            teacher_date_data = filtered_df[filtered_df['FullName'] == target_teacher] if not filtered_df.empty else pd.DataFrame()
            teacher_school = school_master_roster[school_master_roster['FullName'] == target_teacher]['Institution'].values[0] if not school_master_roster[school_master_roster['FullName'] == target_teacher].empty else "N/A"

            st.markdown(f"### 📋 Audit Profile: **{target_teacher}** | School: **{teacher_school}**")

            t_day_ld = teacher_date_data[teacher_date_data['Type'] == 'lessonDelivery']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
            t_day_lib = teacher_date_data[teacher_date_data['Type'] == 'library']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0

            ld_pct = (t_day_ld / calc_ld_kpi) * 100 if calc_ld_kpi > 0 else 100.0
            lib_pct = (t_day_lib / calc_lib_kpi) * 100 if calc_lib_kpi > 0 else 100.0

            col_sum1, col_sum2 = st.columns([1, 1.2])
            with col_sum1:
                st.markdown("##### 📌 KPI Overview")
                s1, s2 = st.columns(2)
                s1.metric("Lesson Prep Mins", f"{t_day_ld:.1f} mins", delta=f"{ld_pct:.0f}% of KPI")
                s2.metric("Library Usage Mins", f"{t_day_lib:.1f} mins", delta=f"{lib_pct:.0f}% of KPI")
            with col_sum2:
                st.markdown("##### 📊 KPI Achievement Comparison")
                ach_df = pd.DataFrame({
                    'KPI Category': [f'Lesson Prep ({calc_ld_kpi:.0f}m)', f'Library Usage ({calc_lib_kpi:.0f}m)'],
                    'Logged Minutes': [t_day_ld, t_day_lib],
                    'KPI Standard': [calc_ld_kpi, calc_lib_kpi]
                })
                fig_ach = px.bar(ach_df, x='KPI Category', y=['LoggedMinutes', 'KPI Standard'], barmode='group', title="Performance vs Standard")
                st.plotly_chart(fig_ach, use_container_width=True)

    # TAB 5: MANAGER PORTFOLIO & SCHOOL QUADRANTS
    with tab5:
        st.header("🏛️ Academic Manager Portfolio Overview")
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
                ld_ok = row['Avg_Lesson_Prep_Mins'] >= 10.0
                lib_ok = row['Avg_Library_Usage_Mins'] >= 30.0
                if ld_ok and lib_ok: return '🌟 Pace Setters'
                elif ld_ok and not lib_ok: return '📘 Lesson Focused'
                elif not ld_ok and lib_ok: return '📚 Library Focused'
                else: return '🚨 Priority Focus'

            school_stats['Classification'] = school_stats.apply(classify_school, axis=1)
            st.subheader("📋 Complete School Performance Leaderboard")
            st.dataframe(school_stats, use_container_width=True)

    # TAB 6: SCHOOL-LEVEL TEACHER PROGRESSION
    with tab6:
        st.header("🏫 School-Level Teacher Progression & Execution Tiers")
        all_schools_list_t6 = sorted(school_master_roster['Institution'].unique())
        if all_schools_list_t6:
            target_school_t6 = st.selectbox("Select School to Inspect:", options=all_schools_list_t6, key="t6_school_sel")
            school_t6_roster = school_master_roster[school_master_roster['Institution'] == target_school_t6]
            school_t6_data = school_filtered_df[school_filtered_df['Institution'] == target_school_t6]
            st.markdown(f"### School Audit: **{target_school_t6}**")
            st.dataframe(school_t6_roster, use_container_width=True)

    # TAB 7: STUDENT ASSESSMENT OUTCOMES
    with tab7:
        st.header("📊 Student Assessment Outcomes & Impact Analysis")
        if 'Assessment_Score_Pct' not in school_filtered_df.columns or school_filtered_df['Assessment_Score_Pct'].dropna().empty:
            st.info("No student assessment score data available in the current filter.")
        else:
            assess_df = school_filtered_df.dropna(subset=['Assessment_Score_Pct'])
            st.metric("Average Assessment Score", f"{assess_df['Assessment_Score_Pct'].mean():.1f}%")
            st.dataframe(assess_df, use_container_width=True)

    # TAB 8: ENHANCED TEACHER QUALITATIVE SUBMISSION PORTAL (DIRECTLY SAVING TO SUPABASE)
    with tab8:
        st.header("📝 Teacher Qualitative Submission Portal (Cloud Connected)")
        st.caption("Submit daily logs, qualitative feedback, voice notes, activity videos, and student artifacts directly into Supabase cloud database & storage.")

        if master_teacher_roster.empty:
            st.warning("Please upload your master roster or data via sidebar first so teachers and schools are initialized.")
        else:
            with st.form("teacher_cloud_portal_form"):
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    portal_schools = sorted(master_teacher_roster['Institution'].unique().tolist())
                    sub_school = st.selectbox("Select School:", options=portal_schools, key="portal_school")
                with col_p2:
                    school_teachers = sorted(master_teacher_roster[master_teacher_roster['Institution'] == sub_school]['FullName'].unique().tolist())
                    sub_teacher = st.selectbox("Select Teacher Name:", options=school_teachers if school_teachers else ["No teachers found"], key="portal_teacher")

                st.markdown("---")
                col_p3, col_p4, col_p5 = st.columns(3)
                with col_p3:
                    sub_grade = st.selectbox("Select Grade:", options=["Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6"], key="portal_grade")
                with col_p4:
                    sub_subject = st.selectbox("Select Subject:", options=["Mathematics", "English", "EVS", "Science", "Hindi"], key="portal_subject")
                with col_p5:
                    sub_book = st.text_input("Lesson Plan Number / Chapter Name:", key="portal_book")

                st.markdown("---")
                st.subheader("📁 Qualitative Artifact Uploads")
                up_c1, up_c2 = st.columns(2)
                with up_c1:
                    voice_file = st.file_uploader("Upload Voice Note (Audio)", type=["mp3", "wav", "m4a"], key="portal_voice")
                    activity_file = st.file_uploader("Upload Classroom Activity Video", type=["mp4", "mov", "avi"], key="portal_activity")
                with up_c2:
                    phonics_file = st.file_uploader("Upload Phonics Practice File", type=["mp3", "wav", "mp4", "m4a"], key="portal_phonics")
                    writing_file = st.file_uploader("Upload Student Writing Artifact", type=["pdf", "png", "jpg", "jpeg"], key="portal_writing")

                submitted_portal = st.form_submit_button("🚀 Submit Data & Evidence to Supabase Cloud")

                if submitted_portal:
                    if sub_teacher == "No teachers found" or not sub_book:
                        st.error("Please complete all required fields and select a valid teacher.")
                    else:
                        try:
                            # Helper to upload files to Supabase Storage bucket ('qualitative_evidences')
                            def upload_to_supabase_storage(file_obj, prefix):
                                if file_obj is not None:
                                    file_bytes = file_obj.getvalue()
                                    file_path = f"{sub_school}/{sub_teacher}_{prefix}_{file_obj.name}"
                                    # Uploading to Supabase Storage Bucket
                                    supabase.storage.from_("qualitative_evidences").upload(
                                        path=file_path,
                                        file=file_bytes,
                                        file_options={"upsert": "true"}
                                    )
                                    # Get Public URL
                                    public_url = supabase.storage.from_("qualitative_evidences").get_public_url(file_path)
                                    return public_url
                                return None

                            v_url = upload_to_supabase_storage(voice_file, "voice")
                            a_url = upload_to_supabase_storage(activity_file, "activity")
                            p_url = upload_to_supabase_storage(phonics_file, "phonics")
                            w_url = upload_to_supabase_storage(writing_file, "writing")

                            # Insert record into Supabase table 'user_metrics'
                            new_record = {
                                'Institution': sub_school,
                                'FullName': sub_teacher,
                                'Grade': sub_grade,
                                'Subject': sub_subject,
                                'Book': sub_book,
                                'Type': 'lessonDelivery',
                                'Duration_Min': 10.0,
                                'StartTime': datetime.now().isoformat(),
                                'Voice_Note_Link': v_url,
                                'Video_Evidence_1': a_url,
                                'Video_Evidence_2': p_url,
                                'Writing_Sample_Link': w_url
                            }

                            supabase.table("user_metrics").insert(new_record).execute()
                            st.success(f"Successfully uploaded qualitative evidence and recorded session for **{sub_teacher}** to Supabase!")
                            st.cache_data.clear()
                        except Exception as e:
                            st.error(f"Error pushing submission to Supabase: {e}")
