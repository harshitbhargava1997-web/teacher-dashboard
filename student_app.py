import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import re
from io import BytesIO
from supabase import create_client

# ReportLab PDF Libraries for KDM Export
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration
st.set_page_config(page_title="Student Engagement & Usage Dashboard", page_icon="👨‍🎓", layout="wide")

# --- SUPABASE CLOUD SETUP ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"].rstrip('/')
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    BUCKET_NAME = st.secrets["supabase"]["bucket_name"]
    PARQUET_FILE_NAME = "master_database.parquet"
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"Supabase credentials missing or misconfigured in Streamlit Secrets: {e}")

def _norm_text(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def _sanitize_df_for_parquet(df_in):
    if df_in is None or df_in.empty:
        return pd.DataFrame()
    out = df_in.copy()
    for dt_col in ['StartTime', 'EndTime']:
        if dt_col in out.columns:
            out[dt_col] = pd.to_datetime(out[dt_col], errors='coerce')
    return out

def normalize_identity_columns(df):
    out = df.copy()
    for col in ["Institution", "Center", "FirstName", "LastName", "FullName", "Role", "Uploaded_By", "State_Zone"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].map(_norm_text)

    out.loc[out["State_Zone"].eq(""), "State_Zone"] = "Madhya Pradesh (MP)"
    out.loc[out["Uploaded_By"].eq(""), "Uploaded_By"] = "Harshit Bhargava"

    calculated_full = (out["FirstName"].fillna("") + " " + out["LastName"].fillna("")).map(_norm_text)
    empty_full = out["FullName"].eq("")
    out.loc[empty_full, "FullName"] = calculated_full.loc[empty_full]
    out.loc[out["FullName"].eq(""), "FullName"] = "Unknown Student"
    return out

# --- HIGH-PERFORMANCE CACHED DATA FETCHER ---
@st.cache_data(ttl=10800, show_spinner="Loading student database from cloud...")
def fetch_master_db_from_supabase():
    base_df = pd.DataFrame()
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            base_df = pd.read_parquet(BytesIO(response))
            base_df = _sanitize_df_for_parquet(base_df)
    except Exception:
        pass
    return normalize_identity_columns(base_df) if not base_df.empty else base_df


# --- PDF KDM DETAILED REPORT GENERATOR ---
def generate_student_hierarchical_pdf(school_name, filter_desc, summary_metrics, type_df, subj_df, student_summary_df):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor('#2563EB')
    dark_neutral = colors.HexColor('#1E293B')
    light_bg = colors.HexColor('#F8FAFC')
    border_color = colors.HexColor('#E2E8F0')
    
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=15, textColor=primary_color, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9.5, textColor=dark_neutral)
    sec_head_style = ParagraphStyle('SecHead', parent=styles['Heading2'], fontSize=11, textColor=primary_color, fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=4)
    card_header = ParagraphStyle('CardHead', parent=styles['Normal'], fontSize=7.5, textColor=colors.HexColor('#64748B'), fontName='Helvetica-Bold', alignment=1)
    card_value = ParagraphStyle('CardVal', parent=styles['Normal'], fontSize=11, textColor=primary_color, fontName='Helvetica-Bold', alignment=1)

    story.append(Paragraph("<b>Detailed Student Engagement & Content Usage Report (KDM Review)</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"<b>Institution:</b> {school_name} | <b>Period:</b> {filter_desc}", subtitle_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

    # KPI Metrics Cards
    headers_row = [Paragraph(k, card_header) for k in summary_metrics.keys()]
    values_row = [Paragraph(str(v), card_value) for v in summary_metrics.values()]
    col_w = 540 / len(summary_metrics)
    kpi_table = Table([headers_row, values_row], colWidths=[col_w] * len(summary_metrics))
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), light_bg),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, border_color),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 10))

    # Subject Table
    if not subj_df.empty:
        story.append(Paragraph("<b>Time Allocation by Subject / Theme</b>", sec_head_style))
        subj_data = [["Subject / Theme", "Total Minutes Logged"]] + subj_df.astype(str).values.tolist()
        subj_table = Table(subj_data, colWidths=[360, 180])
        subj_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
        ]))
        story.append(subj_table)
        story.append(Spacer(1, 10))

    # Student-Wise Summary Table
    if student_summary_df is not None and not student_summary_df.empty:
        story.append(Paragraph("<b>Grade ➔ Student ➔ Module ➔ Content Usage Breakdown</b>", sec_head_style))
        table_data = [["Grade", "Student Name", "Module Type", "Subject / Theme", "Time (Mins)"]] + student_summary_df.head(35).astype(str).values.tolist()
        hier_table = Table(table_data, colWidths=[65, 110, 85, 205, 75])
        hier_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.4, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(hier_table)

    doc.build(story)
    buffer.seek(0)
    return buffer


