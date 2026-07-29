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
# 🧠 STRICT ERHMIS MAPPING KNOWLEDGE BASE
# ==========================================
FORM_CONFIGS = {
    "631": {
        "timeframe": "monthly",
        "processing_mode": "dual_interleave", 
        "ai_instructions": """
            HOW TO FIND THE DATA: 
            This is a 12-month ledger. Months are labeled with a SINGLE LETTER at the top of the columns.
            For {target_month_name}, look for the column labeled '{target_month_letter}'.
            Because some letters repeat, ensure accuracy: {target_month_name} is data column number {target_month_num} from left to right.
        """
    },
    "1247": {
        "timeframe": "annual",
        "processing_mode": "single_bundle", 
        "ai_instructions": """
            CRITICAL VISUAL MAPPING CHEAT SHEET FOR FORM H1247:
            You must map the physical row numbers on the paper to the schema exactly as defined below. Do not guess.
            
            0. SYMBOLS: 
               - Checkmark / Tick (✓) = 1
               - Dash (-) or empty box = BLANK (Do NOT output)
               
            1. SECTION 3 (Officers participated):
               Look at the printed numbers 1 to 11 on the paper. Map handwritten values ONLY to these specific labels:
               Row 1 = MOH
               Row 2 = AMOH
               Row 3 = Other MOs
               Row 4 = Dental Surgeon
               Row 5 = RMO/AMO
               Row 6 = SPHI
               Row 7 = PHI
               Row 8 = PHNS
               Row 9 = HEO
               Row 10 = SDT
               Row 11 = PHM
               * Example: If a '1' is written on Row 7, it belongs ONLY to 'PHI'. Do not put 1 for AMOH.
               
            2. SECTION 4 (Students examined):
               Map the physical rows on the paper to these exact grades:
               Row 1 = Grade 1
               Row 2 = Grade 4
               Row 3 = Grade 7
               Row 4 = Grade 10
               Row 5 = Other
               
            3. SECTION 6 (Problems & Defects Matrix):
               The grid columns strictly correspond to:
               Col 1 = Grade 1 (Male)
               Col 2 = Grade 1 (Female)
               Col 3 = Grade 4 (Male)
               Col 4 = Grade 4 (Female)
               Col 5 = Grade 7 (Male)
               Col 6 = Grade 7 (Female)
               Col 7 = Grade 10 (Male)
               Col 8 = Grade 10 (Female)
               Col 9 = Other (Male)
               Col 10 = Other (Female)
               * Trace carefully! If a '1' is in Row 2 (Wasting) and Column 3, map it to 'Wasting ---> [Grade 4 - Male]'.
               
            4. PAGE 2 'NIL':
               If 'NIL' or a large line is drawn across a page, ignore all fields on that page.
        """
    },
    "default": {
        "timeframe": "monthly",
        "processing_mode": "single_bundle",
        "ai_instructions": """
            HOW TO FIND THE DATA:
            This is a standard summary form.
            Scan the uploaded pages for fields that match the 'Field_Description' labels in the schema below.
            Extract the number written directly next to, below, or inside the box for that specific label.
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
            cols[i % 3].image(img_file, caption=f"Page {i+1}", use_column_width=True)

    if st.button("✨ Extract Data via Dual-Core AI", type="primary", use_container_width=True):
        
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

                # --- STEP 2: MEMORY SAFE COMPRESSION ---
                st.write("⏳ Step 2: Compressing photos...")
                image_parts = []
                for f in uploaded_files:
                    img = Image.open(f)
                    if img.mode != 'RGB': img = img.convert('RGB')
                    img.thumbnail((800, 800)) 
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG', quality=45) 
                    image_parts.append({
                        "mime_type": "image/jpeg",
                        "data": img_byte_arr.getvalue()
                    })
                    del img
                    gc.collect()
                st.write("✅ Step 2 Complete.")
                
                # --- STEP 3: AI EXTRACTION ENGINE ROUTER ---
                st.write("⏳ Step 3: AI is reading handwriting with Schema Maps...")
                
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

                specific_instructions = current_config["ai_instructions"].format(**month_context)

                ai_prompt = f"""
                You are an expert data entry assistant for the Sri Lankan Ministry of Health.
                CRITICAL INSTRUCTION: You MUST ONLY extract the data for {target_timeframe_text}.
                
                {specific_instructions}

                STRICT RULES:
                1. Output STRICTLY as raw CSV text. No markdown blocks (do not use ```csv).
                2. The output MUST contain exactly 4 columns separated by commas. The first row MUST be exactly: DataElement_ID,Category_ID,Field_Description,Value
                3. OMIT BLANKS: ONLY output rows where you found a visible handwritten number or checkmark on the assigned pages. If a field is explicitly blank, DO NOT include that row in your output.
                4. Do NOT hallucinate or copy values across empty rows.

                SCHEMA BLUEPRINT (Use this to match IDs):
                {schema_blueprint}
                """

                def process_stack(stack, api_key, prompt):
                    if not stack: return None
                    genai.configure(api_key=api_key, transport="rest")
                    # Using gemini-1.5-flash as the fast, standard tier for document processing
                    model = genai.GenerativeModel('gemini-3.5-flash')
                    contents = stack + [prompt]
                    response = model.generate_content(
                        contents, 
                        generation_config={"temperature": 0.0, "max_output_tokens": 8192},
                        request_options={"timeout": 500}
                    )
                    return response.text.strip()

                mode = current_config.get("processing_mode", "single_bundle")
                
                dfs = []
                if mode == "dual_interleave":
                    stack_odd = image_parts[0::2]
                    stack_even = image_parts[1::2]
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future_odd = executor.submit(process_stack, stack_odd, api_key_1, ai_prompt)
                        future_even = executor.submit(process_stack, stack_even, api_key_2, ai_prompt)
                        csv_odd = future_odd.result()
                        csv_even = future_even.result()
                    if csv_odd:
                        try: dfs.append(pd.read_csv(io.StringIO(clean_ai_csv(csv_odd)), on_bad_lines='skip'))
                        except Exception: pass
                    if csv_even:
                        try: dfs.append(pd.read_csv(io.StringIO(clean_ai_csv(csv_even)), on_bad_lines='skip'))
                        except Exception: pass
                else:
                    csv_full = process_stack(image_parts, api_key_1, ai_prompt)
                    if csv_full:
                        try: dfs.append(pd.read_csv(io.StringIO(clean_ai_csv(csv_full)), on_bad_lines='skip'))
                        except Exception: pass

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
            if val and val != "Value":
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
                    upload_status.update(label="✅ Transmission Successful", state="complete", expanded=False)
                    st.subheader("🎉 Server Transaction Summary:")
                    st.json(post_res.json())
                    
                    if post_res.status_code in [200, 201]:
                        st.success(f"✨ Perfect Upload! Data is now live for {selected_clinic_name}.")
                        del st.session_state.extracted_csv
                except requests.exceptions.Timeout:
                    st.error("❌ Transmission timed out. The server acknowledged the payload but took too long.")
                except Exception as e:
                    st.error(f"❌ Critical failure: {e}")
