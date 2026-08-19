import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO
from supabase import create_client

# ReportLab PDF Libraries for Grade & School Exports
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration
st.set_page_config(page_title="OneLearn - Grade & School Student Reports", page_icon="📊", layout="wide")

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


# --- PDF COMPREHENSIVE REPORT GENERATOR ---
def generate_comprehensive_student_pdf(school_name, scope_title, filter_desc, content_report_df, platform_report_df):
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
    sec_head_style = ParagraphStyle('SecHead', parent=styles['Heading2'], fontSize=11, textColor=primary_color, fontName='Helvetica-Bold', spaceBefore=12, spaceAfter=6)

    story.append(Paragraph(f"<b>Student Usage Report: {scope_title}</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"<b>Institution:</b> {school_name} | <b>Period:</b> {filter_desc}", subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

    # Content Section
    story.append(Paragraph("<b>1. Student-Wise Content Usage (Books & Chapters)</b>", sec_head_style))
    if content_report_df is not None and not content_report_df.empty:
        c_data = [content_report_df.columns.tolist()] + content_report_df.astype(str).values.tolist()
        c_table = Table(c_data, colWidths=[100, 55, 185, 60, 70, 70])
        c_table.setStyle(TableStyle([
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
        story.append(c_table)
    else:
        story.append(Paragraph("No content usage records found.", styles['Normal']))

    story.append(Spacer(1, 14))

    # Platform Section
    story.append(Paragraph("<b>2. Student-Wise Platform Usage (Features & Modules)</b>", sec_head_style))
    if platform_report_df is not None and not platform_report_df.empty:
        p_data = [platform_report_df.columns.tolist()] + platform_report_df.astype(str).values.tolist()
        p_table = Table(p_data, colWidths=[110, 60, 195, 60, 60, 55])
        p_table.setStyle(TableStyle([
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
        story.append(p_table)
    else:
        story.append(Paragraph("No platform usage records found.", styles['Normal']))

    doc.build(story)
    buffer.seek(0)
    return buffer


# --- MAIN APP ---
st.title("📊 OneLearn - Grade & School Student Reports")
st.markdown("Generate comprehensive student-wise content and platform usage reports filtered by Grade Level or Complete School.")

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
selected_scope = st.sidebar.selectbox("Select Report Scope", options=["Complete School (All Grades)"] + [f"Grade: {g}" for g in all_grades])

if selected_scope == "Complete School (All Grades)":
    scope_df = school_df
    scope_title = "Complete School Report"
else:
    grade_name = selected_scope.replace("Grade: ", "")
    scope_df = school_df[school_df['Grade'] == grade_name]
    scope_title = f"Grade Level: {grade_name}"

# --- PREPARE DATASETS FOR CONTENT & PLATFORM ---
st.subheader(f"🏫 **{selected_school}** — *{scope_title}*")

tab_content, tab_platform = st.tabs(["📚 Student-Wise Content Usage", "🌐 Student-Wise Platform Usage"])

with tab_content:
    st.markdown("#### **Content Usage Report (Books & Chapters Opened)**")
    
    content_sub = scope_df[scope_df['Type'].str.casefold().isin(['book', 'library'])].copy()
    
    if content_sub.empty:
        st.info("No content usage records found for this selection.")
    else:
        # Aggregate by Student, Grade, and Book/Subject (only including time > 0)
        content_agg = content_sub.groupby(['Grade', 'FullName', 'Subject', 'Book']).agg(
            Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)
        ).reset_index()

        content_agg = content_agg[content_agg['Total_Seconds'] > 0].sort_values(by=['Grade', 'FullName'])

        c_rows = []
        for idx, row in content_agg.iterrows():
            hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
            book_name = row['Book'] if pd.notna(row['Book']) and str(row['Book']).strip() != '' else row['Subject']
            c_rows.append({
                'STUDENT NAME': row['FullName'],
                'GRADE': row['Grade'],
                'BOOK / CHAPTER': book_name,
                'HOURS': hrs,
                'MINUTES': mins,
                'SECONDS': secs
            })

        content_report_df = pd.DataFrame(c_rows)
        st.dataframe(content_report_df, use_container_width=True, hide_index=True)

with tab_platform:
    st.markdown("#### **Platform Usage Report (Features & Modules)**")
    
    # Filter for platform features
    platform_sub = scope_df[~scope_df['Type'].str.casefold().isin(['book'])].copy()
    
    if platform_sub.empty:
        st.info("No platform usage records found for this selection.")
    else:
        # Aggregate by Student, Grade, and Type/Feature
        platform_agg = platform_sub.groupby(['Grade', 'FullName', 'Type']).agg(
            Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)
        ).reset_index()

        platform_agg = platform_agg[platform_agg['Total_Seconds'] > 0].sort_values(by=['Grade', 'FullName'])

        p_rows = []
        for idx, row in platform_agg.iterrows():
            hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
            feature_name = str(row['Type']).capitalize()
            p_rows.append({
                'STUDENT NAME': row['FullName'],
                'GRADE': row['Grade'],
                'FEATURE / MODULE': feature_name,
                'HOURS': hrs,
                'MINUTES': mins,
                'SECONDS': secs
            })

        platform_report_df = pd.DataFrame(p_rows)
        st.dataframe(platform_report_df, use_container_width=True, hide_index=True)

# --- MASTER DOWNLOAD / PRINT BUTTON ---
st.markdown("---")
st.subheader("📥 Export Official PDF Report")

col_dl1, col_dl2 = st.columns(2)
with col_dl1:
    c_df_exp = content_report_df if 'content_report_df' in locals() else pd.DataFrame()
    p_df_exp = platform_report_df if 'platform_report_df' in locals() else pd.DataFrame()
    
    pdf_buffer = generate_comprehensive_student_pdf(
        school_name=selected_school,
        scope_title=scope_title,
        filter_desc="Active Academic Term",
        content_report_df=c_df_exp,
        platform_report_df=p_df_exp
    )
    st.download_button(
        label=f"📄 Download Complete Report ({selected_school} - {scope_title}) [PDF]",
        data=pdf_buffer,
        file_name=f"Student_Usage_Report_{selected_school.replace(' ', '_')}.pdf",
        mime="application/pdf"
    )
