import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import os
import glob
from io import BytesIO
from datetime import datetime

# ReportLab PDF Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# --- MAPPING: HEAD OF INSTITUTION -> SCHOOL NAME ---
institution_mapping = {
    "Ms. Megha Balke (Pragyanam International School)": "Pragyanam International School",
    "Mr. Bhavesh Verma (Little Commando Foundations School)": "Little Commando Foundations School",
    "Mr. Saket Sharma (Nature's Kids University)": "Nature's Kids University",
    "Mr. Pramod Sharma (Wisdom World School - Gwalior)": "Wisdom World School - Gwalior",
    "Mr. Donger (Noble Minds International School Gwalior)": "Noble Minds International School Gwalior",
    "Ms. Deepa Ma'am (Nahar Global School)": "Nahar Global School",
    "Ms. Simmi Narad (Colonels Academy)": "Colonels Academy",
    "Mr. Ashok Waghmare (Jayshree Bal Vinay Mandir)": "Jayshree Bal Vinay Mandir",
    "Mr. Sapna Bastion (Jain Public School)": "Jain Public School",
    "Ms. Namrata Ma'am (Rational Kids Academy-Gwalior)": "Rational Kids Academy-Gwalior",
    "Mr. Sandeep Sir (Charming Kids International)": "Charming Kids International",
    "Mr. Rakesh Sir (Ambika Convent HR Sec school)": "Ambika Convent HR Sec school",
    "Ms. Kamiya Ma'am (Mother's Pride School)": "Mother's Pride School",
    "Mr. Sunil (Universal Day Boarding Academy)": "Universal Day Boarding Academy-Gwalior",
    "Mr Atul (Credible World School)": "Credible World School",
    "Mr. Anas (AG Azad Memorial Academy)": "AG Azad Memorial Academy",
    "Mr. Nazim Mansuri (Scholar's High School)": "Scholar's High School",
    "Mr. Oliver (Late Shree Pidiya Bhuriya Memorial School)": "Late Shree Pidiya Bhuriya Memorial School",
    "Miss. Soniya (JK International School)": "JK International School",
    "Mr. Sunil (Rainbow Play School - Karnawad)": "Rainbow Play School - Karnawad",
    "Mr. Manish (Dream India-Khargone)": "Dream India-Khargone",
    "Shubharambh Academy (Mr. Ajay Rajput)": "Shubharambh Academy",
    "Mr. Pankaj (Active English School)": "Active English School",
    "Mr. Mustafa (Lebad Public School)": "Lebad Public School",
    "Mrs. Smita (Adarsh Gurukul Academy)": "Adarsh Gurukul Academy",
    "Mr. Syed Maksood Ali (Innovative Public School)": "Innovative Public School",
    "Ms. Vaishali (ECS Maha lakshmi)": "ECS Maha lakshmi",
    "Ms. Pooja (ECS Vijay nagar)": "ECS Vijay nagar",
    "Ms. Megha Shrivastav (Kids Garden School)": "Kids Garden School",
    "Mr Abhishek (Arhamn International School)": "Arhamn International School"
}

