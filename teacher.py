import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from supabase import create_client

# Page configuration for mobile-friendly teacher submission
st.set_page_config(page_title="Teacher Daily Submission Portal", page_icon="📝", layout="centered")

# --- SUPABASE CLOUD STORAGE SETUP ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    BUCKET_NAME = st.secrets["supabase"]["bucket_name"]
    PARQUET_FILE_NAME = "master_database.parquet"

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error("⚠️ Cloud connection configuration is missing. Please check app secrets.")

def fetch_master_db_from_supabase():
    """Fetches the existing master parquet file from Supabase storage."""
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            return pd.read_parquet(BytesIO(response))
    except Exception:
        pass
    return pd.DataFrame()

def append_teacher_submission(new_df):
    """Downloads current master DB, appends new teacher submission, deduplicates, and saves back to Supabase."""
    master_df = fetch_master_db_from_supabase()

    if not master_df.empty:
        all_data = pd.concat([master_df, new_df], ignore_index=True)
    else:
        all_data = new_df

    # Deduplicate based on unique session signature
    dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution']
    available_dedup_cols = [c for c in dedup_cols if c in all_data.columns]
    master_df = all_data.drop_duplicates(subset=available_dedup_cols, keep='first')

    # Convert to Parquet buffer and upload to Supabase bucket
    parquet_buffer = BytesIO()
    master_df.to_parquet(parquet_buffer, index=False)
    parquet_buffer.seek(0)

    supabase.storage.from_(BUCKET_NAME).upload(
        path=PARQUET_FILE_NAME,
        file=parquet_buffer.getvalue(),
        file_options={"upsert": "true", "content-type": "application/octet-stream"}
    )

# --- UI FOR TEACHERS ---
st.title("📝 Teacher Daily Submission Portal")
st.markdown("Please fill out this form daily to log your session details and qualitative evidence links. Your submission will instantly update the central admin review records.")

# Pre-defined list of schools (or you can fetch/hardcode them based on your portfolio)
school_options = [
    "Select School", 
    "School Alpha", 
    "School Beta", 
    "School Gamma"
]

with st.form("standalone_teacher_form", clear_on_submit=True):
    st.subheader("1. Teacher Details")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sub_school = st.selectbox("Select Your School / Institution", options=school_options)
        sub_firstname = st.text_input("First Name *")
    with col_s2:
        sub_date = st.date_input("Submission Date *")
        sub_lastname = st.text_input("Last Name *")

    st.subheader("2. Session & Activity Metrics")
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        sub_type = st.selectbox("Activity Type", options=["lessonDelivery", "library", "other"])
    with col_m2:
        sub_grade = st.text_input("Grade Level (e.g., Grade 5)")
    with col_m3:
        sub_subject = st.text_input("Subject (e.g., Mathematics)")

    col_m4, col_m5 = st.columns(2)
    with col_m4:
        sub_book = st.text_input("Book / Chapter Module Name")
    with col_m5:
        sub_duration_mins = st.number_input("Duration (Minutes)", min_value=0.0, max_value=300.0, value=30.0, step=5.0)

    st.subheader("3. Qualitative Evidences & Artifact Links")
    sub_voice = st.text_input("Voice Note Link (Pre-class reflection audio URL)")
    
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        sub_vid1 = st.text_input("Classroom Video Evidence Link 1")
        sub_vid2 = st.text_input("Classroom Video Evidence Link 2")
    with col_v2:
        sub_vid3 = st.text_input("Classroom Video Evidence Link 3")
        sub_writing = st.text_input("Student Writing Sample Artifact Link")

    submitted = st.form_submit_button("🚀 Submit Daily Log & Evidence")

    if submitted:
        if sub_school == "Select School":
            st.error("Please select your valid school/institution.")
        elif not sub_firstname.strip() or not sub_lastname.strip():
            st.error("Please provide both your First and Last Name.")
        else:
            try:
                # Format entry data matching your main dashboard schema
                new_entry = pd.DataFrame([{
                    'FirstName': sub_firstname.strip(),
                    'LastName': sub_lastname.strip(),
                    'FullName': f"{sub_firstname.strip()} {sub_lastname.strip()}",
                    'Institution': sub_school,
                    'Grade': sub_grade.strip(),
                    'Subject': sub_subject.strip(),
                    'Book': sub_book.strip(),
                    'Type': sub_type,
                    'Duration_Min': sub_duration_mins,
                    'Duration (HH:MM:SS)': f"00:{int(sub_duration_mins):02d}:00",
                    'StartTime': pd.to_datetime(sub_date),
                    'Voice_Note_Link': sub_voice.strip() if sub_voice else None,
                    'Video_Evidence_1': sub_vid1.strip() if sub_vid1 else None,
                    'Video_Evidence_2': sub_vid2.strip() if sub_vid2 else None,
                    'Video_Evidence_3': sub_vid3.strip() if sub_vid3 else None,
                    'Writing_Sample_Link': sub_writing.strip() if sub_writing else None,
                    'Assessment_Score_Pct': None
                }])

                append_teacher_submission(new_entry)
                st.success(f"✅ Thank you, {sub_firstname}! Your log has been successfully submitted and synced to the central system.")
            except Exception as e:
                st.error(f"❌ Submission failed: {e}")
