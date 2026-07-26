import streamlit as st
import requests
import json
import csv
import io
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
# 🔒 ACCESS LIMITATION & SECURITY LAYER
# ==========================================
def check_authorization(username):
    try:
        authorized_list = st.secrets["security"]["AUTHORIZED_USERS"]
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
            if not check_authorization(username) and app_passcode != st.secrets["security"]["MASTER_TOKEN"]:
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
# 🎛️ MAIN APPLICATION INTERFACE (AUTHENTICATED)
# ==========================================
st.title("🚀 eRHMIS Smart Upload")
st.caption(f"Logged in as: {st.session_state.username} | System Admin: {ADMIN_EMAIL}")

if st.sidebar.button("Sign Out"):
    st.session_state.authenticated = False
    st.rerun()

# --- STEP 1: DYNAMIC ENVIRONMENT DISCOVERY ---
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

# --- STEP 2: USER META-DATA SELECTION ---
col1, col2 = st.columns(2)
with col1:
    form_names = [f["name"] for f in available_forms]
    selected_form_name = st.selectbox("📝 Target Form Schema", form_names)
    selected_dataset_id = available_forms[form_names.index(selected_form_name)]["id"]

with col2:
    clinic_names = [c["name"] for c in available_clinics]
    selected_clinic_name = st.selectbox("🏥 PHI Area / Clinic", clinic_names)
    selected_ou_id = available_clinics[clinic_names.index(selected_clinic_name)]["id"]

# 🛑 FIXED: Dynamic Date Selection Based on Form Type
is_annual_form = "1247" in selected_form_name.lower()

col3, col4 = st.columns(2)
with col3:
    year = st.selectbox("📅 Year", ["2025", "2026", "2027"])
with col4:
    if is_annual_form:
        st.info("📅 Annual Report")
        month = None
        period = year  # ERHMIS format for Yearly is just "YYYY"
    else:
        month = st.selectbox("📅 Month", [str(i).zfill(2) for i in range(1, 13)], format_func=lambda x: ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][int(x)-1])
        period = f"{year}{month}"  # ERHMIS format for Monthly is "YYYYMM"

# ==========================================
# 🤖 AI PROCESSING PIPELINE
# ==========================================
st.write("---")
st.subheader("📸 Form Image Ingestion")
uploaded_files = st.file_uploader(
    "Capture or upload report sheets", 
    type=["jpg", "jpeg", "png"], 
    accept_multiple_files=True,
    help="Odd/Even Server routing is automatically enabled."
)

