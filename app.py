import streamlit as st
import pandas as pd
import os
from datetime import datetime

# Page Configuration
st.set_page_config(page_title="Teacher Daily Submission & Evidence Portal", layout="centered")

st.title("📝 Teacher Daily Submission & Evidence Portal")
st.markdown("Please select your school and name, fill out your session details, and directly upload your daily evidences and result snapshots.")

# Local Storage Folders
DATA_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "dashboard_data_store")
MEDIA_FOLDER = os.path.join(DATA_FOLDER, "uploaded_media")

try:
    os.makedirs(MEDIA_FOLDER, exist_ok=True)
except Exception:
    DATA_FOLDER = "dashboard_data_store"
    MEDIA_FOLDER = os.path.join(DATA_FOLDER, "uploaded_media")
    os.makedirs(MEDIA_FOLDER, exist_ok=True)

FORM_DB_PATH = os.path.join(DATA_FOLDER, "teacher_form_submissions.xlsx")

# --- SCHOOL & TEACHER DIRECTORY MAPPING ---
# Customize this dictionary with your actual schools and their respective teachers
SCHOOL_TEACHER_ROSTER = {
    "Delhi Public School Bhopal": ["Aarav Sharma", "Priya Verma", "Rohan Gupta", "Ananya Singh"],
    "St. Xavier's High School": ["Manish Kumar", "Neha Patel", "Amit Joshi", "Pooja Sharma"],
    "Campion School Bhopal": ["Rajeshwari Iyer", "Vikram Malhotra", "Sunita Nair", "Deepak Rao"],
    "Bhopal Convent School": ["Sanjay Roy", "Meenakshi Das", "Karan Kapoor", "Divya Sen"],
    "Global Tech School": ["Harshit Bhargav", "Alok Mishra", "Swati Tiwari", "Nikhil Jain"]
}