# --- MASTER TEACHER ROSTERS ---
school_teachers = {
    "Pragyanam International School": ["Deepali Yadav", "Tr Hema", "Tr Kavita"],
    "Little Commando Foundations School": ["Deepika mewada", "Gayatri Singh", "Jagruti Patil", "Roshni Rawat", "Sapna yadav", "Shraddha mishra", "TrPriyanka"],
    "Nature's Kids University": ["AAYUSHI PATIDAR", "ADITI YOGI", "Anjali Mukati", "Binu Joshi", "ISHIKA PANJWANI", "Kawaljeet Kaur Bhatia", "Komal Wardhani", "MANISHA VARMA", "Meenakshi Panwar", "PRACHI SEN", "REETA HADA", "SHIVANI RATHORE", "SURYA PATIDAR", "Saket Sharma", "Sakshi Sanwatsar", "Sapna Chouhan", "TANISHA BORYALE", "Tejaswi Mishra", "VEENA CHOUDHARY"],
    "Wisdom World School - Gwalior": ["Bhumi Sharma", "Hemlata Sharma", "Lata Golash", "Neelu Gupta", "Saloni Tyagi", "Sanya Yadav", "Shashi Maini"],
    "Noble Minds International School Gwalior": ["Geeta Godiya", "Miss Mohini", "Smita Chauhan", "Manisha Pandey", "Pinky Goud", "Soma Tomar", "Manju Pal", "Seema Tomar", "Soma Khare", "Pooja Jha"],
    "Nahar Global School": ["Anushika Rathod", "KAJALTANK", "Rimzim Sisodiya", "TrKhushboo", "mansi Sisodiya", "Atika Mansuri", "Pragati Rathore", "SIMRAN BHATIA", "Umang Solanki", "JAGRATIBAIS", "Pragya Dixit", "Tanishka RATHORE", "archana Upadhyay"],
    "Colonels Academy": ["Aarti Joseph", "Karuna Tomer", "Prachi Joshi", "Prizma Singh", "Tara Pawar", "Divya Dubey", "Neha Bisht", "Prakrati", "Rehana Hussain", "TrSakshi", "Heena", "Noopur Thapliyal", "Preetilyer", "Shubhangi", "Vijaya Bisht"],
    "Jayshree Bal Vinay Mandir": ["Anshu Tiwari", "Bulbul Patel", "Geeta Patel", "Mahima Jadhav", "Maya Parmar", "Neetu Patel", "Rekha Solanki", "Tushar Waghmare"],
    "Jain Public School": ["Arjun Borana", "Deepti Pateriya", "Paridhi Soni", "Pragati Pawar", "Ragni Varagi", "Ritu Shatawar", "Shrijal Gupta", "Sushma Kumar", "Swati Dwivedi"],
    "Rational Kids Academy-Gwalior": ["Esha Saxena", "Ms Namrata", "Rakhi Kushwah", "Ishika Sharna", "Muskan Bhadoriya", "Sneha Prabha", "Neha Saxena", "Kushboo Sharma"],
    "Charming Kids International": ["ANUPAMA MOGHE", "Chhaya Motwani", "Kushboo Purwani", "Mitali Sachdev", "Muskan Sachdev", "Palak Kingrani", "Swati Parmar"],
    "Ambika Convent HR Sec school": ["Ankita Khede", "Komal Vaskel", "Mamta Meena", "Ramila Dawar", "Simran Pancholi"],
    "Mother's Pride School": ["Jyoti Modi", "Kalpana Rathore", "Monika Rekvar", "Pooja Bairagi", "Rani Gujrati"],
    "Universal Day Boarding Academy-Gwalior": ["Dolly", "Pooja Kushwah", "Raghvendra", "Rajeev Pal", "Sandhya", "Somesh Shah"],
    "Credible World School": ["Disha Raghuvanshi", "Komal Jain", "Miss aakrati", "Priyanka Joshi", "Ritu Rathore", "Shivani Jain"],
    "AG Azad Memorial Academy": [],
    "Scholar's High School": ["Israr Mansuri", "Nidhi bhawasar", "Shahnoor Sheikh", "Vishal Pawar", "tamanna waskale"],
    "Late Shree Pidiya Bhuriya Memorial School": ["Anjali Khatediya", "Gayatri Datla", "Kamala Muniya", "Pratika Bhuriya", "Rasna Ninama", "Seema Damor", "Vandana Vasuniya"],
    "JK International School": ["Anjali Pal", "Palak Agrawal", "priyanka kol", "Arpita Sharma", "Poorva Soni", "Khushbu Gupta", "Sakhi Sonia", "Shivangi Malviya", "harshita mishra", "kirti choubey"],
    "Rainbow Play School - Karnawad": ["Neha Patidar", "Payal Patidar", "Purva Patidar", "Suhani Patidar", "Nikita Patidar", "Pooja Patidar", "Rachna Patidar", "Nita Soner", "Pratibha Patidar", "Ravi Patidar"],
    "Dream India-Khargone": ["Ayushi Rathod", "Manish Mandloi", "Ragini Chouhan", "Simran Kushwah", "Laxmi Rathore", "Miss Anisha", "Shahani Pinjane", "Sunita Rawal", "Madhu gupta", "PRIYA KANUNGO", "Shailbala singh", "Vashnavi Pinjane"],
    "Shubharambh Academy": ["Ajay Rajput", "Mamta Solanki", "Sharda Trivedi", "payal bhabhri"],
    "Active English School": ["Bhumika Jhawar", "Meena Sharma", "Mithlesh Suri", "Pankaj Sir", "Priyanka prajapati", "SHEEFA Mansuri", "Sheefa Mansuri", "Vinesh Sikarwar", "Ranjana Pandey"],
    "Lebad Public School": ["Deepali Rathore", "Divya Parmar", "Ishika Raghuvanshi", "Joyti Bhat", "KAVITA CHOUHAN", "Madhu Gour", "Miss Shewta", "Miss Joyti", "Mustafa Sir", "NIBHA UPADHYAY", "Nikita Manawat", "Rani Singh", "Roshni Goswami", "Shivani Pandey", "Sneha Rai"],
    "Adarsh Gurukul Academy": ["Mrs Kalpana Mahawar", "Manali Sharma", "Neelu Garg", "Smita Dashottar", "Anju Jain", "ManjuBala Sharma", "Neha Shaktawat", "Rekha Salaya", "Uma Dwivedi", "Kavita Budhani", "Neelam Rathod", "Poonam Gadwal", "Simran Bhatia", "Pramila Lalan", "sneha gehlot"],
    "Innovative Public School": ["Alina Khan", "Sarita Sharma", "Chandni Khan", "Naseen Shaikh", "Farheen Khan", "Mantasha Shah", "Pooja Verma"],
    "ECS Maha lakshmi": [],
    "ECS Vijay nagar": [],
    "Kids Garden School": ["Arpita Rajak", "Megha Shrivastava", "Palak Jain", "Ritika Parmar", "Gagan Preet Kaur", "Ms Yeshavi", "Reem Wareen", "Hema Rawat", "Muskan Rathore", "Ritiesh Parmar"],
    "Arhamn International School": ["ANURAG JAIN", "CHANCHAL VIDHYARTHI", "JYOTI BALA YADAV", "Khushi Patil", "PALLAVI RAWAT", "Prachi chouhan", "Priti Bhawsar", "Rashmi Verma", "Yashaswi Girnar", "pooja Chouhan"]
}

# 0. PDF Generator Helper Function
def generate_pdf_report(title_text, subtitle_text, summary_metrics, dataframe):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=15, leading=18, textColor=colors.HexColor('#1F77B4'))
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.gray)
    
    story.append(Paragraph(f"<b>{title_text}</b>", title_style))
    story.append(Paragraph(subtitle_text, subtitle_style))
    story.append(Spacer(1, 12))

    if summary_metrics:
        metric_text = " &nbsp;&nbsp;|&nbsp;&nbsp; ".join([f"<b>{k}:</b> {v}" for k, v in summary_metrics.items()])
        metric_style = ParagraphStyle('MetricBlock', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor('#2CA02C'))
        story.append(Paragraph(metric_text, metric_style))
        story.append(Spacer(1, 12))

    if not dataframe.empty:
        raw_data = [dataframe.columns.tolist()] + dataframe.astype(str).values.tolist()
        
        cell_style = ParagraphStyle('TableCell', parent=styles['Normal'], fontSize=8, leading=10)
        header_style = ParagraphStyle('TableHeader', parent=styles['Normal'], fontSize=8, leading=10, textColor=colors.white, fontName='Helvetica-Bold')

        formatted_data = []
        for i, row in enumerate(raw_data):
            formatted_row = []
            for cell in row:
                st_to_use = header_style if i == 0 else cell_style
                formatted_row.append(Paragraph(str(cell), st_to_use))
            formatted_data.append(formatted_row)

        pdf_table = Table(formatted_data, repeatRows=1)
        pdf_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F77B4')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9F9F9')]),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(pdf_table)

    doc.build(story)
    buffer.seek(0)
    return buffer

def get_working_days(start_date, end_date, excluded_dates_list, exclude_sundays=True):
    try:
        start_np = np.datetime64(start_date)
        end_np = np.datetime64(end_date) + np.timedelta64(1, 'D')
        holidays_np = [np.datetime64(d) for d in excluded_dates_list] if excluded_dates_list else []
        
        w_mask = '1111110' if exclude_sundays else '1111111'
        return int(np.busday_count(start_np, end_np, weekmask=w_mask, holidays=holidays_np))
    except Exception:
        return 1

# Page layout
st.set_page_config(page_title="Academic Manager Portfolio & Teacher KPI Dashboard", layout="wide")

# --- SIDEBAR ACCESS TOGGLE (ADMIN VS TEACHER PORTAL) ---
st.sidebar.header("🔐 Access Control Mode")
access_mode = st.sidebar.radio("Select View Mode:", ["Admin & Analytics Dashboard", "Teacher Submission Portal Only"])

# 1. Local Storage & Parquet Database Setup
DATA_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "dashboard_data_store")
try:
    os.makedirs(DATA_FOLDER, exist_ok=True)
