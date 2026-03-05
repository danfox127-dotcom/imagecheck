import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image
import imagehash
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_image_hash(image):
    return imagehash.phash(image)

def get_columbia_doctor_image(url):
    """
    Specific logic for ColumbiaDoctors.org structure.
    Targets the main profile image to avoid scanning logos/icons.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for the primary profile image 
        # (Columbia typically uses specific classes for doctor headshots)
        img_tag = soup.find('img', {'class': 'field-name-field-image'}) or \
                  soup.find('div', {'class': 'provider-image'}).find('img') or \
                  soup.find('img', {'alt': lambda x: x and 'Profile photo' in x})
        
        if img_tag and img_tag.get('src'):
            return img_tag.get('src')
    except:
        return None
    return None

# --- UI ---
st.title("ColumbiaDoctors Image Matcher 🩺")

# Upload the target image to find
uploaded_image = st.file_uploader("1. Upload Target Doctor Image", type=['jpg', 'jpeg', 'png'])

# Upload the CSV from your previous project
uploaded_csv = st.file_uploader("2. Upload ColumbiaDoctors CSV", type=['csv'])

if uploaded_image and uploaded_csv:
    df = pd.read_csv(uploaded_csv)
    # Automatically find the URL column
    url_col = next((col for col in df.columns if 'url' in col.lower() or 'link' in col.lower()), None)
    
    if not url_col:
        st.error("Could not find a 'URL' column in your CSV.")
    else:
        st.success(f"Found {len(df)} doctor profiles to check.")
        
        if st.button("Start Precision Match"):
            target_hash = get_image_hash(Image.open(uploaded_image).convert('RGB'))
            matches = []
            
            progress_bar = st.progress(0)
            status = st.empty()
            
            # Use ThreadPool to check images quickly
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(get_columbia_doctor_image, row[url_col]): row for _, row in df.iterrows()}
                
                for i, future in enumerate(as_completed(futures)):
                    row = futures[future]
                    img_src = future.result()
                    
                    if img_src:
                        try:
                            # Download and compare
                            img_data = requests.get(img_src, stream=True).content
                            found_img = Image.open(io.BytesIO(img_data)).convert('RGB')
                            if (target_hash - get_image_hash(found_img)) <= 10:
                                matches.append({"Doctor": row.get('Name', 'Unknown'), "URL": row[url_col], "Image": img_src})
                        except:
                            continue
                    
                    progress_bar.progress((i + 1) / len(df))
                    status.text(f"Processed {i+1}/{len(df)} doctors...")

            if matches:
                st.write("### 🚨 Matches Found!")
                for m in matches:
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        st.image(m['Image'])
                    with col2:
                        st.write(f"**{m['Doctor']}**")
                        st.write(f"[View Profile]({m['URL']})")
            else:
                st.info("No matches found in the provided CSV list.")