# --- MAIN APP EXECUTION ---
st.title("👨‍🎓 Student Engagement & Detailed Usage Analytics Dashboard")
st.markdown("Explore grade-level, student-wise, and content-specific usage reports for school principal and KDM reviews.")

df = fetch_master_db_from_supabase()

if df.empty:
    st.info("No data available in the cloud database. Please ensure data is uploaded via the Admin Portal.")
    st.stop()

# STRICTLY FILTER FOR STUDENTS ONLY
student_master_df = df[df['Role'].str.casefold().str.contains('student', na=False)].copy()

if student_master_df.empty:
    st.warning("No student records found in the database. Ensure student UserMetrics are uploaded.")
    st.stop()

# Prepare Dates
if 'StartTime' in student_master_df.columns and not student_master_df['StartTime'].isna().all():
    student_master_df['Date'] = student_master_df['StartTime'].dt.date
    student_master_df['Month_Name'] = student_master_df['StartTime'].dt.strftime('%B %Y')
    student_master_df['Month_Sort'] = student_master_df['StartTime'].dt.strftime('%Y-%m')
    
    def get_week_of_month(dt):
        try:
            first_day = dt.replace(day=1)
            dom = dt.day
            adjusted_dom = dom + first_day.weekday()
            return int(np.ceil(adjusted_dom / 7.0))
        except:
            return 1

    student_master_df['Week_Num'] = student_master_df['StartTime'].apply(get_week_of_month)
    week_ranges = student_master_df.groupby(['Month_Name', 'Week_Num'])['Date'].agg(['min', 'max']).reset_index()
    week_ranges['Week_Date_Range'] = (
        week_ranges['min'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '') + " to " + 
        week_ranges['max'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '')
    )
    student_master_df = student_master_df.merge(week_ranges[['Month_Name', 'Week_Num', 'Week_Date_Range']], on=['Month_Name', 'Week_Num'], how='left')
    student_master_df['Month_Week_Label'] = student_master_df['StartTime'].dt.strftime('%b %Y') + " - Week " + student_master_df['Week_Num'].astype(str) + " (" + student_master_df['Week_Date_Range'] + ")"
else:
    student_master_df['Date'] = None
    student_master_df['Month_Name'] = "N/A"
    student_master_df['Month_Week_Label'] = "N/A"

# --- SIDEBAR FILTERS ---
st.sidebar.header("🔍 Institutional Filters")

all_states = sorted([str(s) for s in student_master_df['State_Zone'].unique() if str(s).strip() and str(s).lower() not in ['nan', 'none']])
selected_states = st.sidebar.multiselect("1. Select State(s)", options=all_states, default=all_states)
df_state = student_master_df[student_master_df['State_Zone'].isin(selected_states)] if selected_states else student_master_df

all_schools = sorted([str(s) for s in df_state['Institution'].unique() if str(s).strip() and str(s).lower() not in ['nan', 'none']])
selected_school = st.sidebar.selectbox("2. Select School / Institution", options=["All Schools"] + all_schools)

df_school = df_state if selected_school == "All Schools" else df_state[df_state['Institution'] == selected_school]

st.sidebar.markdown("---")
st.sidebar.header("📅 Date & Granularity Filters")

