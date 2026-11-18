"""Check an image: is it already published on the website?"""

import hashlib

import streamlit as st

from imagecheck.search import NO_MATCH, REVIEW, SAME, Searcher
from imagecheck.webapp import ago, freshness, get_searcher

# Confirmed matches whose aligned region covers less than this fraction of both
# images are a partial overlap rather than the whole photo.
PARTIAL_COVERAGE = 0.25

REASON_LABELS = {
    "getty_id": "Same Getty Images ID",
    "exact_file": "Identical file",
    "near_exact": "Same image, resized or recompressed",
    "geometric": "Same photo (cropped, resized, or edited)",
}


def show_pages(pages: list[str]) -> None:
    if not pages:
        st.caption("No page list for this image in the crawl data.")
        return
    st.markdown(f"**Used on {len(pages)} page{'s' if len(pages) != 1 else ''}:**")
    for p in pages[:5]:
        st.markdown(f"- [{p}]({p})")
    if len(pages) > 5:
        with st.expander(f"See {len(pages) - 5} more pages"):
            for p in pages[5:]:
                st.markdown(f"- [{p}]({p})")


def show_match(searcher: Searcher, c) -> None:
    rec = searcher.index.images[c.row]
    name = rec["url"].rsplit("/", 1)[-1]
    col1, col2 = st.columns([1, 3])
    with col1:
        path = searcher.cache.path_for(rec["url"]) if searcher.cache else None
        st.image(str(path) if path and path.exists() else rec["url"], width="stretch",
                 alt=(rec.get("alt_texts") or [f"Site image {name}"])[0])
    with col2:
        if c.verdict == SAME:
            labels = [REASON_LABELS.get(r, r) for r in c.reasons]
            if c.reasons == ["geometric"] and c.coverage < PARTIAL_COVERAGE:
                labels = ["Shares part of this image (a tight crop, or a reused panel in a composite)"]
            st.markdown("**" + " · ".join(labels) + "**")
        else:
            st.markdown(f"**Looks similar ({c.similarity:.0%}), but not confirmed as the same photo.** "
                        "This could be another shot from the same photo shoot. Check it yourself.")
        st.markdown(f"Original file: [{name}]({rec['url']})")
        if rec.get("alt_texts"):
            st.caption("Alt text: " + " | ".join(rec["alt_texts"][:3]))
        show_pages(rec.get("pages", []))
        st.caption(f"Evidence: {c.inliers} aligned keypoints, {c.agreement:.0%} of detail agrees "
                   f"after alignment, visual similarity {c.similarity:.2f}"
                   + (f", pHash distance {c.phash_distance}" if c.phash_distance is not None else ""))
        if c.error:
            st.caption(f":material/warning: {c.error}")
    st.divider()


st.title("Image usage checker", icon=":material/image_search:")
st.caption("Upload an image to see whether it's already published on the website, "
           "including cropped, resized, or edited copies.")

searcher = get_searcher()
if searcher is None:
    st.warning("There's no image index yet. Scan the site first.", title="Nothing to search",
               icon=":material/travel_explore:")
    st.page_link("app_pages/scan.py", label="Go to Site scan", icon=":material/travel_explore:")
    st.stop()

uploaded = st.file_uploader("Image to check", type=["jpg", "jpeg", "png", "webp"], key="upload")
if not uploaded:
    st.stop()
data = uploaded.getvalue()

# Reruns (opening an expander, ticking a box) reuse the result instead of searching again.
result_key = (hashlib.sha256(data).hexdigest(), uploaded.name, searcher.index.meta.get("build_id"))
cached = st.session_state.get("last_result")
if cached and cached[0] == result_key:
    result = cached[1]
else:
    with st.spinner("Searching..."):
        try:
            result = searcher.search(data, uploaded.name)
        except (OSError, ValueError) as e:
            st.error(f"Couldn't read that image: {e}", icon=":material/broken_image:")
            st.stop()
    st.session_state.last_result = (result_key, result)

left, right = st.columns([1, 3])
with left:
    st.image(data, caption=uploaded.name, width="stretch", alt=f"Uploaded image {uploaded.name}")
with right:
    cov = result.coverage
    n_failed = len(cov["failed"])
    fresh = freshness()
    as_of = f"as of the last full scan ({fresh['date']}, {ago(fresh['days'])})" if fresh["full"] else ""
    if result.verdict == SAME:
        n = len(result.same)
        st.error(f"Found {n} matching site image{'s' if n != 1 else ''}.", title="Already on the site",
                 icon=":material/warning:")
    elif result.verdict == REVIEW:
        st.warning("Nothing is confirmed as the same photo. Check the look-alikes below.",
                   title="No confirmed match, but similar images exist", icon=":material/help:")
    else:
        st.success(f"No copy of this image is on the site{' ' + as_of if as_of else ''}.",
                   title="Not found on the site", icon=":material/check_circle:")
    st.caption(f"Checked against {cov['indexed']:,} site images in {result.timings['total']:.1f}s.")
    if result.verdict != SAME and fresh["stale"]:
        st.warning("Images published after the last full scan aren't in the index. Run a scan to be sure.",
                   title="Site data is out of date", icon=":material/history:")
    if n_failed and result.verdict != SAME:
        st.warning(f"{n_failed:,} site image(s) couldn't be downloaded when the index was built, "
                   "so they weren't checked. See the sidebar.", icon=":material/warning:")

if result.same:
    st.subheader("Matches")
    for c in result.same:
        show_match(searcher, c)
if result.review:
    if result.same:
        with st.expander(f"Also similar, not the same photo ({len(result.review)})"):
            for c in result.review:
                show_match(searcher, c)
    else:
        st.subheader("Look-alikes to review")
        for c in result.review:
            show_match(searcher, c)
if result.verdict != NO_MATCH or st.checkbox("Show closest candidates anyway", key="show_candidates"):
    with st.expander("Debug: all candidates examined"):
        st.dataframe(
            [{"url": c.url, "verdict": c.verdict, "similarity": round(c.similarity, 3),
              "inliers": c.inliers, "agreement": round(c.agreement, 3), "coverage": round(c.coverage, 3),
              "phash_dist": c.phash_distance, "reasons": ", ".join(c.reasons)} for c in result.candidates],
            width="stretch", alt="Every site image the search examined, with its match scores",
        )
