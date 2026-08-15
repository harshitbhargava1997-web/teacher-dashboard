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

# --- COMPLETE SCHOOL & TEACHER DIRECTORY ---
SCHOOL_TEACHER_ROSTER = {
    "Pragyanam International School": ["Deepali Yadav", "Tr Hema", "Tr Kavita"],
    "Little Commando Foundations School": ["Deepika mewada", "Gayatri Singh", "Jagruti Patil", "Roshni Rawat", "Sapna yadav", "Shraddha mishra", "TrPriyanka"],
    "Nature's Kids University": ["AAYUSHI PATIDAR", "ADITI YOGI", "Anjali Mukati", "Binu Joshi", "ISHIKA PANJWANI", "Kawaljeet Kaur Bhatia", "Komal Wardhani", "MANISHA VARMA", "Meenakshi Panwar", "PRACHI SEN", "REETA HADA", "SHIVANI RATHORE", "SURYA PATIDAR", "Saket Sharma", "Sakshi Sanwatsar", "Sapna Chouhan", "TANISHA BORYALE", "Tejaswi Mishra", "VEENA CHOUDHARY"],
    "Wisdom World School - Gwalior": ["Bhumi Sharma", "Hemlata Sharma", "Lata Golash", "Neelu Gupta", "Saloni Tyagi", "Sanya Yadav", "Shashi Maini"],
    "Noble Minds International School Gwalior": ["Geeta Godiya", "Miss Mohini", "Smita Chauhan", "Manisha Pandey", "Pinky Goud", "Soma Tomar", "Manju Pal", "Seema Tomar", "Soma Khare", "Pooja Jha"],
    "Nahar Global School": ["Anushika Rathod", "KAJAL TANK", "Rimzim Sisodiya", "TrKhushboo", "mansi Sisodiya", "Atika Mansuri", "Pragati Rathore", "SIMRAN BHATIA", "Umang Solanki", "JAGRATI BAIS", "Pragya Dixit", "Tanishka RATHORE", "archana Upadhyay"],
    "Colonels Academy": ["Aarti Joseph", "Karuna Tomer", "Prachi Joshi", "Prizma Singh", "Tara Pawar", "Divya Dubey", "Neha Bisht", "Prakrati", "Rehana Hussain", "TrSakshi", "Heena", "Noopur Thapliyal", "Preetilyer", "Shubhangi", "Vijaya Bisht"],
    "Jayshree Bal Vinay Mandir": ["Anshu Tiwari", "Bulbul Patel", "Geeta Patel", "Mahima Jadhav", "Maya Parmar", "Neetu Patel", "Rekha Solanki", "Tushar Waghmare"],
    "Jain Public School": ["Arjun Borana", "Deepti Pateriya", "Paridhi Soni", "Pragati Pawar", "Ragni Varagi", "Ritu Shatawar", "Shrijal Gupta", "Sushma Kumar", "Swati Dwivedi"],
    "Rational Kids Academy-Gwalior": ["Esha Saxena", "Ms Namrata", "Rakhi Kushwah", "Ishika Sharna", "Muskan Bhadoriya", "Sneha Prabha", "Neha Saxena", "Kushboo Sharma"],
    "Charming Kids International": ["ANUPAMA MOGHE", "Chhaya Motwani", "Kushboo Purwani", "Mitali Sachdev", "Muskan Sachdev", "Palak Kingrani", "Swati Parmar"],
    "Ambika Convent HR Sec school": ["Ankita Khede", "Komal Vaskel", "Mamta Meena", "Ramila Dawar", "Simran Pancholi"],
    "Mother's Pride School": ["Jyoti Modi", "Kalpana Rathore", "Monika Rekvar", "Pooja Bairagi", "Rani Gujrati"],
    "Universal Day Boarding Academy": ["Dolly", "Pooja Kushwah", "Raghvendra", "Rajeev Pal", "Sandhya", "Somesh Shah"],
    "Credible World School": ["Disha Raghuvanshi", "Komal Jain", "Miss aakrati", "Priyanka Joshi", "Ritu Rathore", "Shivani Jain"],
    "Scholar's High School": ["Israr Mansuri", "Nidhi bhawasar", "Shahnoor Sheikh", "Vishal Pawar", "tamanna waskale"],
    "Late Shree Pidiya Bhuriya Memorial School": ["Anjali Khatediya", "Gayatri Datla", "Kamala Muniya", "Pratika Bhuriya", "Rasna Ninama", "Seema Damor", "Vandana Vasuniya"],
    "JK International School": ["Anjali Pal", "Palak Agrawal", "priyanka kol", "Arpita Sharma", "Poorva Soni", "Khushbu Gupta", "Sakhi Sonia", "Shivangi Malviya", "harshita mishra", "kirti choubey"],
    "Rainbow Play School - Karnawad": ["Neha Patidar", "Payal Patidar", "Purva Patidar", "Suhani Patidar", "Nikita Patidar", "Pooja Patidar", "Rachna Patidar", "Nita Soner", "Pratibha Patidar", "Ravi Patidar"],
    "Dream India-Khargone": ["Ayushi Rathod", "Manish Mandloi", "Ragini Chouhan", "Simran Kushwah", "Laxmi Rathore", "Miss Anisha", "Shahani Pinjane", "Sunita Rawal", "Madhu gupta", "PRIYA KANUNGO", "Shailbala singh", "Vashnavi Pinjane"],
    "Shubharambh Academy": ["Ajay Rajput", "Mamta Solanki", "Sharda Trivedi", "payal bhabhri"],
    "Active English School": ["Bhumika Jhawar", "Meena Sharma", "Mithlesh Suri", "Pankaj Sir", "Priyanka prajapati", "SHEEFA Mansuri", "Sheefa Mansuri", "Vinesh Sikarwar", "Ranjana Pandey"],
    "Lebad Public School": ["Deepali Rathore", "Divya Parmar", "Ishika Raghuvanshi", "Joyti Bhat", "KAVITA CHOUHAN", "Madhu Gour", "Miss Shewta", "Miss Joyti", "Mustafa Sir", "NIBHA UPADHYAY", "Nikita Manawat", "Rani Singh", "Roshni Goswami", "Shivani Pandey", "Sneha Rai"],
    "Adarsh Gurukul Academy": ["Mrs Kalpana Mahawar", "Manali Sharma", "Neelu Garg", "Smita Dashottar", "Anju Jain", "ManjuBala Sharma", "Neha Shaktawat", "Rekha Salaya", "Uma Dwiveldi", "Kavita Budhani", "Neelam Rathod", "Poonam Gadwal", "Simran Bhatia", "Pramila Lalan", "sneha gehlot"],
    "Innovative Public School": ["Alina Khan", "Sarita Sharma", "Chandni Khan", "Naseen Shaikh", "Farheen Khan", "Mantasha Shah", "Pooja Verma"],
    "ECS - SANSKRITI CORRIDOR": ["Deepali Gore", "Deepali Gore", "Kajal Khare", "Mahima Soni", "Megha Sharma"],
    "Arhamn International School": ["ANURAG JAIN", "CHANCHAL VIDHYARTHI", "JYOTI BALA YADAV", "Khushi Patil", "PALLAVI RAWAT", "Prachi chouhan", "Priti Bhawsar", "Rashmi Verma", "Yashaswi Girnar", "pooja Chouhan"]
}