available_months = df_school[['Month_Sort', 'Month_Name']].dropna().drop_duplicates().sort_values(by='Month_Sort', ascending=False)['Month_Name'].tolist()
selected_month = st.sidebar.selectbox("Select Review Month:", options=available_months if available_months else ["No Month Data"])
month_df = df_school[df_school['Month_Name'] == selected_month]

view_mode = st.sidebar.radio("Granularity:", ["Full Month Summary", "Specific Week of Month", "Single Day Review", "Custom Date Range"])

if month_df.empty and view_mode != "Custom Date Range":
    filtered_df = month_df
    filter_description = f"Full Month: {selected_month}"
elif view_mode == "Full Month Summary":
    filtered_df = month_df
    filter_description = f"Full Month: {selected_month}"
elif view_mode == "Specific Week of Month":
    available_weeks = sorted(month_df['Month_Week_Label'].dropna().unique())
    selected_week = st.sidebar.selectbox("Select Week:", options=available_weeks)
    filtered_df = month_df[month_df['Month_Week_Label'] == selected_week]
    filter_description = f"{selected_week}"
elif view_mode == "Single Day Review":
    available_dates = sorted(month_df['Date'].dropna().unique(), reverse=True)
    selected_date = st.sidebar.selectbox("Select Day:", options=available_dates)
    filtered_df = month_df[month_df['Date'] == selected_date]
    filter_description = f"Single Date: {selected_date}"
else:
    min_avail = df_school['Date'].dropna().min() if not df_school['Date'].dropna().empty else pd.Timestamp.now().date()
    max_avail = df_school['Date'].dropna().max() if not df_school['Date'].dropna().empty else pd.Timestamp.now().date()
    custom_date = st.sidebar.date_input("Select Custom Date Range:", value=(min_avail, max_avail), min_value=min_avail, max_value=max_avail)
    if isinstance(custom_date, (tuple, list)) and len(custom_date) == 2:
        c_start, c_end = custom_date
    else:
        c_start = c_end = custom_date[0] if isinstance(custom_date, (tuple, list)) else custom_date
    filtered_df = df_school[(df_school['Date'] >= c_start) & (df_school['Date'] <= c_end)]
    filter_description = f"Custom Range: {c_start} to {c_end}"

st.sidebar.markdown("---")
st.sidebar.header("🎯 Grade Level Filter")
all_grades = ["All Grades"] + sorted([str(g) for g in filtered_df['Grade'].unique() if str(g).strip() and str(g).lower() != 'nan'])
selected_grade = st.sidebar.selectbox("Filter by Grade", options=all_grades)
if selected_grade != "All Grades":
    filtered_df = filtered_df[filtered_df['Grade'] == selected_grade]

# --- MAIN DASHBOARD RENDER ---
if filtered_df.empty:
    st.warning("No student engagement records match the current filter criteria.")