# Form Input Fields
with st.form("teacher_submission_form", clear_on_submit=True):
    st.subheader("🏫 1. School & Teacher Details")
    
    school_options = ["Select School..."] + list(SCHOOL_TEACHER_ROSTER.keys())
    school_name = st.selectbox("Select Your School / Institution *", options=school_options)
    
    # Dynamic Teacher Selection based on School choice
    if school_name != "Select School...":
        available_teachers = ["Select Teacher..."] + SCHOOL_TEACHER_ROSTER[school_name]
        teacher_name = st.selectbox("Select Your Name *", options=available_teachers)
    else:
        teacher_name = st.selectbox("Select Your Name *", options=["Please select a school first..."])

    st.markdown("---")
    st.subheader("📚 2. Class & Academic Details")
    
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        grade = st.selectbox(
            "Grade / Class *", 
            options=["Nursery", "LKG", "UKG", "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5"]
        )
    with col_g2:
        subject = st.selectbox(
            "Subject *", 
            options=[
                "Maths / Numeracy", 
                "EVS", 
                "Literacy / English", 
                "Hindi", 
                "Science", 
                "Social Science", 
                "Computer", 
                "GK", 
                "Grammar", 
                "Pre-Primary Play Activities", 
                "Pre-Primary Art & Craft Activities"
            ]
        )

    col_l1, col_l2 = st.columns(2)
    with col_l1:
        lesson_plan_number = st.text_input("Lesson Plan Number / Code * (e.g., LP-04, Ch-2)")
    with col_l2:
        num_activities = st.number_input("Number of Classroom Activities Conducted *", min_value=0, max_value=20, value=1, step=1)

    st.markdown("---")
    st.subheader("📁 3. Direct Qualitative Evidence Uploads")
    st.caption("Upload your files directly from your device (Audio recordings, Activity videos, Writing samples).")

    voice_note_file = st.file_uploader("🎧 Upload Voice Note / Audio Recording (.mp3, .wav, .m4a)", type=["mp3", "wav", "m4a"], key="voice_upload")
    video_file = st.file_uploader("🎥 Upload Classroom Activity Video (.mp4, .mov, .avi)", type=["mp4", "mov", "avi"], key="video_upload")
    writing_file = st.file_uploader("📝 Upload Student Writing Sample / Worksheet (.pdf, .jpg, .png)", type=["pdf", "jpg", "png"], key="writing_upload")

    st.markdown("---")
    st.subheader("📸 4. Assessment & Result Picture Upload")
    st.caption("Upload snapshots of evaluation result sheets, mark registers, or student performance scorecards.")

    result_image = st.file_uploader("📊 Upload Assessment / Result Snapshot (.png, .jpg, .jpeg)", type=["png", "jpg", "jpeg"], key="result_upload")
    assessment_score = st.number_input("Overall Calculated Assessment Score (%) - Optional", min_value=0.0, max_value=100.0, value=0.0, step=1.0)

    submitted = st.form_submit_button("🚀 Submit Daily Report")

    if submitted:
        if school_name == "Select School...":
            st.error("⚠️ Please select your school.")
        elif teacher_name == "Select Teacher..." or teacher_name.startswith("Please"):
            st.error("⚠️ Please select your teacher name.")
        elif not lesson_plan_number.strip():
            st.error("⚠️ Please enter the Lesson Plan Number / Code.")
        else:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = teacher_name.strip().replace(" ", "_")

            # Direct File Handling & Saving Locally
            def save_uploaded_file(uploaded_file, prefix):
                if uploaded_file is not None:
                    ext = uploaded_file.name.split('.')[-1]
                    filename = f"{safe_name}_{prefix}_{timestamp_str}.{ext}"
                    path = os.path.join(MEDIA_FOLDER, filename)
                    with open(path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    return path
                return None

            voice_path = save_uploaded_file(voice_note_file, "voice")
            video_path = save_uploaded_file(video_file, "video")
            writing_path = save_uploaded_file(writing_file, "writing")
            result_path = save_uploaded_file(result_image, "result")

            # Package submission record (mapped to main dashboard schema)
            new_entry = {
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Institution": school_name,
                "FullName": teacher_name.strip(),
                "Grade": grade,
                "Subject": subject,
                "Duration_Min": 10.0,  # Standard default metric time placeholder
                "Lesson_Plan_Num": lesson_plan_number.strip(),
                "Num_Activities": num_activities,
                "Type": "teacherSubmission",
                "Voice_Note_Link": voice_path,
                "Video_Evidence_1": video_path,
                "Writing_Sample_Link": writing_path,
                "Assessment_Score_Pct": assessment_score if assessment_score > 0 else None,
                "Assessment_Image_Path": result_path,
                "Portfolio_Image_Path": result_path
            }

            # Append to local form storage file
            df_new = pd.DataFrame([new_entry])
            if os.path.exists(FORM_DB_PATH):
                try:
                    df_existing = pd.read_excel(FORM_DB_PATH)
                    df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                except Exception:
                    df_combined = df_new
            else:
                df_combined = df_new

            df_combined.to_excel(FORM_DB_PATH, index=False)
            st.success(f"✅ Thank you, {teacher_name}! Your submission and direct uploads have been recorded successfully.")
            st.balloons()

# --- ADMIN EXPORT PANEL ---
st.markdown("---")
with st.expander("🔐 Academic Manager Export Panel"):
    st.caption("Download all collected submissions to upload directly into your main dashboard.")
    if os.path.exists(FORM_DB_PATH):
        try:
            admin_df = pd.read_excel(FORM_DB_PATH)
            st.write(f"Total Submissions Collected: {len(admin_df)}")
            
            csv_data = admin_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download All Submissions (CSV)",
                data=csv_data,
                file_name=f"Teacher_Submissions_Export_{datetime.now().strftime('%Y-%m-%d')}.csv",
                mime="application/csv"
            )
        except Exception as e:
            st.error(f"Error loading export file: {e}")
    else:
        st.info("No submissions recorded yet.")
