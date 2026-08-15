import streamlit as st
import pandas as pd
import os
from datetime import datetime

# Page Configuration
st.set_page_config(page_title="Teacher Daily Submission & Evidence Form", layout="centered")

st.title("📝 Teacher Daily Submission & Evidence Form")
st.markdown("Please fill out this form daily after completing your sessions to log your lesson preparation, class details, qualitative evidence links, and snapshot pictures.")

# Local Database Folder for Form Submissions
DATA_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "dashboard_data_store")
MEDIA_FOLDER = os.path.join(DATA_FOLDER, "uploaded_media")

try:
    os.makedirs(MEDIA_FOLDER, exist_ok=True)
except Exception:
    DATA_FOLDER = "dashboard_data_store"
    MEDIA_FOLDER = os.path.join(DATA_FOLDER, "uploaded_media")
    os.makedirs(MEDIA_FOLDER, exist_ok=True)

FORM_DB_PATH = os.path.join(DATA_FOLDER, "teacher_form_submissions.xlsx")

# Form Input Fields
with st.form("teacher_submission_form", clear_on_submit=True):
    st.subheader("🏫 1. School & Teacher Details")
    
    school_name = st.selectbox(
        "Select Your School / Institution *",
        options=["Select School...", "Delhi Public School Bhopal", "St. Xavier's High School", "Campion School Bhopal", "Bhopal Convent School", "Global Tech School"]
    )
    
    teacher_name = st.text_input("Full Name (e.g., Harshit Bhargav) *")

    st.markdown("---")
    st.subheader("📚 2. Class & Academic Details")
    
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        grade = st.selectbox("Grade / Class *", options=["Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6", "Grade 7", "Grade 8", "Grade 9", "Grade 10"])
    with col_g2:
        subject = st.selectbox("Subject *", options=["Mathematics", "Science", "English", "Social Studies", "Hindi", "Environmental Studies (EVS)", "Computer Science"])

    lesson_prep_mins = st.number_input("Lesson Preparation / Delivery Time (in Minutes) *", min_value=0.0, max_value=300.0, value=10.0, step=5.0)

    st.markdown("---")
    st.subheader("📁 3. Qualitative Evidences & Artifact Hub")
    st.caption("Paste shareable links for your daily evidence or audio/video recordings.")

    voice_note_link = st.text_input("🎧 Voice Note Link (Pre-class planning or reflection audio)")
    
    col_v1, col_v2 = st.text_input("🎥 Classroom Activity Video Link #1"), st.text_input("🎥 Classroom Activity Video Link #2")
    
    writing_sample_link = st.text_input("📝 Student Writing Sample / Worksheet Link")
    
    assessment_score = st.number_input("📊 Student Assessment Outcome Score (%) - Optional", min_value=0.0, max_value=100.0, value=0.0, step=1.0)

    st.markdown("---")
    st.subheader("📸 4. Snapshot Picture Uploads")
    st.caption("Upload images directly (snapshots of assessment result sheets, mark registers, or student portfolio project work).")

    assessment_image = st.file_uploader("🖼️ Upload Assessment Score Picture (.png, .jpg, .jpeg)", type=["png", "jpg", "jpeg"], key="assess_img")
    portfolio_image = st.file_uploader("🖼️ Upload Student Portfolio Picture (.png, .jpg, .jpeg)", type=["png", "jpg", "jpeg"], key="portfolio_img")

    submitted = st.form_submit_button("🚀 Submit Daily Report")

    if submitted:
        if school_name == "Select School...":
            st.error("⚠️ Please select your school before submitting.")
        elif not teacher_name.strip():
            st.error("⚠️ Please enter your full name.")
        else:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = teacher_name.strip().replace(" ", "_")

            # Save uploaded assessment image locally if present
            assess_img_path = None
            if assessment_image is not None:
                file_extension = assessment_image.name.split('.')[-1]
                assess_filename = f"{safe_name}_assessment_{timestamp_str}.{file_extension}"
                assess_img_path = os.path.join(MEDIA_FOLDER, assess_filename)
                with open(assess_img_path, "wb") as f:
                    f.write(assessment_image.getbuffer())

            # Save uploaded portfolio image locally if present
            portfolio_img_path = None
            if portfolio_image is not None:
                file_extension = portfolio_image.name.split('.')[-1]
                portfolio_filename = f"{safe_name}_portfolio_{timestamp_str}.{file_extension}"
                portfolio_img_path = os.path.join(MEDIA_FOLDER, portfolio_filename)
                with open(portfolio_img_path, "wb") as f:
                    f.write(portfolio_image.getbuffer())

            # Package submission record
            new_entry = {
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Institution": school_name,
                "FullName": teacher_name.strip(),
                "Grade": grade,
                "Subject": subject,
                "Duration_Min": lesson_prep_mins,
                "Type": "teacherSubmission",
                "Voice_Note_Link": voice_note_link if voice_note_link.strip() else None,
                "Video_Evidence_1": col_v1 if col_v1.strip() else None,
                "Video_Evidence_2": col_v2 if col_v2.strip() else None,
                "Video_Evidence_3": None,
                "Writing_Sample_Link": writing_sample_link if writing_sample_link.strip() else None,
                "Assessment_Score_Pct": assessment_score if assessment_score > 0 else None,
                "Assessment_Image_Path": assess_img_path,
                "Portfolio_Image_Path": portfolio_img_path
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
            st.success(f"✅ Thank you, {teacher_name}! Your submission and pictures have been successfully saved.")
            st.balloons()

# --- ADMIN EXPORT PANEL ---
st.markdown("---")
with st.expander("🔐 Academic Manager Export Panel"):
    st.caption("Download all collected teacher form submissions to upload directly into your main dashboard.")
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
