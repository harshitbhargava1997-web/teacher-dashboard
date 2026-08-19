import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import os
import glob
import re
import json
import urllib.parse
from io import BytesIO
from supabase import create_client

# Google GenAI SDK (Requires package 'google-genai')
from google import genai

# ReportLab PDF Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Page layout configuration (Must be the first Streamlit command)
st.set_page_config(page_title="Academic Manager Portfolio & Teacher Performance Indicator Review Dashboard", layout="wide")

# --- SUPABASE & GEMINI CLOUD SETUP ---
try:
    SUPABASE_URL = st.secrets["supabase"]["url"].rstrip('/')
    SUPABASE_KEY = st.secrets["supabase"]["key"]
    BUCKET_NAME = st.secrets["supabase"]["bucket_name"]
    PARQUET_FILE_NAME = "master_database.parquet"
    CRM_FILE_NAME = "school_crm_data.json"
    CALL_LOGS_FILE_NAME = "school_call_logs_store.json"
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"Supabase credentials missing or misconfigured in Streamlit Secrets: {e}")

# Initialize Gemini Client using google-genai SDK (Using Gemini 2.5 Flash)
try:
    GEMINI_API_KEY = st.secrets["gemini"]["api_key"]
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception:
    ai_client = None


def _norm_text(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _norm_key(value):
    return _norm_text(value).casefold()


def _sanitize_df_for_parquet(df_in):
    """Ensures consistent datatypes and handles datetime coercion for Parquet serialization."""
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

    # Fallback to prevent JSON submissions from being dropped by global sidebar filters
    out.loc[out["State_Zone"].eq(""), "State_Zone"] = "Madhya Pradesh (MP)"
    out.loc[out["Uploaded_By"].eq(""), "Uploaded_By"] = "Harshit Bhargava"

    calculated_full = (
        out["FirstName"].fillna("") + " " + out["LastName"].fillna("")
    ).map(_norm_text)
    empty_full = out["FullName"].eq("")
    out.loc[empty_full, "FullName"] = calculated_full.loc[empty_full]

    out.loc[out["FullName"].eq(""), "FullName"] = "Unknown Teacher"
    return out


# --- OPTIMIZED CACHED DATA FETCHER (Increased TTL to 300s & Set Submission Limit to 10,000) ---
@st.cache_data(ttl=300, show_spinner=False)
def fetch_master_db_from_supabase():
    """Reads base master parquet file AND merges isolated teacher JSON submissions efficiently."""
    base_df = pd.DataFrame()
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(PARQUET_FILE_NAME)
        if response:
            base_df = pd.read_parquet(BytesIO(response))
            base_df = _sanitize_df_for_parquet(base_df)
    except Exception:
        pass

    sub_records = []
    try:
        # Submission list limit set to 10,000 to ensure no teacher submission is ever cut off[cite: 4]
        file_list = supabase.storage.from_(BUCKET_NAME).list("submissions", {"limit": 10000})
        if file_list:
            for item in file_list:
                fname = item.get('name', '')
                if fname.endswith('.json'):
                    raw_data = supabase.storage.from_(BUCKET_NAME).download(f"submissions/{fname}")
                    if raw_data:
                        sub_records.append(json.loads(raw_data.decode('utf-8')))
    except Exception:
        pass

    if sub_records:
        subs_df = pd.DataFrame(sub_records)
        subs_df = _sanitize_df_for_parquet(subs_df)
        combined = pd.concat([base_df, subs_df], ignore_index=True) if not base_df.empty else subs_df
        return normalize_identity_columns(combined)

    return normalize_identity_columns(base_df) if not base_df.empty else base_df


# --- SUPABASE PERSISTENCE FOR CRM CONTACTS & CALL LOGS ---
def load_crm_data_from_supabase():
    """Loads saved global school contacts directory from Supabase cloud storage."""
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(CRM_FILE_NAME)
        if response:
            return json.loads(response.decode('utf-8'))
    except Exception:
        pass
    return {"contacts": {}}


def save_crm_data_to_supabase(crm_data):
    """Saves updated global school contacts directory back to Supabase cloud storage."""
    try:
        crm_buffer = BytesIO(json.dumps(crm_data, indent=2).encode('utf-8'))
        supabase.storage.from_(BUCKET_NAME).upload(
            path=CRM_FILE_NAME,
            file=crm_buffer.getvalue(),
            file_options={"upsert": "true", "content-type": "application/json"}
        )
    except Exception as e:
        st.error(f"Could not sync CRM data to Supabase: {e}")


def load_call_logs_from_supabase():
    """Loads persistent call discussion logs and notes from Supabase cloud storage."""
    try:
        response = supabase.storage.from_(BUCKET_NAME).download(CALL_LOGS_FILE_NAME)
        if response:
            return json.loads(response.decode('utf-8'))
    except Exception:
        pass
    return []


def save_call_logs_to_supabase(logs_list):
    """Saves updated call discussion logs back to Supabase cloud storage."""
    try:
        logs_buffer = BytesIO(json.dumps(logs_list, indent=2).encode('utf-8'))
        supabase.storage.from_(BUCKET_NAME).upload(
            path=CALL_LOGS_FILE_NAME,
            file=logs_buffer.getvalue(),
            file_options={"upsert": "true", "content-type": "application/json"}
        )
    except Exception as e:
        st.error(f"Could not sync call discussion logs to Supabase: {e}")


def upload_pdf_to_supabase(pdf_buffer, school_name):
    """Uploads a generated school audit PDF to Supabase storage and returns the public download URL."""
    try:
        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', school_name)
        remote_path = f"reports/{clean_name}_Comprehensive_Audit.pdf"
        
        supabase.storage.from_(BUCKET_NAME).upload(
            path=remote_path,
            file=pdf_buffer.getvalue(),
            file_options={"upsert": "true", "content-type": "application/pdf"}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{remote_path}"
        return public_url
    except Exception:
        return None


def build_teacher_roster(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["Institution", "Center", "FirstName", "LastName", "FullName", "Role", "Uploaded_By", "State_Zone"])

    roster = normalize_identity_columns(df)

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

    candidate["_institution_key"] = candidate["Institution"].map(_norm_key)
    candidate["_teacher_key"] = candidate["FullName"].map(_norm_key)
    candidate = candidate.drop_duplicates(
        subset=["_institution_key", "_teacher_key"], keep="last"
    ).sort_values(["Institution", "FullName"], kind="stable")

    return candidate.reset_index(drop=True)


# --- AI HELPER FUNCTIONS (GEMINI MULTIMODAL INTEGRATION) ---
def get_gemini_summary(context_prompt, audio_file_obj=None):
    """Sends prompt and optional audio recording directly to Gemini 2.5 Flash."""
    if not ai_client:
        return "⚠️ Gemini API key not found in Streamlit secrets."
    try:
        contents_payload = [context_prompt]
        
        if audio_file_obj is not None:
            audio_bytes = audio_file_obj.read()
            contents_payload.append(
                genai.types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type="audio/wav"
                )
            )

        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents_payload
        )
        return response.text
    except Exception as e:
        return f"AI Generation Notice: {e}"


def render_universal_crm_box(tab_name, active_selected_schools, current_filter_description, metrics_summary_text):
    """Universal CRM with robust contact synchronization, Voice & Text AI generator, and WhatsApp template."""
    st.markdown("---")
    st.subheader(f"📞 Universal School & Coordinator CRM, Call Notes & WhatsApp Generators ({tab_name})")
    
    if "crm_global_data" not in st.session_state:
        st.session_state["crm_global_data"] = load_crm_data_from_supabase()

    if "crm_call_logs_store" not in st.session_state:
        st.session_state["crm_call_logs_store"] = load_call_logs_from_supabase()

    crm_data = st.session_state["crm_global_data"]
    if "contacts" not in crm_data:
        crm_data["contacts"] = {}

    c_col1, c_col2 = st.columns([1, 2])
    with c_col1:
        if isinstance(active_selected_schools, str):
            schools_list = [active_selected_schools]
        elif isinstance(active_selected_schools, (list, tuple, pd.Series, np.ndarray)):
            schools_list = [str(s) for s in active_selected_schools if str(s).strip()]
        else:
            schools_list = ["Default School"]
            
        if not schools_list:
            schools_list = ["Default School"]

        target_crm_school = st.selectbox("Select School:", options=schools_list, key=f"crm_school_{tab_name}")
        
        if target_crm_school not in crm_data["contacts"]:
            crm_data["contacts"][target_crm_school] = {
                "Principal": {"name": "", "phone": ""},
                "Owner": {"name": "", "phone": ""},
                "Coordinator": {"name": "", "phone": ""}
            }

        st.markdown("##### 👥 Select Entity & Contact Details")
        selected_entity_type = st.selectbox("Target Entity Type:", options=["Principal", "Owner", "Coordinator"], key=f"entity_type_{tab_name}")
        
        current_entity_data = crm_data["contacts"][target_crm_school].get(selected_entity_type, {"name": "", "phone": ""})
        
        input_contact_name = st.text_input(f"{selected_entity_type} Name:", value=current_entity_data.get("name", ""), key=f"cname_{tab_name}_{target_crm_school}_{selected_entity_type}")
        input_phone = st.text_input(f"{selected_entity_type} Mobile (+91...):", value=current_entity_data.get("phone", ""), key=f"cphone_{tab_name}_{target_crm_school}_{selected_entity_type}")

        if st.button(f"💾 Save {selected_entity_type} Contact to Supabase", key=f"save_contact_btn_{tab_name}_{target_crm_school}_{selected_entity_type}"):
            crm_data["contacts"][target_crm_school][selected_entity_type] = {
                "name": input_contact_name,
                "phone": input_phone
            }
            save_crm_data_to_supabase(crm_data)
            st.success(f"Successfully saved {selected_entity_type} details for {target_crm_school} to Supabase!")

        active_phone = input_phone.strip()
        if active_phone:
            clean_phone = re.sub(r'[^0-9+]', '', active_phone)
            contact_greeting = input_contact_name if input_contact_name else selected_entity_type
            quick_wa = urllib.parse.quote(f"Namaste {contact_greeting} ji, checking in from Onelearn Academic Team regarding {tab_name} metrics for {target_crm_school} - {current_filter_description}.")
            st.markdown(f'<a href="tel:{active_phone}" target="_blank" style="text-decoration:none;"><button style="background-color:#2CA02C;color:white;padding:8px 14px;border:none;border-radius:4px;cursor:pointer;font-weight:bold;margin-bottom:6px;width:100%;">📞 Call {selected_entity_type}</button></a>', unsafe_allow_html=True)
            st.markdown(f'<a href="https://wa.me/{clean_phone}?text={quick_wa}" target="_blank" style="text-decoration:none;"><button style="background-color:#25D366;color:white;padding:8px 14px;border:none;border-radius:4px;cursor:pointer;font-weight:bold;width:100%;">📱 Quick WhatsApp Message</button></a>', unsafe_allow_html=True)
        else:
            st.warning(f"Please enter and save a mobile number for the selected {selected_entity_type}.")

    with c_col2:
        st.markdown("##### 💬 WhatsApp & Calling Generators (Indian Context)")
        
        custom_tone = st.selectbox("Select Message Tone:", ["Encouraging & Supportive", "Constructive & Corrective", "Executive Summary"], key=f"tone_{tab_name}")
        
        with st.expander("✨ AI-Driven Calling Script & Smart Message Generator (Voice & Text)"):
            
            manager_voice_audio = st.audio_input(
                "🎙️ Record Voice Instructions (Speak your custom prompt):",
                key=f"voice_input_{tab_name}_{target_crm_school}"
            )
            
            user_custom_instruction = st.text_area(
                "Or Type Custom Instructions (Alternative to voice):",
                placeholder="e.g., Focus heavily on improving library engagement and phonics submissions...",
                key=f"ai_custom_prompt_{tab_name}_{target_crm_school}"
            )
            
            if st.button("Generate AI Script & Message", key=f"gen_ai_both_{tab_name}"):
                if not ai_client:
                    st.error("Gemini API client is not initialized.")
                else:
                    ai_prompt = f"""
                    You are an expert Academic Consultant. 
                    Based on these detailed filtered metrics for {tab_name} at {target_crm_school} ({current_filter_description}):
                    Metrics & Breakdown: {metrics_summary_text}
                    Target Entity: {selected_entity_type} named {input_contact_name or 'Sir/Madam'}
                    Tone: {custom_tone}
                    Text Instructions Provided: {user_custom_instruction if user_custom_instruction else 'None'}
                    
                    Generate two distinct outputs:
                    1. **Calling Script**: A structured phone conversation script calling out specific teacher data points, praises, and areas of concern to discuss with this {selected_entity_type}.
                    2. **AI WhatsApp Follow-up Message**: A concise, professional message summarizing these exact findings and action items to send on WhatsApp afterward. Sign off with 'Onelearn Academic Team'.
                    """
                    with st.spinner("Processing voice/text instructions with Gemini..."):
                        try:
                            ai_result = get_gemini_summary(ai_prompt, audio_file_obj=manager_voice_audio)
                            st.session_state[f"ai_gen_output_{tab_name}_{target_crm_school}"] = ai_result
                        except Exception as e:
                            st.error(f"Error generating AI content: {e}")
            
            if f"ai_gen_output_{tab_name}_{target_crm_school}" in st.session_state:
                st.markdown(st.session_state[f"ai_gen_output_{tab_name}_{target_crm_school}"])

        st.markdown("##### 📝 Quick WhatsApp Message Draft (Standard Template)")
        
        draft_state_key = f"wa_draft_text_{tab_name}_{target_crm_school}_{selected_entity_type}"
        name_prefix = f" {input_contact_name}" if input_contact_name and input_contact_name.strip() else ""
        
        default_template_string = (
            f"Dear {name_prefix} ji,\n\n"
            f"Here is the performance update for {target_crm_school} - {current_filter_description}:\n\n"
            f"📊 *Module:* {tab_name}\n"
            f"{metrics_summary_text}\n\n"
            f"Regards,\n"
            f"Harshit Bhargava,\n"
            f"Onelern Academic Team"
        )

        if draft_state_key not in st.session_state or st.session_state.get(f"last_name_{tab_name}_{target_crm_school}") != input_contact_name:
            st.session_state[draft_state_key] = default_template_string
            st.session_state[f"last_name_{tab_name}_{target_crm_school}"] = input_contact_name

        editable_wa_area = st.text_area("Confirm or Edit Final WhatsApp Message Draft:", value=st.session_state[draft_state_key], height=140, key=f"wa_textarea_{tab_name}_{target_crm_school}_{selected_entity_type}")
        st.session_state[draft_state_key] = editable_wa_area

        if active_phone:
            clean_phone = re.sub(r'[^0-9+]', '', active_phone)
            encoded_final_text = urllib.parse.quote(editable_wa_area)
            st.markdown(f'<a href="https://wa.me/{clean_phone}?text={encoded_final_text}" target="_blank" style="text-decoration:none;"><button style="background-color:#25D366;color:white;padding:10px 18px;border:none;border-radius:4px;cursor:pointer;font-weight:bold;width:100%;">🚀 Send Final WhatsApp Message</button></a>', unsafe_allow_html=True)

    # --- CALL DISCUSSION NOTES & FOLLOW-UP SYNC TO SUPABASE ---
    st.markdown("---")
    st.markdown(f"##### 📝 Post-Call Discussion Notes & Follow-up Scheduler ({target_crm_school} - {selected_entity_type})")
    
    with st.form(key=f"call_log_form_{tab_name}_{target_crm_school}_{selected_entity_type}"):
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            call_date_punched = st.date_input("Call Conducted Date:", value=pd.Timestamp.now().date(), key=f"cdate_{tab_name}")
        with col_f2:
            next_followup_date = st.date_input("Next Scheduled Follow-up Date:", value=pd.Timestamp.now().date() + pd.Timedelta(days=7), key=f"fdate_{tab_name}")
            
        discussion_notes = st.text_area("Discussion Summary / Notes from Call:", placeholder="Punch key talking points, agreed commitments, and action items...", key=f"dnotes_{tab_name}")
        call_status_opt = st.selectbox("Call Status / Resolution:", options=["Open Action Item", "In Progress", "Successfully Resolved"], key=f"cstat_{tab_name}")
        
        submit_call_log = st.form_submit_button("💾 Save Call Note & Sync to Supabase Cloud")
        
        if submit_call_log:
            if discussion_notes.strip():
                new_log_entry = {
                    "School": target_crm_school,
                    "Entity Type": selected_entity_type,
                    "Contact Name": input_contact_name or "N/A",
                    "Module Tab": tab_name,
                    "Filter Window": current_filter_description,
                    "Call Date": str(call_date_punched),
                    "Discussion Notes": discussion_notes.strip(),
                    "Next Follow-up Date": str(next_followup_date),
                    "Status": call_status_opt
                }
                st.session_state["crm_call_logs_store"].append(new_log_entry)
                save_call_logs_to_supabase(st.session_state["crm_call_logs_store"])
                st.success("✅ Call notes and follow-up schedule successfully saved and synced to Supabase Cloud!")
            else:
                st.warning("Please enter discussion notes before saving.")

    if st.session_state["crm_call_logs_store"]:
        st.markdown("##### 📊 Filterable Call Discussion Logs & Audit Trail (Respects Active Global Filters)")
        logs_df = pd.DataFrame(st.session_state["crm_call_logs_store"])
        
        # Apply global school scope strictly to logs
        if isinstance(active_selected_schools, (list, tuple, pd.Series, np.ndarray)) and len(active_selected_schools) > 0:
            allowed_schools = [str(s) for s in active_selected_schools]
            if 'School' in logs_df.columns:
                logs_df = logs_df[logs_df['School'].isin(allowed_schools)]

        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            avail_log_schs = sorted(logs_df['School'].unique().tolist()) if 'School' in logs_df.columns else []
            log_school_filter = st.selectbox("Filter Logs by School:", options=["All Filtered Schools"] + avail_log_schs, key=f"log_sch_filt_{tab_name}")
        with f_col2:
            log_entity_filter = st.selectbox("Filter by Entity:", options=["All Entities"] + sorted(logs_df['Entity Type'].unique().tolist() if 'Entity Type' in logs_df.columns else []), key=f"log_ent_filt_{tab_name}")
        with f_col3:
            log_time_filter = st.selectbox("Filter by Period:", options=["All Time", "Today / Recent", "Upcoming Follow-ups"], key=f"log_time_filt_{tab_name}")

        filtered_logs_df = logs_df.copy()
        if log_school_filter != "All Filtered Schools" and 'School' in filtered_logs_df.columns:
            filtered_logs_df = filtered_logs_df[filtered_logs_df['School'] == log_school_filter]
        if log_entity_filter != "All Entities" and 'Entity Type' in filtered_logs_df.columns:
            filtered_logs_df = filtered_logs_df[filtered_logs_df['Entity Type'] == log_entity_filter]
            
        if not filtered_logs_df.empty:
            desired_cols = ['School', 'Entity Type', 'Contact Name', 'Module Tab', 'Filter Window', 'Call Date', 'Discussion Notes', 'Next Follow-up Date', 'Status']
            available_log_cols = [c for c in desired_cols if c in filtered_logs_df.columns]
            
            st.dataframe(filtered_logs_df[available_log_cols], use_container_width=True)
            
            dl_col1, dl_col2 = st.columns(2)
            with dl_col1:
                output_buffer = BytesIO()
                with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
                    filtered_logs_df[available_log_cols].to_excel(writer, index=False, sheet_name='Call_Discussion_Logs')
                output_buffer.seek(0)
                
                st.download_button(
                    label="📥 Download Filtered Call Logs (Excel)",
                    data=output_buffer,
                    file_name=f"School_CRM_Call_Logs_{target_crm_school.replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_excel_{tab_name}"
                )
            with dl_col2:
                if st.button("🗑️ Clear All Saved Call Logs from Supabase", key=f"clear_logs_btn_{tab_name}"):
                    st.session_state["crm_call_logs_store"] = []
                    save_call_logs_to_supabase([])
                    st.success("Successfully cleared all call logs from Supabase!")
                    st.rerun()
        else:
            st.info("No call logs match the selected filter criteria.")


