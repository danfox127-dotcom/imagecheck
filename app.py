"""Streamlit UI: is this image already on the website?

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from imagecheck.config import DEFAULT_CACHE_DIR, DEFAULT_INDEX_DIR, Thresholds
from imagecheck.features import Embedder
from imagecheck.fetch import ImageCache
from imagecheck.index import Index
from imagecheck.search import NO_MATCH, REVIEW, SAME, Searcher

st.set_page_config(page_title="Image Usage Checker", page_icon="🖼️", layout="wide")

# Confirmed matches whose aligned region covers less than this fraction of both
# images are a partial overlap rather than the whole photo.
PARTIAL_COVERAGE = 0.25

REASON_LABELS = {
    "getty_id": "Same Getty Images ID",
    "exact_file": "Identical file",
    "near_exact": "Same image, resized or recompressed",
    "geometric": "Same photo (cropped, resized, or edited)",
}


@st.cache_resource(show_spinner="Loading image index...")
def load_index() -> Index | None:
    if not (DEFAULT_INDEX_DIR / "images.json").exists():
        return None
    return Index.load(DEFAULT_INDEX_DIR)


@st.cache_resource(show_spinner="Loading image model (first run downloads ~90 MB)...")
def load_embedder() -> Embedder:
    return Embedder()


def get_searcher() -> Searcher | None:
    index = load_index()
    if index is None:
        return None
    if "searcher" not in st.session_state:
        st.session_state.searcher = Searcher(
            index, load_embedder(), ImageCache(DEFAULT_CACHE_DIR),
            Thresholds.load(DEFAULT_INDEX_DIR / "thresholds.json"),
        )
    return st.session_state.searcher


def show_pages(pages: list[str]) -> None:
    if not pages:
        st.caption("No page list for this image in the crawl export.")
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
    col1, col2 = st.columns([1, 3])
    with col1:
        path = searcher.cache.path_for(rec["url"]) if searcher.cache else None
        st.image(str(path) if path and path.exists() else rec["url"], width="stretch")
    with col2:
        if c.verdict == SAME:
            labels = [REASON_LABELS.get(r, r) for r in c.reasons]
            if c.reasons == ["geometric"] and c.coverage < PARTIAL_COVERAGE:
                labels = ["Shares part of this image (a tight crop, or a reused panel in a composite)"]
            st.markdown("**" + " · ".join(labels) + "**")
        else:
            st.markdown(f"**Looks similar ({c.similarity:.0%}), but not confirmed as the same photo.** "
                        "This could be another shot from the same photo shoot. Check it yourself.")
        st.markdown(f"Original file: [{rec['url'].rsplit('/', 1)[-1]}]({rec['url']})")
        if rec.get("alt_texts"):
            st.caption("Alt text: " + " | ".join(rec["alt_texts"][:3]))
        show_pages(rec.get("pages", []))
        st.caption(f"Evidence: {c.inliers} aligned keypoints, {c.agreement:.0%} of detail agrees "
                   f"after alignment, visual similarity {c.similarity:.2f}"
                   + (f", pHash distance {c.phash_distance}" if c.phash_distance is not None else ""))
        if c.error:
            st.caption(f"⚠️ {c.error}")
    st.divider()


def sidebar(index: Index) -> None:
    cov = index.coverage()
    st.sidebar.subheader("Index")
    st.sidebar.write(f"**{cov['indexed']:,}** of {cov['total']:,} site images indexed")
    if cov.get("built_at"):
        st.sidebar.caption(f"Built {cov['built_at'][:16].replace('T', ' ')} UTC from "
                           f"{', '.join(index.meta.get('crawl_files', [])) or 'crawl exports'}")
    if cov["failed"]:
        with st.sidebar.expander(f"⚠️ {len(cov['failed'])} images could not be indexed"):
            for f in cov["failed"]:
                st.caption(f"{f['url']} ({f['error'][:120]})")
    st.sidebar.caption("To refresh: export a new Screaming Frog crawl into data/crawls/ and run "
                       "`python scripts/build_index.py`.")


def main() -> None:
    st.title("Image Usage Checker 🖼️")
    st.write("Upload an image to see whether it's already published on the website, "
             "including cropped, resized, or edited copies.")

    searcher = get_searcher()
    if searcher is None:
        st.error("No index found. Build one first:\n\n```\npython scripts/build_index.py\n```")
        return
    sidebar(searcher.index)

    uploaded = st.file_uploader("Image to check", type=["jpg", "jpeg", "png", "webp"])
    if not uploaded:
        return
    data = uploaded.getvalue()

    left, right = st.columns([1, 3])
    with left:
        st.image(data, caption=uploaded.name, width="stretch")
    with right:
        with st.spinner("Searching..."):
            try:
                result = searcher.search(data, uploaded.name)
            except (OSError, ValueError) as e:
                st.error(f"Couldn't read that image: {e}")
                return
        cov = result.coverage
        n_failed = len(cov["failed"])
        if result.verdict == SAME:
            n = len(result.same)
            st.error(f"### ⚠️ Already on the site\nFound {n} matching site image{'s' if n != 1 else ''}.")
        elif result.verdict == REVIEW:
            st.warning("### 🤔 No confirmed match, but similar images exist\n"
                       "Nothing is confirmed as the same photo. Check the look-alikes below.")
        else:
            st.success("### ✅ Not found on the site")
        st.caption(f"Checked against {cov['indexed']:,} site images in {result.timings['total']:.1f}s.")
        if n_failed and result.verdict != SAME:
            st.warning(f"{n_failed} site image(s) couldn't be downloaded when the index was built, "
                       "so they weren't checked. See the sidebar.")

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
    if result.verdict != NO_MATCH or st.checkbox("Show closest candidates anyway"):
        with st.expander("Debug: all candidates examined"):
            st.dataframe(
                [{"url": c.url, "verdict": c.verdict, "similarity": round(c.similarity, 3),
                  "inliers": c.inliers, "agreement": round(c.agreement, 3), "coverage": round(c.coverage, 3),
                  "phash_dist": c.phash_distance, "reasons": ", ".join(c.reasons)} for c in result.candidates],
                width="stretch",
            )


main()
