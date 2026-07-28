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
# 🔒 HUGGING FACE SECURITY LAYER
# ==========================================
def check_authorization(username):
    try:
        auth_users_str = os.environ.get("AUTHORIZED_USERS", "91-krw-sphi,admin")
        authorized_list = [u.strip() for u in auth_users_str.split(",")]
    except Exception:
        authorized_list = ["91-krw-sphi", "admin"]
    return username.strip().lower() in [user.lower() for user in authorized_list]

if not st.session_state.authenticated:
    st.title("🔐 eRHMIS Smart Upload")
    st.subheader("Authorized Personnel Only")
    
    with st.form("login_form"):
        username = st.text_input("ERHMIS Username", autocomplete="username")
        password = st.text_input("ERHMIS Password", type="password", autocomplete="current-password")
        app_passcode = st.text_input("App Access Token", type="password", help="Contact admin for your token.")
        
        submit_login = st.form_submit_button("Secure Log In")
        
        if submit_login:
            master_token = os.environ.get("MASTER_TOKEN", "fallback_token")
            
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

is_annual_form = "1247" in selected_form_name.lower()

col3, col4 = st.columns(2)
with col3:
    year = st.selectbox("📅 Year", ["2025", "2026", "2027"])
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

# HELPER FUNCTION: Cleans messy AI outputs before Pandas reads them
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
        
        with st.status("🤖 Initiating AI Pipeline...", expanded=True) as status:
            try:
                # --- STEP 1: SCHEMA FETCH ---
                st.write("⏳ Step 1: Downloading dynamic form blueprint...")
                mat_res = requests.get(f"{BASE_URL}/dataSets/{selected_dataset_id}.json?fields=dataSetElements[dataElement[id,name,formName,categoryCombo[categoryOptionCombos[id,name]]]]", auth=st.session_state.auth, timeout=20)
                
                # Use QUOTE_MINIMAL to protect against commas inside the Field_Description
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
                
                # --- STEP 3: DUAL-CORE EXTRACTION ---
                st.write("⏳ Step 3: AI is reading handwriting...")
                
                if is_annual_form:
                    target_timeframe_text = f"the entire year of {year}"
                else:
                    month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                    month_idx = int(month) - 1
                    target_month_name = month_names[month_idx]
                    target_timeframe_text = f"{target_month_name} {year}"

                if "631" in selected_form_name.lower():
                    month_letters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
                    target_month_letter = month_letters[month_idx]
                    target_month_num = str(int(month))
                    form_layout_instructions = f"""
                    HOW TO FIND THE COLUMN:
                    Months are labeled with a SINGLE LETTER at the top of the columns.
                    For {target_month_name}, look for the column labeled '{target_month_letter}' (Column #{target_month_num} from left to right).
                    """
                elif is_annual_form:
                    form_layout_instructions = f"""
                    HOW TO FIND THE DATA (GRID FORMAT):
                    This is a multi-page ANNUAL summary form (H1247). 
                    Many sections (like Section 6) are formatted as a GRID. 
                    The rows represent conditions (e.g., "Stunting", "Wasting") and the columns represent Grade and Gender (e.g., Grade 1 M, Grade 4 F).
                    In the schema below, these grid intersections are represented as 'Condition ---> [Grade - Gender]'. 
                    You MUST carefully match the written number in the grid to the exact combination in the schema.
                    """
                else:
                    form_layout_instructions = f"Extract numbers matching the schema descriptions."

                ai_prompt = f"""
                You are an expert data entry assistant for the Sri Lankan Ministry of Health.
                CRITICAL INSTRUCTION: You MUST ONLY extract the data for {target_timeframe_text}.
                
                {form_layout_instructions}

                STRICT RULES:
                1. Output STRICTLY as raw CSV text. No markdown blocks (do not use ```csv).
                2. The output MUST contain exactly 4 columns separated by commas. The first row MUST be exactly: DataElement_ID,Category_ID,Field_Description,Value
                3. OMIT BLANKS: ONLY output rows where you found a visible number on the assigned pages. If a field is blank, DO NOT include that row in your output.
                4. NIL RULE: If a large "NIL" or line is drawn across a whole page or section, DO NOT output any data for that section.

                SCHEMA BLUEPRINT (Use this to match IDs):
                {schema_blueprint}
                """

                stack_odd = image_parts[0::2]
                stack_even = image_parts[1::2]

                def process_stack(stack, api_key, prompt):
                    if not stack: return None
                    genai.configure(api_key=api_key, transport="rest")
                    model = genai.GenerativeModel('gemini-3.5-flash')
                    contents = stack + [prompt]
                    response = model.generate_content(
                        contents, 
                        generation_config={"temperature": 0.0, "max_output_tokens": 8192},
                        request_options={"timeout": 240}
                    )
                    return response.text.strip()

                api_key_1 = os.environ.get("GEMINI_API_KEY_1")
                api_key_2 = os.environ.get("GEMINI_API_KEY_2")

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future_odd = executor.submit(process_stack, stack_odd, api_key_1, ai_prompt)
                    future_even = executor.submit(process_stack, stack_even, api_key_2, ai_prompt)
                    csv_odd = future_odd.result()
                    csv_even = future_even.result()

                # --- NEW FAULT-TOLERANT MERGE ENGINE ---
                dfs = []
                if csv_odd:
                    try:
                        dfs.append(pd.read_csv(io.StringIO(clean_ai_csv(csv_odd)), on_bad_lines='skip'))
                    except Exception as e:
                        st.warning(f"Engine A Warning: Could not parse some rows.")
                if csv_even:
                    try:
                        dfs.append(pd.read_csv(io.StringIO(clean_ai_csv(csv_even)), on_bad_lines='skip'))
                    except Exception as e:
                        st.warning(f"Engine B Warning: Could not parse some rows.")

                if dfs:
                    df_final = pd.concat(dfs, ignore_index=True)
                    
                    # Ensure columns exist even if AI forgot them
                    if 'Value' not in df_final.columns:
                        df_final['Value'] = np.nan
                    
                    df_final['Value'] = df_final['Value'].replace(r'^\s*$', np.nan, regex=True)
                    df_final = df_final.dropna(subset=['Value']) # Drop rows that AI accidentally included as blank
                    
                    # Deduplicate in case both engines found the same row
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
