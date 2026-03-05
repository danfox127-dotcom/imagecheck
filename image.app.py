import streamlit as st
import pandas as pd
import requests
from PIL import Image
import imagehash
import io
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
CSV_FILE = "columbiadoctors_images.csv" 

# --- Sidebar Debug ---
st.sidebar.subheader("System Debugger")
visible_files = os.listdir(".")
st.sidebar.write("Files in Repo:", visible_files)

def get_image_hash(image):
    return imagehash.phash(image)

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
    # Screaming Frog 'All Images' usually uses 'Source' for the image URL
    # and 'Address' for the page it was found on.
    img_col = next((c for c in df.columns if c.lower() in ['source', 'image url', 'src']), None)
    page_col = next((c for c in df.columns if c.lower() in ['address', 'destination', 'page url']), None)

    if not img_col:
        st.error(f"❌ Could not find an image source column. Found: {list(df.columns)[:5]}...")
    else:
        st.sidebar.success(f"✅ Scanning {len(df)} direct image links.")
        
        uploaded_image = st.file_uploader("Upload Target Photo", type=['jpg', 'jpeg', 'png'])
        
        if uploaded_image:
            target_img = Image.open(uploaded_image).convert('RGB')
            st.image(target_img, caption="Target Image", width=150)
            
            tolerance = st.sidebar.slider("Match Sensitivity", 0, 25, 12)
            
            if st.button("🚀 Run Turbo Scan"):
                target_hash = get_image_hash(target_img)
                matches = []
                
                progress_bar = st.progress(0)
                status = st.empty()
                
                # Internal function for threading
                def check_single_image(img_url):
                    try:
                        resp = requests.get(img_url, stream=True, timeout=5)
                        # We only care about actual images
                        if 'image' in resp.headers.get('Content-Type', ''):
                            test_img = Image.open(io.BytesIO(resp.content)).convert('RGB')
                            diff = target_hash - get_image_hash(test_img)
                            if diff <= tolerance:
                                return True
                    except:
                        pass
                    return False

                # We use a set of URLs to avoid checking the same image twice 
                # (Screaming Frog lists one image for every page it appears on)
                unique_images = df[img_col].unique()

                with ThreadPoolExecutor(max_workers=20) as executor:
                    futures = {executor.submit(check_single_image, url): url for url in unique_images}
                    
                    for i, future in enumerate(as_completed(futures)):
                        img_url = futures[future]
                        if future.result():
                            # Find the first page this image was seen on
                            page_url = df[df[img_col] == img_url][page_col].values[0] if page_col else "Unknown Page"
                            matches.append({"img": img_url, "page": page_url})
                        
                        if i % 10 == 0:
                            progress_bar.progress((i + 1) / len(unique_images))
                            status.text(f"Scanning image {i+1} of {len(unique_images)}...")

                if matches:
                    st.balloons()
                    st.success(f"Found {len(matches)} match(es)!")
                    for m in matches:
                        col1, col2 = st.columns([1, 4])
                        col1.image(m['img'])
                        col2.write(f"**Image Source:** {m['img']}")
                        col2.write(f"🔗 [Found on Page]({m['page']})")
                        st.divider()
                else:
                    st.warning("No matches found.")
