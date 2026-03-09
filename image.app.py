import streamlit as st
import pandas as pd
import requests
from PIL import Image
import imagehash
import io
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
# Make sure this matches your current file!
CSV_FILE = "all_images.csv.zip" 

# --- Sidebar Debug ---
st.sidebar.subheader("System Debugger")
visible_files = os.listdir(".")
st.sidebar.write("Files in Repo:", visible_files)

def get_image_hash(image):
    return imagehash.phash(image)

def is_valid_content_image(url):
    """Strictly filters for content images (JPG, PNG, WEBP) and ignores structural assets."""
    if not isinstance(url, str): 
        return False
    
    url_lower = url.lower()
    clean_url = url_lower.split('?')[0]
    
    if not clean_url.endswith(('.jpg', '.jpeg', '.png', '.webp')):
        return False 
        
    EXCLUSION_KEYWORDS = [
        'logo', 'icon', 'facebook', 'twitter', 'instagram', 
        'linkedin', 'youtube', 'bg', 'background', 'spacer', 
        'button', 'sprite', 'footer', 'header', 'avatar'
    ]
    for keyword in EXCLUSION_KEYWORDS:
        if keyword in url_lower:
            return False
            
    return True

# --- Main App ---
st.title("Site-Wide Image Usage Checker 🖼️")
st.write("Upload a stock photo or b-roll image to see if it is already published somewhere on the website.")

@@st.cache_data
def load_image_data():
    try:
        # Tell pandas to unzip it automatically in the background!
        data = pd.read_csv(CSV_FILE, compression='zip')
        data.columns = [c.strip() for c in data.columns]
        return data
    except Exception as e:
        st.error(f"Error loading {CSV_FILE}: {e}")
        return None

df = load_image_data()

if df is not None:
    img_col = 'Destination' if 'Destination' in df.columns else next((c for c in df.columns if c.lower() in ['address', 'destination', 'image url', 'src', 'url']), None)
    page_col = 'Source' if 'Source' in df.columns else next((c for c in df.columns if c.lower() in ['source', 'page url']), None)

    if not img_col:
        st.error(f"❌ Could not find an image column. Found: {list(df.columns)[:5]}...")
    else:
        all_unique_images = df[img_col].dropna().unique()
        filtered_images = [url for url in all_unique_images if is_valid_content_image(url)]
        
        st.sidebar.success(f"✅ Loaded {len(all_unique_images)} total image links.")
        st.sidebar.info(f"🧹 Filtered down to {len(filtered_images)} strict content photos.")
        
        uploaded_image = st.file_uploader("Upload Image to Check", type=['jpg', 'jpeg', 'png', 'webp'])
        
        if uploaded_image:
            target_img = Image.open(uploaded_image).convert('RGB')
            st.image(target_img, caption="Checking this image against the database...", width=250)
            
            tolerance = st.sidebar.slider("Match Sensitivity", 0, 25, 12, help="Lower = Stricter match. 12 is ideal for compressed web images.")
            
            if st.button("🚀 Search Website Database"):
                target_hash = get_image_hash(target_img)
                matches = []
                
                progress_bar = st.progress(0)
                status = st.empty()
                
                def check_single_image(img_url):
                    try:
                        resp = requests.get(img_url, stream=True, timeout=5)
                        if 'image' in resp.headers.get('Content-Type', '') or img_url.lower().split('?')[0].endswith(('webp', 'jpg', 'png', 'jpeg')):
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
                            # Grab pages, but ensure we don't grab empty ones
                            pages_found = df[df[img_col] == img_url][page_col].unique() if page_col else []
                            matches.append({"img": img_url, "pages": pages_found})
                        
                        progress_bar.progress((i + 1) / len(filtered_images))
                        status.text(f"Scanning image {i+1} of {len(filtered_images)}...")

                # --- Results Display ---
                if matches:
                    st.error(f"⚠️ Image is already in use! Found {len(matches)} matching image file(s).")
                    for m in matches:
                        with st.container():
                            col1, col2 = st.columns([1, 4])
                            with col1:
                                st.image(m['img'], use_container_width=True)
                            with col2:
                                st.write(f"**Live Image Source:** {m['img']}")
                                st.write("**Appears on these pages:**")
                                
                                # --- THE FIX: Clean out the "NaN" values ---
                                valid_pages = [p for p in m['pages'] if pd.notna(p) and str(p).lower() != 'nan']
                                
                                if valid_pages:
                                    for p in valid_pages[:5]: 
                                        st.write(f"🔗 [{p}]({p})")
                                    if len(valid_pages) > 5:
                                        st.write(f"*...and {len(valid_pages) - 5} more pages.*")
                                else:
                                    st.write("📄 *Source page not listed in this specific CSV format.*")
                                    st.caption("💡 To see exact page links, ensure you are using 'Bulk Export > Images > All Images' from the top menu of Screaming Frog.")
                            st.divider()
                else:
                    st.balloons()
                    st.success("✅ Good to go! No matches found. This image appears to be new to the site.")
