import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from supabase import create_client

# Page configuration (Must be the first Streamlit command)
st.set_page_config(page_title="Teacher Daily Submission Portal", page_icon="📝", layout="centered")

# --- SUPABASE CLOUD STORAGE SETUP ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    BUCKET_NAME = st.secrets["supabase"]["bucket_name"]
    PARQUET_FILE_NAME = "master_database.parquet"

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error("⚠️ Cloud connection configuration is missing. Please check your Streamlit Cloud Secrets settings.")

@st.cache_data(show_spinner=False)
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
    dedup_cols = ['FullName', 'StartTime', 'Book', 'Institution']
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
    # Clear cache so fresh data reloads if needed
    fetch_master_db_from_supabase.clear()

# --- LOAD DATABASE FOR DYNAMIC MAPPING ---
master_df = fetch_master_db_from_supabase()

# Extract dynamic school list and school-to-teacher mapping
if not master_df.empty and 'Institution' in master_df.columns and 'FullName' in master_df.columns:
    school_options = sorted(master_df['Institution'].dropna().unique().tolist())
else:
    school_options = []

# --- UI FOR TEACHERS ---
st.title("📝 Teacher Daily Submission Portal")
st.markdown("Please select your school and name from the roster, log your lesson details, and upload/link your qualitative evidences directly.")

with st.form("standalone_teacher_form", clear_on_submit=True):
    st.subheader("1. School & Teacher Roster Selection")
    
    if not school_options:
        st.warning("⚠️ No school data found in the central database yet. Please ensure your admin database has initial roster data loaded.")
        sub_school = st.selectbox("Select School / Institution", options=["No Schools Available"])
        sub_teacher_name = st.selectbox("Select Your Name", options=["No Teachers Available"])
    else:
        sub_school = st.selectbox("Select School / Institution *", options=["-- Select School --"] + school_options)
        
        # Filter teachers dynamically based on the selected school
        if sub_school != "-- Select School --":
            filtered_teachers = sorted(master_df[master_df['Institution'] == sub_school]['FullName'].dropna().unique().tolist())
        else:
            filtered_teachers = []
            
        sub_teacher_name = st.selectbox(
            "Select Your Name *", 
            options=["-- Select Your Name --"] + filtered_teachers,
            help="Your name is fetched automatically from the registered school roster."
        )

    sub_date = st.date_input("Submission Date *")

    st.subheader("2. Academic Lesson Details")
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        sub_grade = st.text_input("Grade (e.g., Grade 5) *")
    with col_a2:
        sub_subject = st.text_input("Subject *")
    with col_a3:
        sub_chapter = st.text_input("Chapter Number *")

    sub_lesson_title = st.text_input("Lesson Plan Topic / Title being Taught *")

    st.subheader("3. Qualitative Evidences & Artifact Hub")
    st.markdown("Provide cloud links or reference URLs for your lesson plan and classroom evidence files:")
    
    sub_voice = st.text_input("🎤 Lesson Plan Voice Note Link")
    sub_pic = st.text_input("🖼️ Lesson Plan Picture Link")
    
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        sub_vid1 = st.text_input("🎥 Classroom Activity Video Evidence 1")
        sub_vid2 = st.text_input("🎥 Classroom Activity Video Evidence 2")
    with col_v2:
        sub_vid3 = st.text_input("🎥 Classroom Activity Video Evidence 3")
        sub_writing = st.text_input("📝 Student Writing Samples Link")

    submitted = st.form_submit_button("🚀 Submit Evidence & Lesson Log")

    if submitted:
        if sub_school == "-- Select School --":
            st.error("Please select a valid School Name.")
        elif sub_teacher_name == "-- Select Your Name --":
            st.error("Please select your name from the roster.")
        elif not sub_lesson_title.strip() or not sub_chapter.strip():
            st.error("Please fill in the Lesson Plan title and Chapter Number.")
        else:
            try:
                # Split FullName back into First and Last name for database compatibility
                name_parts = sub_teacher_name.split(" ", 1)
                f_name = name_parts[0]
                l_name = name_parts[1] if len(name_parts) > 1 else ""

                new_entry = pd.DataFrame([{
                    'FirstName': f_name,
                    'LastName': l_name,
                    'FullName': sub_teacher_name,
                    'Institution': sub_school,
                    'Grade': sub_grade.strip(),
                    'Subject': sub_subject.strip(),
                    'Book': f"Ch. {sub_chapter.strip()}: {sub_lesson_title.strip()}",
                    'Type': 'lessonDelivery',  # Default type assigned for artifact logs
                    'Duration_Min': 0.0,       # Duration field omitted per request
                    'Duration (HH:MM:SS)': "00:00:00",
                    'StartTime': pd.to_datetime(sub_date),
                    'Voice_Note_Link': sub_voice.strip() if sub_voice else None,
                    'Lesson_Plan_Picture': sub_pic.strip() if sub_pic else None,
                    'Video_Evidence_1': sub_vid1.strip() if sub_vid1 else None,
                    'Video_Evidence_2': sub_vid2.strip() if sub_vid2 else None,
                    'Video_Evidence_3': sub_vid3.strip() if sub_vid3 else None,
                    'Writing_Sample_Link': sub_writing.strip() if sub_writing else None,
                    'Assessment_Score_Pct': None
                }])

                append_teacher_submission(new_entry)
                st.success(f"✅ Success! Evidence and lesson log for {sub_teacher_name} ({sub_school}) have been successfully submitted and synced to the central admin dashboard.")
            except Exception as e:
                st.error(f"❌ Submission error: {e}")
