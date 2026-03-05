import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image
import imagehash
import io
import os
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
CSV_FILE = "columbiadoctors_internal_all.csv" 

# --- Debug Section (Sidebar) ---
st.sidebar.subheader("System Debugger")
visible_files = os.listdir(".")
st.sidebar.write("Files in Repo:", visible_files)

def get_image_hash(image):
    return imagehash.phash(image)

def get_columbia_doctor_image(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Specific Columbia image selectors
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
        data = pd.read_csv(CSV_FILE)
        data.columns = [c.strip() for c in data.columns]
        return data
    except Exception as e:
        st.error(f"Error loading CSV: {e}")
        return None

df = load_internal_data()

if df is not None:
    # --- UPDATED AUTO-DETECT ---
    # Now includes 'address' which is common in Screaming Frog exports
    url_col = next((c for c in df.columns if c.lower() in ['address', 'url', 'link', 'url encoded address']), None)
    
    if not url_col:
        st.error(f"❌ Could not find a URL/Address column. Columns found: {list(df.columns)[:5]}...")
    else:
        st.sidebar.success(f"✅ Mapping to column: '{url_col}'")
        
        uploaded_image = st.file_uploader("Upload Doctor Photo", type=['jpg', 'jpeg', 'png'])
        
        if uploaded_image:
            target_img = Image.open(uploaded_image).convert('RGB')
            st.image(target_img, caption="Searching for matches...", width=200)
            
            tolerance = st.sidebar.slider("Match Sensitivity", 0, 25, 12)
            
            if st.button("🔎 Scan Internal Database"):
                target_hash = get_image_hash(target_img)
                matches = []
                
                progress_bar = st.progress(0)
                status = st.empty()
                
                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = {executor.submit(get_columbia_doctor_image, row[url_col]): row for _, row in df.iterrows()}
                    
                    for i, future in enumerate(as_completed(futures)):
                        row = futures[future]
                        img_src = future.result()
                        
                        if img_src:
                            try:
                                img_data = requests.get(img_src, stream=True, timeout=5).content
                                found_img = Image.open(io.BytesIO(img_data)).convert('RGB')
                                
                                if (target_hash - get_image_hash(found_img)) <= tolerance:
                                    # Use 'Title 1' as the Name if 'Name' column doesn't exist
                                    doc_name = row.get('Name', row.get('Title 1', 'Doctor Profile'))
                                    matches.append({
                                        "
