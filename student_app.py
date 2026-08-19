import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO
from supabase import create_client

# ReportLab PDF Libraries for Individual-Student-Per-Page Export
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration
st.set_page_config(page_title="OneLearn - Individual Student Reports", page_icon="📊", layout="wide")

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


# --- MULTI-PAGE INDIVIDUAL STUDENT PDF GENERATOR ---
def generate_grade_individual_student_pdf(school_name, grade_name, filter_desc, student_data_dict):
    """Generates a PDF where each student has their own dedicated page containing Content and Platform usage."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor('#2563EB')
    dark_neutral = colors.HexColor('#1E293B')
    light_bg = colors.HexColor('#F8FAFC')
    border_color = colors.HexColor('#E2E8F0')

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=14, textColor=primary_color, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9, textColor=dark_neutral)
    student_head = ParagraphStyle('StudentHead', parent=styles['Heading2'], fontSize=13, textColor=colors.HexColor('#0F172A'), fontName='Helvetica-Bold', spaceBefore=4, spaceAfter=8)
    sec_head_style = ParagraphStyle('SecHead', parent=styles['Heading3'], fontSize=10, textColor=primary_color, fontName='Helvetica-Bold', spaceBefore=8, spaceAfter=4)

    for idx, (student_name, data) in enumerate(student_data_dict.items()):
        if idx > 0:
            story.append(PageBreak())

        story.append(Paragraph(f"<b>Student Usage Report</b>", title_style))
        story.append(Spacer(1, 2))
        story.append(Paragraph(f"<b>Institution:</b> {school_name} | <b>Grade:</b> {grade_name} | <b>Period:</b> {filter_desc}", subtitle_style))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="100%", thickness=1, color=primary_color, spaceAfter=8))

        story.append(Paragraph(f"<b>{student_name.upper()}</b>", student_head))

        # 1. Content Usage Table
        story.append(Paragraph("<b>1. Content Usage (Books & Chapters Opened)</b>", sec_head_style))
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
            story.append(Paragraph("No content usage records for this period.", styles['Normal']))

        story.append(Spacer(1, 8))

        # 2. Platform Usage Table
        story.append(Paragraph("<b>2. Platform Usage (Features & Modules)</b>", sec_head_style))
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
            story.append(Paragraph("No platform usage records for this period.", styles['Normal']))

    doc.build(story)
    buffer.seek(0)
    return buffer


# --- MAIN APP ---
st.title("📊 OneLearn - Individual Student Reports by Grade")
st.markdown("Select a school and grade level to generate multi-page executive reports where **each student has their own dedicated page** detailing content and platform usage.")

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

# --- PREPARE STUDENT-WISE DICTIONARY ---
all_students_in_grade = sorted([s for s in grade_df['FullName'].unique() if s and str(s).lower() != 'unknown student'])

if not all_students_in_grade:
    st.warning("No valid student names found in this grade.")
    st.stop()

st.subheader(f"🏫 **{selected_school}** — Grade: **{selected_grade}** ({len(all_students_in_grade)} Students)")

student_data_dict = {}

for student_name in all_students_in_grade:
    st.markdown(f"### 👤 **{student_name}**")
    s_df = grade_df[grade_df['FullName'] == student_name]

    tab_c, tab_p = st.tabs(["📚 Content Usage", "🌐 Platform Usage"])

    with tab_c:
        content_sub = s_df[s_df['Type'].str.casefold().isin(['book', 'library'])].copy()
        if content_sub.empty:
            st.info("No content usage recorded.")
            c_df = pd.DataFrame()
        else:
            c_agg = content_sub.groupby(['Subject', 'Book']).agg(Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)).reset_index()
            c_agg = c_agg[c_agg['Total_Seconds'] > 0].sort_values(by='Total_Seconds', ascending=False)
            
            c_rows = []
            for idx, row in c_agg.iterrows():
                hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
                c_rows.append({
                    'S.NO': idx + 1,
                    'SUBJECT': row['Subject'],
                    'BOOK / CHAPTER': row['Book'] if pd.notna(row['Book']) and str(row['Book']).strip() != '' else 'General Reading',
                    'HOURS': hrs,
                    'MINUTES': mins,
                    'SECONDS': secs
                })
            c_df = pd.DataFrame(c_rows)
            st.dataframe(c_df, use_container_width=True, hide_index=True)

    with tab_p:
        platform_sub = s_df[~s_df['Type'].str.casefold().isin(['book'])].copy()
        if platform_sub.empty:
            st.info("No platform usage recorded.")
            p_df = pd.DataFrame()
        else:
            p_agg = platform_sub.groupby('Type').agg(Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)).reset_index()
            p_agg = p_agg[p_agg['Total_Seconds'] > 0].sort_values(by='Total_Seconds', ascending=False)

            p_rows = []
            for idx, row in p_agg.iterrows():
                hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
                p_rows.append({
                    'S.NO': idx + 1,
                    'FEATURE / MODULE': str(row['Type']).capitalize(),
                    'HOURS': hrs,
                    'MINUTES': mins,
                    'SECONDS': secs
                })
            p_df = pd.DataFrame(p_rows)
            st.dataframe(p_df, use_container_width=True, hide_index=True)

    student_data_dict[student_name] = {
        'content': c_df if 'c_df' in locals() else pd.DataFrame(),
        'platform': p_df if 'p_df' in locals() else pd.DataFrame()
    }

# --- MASTER PDF DOWNLOAD FOR GRADE ---
st.markdown("---")
st.subheader(f"📥 Download Multi-Page PDF Report for {selected_grade}")

pdf_buffer = generate_grade_individual_student_pdf(
    school_name=selected_school,
    grade_name=selected_grade,
    filter_desc="Active Academic Term",
    student_data_dict=student_data_dict
)

st.download_button(
    label=f"📄 Download All Student Reports for {selected_grade} [PDF]",
    data=pdf_buffer,
    file_name=f"Student_Reports_{selected_school.replace(' ', '_')}_{selected_grade}.pdf",
    mime="application/pdf"
)