# Form Input Fields
with st.form("teacher_submission_form", clear_on_submit=True):
    st.subheader("🏫 1. School & Teacher Details")
    
    school_options = ["Select School..."] + list(SCHOOL_TEACHER_ROSTER.keys())
    school_name = st.selectbox("Select Your School / Institution *", options=school_options)
    
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
                "Maths / Numeracy", "EVS", "Literacy / English", "Hindi", "Science", 
                "Social Science", "Computer", "GK", "Grammar", 
                "Pre-Primary Play Activities", "Pre-Primary Art & Craft Activities"
            ]
        )

    lesson_plan_number = st.text_input("Lesson Plan Number *")

    st.markdown("---")
    st.subheader("📁 3. Direct Qualitative Evidence Uploads")
    st.caption("You may upload one or multiple files below (Audio, Video, Writing/Worksheet).")

    voice_note_file = st.file_uploader("🎧 Upload Voice Note (.mp3, .wav, .m4a)", type=["mp3", "wav", "m4a"], key="voice_upload")
    video_file = st.file_uploader("🎥 Upload Classroom Video (.mp4, .mov, .avi)", type=["mp4", "mov", "avi"], key="video_upload")
    writing_file = st.file_uploader("📝 Upload Writing Sample / Worksheet (.pdf, .jpg, .png)", type=["pdf", "jpg", "png"], key="writing_upload")

    st.markdown("---")
    st.subheader("📸 4. Assessment & Result Picture Upload")
    st.caption("Upload snapshots of evaluation result sheets or mark registers.")

    result_image = st.file_uploader("📊 Upload Assessment / Result Snapshot (.png, .jpg, .jpeg)", type=["png", "jpg", "jpeg"], key="result_upload")

    submitted = st.form_submit_button("🚀 Submit Daily Report")

    if submitted:
        if school_name == "Select School...":
            st.error("⚠️ Please select your school.")
        elif teacher_name == "Select Teacher..." or teacher_name.startswith("Please"):
            st.error("⚠️ Please select your teacher name.")
        elif not lesson_plan_number.strip():
            st.error("⚠️ Please enter the Lesson Plan Number.")
        else:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = teacher_name.strip().replace(" ", "_")

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

            new_entry = {
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Institution": school_name,
                "FullName": teacher_name.strip(),
                "Grade": grade,
                "Subject": subject,
                "Lesson_Plan_Num": lesson_plan_number.strip(),
                "Type": "teacherSubmission",
                "Voice_Note_Link": voice_path,
                "Video_Evidence_1": video_path,
                "Writing_Sample_Link": writing_path,
                "Assessment_Image_Path": result_path
            }

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
            st.success(f"✅ Thank you, {teacher_name}! Your submission has been recorded.")
            st.balloons()

# --- ADMIN EXPORT PANEL ---
st.markdown("---")
with st.expander("🔐 Academic Manager Export Panel"):
    st.caption("Download collected submissions as CSV for dashboard sync.")
    if os.path.exists(FORM_DB_PATH):
        try:
            admin_df = pd.read_excel(FORM_DB_PATH)
            csv_data = admin_df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Submissions (CSV)", data=csv_data, file_name="Teacher_Submissions_Export.csv", mime="application/csv")
        except Exception as e:
            st.error(f"Error loading export file: {e}")
