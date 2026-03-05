import streamlit as st
import requests
from bs4 import BeautifulSoup
from PIL import Image
import imagehash
import io
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Logic Functions ---

def get_image_hash(image):
    """Generates the perceptual hash."""
    return imagehash.phash(image)

def is_internal(base_url, link_url):
    """Ensures the crawler stays on the target domain."""
    base_netloc = urlparse(base_url).netloc
    link_netloc = urlparse(link_url).netloc
    return link_netloc == '' or link_netloc == base_netloc

def process_page(url, target_hash, tolerance):
    """Function run by each thread to scan a single page."""
    page_matches = []
    new_links = set()
    try:
        # User-Agent makes the request look like a real browser
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find images
        for img in soup.find_all('img'):
            src = img.get('src')
            if not src: continue
            img_url = urljoin(url, src)
            try:
                img_data = requests.get(img_url, headers=headers, stream=True, timeout=5).content
                web_img = Image.open(io.BytesIO(img_data)).convert('RGB')
                if (target_hash - get_image_hash(web_img)) <= tolerance:
                    page_matches.append({"page": url, "img": img_url})
            except:
                continue

        # Find links for the next batch
        for a in soup.find_all('a', href=True):
            link = urljoin(url, a['href'])
            if is_internal(url, link):
                # Clean URL (remove fragments like #section1)
                clean_link = link.split('#')[0].rstrip('/')
                new_links.add(clean_link)

    except Exception as e:
        pass # Silent fail for broken pages
    
    return page_matches, new_links

# --- Streamlit UI ---

st.set_page_config(page_title="Image Hunter Pro", layout="wide")
st.title("Image Hunter Pro 🛰️")
st.sidebar.header("Settings")

uploaded_file = st.sidebar.file_uploader("1. Upload Image", type=['png', 'jpg', 'jpeg'])
domain_url = st.sidebar.text_input("2. Target Domain URL")
max_p = st.sidebar.slider("Max pages", 5, 200, 50)
threads = st.sidebar.slider("Parallel Threads", 1, 10, 5)
tolerance = st.sidebar.slider("Match Sensitivity (Lower = Stricter)", 0, 20, 10)

if st.sidebar.button("Launch Domain Scan") and uploaded_file and domain_url:
    target_img = Image.open(uploaded_file).convert('RGB')
    t_hash = get_image_hash(target_img)
    
    visited = set()
    to_visit = {domain_url.rstrip('/')}
    all_matches = []
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    # Using ThreadPoolExecutor for speed
    with ThreadPoolExecutor(max_workers=threads) as executor:
        while to_visit and len(visited) < max_p:
            # Batch process the current 'to_visit' list
            current_batch = list(to_visit)[:threads]
            for url in current_batch:
                to_visit.remove(url)
                visited.add(url)
            
            futures = {executor.submit(process_page, url, t_hash, tolerance): url for url in current_batch}
            
            for future in as_completed(futures):
                matches, found_links = future.result()
                all_matches.extend(matches)
                
                # Add new links that haven't been visited or queued
                for link in found_links:
                    if link not in visited:
                        to_visit.add(link)
                
                status_text.text(f"Scanned {len(visited)} / {max_p} pages...")
                progress_bar.progress(min(len(visited) / max_p, 1.0))

    if all_matches:
        st.success(f"Done! Found {len(all_matches)} matches.")
        cols = st.columns(3)
        for i, res in enumerate(all_matches):
            with cols[i % 3]:
                st.image(res['img'], use_container_width=True)
                st.caption(f"Found on: {res['page']}")
    else:
        st.warning("Scan complete. No matches found.")
