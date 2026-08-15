import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO
from supabase import create_client

# Page configuration (Must be the first Streamlit command)
st.set_page_config(page_title="Teacher Daily Evidence Portal", page_icon="📝", layout="centered")

# --- SUPABASE CLOUD STORAGE SETUP ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    BUCKET_NAME = st.secrets["supabase"]["bucket_name"]
    PARQUET_FILE_NAME = "master_database.parquet"

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"⚠️ Cloud connection configuration is missing: {e}")

@st.cache_data(ttl=5, show_spinner=False)
def fetch_master_db_from_supabase():
    """Fetches the existing master parquet file from Supabase storage with instant sync TTL."""
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            df = pd.read_parquet(BytesIO(response))
            # Clean string columns immediately upon fetch with regex
            for col in df.select_dtypes(include=['object', 'string']).columns:
                df[col] = df[col].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
            return df
    except Exception:
        pass
    return pd.DataFrame()

def upload_file_to_supabase(uploaded_file, folder_name="teacher_uploads"):
    """Uploads a Streamlit uploaded file directly to Supabase storage and returns its public URL."""
    if uploaded_file is None:
        return None
    try:
        file_bytes = uploaded_file.getvalue()
        file_path = f"{folder_name}/{np.random.randint(10000, 99999)}_{uploaded_file.name}"
        
        supabase.storage.from_(BUCKET_NAME).upload(
            path=file_path,
            file=file_bytes,
            file_options={"upsert": "true", "content-type": uploaded_file.type}
        )
        public_url_response = supabase.storage.from_(BUCKET_NAME).get_public_url(file_path)
        return public_url_response
    except Exception:
        return f"supabase://{file_path}"

