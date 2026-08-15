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
    """Append a teacher evidence submission to the shared master parquet without changing the schema."""
    master_df = fetch_master_db_from_supabase()

    expected_cols = [
        'Institution', 'Center', 'FirstName', 'LastName', 'Role', 'Type',
        'Grade', 'Subject', 'Book', 'StartTime', 'EndTime',
        'Duration (Minutes)', 'Duration (HH:MM:SS)', 'FullName', 'Duration_Min',
        'Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1',
        'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 'Assessment_Score_Pct'
    ]

    new_df = new_df.copy()
    for col in expected_cols:
        if col not in new_df.columns:
            new_df[col] = None
        if not master_df.empty and col not in master_df.columns:
            master_df[col] = None

    all_data = pd.concat([master_df, new_df], ignore_index=True) if not master_df.empty else new_df
    all_data = normalize_identity_columns(all_data)

    # Keep lesson/book in the signature so separate submissions for the same teacher/date survive.
    dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution']
    available_dedup_cols = [c for c in dedup_cols if c in all_data.columns]
    all_data = all_data.drop_duplicates(subset=available_dedup_cols, keep='last')

    parquet_buffer = BytesIO()
    all_data.to_parquet(parquet_buffer, index=False)
    parquet_buffer.seek(0)

    supabase.storage.from_(BUCKET_NAME).upload(
        path=PARQUET_FILE_NAME,
        file=parquet_buffer.getvalue(),
        file_options={"upsert": "true", "content-type": "application/octet-stream"}
    )
    fetch_master_db_from_supabase.clear()
    return True

# --- SHARED MASTER ROSTER LOADER ---
master_df = fetch_master_db_from_supabase()
if not master_df.empty:
    master_df = normalize_identity_columns(master_df)

teacher_roster = build_teacher_roster(master_df)

school_options = []
if not teacher_roster.empty:
    school_options = sorted(teacher_roster['Institution'].dropna().unique().tolist())

# --- UI FOR TEACHERS ---
st.title("📝 Teacher Daily Evidence Portal")
st.markdown("Select your school and name from the roster, fill out your lesson details, and upload your qualitative evidence files directly.")

with st.form("standalone_teacher_form", clear_on_submit=True):
    st.subheader("1. School & Teacher Roster Selection")
    
    if not school_options:
        st.warning("⚠️ No schools detected in Supabase database. Please check your admin uploads.")
        school_options = ["No Schools Found"]

    sub_school = st.selectbox("Select School / Institution *", options=["-- Select School --"] + school_options)
    
    # --- AUTHORITATIVE SCHOOL -> TEACHER ROSTER FILTERING ---
    filtered_teachers = []
    if sub_school != "-- Select School --" and not teacher_roster.empty:
        school_subset = teacher_roster[
            teacher_roster['_institution_key'].eq(_norm_key(sub_school))
        ]
        filtered_teachers = sorted(
            school_subset['FullName'].dropna().astype(str).map(_norm_text).unique().tolist()
        )

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
        elif not filtered_teachers:
            st.error("No teacher roster is available for the selected school. Please ask the admin to verify the master UserMetrics roster.")
        else:
            try:
                selected_roster = teacher_roster[
                    teacher_roster['_institution_key'].eq(_norm_key(sub_school))
                    & teacher_roster['_teacher_key'].eq(_norm_key(sub_teacher_name))
                ]
                if selected_roster.empty:
                    raise ValueError("Selected teacher could not be matched to the school roster.")
                selected_teacher = selected_roster.iloc[0]

                with st.spinner("Uploading files securely to cloud storage..."):
                    voice_url = upload_file_to_supabase(uploaded_voice, "voice_notes")
                    pic_url = upload_file_to_supabase(uploaded_pic, "pictures")
                    vid1_url = upload_file_to_supabase(uploaded_vid1, "videos")
                    vid2_url = upload_file_to_supabase(uploaded_vid2, "videos")
                    vid3_url = upload_file_to_supabase(uploaded_vid3, "videos")
                    writing_url = upload_file_to_supabase(uploaded_writing, "writing_samples")

                f_name = _norm_text(selected_teacher.get('FirstName', ''))
                l_name = _norm_text(selected_teacher.get('LastName', ''))
                canonical_name = _norm_text(selected_teacher.get('FullName', sub_teacher_name))
                canonical_school = _norm_text(selected_teacher.get('Institution', sub_school))
                canonical_center = _norm_text(selected_teacher.get('Center', canonical_school)) or canonical_school

                new_entry = pd.DataFrame([{
                    'Institution': canonical_school,
                    'Center': canonical_center,
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
                    'FullName': canonical_name,
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
                fetch_master_db_from_supabase.clear()
                st.success(f"✅ Success! Evidence and lesson log for {canonical_name} ({canonical_school}) have been successfully uploaded and synced to the admin dashboard.")
            except Exception as e:
                st.error(f"❌ Upload and submission error: {e}")

# --- DIAGNOSTIC FOOTER ---
with st.expander("🛠️ Real-Time Roster Debugger"):
    if not master_df.empty:
        st.write(f"Total rows in cloud DB: {len(master_df)}")
        st.write("Unique Schools:", master_df['Institution'].unique().tolist() if 'Institution' in master_df.columns else "No Institution")
        st.write("Roster Teachers:", teacher_roster['FullName'].unique().tolist() if not teacher_roster.empty else "No teachers found")
    else:
        st.write("Cloud database is currently empty.")
