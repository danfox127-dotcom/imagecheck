"""Image Usage Checker: is this image already on the website?

    streamlit run app.py
"""

import streamlit as st

from imagecheck.webapp import ago, current_index, describe_age, freshness, scan_job

st.set_page_config(page_title="Image usage checker", page_icon=":material/image_search:", layout="wide")

check = st.Page(
    "app_pages/check.py", title="Check an image", icon=":material/image_search:", default=True,
    help="Upload an image to see whether it's already on the website, including cropped, resized or edited copies.",
)
scan = st.Page(
    "app_pages/scan.py", title="Site scan", icon=":material/travel_explore:",
    help="Crawl the website to find newly published images and update the index the checker searches.",
)
page = st.navigation([check, scan], position="top")

# ---- sidebar: how fresh and complete the data is (shown on every page)
with st.sidebar:
    fresh = freshness()
    job = scan_job()
    status = job.status()
    if status.active:
        st.info(f"Scan in progress: {status.pages_done:,} pages checked so far.", icon=":material/sync:",
                title="Scanning the site")
    if fresh["days"] is None and not fresh["full"]:
        st.warning("The site hasn't been scanned yet.", title="No scan yet", icon=":material/warning:")
        st.page_link(scan, label="Scan the site", icon=":material/travel_explore:")
    elif fresh["stale"]:
        st.warning(f"Last full scan: {fresh['date']} ({ago(fresh['days'])}). Images published since then "
                   "won't be found.", title="Site data is out of date", icon=":material/history:")
        st.page_link(scan, label="Scan the site now", icon=":material/travel_explore:")
    else:
        st.success(f"Last full scan: {fresh['date']} ({ago(fresh['days'])}).", title="Site data is current",
                   icon=":material/check_circle:")
    last = fresh["last"]
    if last.get("include_paths") and last.get("finished_at", "") > fresh["full"].get("finished_at", ""):
        d, n = describe_age(last["finished_at"])
        st.caption(f"Sections rescanned {ago(n)}: {', '.join('/' + p.strip('/') for p in last['include_paths'])}")

    index = current_index()
    if index is not None:
        cov = index.coverage()
        st.metric("Site images indexed", f"{cov['indexed']:,}",
                  help="Distinct original images found on the site (all crops of a photo count once).")
        if cov["failed"]:
            with st.expander(f"{len(cov['failed']):,} images could not be indexed", icon=":material/warning:"):
                for f in cov["failed"][:200]:
                    st.caption(f"{f['url']} ({f['error'][:120]})")

page.run()
