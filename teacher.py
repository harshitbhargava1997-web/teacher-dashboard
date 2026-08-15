with st.form("standalone_teacher_form", clear_on_submit=True):
    st.subheader("1. School & Teacher Details")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        # Dynamically matches your portfolio school names
        sub_school = st.selectbox("Select School / Institution *", options=["Select School", "School Alpha", "School Beta", "School Gamma"])
        sub_firstname = st.text_input("Teacher First Name *")
    with col_s2:
        sub_date = st.date_input("Submission Date *")
        sub_lastname = st.text_input("Teacher Last Name *")

    st.subheader("2. Academic Details")
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        sub_grade = st.text_input("Grade (e.g., Grade 5) *")
    with col_a2:
        sub_subject = st.text_input("Subject *")
    with col_a3:
        sub_chapter = st.text_input("Lesson Plan / Chapter Number *")

    sub_lesson_title = st.text_input("Lesson Plan Topic / Title being Taught *")
    
    col_m1, col_m2 = st.columns(2)
    with col_m1:
        sub_type = st.selectbox("Primary Activity Type", options=["lessonDelivery", "library", "other"])
    with col_m2:
        sub_duration_mins = st.number_input("Session Duration (Minutes)", min_value=0.0, max_value=300.0, value=30.0, step=5.0)

    st.subheader("3. Qualitative Evidences & Artifact Hub")
    st.markdown("Provide URLs, cloud links, or reference paths for the following evidence types:")
    
    sub_voice = st.text_input("🎤 Lesson Plan Voice Note Link")
    sub_pic = st.text_input("🖼️ Lesson Plan Picture Link")
    
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        sub_vid1 = st.text_input("🎥 Classroom Activity Video Evidence 1")
        sub_vid2 = st.text_input("🎥 Classroom Activity Video Evidence 2")
    with col_v2:
        sub_vid3 = st.text_input("🎥 Classroom Activity Video Evidence 3")
        sub_writing = st.text_input("📝 Student Writing Samples Link")

    submitted = st.form_submit_button("🚀 Submit Teacher Log & Evidence")

    if submitted:
        if sub_school == "Select School":
            st.error("Please select a valid School Name.")
        elif not sub_firstname.strip() or not sub_lastname.strip():
            st.error("Please provide the Teacher's First and Last Name.")
        elif not sub_lesson_title.strip() or not sub_chapter.strip():
            st.error("Please fill in the Lesson Plan title and Chapter Number.")
        else:
            try:
                # Constructing the record mapping directly to your admin dashboard schema
                new_entry = pd.DataFrame([{
                    'FirstName': sub_firstname.strip(),
                    'LastName': sub_lastname.strip(),
                    'FullName': f"{sub_firstname.strip()} {sub_lastname.strip()}",
                    'Institution': sub_school,
                    'Grade': sub_grade.strip(),
                    'Subject': sub_subject.strip(),
                    'Book': f"Ch. {sub_chapter.strip()}: {sub_lesson_title.strip()}",
                    'Type': sub_type,
                    'Duration_Min': sub_duration_mins,
                    'Duration (HH:MM:SS)': f"00:{int(sub_duration_mins):02d}:00",
                    'StartTime': pd.to_datetime(sub_date),
                    'Voice_Note_Link': sub_voice.strip() if sub_voice else None,
                    'Lesson_Plan_Picture': sub_pic.strip() if sub_pic else None, # Mapped field
                    'Video_Evidence_1': sub_vid1.strip() if sub_vid1 else None,
                    'Video_Evidence_2': sub_vid2.strip() if sub_vid2 else None,
                    'Video_Evidence_3': sub_vid3.strip() if sub_vid3 else None,
                    'Writing_Sample_Link': sub_writing.strip() if sub_writing else None,
                    'Assessment_Score_Pct': None
                }])

                append_teacher_submission(new_entry)
                st.success(f"✅ Success! Data for {sub_firstname} {sub_lastname} has been logged and synced to the central admin dashboard.")
            except Exception as e:
                st.error(f"❌ Submission error: {e}")