except Exception:
    DATA_FOLDER = "dashboard_data_store"
    if not os.path.exists(DATA_FOLDER):
        try:
            os.mkdir(DATA_FOLDER)
        except Exception:
            pass

DB_PATH = os.path.join(DATA_FOLDER, "master_database.parquet")

def load_or_update_master_db(new_upload_dfs=None):
    master_df = pd.DataFrame()
    if os.path.exists(DB_PATH):
        try:
            master_df = pd.read_parquet(DB_PATH)
        except Exception:
            master_df = pd.DataFrame()

    if new_upload_dfs:
        combined_new = pd.concat(new_upload_dfs, ignore_index=True)
        if not master_df.empty:
            all_data = pd.concat([master_df, combined_new], ignore_index=True)
        else:
            all_data = combined_new

        dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution']
        available_dedup_cols = [c for c in dedup_cols if c in all_data.columns]
        
        master_df = all_data.drop_duplicates(subset=available_dedup_cols, keep='first')
        
        try:
            master_df.to_parquet(DB_PATH, index=False)
        except Exception as e:
            st.sidebar.error(f"Error saving Parquet Database: {e}")

    return master_df

# Handle Sidebar Uploads (Admin side)
if access_mode == "Admin & Analytics Dashboard":
    st.sidebar.markdown("---")
    st.sidebar.header("📁 Data Upload & Database Sync")
    uploaded_files = st.sidebar.file_uploader("Upload UserMetrics Excel (.xlsx)", type=["xlsx"], accept_multiple_files=True)
else:
    uploaded_files = None

new_processed_dfs = []
if uploaded_files:
    for file in uploaded_files:
        try:
            temp_df = pd.read_excel(file, sheet_name="UserMetrics")
            
            temp_df['FirstName'] = temp_df['FirstName'].fillna('').astype(str).str.strip() if 'FirstName' in temp_df.columns else ''
            temp_df['LastName'] = temp_df['LastName'].fillna('').astype(str).str.strip() if 'LastName' in temp_df.columns else ''
            temp_df['FullName'] = (temp_df['FirstName'] + " " + temp_df['LastName']).str.strip()
            temp_df.loc[temp_df['FullName'] == '', 'FullName'] = 'Unknown Teacher'

            if 'Institution' not in temp_df.columns:
                temp_df['Institution'] = "Default School"
            else:
                temp_df['Institution'] = temp_df['Institution'].fillna('Unknown School').astype(str).str.strip()

            for col in ['Grade', 'Subject', 'Book']:
                if col not in temp_df.columns:
                    temp_df[col] = ''
                else:
                    temp_df[col] = temp_df[col].fillna('').astype(str).str.strip()

            def parse_time_mins(t_str):
                try:
                    parts = str(t_str).split(':')
                    return int(parts[0])*60 + int(parts[1]) + float(parts[2])/60.0
                except:
                    return 0.0

            if 'Duration (HH:MM:SS)' in temp_df.columns:
                temp_df['Duration_Min'] = temp_df['Duration (HH:MM:SS)'].apply(parse_time_mins)
            elif 'Duration (Minutes)' in temp_df.columns:
                temp_df['Duration_Min'] = pd.to_numeric(temp_df['Duration (Minutes)'], errors='coerce').fillna(0.0)
            else:
                temp_df['Duration_Min'] = 0.0

            if 'Type' in temp_df.columns:
                temp_df['Type'] = temp_df['Type'].fillna('Other').astype(str)

            if 'StartTime' in temp_df.columns:
                temp_df['StartTime'] = pd.to_datetime(temp_df['StartTime'], errors='coerce')

            for qual_col in ['Voice_Note_Link', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 'Assessment_Score_Pct']:
                if qual_col not in temp_df.columns:
                    temp_df[qual_col] = None

            new_processed_dfs.append(temp_df)
        except Exception as e:
            st.sidebar.error(f"Error reading {file.name}: {e}")

if new_processed_dfs:
    df = load_or_update_master_db(new_processed_dfs)
    st.sidebar.success(f"Synced {len(uploaded_files)} file(s) into Master Parquet DB!")
else:
    df = load_or_update_master_db()

# --- STANDALONE TEACHER PORTAL MODE ---
if access_mode == "Teacher Submission Portal Only":
    st.title("📚 Teacher Evidence & Qualitative Hub")
    st.markdown("Welcome teachers! Select your institution head and your name to submit your daily lesson prep and classroom artifacts securely.")

    # Clean mapping where keys are ONLY the Head's name, hiding school names completely from teachers
    head_only_mapping = {
        head_str.split('(')[0].strip(): school 
        for head_str, school in institution_mapping.items()
    }

    selected_head = st.selectbox("Select Your Institution Head", list(head_only_mapping.keys()), key="portal_head_solo")
    selected_school = head_only_mapping[selected_head]

    available_portal_teachers = school_teachers.get(selected_school, [])
    selected_portal_teacher = st.selectbox("Select Your Name", available_portal_teachers if available_portal_teachers else ["No teachers listed - check roster"], key="portal_teacher_solo")

    with st.form("teacher_live_submission_form_solo"):
        st.subheader("Activity & Evidence Details")
        
        p_grade = st.selectbox("Grade / Class Level", ["Nursery", "LKG", "UKG", "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5"], key="portal_grade_solo")
        p_subject = st.selectbox("Subject", ["Mathematics", "EVS", "English", "Hindi", "Phonics"], key="portal_subject_solo")
        p_activity_num = st.text_input("Lesson Plan / Activity Number", key="portal_act_num_solo")
        p_activity_date = st.date_input("Date of Activity", datetime.today(), key="portal_date_solo")
        
        st.markdown("---")
        st.markdown("### Upload Evidence Files")
        voice_note = st.file_uploader("1. Daily Voice Note (Lesson Prep)", type=["mp3", "m4a", "wav"], key="portal_vn_solo")
        video_a = st.file_uploader("2. Classroom Video (Lesson Delivery & Concepts)", type=["mp4", "mov"], key="portal_va_solo")
        video_b = st.file_uploader("3. Classroom Video (Phonics & Literacy Reading)", type=["mp4", "mov"], key="portal_vb_solo")
        writing_sample = st.file_uploader("4. Student Writing Practice / Notebook Artifact", type=["png", "jpg", "pdf"], key="portal_ws_solo")
        
        portal_submitted = st.form_submit_button("Submit Evidence to Cloud")
        
        if portal_submitted:
            if selected_portal_teacher == "No teachers listed - check roster":
                st.error("Please select a valid teacher name before submitting.")
            else:
                try:
                    new_submission = pd.DataFrame([{
                        'Institution': selected_school,
                        'FullName': selected_portal_teacher,
                        'Grade': p_grade,
                        'Subject': p_subject,
                        'Book': p_activity_num,
                        'Type': 'lessonDelivery',
                        'StartTime': pd.to_datetime(p_activity_date),
                        'Duration_Min': 10.0,
                        'Voice_Note_Link': voice_note.name if voice_note else None,
                        'Video_Evidence_1': video_a.name if video_a else None,
                        'Video_Evidence_2': video_b.name if video_b else None,
                        'Writing_Sample_Link': writing_sample.name if writing_sample else None
                    }])

                    if os.path.exists(DB_PATH):
                        existing_master = pd.read_parquet(DB_PATH)
                        updated_master = pd.concat([existing_master, new_submission], ignore_index=True)
                    else:
                        updated_master = new_submission
                    
                    updated_master.to_parquet(DB_PATH, index=False)

                    st.success(f"Successfully submitted evidence for **{selected_portal_teacher}**! Your data has been securely logged.")
                except Exception as e:
                    st.error(f"Error saving submission: {e}")

