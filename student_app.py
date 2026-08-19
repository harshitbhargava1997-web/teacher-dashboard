import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO
from supabase import create_client

# ReportLab PDF Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration
st.set_page_config(page_title="OneLearn - Grade & Student Usage Reports", page_icon="📊", layout="wide")

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

def convert_seconds_to_hms(total_seconds):
    try:
        total_seconds = int(float(total_seconds))
    except:
        total_seconds = 0
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}", f"{minutes:02d}", f"{seconds:02d}"


# --- MULTI-PAGE COMPREHENSIVE PDF GENERATOR ---
def generate_grade_master_pdf(school_name, grade_name, filter_desc, summary_metrics, grade_content_summary, grade_platform_summary, student_data_dict):
    """Generates a master PDF starting with a Grade Consolidated Summary page, followed by individual student pages."""
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

    # PAGE 1: GRADE CONSOLIDATED SUMMARY
    story.append(Paragraph(f"<b>Grade-Level Consolidated Report: {grade_name}</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"<b>Institution:</b> {school_name} | <b>Period:</b> {filter_desc}", subtitle_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

    # KPI Metrics
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

    # Grade Content Summary Table
    story.append(Paragraph("<b>1. Grade Consolidated Content Usage (Books & Chapters)</b>", sec_head_style))
    if grade_content_summary is not None and not grade_content_summary.empty:
        gc_data = [grade_content_summary.columns.tolist()] + grade_content_summary.astype(str).values.tolist()
        gc_table = Table(gc_data, colWidths=[230, 160, 50, 50, 50])
        gc_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('GRID', (0, 0), (-1, -1), 0.4, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(gc_table)

    story.append(Spacer(1, 10))

    # Grade Platform Summary Table
    story.append(Paragraph("<b>2. Grade Consolidated Platform Usage (Features & Modules)</b>", sec_head_style))
    if grade_platform_summary is not None and not grade_platform_summary.empty:
        gp_data = [grade_platform_summary.columns.tolist()] + grade_platform_summary.astype(str).values.tolist()
        gp_table = Table(gp_data, colWidths=[290, 80, 50, 50, 70])
        gp_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('GRID', (0, 0), (-1, -1), 0.4, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(gp_table)

    # SUBSEQUENT PAGES: INDIVIDUAL STUDENT PROFILES
    student_head_style = ParagraphStyle('StudentHead', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor('#0F172A'), fontName='Helvetica-Bold', spaceBefore=4, spaceAfter=6)
    
    for student_name, data in student_data_dict.items():
        story.append(PageBreak())
        story.append(Paragraph(f"<b>Student Usage Report</b>", title_style))
        story.append(Spacer(1, 2))
        story.append(Paragraph(f"<b>Institution:</b> {school_name} | <b>Grade:</b> {grade_name} | <b>Period:</b> {filter_desc}", subtitle_style))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceAfter=8))

        story.append(Paragraph(f"<b>{student_name.upper()}</b>", student_head_style))

        # Individual Content Usage
        story.append(Paragraph("<b>Content Usage (Books & Chapters Opened)</b>", sec_head_style))
        content_df = data['content']
        if not content_df.empty:
            c_data = [content_df.columns.tolist()] + content_df.astype(str).values.tolist()
            c_table = Table(c_data, colWidths=[35, 175, 110, 65, 75, 80])
            c_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('GRID', (0, 0), (-1, -1), 0.4, border_color),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(c_table)
        else:
            story.append(Paragraph("No content usage records.", styles['Normal']))

        story.append(Spacer(1, 8))

        # Individual Platform Usage
        story.append(Paragraph("<b>Platform Usage (Features & Modules)</b>", sec_head_style))
        platform_df = data['platform']
        if not platform_df.empty:
            p_data = [platform_df.columns.tolist()] + platform_df.astype(str).values.tolist()
            p_table = Table(p_data, colWidths=[35, 205, 80, 75, 75, 70])
            p_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('GRID', (0, 0), (-1, -1), 0.4, border_color),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(p_table)
        else:
            story.append(Paragraph("No platform usage records.", styles['Normal']))

    doc.build(story)
    buffer.seek(0)
    return buffer


# --- MAIN APP ---
st.title("📊 OneLearn - Grade & Student Usage Reports")
st.markdown("Select a school and grade level to review consolidated grade summaries and individual student pages, with the PDF download option conveniently located at the top.")

df = fetch_master_db_from_supabase()

if df.empty:
    st.info("No data available in the cloud database.")
    st.stop()

# Filter strictly for students
student_master_df = df[df['Role'].str.casefold().str.contains('student', na=False)].copy()

if student_master_df.empty:
    st.warning("No student records found.")
    st.stop()

# --- SIDEBAR FILTERS ---
st.sidebar.header("🔍 Report Filters")

all_schools = sorted([str(s) for s in student_master_df['Institution'].unique() if str(s).strip()])
selected_school = st.sidebar.selectbox("Select School / Institution", options=all_schools)
school_df = student_master_df[student_master_df['Institution'] == selected_school]

all_grades = sorted([str(g) for g in school_df['Grade'].unique() if str(g).strip() and str(g).lower() != 'nan'])
selected_grade = st.sidebar.selectbox("Select Grade Level", options=all_grades)
grade_df = school_df[school_df['Grade'] == selected_grade]

if grade_df.empty:
    st.warning("No student records found for this grade.")
    st.stop()

# --- PREPARE DATA FOR GRADE & STUDENTS ---
all_students_in_grade = sorted([s for s in grade_df['FullName'].unique() if s and str(s).lower() != 'unknown student'])

# --- TOP DOWNLOAD BUTTON AREA ---
st.markdown("---")
col_top_info, col_top_btn = st.columns([2, 1])
with col_top_info:
    st.subheader(f"🏫 **{selected_school}** — Grade: **{selected_grade}**")
    st.caption(f"Total Active Students in Grade: {len(all_students_in_grade)}")

# Generate summaries for PDF and UI
content_sub_all = grade_df[grade_df['Type'].str.casefold().isin(['book', 'library'])].copy()
platform_sub_all = grade_df[~grade_df['Type'].str.casefold().isin(['book'])].copy()

# Grade Content Summary Table
if not content_sub_all.empty:
    gc_agg = content_sub_all.groupby(['Subject', 'Book']).agg(Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)).reset_index()
    gc_agg = gc_agg[gc_agg['Total_Seconds'] > 0].sort_values(by='Total_Seconds', ascending=False)
    gc_rows = []
    for idx, row in gc_agg.iterrows():
        hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
        gc_rows.append({'SUBJECT': row['Subject'], 'BOOK / CHAPTER': row['Book'], 'HOURS': hrs, 'MINUTES': mins, 'SECONDS': secs})
    grade_content_summary_df = pd.DataFrame(gc_rows)
else:
    grade_content_summary_df = pd.DataFrame()

# Grade Platform Summary Table
if not platform_sub_all.empty:
    gp_agg = platform_sub_all.groupby('Type').agg(Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)).reset_index()
    gp_agg = gp_agg[gp_agg['Total_Seconds'] > 0].sort_values(by='Total_Seconds', ascending=False)
    gp_rows = []
    for idx, row in gp_agg.iterrows():
        hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
        gp_rows.append({'FEATURE / MODULE': str(row['Type']).capitalize(), 'HOURS': hrs, 'MINUTES': mins, 'SECONDS': secs})
    grade_platform_summary_df = pd.DataFrame(gp_rows)