else:
    st.subheader(f"Dashboard Overview: {selected_school}")
    st.caption(f"Observation Window: {filter_description} | Grade Level Filter: {selected_grade}")

    tot_student_time = filtered_df['Duration_Min'].sum() if 'Duration_Min' in filtered_df.columns else 0.0
    tot_student_sessions = len(filtered_df)
    unique_students = filtered_df['FullName'].nunique() if 'FullName' in filtered_df.columns else 0
    avg_time_per_session = tot_student_time / tot_student_sessions if tot_student_sessions > 0 else 0.0

    s_col1, s_col2, s_col3, s_col4 = st.columns(4)
    s_col1.metric("Total Active Platform Time", f"{tot_student_time:.1f} Mins ({tot_student_time/60:.1f} Hrs)")
    s_col2.metric("Total Platform Sessions", tot_student_sessions)
    s_col3.metric("Active Students Logged", unique_students)
    s_col4.metric("Avg. Time per Session", f"{avg_time_per_session:.1f} Mins")

    st.markdown("---")
    st.subheader("📊 Module & Subject Engagement Distribution")

    sc1, sc2 = st.columns(2)
    type_summary = pd.DataFrame()
    subj_summary = pd.DataFrame()

    with sc1:
        if 'Type' in filtered_df.columns:
            type_summary = filtered_df.groupby('Type')['Duration_Min'].sum().reset_index().round({'Duration_Min': 1})
            fig_stype = px.pie(type_summary, names='Type', values='Duration_Min', title="Engagement Time by Module Type")
            st.plotly_chart(fig_stype, use_container_width=True)
            
    with sc2:
        if 'Subject' in filtered_df.columns:
            subj_summary = filtered_df.groupby('Subject')['Duration_Min'].sum().reset_index().round({'Duration_Min': 1})
            fig_ssubj = px.bar(subj_summary, x='Duration_Min', y='Subject', orientation='h', title="Time Spent per Subject (Minutes)", text_auto=".1f")
            fig_ssubj.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_ssubj, use_container_width=True)

    # --- HIERARCHICAL TABLE DISPLAY ---
    st.subheader("📚 Detailed Grade ➔ Student ➔ Module ➔ Content Usage Breakdown")
    
    display_hier_table = pd.DataFrame()
    if not filtered_df.empty and 'FullName' in filtered_df.columns:
        # Group by Grade, FullName, Type, Subject/Book
        hier_agg = filtered_df.groupby(['Grade', 'FullName', 'Type', 'Subject']).agg(
            Total_Time_Mins=('Duration_Min', 'sum'),
            Content_Items=('Book', lambda x: ", ".join([str(b) for b in x.dropna().unique() if str(b).strip() and str(b).lower() != 'nan']))
        ).reset_index().sort_values(by=['Grade', 'FullName', 'Total_Time_Mins'], ascending=[True, True, False])

        hier_agg['Total_Time_Mins'] = hier_agg['Total_Time_Mins'].round(1)

        display_hier_table = hier_agg.rename(columns={
            'Grade': 'Grade Level',
            'FullName': 'Student Name',
            'Type': 'Module Type',
            'Subject': 'Subject / Theme',
            'Content_Items': 'Books / Chapters Opened',
            'Total_Time_Mins': 'Time (Mins)'
        })
        st.dataframe(display_hier_table, use_container_width=True)
    else:
        st.info("No detailed student breakdown available.")

    # --- EXPORT SECTION ---
    st.markdown("---")
    st.subheader("📥 Export Grade-Level & School-Wide Student Reports")
    
    btn_col1, btn_col2 = st.columns(2)
    
    with btn_col1:
        if not display_hier_table.empty:
            buf_s_xlsx = BytesIO()
            with pd.ExcelWriter(buf_s_xlsx, engine='openpyxl') as writer:
                display_hier_table.to_excel(writer, index=False, sheet_name="Grade_Student_Usage")
                filtered_df.rename(columns={'Duration_Min': 'Minutes'}).to_excel(writer, index=False, sheet_name="Raw_Student_Logs")
            buf_s_xlsx.seek(0)
            st.download_button(
                label=f"📥 Download Grade/Student Usage Report ({selected_grade}) [Excel]",
                data=buf_s_xlsx,
                file_name=f"Student_Usage_Report_{selected_school.replace(' ', '_')}_{selected_grade}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    with btn_col2:
        metrics_dict = {
            "Total Time": f"{tot_student_time:.1f}m",
            "Sessions": str(tot_student_sessions),
            "Students": str(unique_students),
            "Avg Session": f"{avg_time_per_session:.1f}m"
        }
        pdf_buf = generate_student_hierarchical_pdf(
            school_name=selected_school,
            filter_desc=f"{filter_description} | Grade Filter: {selected_grade}",
            summary_metrics=metrics_dict,
            type_df=type_summary,
            subj_df=subj_summary,
            student_summary_df=display_hier_table
        )
        st.download_button(
            label=f"📄 Download Executive KDM Report ({selected_grade}) [PDF]",
            data=pdf_buf,
            file_name=f"Student_KDM_Report_{selected_school.replace(' ', '_')}_{selected_grade}.pdf",
            mime="application/pdf"
        )
