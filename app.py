import streamlit as st
import requests
import json
import csv
import io
import os
import gc
import google.generativeai as genai
from PIL import Image
import concurrent.futures
import pandas as pd
import numpy as np

# --- PWA CONFIGURATION & MOBILE STYLING ---
st.set_page_config(page_title="eRHMIS Smart Upload", page_icon="🚀", layout="centered")

st.markdown("""
    <style>
        .main { max-width: 700px; margin: 0 auto; }
        .stButton>button { width: 100%; border-radius: 8px; height: 3em; font-weight: bold; }
        .stTextInput>div>div>input { border-radius: 8px; }
        #MainMenu {visibility: hidden;} 
        header {visibility: hidden;} 
        footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

BASE_URL = "https://erhmis.fhb.health.gov.lk/erhmis/api"
ADMIN_EMAIL = "bacjanaka@gmail.com"

# --- INITIALIZE SESSION STATES ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_roles" not in st.session_state:
    st.session_state.user_roles = None

# ==========================================
# 🧠 ISOLATED PROMPT CONFIGURATION LIBRARY
# ==========================================
FORM_CONFIGS = {
    "631": {
        "timeframe": "monthly",
        "prompt_template": """
            Your task is to act as an expert data entry assistant for the Sri Lankan Ministry of Health.
            Carefully read the handwritten and printed numbers from the attached images of the health report.
            
            CRITICAL INSTRUCTION - TARGET MONTH: 
            The images may contain a ledger with data for multiple months. 
            You MUST ONLY extract the data for the month of {target_timeframe_text}. 
            Ignore data from any other months, and completely ignore obsolete data before 2025.
            
            HOW TO FIND THE MONTH:
            The months are labeled at the top of the columns with a SINGLE LETTER (J, F, M, A, M, J, J, A, S, O, N, D).
            Look for the column labeled '{target_month_letter}'. Completely ignore 'Quarter' or 'Q' columns (e.g., Q1, Q2, Q3, Q4).

            Look at the 'Field_Description' column in the schema below, match the correct data for {target_timeframe_text}, and type the extracted number into the 'Value' column.

            STRICT RULES:
            1. Output the final result STRICTLY as raw CSV text. Do NOT wrap it in Markdown formatting blocks (do not use ```csv).
            2. Keep the DataElement_ID and Category_ID columns exactly as they appear in the schema. Do not alter these codes.
            3. The final output must have exactly these 4 columns: DataElement_ID, Category_ID, Field_Description, Value.
            4. Do not omit any rows. Every single row from the blueprint must be in your output.
            5. If a field is blank, unreadable, or crossed out for {target_timeframe_text}, leave the Value column completely blank. Do NOT write '0' unless there is literally a '0' written on the form.

            SCHEMA BLUEPRINT (Use this to match IDs):
            {schema_blueprint}
        """
    },
    "1247": {
        "timeframe": "annual",
        "prompt_template": """
            You are an expert data entry assistant for the Sri Lankan Ministry of Health.
            CRITICAL INSTRUCTION: You MUST ONLY extract the data for {target_timeframe_text}.

            CRITICAL VISUAL MAPPING CHEAT SHEET FOR FORM H1247:
            
            0. 🚨 SECTION 6 IS SPLIT ACROSS TWO PAGES (READ CAREFULLY):
               - PAGE 1 BOTTOM contains Items 1 through 10 (Stunting, Wasting, Overweight, Obesity, Referred, Visual, Hearing, Speech, Pallor, Untreated caries).
               - PAGE 2 TOP contains Items 11 through 35 (Calculus, ENT defects, Heart problems, Asthma, etc.).
               
            1. 🛑 PAGE 2 'NIL' LINE IS STRICTLY LIMITED TO PAGE 2:
               - The diagonal blue line and 'NIL' drawn on Page 2 ONLY applies to Items 11 through 35!
               - DO NOT skip Items 1 through 10 at the bottom of Page 1!
               
            2. SECTION 6 (PAGE 1 ITEMS 1 TO 10 EXTRACTION):
               - Item 2: 'Wasting (< -2SD)' -> Extract written digits.
               - Item 3: 'Overweight (> +1SD to +2SD)' -> Extract written digits.
               - Map these condition names + Grade + Gender directly to the exact matching 'Field_Description' in the Schema Blueprint!
               
            3. TOP TABLE ('No. of Children'):
               - Extract numbers written under columns (1) through (13) and map to Grade 1 through Grade 13.
               - Ignore 'Total' rows.
               
            4. SECTION 3 (Officers participated):
               - Read the printed text next to the row number! If '1' is written on Row 7 (PHI), map ONLY to PHI. Do NOT shift to MOH or AMOH.
               
            5. SECTION 4 (Students examined):
               - Match Grade 1, Grade 4, Grade 7, Grade 10, Other directly by title.
               - If an officer wrote totals in "5. Other", DO NOT extract them.
               
            6. SYMBOLS: Checkmark (✓) = 1. Dash (-) or blank = DO NOT output.

            STRICT RULES:
            1. Output STRICTLY as raw CSV text. No markdown blocks (do not use ```csv).
            2. The output MUST contain exactly 4 columns separated by commas. 
            3. STRICT ID MATCHING: The 'DataElement_ID' and 'Category_ID' must be exactly 11 characters. NEVER invent ID strings.
            4. OMIT BLANKS: ONLY output rows where you found a visible handwritten number or checkmark on the assigned pages. 

            SCHEMA BLUEPRINT (Use this to match IDs):
            {schema_blueprint}
        """
    },
    "default": {
        "timeframe": "monthly",
        "prompt_template": """
            You are an expert data entry assistant for the Sri Lankan Ministry of Health.
            CRITICAL INSTRUCTION: You MUST ONLY extract the data for {target_timeframe_text}.
            
            HOW TO FIND THE DATA:
            This is a standard summary form. Scan all uploaded pages.
            Extract the number written directly next to, below, or inside the box for that specific label.

            STRICT RULES:
            1. Output STRICTLY as raw CSV text. No markdown blocks (do not use ```csv).
            2. The output MUST contain exactly 4 columns separated by commas. 
            3. STRICT ID MATCHING: The 'DataElement_ID' and 'Category_ID' must be exactly 11 characters. NEVER invent ID strings.
            4. OMIT BLANKS: ONLY output rows where you found a visible handwritten number or checkmark on the assigned pages. 

            SCHEMA BLUEPRINT (Use this to match IDs):
            {schema_blueprint}
        """
    }
}

# ==========================================
# 🔒 SECURE KEY MATCHER
# ==========================================
def get_api_key(key_name):
    try:
        return st.secrets["ai"][key_name]
    except Exception:
        pass
    try:
        return st.secrets[key_name]
    except Exception:
        pass
    return os.environ.get(key_name, None)

def check_authorization(username):
    try:
        auth_users_str = get_api_key("AUTHORIZED_USERS") or "91-krw-sphi,admin"
        authorized_list = [u.strip() for u in auth_users_str.split(",")]
    except Exception:
        authorized_list = ["91-krw-sphi", "admin"]
    return username.strip().lower() in [user.lower() for user in authorized_list]

# --- LOGIN SCREEN ---
if not st.session_state.authenticated:
    st.title("🔐 eRHMIS Smart Upload")
    st.subheader("Authorized Personnel Only")
    
    with st.form("login_form"):
        username = st.text_input("ERHMIS Username", autocomplete="username")
        password = st.text_input("ERHMIS Password", type="password", autocomplete="current-password")
        app_passcode = st.text_input("App Access Token", type="password", help="Contact admin for your token.")
        
        submit_login = st.form_submit_button("Secure Log In")
        
        if submit_login:
            master_token = get_api_key("MASTER_TOKEN") or "fallback_token"
            
            if not check_authorization(username) and app_passcode != master_token:
                st.error(f"❌ Access Denied. Your account is not whitelisted. Please contact {ADMIN_EMAIL}.")
            else:
                with st.spinner("Authenticating with ERHMIS Server..."):
                    try:
                        res = requests.get(f"{BASE_URL}/me.json", auth=(username, password), timeout=15)
                        if res.status_code == 200:
                            st.session_state.authenticated = True
                            st.session_state.auth = (username, password)
                            st.session_state.username = username
                            st.success("🔓 Access Granted!")
                            st.rerun()
                        else:
                            st.error("❌ Invalid ERHMIS Username or Password.")
                    except requests.exceptions.RequestException:
                        st.error("❌ Connection Timeout. The ERHMIS server might be undergoing maintenance.")
    st.stop()

# ==========================================
# 🎛️ MAIN APPLICATION INTERFACE
# ==========================================
st.title("🚀 eRHMIS Smart Upload")
st.caption(f"Logged in as: {st.session_state.username} | System Admin: {ADMIN_EMAIL}")

if st.sidebar.button("Sign Out"):
    st.session_state.authenticated = False
    st.rerun()

@st.cache_data(show_spinner="Syncing Forms from ERHMIS...")
def fetch_forms(auth):
    res = requests.get(f"{BASE_URL}/dataSets.json?paging=false&fields=id,name", auth=auth, timeout=15)
    return sorted(res.json().get("dataSets", []), key=lambda x: x.get('name', ''))

@st.cache_data(show_spinner="Syncing PHI Areas...")
def fetch_clinics(auth):
    res = requests.get(f"{BASE_URL}/me.json?fields=organisationUnits[id,name,children[id,name,children[id,name]]]", auth=auth, timeout=15)
    clinics = []
    for root in res.json().get("organisationUnits", []):
        for child in root.get("children", []):
            clinics.append({"id": child["id"], "name": child["name"]})
            for grandchild in child.get("children", []):
                clinics.append({"id": grandchild["id"], "name": grandchild["name"]})
    return sorted(clinics, key=lambda x: x['name'])

try:
    available_forms = fetch_forms(st.session_state.auth)
    available_clinics = fetch_clinics(st.session_state.auth)
except Exception as e:
    st.error(f"Failed to synchronize environment rules: {e}")
    st.stop()

col1, col2 = st.columns(2)
with col1:
    form_names = [f["name"] for f in available_forms]
    selected_form_name = st.selectbox("📝 Target Form Schema", form_names)
    selected_dataset_id = available_forms[form_names.index(selected_form_name)]["id"]

with col2:
    clinic_names = [c["name"] for c in available_clinics]
    selected_clinic_name = st.selectbox("🏥 PHI Area / Clinic", clinic_names)
    selected_ou_id = available_clinics[clinic_names.index(selected_clinic_name)]["id"]

# --- DYNAMIC FORM CONFIGURATION ROUTER ---
form_key = "default"
for key in FORM_CONFIGS.keys():
    if key != "default" and key in selected_form_name.lower():
        form_key = key
        break

current_config = FORM_CONFIGS[form_key]
is_annual_form = (current_config["timeframe"] == "annual")

col3, col4 = st.columns(2)
with col3:
    year = st.selectbox("📅 Year", ["2025", "2026", "2027", "2028"])
with col4:
    if is_annual_form:
        override_monthly = st.checkbox("Force Monthly Format (Check if ERHMIS rejects upload)")
        if override_monthly:
            month = st.selectbox("📅 Month", [str(i).zfill(2) for i in range(1, 13)], format_func=lambda x: ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][int(x)-1])
            period = f"{year}{month}"
        else:
            st.info("📅 Annual Report")
            month = None
            period = year
    else:
        month = st.selectbox("📅 Month", [str(i).zfill(2) for i in range(1, 13)], format_func=lambda x: ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][int(x)-1])
        period = f"{year}{month}"

# ==========================================
# 🤖 FAULT-TOLERANT AI PIPELINE
# ==========================================
st.write("---")
st.subheader("📸 Form Image Ingestion")
uploaded_files = st.file_uploader(
    "Capture or upload report sheets", 
    type=["jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

def clean_ai_csv(raw_text):
    cleaned = raw_text.replace("```csv", "").replace("```", "").strip()
    if "DataElement_ID" not in cleaned:
        cleaned = "DataElement_ID,Category_ID,Field_Description,Value\n" + cleaned
    return cleaned

if uploaded_files:
    st.success(f"✅ Successfully loaded {len(uploaded_files)} photos into memory!")
    
    with st.expander("👀 Tap here to preview your photos"):
        cols = st.columns(3) 
        for i, img_file in enumerate(uploaded_files):
            cols[i % 3].image(img_file, caption=f"Page {i+1}", use_container_width=True)

    if st.button("✨ Extract Data via Parallel Engine", type="primary", use_container_width=True):
        
        api_key_1 = get_api_key("GEMINI_API_KEY_1")
        api_key_2 = get_api_key("GEMINI_API_KEY_2")
        
        if not api_key_1 or not api_key_2:
            st.error("❌ CRITICAL ERROR: Streamlit API Keys are missing. Please check your st.secrets configuration.")
            st.stop()

        with st.status("🤖 Initiating AI Pipeline...", expanded=True) as status:
            try:
                # --- STEP 1: SCHEMA FETCH ---
                st.write("⏳ Step 1: Downloading dynamic form blueprint...")
                mat_res = requests.get(f"{BASE_URL}/dataSets/{selected_dataset_id}.json?fields=dataSetElements[dataElement[id,name,formName,categoryCombo[categoryOptionCombos[id,name]]]]", auth=st.session_state.auth, timeout=20)
                
                schema_buffer = io.StringIO()
                writer = csv.writer(schema_buffer, quoting=csv.QUOTE_MINIMAL)
                writer.writerow(["DataElement_ID", "Category_ID", "Field_Description", "Value"])
                
                for dse in mat_res.json().get("dataSetElements", []):
                    de = dse.get("dataElement", {})
                    de_id = de.get("id")
                    de_name = de.get("formName") or de.get("name")
                    for coc in de.get("categoryCombo", {}).get("categoryOptionCombos", []):
                        display_name = de_name if coc.get("name") == "default" else f"{de_name} ---> [{coc.get('name')}]"
                        writer.writerow([de_id, coc.get("id"), display_name, ""])
                
                schema_blueprint = schema_buffer.getvalue()
                st.write("✅ Step 1 Complete.")

                # --- STEP 2: HIGH-DEFINITION COMPRESSION ---
                st.write("⏳ Step 2: Preparing HD images for parallel processing...")
                image_parts = []
                for f in uploaded_files:
                    img = Image.open(f)
                    if img.mode != 'RGB': img = img.convert('RGB')
                    
                    # 1280px FOR CRYSTAL CLEAR HANDWRITING RECOGNITION
                    img.thumbnail((1280, 1280)) 
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG', quality=50) 
                    
                    image_parts.append({
                        "mime_type": "image/jpeg",
                        "data": img_byte_arr.getvalue()
                    })
                    del img
                    gc.collect()
                st.write("✅ Step 2 Complete.")
                
                # --- STEP 3: PARALLEL AI EXTRACTION ENGINE ---
                st.write(f"⏳ Step 3: AI is processing {len(image_parts)} pages simultaneously...")
                
                if is_annual_form:
                    target_timeframe_text = f"the entire year of {year}"
                    month_context = {}
                else:
                    month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                    month_letters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
                    month_idx = int(month) - 1
                    
                    target_timeframe_text = f"{month_names[month_idx]} {year}"
                    month_context = {
                        "target_month_name": month_names[month_idx],
                        "target_month_letter": month_letters[month_idx],
                        "target_month_num": str(int(month))
                    }

                ai_prompt = current_config["prompt_template"].format(
                    target_timeframe_text=target_timeframe_text,
                    target_month_name=month_context.get("target_month_name", ""),
                    target_month_letter=month_context.get("target_month_letter", ""),
                    target_month_num=month_context.get("target_month_num", ""),
                    schema_blueprint=schema_blueprint
                )

                def process_single_page(page_data, primary_key, backup_key, prompt):
                    # FIX: Corrected list structure [page_data, prompt]
                    contents = [page_data, prompt]
                    try:
                        genai.configure(api_key=primary_key, transport="rest")
                        model = genai.GenerativeModel('gemini-3.5-flash')
                        response = model.generate_content(
                            contents, 
                            generation_config={"temperature": 0.0, "max_output_tokens": 4096},
                            request_options={"timeout": 60}
                        )
                        return response.text.strip()
                    except Exception as e:
                        genai.configure(api_key=backup_key, transport="rest")
                        model = genai.GenerativeModel('gemini-3.5-flash')
                        response = model.generate_content(
                            contents, 
                            generation_config={"temperature": 0.0, "max_output_tokens": 4096},
                            request_options={"timeout": 60}
                        )
                        return response.text.strip()

                dfs = []
                # Execute all pages concurrently in a multi-threaded pool
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(image_parts)) as executor:
                    futures = [
                        executor.submit(process_single_page, page, api_key_1, api_key_2, ai_prompt) 
                        for page in image_parts
                    ]
                    
                    for future in concurrent.futures.as_completed(futures):
                        csv_result = future.result()
                        if csv_result:
                            try:
                                dfs.append(pd.read_csv(io.StringIO(clean_ai_csv(csv_result)), on_bad_lines='skip'))
                            except Exception:
                                pass

                if dfs:
                    df_final = pd.concat(dfs, ignore_index=True)
                    
                    if 'Value' not in df_final.columns:
                        df_final['Value'] = np.nan
                    
                    df_final['Value'] = df_final['Value'].replace(r'^\s*$', np.nan, regex=True)
                    df_final = df_final.dropna(subset=['Value'])
                    
                    if 'DataElement_ID' in df_final.columns and 'Category_ID' in df_final.columns:
                        df_final = df_final.drop_duplicates(subset=['DataElement_ID', 'Category_ID'], keep='first')
                    
                    raw_csv_output = df_final.to_csv(index=False)
                else:
                    raise Exception("AI failed to extract any valid formatted data.")
                
                st.session_state.extracted_csv = raw_csv_output
                st.write("✅ Step 3 Complete.")
                status.update(label="✅ AI Extraction Complete!", state="complete", expanded=False)
                
            except Exception as e:
                status.update(label="❌ Pipeline Error!", state="error", expanded=True)
                st.error(f"Error Details: {e}")

# ==========================================
# 📤 FINAL TRANSMISSION LAYER
# ==========================================
if "extracted_csv" in st.session_state:
    st.write("---")
    st.subheader("📋 Pre-Transmission Check")
    
    compiled_values = []
    lines = st.session_state.extracted_csv.strip().split('\n')
    
    for line in lines:
        parts = line.split(',')
        if len(parts) >= 4 and parts[0] != "DataElement_ID":
            de_id, cat_id, val = parts[0].strip(), parts[1].strip(), parts[-1].strip()
            if len(de_id) >= 10 and len(cat_id) >= 10 and val and val != "Value":
                try:
                    clean_val = str(int(float(val)))
                    compiled_values.append({
                        "dataElement": de_id,
                        "categoryOptionCombo": cat_id,
                        "value": clean_val
                    })
                except ValueError:
                    pass

    st.metric(label="Validated Populated Parameters", value=len(compiled_values))
    
    if st.button("🚀 Push Mapped Records to Live ERHMIS"):
        if len(compiled_values) == 0:
            st.warning("⚠️ No populated data found. Upload cancelled to prevent wiping ERHMIS fields.")
        else:
            payload = {
                "dataSet": selected_dataset_id,
                "period": period,
                "orgUnit": selected_ou_id,
                "dataValues": compiled_values
            }
            with st.status("📡 Step 4: Transmitting payload to ERHMIS Server...", expanded=True) as upload_status:
                try:
                    post_res = requests.post(
                        f"{BASE_URL}/dataValueSets", 
                        auth=st.session_state.auth, 
                        json=payload, 
                        timeout=45
                    )
                    st.write("✅ Step 4 Complete.")
                    upload_status.update(label="✅ Transmission Checked", state="complete", expanded=False)
                    st.subheader("🎉 Server Transaction Summary:")
                    
                    res_json = post_res.json()
                    st.json(res_json)
                    
                    if post_res.status_code in [200, 201]:
                        imported = 0
                        ignored = 0
                        if "response" in res_json and "importCount" in res_json["response"]:
                            imported = res_json["response"]["importCount"].get("imported", 0)
                            ignored = res_json["response"]["importCount"].get("ignored", 0)
                        elif "importCount" in res_json:
                            imported = res_json["importCount"].get("imported", 0)
                            ignored = res_json["importCount"].get("ignored", 0)

                        if ignored > 0 and imported == 0:
                            st.error(f"❌ ERHMIS REJECTED THE DATA! (Ignored: {ignored})")
                            st.warning("⚠️ ERHMIS refused to save the data. Try checking the 'Force Monthly Format' box above!")
                        elif ignored > 0:
                            st.warning(f"⚠️ Partial Upload! Imported: {imported}, Ignored: {ignored}")
                            st.success(f"✨ Data is live for {selected_clinic_name}, but some records were rejected.")
                            del st.session_state.extracted_csv
                        else:
                            st.success(f"✨ Perfect Upload! {imported} records are now live for {selected_clinic_name}.")
                            del st.session_state.extracted_csv
                            
                except requests.exceptions.Timeout:
                    st.error("❌ Transmission timed out. The server acknowledged the payload but took too long.")
                except Exception as e:
                    st.error(f"❌ Critical failure: {e}")
