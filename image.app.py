import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image
import imagehash
import io
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
CSV_FILE = "columbiadoctors_internal_all"  # Updated filename
TARGET_COLUMN = "URL"                          # Ensure this matches your CSV header!

def get_image_hash(image):
    """Generates a perceptual fingerprint (hash) for an image."""
    return imagehash.phash(image)

def get_columbia_doctor_image(url):
    """Targets the specific HTML structure of ColumbiaDoctors profile pages."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Priority 1: The specific Columbia 'field-name-field-image' class
        # Priority 2: Standard provider image containers
        # Priority 3: First meaningful image with an alt tag
        img_tag = soup.select_one('.field-name-field-image img') or \
                  soup.select_one('.provider-image img') or \
                  soup.find('img', alt=True)
        
        if img_tag and img_tag.get('src'):
            return urljoin(url, img_tag.get('src'))
    except:
        return None
    return None

# --- Streamlit UI Setup ---
st.set_page_config(page_title="Columbia Image Matcher", layout="wide")
st.title("ColumbiaDoctors Precision Matcher 🩺")
st.markdown("Upload a photo to see if that doctor exists in the internal database.")

# Load the hard-coded internal CSV
@st.cache_data
def load_internal_data():
    try:
        data = pd.read_csv(CSV_FILE)
        return data
    except FileNotFoundError:
        st.error(f"Critical Error: '{CSV_FILE}' was not found in the GitHub repository.")
        return None

df = load_internal_data()

if df is not None:
    st.sidebar.info(f"Database Loaded: {len(df)} profiles.")
    
    uploaded_image = st.file_uploader("Upload Doctor Photo", type=['jpg', 'jpeg', 'png'])
    
    if uploaded_image:
        target_img = Image.open(uploaded_image).convert('RGB')
        st.image(target_img, caption="Searching for this individual...", width=200)
        
        if st.button("🔎 Scan Internal Database"):
            target_hash = get_image_hash(target_img)
            matches = []
            
            progress_bar = st.progress(0)
            status = st.empty()
            
            # Using ThreadPool for parallel processing (speed)
            with ThreadPoolExecutor(max_workers=10) as executor:
                # Map the scraping function to every URL in the hard-coded CSV
                futures = {executor.submit(get_columbia_doctor_image, row[TARGET_COLUMN]): row for _, row in df.iterrows()}
                
                for i, future in enumerate(as_completed(futures)):
                    row = futures[future]
                    img_src = future.result()
                    
                    if img_src:
                        try:
                            # Compare the found web image to our target
                            img_data = requests.get(img_src, stream=True, timeout=5).content
                            found_img = Image.open(io.BytesIO(img_data)).convert('RGB')
                            
                            # Hamming distance <= 12 is a solid match for web-compressed photos
                            if (target_hash - get_image_hash(found_img)) <= 12:
                                matches.append({
                                    "Name": row.get('Name', 'Doctor'), 
                                    "URL": row[TARGET_COLUMN], 
                                    "Image": img_src
                                })
                        except:
                            continue
                    
                    # Update UI progress
                    progress_bar.progress((i + 1) / len(df))
                    status.text(f"Analyzing profile {i+1} of {len(df)}...")

            # --- Results Display ---
            if matches:
                st.balloons()
                st.success(f"Match found! Found {len(matches)} instance(s).")
                for m in matches:
                    with st.container():
                        col1, col2 = st.columns([1, 4])
                        with col1:
                            st.image(m['Image'], use_container_width=True)
                        with col2:
                            st.subheader(m['Name'])
                            st.write(f"🔗 [View Official Profile]({m['URL']})")
                        st.divider()
            else:
                st.warning("No visual match found in the database. Ensure the photo is clear.")