# --- PDF REPORT GENERATOR HELPERS ---
def generate_pdf_report(title_text, subtitle_text, school_name, summary_metrics, dataframe=None, custom_sections=None):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor('#2563EB')
    dark_neutral = colors.HexColor('#1E293B')
    light_bg = colors.HexColor('#F8FAFC')
    border_color = colors.HexColor('#E2E8F0')
    accent_color = colors.HexColor('#0F172A')

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=16, leading=20, textColor=primary_color, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9, leading=13, textColor=dark_neutral)
    school_style = ParagraphStyle('SchoolHead', parent=styles['Normal'], fontSize=10, leading=14, textColor=accent_color, fontName='Helvetica-Bold')
    sec_head_style = ParagraphStyle('SecHead', parent=styles['Heading2'], fontSize=11, leading=15, textColor=primary_color, fontName='Helvetica-Bold', spaceBefore=12, spaceAfter=5)
    normal_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=8.5, leading=13, textColor=dark_neutral)
    link_style = ParagraphStyle('LinkStyle', parent=styles['Normal'], fontSize=8, leading=11, textColor=colors.HexColor('#2563EB'), fontName='Helvetica-Bold')
    card_header = ParagraphStyle('CardHead', parent=styles['Normal'], fontSize=7.5, leading=10, textColor=colors.HexColor('#64748B'), fontName='Helvetica-Bold', alignment=1)
    card_value = ParagraphStyle('CardVal', parent=styles['Normal'], fontSize=11, leading=14, textColor=primary_color, fontName='Helvetica-Bold', alignment=1)
    
    story.append(Paragraph(f"<b>{title_text}</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"🏫 <b>Institution / School Focus:</b> {school_name}", school_style))
    story.append(Spacer(1, 3))
    story.append(Paragraph(subtitle_text, subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=12))

    if summary_metrics:
        headers_row = [Paragraph(k, card_header) for k in summary_metrics.keys()]
        values_row = [Paragraph(str(v), card_value) for v in summary_metrics.values()]
        col_w = 540 / len(summary_metrics)
        kpi_table = Table([headers_row, values_row], colWidths=[col_w] * len(summary_metrics))
        kpi_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), light_bg),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, border_color),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 12))

    if custom_sections:
        for heading, body_items in custom_sections.items():
            story.append(Paragraph(f"<b>{heading}</b>", sec_head_style))
            story.append(HRFlowable(width="100%", thickness=0.5, color=border_color, spaceAfter=6))
            for item in body_items:
                if "<a href=" in item:
                    story.append(Paragraph(f"{item}", link_style))
                else:
                    story.append(Paragraph(f"• {item}", normal_style))
            story.append(Spacer(1, 10))

    if dataframe is not None and not dataframe.empty:
        story.append(Spacer(1, 4))
        raw_data = [dataframe.columns.tolist()] + dataframe.astype(str).values.tolist()
        cell_style = ParagraphStyle('TableCell', parent=styles['Normal'], fontSize=8, leading=12, textColor=dark_neutral)
        header_style = ParagraphStyle('TableHeader', parent=styles['Normal'], fontSize=8.5, leading=12, textColor=colors.white, fontName='Helvetica-Bold')

        formatted_data = []
        for i, row in enumerate(raw_data):
            formatted_row = []
            for cell in row:
                st_to_use = header_style if i == 0 else cell_style
                formatted_row.append(Paragraph(str(cell), st_to_use))
            formatted_data.append(formatted_row)

        num_cols = len(dataframe.columns)
        col_width = 540 / num_cols

        pdf_table = Table(formatted_data, colWidths=[col_width] * num_cols, repeatRows=1)
        pdf_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.4, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(pdf_table)

    doc.build(story)
    buffer.seek(0)
    return buffer