def append_teacher_submission(new_df):
    """Downloads current master DB, aligns schemas, appends new submission, and saves back to Supabase."""
    master_df = fetch_master_db_from_supabase()

    # Guarantee 22-column alignment exactly as expected by Admin Portal
    expected_cols = [
        'Institution', 'Center', 'FirstName', 'LastName', 'Role', 'Type', 
        'Grade', 'Subject', 'Book', 'StartTime', 'EndTime', 
        'Duration (Minutes)', 'Duration (HH:MM:SS)', 'FullName', 'Duration_Min',
        'Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 
        'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 'Assessment_Score_Pct'
    ]

    for col in expected_cols:
        if col not in new_df.columns:
            new_df[col] = None
        if not master_df.empty and col not in master_df.columns:
            master_df[col] = None

    if not master_df.empty:
        all_data = pd.concat([master_df, new_df], ignore_index=True)
    else:
        all_data = new_df

    # Normalize FullName and Institution across all data via Regex
    all_data['FirstName'] = all_data['FirstName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
    all_data['LastName'] = all_data['LastName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
    all_data['FullName'] = (all_data['FirstName'] + " " + all_data['LastName']).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
    all_data.loc[all_data['FullName'] == '', 'FullName'] = 'Unknown Teacher'
    all_data['Institution'] = all_data['Institution'].fillna('Unknown School').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())

    master_df = all_data.drop_duplicates(subset=['FullName', 'StartTime', 'Institution', 'Type'], keep='last')

    parquet_buffer = BytesIO()
    master_df.to_parquet(parquet_buffer, index=False)
    parquet_buffer.seek(0)

    supabase.storage.from_(BUCKET_NAME).upload(
        path=PARQUET_FILE_NAME,
        file=parquet_buffer.getvalue(),
        file_options={"upsert": "true", "content-type": "application/octet-stream"}
    )
    fetch_master_db_from_supabase.clear()

# --- BULLETPROOF DATABASE & ROSTER LOADER ---
master_df = fetch_master_db_from_supabase()

school_options = []
if not master_df.empty:
    if 'Institution' in master_df.columns:
        school_options = sorted([s for s in master_df['Institution'].unique() if s and s != 'nan' and s != 'Unknown School'])

# --- UI FOR TEACHERS ---
st.title("📝 Teacher Daily Evidence Portal")
st.markdown("Select your school and name from the roster, fill out your lesson details, and upload your qualitative evidence files directly.")

with st.form("standalone_teacher_form", clear_on_submit=True):
    st.subheader("1. School & Teacher Roster Selection")
    
    if not school_options:
        st.warning("⚠️ No schools detected in Supabase database. Please check your admin uploads.")
        school_options = ["No Schools Found"]

    sub_school = st.selectbox("Select School / Institution *", options=["-- Select School --"] + school_options)
    
    # --- ROBUST TEACHER FILTERING ---
    filtered_teachers = []
    if sub_school != "-- Select School --" and not master_df.empty:
        # Match school case-insensitively
        school_subset = master_df[master_df['Institution'].str.lower() == sub_school.lower()]
        
        if not school_subset.empty and 'FullName' in school_subset.columns:
            all_names = school_subset['FullName'].astype(str).unique().tolist()
            filtered_teachers = [n for n in all_names if n and n.lower() not in ['nan', 'unknown teacher', '', 'none']]

        filtered_teachers = sorted(list(set(filtered_teachers)))

    sub_teacher_name = st.selectbox(
        "Select Your Name *", 
        options=["-- Select Your Name --"] + filtered_teachers,
        help="Your name populates automatically based on the school selected."
    )

    sub_date = st.date_input("Submission Date *")

    st.subheader("2. Academic Lesson Details")
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        grade_options = ["Nursery", "LKG", "UKG", "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5"]
        sub_grade = st.selectbox("Select Grade *", options=grade_options)
    with col_a2:
        subject_options = ["Mathematics", "English", "Hindi", "Environmental Studies (EVS)", "Science", "General Knowledge"]
        sub_subject = st.selectbox("Select Subject *", options=subject_options)

    sub_lesson_num = st.text_input("Lesson Plan Number (e.g., Lesson 4) *")

    st.subheader("3. Direct Qualitative Evidence Uploads")
    st.markdown("Upload your evidence files directly from your device:")
    
    uploaded_voice = st.file_uploader("🎤 Upload Lesson Plan Voice Note (Audio)", type=["mp3", "wav", "m4a", "ogg"])
    uploaded_pic = st.file_uploader("🖼️ Upload Lesson Plan Picture", type=["png", "jpg", "jpeg"])
    
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        uploaded_vid1 = st.file_uploader("🎥 Classroom Activity Video 1", type=["mp4", "mov", "avi"])
        uploaded_vid2 = st.file_uploader("🎥 Classroom Activity Video 2", type=["mp4", "mov", "avi"])
    with col_v2:
        uploaded_vid3 = st.file_uploader("🎥 Classroom Activity Video 3", type=["mp4", "mov", "avi"])
        uploaded_writing = st.file_uploader("📝 Upload Student Writing Sample", type=["pdf", "png", "jpg", "jpeg"])

    submitted = st.form_submit_button("🚀 Upload Evidence & Submit Log")

    if submitted:
        if sub_school == "-- Select School --":
            st.error("Please select a valid School Name.")
        elif sub_teacher_name == "-- Select Your Name --":
            st.error("Please select your name from the roster.")
        elif not sub_lesson_num.strip():
            st.error("Please provide the Lesson Plan Number.")
        else:
            try:
                with st.spinner("Uploading files securely to cloud storage..."):
                    voice_url = upload_file_to_supabase(uploaded_voice, "voice_notes")
                    pic_url = upload_file_to_supabase(uploaded_pic, "pictures")
                    vid1_url = upload_file_to_supabase(uploaded_vid1, "videos")
                    vid2_url = upload_file_to_supabase(uploaded_vid2, "videos")
                    vid3_url = upload_file_to_supabase(uploaded_vid3, "videos")
                    writing_url = upload_file_to_supabase(uploaded_writing, "writing_samples")

                name_parts = sub_teacher_name.split(" ", 1)
                f_name = name_parts[0]
                l_name = name_parts[1] if len(name_parts) > 1 else ""

                new_entry = pd.DataFrame([{
                    'Institution': sub_school,
                    'Center': sub_school,
                    'FirstName': f_name,
                    'LastName': l_name,
                    'Role': 'teacher',
                    'Type': 'lessonDelivery',
                    'Grade': sub_grade,
                    'Subject': sub_subject,
                    'Book': f"Lesson Plan #{sub_lesson_num.strip()}",
                    'StartTime': pd.to_datetime(sub_date),
                    'EndTime': pd.to_datetime(sub_date),
                    'Duration (Minutes)': 0.0,
                    'Duration (HH:MM:SS)': "00:00:00",
                    'FullName': sub_teacher_name,
                    'Duration_Min': 0.0,
                    'Voice_Note_Link': str(voice_url) if voice_url else None,
                    'Lesson_Plan_Picture': str(pic_url) if pic_url else None,
                    'Video_Evidence_1': str(vid1_url) if vid1_url else None,
                    'Video_Evidence_2': str(vid2_url) if vid2_url else None,
                    'Video_Evidence_3': str(vid3_url) if vid3_url else None,
                    'Writing_Sample_Link': str(writing_url) if writing_url else None,
                    'Assessment_Score_Pct': None
                }])

                append_teacher_submission(new_entry)
                st.success(f"✅ Success! Evidence and lesson log for {sub_teacher_name} ({sub_school}) have been successfully uploaded and synced to the admin dashboard.")
            except Exception as e:
                st.error(f"❌ Upload and submission error: {e}")

# --- DIAGNOSTIC FOOTER ---
with st.expander("🛠️ Real-Time Roster Debugger"):
    if not master_df.empty:
        st.write(f"Total rows in cloud DB: {len(master_df)}")
        st.write("Unique Schools:", master_df['Institution'].unique().tolist() if 'Institution' in master_df.columns else "No Institution")
        st.write("Unique Roster Names:", master_df['FullName'].unique().tolist() if 'FullName' in master_df.columns else "No FullName")
    else:
        st.write("Cloud database is currently empty.")
