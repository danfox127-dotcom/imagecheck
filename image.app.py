import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image
import imagehash
import io
import os  # Added for debugging
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
CSV_FILE = "columbiadoctors_internal_all.csv" 
TARGET_COLUMN = "URL"

# --- DEBUG SECTION ---
st.sidebar.subheader("System Debugger")
visible_files = os.listdir(".")
st.sidebar.write("Files found in Repo:", visible_files)

if CSV_FILE not in visible_files:
    st.sidebar.error(f"❌ '{CSV_FILE}' is MISSING from the list above!")
else:
    st.sidebar.success(f"✅ '{CSV_FILE}' found and ready.")
# ---------------------

def get_image_hash(image):
    return imagehash.phash(image)

def get_columbia_doctor_image(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Targeting Columbia's specific image classes
        img_tag = soup.select_one('.field-name-field-image img') or \
                  soup.select_one('.provider-image img') or \
                  soup.find('img', alt=True)
        
        if img_tag and img_tag.get('src'):
            return urljoin(url, img_tag.get('src'))
    except:
        return None
    return None

# --- Main App ---
st.title("ColumbiaDoctors Precision Matcher 🩺")

@st.cache_data
def load_internal_data():
    try:
        return pd.read_csv(CSV_FILE)
    except Exception as e:
        st.error(f"Error loading CSV: {e}")
        return None

df = load_internal_data()

if df is not None:
    uploaded_image = st.file_uploader("Upload Doctor Photo", type=['jpg', 'jpeg', 'png'])
    
    if uploaded_image:
        target_img = Image.open(uploaded_image).convert('RGB')
        st.image(target_img, caption="Target Image", width=200)
        
        if st.button("🔎 Scan Database"):
            target_hash = get_image_hash(target_img)
            matches = []
            
            progress_bar = st.progress(0)
            status = st.empty()
            
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(get_columbia_doctor_image, row[TARGET_COLUMN]): row for _, row in df.iterrows()}
                
                for i, future in enumerate(as_completed(futures)):
                    row = futures[future]
                    img_src = future.result()
                    
                    if img_src:
                        try:
                            img_data = requests.get(img_src, stream=True, timeout=5).content
                            found_img = Image.open(io.BytesIO(img_data)).convert('RGB')
                            
                            if (target_hash - get_image_hash(found_img)) <= 12:
                                matches.append({
                                    "Name": row.get('Name', 'Doctor'), 
                                    "URL": row[TARGET_COLUMN], 
                                    "Image": img_src
                                })
                        except:
                            continue
                    
                    progress_bar.progress((i + 1) / len(df))
                    status.text(f"Scanning {i+1}/{len(df)}...")

            if matches:
                st.balloons()
                for m in matches:
                    col1, col2 = st.columns([1, 4])
                    col1.image(m['Image'])
                    col2.subheader(m['Name'])
                    col2.write(f"🔗 [Profile]({m['URL']})")
                    st.divider()
            else:
                st.warning("No match found.")