def generate_comprehensive_school_pdf_report(school_name, teachers_list, school_filtered_df, filtered_df, filter_desc, calc_ld_kpi, calc_lib_kpi, daily_ld_target, daily_lib_target, selected_num_days, target_vid_count=3, target_writing_count=3, target_lp_combo_count=3, target_phonics_count=2, target_portfolio_count=1, enable_quant_kpi=True, enable_qual_kpi=True):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor('#2563EB')
    dark_neutral = colors.HexColor('#1E293B')
    light_bg = colors.HexColor('#F8FAFC')
    border_color = colors.HexColor('#E2E8F0')
    accent_color = colors.HexColor('#0F172A')

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=16, leading=20, textColor=primary_color, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9, leading=13, textColor=dark_neutral)
    school_style = ParagraphStyle('SchoolHead', parent=styles['Normal'], fontSize=10, leading=14, textColor=accent_color, fontName='Helvetica-Bold')
    sec_head_style = ParagraphStyle('SecHead', parent=styles['Heading2'], fontSize=11, leading=15, textColor=primary_color, fontName='Helvetica-Bold', spaceBefore=12, spaceAfter=5)
    normal_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=8.5, leading=13, textColor=dark_neutral)
    link_style = ParagraphStyle('LinkStyle', parent=styles['Normal'], fontSize=8, leading=11, textColor=colors.HexColor('#2563EB'), fontName='Helvetica-Bold')
    card_header = ParagraphStyle('CardHead', parent=styles['Normal'], fontSize=7.5, leading=10, textColor=colors.HexColor('#64748B'), fontName='Helvetica-Bold', alignment=1)
    card_value = ParagraphStyle('CardVal', parent=styles['Normal'], fontSize=11, leading=14, textColor=primary_color, fontName='Helvetica-Bold', alignment=1)

    school_curr_df = filtered_df[filtered_df['Institution'] == school_name]

    # PART 1: CONSOLIDATED TABLES
    story.append(Paragraph(f"<b>Comprehensive School Audit & Feature-Wise Report</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"<b>Institution / School Focus:</b> {school_name}", school_style))
    story.append(Spacer(1, 3))
    story.append(Paragraph(f"Observation Window: {filter_desc}", subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=12))

    ld_df = school_curr_df[school_curr_df['Type'] == 'lessonDelivery']
    ld_usage = ld_df.groupby('FullName')['Duration_Min'].sum().reset_index()
    
    lib_df = school_curr_df[school_curr_df['Type'] == 'library']
    lib_usage = lib_df.groupby('FullName')['Duration_Min'].sum().reset_index()

    total_teachers_count = len(teachers_list)
    met_ld_count = 0
    met_lib_count = 0

    for t_name in teachers_list:
        t_ld = ld_usage[ld_usage['FullName'] == t_name]['Duration_Min'].values[0] if not ld_usage[ld_usage['FullName'] == t_name].empty else 0.0
        t_lib = lib_usage[lib_usage['FullName'] == t_name]['Duration_Min'].values[0] if not lib_usage[lib_usage['FullName'] == t_name].empty else 0.0
        if (calc_ld_kpi > 0 and t_ld >= calc_ld_kpi) or (calc_ld_kpi == 0 and t_ld > 0):
            met_ld_count += 1
        if (calc_lib_kpi > 0 and t_lib >= calc_lib_kpi) or (calc_lib_kpi == 0 and t_lib > 0):
            met_lib_count += 1

    school_summary_metrics = {
        "Active Roster Teachers": total_teachers_count,
        "Working Days Evaluated": f"{selected_num_days} Days"
    }
    if enable_quant_kpi:
        school_summary_metrics["Met Lesson Prep KPI"] = f"{met_ld_count} / {total_teachers_count}"
        school_summary_metrics["Met Library KPI"] = f"{met_lib_count} / {total_teachers_count}"

    headers_row = [Paragraph(k, card_header) for k in school_summary_metrics.keys()]
    values_row = [Paragraph(str(v), card_value) for v in school_summary_metrics.values()]
    col_w = 540 / len(school_summary_metrics)
    kpi_table = Table([headers_row, values_row], colWidths=[col_w] * len(school_summary_metrics))
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), light_bg),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, border_color),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 10))

    if enable_quant_kpi:
        story.append(Paragraph("<b>School-Level Feature Performance Summary & Guidelines</b>", sec_head_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=border_color, spaceAfter=6))
        story.append(Paragraph(f"• <b>Lesson Plan Performance Standard:</b> {daily_ld_target:.0f} mins/day × {selected_num_days} working days ({calc_ld_kpi:.0f} mins total benchmark standard)", normal_style))
        story.append(Paragraph(f"• <b>Library Usage Performance Standard:</b> {daily_lib_target:.0f} mins/day × {selected_num_days} working days ({calc_lib_kpi:.0f} mins total benchmark standard)", normal_style))
        story.append(Spacer(1, 10))

    # 1. Lesson Plan Preparation Consolidated Report
    story.append(Paragraph("<b>1. Lesson Plan Preparation Consolidated Report</b>", sec_head_style))
    ld_summary_table_data = [["Teacher Name", "Total Minutes Logged", "Average Mins/Day", "Performance Indicator Status"]]
    for t_name in teachers_list:
        t_mins = ld_usage[ld_usage['FullName'] == t_name]['Duration_Min'].values[0] if not ld_usage[ld_usage['FullName'] == t_name].empty else 0.0
        t_avg = t_mins / selected_num_days if selected_num_days > 0 else 0.0
        if not enable_quant_kpi or calc_ld_kpi == 0:
            t_stat = "Activity Logged" if t_mins > 0 else "No Activity Logged"
        elif t_mins >= calc_ld_kpi:
            t_stat = f"Met Performance Indicator (>= {calc_ld_kpi:.0f}m)"
        elif t_mins > 0.0:
            t_stat = f"Below Performance Indicator (< {calc_ld_kpi:.0f}m)"
        else:
            t_stat = "Inactive (0 Mins)"
        ld_summary_table_data.append([t_name, f"{t_mins:.1f}m", f"{t_avg:.1f}m/day", t_stat])

    ld_table_obj = Table(ld_summary_table_data, colWidths=[140, 110, 100, 190])
    ld_table_obj.setStyle(TableStyle([
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
    story.append(ld_table_obj)
    story.append(Spacer(1, 14))

    # 2. Library Usage Consolidated Report
    story.append(Paragraph("<b>2. Library Usage Consolidated Report</b>", sec_head_style))
    lib_summary_table_data = [["Teacher Name", "Total Minutes Logged", "Average Mins/Day", "Performance Indicator Status"]]
    for t_name in teachers_list:
        t_lib_mins = lib_usage[lib_usage['FullName'] == t_name]['Duration_Min'].values[0] if not lib_usage[lib_usage['FullName'] == t_name].empty else 0.0
        t_lib_avg = t_lib_mins / selected_num_days if selected_num_days > 0 else 0.0
        if not enable_quant_kpi or calc_lib_kpi == 0:
            t_lib_stat = "Activity Logged" if t_lib_mins > 0 else "No Activity Logged"
        elif t_lib_mins >= calc_lib_kpi:
            t_lib_stat = f"Met Performance Indicator (>= {calc_lib_kpi:.0f}m)"
        elif t_lib_mins > 0.0:
            t_lib_stat = f"Below Performance Indicator (< {calc_lib_kpi:.0f}m)"
        else:
            t_lib_stat = "Inactive (0 Mins)"
        lib_summary_table_data.append([t_name, f"{t_lib_mins:.1f}m", f"{t_lib_avg:.1f}m/day", t_lib_stat])

    lib_table_obj = Table(lib_summary_table_data, colWidths=[140, 110, 100, 190])
    lib_table_obj.setStyle(TableStyle([
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
    story.append(lib_table_obj)
    story.append(Spacer(1, 14))

    # 3. Qualitative Classroom Evidence Submissions Consolidated Table (Only if enabled)
    if enable_qual_kpi:
        story.append(Paragraph("<b>3. Qualitative Submissions & Evidence Compliance</b>", sec_head_style))
        qual_summary_table_data = [["Teacher Name", "LP / Audio Notes", "Activity Videos", "Writing Samples", "Phonics Evidences", "Portfolio Artifacts", "Status"]]
        
        for t_name in teachers_list:
            sub_t = school_curr_df[school_curr_df['FullName'] == t_name]
            v_cnt = sum([len([l for l in sub_t[col].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) for col in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3'] if col in sub_t.columns])
            w_cnt = len([l for l in sub_t['Writing_Sample_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Writing_Sample_Link' in sub_t.columns else 0
            lp_cnt = len([l for l in sub_t['Lesson_Plan_Picture'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in sub_t.columns else 0
            vn_cnt = len([l for l in sub_t['Voice_Note_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in sub_t.columns else 0
            ph_cnt = len([l for l in sub_t['Phonics_Evidence_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Phonics_Evidence_Link' in sub_t.columns else 0
            pf_cnt = len([l for l in sub_t['Portfolio_Evidence_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Portfolio_Evidence_Link' in sub_t.columns else 0
            
            is_q_ok = (v_cnt >= target_vid_count and w_cnt >= target_writing_count and (lp_cnt + vn_cnt) >= target_lp_combo_count and ph_cnt >= target_phonics_count and pf_cnt >= target_portfolio_count)
            q_stat = "Met Standard" if is_q_ok else "In Progress"
            qual_summary_table_data.append([t_name, str(lp_cnt + vn_cnt), str(v_cnt), str(w_cnt), str(ph_cnt), str(pf_cnt), q_stat])

        qual_table_obj = Table(qual_summary_table_data, colWidths=[130, 80, 70, 70, 75, 75, 40])
        qual_table_obj.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('GRID', (0, 0), (-1, -1), 0.4, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(qual_table_obj)
        story.append(Spacer(1, 12))

    # PART 2: INDIVIDUAL TEACHER 360° PROFILES (WITH WORKING CLICKABLE HYPERLINKS)
    for target_teacher in teachers_list:
        story.append(PageBreak())

        teacher_all_data = school_filtered_df[(school_filtered_df['FullName'] == target_teacher) & (school_filtered_df['Institution'] == school_name)]
        teacher_date_data = school_curr_df[school_curr_df['FullName'] == target_teacher]

        t_day_ld = teacher_date_data[teacher_date_data['Type'] == 'lessonDelivery']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
        t_day_lib = teacher_date_data[teacher_date_data['Type'] == 'library']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
        
        ld_pct = (t_day_ld / calc_ld_kpi) * 100 if calc_ld_kpi > 0 else (100.0 if t_day_ld >= 0 else 0)
        lib_pct = (t_day_lib / calc_lib_kpi) * 100 if calc_lib_kpi > 0 else (100.0 if t_day_lib >= 0 else 0)

        ld_advice = f"Steady Execution ({t_day_ld:.1f}m logged)" if (calc_ld_kpi > 0 and t_day_ld >= calc_ld_kpi) else (f"In-Progress ({t_day_ld:.1f}m logged)" if t_day_ld > 0 else "Pending Activity")
        lib_advice = f"Steady Execution ({t_day_lib:.1f}m logged)" if (calc_lib_kpi > 0 and t_day_lib >= calc_lib_kpi) else (f"In-Progress ({t_day_lib:.1f}m logged)" if t_day_lib > 0 else "Pending Activity")

        t_books_raw = teacher_date_data[teacher_date_data['Book'].str.len() > 0]
        if t_books_raw.empty:
            t_books_raw = teacher_all_data[teacher_all_data['Book'].str.len() > 0]
        teacher_books = t_books_raw[~t_books_raw['Book'].str.match(r'^Lesson Plan', case=False, na=False)]

        evidence_source = teacher_date_data if not teacher_date_data.empty else teacher_all_data

        def extract_evidence_items(df_src, col_name):
            if col_name not in df_src.columns:
                return []
            items = []
            for _, r in df_src.iterrows():
                val = str(r[col_name]).strip()
                if re.match(r'^https?://', val, re.IGNORECASE):
                    d_str = str(r['Date']) if 'Date' in r and pd.notna(r['Date']) else "Recent"
                    g_str = f"Grade {r['Grade']}" if 'Grade' in r and str(r['Grade']).strip() else "Grade N/A"
                    s_str = str(r['Subject']).strip() if 'Subject' in r and str(r['Subject']).strip() else "General Subject"
                    b_str = str(r['Book']).strip() if 'Book' in r and str(r['Book']).strip() else "Activity Lesson"
                    items.append({'url': val, 'date': d_str, 'grade': g_str, 'subject': s_str, 'lesson': b_str})
            seen = set()
            deduped = []
            for item in items:
                if item['url'] not in seen:
                    seen.add(item['url'])
                    deduped.append(item)
            return deduped

        v_voice = extract_evidence_items(evidence_source, 'Voice_Note_Link')
        v_pic = extract_evidence_items(evidence_source, 'Lesson_Plan_Picture')
        v_writing = extract_evidence_items(evidence_source, 'Writing_Sample_Link')
        v_phonics = extract_evidence_items(evidence_source, 'Phonics_Evidence_Link')
        v_portfolio = extract_evidence_items(evidence_source, 'Portfolio_Evidence_Link')

        v_vid = []
        for col in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
            v_vid.extend(extract_evidence_items(evidence_source, col))
        seen_v = set()
        deduped_v = []
        for item in v_vid:
            if item['url'] not in seen_v:
                seen_v.add(item['url'])
                deduped_v.append(item)
        v_vid = deduped_v

        lp_combo_total = len(v_voice) + len(v_pic)
        total_artifacts = lp_combo_total + len(v_vid) + len(v_writing) + len(v_phonics) + len(v_portfolio)

        pdf_book_items = []
        if not teacher_books.empty:
            b_summary_df = teacher_books.groupby(['Book', 'Grade', 'Subject'])['Duration_Min'].sum().reset_index()
            for _, br in b_summary_df.iterrows():
                pdf_book_items.append(f"Book: {br['Book']} ({br['Grade']} - {br['Subject']}) | Time Spent: {br['Duration_Min']:.1f} Mins")
        else:
            pdf_book_items.append("No textbooks or digital modules opened.")

        # Clean, Masked Hyperlinks
        pdf_link_items = []
        for i, item in enumerate(v_voice, 1): 
            pdf_link_items.append(f'• 🎧 <a href="{item["url"]}"><u><b>Open Voice Reflection #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
        for i, item in enumerate(v_pic, 1): 
            pdf_link_items.append(f'• 🖼️ <a href="{item["url"]}"><u><b>View Lesson Plan Photo #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
        for i, item in enumerate(v_vid, 1): 
            pdf_link_items.append(f'• 🎥 <a href="{item["url"]}"><u><b>Watch Classroom Activity Video #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
        for i, item in enumerate(v_writing, 1): 
            pdf_link_items.append(f'• 📝 <a href="{item["url"]}"><u><b>View Student Writing Sample #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
        for i, item in enumerate(v_phonics, 1): 
            pdf_link_items.append(f'• 🔤 <a href="{item["url"]}"><u><b>Open Phonics Evidence #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
        for i, item in enumerate(v_portfolio, 1): 
            pdf_link_items.append(f'• 📁 <a href="{item["url"]}"><u><b>View Teacher Portfolio Showcase #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')

        story.append(Paragraph(f"<b>Academic Performance Profile: {target_teacher}</b>", title_style))
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"<b>Institution / School Focus:</b> {school_name}", school_style))
        story.append(Spacer(1, 3))
        story.append(Paragraph(f"Observation Window: {filter_desc}", subtitle_style))
        story.append(Spacer(1, 6))
        story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

        summary_metrics = {
            "Teacher": target_teacher,
            "Lesson Prep": f"{t_day_ld:.1f}m",
            "Library Usage": f"{t_day_lib:.1f}m",
            "Phonics/Portfolio": f"{len(v_phonics)} / {len(v_portfolio)}",
            "Activity Submissions": f"{total_artifacts}"
        }
        headers_row = [Paragraph(k, card_header) for k in summary_metrics.keys()]
        values_row = [Paragraph(str(v), card_value) for v in summary_metrics.values()]
        col_w = 540 / len(summary_metrics)
        kpi_table = Table([headers_row, values_row], colWidths=[col_w] * len(summary_metrics))
        kpi_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), light_bg),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, border_color),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 10))

        sections = {
            "1. Lesson Preparation, Lesson Delivery, and Library Usage": [
                f"Lesson Preparation Duration: {t_day_ld:.1f} Minutes" + (f" ({ld_pct:.0f}% of Academic Benchmark)" if enable_quant_kpi else ""),
                f"Library & Digital Resources Duration: {t_day_lib:.1f} Minutes" + (f" ({lib_pct:.0f}% of Academic Benchmark)" if enable_quant_kpi else ""),
                f"Consultant Assessment: {ld_advice} in lesson preparation, {lib_advice} in library integration."
            ],
            "2. Content / Digital Book Content Usage": pdf_book_items,
            "3. Activity Evidence, Activity Submission, and Artifact Evidence": pdf_link_items if pdf_link_items else ["No activity or evidence submission links recorded in active window."]
        }

        for heading, body_items in sections.items():
            story.append(Paragraph(f"<b>{heading}</b>", sec_head_style))
            story.append(HRFlowable(width="100%", thickness=0.5, color=border_color, spaceAfter=4))
            for item in body_items:
                if "<a href=" in item:
                    story.append(Paragraph(f"{item}", link_style))
                else:
                    story.append(Paragraph(f"• {item}", normal_style))
            story.append(Spacer(1, 8))

    doc.build(story)
    buffer.seek(0)
    return buffer


def get_working_days(start_date, end_date, excluded_dates_list, exclude_sundays=True):
    try:
        start_np = np.datetime64(start_date)
        end_np = np.datetime64(end_date) + np.timedelta64(1, 'D')
        holidays_np = [np.datetime64(d) for d in excluded_dates_list] if excluded_dates_list else []
        w_mask = '1111110' if exclude_sundays else '1111111'
        return max(1, int(np.busday_count(start_np, end_np, weekmask=w_mask, holidays=holidays_np)))
    except Exception:
        return 1

# Page layout title
st.title("🏫 Academic Manager Portfolio & Teacher Performance Indicator Review Dashboard")
st.markdown("Track **School Portfolio Management**, **School WoW Velocity**, **Teacher Execution Tiers**, **Quantitative Performance Indicators (Lesson Prep / Library)**, and **360° Qualitative Evidences & Artifact Compliance**.")

# 1. Supabase Parquet Database Manager Function
def load_or_update_master_db(new_upload_dfs=None):
    master_df = fetch_master_db_from_supabase()
    master_df = _sanitize_df_for_parquet(master_df)

    if not new_upload_dfs:
        return normalize_identity_columns(master_df) if not master_df.empty else master_df

    combined_new = pd.concat(new_upload_dfs, ignore_index=True)
    combined_new = _sanitize_df_for_parquet(combined_new)

    all_data = pd.concat([master_df, combined_new], ignore_index=True) if not master_df.empty else combined_new
    all_data = normalize_identity_columns(all_data)
    all_data = _sanitize_df_for_parquet(all_data)

    dedup_cols = ['FullName', 'StartTime', 'Book', 'Type', 'Duration_Min', 'Institution']
    available_dedup_cols = [c for c in dedup_cols if c in all_data.columns]
    master_df = all_data.drop_duplicates(subset=available_dedup_cols, keep='last')
    master_df = _sanitize_df_for_parquet(master_df)

    try:
        parquet_buffer = BytesIO()
        master_df.to_parquet(parquet_buffer, index=False)
        parquet_buffer.seek(0)

        supabase.storage.from_(BUCKET_NAME).upload(
            path=PARQUET_FILE_NAME,
            file=parquet_buffer.getvalue(),
            file_options={"upsert": "true", "content-type": "application/octet-stream"}
        )
        fetch_master_db_from_supabase.clear()
        st.sidebar.success("Successfully synced database to Supabase Cloud!")
    except Exception as e:
        st.sidebar.error(f"Error saving Parquet Database to Supabase: {e}")

    return master_df


# --- 2. MULTI-EMPLOYEE HIERARCHY & DATA UPLOAD MANAGER ---
st.sidebar.header("📁 Multi-Employee Data Ingestion Portal")

employee_name = st.sidebar.text_input("Enter Consultant Name:", value="Harshit Bhargava")
employee_state = st.sidebar.selectbox("Select State / Zone (India Region):", [
    "Madhya Pradesh (MP)", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", 
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", 
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", 
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", 
    "Uttarakhand", "West Bengal", "Delhi NCR", "Jammu and Kashmir", "Ladakh"
])

uploaded_files = st.sidebar.file_uploader("Upload UserMetrics Excel (.xlsx)", type=["xlsx"], accept_multiple_files=True)

new_processed_dfs = []
if uploaded_files:
    for file in uploaded_files:
        try:
            temp_df = pd.read_excel(file, sheet_name="UserMetrics")
            temp_df = normalize_identity_columns(temp_df)
            
            temp_df['Uploaded_By'] = employee_name
            temp_df['State_Zone'] = employee_state

            if temp_df['Institution'].eq('').all():
                temp_df['Institution'] = "Default School"
            else:
                temp_df['Institution'] = temp_df['Institution'].replace('', 'Unknown School')

            for col in ['Grade', 'Subject', 'Book']:
                if col not in temp_df.columns:
                    temp_df[col] = ''
                else:
                    temp_df[col] = temp_df[col].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())

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

            for dt_col in ['StartTime', 'EndTime']:
                if dt_col in temp_df.columns:
                    temp_df[dt_col] = pd.to_datetime(temp_df[dt_col], errors='coerce')

            for qual_col in ['Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 'Phonics_Evidence_Link', 'Portfolio_Evidence_Link', 'Assessment_Score_Pct']:
                if qual_col not in temp_df.columns:
                    temp_df[qual_col] = None

            new_processed_dfs.append(temp_df)
        except Exception as e:
            st.sidebar.error(f"Error reading {file.name}: {e}")

if new_processed_dfs:
    df = load_or_update_master_db(new_processed_dfs)
    st.sidebar.success(f"Synced {len(uploaded_files)} file(s) into Supabase Parquet DB!")
else:
    df = load_or_update_master_db()

# --- 3. GRANULAR CLOUD DATABASE MANAGEMENT & CONSULTANT FILTERED DELETION ---
st.sidebar.markdown("---")
st.sidebar.header("🗄️ Granular Cloud Database Management")

if st.sidebar.button("🔄 Sync Latest Teacher Submissions"):
    fetch_master_db_from_supabase.clear()
    st.rerun()

current_db_check = fetch_master_db_from_supabase()

if not current_db_check.empty:
    st.sidebar.metric("Cloud DB Total Records", len(current_db_check))
    
    with st.sidebar.expander("🛠️ Selective Database Cleanup"):
        clean_mode = st.radio("Select Cleanup Scope:", ["By Consultant Name & State/Zone", "By School", "Clear Entire DB"])
        
        if clean_mode == "By Consultant Name & State/Zone":
            del_emp_name = st.text_input("Enter Exact Consultant Name to Delete:", value="")
            del_state_zone = st.selectbox("Select State/Zone for Cleanup:", [
                "Madhya Pradesh (MP)", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", 
                "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", 
                "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", 
                "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", 
                "Uttarakhand", "West Bengal", "Delhi NCR", "Jammu and Kashmir", "Ladakh"
            ], key="del_state_select")
            
            if st.button("🗑️ Delete Consultant Records from Cloud"):
                try:
                    if not del_emp_name.strip():
                        st.error("Please enter the consultant name.")
                    else:
                        mask = ~(
                            (current_db_check['Uploaded_By'].str.casefold() == del_emp_name.strip().casefold()) & 
                            (current_db_check['State_Zone'] == del_state_zone)
                        )
                        updated_db = current_db_check[mask].copy()
                        updated_db = _sanitize_df_for_parquet(updated_db)

                        parquet_buffer = BytesIO()
                        updated_db.to_parquet(parquet_buffer, index=False)
                        parquet_buffer.seek(0)
                        supabase.storage.from_(BUCKET_NAME).upload(
                            path=PARQUET_FILE_NAME,
                            file=parquet_buffer.getvalue(),
                            file_options={"upsert": "true", "content-type": "application/octet-stream"}
                        )
                        fetch_master_db_from_supabase.clear()
                        st.success(f"Successfully removed records for {del_emp_name} in {del_state_zone}!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Error deleting consultant data: {e}")
                    
        elif clean_mode == "By School":
            schools_in_db = sorted(current_db_check['Institution'].dropna().unique().tolist()) if 'Institution' in current_db_check.columns else []
            target_del_school = st.selectbox("Select School to Delete:", options=schools_in_db)
            if st.button("🗑️ Delete School Data from Cloud"):
                try:
                    updated_db = current_db_check[current_db_check['Institution'] != target_del_school].copy()
                    updated_db = _sanitize_df_for_parquet(updated_db)

                    parquet_buffer = BytesIO()
                    updated_db.to_parquet(parquet_buffer, index=False)
                    parquet_buffer.seek(0)
                    supabase.storage.from_(BUCKET_NAME).upload(
                        path=PARQUET_FILE_NAME,
                        file=parquet_buffer.getvalue(),
                        file_options={"upsert": "true", "content-type": "application/octet-stream"}
                    )
                    fetch_master_db_from_supabase.clear()
                    st.success(f"Successfully removed data for {target_del_school} from Supabase!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error deleting school data: {e}")
                    
        else:
            if st.button("🚨 Clear Entire Cloud Database"):
                try:
                    supabase.storage.from_(BUCKET_NAME).remove([PARQUET_FILE_NAME])
                    fetch_master_db_from_supabase.clear()
                    st.sidebar.error("Cloud database cleared!")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Could not delete database from cloud: {e}")

if df.empty:
    st.info("👋 Upload your raw daily or weekly `UserMetrics.xlsx` files in the sidebar to populate your permanent Supabase database.")
else:
    if 'FullName' not in df.columns:
        if 'FirstName' in df.columns and 'LastName' in df.columns:
            df['FullName'] = (df['FirstName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip()) + " " + df['LastName'].fillna('').astype(str).apply(lambda x: re.sub(r'\s+', ' ', x).strip())).apply(lambda x: re.sub(r'\s+', ' ', x).strip())
        else:
            df['FullName'] = 'Unknown Teacher'

    df = _sanitize_df_for_parquet(df)

    if 'StartTime' in df.columns and not df['StartTime'].isna().all():
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
            week_ranges['min'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '') + " to " + 
            week_ranges['max'].apply(lambda x: x.strftime('%b %d') if pd.notna(x) else '')
        )
        
        df = df.merge(week_ranges[['Month_Name', 'Week_Num', 'Week_Date_Range']], on=['Month_Name', 'Week_Num'], how='left')
        df['Month_Week_Label'] = df['StartTime'].dt.strftime('%b %Y') + " - Week " + df['Week_Num'].astype(str) + " (" + df['Week_Date_Range'] + ")"
        df['Week'] = df['Month_Week_Label']
    else:
        df['Date'] = None
        df['Month_Name'] = "N/A"
        df['Week'] = "N/A"

    master_teacher_roster = build_teacher_roster(df)
    if master_teacher_roster.empty:
        master_teacher_roster = pd.DataFrame(columns=['Institution', 'FullName', 'Uploaded_By', 'State_Zone'])
    else:
        master_teacher_roster = master_teacher_roster[['Institution', 'FullName', 'Uploaded_By', 'State_Zone']].drop_duplicates()

    # --- HIERARCHICAL GLOBAL FILTERS (DEFAULT TO MP) ---
    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Hierarchical Global Filters")
    
    # 1. State / Zone Level Filter
    all_states = sorted([str(s) for s in df['State_Zone'].unique() if str(s).strip() and str(s).lower() not in ['nan', 'none']])
    default_states = ["Madhya Pradesh (MP)"] if "Madhya Pradesh (MP)" in all_states else all_states
    
    if all_states:
        selected_states = st.sidebar.multiselect("1. Select State(s) / Zone(s)", options=all_states, default=default_states)
        df_state = df[df['State_Zone'].isin(selected_states)]
    else:
        df_state = df

    # 2. Consultant / Employee Level Filter
    all_employees = sorted([str(e) for e in df_state['Uploaded_By'].unique() if str(e).strip() and str(e).lower() not in ['nan', 'none']])
    if all_employees:
        selected_employees = st.sidebar.multiselect("2. Select Consultant(s)", options=all_employees, default=all_employees)
        df_emp = df_state[df_state['Uploaded_By'].isin(selected_employees)]
    else:
        df_emp = df_state

    # 3. School Level Filter
    all_schools = sorted([str(s) for s in df_emp['Institution'].unique() if str(s).strip() and str(s).lower() not in ['nan', 'none']])
    selected_schools = st.sidebar.multiselect("3. Select School(s)", options=all_schools, default=all_schools)

    school_master_roster = master_teacher_roster[master_teacher_roster['Institution'].isin(selected_schools)]
    school_filtered_df = df_emp[df_emp['Institution'].isin(selected_schools)]

    # --- CALENDAR & HOLIDAY MANAGER ---
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Calendar & Holiday Manager")
    
    available_months_df = school_filtered_df[['Month_Sort', 'Month_Name']].dropna().drop_duplicates().sort_values(by='Month_Sort', ascending=False)
    month_options = available_months_df['Month_Name'].tolist()
    
    selected_month = st.sidebar.selectbox("Select Review Month:", options=month_options if month_options else ["No Month Data"])
    month_filtered_df = school_filtered_df[school_filtered_df['Month_Name'] == selected_month]
    
    exclude_sundays_flag = st.sidebar.checkbox("🗓️ Exclude Sundays from Performance Indicators", value=True)

    user_excluded_dates = []
    if not month_filtered_df['Date'].isna().all() and not month_filtered_df.empty:
        m_min_date = month_filtered_df['Date'].min()
        m_max_date = month_filtered_df['Date'].max()
        all_month_possible_dates = [d.date() for d in pd.date_range(start=m_min_date, end=m_max_date)]
        
        user_excluded_dates = st.sidebar.multiselect(
            f"🗓️ Punch Holidays for {selected_month}:",
            options=all_month_possible_dates,
            format_func=lambda x: x.strftime('%Y-%m-%d')
        )

    # --- QUANTITATIVE & QUALITATIVE KPI CONTROLS ---
    st.sidebar.markdown("---")
    st.sidebar.header("🎯 Quantitative Performance Indicator Controls")
    enable_quant_kpi = st.sidebar.checkbox("Enable Quantitative Performance Indicator Benchmarks", value=True)
    
    if enable_quant_kpi:
        daily_ld_target = st.sidebar.number_input("Lesson Prep Target (Mins/Day)", min_value=0.0, max_value=60.0, value=10.0, step=5.0)
        daily_lib_target = st.sidebar.number_input("Library Usage Target (Mins/Day)", min_value=0.0, max_value=120.0, value=30.0, step=5.0)
    else:
        daily_ld_target = 0.0
        daily_lib_target = 0.0

    st.sidebar.markdown("---")
    st.sidebar.header("🎨 Qualitative Artifact Performance Indicator Controls")
    enable_qual_kpi = st.sidebar.checkbox("Enable Qualitative Performance Indicator Benchmarks", value=True)
    
    if enable_qual_kpi:
        target_vid_count = st.sidebar.number_input("Min. Activity Videos Required (Per Teacher)", min_value=1, max_value=20, value=3, step=1)
        target_writing_count = st.sidebar.number_input("Min. Writing Practice Required (Per Teacher)", min_value=1, max_value=20, value=3, step=1)
        target_lp_combo_count = st.sidebar.number_input("Min. Lesson Plan / Voice Note Submissions (Per Teacher)", min_value=1, max_value=20, value=3, step=1)
        target_phonics_count = st.sidebar.number_input("Min. Phonics / Phonetics Submissions (Per Teacher)", min_value=1, max_value=20, value=2, step=1)
        target_portfolio_count = st.sidebar.number_input("Min. Portfolio Evidence Submissions (Per Teacher)", min_value=1, max_value=20, value=1, step=1)
    else:
        target_vid_count = 0
        target_writing_count = 0
        target_lp_combo_count = 0
        target_phonics_count = 0
        target_portfolio_count = 0

    # --- GRANULARITY & CUSTOM DATE RANGE SELECTOR ---
    st.sidebar.subheader("🔍 Review View Level")
    available_month_weeks = sorted(month_filtered_df['Month_Week_Label'].dropna().unique())
    available_dates = sorted(month_filtered_df['Date'].dropna().unique(), reverse=True)
    
    view_mode = st.sidebar.radio("Granularity:", ["Full Month Summary", "Specific Week of Month", "Single Day Review", "Custom Date Range"])
    
    if month_filtered_df.empty and view_mode != "Custom Date Range":
        filtered_df = month_filtered_df
        selected_num_days = 1
        filter_description_text = f"Full Month: {selected_month} - 0 Records"
    elif view_mode == "Full Month Summary":
        filtered_df = month_filtered_df
        selected_num_days = get_working_days(month_filtered_df['Date'].min(), month_filtered_df['Date'].max(), user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Full Month: {selected_month} - {selected_num_days} Working Days"
    elif view_mode == "Specific Week of Month":
        selected_week_label = st.sidebar.selectbox("Select Week:", options=available_month_weeks)
        filtered_df = month_filtered_df[month_filtered_df['Month_Week_Label'] == selected_week_label]
        w_start = filtered_df['Date'].min() if not filtered_df.empty else selected_month
        w_end = filtered_df['Date'].max() if not filtered_df.empty else selected_month
        selected_num_days = get_working_days(w_start, w_end, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"{selected_week_label} - {selected_num_days} Working Days"
    elif view_mode == "Single Day Review":
        selected_date = st.sidebar.selectbox("Select Day:", options=available_dates)
        filtered_df = month_filtered_df[month_filtered_df['Date'] == selected_date]
        selected_num_days = get_working_days(selected_date, selected_date, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Single Date: {selected_date} - {selected_num_days} Working Days"
    else:
        min_avail = school_filtered_df['Date'].dropna().min() if not school_filtered_df['Date'].dropna().empty else pd.Timestamp.now().date()
        max_avail = school_filtered_df['Date'].dropna().max() if not school_filtered_df['Date'].dropna().empty else pd.Timestamp.now().date()
        
        custom_date_range = st.sidebar.date_input("Select Custom Date Range:", value=(min_avail, max_avail), min_value=min_avail, max_value=max_avail)
        if isinstance(custom_date_range, (tuple, list)) and len(custom_date_range) == 2:
            c_start, c_end = custom_date_range
        elif isinstance(custom_date_range, (tuple, list)) and len(custom_date_range) == 1:
            c_start = c_end = custom_date_range[0]
        else:
            c_start = c_end = custom_date_range
            
        filtered_df = school_filtered_df[(school_filtered_df['Date'] >= c_start) & (school_filtered_df['Date'] <= c_end)]
        selected_num_days = get_working_days(c_start, c_end, user_excluded_dates, exclude_sundays=exclude_sundays_flag)
        filter_description_text = f"Custom Range: {c_start} to {c_end} - {selected_num_days} Working Days"

    calc_ld_kpi = daily_ld_target * selected_num_days
    calc_lib_kpi = daily_lib_target * selected_num_days

    # 4. Global Teacher Filter
    available_teachers = sorted([str(t) for t in school_master_roster['FullName'].unique() if str(t).strip()])
    selected_teachers = st.sidebar.multiselect("4. Select Teacher(s)", options=available_teachers, default=available_teachers)
    
    filtered_roster = school_master_roster[school_master_roster['FullName'].isin(selected_teachers)]
    filtered_df = filtered_df[filtered_df['FullName'].isin(selected_teachers)]

    # --- SIDEBAR DIRECT EXCEL & DATABASE BACKUP EXPORT ---
    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 Direct Admin Master Export")
    buf_master_xlsx = BytesIO()
    with pd.ExcelWriter(buf_master_xlsx, engine='openpyxl') as writer:
        filtered_df.to_excel(writer, index=False, sheet_name="Filtered_Database_Logs")
    buf_master_xlsx.seek(0)
    st.sidebar.download_button(
        label="📥 Download Filtered Master DB (Excel)",
        data=buf_master_xlsx,
        file_name=f"Master_Database_Export_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # 8 Dedicated Active Tabs - With Bulk WhatsApp Hub at the Top (Tab 1)
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "🚀 1. Bulk WhatsApp & Dispatch Hub",
        "📘 2. Lesson Plan Preparation Tracker", 
        "📚 3. Library Usage Tracker", 
        "📖 4. Content & Chapters", 
        "👤 5. Teacher 360° Profile Report",
        "🏛️ 6. Manager Portfolio Quadrants",
        "🏫 7. School Teacher Progression",
        "📬 8. Live Evidence Submissions Feed"
    ])

    # ==============================================================================
    # TAB 1: BULK WHATSAPP & AUTOMATED SCHOOL PDF DISPATCH HUB
    # ==============================================================================
    with tab1:
        st.header("🚀 Bulk WhatsApp & School Comprehensive PDF Dispatch Hub")
        st.caption("Auto-generates multi-module summaries with explicit evaluation benchmark standards + Hosts live PDF dossiers.")

        if "crm_global_data" not in st.session_state:
            st.session_state["crm_global_data"] = load_crm_data_from_supabase()

        crm_data = st.session_state["crm_global_data"]
        contacts_dict = crm_data.get("contacts", {})

        bulk_target_schools = sorted(school_filtered_df['Institution'].dropna().unique().tolist())

        if not bulk_target_schools:
            st.info("No schools available in the selected filters.")
        else:
            st.markdown(f"#### 📋 Portfolio Action Center ({len(bulk_target_schools)} Schools)")

            c_cfg1, c_cfg2 = st.columns([1, 1])
            with c_cfg1:
                selected_bulk_entity = st.radio("Default Recipient Role for Bulk Dispatch:", ["Principal", "Owner", "Coordinator"], horizontal=True, key="bulk_entity_sel_radio")
                auto_upload_pdfs = st.checkbox("🔗 Automatically generate & embed live Supabase PDF download links into WhatsApp drafts", value=True, key="bulk_pdf_check")
            with c_cfg2:
                include_qual_evidence_in_wa = st.checkbox(
                    "📑 Include Qualitative Classroom Evidence Submissions in WhatsApp Message", 
                    value=bool(enable_qual_kpi), 
                    key="tab1_include_qual_evidence_check"
                )

            dispatch_records = []

            for school in bulk_target_schools:
                sch_roster = school_master_roster[school_master_roster['Institution'] == school]
                # FIXED: Use filtered_df instead of school_filtered_df to respect active date/granularity window
                sch_data = filtered_df[filtered_df['Institution'] == school]
                sch_teachers_list = sorted(sch_roster['FullName'].unique().tolist())
                tot_teachers = len(sch_teachers_list)

                # Quantitative Metrics
                ld_m = sch_data[sch_data['Type'] == 'lessonDelivery'].groupby('FullName')['Duration_Min'].sum()
                lib_m = sch_data[sch_data['Type'] == 'library'].groupby('FullName')['Duration_Min'].sum()

                # FIXED: Synchronized matching exact logic with PDF report generator
                met_ld = 0
                met_lib = 0
                for t in sch_teachers_list:
                    t_ld_mins = ld_m.get(t, 0.0)
                    t_lib_mins = lib_m.get(t, 0.0)
                    if (calc_ld_kpi > 0 and t_ld_mins >= calc_ld_kpi) or (calc_ld_kpi == 0 and t_ld_mins > 0):
                        met_ld += 1
                    if (calc_lib_kpi > 0 and t_lib_mins >= calc_lib_kpi) or (calc_lib_kpi == 0 and t_lib_mins > 0):
                        met_lib += 1

                ld_comp_pct = (met_ld / tot_teachers * 100) if tot_teachers > 0 else 0
                lib_comp_pct = (met_lib / tot_teachers * 100) if tot_teachers > 0 else 0

                # Accurate Inactive Teachers Evaluation
                inactive_teachers = [t for t in sch_teachers_list if (ld_m.get(t, 0.0) == 0.0 and lib_m.get(t, 0.0) == 0.0)]
                if inactive_teachers:
                    inactive_str = ", ".join(inactive_teachers[:3]) + (f" (+{len(inactive_teachers)-3} more)" if len(inactive_teachers) > 3 else "")
                else:
                    inactive_str = "None (All Active)"

                # Qualitative Evidence Metrics
                vids_cnt = len([l for col in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3'] if col in sch_data.columns for l in sch_data[col].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])
                phonics_cnt = len([l for l in sch_data['Phonics_Evidence_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Phonics_Evidence_Link' in sch_data.columns else 0
                writing_cnt = len([l for l in sch_data['Writing_Sample_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Writing_Sample_Link' in sch_data.columns else 0
                lp_pic_cnt = len([l for l in sch_data['Lesson_Plan_Picture'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in sch_data.columns else 0
                voice_cnt = len([l for l in sch_data['Voice_Note_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in sch_data.columns else 0
                portfolio_cnt = len([l for l in sch_data['Portfolio_Evidence_Link'].dropna() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Portfolio_Evidence_Link' in sch_data.columns else 0

                # Teacher Level Adoption for Qualitative ("Submitted")
                teachers_with_vids = sum(1 for t in sch_teachers_list if any(re.match(r'^https?://', str(sch_data[(sch_data["FullName"] == t)][c].dropna().iloc[0]).strip(), re.IGNORECASE) for c in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3'] if c in sch_data.columns and not sch_data[(sch_data["FullName"] == t)][c].dropna().empty))
                teachers_with_ph = sum(1 for t in sch_teachers_list if 'Phonics_Evidence_Link' in sch_data.columns and not sch_data[(sch_data["FullName"] == t)]['Phonics_Evidence_Link'].dropna().empty and re.match(r'^https?://', str(sch_data[(sch_data["FullName"] == t)]['Phonics_Evidence_Link'].dropna().iloc[0]).strip(), re.IGNORECASE))
                teachers_with_w = sum(1 for t in sch_teachers_list if 'Writing_Sample_Link' in sch_data.columns and not sch_data[(sch_data["FullName"] == t)]['Writing_Sample_Link'].dropna().empty and re.match(r'^https?://', str(sch_data[(sch_data["FullName"] == t)]['Writing_Sample_Link'].dropna().iloc[0]).strip(), re.IGNORECASE))
                teachers_with_lp = sum(1 for t in sch_teachers_list if ('Lesson_Plan_Picture' in sch_data.columns and not sch_data[(sch_data["FullName"] == t)]['Lesson_Plan_Picture'].dropna().empty and re.match(r'^https?://', str(sch_data[(sch_data["FullName"] == t)]['Lesson_Plan_Picture'].dropna().iloc[0]).strip(), re.IGNORECASE)) or ('Voice_Note_Link' in sch_data.columns and not sch_data[(sch_data["FullName"] == t)]['Voice_Note_Link'].dropna().empty and re.match(r'^https?://', str(sch_data[(sch_data["FullName"] == t)]['Voice_Note_Link'].dropna().iloc[0]).strip(), re.IGNORECASE)))
                teachers_with_pf = sum(1 for t in sch_teachers_list if 'Portfolio_Evidence_Link' in sch_data.columns and not sch_data[(sch_data["FullName"] == t)]['Portfolio_Evidence_Link'].dropna().empty and re.match(r'^https?://', str(sch_data[(sch_data["FullName"] == t)]['Portfolio_Evidence_Link'].dropna().iloc[0]).strip(), re.IGNORECASE))

                # Contact Lookup
                contact_info = contacts_dict.get(school, {}).get(selected_bulk_entity, {"name": "", "phone": ""})
                c_name = contact_info.get("name", "")
                c_phone = contact_info.get("phone", "")

                # Generate Hosted PDF Link if enabled
                pdf_link_str = ""
                if auto_upload_pdfs:
                    clean_pdf_buf = generate_comprehensive_school_pdf_report(
                        school_name=school,
                        teachers_list=sch_teachers_list,
                        school_filtered_df=school_filtered_df,
                        filtered_df=filtered_df,
                        filter_desc=filter_description_text,
                        calc_ld_kpi=calc_ld_kpi,
                        calc_lib_kpi=calc_lib_kpi,
                        daily_ld_target=daily_ld_target,
                        daily_lib_target=daily_lib_target,
                        selected_num_days=selected_num_days,
                        target_vid_count=target_vid_count,
                        target_writing_count=target_writing_count,
                        target_lp_combo_count=target_lp_combo_count,
                        target_phonics_count=target_phonics_count,
                        target_portfolio_count=target_portfolio_count,
                        enable_quant_kpi=enable_quant_kpi,
                        enable_qual_kpi=enable_qual_kpi
                    )
                    hosted_url = upload_pdf_to_supabase(clean_pdf_buf, school)
                    if hosted_url:
                        pdf_link_str = f"\n\n📄 *Download Full School Audit Report (PDF):*\n{hosted_url}"

                # Compose Benchmark Annotations Dynamically for Quantitative
                ld_bench_str = f" [Benchmark: {daily_ld_target:.0f}m/day × {selected_num_days}d = {calc_ld_kpi:.0f} mins total]" if (enable_quant_kpi and calc_ld_kpi > 0) else ""
                lib_bench_str = f" [Benchmark: {daily_lib_target:.0f}m/day × {selected_num_days}d = {calc_lib_kpi:.0f} mins total]" if (enable_quant_kpi and calc_lib_kpi > 0) else ""

                # Build Structured WhatsApp Message Sections
                greeting = f"Dear {c_name} ji" if c_name else f"Respected {selected_bulk_entity}"
                
                msg_parts = [
                    f"{greeting},\n\n"
                    f"Greetings from OneLearn Academic Team! Here is the latest performance & classroom implementation summary for *{school}* ({filter_description_text}):\n"
                ]

                # Section 1: Quantitative Benchmarks (Strictly only if enable_quant_kpi is True)
                if enable_quant_kpi:
                    msg_parts.append(
                        f"📊 *Quantitative Benchmarks:*\n"
                        f"• Lesson Plan Prep Compliance: {ld_comp_pct:.0f}% ({met_ld}/{tot_teachers} Teachers){ld_bench_str}\n"
                        f"• Library Digital Usage Compliance: {lib_comp_pct:.0f}% ({met_lib}/{tot_teachers} Teachers){lib_bench_str}"
                    )

                # Section 2: Qualitative Classroom Evidence Submissions (Strictly only if toggle is checked AND enable_qual_kpi is True)
                if include_qual_evidence_in_wa and enable_qual_kpi:
                    msg_parts.append(
                        f"\n📬 *Classroom Evidence Submissions:*\n"
                        f"• Activity Videos: {vids_cnt} Uploaded ({teachers_with_vids}/{tot_teachers} Teachers Submitted)\n"
                        f"• Phonics Evidence: {phonics_cnt} Uploaded ({teachers_with_ph}/{tot_teachers} Teachers Submitted)\n"
                        f"• Writing Samples: {writing_cnt} Uploaded ({teachers_with_w}/{tot_teachers} Teachers Submitted)\n"
                        f"• LP Pictures / Voice Notes: {lp_pic_cnt + voice_cnt} Uploaded ({teachers_with_lp}/{tot_teachers} Teachers Submitted)\n"
                        f"• Portfolio Artifacts: {portfolio_cnt} Uploaded ({teachers_with_pf}/{tot_teachers} Teachers Submitted)"
                    )

                # Focus Follow-up & Closing
                msg_parts.append(
                    f"\n⚠️ *Inactive / Follow-up Teachers:* {inactive_str}"
                    f"{pdf_link_str}\n\n"
                    f"Let us connect for a 5-minute review to support your teachers in scaling classroom outcomes.\n\n"
                    f"Regards,\n"
                    f"Harshit Bhargava,\n"
                    f"OneLearn Academic Team"
                )

                wa_msg = "\n".join(msg_parts)

                dispatch_records.append({
                    "School": school,
                    "Total Teachers": tot_teachers,
                    "Prep Compliance": f"{ld_comp_pct:.0f}%",
                    "Library Compliance": f"{lib_comp_pct:.0f}%",
                    "Evidence Count": vids_cnt + phonics_cnt + writing_cnt + lp_pic_cnt + voice_cnt + portfolio_cnt,
                    "Contact Name": c_name or "Not Set",
                    "Phone": c_phone or "Not Set",
                    "Raw_Phone": re.sub(r'[^0-9+]', '', c_phone),
                    "Draft_Message": wa_msg
                })

            # Display Bulk Dispatch Interface
            for idx, r in enumerate(dispatch_records):
                with st.expander(f"🏫 **{r['School']}** — Prep: {r['Prep Compliance']} | Library: {r['Library Compliance']}" + (f" | Evidence: {r['Evidence Count']} Artifacts" if (include_qual_evidence_in_wa and enable_qual_kpi) else ""), expanded=(idx < 2)):
                    col_r1, col_r2 = st.columns([1, 2])
                    with col_r1:
                        st.markdown(f"**Target:** {selected_bulk_entity} ({r['Contact Name']})")
                        st.markdown(f"**Phone:** `{r['Phone']}`")
                        st.markdown(f"**Roster Size:** {r['Total Teachers']} Teachers")
                        st.markdown(f"**Quantitative:** {r['Prep Compliance']} Prep / {r['Library Compliance']} Lib")
                        if include_qual_evidence_in_wa and enable_qual_kpi:
                            st.markdown(f"**Qualitative Evidence:** {r['Evidence Count']} Uploads")

                    with col_r2:
                        edited_msg = st.text_area("Review / Edit Message Draft:", value=r["Draft_Message"], height=160, key=f"bulk_wa_text_{idx}")
                        clean_ph = r["Raw_Phone"]
                        if clean_ph:
                            btn_c1, btn_c2 = st.columns(2)
                            encoded_url = urllib.parse.quote(edited_msg)
                            with btn_c1:
                                st.markdown(f'<a href="https://wa.me/{clean_ph}?text={encoded_url}" target="_blank" style="text-decoration:none;"><button style="background-color:#25D366;color:white;padding:10px 18px;border:none;border-radius:6px;cursor:pointer;font-weight:bold;width:100%;font-size:15px;">🚀 Open in WhatsApp Web</button></a>', unsafe_allow_html=True)
                            with btn_c2:
                                st.markdown(f'<a href="tel:{clean_ph}" target="_blank" style="text-decoration:none;"><button style="background-color:#2CA02C;color:white;padding:10px 18px;border:none;border-radius:6px;cursor:pointer;font-weight:bold;width:100%;font-size:15px;">📞 Call {selected_bulk_entity}</button></a>', unsafe_allow_html=True)
                        else:
                            st.warning(f"⚠️ No phone number saved for {selected_bulk_entity} in {r['School']}. Add it in CRM to enable 1-click send.")

            # Universal Global CRM Box Integrated at the Bottom of Tab 1
            tab1_crm_summary = f"Bulk Action Center: {len(bulk_target_schools)} schools in view ({filter_description_text})."
            render_universal_crm_box("Bulk Dispatch Hub", bulk_target_schools, filter_description_text, tab1_crm_summary)

    # TAB 2: LESSON PLAN PREPARATION TRACKER
    with tab2:
        st.header("📘 Lesson Plan Preparation Tracker")
        if enable_quant_kpi and calc_ld_kpi > 0:
            st.caption(f"Benchmark Standard: **At least {calc_ld_kpi:.0f} Minutes** ({daily_ld_target:.0f} mins/day across {selected_num_days} working day(s)).")
        else:
            st.caption(f"Reviewing cumulative minutes prepared across {selected_num_days} working day(s).")

        ld_df = filtered_df[filtered_df['Type'] == 'lessonDelivery']
        ld_usage = ld_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index()
        ld_daily = filtered_roster.merge(ld_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
        
        def get_ld_status(x):
            if not enable_quant_kpi or calc_ld_kpi == 0: 
                return 'Activity Logged' if x > 0 else 'No Activity Logged'
            if x >= calc_ld_kpi: 
                return f'✅ Met Performance Indicator (>= {calc_ld_kpi:.0f}m)'
            elif x > 0.0: 
                return f'⚠️ Below Performance Indicator (< {calc_ld_kpi:.0f}m)'
            else: 
                return '❌ Inactive (0 Mins)'
        
        ld_daily['Performance Indicator Status'] = ld_daily['Duration_Min'].apply(get_ld_status)

        c1, c2, c3, c4 = st.columns(4)
        total_teachers = len(ld_daily)
        met_count = len(ld_daily[ld_daily['Duration_Min'] >= calc_ld_kpi]) if (enable_quant_kpi and calc_ld_kpi > 0) else len(ld_daily[ld_daily['Duration_Min'] > 0])
        inactive_count = len(ld_daily[ld_daily['Duration_Min'] == 0.0])
        
        c1.metric("Total Roster Teachers", total_teachers)
        c2.metric(f"Met Standard ({calc_ld_kpi:.0f}m)" if enable_quant_kpi else "Active Teachers", f"{met_count} / {total_teachers}")
        c3.metric("Inactive Teachers (0m)", inactive_count, delta=f"{-inactive_count}" if inactive_count > 0 else "0", delta_color="inverse")
        c4.metric("Compliance Rate", f"{(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%")

        with st.expander("✨ Gemini AI Intelligent Lesson Prep Analysis", expanded=False):
            if st.button("Generate AI Lesson Prep Summary", key="ai_btn_tab2"):
                with st.spinner("Analyzing lesson prep metrics with Gemini..."):
                    summary_prompt = f"Analyze these lesson prep statistics: Total Teachers: {total_teachers}, Met Standard: {met_count}, Inactive: {inactive_count}. Provide 3 key actionable takeaways for the academic manager."
                    ai_text = get_gemini_summary(summary_prompt)
                    st.markdown(ai_text)

        fig_ld = px.bar(
            ld_daily, x="FullName", y="Duration_Min", color="Performance Indicator Status",
            title=f"Lesson Prep Minutes per Teacher" + (f" vs. {calc_ld_kpi:.0f} Min Standard" if enable_quant_kpi else ""),
            labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Prepared"},
            text_auto=".1f"
        )
        if enable_quant_kpi and calc_ld_kpi > 0:
            fig_ld.add_hline(y=calc_ld_kpi, line_dash="dash", line_color="black", annotation_text=f"Guideline ({calc_ld_kpi:.0f} mins)")
        st.plotly_chart(fig_ld, use_container_width=True)

        st.subheader("📋 Lesson Plan Preparation Table")
        display_ld_table = ld_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
        st.dataframe(display_ld_table, use_container_width=True)

        col_t1_d1, col_t1_d2 = st.columns(2)
        with col_t1_d1:
            pdf_tab1 = generate_comprehensive_school_pdf_report(
                school_name=selected_schools[0] if len(selected_schools) == 1 else "Multiple Schools Portfolio",
                teachers_list=filtered_roster['FullName'].unique().tolist(),
                school_filtered_df=school_filtered_df,
                filtered_df=filtered_df,
                filter_desc=filter_description_text,
                calc_ld_kpi=calc_ld_kpi,
                calc_lib_kpi=calc_lib_kpi,
                daily_ld_target=daily_ld_target,
                daily_lib_target=daily_lib_target,
                selected_num_days=selected_num_days,
                target_vid_count=target_vid_count,
                target_writing_count=target_writing_count,
                target_lp_combo_count=target_lp_combo_count,
                target_phonics_count=target_phonics_count,
                target_portfolio_count=target_portfolio_count,
                enable_quant_kpi=enable_quant_kpi,
                enable_qual_kpi=enable_qual_kpi
            )
            st.download_button(
                label="📄 Download Tab 2 Report (PDF)",
                data=pdf_tab1,
                file_name=f"Lesson_Plan_Prep_Report_{selected_month.replace(' ', '_')}.pdf",
                mime="application/pdf"
            )
        with col_t1_d2:
            buf_t1_xlsx = BytesIO()
            with pd.ExcelWriter(buf_t1_xlsx, engine='openpyxl') as writer:
                display_ld_table.to_excel(writer, index=False, sheet_name="Lesson_Prep_Logs")
            buf_t1_xlsx.seek(0)
            st.download_button(
                label="📥 Download Tab 2 Data (Excel)",
                data=buf_t1_xlsx,
                file_name=f"Lesson_Plan_Prep_{selected_month.replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        teacher_prep_breakdown = "\n\n".join([f"• **{r['FullName']}**: {r['Duration_Min']:.1f} mins ({r['Performance Indicator Status']})" for _, r in ld_daily.iterrows()])
        tab1_metrics_summary = (
            f"🎯 Target KPI: {daily_ld_target:.0f} mins/day × {selected_num_days} working days = {calc_ld_kpi:.0f} mins total standard\n"
            f"Total Roster: {total_teachers} teachers | Met Standard: {met_count} | Inactive: {inactive_count} | Compliance Rate: {(met_count/total_teachers*100 if total_teachers>0 else 0):.1f}%\n\n"
            f"Detailed Teacher Lesson Prep Logs:\n{teacher_prep_breakdown}"
        )
        render_universal_crm_box("Lesson Plan Prep Tracker", selected_schools, filter_description_text, tab1_metrics_summary)

    # TAB 3: LIBRARY USAGE TRACKER
    with tab3:
        st.header("📚 Library Usage Tracker")
        if enable_quant_kpi and calc_lib_kpi > 0:
            st.caption(f"Benchmark Standard: **At least {calc_lib_kpi:.0f} Minutes** ({daily_lib_target:.0f} mins/day across {selected_num_days} working day(s)).")
        else:
            st.caption(f"Reviewing cumulative library usage minutes across {selected_num_days} working day(s).")

        lib_df = filtered_df[filtered_df['Type'] == 'library']
        lib_usage = lib_df.groupby(['Institution', 'FullName'])['Duration_Min'].sum().reset_index()
        lib_daily = filtered_roster.merge(lib_usage, on=['Institution', 'FullName'], how='left').fillna(0.0)
        
        def get_lib_status(x):
            if not enable_quant_kpi or calc_lib_kpi == 0: 
                return 'Activity Logged' if x > 0 else 'No Activity Logged'
            if x >= calc_lib_kpi: 
                return f'✅ Met Performance Indicator (>= {calc_lib_kpi:.0f}m)'
            elif x > 0.0: 
                return f'⚠️ Below Performance Indicator (< {calc_lib_kpi:.0f}m)'
            else: 
                return '❌ Inactive (0 Mins)'

        lib_daily['Performance Indicator Status'] = lib_daily['Duration_Min'].apply(get_lib_status)

        m1, m2, m3, m4 = st.columns(4)
        lib_total_teachers = len(lib_daily)
        lib_met_count = len(lib_daily[lib_daily['Duration_Min'] >= calc_lib_kpi]) if (enable_quant_kpi and calc_lib_kpi > 0) else len(lib_daily[lib_daily['Duration_Min'] > 0])
        lib_inactive_count = len(lib_daily[lib_daily['Duration_Min'] == 0.0])
        
        m1.metric("Total Roster Teachers", lib_total_teachers)
        m2.metric(f"Met Standard ({calc_lib_kpi:.0f}m)" if enable_quant_kpi else "Active Teachers", f"{lib_met_count} / {lib_total_teachers}")
        m3.metric("Inactive Teachers (0m)", lib_inactive_count, delta=f"{-lib_inactive_count}" if lib_inactive_count > 0 else "0", delta_color="inverse")
        m4.metric("Engagement Rate", f"{(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%")

        with st.expander("✨ Gemini AI Intelligent Library Usage Analysis", expanded=False):
            if st.button("Generate AI Library Summary", key="ai_btn_tab3"):
                with st.spinner("Analyzing library engagement with Gemini..."):
                    summary_prompt = f"Analyze these library usage statistics: Total Teachers: {lib_total_teachers}, Met Standard: {lib_met_count}, Engagement Rate: {(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%. Provide 3 key recommendations."
                    ai_text = get_gemini_summary(summary_prompt)
                    st.markdown(ai_text)

        fig_lib = px.bar(
            lib_daily, x="FullName", y="Duration_Min", color="Performance Indicator Status",
            title=f"Library Usage Minutes per Teacher" + (f" vs. {calc_lib_kpi:.0f} Min Standard" if enable_quant_kpi else ""),
            labels={"FullName": "Teacher Name", "Duration_Min": "Minutes Logged"},
            text_auto=".1f"
        )
        if enable_quant_kpi and calc_lib_kpi > 0:
            fig_lib.add_hline(y=calc_lib_kpi, line_dash="dash", line_color="black", annotation_text=f"Guideline ({calc_lib_kpi:.0f} mins)")
        st.plotly_chart(fig_lib, use_container_width=True)

        st.subheader("📋 Library Usage Table")
        display_lib_table = lib_daily.rename(columns={'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes Logged'}).round({'Minutes Logged': 1})
        st.dataframe(display_lib_table, use_container_width=True)

        col_t2_d1, col_t2_d2 = st.columns(2)
        with col_t2_d1:
            pdf_tab2 = generate_comprehensive_school_pdf_report(
                school_name=selected_schools[0] if len(selected_schools) == 1 else "Multiple Schools Portfolio",
                teachers_list=filtered_roster['FullName'].unique().tolist(),
                school_filtered_df=school_filtered_df,
                filtered_df=filtered_df,
                filter_desc=filter_description_text,
                calc_ld_kpi=calc_ld_kpi,
                calc_lib_kpi=calc_lib_kpi,
                daily_ld_target=daily_ld_target,
                daily_lib_target=daily_lib_target,
                selected_num_days=selected_num_days,
                target_vid_count=target_vid_count,
                target_writing_count=target_writing_count,
                target_lp_combo_count=target_lp_combo_count,
                target_phonics_count=target_phonics_count,
                target_portfolio_count=target_portfolio_count,
                enable_quant_kpi=enable_quant_kpi,
                enable_qual_kpi=enable_qual_kpi
            )
            st.download_button(
                label="📄 Download Tab 3 Report (PDF)",
                data=pdf_tab2,
                file_name=f"Library_Usage_Report_{selected_month.replace(' ', '_')}.pdf",
                mime="application/pdf"
            )
        with col_t2_d2:
            buf_t2_xlsx = BytesIO()
            with pd.ExcelWriter(buf_t2_xlsx, engine='openpyxl') as writer:
                display_lib_table.to_excel(writer, index=False, sheet_name="Library_Usage_Logs")
            buf_t2_xlsx.seek(0)
            st.download_button(
                label="📥 Download Tab 3 Data (Excel)",
                data=buf_t2_xlsx,
                file_name=f"Library_Usage_{selected_month.replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        teacher_lib_breakdown = "\n\n".join([f"• **{r['FullName']}**: {r['Duration_Min']:.1f} mins ({r['Performance Indicator Status']})" for _, r in lib_daily.iterrows()])
        tab2_metrics_summary = (
            f"🎯 Target KPI: {daily_lib_target:.0f} mins/day × {selected_num_days} working days = {calc_lib_kpi:.0f} mins total standard\n"
            f"Total Roster: {lib_total_teachers} teachers | Active Met Standard: {lib_met_count} | Inactive: {lib_inactive_count} | Engagement Rate: {(lib_met_count/lib_total_teachers*100 if lib_total_teachers>0 else 0):.1f}%\n\n"
            f"Detailed Teacher Library Usage Logs:\n{teacher_lib_breakdown}"
        )
        render_universal_crm_box("Library Usage Tracker", selected_schools, filter_description_text, tab2_metrics_summary)

    # TAB 4: CONTENT & CHAPTERS
    with tab4:
        st.header("📖 Content & Chapters")
        st.caption(f"Track specific textbooks and instructional modules opened during `{filter_description_text}`.")

        content_raw = filtered_df[filtered_df['Book'].str.len() > 0]
        content_df = content_raw[~content_raw['Book'].str.match(r'^Lesson Plan', case=False, na=False)]

        if content_df.empty:
            st.info("No specific textbook/chapter access logs found in the uploaded data for the selected global filters.")
        else:
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                t3_school_opt = ["All Selected Schools"] + sorted(content_df['Institution'].unique().tolist())
                t3_school = st.selectbox("🏫 Select School:", t3_school_opt, key="t3_school")
                
            t3_df = content_df if t3_school == "All Selected Schools" else content_df[content_df['Institution'] == t3_school]

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
                k1.metric("Textbooks / Chapters Opened", t3_df['Book'].nunique())
                k2.metric("Subjects Taught", t3_df['Subject'].nunique())
                k3.metric("Total Content Access Time", f"{t3_df['Duration_Min'].sum():.1f} Mins")

                with st.expander("✨ Gemini AI Curriculum Pacing Analysis", expanded=False):
                    if st.button("Generate AI Content Summary", key="ai_btn_tab4"):
                        with st.spinner("Analyzing curriculum usage with Gemini..."):
                            summary_prompt = f"Analyze textbook and subject distribution: Unique Chapters: {t3_df['Book'].nunique()}, Subjects Taught: {t3_df['Subject'].nunique()}, Total Time: {t3_df['Duration_Min'].sum():.1f} mins. Provide pacing insights."
                            ai_text = get_gemini_summary(summary_prompt)
                            st.markdown(ai_text)

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
                            title="Textbooks / Chapters Opened per Teacher (Mins)",
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

                st.subheader("📋 Filtered Granular Textbook Log")
                log_cols = ['Institution', 'FullName', 'Grade', 'Subject', 'Book', 'StartTime', 'Duration (HH:MM:SS)', 'Duration_Min']
                available_cols = [c for c in log_cols if c in t3_df.columns]
                
                display_content_log = t3_df[available_cols].rename(columns={
                    'Institution': 'School', 'FullName': 'Teacher Name', 'Duration_Min': 'Minutes'
                }).sort_values(by='StartTime', ascending=False)
                display_content_log['Minutes'] = display_content_log['Minutes'].round(1)
                st.dataframe(display_content_log, use_container_width=True)

                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    buf_t3_xlsx = BytesIO()
                    with pd.ExcelWriter(buf_t3_xlsx, engine='openpyxl') as writer:
                        display_content_log.to_excel(writer, index=False, sheet_name='Content_Log')
                    buf_t3_xlsx.seek(0)
                    st.download_button(
                        label="📥 Download Content Log (Excel)",
                        data=buf_t3_xlsx,
                        file_name=f"Content_Log_{selected_month.replace(' ', '_')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                with col_d2:
                    pdf_tab3 = generate_pdf_report(
                        title_text="📖 Textbooks & Digital Content Usage Report",
                        subtitle_text=f"Teacher: {t3_teacher} | Subject: {t3_subject}",
                        school_name=t3_school,
                        summary_metrics={
                            "Chapters Opened": t3_df['Book'].nunique(),
                            "Subjects Taught": t3_df['Subject'].nunique(),
                            "Total Duration": f"{t3_df['Duration_Min'].sum():.1f} Mins"
                        },
                        dataframe=display_content_log[['School', 'Teacher Name', 'Grade', 'Subject', 'Book', 'Minutes']].head(30)
                    )
                    st.download_button(
                        label="📄 Download Tab 4 Content Report (PDF)",
                        data=pdf_tab3,
                        file_name=f"Content_Usage_Report_{selected_month.replace(' ', '_')}.pdf",
                        mime="application/pdf"
                    )

                book_breakdown_summary = "\n\n".join([f"• {r['Book']} ({r['Grade']} - {r['Subject']}): {r['Duration_Min']:.1f} mins" for _, r in t3_df.groupby(['Book', 'Grade', 'Subject'])['Duration_Min'].sum().reset_index().iterrows()])
                tab3_metrics_summary = (
                    f"Chapters Opened: {t3_df['Book'].nunique()} | Subjects Taught: {t3_df['Subject'].nunique()} | Total Access Time: {t3_df['Duration_Min'].sum():.1f} Mins\n\n"
                    f"Chapter Breakdown:\n{book_breakdown_summary}"
                )
                render_universal_crm_box("Content & Chapters", t3_school if t3_school != "All Selected Schools" else selected_schools, filter_description_text, tab3_metrics_summary)

    # TAB 5: SINGLE TEACHER 360° PROFILE REPORT
    with tab5:
        st.header("👤 Teacher 360° Performance Profile")
        st.caption("Review quantitative lesson metrics, detailed textbook time logs, and structured qualitative performance evidence with clickable artifact links.")

        all_roster_teachers = sorted(school_master_roster['FullName'].unique())
        
        if not all_roster_teachers:
            st.info("No teachers found in roster for the selected school(s).")
        else:
            col_sel_top, col_btn_top, col_bulk_btn = st.columns([2, 1, 1])
            with col_sel_top:
                target_teacher = st.selectbox("Select Teacher to Audit:", options=all_roster_teachers, key="top_teacher_select")
            
            teacher_all_data = school_filtered_df[school_filtered_df['FullName'] == target_teacher]
            teacher_date_data = filtered_df[filtered_df['FullName'] == target_teacher]
            teacher_school = school_master_roster[school_master_roster['FullName'] == target_teacher]['Institution'].values[0] if not school_master_roster[school_master_roster['FullName'] == target_teacher].empty else "N/A"

            t_day_ld = teacher_date_data[teacher_date_data['Type'] == 'lessonDelivery']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
            t_day_lib = teacher_date_data[teacher_date_data['Type'] == 'library']['Duration_Min'].sum() if not teacher_date_data.empty else 0.0
            
            ld_pct = (t_day_ld / calc_ld_kpi) * 100 if calc_ld_kpi > 0 else (100.0 if t_day_ld >= 0 else 0)
            lib_pct = (t_day_lib / calc_lib_kpi) * 100 if calc_lib_kpi > 0 else (100.0 if t_day_lib >= 0 else 0)

            if calc_ld_kpi > 0:
                ld_advice = f"🌟 Steady Execution ({t_day_ld:.1f}m logged)" if t_day_ld >= calc_ld_kpi else (f"⚠️ In-Progress ({t_day_ld:.1f}m logged)" if t_day_ld > 0 else "❌ Pending Activity")
            else:
                ld_advice = "✅ Holiday / Scheduled Break"

            if calc_lib_kpi > 0:
                lib_advice = f"🌟 Steady Execution ({t_day_lib:.1f}m logged)" if t_day_lib >= calc_lib_kpi else (f"⚠️ In-Progress ({t_day_lib:.1f}m logged)" if t_day_lib > 0 else "❌ Pending Activity")
            else:
                lib_advice = "✅ Holiday / Scheduled Break"

            t_books_raw = teacher_date_data[teacher_date_data['Book'].str.len() > 0]
            if t_books_raw.empty:
                t_books_raw = teacher_all_data[teacher_all_data['Book'].str.len() > 0]
            teacher_books = t_books_raw[~t_books_raw['Book'].str.match(r'^Lesson Plan', case=False, na=False)]

            evidence_source = teacher_date_data if not teacher_date_data.empty else teacher_all_data
            
            def extract_evidence_items(df_src, col_name):
                if col_name not in df_src.columns:
                    return []
                items = []
                for _, r in df_src.iterrows():
                    val = str(r[col_name]).strip()
                    if re.match(r'^https?://', val, re.IGNORECASE):
                        d_str = str(r['Date']) if 'Date' in r and pd.notna(r['Date']) else "Recent"
                        g_str = f"Grade {r['Grade']}" if 'Grade' in r and str(r['Grade']).strip() else "Grade N/A"
                        s_str = str(r['Subject']).strip() if 'Subject' in r and str(r['Subject']).strip() else "General Subject"
                        b_str = str(r['Book']).strip() if 'Book' in r and str(r['Book']).strip() else "Activity Lesson"
                        items.append({'url': val, 'date': d_str, 'grade': g_str, 'subject': s_str, 'lesson': b_str})
                seen = set()
                deduped = []
                for item in items:
                    if item['url'] not in seen:
                        seen.add(item['url'])
                        deduped.append(item)
                return deduped

            v_voice = extract_evidence_items(evidence_source, 'Voice_Note_Link')
            v_pic = extract_evidence_items(evidence_source, 'Lesson_Plan_Picture')
            v_writing = extract_evidence_items(evidence_source, 'Writing_Sample_Link')
            v_phonics = extract_evidence_items(evidence_source, 'Phonics_Evidence_Link')
            v_portfolio = extract_evidence_items(evidence_source, 'Portfolio_Evidence_Link')

            v_vid = []
            for col in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                v_vid.extend(extract_evidence_items(evidence_source, col))
            seen_v = set()
            deduped_v = []
            for item in v_vid:
                if item['url'] not in seen_v:
                    seen_v.add(item['url'])
                    deduped_v.append(item)
            v_vid = deduped_v

            lp_combo_total = len(v_voice) + len(v_pic)
            total_artifacts = lp_combo_total + len(v_vid) + len(v_writing) + len(v_phonics) + len(v_portfolio)

            pdf_book_items = []
            if not teacher_books.empty:
                b_summary_df = teacher_books.groupby(['Book', 'Grade', 'Subject'])['Duration_Min'].sum().reset_index()
                for _, br in b_summary_df.iterrows():
                    pdf_book_items.append(f"Book: {br['Book']} ({br['Grade']} - {br['Subject']}) | Time Spent: {br['Duration_Min']:.1f} Mins")
            else:
                pdf_book_items.append("No textbooks or digital modules opened.")

            # Clean, Masked Hyperlinks
            pdf_link_items = []
            for i, item in enumerate(v_voice, 1): 
                pdf_link_items.append(f'• 🎧 <a href="{item["url"]}"><u><b>Open Voice Reflection #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
            for i, item in enumerate(v_pic, 1): 
                pdf_link_items.append(f'• 🖼️ <a href="{item["url"]}"><u><b>View Lesson Plan Photo #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
            for i, item in enumerate(v_vid, 1): 
                pdf_link_items.append(f'• 🎥 <a href="{item["url"]}"><u><b>Watch Classroom Activity Video #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
            for i, item in enumerate(v_writing, 1): 
                pdf_link_items.append(f'• 📝 <a href="{item["url"]}"><u><b>View Student Writing Sample #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
            for i, item in enumerate(v_phonics, 1): 
                pdf_link_items.append(f'• 🔤 <a href="{item["url"]}"><u><b>Open Phonics Evidence #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')
            for i, item in enumerate(v_portfolio, 1): 
                pdf_link_items.append(f'• 📁 <a href="{item["url"]}"><u><b>View Teacher Portfolio Showcase #{i}</b></u></a> — <i>{item["grade"]} | {item["subject"]} ({item["lesson"]}, {item["date"]})</i>')

            pdf_custom_sections = {
                "1. Lesson Preparation, Lesson Delivery, and Library Usage": [
                    f"Lesson Preparation Duration: {t_day_ld:.1f} Minutes" + (f" ({ld_pct:.0f}% of Academic Benchmark)" if enable_quant_kpi else ""),
                    f"Library & Digital Resources Duration: {t_day_lib:.1f} Minutes" + (f" ({lib_pct:.0f}% of Academic Benchmark)" if enable_quant_kpi else ""),
                    f"Consultant Assessment: {ld_advice} in lesson preparation, {lib_advice} in library integration."
                ],
                "2. Content / Digital Book Content Usage": pdf_book_items,
                "3. Activity Evidence, Activity Submission, and Artifact Evidence": pdf_link_items if pdf_link_items else ["No activity or evidence submission links recorded in active window."]
            }

            pdf_tab4_summary = generate_pdf_report(
                title_text=f"🏫 Academic Performance Profile: {target_teacher}",
                subtitle_text=f"Observation Window: {filter_description_text}",
                school_name=teacher_school,
                summary_metrics={
                    "Teacher": target_teacher,
                    "Lesson Prep": f"{t_day_ld:.1f}m",
                    "Library Usage": f"{t_day_lib:.1f}m",
                    "Phonics / Portfolio": f"{len(v_phonics)} / {len(v_portfolio)}",
                    "Activity Submissions": f"{total_artifacts}"
                },
                dataframe=None,
                custom_sections=pdf_custom_sections
            )

            with col_btn_top:
                st.markdown("<br>", unsafe_allow_html=True)
                st.download_button(
                    label="📥 Download 360° Profile (PDF)",
                    data=pdf_tab4_summary,
                    file_name=f"{target_teacher.replace(' ', '_')}_360_Profile_Report.pdf",
                    mime="application/pdf",
                    key="top_pdf_download_btn"
                )

            with col_bulk_btn:
                st.markdown("<br>", unsafe_allow_html=True)
                school_teachers_list = sorted(school_master_roster[school_master_roster['Institution'] == teacher_school]['FullName'].unique().tolist())
                bulk_pdf_data = generate_comprehensive_school_pdf_report(
                    school_name=teacher_school,
                    teachers_list=school_teachers_list,
                    school_filtered_df=school_filtered_df,
                    filtered_df=filtered_df,
                    filter_desc=filter_description_text,
                    calc_ld_kpi=calc_ld_kpi,
                    calc_lib_kpi=calc_lib_kpi,
                    daily_ld_target=daily_ld_target,
                    daily_lib_target=daily_lib_target,
                    selected_num_days=selected_num_days,
                    target_vid_count=target_vid_count,
                    target_writing_count=target_writing_count,
                    target_lp_combo_count=target_lp_combo_count,
                    target_phonics_count=target_phonics_count,
                    target_portfolio_count=target_portfolio_count,
                    enable_quant_kpi=enable_quant_kpi,
                    enable_qual_kpi=enable_qual_kpi
                )
                st.download_button(
                    label="📥 Download Bulk School 360 Profiles (PDF)",
                    data=bulk_pdf_data,
                    file_name=f"{teacher_school.replace(' ', '_')}_Comprehensive_School_Report.pdf",
                    mime="application/pdf",
                    key="bulk_school_pdf_btn"
                )

            st.markdown(f"### 📋 Audit Profile: **{target_teacher}** | School: **{teacher_school}**")

            with st.expander("✨ Gemini AI Comprehensive Teacher Evaluation Report", expanded=False):
                if st.button("Generate AI Teacher 360 Review", key="ai_btn_tab5"):
                    with st.spinner("Generating comprehensive teacher evaluation with Gemini..."):
                        review_prompt = f"Write an academic manager review for teacher {target_teacher} at {teacher_school}. Lesson prep: {t_day_ld:.1f} mins, Library usage: {t_day_lib:.1f} mins, Phonics evidence: {len(v_phonics)}, Portfolio uploads: {len(v_portfolio)}, Activity videos: {len(v_vid)}, Writing samples: {len(v_writing)}. Provide constructive feedback."
                        ai_eval = get_gemini_summary(review_prompt)
                        st.markdown(ai_eval)

            st.subheader("1. Quantitative Performance Indicator Summary")
            st.info(f"📅 **Active Filter**: `{filter_description_text}` | **Performance Indicator Duration**: `{selected_num_days} Working Day(s)`")

            col_sum1, col_sum2 = st.columns([1, 1.2])

            with col_sum1:
                st.markdown("##### 📌 Quantitative Performance Indicator Overview")
                s1, s2 = st.columns(2)
                s1.metric("Lesson Prep Mins", f"{t_day_ld:.1f} mins", delta=f"{ld_pct:.0f}% of Standard" if enable_quant_kpi else None)
                s2.metric("Library Usage Mins", f"{t_day_lib:.1f} mins", delta=f"{lib_pct:.0f}% of Standard" if enable_quant_kpi else None)
                
                st.markdown("##### 💡 Academic Consultant Observation")
                if calc_ld_kpi == 0 and calc_lib_kpi == 0:
                    st.info(f"🏖️ **Break Period**: Active filter falls on an excluded calendar break.")
                elif t_day_ld >= calc_ld_kpi and t_day_lib >= calc_lib_kpi:
                    st.success(f"👏 **Consistent Delivery**: {target_teacher} maintained steady curriculum prep and library engagement.")
                elif t_day_ld < calc_ld_kpi and t_day_lib < calc_lib_kpi:
                    st.warning(f"💡 **Growth Opportunity**: Focus on structured digital planning hours and library exploration.")
                else:
                    st.info(f"📌 **Balanced Usage**: Progress noted with potential to scale integration.")

                st.write(f"• **Lesson Plan Preparation**: {ld_advice}")
                st.write(f"• **Library Usage Engagement**: {lib_advice}")

            with col_sum2:
                st.markdown("##### 📊 Performance Indicator Achievement Comparison")
                ach_df = pd.DataFrame({
                    'Performance Indicator Category': [f'Lesson Prep ({calc_ld_kpi:.0f}m)' if enable_quant_kpi else 'Lesson Prep', 
                                                       f'Library Usage ({calc_lib_kpi:.0f}m)' if enable_quant_kpi else 'Library Usage'],
                    'Logged Minutes': [t_day_ld, t_day_lib],
                    'Performance Indicator Standard': [calc_ld_kpi, calc_lib_kpi]
                })
                
                fig_ach = go.Figure()
                fig_ach.add_trace(go.Bar(
                    x=ach_df['Performance Indicator Category'], y=ach_df['Logged Minutes'],
                    name='Logged Minutes', marker_color='#2CA02C', text=[f"{v:.1f} mins" for v in ach_df['Logged Minutes']], textposition='auto'
                ))
                if enable_quant_kpi:
                    fig_ach.add_trace(go.Bar(
                        x=ach_df['Performance Indicator Category'], y=ach_df['Performance Indicator Standard'],
                        name='Standard Guideline', marker_color='#E5E5E5', opacity=0.6, text=[f"{v:.1f} mins" for v in ach_df['Performance Indicator Standard']], textposition='auto'
                    ))
                fig_ach.update_layout(
                    barmode='group', title=f"Logged Minutes vs. Standard Guideline ({selected_num_days} Working Day(s))",
                    height=280, margin=dict(l=20, r=20, t=40, b=20)
                )
                st.plotly_chart(fig_ach, use_container_width=True)

            st.markdown("---")

            st.subheader("2. Detailed Textbook & Chapter Time Breakdown")
            if teacher_books.empty:
                st.info(f"No digital textbooks or modules recorded for **{target_teacher}**.")
            else:
                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    t_book_summary = teacher_books.groupby(['Book', 'Grade', 'Subject'])['Duration_Min'].sum().reset_index()
                    fig_tb_bar = px.bar(
                        t_book_summary, x="Duration_Min", y="Book", color="Grade", orientation="h",
                        title=f"Time Spent per Book/Chapter by {target_teacher} (Minutes)",
                        labels={"Duration_Min": "Time Spent (Minutes)", "Book": "Book / Chapter"},
                        text_auto=".1f"
                    )
                    fig_tb_bar.update_layout(yaxis={'categoryorder':'total ascending'}, height=320)
                    st.plotly_chart(fig_tb_bar, use_container_width=True)
                    
                with col_b2:
                    st.markdown("##### ⏱️ Time Allocation Table")
                    display_book_table = t_book_summary.rename(columns={'Book': 'Textbook / Module', 'Grade': 'Grade', 'Subject': 'Subject', 'Duration_Min': 'Time Spent (Mins)'}).round({'Time Spent (Mins)': 1})
                    st.dataframe(display_book_table, use_container_width=True)

            st.markdown("---")

            st.subheader("3. Qualitative Evidences & Artifact Hub (Phonics & Portfolio Integrated)")

            v_cols = st.columns(5)
            v_cols[0].metric("📖 LP / Audio Notes", f"{lp_combo_total}", delta=f"{len(v_voice)} Audio | {len(v_pic)} Img")
            v_cols[1].metric("🎥 Activity Videos", f"{len(v_vid)}")
            v_cols[2].metric("📝 Writing Samples", f"{len(v_writing)}")
            v_cols[3].metric("🔤 Phonics Evidence", f"{len(v_phonics)}")
            v_cols[4].metric("📁 Portfolio Uploads", f"{len(v_portfolio)}")

            st.markdown("##### 📌 Detailed Evidence Submissions & Direct Artifact Links")
            q_cols1, q_cols2, q_cols3 = st.columns(3)
            
            with q_cols1:
                st.markdown("###### 📖 1. Lesson Plans & Pre-Class Voice Notes")
                combined_lp_items = []
                for i, item in enumerate(v_voice, 1):
                    combined_lp_items.append(f'🎧 <a href="{item["url"]}"><u><b>Audio Reflection #{i}</b></u></a> - **{item["grade"]}** | *{item["subject"]}* ({item["lesson"]}, {item["date"]})')
                for i, item in enumerate(v_pic, 1):
                    combined_lp_items.append(f'🖼️ <a href="{item["url"]}"><u><b>LP Photo #{i}</b></u></a> - **{item["grade"]}** | *{item["subject"]}* ({item["lesson"]}, {item["date"]})')
                if combined_lp_items:
                    for line in combined_lp_items: st.markdown(f"• {line}", unsafe_allow_html=True)
                else:
                    st.caption("No lesson plans or voice reflections submitted.")

            with q_cols2:
                st.markdown("###### 🎥 2. Classroom Videos & Student Writing")
                for i, item in enumerate(v_vid, 1):
                    st.markdown(f'• 🎥 <a href="{item["url"]}"><u><b>Classroom Video #{i}</b></u></a> - **{item["grade"]}** | *{item["subject"]}* ({item["lesson"]}, {item["date"]})', unsafe_allow_html=True)
                for i, item in enumerate(v_writing, 1):
                    st.markdown(f'• 📝 <a href="{item["url"]}"><u><b>Writing Sample #{i}</b></u></a> - **{item["grade"]}** | *{item["subject"]}* ({item["lesson"]}, {item["date"]})', unsafe_allow_html=True)
                if not v_vid and not v_writing:
                    st.caption("No activity videos or writing samples uploaded.")

            with q_cols3:
                st.markdown("###### 🔤 3. Phonics Implementation & Portfolio Showcase")
                for i, item in enumerate(v_phonics, 1):
                    st.markdown(f'• 🔤 <a href="{item["url"]}"><u><b>Phonics Evidence #{i}</b></u></a> - **{item["grade"]}** | *{item["subject"]}* ({item["lesson"]}, {item["date"]})', unsafe_allow_html=True)
                for i, item in enumerate(v_portfolio, 1):
                    st.markdown(f'• 📁 <a href="{item["url"]}"><u><b>Portfolio Artifact #{i}</b></u></a> - **{item["grade"]}** | *{item["subject"]}* ({item["lesson"]}, {item["date"]})', unsafe_allow_html=True)
                if not v_phonics and not v_portfolio:
                    st.caption("No phonics implementation or portfolio files uploaded.")

            st.markdown("---")

            col_log_head, col_log_filt = st.columns([2, 1])
            with col_log_head:
                st.subheader(f"4. Granular Classroom Audit Log for {target_teacher}")
            with col_log_filt:
                available_types = ["All Types"] + sorted(teacher_all_data['Type'].dropna().unique().tolist())
                selected_type_filter = st.selectbox("Filter Audit Log by Type:", options=available_types)

            if selected_type_filter == "All Types":
                filtered_audit_log = teacher_all_data
            else:
                filtered_audit_log = teacher_all_data[teacher_all_data['Type'] == selected_type_filter]

            t_log_cols = ['Date', 'Type', 'Grade', 'Subject', 'Book', 'StartTime', 'Phonics_Evidence_Link', 'Portfolio_Evidence_Link', 'Voice_Note_Link', 'Video_Evidence_1', 'Writing_Sample_Link', 'Duration (HH:MM:SS)', 'Duration_Min']
            t_avail_cols = [c for c in t_log_cols if c in filtered_audit_log.columns]
            
            if filtered_audit_log.empty:
                st.info(f"No logs found for type `{selected_type_filter}` during `{filter_description_text}`.")
            else:
                t_display_log = filtered_audit_log[t_avail_cols].rename(columns={'Duration_Min': 'Minutes'}).sort_values(by='StartTime', ascending=False)
                t_display_log['Minutes'] = t_display_log['Minutes'].round(1)
                st.dataframe(t_display_log, use_container_width=True)

                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    buf_p1_xlsx = BytesIO()
                    with pd.ExcelWriter(buf_p1_xlsx, engine='openpyxl') as writer:
                        t_display_log.to_excel(writer, index=False, sheet_name='Teacher_Audit')
                    buf_p1_xlsx.seek(0)
                    st.download_button(
                        label=f"📥 Download Full Excel Audit for {target_teacher}",
                        data=buf_p1_xlsx,
                        file_name=f"{target_teacher.replace(' ', '_')}_{selected_type_filter}_Audit.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

            tab4_metrics_summary = f"Teacher Audit: {target_teacher} (School: {teacher_school}), Lesson Prep: {t_day_ld:.1f}m, Library Usage: {t_day_lib:.1f}m, Phonics: {len(v_phonics)}, Portfolio: {len(v_portfolio)}, Activity Submissions: {total_artifacts}"
            render_universal_crm_box("Teacher 360 Profile", teacher_school, filter_description_text, tab4_metrics_summary)

    # TAB 6: MANAGER PORTFOLIO & SCHOOL QUADRANTS
    with tab6:
        st.header("🏛️ Academic Manager Portfolio Overview")
        st.caption("High-level classification, Quantitative indicators, and Week-on-Week Velocity tracking across your school portfolio.")

        if school_filtered_df.empty:
            st.warning("No data available for the selected school filter.")
        else:
            school_stats = filtered_df.groupby(['Institution', 'Type'])['Duration_Min'].sum().unstack(fill_value=0.0).reset_index()
            
            if 'lessonDelivery' not in school_stats.columns: school_stats['lessonDelivery'] = 0.0
            if 'library' not in school_stats.columns: school_stats['library'] = 0.0
            
            all_active_schools = school_filtered_df['Institution'].unique()
            for s_name in all_active_schools:
                if s_name not in school_stats['Institution'].values:
                    new_row = pd.DataFrame({'Institution': [s_name], 'lessonDelivery': [0.0], 'library': [0.0]})
                    school_stats = pd.concat([school_stats, new_row], ignore_index=True)

            school_roster_count = school_master_roster.groupby('Institution')['FullName'].nunique().reset_index().rename(columns={'FullName': 'Roster_Teachers'})
            school_stats = school_stats.merge(school_roster_count, on='Institution', how='left').fillna(1)

            school_stats['Avg_Lesson_Prep_Mins'] = (school_stats['lessonDelivery'] / school_stats['Roster_Teachers'] / selected_num_days).round(1)
            school_stats['Avg_Library_Usage_Mins'] = (school_stats['library'] / school_stats['Roster_Teachers'] / selected_num_days).round(1)

            qual_agg = []
            for s_name in school_stats['Institution'].unique():
                s_data = filtered_df[filtered_df['Institution'] == s_name]
                s_vids = 0
                for vc in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                    if vc in s_data.columns:
                        s_vids += len([l for l in s_data[vc].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])
                s_w = len([l for l in s_data['Writing_Sample_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Writing_Sample_Link' in s_data.columns else 0
                s_lp = len([l for l in s_data['Lesson_Plan_Picture'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in s_data.columns else 0
                s_vn = len([l for l in s_data['Voice_Note_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in s_data.columns else 0
                s_ph = len([l for l in s_data['Phonics_Evidence_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Phonics_Evidence_Link' in s_data.columns else 0
                s_pf = len([l for l in s_data['Portfolio_Evidence_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Portfolio_Evidence_Link' in s_data.columns else 0

                qual_agg.append({
                    'Institution': s_name,
                    'Activity_Videos': s_vids,
                    'Writing_Samples': s_w,
                    'LP_Audio_Submissions': s_lp + s_vn,
                    'Phonics_Evidences': s_ph,
                    'Portfolio_Artifacts': s_pf
                })
            
            qual_df_school = pd.DataFrame(qual_agg)
            school_stats = school_stats.merge(qual_df_school, on='Institution', how='left').fillna(0)

            def classify_school(row):
                if not enable_quant_kpi:
                    return 'Active Portfolio'
                ld_ok = row['Avg_Lesson_Prep_Mins'] >= daily_ld_target
                lib_ok = row['Avg_Library_Usage_Mins'] >= daily_lib_target
                qual_ok = True
                if enable_qual_kpi:
                    qual_ok = (row['Activity_Videos'] >= target_vid_count) or (row['Writing_Samples'] >= target_writing_count)

                if ld_ok and lib_ok and qual_ok:
                    return '🌟 Pace Setters'
                elif ld_ok and not lib_ok:
                    return '📘 Lesson Focused'
                elif not ld_ok and lib_ok:
                    return '📚 Library Focused'
                else:
                    return '🚨 Priority Focus'

            school_stats['Classification'] = school_stats.apply(classify_school, axis=1)

            st.subheader("🖼️ 2x2 Portfolio Classification Matrix")
            
            pace_setters = school_stats[school_stats['Classification'] == '🌟 Pace Setters']['Institution'].tolist()
            lesson_focused = school_stats[school_stats['Classification'] == '📘 Lesson Focused']['Institution'].tolist()
            library_focused = school_stats[school_stats['Classification'] == '📚 Library Focused']['Institution'].tolist()
            priority_focus = school_stats[school_stats['Classification'] == '🚨 Priority Focus']['Institution'].tolist()

            col_top1, col_top2 = st.columns(2)
            with col_top1:
                st.success(f"🌟 **Pace Setters ({len(pace_setters)} Schools)**\n\n*Met Lesson Prep, Library & Qualitative Standards*\n\n" + (", ".join(pace_setters) if pace_setters else "None"))
            with col_top2:
                st.info(f"📘 **Lesson Focused ({len(lesson_focused)} Schools)**\n\n*Met Lesson Prep, Below Library/Artifact Targets*\n\n" + (", ".join(lesson_focused) if lesson_focused else "None"))

            col_bot1, col_bot2 = st.columns(2)
            with col_bot1:
                st.warning(f"📚 **Library Focused ({len(library_focused)} Schools)**\n\n*Met Library, Below Lesson Prep Targets*\n\n" + (", ".join(library_focused) if library_focused else "None"))
            with col_bot2:
                st.error(f"🚨 **Priority Focus ({len(priority_focus)} Schools)**\n\n*Below Quantitative & Qualitative Standards*\n\n" + (", ".join(priority_focus) if priority_focus else "None"))

            st.markdown("---")
            st.subheader("📋 Complete School Performance Leaderboard (Quantitative & Qualitative)")
            display_qtable = school_stats[['Institution', 'Roster_Teachers', 'Avg_Lesson_Prep_Mins', 'Avg_Library_Usage_Mins', 'LP_Audio_Submissions', 'Activity_Videos', 'Writing_Samples', 'Phonics_Evidences', 'Portfolio_Artifacts', 'Classification']].rename(columns={
                'Institution': 'School Name',
                'Roster_Teachers': 'Active Teachers',
                'Avg_Lesson_Prep_Mins': 'Prep (m/day)',
                'Avg_Library_Usage_Mins': 'Library (m/day)',
                'LP_Audio_Submissions': 'LP/Audio Notes',
                'Activity_Videos': 'Activity Videos',
                'Writing_Samples': 'Writing Samples',
                'Phonics_Evidences': 'Phonics Uploads',
                'Portfolio_Artifacts': 'Portfolio Uploads'
            })
            st.dataframe(display_qtable, use_container_width=True)

            col_t5_d1, col_t5_d2 = st.columns(2)
            with col_t5_d1:
                pdf_tab5 = generate_pdf_report(
                    title_text="🏛️ Academic Manager Portfolio Review",
                    subtitle_text=f"Portfolio Performance Leaderboard ({selected_num_days} Working Days)",
                    school_name="Multiple Portfolio Schools",
                    summary_metrics={
                        "Total Schools": len(school_stats),
                        "Pace Setters": len(pace_setters),
                        "Priority Focus": len(priority_focus)
                    },
                    dataframe=display_qtable
                )
                st.download_button(
                    label="📄 Download Portfolio Overview Report (PDF)",
                    data=pdf_tab5,
                    file_name=f"Manager_Portfolio_Overview_{selected_month.replace(' ', '_')}.pdf",
                    mime="application/pdf"
                )
            with col_t5_d2:
                buf_t5_xlsx = BytesIO()
                with pd.ExcelWriter(buf_t5_xlsx, engine='openpyxl') as writer:
                    display_qtable.to_excel(writer, index=False, sheet_name='Portfolio_Leaderboard')
                buf_t5_xlsx.seek(0)
                st.download_button(
                    label="📥 Download Portfolio Leaderboard (Excel)",
                    data=buf_t5_xlsx,
                    file_name=f"Portfolio_Leaderboard_{selected_month.replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

            school_breakdown_summary = "\n".join([f"• {r['School Name']}: Prep {r['Prep (m/day)']}m/day, Library {r['Library (m/day)']}m/day ({r['Classification']})" for _, r in display_qtable.iterrows()])
            tab5_metrics_summary = (
                f"Total Portfolio Schools Tracked: {len(school_stats)} | Pace Setters: {len(pace_setters)} | Priority Focus: {len(priority_focus)}\n\n"
                f"School Performance Breakdown:\n{school_breakdown_summary}"
            )
            render_universal_crm_box("Manager Portfolio", selected_schools, filter_description_text, tab5_metrics_summary)

    # TAB 7: SCHOOL-LEVEL TEACHER PROGRESSION & EXECUTION TIERS
    with tab7:
        st.header("🏫 School-Level Teacher Progression & Execution Tiers")
        st.caption("Drill down into any individual school to classify teachers into execution tiers based on benchmark standards.")

        all_schools_list_t6 = sorted(school_master_roster['Institution'].unique())
        
        if not all_schools_list_t6:
            st.info("No schools found in roster.")
        else:
            target_school_t6 = st.selectbox("Select School to Inspect:", options=all_schools_list_t6)

            school_t6_roster = school_master_roster[school_master_roster['Institution'] == target_school_t6]
            school_t6_data = school_filtered_df[school_filtered_df['Institution'] == target_school_t6]

            st.markdown(f"### 🏫 School Audit: **{target_school_t6}** | Active Roster: **{len(school_t6_roster)} Teachers**")

            st.subheader("1. Teacher Execution Tiers")

            t6_ld = school_t6_data[school_t6_data['Type'] == 'lessonDelivery'].groupby('FullName')['Duration_Min'].sum().reset_index()
            t6_lib = school_t6_data[school_t6_data['Type'] == 'library'].groupby('FullName')['Duration_Min'].sum().reset_index()

            t6_teachers = school_t6_roster.merge(t6_ld.rename(columns={'Duration_Min': 'Lesson_Mins'}), on='FullName', how='left').fillna(0.0)
            t6_teachers = t6_teachers.merge(t6_lib.rename(columns={'Duration_Min': 'Library_Mins'}), on='FullName', how='left').fillna(0.0)

            def tier_teacher(row):
                ld_pct = (row['Lesson_Mins'] / calc_ld_kpi) if calc_ld_kpi > 0 else 1.0
                lib_pct = (row['Library_Mins'] / calc_lib_kpi) if calc_lib_kpi > 0 else 1.0

                if ld_pct >= 1.0 and lib_pct >= 1.0:
                    return '🌟 Consistent Achiever (>= 100%)'
                elif ld_pct < 0.40 and lib_pct < 0.40:
                    return '❌ Persistent Inactive (< 40%)'
                else:
                    return '⚠️ Fluctuating / Partial (40%-99%)'

            t6_teachers['Execution_Tier'] = t6_teachers.apply(tier_teacher, axis=1)

            e1, e2, e3 = st.columns(3)
            num_ach = len(t6_teachers[t6_teachers['Execution_Tier'].str.startswith('🌟')])
            num_fluc = len(t6_teachers[t6_teachers['Execution_Tier'].str.startswith('⚠️')])
            num_inact = len(t6_teachers[t6_teachers['Execution_Tier'].str.startswith('❌')])

            e1.metric("🌟 Consistent Achievers", num_ach)
            e2.metric("⚠️ Fluctuating / Partial", num_fluc)
            e3.metric("❌ Persistent Inactive", num_inact)

            fig_t6_bar = px.bar(
                t6_teachers, x="FullName", y=["Lesson_Mins", "Library_Mins"],
                title=f"Teacher Usage Breakdown for {target_school_t6} (Mins)",
                labels={"FullName": "Teacher Name", "value": "Logged Minutes", "variable": "Feature"},
                barmode="group", text_auto=".1f"
            )
            st.plotly_chart(fig_t6_bar, use_container_width=True)

            st.subheader("📋 Teacher Execution Tier Table")
            display_t6_table = t6_teachers.rename(columns={'FullName': 'Teacher Name', 'Lesson_Mins': 'Lesson Prep (m)', 'Library_Mins': 'Library Usage (m)', 'Execution_Tier': 'Execution Tier'})
            st.dataframe(display_t6_table, use_container_width=True)

            col_t6_d1, col_t6_d2 = st.columns(2)
            with col_t6_d1:
                pdf_tab6 = generate_comprehensive_school_pdf_report(
                    school_name=target_school_t6,
                    teachers_list=school_t6_roster['FullName'].unique().tolist(),
                    school_filtered_df=school_filtered_df,
                    filtered_df=filtered_df,
                    filter_desc=filter_description_text,
                    calc_ld_kpi=calc_ld_kpi,
                    calc_lib_kpi=calc_lib_kpi,
                    daily_ld_target=daily_ld_target,
                    daily_lib_target=daily_lib_target,
                    selected_num_days=selected_num_days,
                    target_vid_count=target_vid_count,
                    target_writing_count=target_writing_count,
                    target_lp_combo_count=target_lp_combo_count,
                    target_phonics_count=target_phonics_count,
                    target_portfolio_count=target_portfolio_count,
                    enable_quant_kpi=enable_quant_kpi,
                    enable_qual_kpi=enable_qual_kpi
                )
                st.download_button(
                    label=f"📄 Download {target_school_t6} Report (PDF)",
                    data=pdf_tab6,
                    file_name=f"{target_school_t6.replace(' ', '_')}_Execution_Report.pdf",
                    mime="application/pdf"
                )
            with col_t6_d2:
                buf_t6_xlsx = BytesIO()
                with pd.ExcelWriter(buf_t6_xlsx, engine='openpyxl') as writer:
                    display_t6_table.to_excel(writer, index=False, sheet_name="School_Teacher_Tiers")
                buf_t6_xlsx.seek(0)
                st.download_button(
                    label=f"📥 Download {target_school_t6} Data (Excel)",
                    data=buf_t6_xlsx,
                    file_name=f"{target_school_t6.replace(' ', '_')}_Tiers.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

            t6_teacher_breakdown = "\n".join([f"• **{r['Teacher Name']}**: Prep {r['Lesson Prep (m)']}m, Library {r['Library Usage (m)']}m ({r['Execution Tier']})" for _, r in display_t6_table.iterrows()])
            tab6_metrics_summary = (
                f"School Inspection: {target_school_t6} | Achievers: {num_ach} | Fluctuating: {num_fluc} | Inactive: {num_inact}\n\n"
                f"Teacher Progression Breakdown:\n{t6_teacher_breakdown}"
            )
            render_universal_crm_box("School Inspection", target_school_t6, filter_description_text, tab6_metrics_summary)

    # TAB 8: GLOBAL LIVE EVIDENCE SUBMISSIONS FEED & QUALITATIVE PERFORMANCE INDICATOR TRACKER
    with tab8:
        st.header("📬 Live Evidence Submissions Feed & Qualitative Performance Indicator Tracker")
        if enable_qual_kpi:
            st.caption(f"Track submissions against Qualitative Performance Indicators (Min. {target_vid_count} Videos, Min. {target_writing_count} Writing, Min. {target_lp_combo_count} LP/Voice Notes, Min. {target_phonics_count} Phonics, Min. {target_portfolio_count} Portfolio).")
        else:
            st.caption("Complete log of all qualitative evidence submissions from the Teacher Portal across the filtered database.")

        evidence_cols = ['Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link', 'Phonics_Evidence_Link', 'Portfolio_Evidence_Link']
        avail_ev_cols = [c for c in evidence_cols if c in filtered_df.columns]

        def has_valid_evidence(row):
            for col in avail_ev_cols:
                val = str(row[col]).strip()
                if re.match(r'^https?://', val, re.IGNORECASE):
                    return True
            return False

        all_submissions_df = filtered_df[filtered_df.apply(has_valid_evidence, axis=1)].copy() if not filtered_df.empty and avail_ev_cols else pd.DataFrame()

        if all_submissions_df.empty:
            st.info("No teacher evidence submissions match the currently selected global filter criteria.")
        else:
            col_t7_f1, col_t7_f2, col_t7_f3 = st.columns(3)
            with col_t7_f1:
                t7_schools = ["All Schools"] + sorted([s for s in all_submissions_df['Institution'].unique() if str(s).strip()])
                t7_selected_school = st.selectbox("Filter by School:", t7_schools, key="t7_school")
            
            t7_filtered = all_submissions_df if t7_selected_school == "All Schools" else all_submissions_df[all_submissions_df['Institution'] == t7_selected_school]

            with col_t7_f2:
                t7_teachers = ["All Teachers"] + sorted([t for t in t7_filtered['FullName'].unique() if str(t).strip()])
                t7_selected_teacher = st.selectbox("Filter by Teacher:", t7_teachers, key="t7_teacher")

            if t7_selected_teacher != "All Teachers":
                t7_filtered = t7_filtered[t7_filtered['FullName'] == t7_selected_teacher]

            with col_t7_f3:
                t7_grades = ["All Grades"] + sorted([g for g in t7_filtered['Grade'].unique() if str(g).strip()])
                t7_selected_grade = st.selectbox("Filter by Grade:", t7_grades, key="t7_grade")

            if t7_selected_grade != "All Grades":
                t7_filtered = t7_filtered[t7_filtered['Grade'] == t7_selected_grade]

            st.markdown("---")

            tot_subs = len(t7_filtered)
            tot_audios = sum([1 for l in t7_filtered['Voice_Note_Link'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in t7_filtered.columns else 0
            tot_pics = sum([1 for l in t7_filtered['Lesson_Plan_Picture'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in t7_filtered.columns else 0
            tot_writing = sum([1 for l in t7_filtered['Writing_Sample_Link'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Writing_Sample_Link' in t7_filtered.columns else 0
            tot_phonics = sum([1 for l in t7_filtered['Phonics_Evidence_Link'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Phonics_Evidence_Link' in t7_filtered.columns else 0
            tot_portfolio = sum([1 for l in t7_filtered['Portfolio_Evidence_Link'] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Portfolio_Evidence_Link' in t7_filtered.columns else 0

            tot_vids = 0
            for vc in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                if vc in t7_filtered.columns:
                    tot_vids += sum([1 for l in t7_filtered[vc] if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])

            m_c1, m_c2, m_c3, m_c4, m_c5, m_c6 = st.columns(6)
            m_c1.metric("📋 Total Submissions", tot_subs)
            m_c2.metric("🎧 Audio Notes", tot_audios)
            m_c3.metric("🖼️ LP Pictures", tot_pics)
            m_c4.metric("🎥 Activity Videos", tot_vids)
            m_c5.metric("🔤 Phonics Evidence", tot_phonics)
            m_c6.metric("📁 Portfolio Uploads", tot_portfolio)

            st.markdown("---")

            if enable_qual_kpi:
                st.subheader("🎯 Teacher Qualitative Performance Indicator Compliance Table")

                teacher_kpi_records = []
                target_roster = filtered_roster if t7_selected_school == "All Schools" else filtered_roster[filtered_roster['Institution'] == t7_selected_school]
                if t7_selected_teacher != "All Teachers":
                    target_roster = target_roster[target_roster['FullName'] == t7_selected_teacher]

                for _, t_row in target_roster.iterrows():
                    t_name = t_row['FullName']
                    t_inst = t_row['Institution']
                    sub_t_data = t7_filtered[(t7_filtered['FullName'] == t_name) & (t7_filtered['Institution'] == t_inst)]
                    
                    v_count = 0
                    for vc in ['Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3']:
                        if vc in sub_t_data.columns:
                            v_count += len([l for l in sub_t_data[vc].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)])
                    
                    w_count = len([l for l in sub_t_data['Writing_Sample_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Writing_Sample_Link' in sub_t_data.columns else 0
                    lp_pic_count = len([l for l in sub_t_data['Lesson_Plan_Picture'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Lesson_Plan_Picture' in sub_t_data.columns else 0
                    vn_count = len([l for l in sub_t_data['Voice_Note_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Voice_Note_Link' in sub_t_data.columns else 0
                    lp_combo_total = lp_pic_count + vn_count
                    ph_count = len([l for l in sub_t_data['Phonics_Evidence_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Phonics_Evidence_Link' in sub_t_data.columns else 0
                    pf_count = len([l for l in sub_t_data['Portfolio_Evidence_Link'].dropna().unique() if re.match(r'^https?://', str(l).strip(), re.IGNORECASE)]) if 'Portfolio_Evidence_Link' in sub_t_data.columns else 0

                    is_compliant = (v_count >= target_vid_count and w_count >= target_writing_count and lp_combo_total >= target_lp_combo_count and ph_count >= target_phonics_count and pf_count >= target_portfolio_count)
                    overall_status = "🌟 Fully Compliant" if is_compliant else "⚠️ In Progress"
                    
                    teacher_kpi_records.append({
                        'School': t_inst,
                        'Teacher Name': t_name,
                        'Activity Videos': v_count,
                        'Writing Samples': w_count,
                        'LP / Voice Notes': lp_combo_total,
                        'Phonics Evidences': ph_count,
                        'Portfolio Uploads': pf_count,
                        'Overall Qualitative Status': overall_status
                    })

                kpi_summary_df = pd.DataFrame(teacher_kpi_records)
                st.dataframe(kpi_summary_df, use_container_width=True)

            st.subheader("📋 Granular Qualitative Submissions Log")
            t7_display_cols = ['StartTime', 'Institution', 'FullName', 'Grade', 'Subject', 'Book', 'Phonics_Evidence_Link', 'Portfolio_Evidence_Link', 'Voice_Note_Link', 'Lesson_Plan_Picture', 'Video_Evidence_1', 'Video_Evidence_2', 'Video_Evidence_3', 'Writing_Sample_Link']
            t7_avail = [c for c in t7_display_cols if c in t7_filtered.columns]
            
            t7_table = t7_filtered[t7_avail].sort_values(by='StartTime', ascending=False)
            st.dataframe(t7_table, use_container_width=True)

            col_t7_dl1, col_t7_dl2 = st.columns(2)
            with col_t7_dl1:
                buf_t7_xlsx = BytesIO()
                with pd.ExcelWriter(buf_t7_xlsx, engine='openpyxl') as writer:
                    t7_table.to_excel(writer, index=False, sheet_name='Evidence_Submissions')
                buf_t7_xlsx.seek(0)
                st.download_button(
                    label="📥 Download Evidence Submissions (Excel)",
                    data=buf_t7_xlsx,
                    file_name=f"Evidence_Submissions_Feed_{selected_month.replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            with col_t7_dl2:
                pdf_t7 = generate_comprehensive_school_pdf_report(
                    school_name=t7_selected_school if t7_selected_school != "All Schools" else "Multiple Schools Portfolio",
                    teachers_list=filtered_roster['FullName'].unique().tolist(),
                    school_filtered_df=school_filtered_df,
                    filtered_df=filtered_df,
                    filter_desc=filter_description_text,
                    calc_ld_kpi=calc_ld_kpi,
                    calc_lib_kpi=calc_lib_kpi,
                    daily_ld_target=daily_ld_target,
                    daily_lib_target=daily_lib_target,
                    selected_num_days=selected_num_days,
                    target_vid_count=target_vid_count,
                    target_writing_count=target_writing_count,
                    target_lp_combo_count=target_lp_combo_count,
                    target_phonics_count=target_phonics_count,
                    target_portfolio_count=target_portfolio_count,
                    enable_quant_kpi=enable_quant_kpi,
                    enable_qual_kpi=enable_qual_kpi
                )
                st.download_button(
                    label="📄 Download Evidence Feed Summary (PDF)",
                    data=pdf_t7,
                    file_name=f"Evidence_Submissions_Report_{selected_month.replace(' ', '_')}.pdf",
                    mime="application/pdf"
                )

            tab7_metrics_summary = (
                f"Active KPIs: Min {target_vid_count} Vids, {target_writing_count} Writing, {target_lp_combo_count} LP/Audio, {target_phonics_count} Phonics, {target_portfolio_count} Portfolio\n"
                f"Total Submission Logs: {tot_subs} | Phonics: {tot_phonics} | Portfolio: {tot_portfolio} | Videos: {tot_vids}"
            )
            render_universal_crm_box("Live Evidence Feed", t7_selected_school if t7_selected_school != "All Schools" else selected_schools, filter_description_text, tab7_metrics_summary)