if uploaded_files:
    st.success(f"✅ Successfully loaded {len(uploaded_files)} photos into memory!")
    
    with st.expander("👀 Tap here to preview your photos (Check for clarity)"):
        cols = st.columns(3) 
        for i, img_file in enumerate(uploaded_files):
            cols[i % 3].image(img_file, caption=f"Page {i+1}", use_column_width=True)

    if st.button("✨ Extract Data via Dual-Core AI", type="primary", use_container_width=True):
        
        with st.status("🤖 Initiating AI Pipeline...", expanded=True) as status:
            try:
                # --- STEP 1: ERHMIS SCHEMA FETCH ---
                st.write("⏳ Step 1: Downloading dynamic form blueprint from ERHMIS...")
                mat_res = requests.get(f"{BASE_URL}/dataSets/{selected_dataset_id}.json?fields=dataSetElements[dataElement[id,name,formName,categoryCombo[categoryOptionCombos[id,name]]]]", auth=st.session_state.auth, timeout=20)
                
                schema_buffer = io.StringIO()
                writer = csv.writer(schema_buffer)
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

                # --- STEP 2: AGGRESSIVE COMPRESSION ---
                st.write("⏳ Step 2: Compressing photos for fast transmission...")
                image_parts = []
                for f in uploaded_files:
                    img = Image.open(f)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                        
                    img.thumbnail((800, 800)) 
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG', quality=50) 
                    
                    image_parts.append({
                        "mime_type": "image/jpeg",
                        "data": img_byte_arr.getvalue()
                    })
                st.write("✅ Step 2 Complete.")
                
                # --- STEP 3: DUAL-CORE AI EXTRACTION ---
                st.write("⏳ Step 3: AI is reading handwriting using Odd/Even Dual Engines...")
                
                # Dynamic Prompt Setup based on Annual vs Monthly
                if is_annual_form:
                    target_timeframe_text = f"the entire year of {year}"
                else:
                    month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                    month_idx = int(month) - 1
                    target_month_name = month_names[month_idx]
                    target_timeframe_text = f"{target_month_name} {year}"

                # Logic Branching based on Form Name
                if "631" in selected_form_name.lower():
                    month_letters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
                    target_month_letter = month_letters[month_idx]
                    target_month_num = str(int(month))
                    
                    form_layout_instructions = f"""
                    HOW TO FIND THE COLUMN:
                    Months are labeled with a SINGLE LETTER at the top of the columns (J, F, M, A, M, J, J, A, S, O, N, D).
                    For {target_month_name}, look for the column labeled '{target_month_letter}'.
                    Because some letters repeat, ensure accuracy: {target_month_name} is data column number {target_month_num} from left to right.
                    """
                elif is_annual_form:
                    form_layout_instructions = f"""
                    HOW TO FIND THE DATA:
                    This is a standard multi-page ANNUAL summary form for the YEAR {year}.
                    It DOES NOT use a monthly grid. Scan the uploaded pages for fields that match the 'Field_Description' labels in the schema below.
                    Extract the number written directly next to, below, or inside the box for that specific label.
                    """
                else:
                    form_layout_instructions = f"""
                    HOW TO FIND THE DATA:
                    This is a standard multi-page summary form for {target_timeframe_text}.
                    Scan the uploaded pages for fields that match the 'Field_Description' labels in the schema below.
                    Extract the number written directly next to, below, or inside the box for that specific label.
                    """

                ai_prompt = f"""
                Your task is to act as an expert data entry assistant for the Sri Lankan Ministry of Health.
                
                CRITICAL INSTRUCTION: 
                You MUST ONLY extract the data for {target_timeframe_text}. Ignore obsolete data.
                
                {form_layout_instructions}

                STRICT RULES:
                1. Output STRICTLY as raw CSV text. No markdown blocks.
                2. Keep DataElement_ID and Category_ID exactly as they appear.
                3. Final output must have 4 columns: DataElement_ID, Category_ID, Field_Description, Value.
                4. Do not omit any rows. Every row from the blueprint must be output.
                5. If a field is explicitly blank, unreadable, or not found on the provided pages, leave the Value column completely blank. Do not write '0' unless written on the form.

                SCHEMA MATRIX BLUEPRINT:
                {schema_blueprint}
                """

                # Interleave Splitting
                stack_odd = image_parts[0::2]
                stack_even = image_parts[1::2]

                def process_stack(stack, api_key, prompt):
                    if not stack: 
                        return None
                    genai.configure(api_key=api_key, transport="rest")
                    model = genai.GenerativeModel('gemini-3.5-flash')
                    
                    generation_config = {
                        "temperature": 0.0,
                        "max_output_tokens": 8192
                    }
                    
                    contents = stack + [prompt]
                    response = model.generate_content(
                        contents, 
                        generation_config=generation_config,
                        request_options={"timeout": 120}
                    )
                    return response.text.strip()

                csv_odd = None
                csv_even = None
                
                # Fire both APIs simultaneously
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future_odd = executor.submit(process_stack, stack_odd, st.secrets["ai"]["GEMINI_API_KEY_1"], ai_prompt)
                    future_even = executor.submit(process_stack, stack_even, st.secrets["ai"]["GEMINI_API_KEY_2"], ai_prompt)
                    csv_odd = future_odd.result()
                    csv_even = future_even.result()

                # Merge Results intelligently
                if csv_odd and csv_even:
                    df_odd = pd.read_csv(io.StringIO(csv_odd))
                    df_even = pd.read_csv(io.StringIO(csv_even))
                    
                    df_odd['Value'] = df_odd['Value'].replace(r'^\s*$', np.nan, regex=True)
                    df_even['Value'] = df_even['Value'].replace(r'^\s*$', np.nan, regex=True)
                    
                    df_final = df_odd.copy()
                    df_final['Value'] = df_odd['Value'].combine_first(df_even['Value'])
                    df_final['Value'] = df_final['Value'].fillna('')
                    raw_csv_output = df_final.to_csv(index=False)
                    
                elif csv_odd:
                    raw_csv_output = csv_odd
                elif csv_even:
                    raw_csv_output = csv_even
                else:
                    raise Exception("No data could be extracted.")
                
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
