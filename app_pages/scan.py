"""Site scan: crawl the website and update the image index."""

import time
from datetime import datetime, timezone

import streamlit as st

from imagecheck.config import DEFAULT_CRAWL_DIR, DEFAULT_SCAN_DIR
from imagecheck.pages import load_meta
from imagecheck.scan import (DEFAULT_SKIP_FOLDERS, DEFAULT_START_URL, ScanNotAllowed, ScanSettings, ScanStatus,
                             validate_start_url)
from imagecheck.webapp import ago, describe_age, freshness, known_page_paths, prepare_job, scan_job

PHASES = {"crawling": "Crawling pages", "indexing": "Updating the image index", "importing": "Importing"}
STATE_ALERTS = {
    "done": (st.success, ":material/check_circle:", "Finished"),
    "cancelled": (st.warning, ":material/stop_circle:", "Cancelled"),
    "failed": (st.error, ":material/error:", "Failed"),
    "interrupted": (st.warning, ":material/sync_problem:", "Interrupted"),
}


def _elapsed(status: ScanStatus) -> str:
    try:
        start = datetime.fromisoformat(status.started_at)
    except ValueError:
        return ""
    end = datetime.fromisoformat(status.finished_at) if status.finished_at else datetime.now(timezone.utc)
    mins = int((end - start).total_seconds() // 60)
    return f"{mins // 60} h {mins % 60} min" if mins >= 60 else f"{mins} min"


def _folders(text: str) -> list[str]:
    return [p.strip().strip("/") for p in text.replace(",", "\n").splitlines() if p.strip().strip("/")]


def show_running(status: ScanStatus) -> None:
    with st.container(border=True):
        st.markdown(f"**{status.message or PHASES.get(status.phase, 'Starting')}** · running {_elapsed(status)}")
        if status.phase == "crawling":
            total = status.pages_done + status.pages_queued
            st.progress(status.pages_done / total if total else 0.0,
                        text=f"{status.pages_done:,} pages checked, {status.pages_queued:,} waiting in the queue")
        elif status.index_total:
            st.progress(status.index_done / status.index_total,
                        text=f"{status.index_done:,} of {status.index_total:,} images")
        with st.container(horizontal=True):
            st.metric("Pages checked", f"{status.pages_done:,}")
            st.metric("Unchanged", f"{status.pages_unchanged:,}",
                      help="Pages the server said haven't changed since the last scan.")
            st.metric("Failed", f"{status.pages_failed:,}", help="Errors, timeouts and 4xx/5xx responses.")
            st.metric("Images found", f"{status.images_seen:,}",
                      help="Distinct original images on the pages crawled so far.")
        if st.button("Cancel scan", icon=":material/stop:", key="cancel_scan"):
            scan_job().cancel()
            st.toast("Stopping after the pages in progress...", icon=":material/stop_circle:")


def show_result(status: ScanStatus) -> None:
    alert, icon, title = STATE_ALERTS.get(status.state, (st.info, ":material/info:", status.state.title()))
    what = {"scan": "Scan", "import": "Import", "reindex": "Index update"}.get(status.kind, "Run")
    finished = describe_age(status.finished_at or status.updated_at)
    body = status.error or status.message or ""
    if status.state == "interrupted":
        body = ("The app stopped while this was running. Pages fetched so far were saved; "
                "start a scan again to pick up where it left off.")
    alert(f"{body}  \n{what} started {describe_age(status.started_at)[0]}, ran {_elapsed(status)}"
          f" (finished {ago(finished[1])}).", title=f"Last {what.lower()}: {title.lower()}", icon=icon)
    s = status.summary
    if s:
        with st.container(horizontal=True):
            if "pages" in s:
                st.metric("Pages", f"{s['pages']:,}")
            if "pages_unchanged" in s:
                st.metric("Unchanged pages", f"{s['pages_unchanged']:,}")
            if "added" in s:
                st.metric("New images", f"{s['added']:,}")
                st.metric("Removed images", f"{s['removed']:,}",
                          help="Images no longer on any page the scan found.")
            if "indexed" in s:
                st.metric("Images indexed", f"{s['indexed']:,}")
    for w in status.warnings:
        st.warning(w, icon=":material/warning:")


# Rerun this section every 2 s while a scan runs; switch the whole page back when it ends.
running_now = scan_job().status().active


@st.fragment(run_every="2s" if running_now else None)
def status_panel() -> None:
    status = scan_job().status()
    if status.active:
        show_running(status)
    elif running_now:
        st.rerun(scope="app")  # finished: refresh the sidebar, controls and index
    elif status.state != "idle":
        show_result(status)


st.title("Site scan", icon=":material/travel_explore:")
fresh = freshness()
if fresh["full"]:
    src = "Screaming Frog export" if fresh["full"].get("kind") == "screaming_frog" else "scan"
    st.caption(f"The index reflects the site as of the last full {src}: {fresh['date']} ({ago(fresh['days'])}). "
               "Scanning crawls every page, finds the images on it (including lazy-loaded ones) and "
               "downloads only images that are new since last time.")
else:
    st.caption("Scanning crawls every page, finds the images on it (including lazy-loaded ones), "
               "and builds the index the checker searches.")

status_panel()

st.subheader("Start a scan")
with st.container(border=True):
    scope = st.segmented_control("What to scan", ["Whole site", "Only these folders"], default="Whole site",
                                 required=True, key="scope")
    only = ""
    if scope == "Only these folders":
        only = st.text_input("Only these folders", placeholder="news, childrens-health", key="only_folders",
                             help="Scan just these top-level folders (comma-separated). Pages elsewhere keep "
                                  "the images found by earlier scans.")
    skip = st.text_input(
        "Skip these folders", value=", ".join(DEFAULT_SKIP_FOLDERS), key="skip_folders",
        help="Folders not to crawl (comma-separated). End with /* to skip only what's under a folder and "
             "still check the folder's own page: health-library/* skips ~6,700 Healthwise articles "
             "(third-party images) but keeps the library's landing page. Pages in skipped folders keep "
             "the images found by earlier scans.",
    )
    with st.expander("Advanced settings", icon=":material/tune:"):
        start_url = st.text_input("Start URL", value=DEFAULT_START_URL, key="start_url")
        c1, c2, c3 = st.columns(3)
        max_pages = c1.number_input("Max pages", 10, 100_000, ScanSettings.max_pages, step=1000, key="max_pages")
        workers = c2.number_input("Parallel requests", 1, 16, ScanSettings.workers, key="workers")
        delay = c3.number_input("Delay between requests (s)", 0.05, 10.0, ScanSettings.delay, step=0.05,
                                key="delay", help="Minimum gap between requests across all workers. 0.25 s "
                                "(4 pages/s) was load-tested on this site with no throttling.")
        respect_robots = st.toggle("Obey robots.txt Disallow rules", value=True, key="respect_robots")
        honor_delay = st.toggle("Honor robots.txt Crawl-delay (slower, politer)", value=False, key="honor_delay",
                                help="The site asks for 10 s between requests; when on, the crawler waits up "
                                     "to 1 s. Off matches Screaming Frog's default.")
    busy = running_now or scan_job().running
    settings = ScanSettings(start_url=start_url, include_paths=_folders(only) if scope != "Whole site" else [],
                            exclude_paths=_folders(skip), max_pages=int(max_pages), workers=int(workers),
                            delay=float(delay), respect_robots=respect_robots, honor_crawl_delay=honor_delay)
    known = sum(1 for path in known_page_paths() if settings.reachable(path))
    if known:
        rate = 1.0 if honor_delay else 1 / max(float(delay), 0.05)
        mins = max(1, round(known / rate / 60))
        st.caption(f"About {known:,} known pages to check: roughly {mins} min at up to {rate:g} pages/s "
                   "(new pages found along the way add a little). You can leave this page; the scan keeps "
                   "running in the background.")
    if st.button("Start scan", type="primary", icon=":material/play_arrow:", key="start_scan",
                 disabled=busy or (scope == "Only these folders" and not settings.include_paths)):
        try:
            validate_start_url(settings.start_url)
        except ScanNotAllowed as e:
            st.error(str(e), icon=":material/block:")
            st.stop()
        if prepare_job().start_scan(settings):
            time.sleep(0.5)  # let the job write its first status
            st.rerun()
        else:
            st.error("Another scan is already running.", icon=":material/error:")

with st.expander("More options", icon=":material/more_horiz:"):
    st.markdown("**Update the index from saved pages**")
    st.caption("Use after a cancelled scan: indexes images on the pages it already fetched, without crawling.")
    if st.button("Update index", icon=":material/refresh:", key="reindex", disabled=busy):
        if prepare_job().start_reindex():
            time.sleep(0.5)
            st.rerun()
    st.divider()
    st.markdown("**Import a Screaming Frog export**")
    st.caption("Bulk Export → Links → All Image Inlinks (CSV, or a ZIP of it). Pages already known from "
               "scans are kept; the export only fills in pages they lack.")
    uploads = st.file_uploader("Export files", type=["csv", "zip"], accept_multiple_files=True, key="sf_upload",
                               disabled=busy)
    if uploads and st.button("Import", icon=":material/upload_file:", key="import", disabled=busy):
        DEFAULT_CRAWL_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        paths = []
        for f in uploads:
            path = DEFAULT_CRAWL_DIR / f"{stamp}-{f.name}"
            path.write_bytes(f.getvalue())
            paths.append(path)
        if prepare_job().start_import(paths):
            time.sleep(0.5)
            st.rerun()

history = load_meta(DEFAULT_SCAN_DIR).get("history") or []
if history:
    st.subheader("History")
    st.dataframe(
        [{"Finished": describe_age(h.get("finished_at"))[0],
          "Type": {"scan": "Scan", "screaming_frog": "Screaming Frog import"}.get(h.get("kind"), h.get("kind")),
          "Scope": ", ".join("/" + p for p in h.get("include_paths") or []) or "Whole site",
          "Skipped": ", ".join("/" + p for p in h.get("exclude_paths") or []),
          "Pages": h.get("pages", 0),
          "Complete": bool(h.get("complete"))} for h in reversed(history)],
        width="stretch", hide_index=True, alt="Previous scans and imports, newest first",
    )
