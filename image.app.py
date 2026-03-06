import streamlit as st
import pandas as pd
import requests
from PIL import Image
import imagehash
import io
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
CSV_FILE = "columbiadoctors_images_used_3_6.csv"

# --- Sidebar Debug ---
st.sidebar.subheader("System Debugger")
visible_files = os.listdir(".")
st.sidebar.write("Files in Repo:", visible_files)

# --- Smart Filtering ---
# Add any other words here that you notice in junk image URLs
EXCLUSION_KEYWORDS = [
    'logo', 'icon', 'facebook', 'twitter', 'instagram', 
    'linkedin', 'youtube', 'bg', 'background', 'spacer', 
    'button', 'sprite', 'social', 'footer', 'header'
]

def get_image_hash(image):
    return imagehash.phash(image)

def is_valid_image_url(url):
    """Checks if an image URL is likely a real photo and not a site asset."""
    if not isinstance(url, str): 
        return False
    
    url_lower = url.lower()
    
    # 1. Skip SVGs (always graphics/logos)
    if url_lower.endswith('.svg'): 
        return False 
        
    # 2. Skip if it contains our exclusion keywords
    for keyword in EXCLUSION_KEYWORDS:
        if keyword in url_lower:
            return False
            
    return True

# --- Main App ---
st.title("Turbo Image Matcher 🏎️")
st.write("Directly comparing your photo against the Screaming Frog image database.")

@st.cache_data
def load_image_data():
    try:
        data = pd.read_csv(CSV_FILE)
        data.columns = [c.strip() for c in data.columns]
        return data
    except Exception as e:
        st.error(f"Error loading {CSV_FILE}: {e}")
        return None

df = load_image_data()

if df is not None:
    # Auto-detect the right columns from Screaming Frog
    img_col = next((c for c in df.columns if c.lower() in ['source', 'image url', 'src', 'url']), None)
    page_col = next((c for c in df.columns if c.lower() in ['address', 'destination', 'page url']), None)

    if not img_col:
        st.error(f"❌ Could not find an image source column. Found: {list(df.columns)[:5]}...")
    else:
        # Apply our Smart Filter to the unique images
        all_unique_images = df[img_col].unique()
        filtered_images = [url for url in all_unique_images if is_valid_image_url(url)]
        
        st.sidebar.success(f"✅ Loaded {len(all_unique_images)} total images.")
        st.sidebar.info(f"🧹 Filtered down to {len(filtered_images)} likely photos.")
        
        uploaded_image = st.file_uploader("Upload Target Photo", type=['jpg', 'jpeg', 'png'])
        
        if uploaded_image:
            target_img = Image.open(uploaded_image).convert('RGB')
            st.image(target_img, caption="Target Image", width=150)
            
            tolerance = st.sidebar.slider("Match Sensitivity", 0, 25, 12, help="Lower = Stricter match")
            
            if st.button("🚀 Run Turbo Scan"):
                target_hash = get_image_hash(target_img)
                matches = []
                
                progress_bar = st.progress(0)
                status = st.empty()
                
                def check_single_image(img_url):
                    try:
                        resp = requests.get(img_url, stream=True, timeout=5)
                        if 'image' in resp.headers.get('Content-Type', ''):
                            test_img = Image.open(io.BytesIO(resp.content)).convert('RGB')
                            diff = target_hash - get_image_hash(test_img)
                            if diff <= tolerance:
                                return True
                    except:
                        pass
                    return False

                with ThreadPoolExecutor(max_workers=20) as executor:
                    futures = {executor.submit(check_single_image, url): url for url in filtered_images}
                    
                    for i, future in enumerate(as_completed(futures)):
                        img_url = futures[future]
                        if future.result():
                            # Find where this image lives
                            page_url = df[df[img_col] == img_url][page_col].values[0] if page_col else "Unknown Page"
                            matches.append({"img": img_url, "page": page_url})
                        
                        # Update progress bar smoothly
                        progress_bar.progress((i + 1) / len(filtered_images))
                        status.text(f"Scanning image {i+1} of {len(filtered_images)}...")

                # --- Results Display ---
                if matches:
                    st.balloons()
                    st.success(f"Found {len(matches)} match(es)!")
                    for m in matches:
                        with st.container():
                            col1, col2 = st.columns([1, 4])
                            with col1:
                                st.image(m['img'], use_container_width=True)
                            with col2:
                                st.write(f"**Image Source:** {m['img']}")
                                st.write(f"🔗 [Found on Page]({m['page']})")
                            st.divider()
                else:
                    st.warning("No matches found. Try increasing the Match Sensitivity.")
