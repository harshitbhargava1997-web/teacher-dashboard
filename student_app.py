import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO
from supabase import create_client

# ReportLab PDF Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration
st.set_page_config(page_title="OneLearn - Student Usage Reports", page_icon="📊", layout="wide")

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


# --- PDF REPORT GENERATOR ---
def generate_student_portal_pdf(title_text, subtitle_text, table_df):
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

    story.append(Paragraph(f"<b>{title_text}</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(subtitle_style, subtitle_style) if isinstance(subtitle_style, str) else Paragraph(f"<b>Details:</b> {subtitle_text}", subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

    if table_df is not None and not table_df.empty:
        raw_data = [table_df.columns.tolist()] + table_df.astype(str).values.tolist()
        table = Table(raw_data, colWidths=[40, 70, 230, 65, 65, 70])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.4, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer


# --- MAIN APP ---
st.title("📊 OneLearn - Student Usage Reports")
st.markdown("Student-wise content and platform utilization tracking matching official project reporting standards.")

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
selected_grade = st.sidebar.selectbox("Select Grade", options=all_grades)
grade_df = school_df[school_df['Grade'] == selected_grade]

all_students = sorted([str(s) for s in grade_df['FullName'].unique() if str(s).strip() and str(s).lower() != 'unknown student'])
if not all_students:
    st.warning("No students found for this selection.")
    st.stop()

selected_student = st.sidebar.selectbox("Select Student-wise Report", options=all_students)
student_df = grade_df[grade_df['FullName'] == selected_student]

# --- TOP HEADER UI (MATCHING PORTAL SCREENSHOT) ---
st.markdown(f"### **{selected_student.upper()}**")
st.caption(f"Usage Reports • Grade: {selected_grade} • School: {selected_school}")

# --- TABS: CONTENT VS PLATFORM ---
tab_content, tab_platform = st.tabs(["📚 Content", "🌐 Platform"])

with tab_content:
    st.markdown("#### **Content Usage (Book-wise)**")
    
    # Filter for book / content interactions
    content_sub = student_df[student_df['Type'].str.casefold().isin(['book', 'library'])].copy()
    
    if content_sub.empty:
        st.info("No content usage logs recorded for this student.")
    else:
        # Group by Book and calculate total seconds
        content_agg = content_sub.groupby(['Grade', 'Book']).agg(
            Total_Seconds=('Duration_Min', lambda x: x.sum() * 60)
        ).reset_index()

        table_rows = []
        for idx, row in content_agg.iterrows():
            hrs, mins, secs = convert_seconds_to_hms(row['Total_Seconds'])
            book_name = row['Book'] if pd.notna(row['Book']) and str(row['Book']).strip() != '' else 'General Reading'
            table_rows.append({
                'S.NO': idx + 1,
                'GRADE': row['Grade'],
                'BOOK': book_name,
                'HOURS': hrs,
                'MINUTES': mins,
                'SECONDS': secs
            })

        content_display_df = pd.DataFrame(table_rows)
        st.dataframe(content_display_df, use_container_width=True, hide_index=True)

        # Print / Export Button
        pdf_c = generate_student_portal_pdf(
            title_text=f"Content Usage Report: {selected_student.upper()}",
            subtitle_text=f"School: {selected_school} | Grade: {selected_grade}",
            table_df=content_display_df[['S.NO', 'GRADE', 'BOOK', 'HOURS', 'MINUTES', 'SECONDS']]
        )
        st.download_button(
            label="🖨️ Print Report (PDF)",
            data=pdf_c,
            file_name=f"Content_Report_{selected_student.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )

with tab_platform:
    st.markdown(f"#### **{selected_grade} - Section A**")
    st.markdown(f"##### **{selected_student.upper()}**")

    standard_features = [
        "Gradebook", "Messages", "Timetable", "Assessment", "Assignments", 
        "Attendance", "Doubts", "Library", "Notebook", "Notifications", "Publish Content"
    ]

    # Map database types to standard portal features
    platform_agg = student_df.groupby('Type')['Duration_Min'].sum().apply(lambda x: x * 60).to_dict()

    platform_rows = []
    for idx, feat in enumerate(standard_features, 1):
        # Match type key
        match_key = feat.lower()
        total_secs = 0.0
        for k, v in platform_agg.items():
            if match_key in str(k).lower():
                total_secs += v

        hrs, mins, secs = convert_seconds_to_hms(total_secs)
        platform_rows.append({
            'S.NO': idx,
            'FEATURES': feat,
            'HOURS': hrs,
            'MINUTES': mins,
            'SECONDS': secs
        })

    platform_display_df = pd.DataFrame(platform_rows)
    st.dataframe(platform_display_df, use_container_width=True, hide_index=True)

    # Print / Export Button
    pdf_p = generate_student_portal_pdf(
        title_text=f"Platform Usage Report: {selected_student.upper()}",
        subtitle_text=f"School: {selected_school} | Grade: {selected_grade} - Section A",
        table_df=platform_display_df
    )
    st.download_button(
        label="🖨️ Print Report (PDF)",
        data=pdf_p,
        file_name=f"Platform_Report_{selected_student.replace(' ', '_')}.pdf",
        mime="application/pdf"
    )
