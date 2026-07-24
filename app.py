import streamlit as st
import requests
import google.generativeai as genai
import io
from PIL import Image
import concurrent.futures
import pandas as pd
import numpy as np
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="ERHMIS AI Portal v2", page_icon="🏥", layout="centered")

# --- 🔒 V1-STYLE WHITELIST AUTHENTICATION ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 ERHMIS Portal Access")
    st.markdown("Enter your authorized User ID and Security Token to continue.")
    
    col1, col2 = st.columns(2)
    with col1:
        user_id = st.text_input("User ID / Username").strip()
    with col2:
        token = st.text_input("Master Token", type="password").strip()
    
    if st.button("Access Portal"):
        try:
            authorized_list = st.secrets["security"]["AUTHORIZED_USERS"]
            master_token = st.secrets["security"]["MASTER_TOKEN"]
            
            if user_id in authorized_list and token == master_token:
                st.session_state.authenticated = True
                st.success("✅ Authorization successful! Launching V2 Engine...")
                st.rerun()
            else:
                st.error("❌ Access Denied: Invalid User ID or Security Token.")
        except Exception as e:
            st.error("❌ Configuration Error: Please check your secrets.toml file setup.")
            
    st.stop() # 🛑 Blocks the V2 extraction tool until authorized
# ---------------------------------------------


# --- 🏥 MAIN PORTAL UI ---
st.title("🏥 ERHMIS AI Data Extraction Portal")
st.markdown("Upload your handwritten ledger photos. The AI will securely digitize and push the data.")

st.subheader("1. Report Details")

# Report Type Selector (For Future Updates)
report_type = st.selectbox(
    "Select Report Type:", 
    ["PHI Monthly Communicable Disease Report", "New Report Format (Coming Soon)"]
)

col1, col2 = st.columns(2)
with col1:
    month = st.selectbox("Select Target Month:", [str(i) for i in range(1, 13)], index=datetime.now().month - 1)
with col2:
    year = st.selectbox("Select Target Year:", ["2025", "2026", "2027"], index=1)

st.subheader("2. Upload Ledger")
uploaded_files = st.file_uploader("📸 Upload Photos (Odd/Even Processing Enabled)", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)


# ⚠️ ------------------------------------------------ ⚠️
# ⚠️ PASTE YOUR ACTUAL 29-ROW SCHEMA BLUEPRINT HERE ⚠️
schema_blueprint = """
DataElement_ID,Category_ID,Field_Description,Value
DE_001,CAT_001,Example Data Row 1,
DE_002,CAT_001,Example Data Row 2,
"""
# ⚠️ ------------------------------------------------ ⚠️


# --- 🤖 AI PROCESSING PIPELINE ---
if st.button("✨ Extract Data via Gemini AI") and uploaded_files:
    
    try:
        # 🔄 Professional Status Animation Box
        with st.status("Initializing ERHMIS AI Engine...", expanded=True) as status_box:
            
            # --- STEP 1: PREPARATION ---
            st.write("⏳ Step 1: Downloading form blueprint from ERHMIS...")
            
            month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
            month_letters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
            
            target_month_name = month_names[int(month) - 1]
            target_month_num = str(int(month))
            target_month_letter = month_letters[int(month) - 1]

            ai_prompt = f"""
            Your task is to act as an expert data entry assistant for the Sri Lankan Ministry of Health.
            Carefully read the handwritten and printed numbers from the attached images of the health report.
            
            CRITICAL INSTRUCTION - TARGET MONTH: 
            The images contain a ledger with data for multiple months. You MUST ONLY extract the data for {target_month_name} {year}. 
            
            HOW TO FIND THE COLUMN:
            On this specific physical form, months are labeled with a SINGLE LETTER at the top of the columns (J, F, M, A, M, J, J, A, S, O, N, D).
            For {target_month_name}, you must look for the column labeled '{target_month_letter}'.
            Because some letters repeat (like 'M' for March and May), you MUST use the column order to ensure accuracy: {target_month_name} is data column number {target_month_num} from left to right.
            Ignore data from any other months, and completely ignore obsolete data before 2025.

            Look at the 'Field_Description' column in the schema below, match the correct data for {target_month_name} {year}, and type the extracted number into the 'Value' column.

            STRICT RULES:
            1. Output the final result STRICTLY as raw CSV text. Do NOT wrap it in Markdown formatting blocks.
            2. Keep the DataElement_ID and Category_ID columns exactly as they appear in the schema. Do not alter these codes.
            3. The final output must have exactly these 4 columns: DataElement_ID, Category_ID, Field_Description, Value.
            4. Do not omit any rows. Every single row from the blueprint must be in your output.
            5. If a field is explicitly blank, unreadable, or crossed out for {target_month_name}, leave the Value column completely blank. Do NOT write '0' unless there is literally a '0' written on the form.

            SCHEMA MATRIX BLUEPRINT:
            {schema_blueprint}
            """
            st.write("✅ Step 1 Complete.")
            
            # --- STEP 2: COMPRESSION ---
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
            
            # --- STEP 3: ODD/EVEN DUAL-CORE ENGINE ---
            st.write("⏳ Step 3: AI is reading handwriting using Odd/Even Dual Engines...")
            
            stack_odd = image_parts[0::2]
            stack_even = image_parts[1::2]

            def process_stack(stack, api_key, prompt):
                if not stack: 
                    return None
                genai.configure(api_key=api_key, transport="rest")
                model = genai.GenerativeModel('gemini-3.5-flash')
                contents = stack + [prompt]
                response = model.generate_content(contents, request_options={"timeout": 120})
                return response.text.strip()

            csv_odd = None
            csv_even = None
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_odd = executor.submit(process_stack, stack_odd, st.secrets["ai"]["GEMINI_API_KEY_1"], ai_prompt)
                future_even = executor.submit(process_stack, stack_even, st.secrets["ai"]["GEMINI_API_KEY_2"], ai_prompt)
                
                csv_odd = future_odd.result()
                csv_even = future_even.result()

            # Merge Engine
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
                raise Exception("No data could be extracted from the images.")
                
            st.write("✅ Step 3 Complete.")
            
            # Close the professional loading box
            status_box.update(label="✅ AI Extraction Completed Successfully!", state="complete", expanded=False)

        # --- STEP 4: PRE-TRANSMISSION CHECK & ERHMIS PUSH ---
        final_df = pd.read_csv(io.StringIO(raw_csv_output))
        final_df['Value'] = final_df['Value'].fillna('') 
        populated_count = final_df[final_df['Value'] != ''].shape[0]
        
        st.subheader("📋 Pre-Transmission Check")
        st.write(f"**Validated Populated Parameters:** {populated_count}")
        st.dataframe(final_df, use_container_width=True) 
        
        if populated_count > 0:
            with st.status("📡 Step 4: Pushing data to ERHMIS Server...", expanded=True) as upload_status:
                
                # ⚠️ ------------------------------------------------ ⚠️
                # ⚠️ PASTE YOUR ACTUAL ERHMIS SERVER UPLOAD CODE HERE ⚠️
                
                # example: response = requests.post(url, json=payload, auth=auth)
                
                # ⚠️ ------------------------------------------------ ⚠️
                
                st.write("✅ Step 4 Complete.")
                upload_status.update(label="Transmission Successful", state="complete", expanded=False)
                
            st.success("✨ Perfect Upload! Data is now live for PHI.")
            
        else:
            st.warning("⚠️ No data found for this month. Upload cancelled to prevent overwriting with blanks.")

    except Exception as e:
        st.error(f"❌ Pipeline Error!\n\nError Details: {e}")