else:
    grade_platform_summary_df = pd.DataFrame()

# Build student dictionary
student_data_dict = {}
for student_name in all_students_in_grade:
    s_df = grade_df[grade_df['FullName'] == student_name]
    
    # Student Content
    c_sub = s_df[s_df['Type'].str.casefold().isin(['book', 'library'])].copy()
    if not c_sub.empty:
        c_agg = c_sub.groupby(['Subject', 'Book']).agg(Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)).reset_index()
        c_agg = c_agg[c_agg['Total_Seconds'] > 0].sort_values(by='Total_Seconds', ascending=False)
        c_rows = []
        for idx, row in c_agg.iterrows():
            hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
            c_rows.append({'S.NO': idx+1, 'SUBJECT': row['Subject'], 'BOOK / CHAPTER': row['Book'], 'HOURS': hrs, 'MINUTES': mins, 'SECONDS': secs})
        c_df = pd.DataFrame(c_rows)
    else:
        c_df = pd.DataFrame()

    # Student Platform
    p_sub = s_df[~s_df['Type'].str.casefold().isin(['book'])].copy()
    if not p_sub.empty:
        p_agg = p_sub.groupby('Type').agg(Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)).reset_index()
        p_agg = p_agg[p_agg['Total_Seconds'] > 0].sort_values(by='Total_Seconds', ascending=False)
        p_rows = []
        for idx, row in p_agg.iterrows():
            hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
            p_rows.append({'S.NO': idx+1, 'FEATURE / MODULE': str(row['Type']).capitalize(), 'HOURS': hrs, 'MINUTES': mins, 'SECONDS': secs})
        p_df = pd.DataFrame(p_rows)
    else:
        p_df = pd.DataFrame()

    student_data_dict[student_name] = {'content': c_df, 'platform': p_df}

with col_top_btn:
    metrics_dict = {"Grade": selected_grade, "Students": str(len(all_students_in_grade))}
    master_pdf_buf = generate_grade_master_pdf(
        school_name=selected_school,
        grade_name=selected_grade,
        filter_desc="Active Academic Term",
        summary_metrics=metrics_dict,
        grade_content_summary=grade_content_summary_df,
        grade_platform_summary=grade_platform_summary_df,
        student_data_dict=student_data_dict
    )
    st.download_button(
        label=f"📄 Download Complete {selected_grade} PDF Report",
        data=master_pdf_buf,
        file_name=f"Comprehensive_Student_Report_{selected_school.replace(' ', '_')}_{selected_grade}.pdf",
        mime="application/pdf",
        type="primary"
    )

st.markdown("---")

# --- UI PREVIEW: GRADE SUMMARY & STUDENT TABS ---
st.markdown("### 📋 Grade Consolidated Summary")
col_sum1, col_sum2 = st.columns(2)
with col_sum1:
    st.markdown("##### **Consolidated Content Usage**")
    if not grade_content_summary_df.empty:
        st.dataframe(grade_content_summary_df, use_container_width=True, hide_index=True)
    else:
        st.info("No grade content records.")
with col_sum2:
    st.markdown("##### **Consolidated Platform Usage**")
    if not grade_platform_summary_df.empty:
        st.dataframe(grade_platform_summary_df, use_container_width=True, hide_index=True)
    else:
        st.info("No grade platform records.")

st.markdown("---")
st.markdown("### 👤 Individual Student Profiles")

for student_name in all_students_in_grade:
    with st.expander(f"Student: {student_name}", expanded=False):
        tab_c, tab_p = st.tabs(["📚 Content Usage", "🌐 Platform Usage"])
        with tab_c:
            if not student_data_dict[student_name]['content'].empty:
                st.dataframe(student_data_dict[student_name]['content'], use_container_width=True, hide_index=True)
            else:
                st.info("No content usage logged.")
        with tab_p:
            if not student_data_dict[student_name]['platform'].empty:
                st.dataframe(student_data_dict[student_name]['platform'], use_container_width=True, hide_index=True)
            else:
                st.info("No platform usage logged.")
