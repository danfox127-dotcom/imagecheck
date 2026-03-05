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
        
        # Priority selectors for Columbia headshots
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
    # Logic to find the correct columns based on your Screaming Frog export
    url_col = next((c for c in df.columns if c.lower() in ['address', 'url', 'link']), None)
    name_col = next((c for c in df.columns if c.lower() in ['name', 'title 1']), None)
    
    if not url_col:
        st.error(f"❌ Missing 'Address' column. Found: {list(df.columns)[:5]}...")
    else:
        st.sidebar.success(f"✅ Mapping to: '{url_col}'")
        
        uploaded_image = st.file_uploader("Upload Doctor Photo", type=['jpg', 'jpeg', 'png'])
        
        if uploaded_image:
            target_img = Image.open(uploaded_image).convert('RGB')
            st.image(target_img, caption="Searching...", width=200)
            
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
                                    # Fallback logic for doctor name
                                    display_name = row[name_col] if name_col else "Doctor Profile"
                                    matches.append({
                                        "Name": display_name, 
                                        "URL": row[url_col], 
                                        "Image": img_src
                                    })
                            except:
                                continue
                        
                        progress_bar.progress((i + 1) / len(df))
                        status.text(f"Processed {i+1} of {len(df)}...")

                if matches:
                    st.balloons()
                    st.success(f"Found {len(matches)} potential match(es)!")
                    for m in matches:
                        with st.container():
                            c1, c2 = st.columns([1, 4])
                            with c1:
                                st.image(m['Image'], use_container_width=True)
                            with c2:
                                st.subheader(m['Name'])
                                st.write(f"🔗 [View Official Profile]({m['URL']})")
                            st.divider()
                else:
                    st.warning("No visual match found.")