# --- FULL ADMIN & ANALYTICS DASHBOARD MODE ---
else:
    st.title("🏫 Academic Manager Portfolio & Teacher KPI Review Dashboard")
    st.markdown("Track **School Portfolio Management**, **School WoW Velocity**, **Teacher Execution Tiers**, **Daily KPIs (10m Lesson / 30m Library)**, **360° Qualitative Evidences**, and **Assessment Outcomes**.")

    if df.empty:
        st.info("👋 Upload your raw daily or weekly `UserMetrics.xlsx` files in the sidebar or use the Teacher Submission Portal mode to populate your permanent database.")
    else:
        if 'StartTime' in df.columns:
            df['Date'] = df['StartTime'].dt.date
            df['Month_Name'] = df['StartTime'].dt.strftime('%B %Y')
            df['Month_Sort'] = df['StartTime'].dt.strftime('%Y-%m')
            
            def get_week_of_month(dt):
                try:
                    first_day = dt.replace(day=1)
                    dom = dt.day
                    adjusted_dom = dom + first_day.weekday()
                    return int(np.ceil(adjusted_dom / 7.0))
                except:
                    return 1
                    
            df['Week_Num'] = df['StartTime'].apply(get_week_of_month)
            
            week_ranges = df.groupby(['Month_Name', 'Week_Num'])['Date'].agg(['min', 'max']).reset_index()
            week_ranges['Week_Date_Range'] = (
                week_ranges['min'].apply(lambda x: x.strftime('%b %d')) + " to " + 
                week_ranges['max'].apply(lambda x: x.strftime('%b %d'))
            )
            
            df = df.merge(week_ranges[['Month_Name', 'Week_Num', 'Week_Date_Range']], on=['Month_Name', 'Week_Num'], how='left')
            df['Month_Week_Label'] = df['StartTime'].dt.strftime('%b %Y') + " - Week " + df['Week_Num'].astype(str) + " (" + df['Week_Date_Range'] + ")"
            df['Week'] = df['Month_Week_Label']
        else:
            df['Date'] = "N/A"
            df['Month_Name'] = "N/A"
            df['Week'] = "N/A"

        master_teacher_roster = df[['Institution', 'FullName']].drop_duplicates()

        st.sidebar.markdown("---")
        st.sidebar.header("🔍 Review Filters")
        all_schools = sorted([str(s) for s in df['Institution'].unique()])
        selected_schools = st.sidebar.multiselect("Select School(s)", options=all_schools, default=all_schools)

        school_master_roster = master_teacher_roster[master_teacher_roster['Institution'].isin(selected_schools)]
        school_filtered_df = df[df['Institution'].isin(selected_schools)]

        st.sidebar.markdown("---")
        st.sidebar.header("📅 Calendar & Holiday Manager")
        
        available_months_df = school_filtered_df[['Month_Sort', 'Month_Name']].drop_duplicates().sort_values(by='Month_Sort', ascending=False)
        month_options = available_months_df['Month_Name'].tolist() if not available_months_df.empty else ["Current Month"]
        
        selected_month = st.sidebar.selectbox("Select Review Month:", options=month_options)
        month_filtered_df = school_filtered_df[school_filtered_df['Month_Name'] == selected_month]
        
        exclude_sundays_flag = st.sidebar.checkbox("🗓️ Exclude Sundays from KPIs", value=True)

        user_excluded_dates = []
        if not month_filtered_df['Date'].isna().all():
            m_min_date = month_filtered_df['Date'].min()
            m_max_date = month_filtered_df['Date'].max()
            all_month_possible_dates = [d.date() for d in pd.date_range(start=m_min_date, end=m_max_date)]
            
            user_excluded_dates = st.sidebar.multiselect(
                f"🗓️ Punch Holidays for {selected_month}:",
                options=all_month_possible_dates,
                format_func=lambda x: x.strftime('%Y-%m-%d')
            )
            if user_excluded_dates:
                st.sidebar.caption(f"{len(user_excluded_dates)} holiday date(s) deducted from {selected_month} KPIs.")

        st.sidebar.subheader("🔍 Review View Level")
        available_month_weeks = sorted(month_filtered_df['Month_Week_Label'].unique()) if not month_filtered_df.empty else []
        available_dates = sorted(month_filtered_df['Date'].dropna().unique(), reverse=True) if not month_filtered_df.empty else []
        
        view_mode = st.sidebar.radio("Granularity:", ["Full Month Summary", "Specific Week of Month", "Single Day Review"])
        
        if view_mode == "Full Month Summary":
            filtered_df = month_filtered_df
            d_min = month_filtered_df['Date'].min() if not month_filtered_df['Date'].isna().all() else datetime.today().date()
            d_max = month_filtered_df['Date'].max() if not month_filtered_df['Date'].isna().all() else datetime.today().date()
            selected_num_days = get_working_days(d_min, d_max, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
            filter_description_text = f"Full Month: {selected_month} ({selected_num_days} Working Day(s))"
            
        elif view_mode == "Specific Week of Month" and available_month_weeks:
            selected_week_label = st.sidebar.selectbox("Select Week:", options=available_month_weeks)
            filtered_df = month_filtered_df[month_filtered_df['Month_Week_Label'] == selected_week_label]
            w_start = filtered_df['Date'].min()
            w_end = filtered_df['Date'].max()
            selected_num_days = get_working_days(w_start, w_end, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
            filter_description_text = f"{selected_week_label} ({selected_num_days} Working Day(s))"
            
        elif available_dates:
            selected_date = st.sidebar.selectbox("Select Day:", options=available_dates)
            filtered_df = month_filtered_df[month_filtered_df['Date'] == selected_date]
            selected_num_days = get_working_days(selected_date, selected_date, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
            filter_description_text = f"Single Date: {selected_date} ({selected_num_days} Working Day(s))"
        else:
            filtered_df = month_filtered_df
            selected_num_days = 1
            filter_description_text = "Default Period"

        calc_ld_kpi = 10.0 * selected_num_days
        calc_lib_kpi = 30.0 * selected_num_days

        available_teachers = sorted([str(t) for t in school_master_roster['FullName'].unique()]) if not school_master_roster.empty else []
        selected_teachers = st.sidebar.multiselect("Select Teacher(s)", options=available_teachers, default=available_teachers)
        
        filtered_roster = school_master_roster[school_master_roster['FullName'].isin(selected_teachers)] if not school_master_roster.empty else pd.DataFrame()
        filtered_df = filtered_df[filtered_df['FullName'].isin(selected_teachers)] if not filtered_df.empty else pd.DataFrame()

        # --- 8 DEDICATED ADMIN REVIEW TABS ---
        tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
            "📘 1. Daily Lesson Plan KPI", 
            "📚 2. Daily Library KPI", 
            "📖 3. Daily Content & Chapters", 
            "👤 4. Teacher 360° Profile Report",
            "🏛️ 5. Manager Portfolio Quadrants",
            "🏫 6. School Teacher Progression",
            "📊 7. Student Assessment Outcomes",
            "📥 8. Global Submissions Audit"
        ])

        # TAB 1: DAILY LESSON PLAN COMPLIANCE
        with tab1:
            st.header("📘 Daily Lesson Plan Preparation Tracker")
            st.caption(f"KPI Benchmark: **At least {calc_ld_kpi:.0f} Minutes** ({10} mins/day across {selected_num_days} working day(s)).")

            if not filtered_roster.empty:
                ld_df = filtered_df[filtered_df['Type'] == 'lessonDelivery'] if not filtered_df.empty else pd.DataFrame()
                ld_usage = ld_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index() if not ld_df.empty else pd.DataFrame(columns=['Institution', 'FullName', 'Duration_Min'])
                ld_daily = filtered_roster.merge(ld_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
                
                def get_ld_status(x):
                    if calc_ld_kpi == 0: return '✅ Holiday / No-Class (0m Req)'
                    if x >= calc_ld_kpi: return f'✅ Met KPI (>= {calc_ld_kpi:.0f}m)'
                    elif x > 0.0: return f'⚠️ Below KPI (< {calc_ld_kpi:.0f}m)'
                    else: return '❌ Inactive (0 Mins)'
                
                ld_daily['KPI Status'] = ld_daily['Duration_Min'].apply(get_ld_status)

                c1, c2, c3, c4 = st.columns(4)
                total_teachers = len(ld_daily)
                met_count = len(ld_daily[ld_daily['Duration_Min'] >= calc_ld_kpi]) if calc_ld_kpi > 0 else total_teachers
                inactive_count = len(ld_daily[ld_daily['Duration_Min'] == 0.0])
                
                c1.metric("Total Roster Teachers", total_teachers)
                c2.metric(f"Met {calc_ld_kpi:.0f}m KPI", f"{met_count} / {total_teachers}")
                c3.metric("Inactive Teachers (0m)", inactive_count, delta=f"{-inactive_count}" if inactive_count > 0 else "0", delta_color="inverse")
                c4.metric("KPI Compliance Rate", f"{(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%")

                fig_ld = px.bar(
                    ld_daily, x="FullName", y="Duration_Min", color="KPI Status",
                    title=f"Lesson Prep Minutes per Teacher vs. {calc_ld_kpi:.0f} Min KPI Standard",
                    labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Prepared"},
                    text_auto=".1f"
                )
                fig_ld.add_hline(y=calc_ld_kpi, line_dash="dash", line_color="black", annotation_text=f"KPI Standard ({calc_ld_kpi:.0f} mins)")
                st.plotly_chart(fig_ld, use_container_width=True)

                st.subheader("📋 Lesson Plan KPI Review Table")
                display_ld_table = ld_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
                st.dataframe(display_ld_table, use_container_width=True)

                pdf_tab1 = generate_pdf_report(
                    title_text="📘 Daily Lesson Plan Preparation Report",
                    subtitle_text=f"Filter: {filter_description_text} | Total Teachers: {total_teachers}",
                    summary_metrics={
                        "Total Teachers": total_teachers,
                        "Met KPI Standard": f"{met_count} / {total_teachers}",
                        "Compliance Rate": f"{(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%"
                    },
                    dataframe=display_ld_table[['School', 'Teacher Name', 'Minutes Logged', 'KPI Status']]
                )
                st.download_button(
                    label="📄 Download Tab 1 Report (PDF)",
                    data=pdf_tab1,
                    file_name=f"Lesson_Plan_KPI_Report_{selected_month.replace(' ', '_')}.pdf",
                    mime="application/pdf"
                )
            else:
                st.info("No active teacher roster available for the selected filters.")

        # TAB 2: DAILY LIBRARY KPI
        with tab2:
            st.header("📚 Daily Library Usage Tracker")
            st.caption(f"KPI Benchmark: **At least {calc_lib_kpi:.0f} Minutes** ({30} mins/day across {selected_num_days} working day(s)).")

            if not filtered_roster.empty:
                lib_df = filtered_df[filtered_df['Type'] == 'library'] if not filtered_df.empty else pd.DataFrame()
                lib_usage = lib_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index() if not lib_df.empty else pd.DataFrame(columns=['Institution', 'FullName', 'Duration_Min'])
                lib_daily = filtered_roster.merge(lib_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
                
                def get_lib_status(x):
                    if calc_lib_kpi == 0: return '✅ Holiday / No-Class (0m Req)'
                    if x >= calc_lib_kpi: return f'✅ Met KPI (>= {calc_lib_kpi:.0f}m)'
                    elif x > 0.0: return f'⚠️ Below KPI (< {calc_lib_kpi:.0f}m)'
                    else: return '❌ Inactive (0 Mins)'

                lib_daily['KPI Status'] = lib_daily['Duration_Min'].apply(get_lib_status)

                m1, m2, m3, m4 = st.columns(4)
                lib_total_teachers = len(lib_daily)
                lib_met_count = len(lib_daily[lib_daily['Duration_Min'] >= calc_lib_kpi]) if calc_lib_kpi > 0 else lib_total_teachers
                lib_inactive_count = len(lib_daily[lib_daily['Duration_Min'] == 0.0])
                
                m1.metric("Total Roster Teachers", lib_total_teachers)
                m2.metric(f"Met {calc_lib_kpi:.0f}m KPI", f"{lib_met_count} / {lib_total_teachers}")
                m3.metric("Inactive Teachers (0m)", lib_inactive_count, delta=f"{-lib_inactive_count}" if lib_inactive_count > 0 else "0", delta_color="inverse")
                m4.metric("Library KPI Compliance Rate", f"{(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%")

                fig_lib = px.bar(
                    lib_daily, x="FullName", y="Duration_Min", color="KPI Status",
                    title=f"Library Minutes per Teacher vs. {calc_lib_kpi:.0f} Min KPI Standard",
                    labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Logged"},
                    text_auto=".1f"
                )
                fig_lib.add_hline(y=calc_lib_kpi, line_dash="dash", line_color="black", annotation_text=f"KPI Standard ({calc_lib_kpi:.0f} mins)")
                st.plotly_chart(fig_lib, use_container_width=True)

                st.subheader("📋 Library KPI Review Table")
                display_lib_table = lib_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
                st.dataframe(display_lib_table, use_container_width=True)

                pdf_tab2 = generate_pdf_report(
                    title_text="📚 Daily Library Usage Report",
                    subtitle_text=f"Filter: {filter_description_text} | Total Teachers: {lib_total_teachers}",
                    summary_metrics={
                        "Total Teachers": lib_total_teachers,
                        "Met KPI Standard": f"{lib_met_count} / {lib_total_teachers}",
                        "Compliance Rate": f"{(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%"
                    },
                    dataframe=display_lib_table[['School', 'Teacher Name', 'Minutes Logged', 'KPI Status']]
                )
                st.download_button(
                    label="📄 Download Tab 2 Report (PDF)",
                    data=pdf_tab2,
                    file_name=f"Library_KPI_Report_{selected_month.replace(' ', '_')}.pdf",
                    mime="application/pdf"
                )
            else:
                st.info("No data available.")

        # TAB 3: CHAPTERS & RESOURCE BREAKDOWN
        with tab3:
            st.header("📖 Chapters & Content Modules Opened")
            st.caption(f"Track specific books, subjects, and themes during `{filter_description_text}`.")

            if not filtered_df.empty and 'Book' in filtered_df.columns:
                content_df = filtered_df[filtered_df['Book'].str.len() > 0]

                if content_df.empty:
                    st.info("No specific chapter/book access logs found in the uploaded data for the selected global filters.")
                else:
                    st.markdown("#### 🎯 Drill-Down Filters")
                    col_f1, col_f2, col_f3 = st.columns(3)
                    
                    with col_f1:
                        t3_school_opt = ["All Selected Schools"] + sorted(content_df['Institution'].unique().tolist())
                        t3_school = st.selectbox("🏫 Select School:", t3_school_opt, key="t3_school")
                        
                    if t3_school != "All Selected Schools":
                        t3_df = content_df[content_df['Institution'] == t3_school]
                    else:
                        t3_df = content_df

                    with col_f2:
                        t3_teacher_opt = ["All Teachers"] + sorted(t3_df['FullName'].unique().tolist())
                        t3_teacher = st.selectbox("👤 Select Teacher:", t3_teacher_opt, key="t3_teacher")
                        
                    if t3_teacher != "All Teachers":
                        t3_df = t3_df[t3_df['FullName'] == t3_teacher]

                    with col_f3:
                        t3_subject_opt = ["All Subjects"] + sorted(t3_df['Subject'].unique().tolist())
                        t3_subject = st.selectbox("📚 Select Subject:", t3_subject_opt, key="t3_subject")

                    if t3_subject != "All Subjects":
                        t3_df = t3_df[t3_df['Subject'] == t3_subject]

                    st.markdown("---")

                    if t3_df.empty:
                        st.warning("No data matches these specific drill-down filters.")
                    else:
                        k1, k2, k3 = st.columns(3)
                        k1.metric("Chapters / Books Opened", t3_df['Book'].nunique())
                        k2.metric("Subjects Taught", t3_df['Subject'].nunique())
                        k3.metric("Total Content Access Time", f"{t3_df['Duration_Min'].sum():.1f} Mins")

                        col_c1, col_c2 = st.columns(2)

                        with col_c1:
                            if t3_teacher != "All Teachers":
                                ch_summary = t3_df.groupby(['Book', 'Grade'])['Duration_Min'].sum().reset_index()
                                fig_ch = px.bar(
                                    ch_summary, x="Duration_Min", y="Book", color="Grade", orientation="h",
                                    title=f"Chapters Opened by {t3_teacher} (Mins)",
                                    labels={"Duration_Min": "Minutes", "Book": "Book / Chapter"},
                                    text_auto=".1f"
                                )
                                fig_ch.update_layout(yaxis={'categoryorder':'total ascending'})
                            else:
                                ch_summary = t3_df.groupby(['FullName', 'Book'])['Duration_Min'].sum().reset_index()
                                fig_ch = px.bar(
                                    ch_summary, x="FullName", y="Duration_Min", color="Book",
                                    title="Chapters / Books Opened per Teacher (Mins)",
                                    labels={"FullName": "Teacher", "Duration_Min": "Minutes", "Book": "Book / Chapter"},
                                    barmode="stack", text_auto=".1f"
                                )
                            st.plotly_chart(fig_ch, use_container_width=True)

                        with col_c2:
                            subj_summary = t3_df.groupby('Subject')['Duration_Min'].sum().reset_index()
                            fig_sub = px.pie(
                                subj_summary, names="Subject", values="Duration_Min",
                                title="Subject / Theme Distribution (Minutes)"
                            )
                            st.plotly_chart(fig_sub, use_container_width=True)

                        st.subheader("📋 Filtered Granular Class Log")
                        log_cols = ['Institution', 'FullName', 'Grade', 'Subject', 'Book', 'StartTime', 'Duration (HH:MM:SS)', 'Duration_Min']
                        available_cols = [c for c in log_cols if c in t3_df.columns]
                        
                        display_content_log = t3_df[available_cols].rename(columns={
                            'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes'
                        }).sort_values(by='StartTime', ascending=False)
                        display_content_log['Minutes'] = display_content_log['Minutes'].round(1)
                        st.dataframe(display_content_log, use_container_width=True)

                        col_d1, col_d2 = st.columns(2)
                        with col_d1:
                            csv_t3 = display_content_log.to_csv(index=False).encode('utf-8')
                            st.download_button(
                                label="📥 Download Content Log (CSV)",
                                data=csv_t3,
                                file_name=f"Content_Log_{selected_month.replace(' ', '_')}.csv",
                                mime="text/csv"
                            )
                        with col_d2:
                            pdf_tab3 = generate_pdf_report(
                                title_text="📖 Chapters & Digital Content Usage Report",
                                subtitle_text=f"School: {t3_school} | Teacher: {t3_teacher} | Subject: {t3_subject}",
                                summary_metrics={
                                    "Chapters Opened": t3_df['Book'].nunique(),
                                    "Subjects Taught": t3_df['Subject'].nunique(),
                                    "Total Duration": f"{t3_df['Duration_Min'].sum():.1f} Mins"
                                },
                                dataframe=display_content_log[['School', 'Teacher Name', 'Grade', 'Subject', 'Book', 'Minutes']].head(30)
                            )
                            st.download_button(
                                label="📄 Download Tab 3 Content Report (PDF)",
                                data=pdf_tab3,
                                file_name=f"Content_Usage_Report_{selected_month.replace(' ', '_')}.pdf",
                                mime="application/pdf"
                            )
            else:
                st.info("No content data available.")

        # TAB 4: TEACHER 360° PROFILE REPORT (Audit Hub for Submissions)
        with tab4:
            st.header("👤 Teacher 360° Performance Profile & Submission Audit")

            all_roster_teachers = sorted(school_master_roster['FullName'].unique()) if not school_master_roster.empty else []
            
            if not all_roster_teachers:
                st.info("No teachers found in roster for the selected school(s).")
            else:
                target_teacher = st.selectbox("Select Teacher to Audit:", options=all_roster_teachers, key="admin_audit_teacher")
                
                teacher_date_data = filtered_df[filtered_df['FullName'] == target_teacher] if not filtered_df.empty else pd.DataFrame()
                teacher_school = school_master_roster[school_master_roster['FullName'] == target_teacher]['Institution'].values[0] if not school_master_roster[school_master_roster['FullName'] == target_teacher].empty else "N/A"

                st.markdown(f"### 📋 Audit Profile & Submissions: **{target_teacher}** | School: **{teacher_school}**")

                st.subheader("1. Performance Indicator Summary")
                st.info(f"📅 **Active Filter**: `{filter_description_text}` | **KPI Duration**: `{selected_num_days} Working Day(s)`")

                t_day_ld = teacher_date_data[teacher_date_data['Type'] == 'lessonDelivery']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
                t_day_lib = teacher_date_data[teacher_date_data['Type'] == 'library']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
                
                ld_pct = (t_day_ld / calc_ld_kpi) * 100 if calc_ld_kpi > 0 else (100.0 if t_day_ld >= 0 else 0)
                lib_pct = (t_day_lib / calc_lib_kpi) * 100 if calc_lib_kpi > 0 else (100.0 if t_day_lib >= 0 else 0)

                col_sum1, col_sum2 = st.columns([1, 1.2])

                with col_sum1:
                    st.markdown("##### 📌 KPI Overview")
                    s1, s2 = st.columns(2)
                    s1.metric("Lesson Prep Mins", f"{t_day_ld:.1f} mins", delta=f"{ld_pct:.0f}% of KPI")
                    s2.metric("Library Usage Mins", f"{t_day_lib:.1f} mins", delta=f"{lib_pct:.0f}% of KPI")
                    
                    st.markdown("##### 💡 Feedback & Recommendations")
                    if calc_ld_kpi == 0 and calc_lib_kpi == 0:
                        st.info(f"🏖️ **Rest Day**: {target_teacher} selected filter falls on an excluded Holiday.")
                    elif t_day_ld >= calc_ld_kpi and t_day_lib >= calc_lib_kpi:
                        st.success(f"👏 **Excellent Work**: {target_teacher} achieved all KPIs for this {selected_num_days}-working day period!")
                    else:
                        st.warning(f"💡 **Review Needed**: Check preparation and library metrics for coaching opportunities.")

                with col_sum2:
                    st.markdown("##### 📊 KPI Achievement Comparison")
                    ach_df = pd.DataFrame({
                        'KPI Category': [f'Lesson Prep ({calc_ld_kpi:.0f}m)', f'Library Usage ({calc_lib_kpi:.0f}m)'],
                        'Logged Minutes': [t_day_ld, t_day_lib],
                        'KPI Standard': [calc_ld_kpi, calc_lib_kpi]
                    })
                    
                    fig_ach = go.Figure()
                    fig_ach.add_trace(go.Bar(
                        x=ach_df['KPI Category'], y=ach_df['Logged Minutes'],
                        name='Logged Minutes', marker_color='#2CA02C', text=[f"{v:.1f} mins" for v in ach_df['Logged Minutes']], textposition='auto'
                    ))
                    fig_ach.add_trace(go.Bar(
                        x=ach_df['KPI Category'], y=ach_df['KPI Standard'],
                        name='KPI Standard', marker_color='#E5E5E5', opacity=0.6, text=[f"{v:.1f} mins" for v in ach_df['KPI Standard']], textposition='auto'
                    ))
                    fig_ach.update_layout(
                        barmode='group', title=f"Logged Minutes vs. KPI Standard ({selected_num_days} Working Day(s))",
                        height=280, margin=dict(l=20, r=20, t=40, b=20)
                    )
                    st.plotly_chart(fig_ach, use_container_width=True)

                st.markdown("---")

                st.subheader("2. Qualitative Evidences & Form Submissions Hub")
                st.caption("Review authentic teacher pre-class voice notes, in-class activity videos, and student writing samples uploaded via the Teacher Portal.")

                v_cols = st.columns(3)
                voice_links = teacher_date_data['Voice_Note_Link'].dropna().unique().tolist() if 'Voice_Note_Link' in teacher_date_data.columns else []
                v_cols[0].metric("🎧 Voice Notes Submitted", len(voice_links))
                
                video_cols_exist = [c for c in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3'] if c in teacher_date_data.columns]
                video_count = teacher_date_data[video_cols_exist].notna().sum().sum() if video_cols_exist and not teacher_date_data.empty else 0
                v_cols[1].metric("🎥 Classroom Videos Uploaded", video_count)

                writing_links = teacher_date_data['Writing_Sample_Link'].dropna().unique().tolist() if 'Writing_Sample_Link' in teacher_date_data.columns else []
                v_cols[2].metric("📝 Student Writing Artifacts", len(writing_links))

                with st.expander("🔍 View & Audit Submitted Artifact Links"):
                    q_cols1, q_cols2, q_cols3 = st.columns(3)
                    
                    with q_cols1:
                        st.markdown("##### 🎧 Daily Voice Notes")
                        if voice_links:
                            for idx, link in enumerate(voice_links, 1):
                                st.markdown(f"• [Voice Note #{idx}]({link})")
                        else:
                            st.caption("No voice notes uploaded.")

                    with q_cols2:
                        st.markdown("##### 🎥 Classroom Videos")
                        if video_count > 0:
                            for col in video_cols_exist:
                                v_list = teacher_date_data[col].dropna().unique().tolist()
                                for idx, link in enumerate(v_list, 1):
                                    st.markdown(f"• [{col} #{idx}]({link})")
                        else:
                            st.caption("No videos uploaded.")

                    with q_cols3:
                        st.markdown("##### 📝 Writing Samples")
                        if writing_links:
                            for idx, link in enumerate(writing_links, 1):
                                st.markdown(f"• [Writing Sample #{idx}]({link})")
                        else:
                            st.caption("No writing samples uploaded.")

                st.markdown("---")
                st.subheader(f"3. Granular Classroom Audit Log for {target_teacher}")
                t_log_cols = ['Date', 'Type', 'Grade', 'Subject', 'Book', 'StartTime', 'Duration_Min']
                t_avail_cols = [c for c in t_log_cols if c in teacher_date_data.columns]
                
                if not teacher_date_data.empty:
                    t_display_log = teacher_date_data[t_avail_cols].rename(columns={'Duration_Min': 'Minutes'}).sort_values(by='StartTime', ascending=False)
                    st.dataframe(t_display_log, use_container_width=True)

        # TAB 5: MANAGER PORTFOLIO & SCHOOL QUADRANTS
        with tab5:
            st.header("🏛️ Academic Manager Portfolio Overview")
            st.caption("High-level classification and Week-on-Week Velocity tracking across your school portfolio.")

            if school_filtered_df.empty:
                st.warning("No data available for the selected school filter.")
            else:
                school_stats = school_filtered_df.groupby(['Institution', 'Type'])['Duration_Min'].sum().unstack(fill_value=0.0).reset_index()
                
                if 'lessonDelivery' not in school_stats.columns: school_stats['lessonDelivery'] = 0.0
                if 'library' not in school_stats.columns: school_stats['library'] = 0.0
                
                school_roster_count = school_master_roster.groupby('Institution')['FullName'].nunique().reset_index().rename(columns={'FullName': 'Roster_Teachers'})
                school_stats = school_stats.merge(school_roster_count, on='Institution', how='left').fillna(1)

                school_stats['Avg_Lesson_Prep_Mins'] = (school_stats['lessonDelivery'] / school_stats['Roster_Teachers'] / selected_num_days).round(1)
                school_stats['Avg_Library_Usage_Mins'] = (school_stats['library'] / school_stats['Roster_Teachers'] / selected_num_days).round(1)

                def classify_school(row):
                    ld_ok = row['Avg_Lesson_Prep_Mins'] >= 10.0
                    lib_ok = row['Avg_Library_Usage_Mins'] >= 30.0
                    if ld_ok and lib_ok: return '🌟 Pace Setters'
                    elif ld_ok and not lib_ok: return '📘 Lesson Focused'
                    elif not ld_ok and lib_ok: return '📚 Library Focused'
                    else: return '🚨 Priority Focus'

                school_stats['Classification'] = school_stats.apply(classify_school, axis=1)

                st.subheader("📋 Complete School Performance Leaderboard")
                st.dataframe(school_stats, use_container_width=True)

        # TAB 6: SCHOOL-LEVEL TEACHER PROGRESSION
        with tab6:
            st.header("🏫 School-Level Teacher Progression & Execution Tiers")
            all_schools_list_t6 = sorted(school_master_roster['Institution'].unique()) if not school_master_roster.empty else []
            
            if not all_schools_list_t6:
                st.info("No schools found in roster.")
            else:
                target_school_t6 = st.selectbox("Select School to Inspect:", options=all_schools_list_t6)
                school_t6_roster = school_master_roster[school_master_roster['Institution'] == target_school_t6]
                school_t6_data = school_filtered_df[school_filtered_df['Institution'] == target_school_t6]

                st.markdown(f"### 🏫 School Audit: **{target_school_t6}** | Active Roster: **{len(school_t6_roster)} Teachers**")
                st.dataframe(school_t6_data, use_container_width=True)

        # TAB 7: STUDENT ASSESSMENT OUTCOMES
        with tab7:
            st.header("📊 Student Assessment Outcomes & Impact Analysis")
            if 'Assessment_Score_Pct' not in school_filtered_df.columns or school_filtered_df['Assessment_Score_Pct'].dropna().empty:
                st.info("👋 No student assessment score data uploaded yet.")
            else:
                assess_df = school_filtered_df.dropna(subset=['Assessment_Score_Pct'])
                st.metric("Average Assessment Score", f"{assess_df['Assessment_Score_Pct'].mean():.1f}%")
                st.dataframe(assess_df[['Institution', 'FullName', 'Grade', 'Subject', 'Assessment_Score_Pct']], use_container_width=True)

        # TAB 8: GLOBAL SUBMISSIONS AUDIT (NEW ADMIN TAB)
        with tab8:
            st.header("📥 Global School-Level Submissions Audit")
            st.markdown("Track total form submissions made by teachers broken down by school, matching your global sidebar filters.")

            if not school_filtered_df.empty:
                submission_summary = school_filtered_df.groupby(['Institution', 'FullName']).agg(
                    Total_Submissions=('FullName', 'count'),
                    Total_Logged_Mins=('Duration_Min', 'sum'),
                    Last_Activity_Date=('Date', 'max')
                ).reset_index().rename(columns={
                    'Institution': 'School Name',
                    'FullName': 'Teacher Name',
                    'Total_Submissions': 'Submissions Count',
                    'Total_Logged_Mins': 'Total Minutes',
                    'Last_Activity_Date': 'Last Active Date'
                })

                s_col1, s_col2, s_col3 = st.columns(3)
                s_col1.metric("Total Submissions Recorded", len(school_filtered_df))
                s_col2.metric("Active Submitting Teachers", submission_summary['Teacher Name'].nunique())
                s_col3.metric("Schools Represented", submission_summary['School Name'].nunique())

                st.markdown("---")
                st.subheader("📋 School Submissions Leaderboard")
                st.dataframe(submission_summary.sort_values(by='Submissions Count', ascending=False), use_container_width=True)

                csv_sub = submission_summary.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Submissions Audit (CSV)",
                    data=csv_sub,
                    file_name="Global_Teacher_Submissions_Audit.csv",
                    mime="text/csv"
                )
            else:
                st.info("No submission records found matching the active school filters.")

    # Active Master Database File Info
    st.sidebar.markdown("---")
    st.sidebar.subheader("🗄️ Active Master Database File")
    if os.path.exists(DB_PATH):
        st.sidebar.caption(f"📁 `master_database.parquet` ({len(df)} records)")
    else:
        st.sidebar.caption("No database file created yet.")
