import streamlit as st
from src.auth import initialize_firebase, sign_in_with_email_and_password
import hashlib

from src.ui_components import render_login, render_platform_selection, render_post_stats, render_comments_stats, render_comment_scrape_form, render_history, render_post_picker, render_url_comment_form
from src.apify_client import fetch_instagram_posts, fetch_instagram_comments, fetch_actor_runs, fetch_dataset_as_dataframe, fetch_dataset_items, parse_instagram_post_urls, _clean_instagram_handle, ACTOR_POST_SCRAPER, ACTOR_COMMENT_SCRAPER
from src.costs import post_price

import extra_streamlit_components as stx
from datetime import datetime, timedelta

# Page Config
st.set_page_config(page_title="Social Account Analyser", page_icon="🕵️‍♀️", layout="wide")

# Initialize Firebase
initialize_firebase()

# Session State Initialization
if "user" not in st.session_state:
    st.session_state.user = None
if "posts_data" not in st.session_state:
    st.session_state.posts_data = None
if "scan_cost" not in st.session_state:
    st.session_state.scan_cost = 0

SOURCE_CURRENT = "This session's scan"
SOURCE_PAST = "A past scan"
SOURCE_URLS = "Post URLs"


def ensure_history_loaded(force=False):
    """Load post and comment runs from Apify once per session (or when forced)."""
    if force or not st.session_state.get("history_loaded"):
        with st.spinner("Loading run history from Apify..."):
            st.session_state.post_runs = fetch_actor_runs(ACTOR_POST_SCRAPER)
            st.session_state.comment_runs = fetch_actor_runs(ACTOR_COMMENT_SCRAPER)
        st.session_state.history_loaded = True
        st.session_state.history_loaded_at = datetime.now()


def load_run_posts(run):
    """Posts of a past scan, cached per run (shared with the History page)."""
    cache = st.session_state.setdefault("_posts_items_cache", {})
    if run["run_id"] not in cache:
        with st.spinner("Loading posts from this scan..."):
            items = fetch_dataset_items(run["dataset_id"])
        if not items:
            return []
        cache[run["run_id"]] = items
    return cache[run["run_id"]]


def run_label(run):
    started = run.get("started_at")
    if isinstance(started, datetime):
        started = started.strftime("%Y-%m-%d %H:%M")
    else:
        started = str(started or "")[:16].replace("T", " ")
    name = run.get("username") or "Unknown"
    if name != "Unknown" and "post URLs" not in name:
        name = f"@{name}"
    return f"{name} · {started}"


def comment_form(selected_posts, key):
    if not selected_posts:
        st.info("Select at least one post with comments.")
        return None
    st.markdown("**How many comments**")
    return render_comment_scrape_form(selected_posts, key=key)


def main():
    cookie_manager = stx.CookieManager()

    # Try to restore session from cookie if not already in session state
    if not st.session_state.user:
        cookie_user = cookie_manager.get("user_session")
        if cookie_user:
            st.session_state.user = cookie_user

    if not st.session_state.user:
        # Login Flow
        with st.container():
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                if st.secrets.get("firebase", {}).get("web_api_key") == "YOUR_FIREBASE_WEB_API_KEY":
                     st.warning("⚠️ Application is running in MOCK MODE. Use `test@example.com` / `password` to login.")

                submit, email, password = render_login()
                if submit:
                    user = sign_in_with_email_and_password(email, password)
                    if user:
                        st.session_state.user = user
                        # Save session to cookie (expires in 7 days)
                        cookie_manager.set("user_session", user, expires_at=datetime.now() + timedelta(days=7))
                        st.success("Login successful!")
                        st.rerun()
    else:
        # Sidebar Navigation
        if "current_page" not in st.session_state:
            st.session_state.current_page = "Post Scraping"

        with st.sidebar:
            st.markdown(f"**{st.session_state.user.get('email', 'User')}**")
            st.markdown("")
            for item in ["Post Scraping", "Comments Scraping", "Scraping History"]:
                if st.button(item, key=f"nav_{item}", use_container_width=True):
                    st.session_state.current_page = item
                    st.rerun()
            st.markdown("---")
            if st.button("Logout", use_container_width=True):
                st.session_state.user = None
                st.session_state.posts_data = None
                st.session_state.pop("comments_result", None)
                st.session_state.history_loaded = False
                cookie_manager.delete("user_session")
                st.rerun()

        page = st.session_state.current_page

        # --- Page: Post Scraping ---
        if page == "Post Scraping":
            platform, handle, scan_params = render_platform_selection()

            if platform == "Instagram" and handle:
                if st.button("Scan Profile"):
                    with st.spinner("Scanning profile... This may take a while."):
                        posts, cost, run_id = fetch_instagram_posts(handle, scan_params)
                        st.session_state.posts_data = posts
                        st.session_state.scan_cost = cost
                        st.session_state.posts_source = {
                            "label": f"@{_clean_instagram_handle(handle)}",
                            "at": datetime.now(),
                            "run_id": run_id or "session",
                        }
                        # New profile: old comment results no longer apply; history has a new run.
                        st.session_state.pop("comments_result", None)
                        st.session_state.history_loaded = False
                        if run_id:
                            st.success("Scan completed successfully!")

            # Display Results
            if st.session_state.posts_data:
                render_post_stats(st.session_state.posts_data, st.session_state.scan_cost)

        # --- Page: Comments Scraping ---
        elif page == "Comments Scraping":
            st.header("Comments Scraping")

            source = st.radio(
                "Take comments from",
                [SOURCE_CURRENT, SOURCE_PAST, SOURCE_URLS],
                index=0 if st.session_state.posts_data else 1,
                horizontal=True,
                key="comments_source",
            )
            plan, source_label = None, ""

            if source == SOURCE_CURRENT:
                posts = st.session_state.posts_data
                meta = st.session_state.get("posts_source") or {}
                if not posts:
                    st.info("No profile scanned in this session yet. Scan one in **Post Scraping**, or pick a past scan or paste post URLs.")
                else:
                    when = f" at {meta['at']:%H:%M}" if meta.get("at") else ""
                    st.caption(f"Posts from your scan of **{meta.get('label', 'this session')}**{when} · {len(posts)} posts")
                    source_label = f"scan of {meta.get('label', 'this session')}{when}"
                    selected = render_post_picker(posts, key=f"current_{meta.get('run_id', 'session')}")
                    plan = comment_form(selected, key="current_scan")

            elif source == SOURCE_PAST:
                ensure_history_loaded()
                runs = [r for r in st.session_state.get("post_runs") or [] if r["status"] == "SUCCEEDED" and r["dataset_id"]]
                if not runs:
                    st.info("No successful post scans found in Apify.")
                else:
                    by_id = {r["run_id"]: r for r in runs}
                    run_id = st.selectbox("Past scan", list(by_id), format_func=lambda rid: run_label(by_id[rid]), key="comments_past_run")
                    run = by_id[run_id]
                    posts = load_run_posts(run)
                    if not posts:
                        st.warning("Couldn't load the posts of this scan.")
                    else:
                        source_label = f"past scan {run_label(run)}"
                        selected = render_post_picker(posts, key=f"hist_{run_id}", page_fallback=run.get("username"))
                        plan = comment_form(selected, key=run_id)

            else:
                text = st.text_area(
                    "Instagram post or reel URLs, one per line",
                    key="comments_urls",
                    placeholder="https://www.instagram.com/p/…",
                )
                urls, invalid = parse_instagram_post_urls(text)
                if invalid:
                    st.warning("Not post URLs, ignored: " + ", ".join(invalid[:5]) + (" …" if len(invalid) > 5 else ""))
                if urls:
                    source_label = f"{len(urls)} pasted post URL" + ("s" if len(urls) > 1 else "")
                    url_key = hashlib.sha1("|".join(urls).encode()).hexdigest()[:10]
                    previews = st.session_state.setdefault("_url_previews", {})
                    if url_key in previews:
                        selected = render_post_picker(previews[url_key], key=f"urls_{url_key}")
                        plan = comment_form(selected, key=f"urls_{url_key}")
                    else:
                        st.markdown("\n".join(f"- [{u}]({u})" for u in urls))
                        preview_cost = len(urls) * post_price(detailed=False)
                        if st.button(f"Load previews (≈ \\${preview_cost:.4f})", help="Scrapes these posts to show image, caption, page and comment counts."):
                            with st.spinner("Loading post previews..."):
                                preview_posts, _, _ = fetch_instagram_posts(urls, {"dataDetailLevel": "basicData"})
                            st.session_state.history_loaded = False
                            if preview_posts:
                                previews[url_key] = preview_posts
                                st.rerun()
                            st.warning("No previews returned; you can still scrape comments directly.")
                        st.markdown("**How many comments**")
                        plan = render_url_comment_form(urls, key="urls")

            if plan and plan["urls"]:
                if st.button("Start Analysis", type="primary"):
                    with st.spinner("Fetching comments..."):
                        result = fetch_instagram_comments(
                            plan["urls"],
                            plan["per_post_limit"],
                            include_replies=plan["include_replies"],
                            max_charge_usd=plan["max_charge_usd"],
                        )
                    st.session_state.comments_result = {"data": result, "source": source_label}
                    st.session_state.history_loaded = False

            # Kept in session state so results survive reruns (downloads, other widgets).
            last = st.session_state.get("comments_result")
            if last:
                st.divider()
                st.caption(f"Last analysis: comments from {last['source']}")
                comments, cost = last["data"]
                render_comments_stats(comments, cost, key="current_scan")

        # --- Page: Scraping History ---
        elif page == "Scraping History":
            st.header("Scraping History")

            col_info, col_refresh = st.columns([4, 1])
            with col_refresh:
                refresh = st.button("🔄 Refresh", use_container_width=True)

            ensure_history_loaded(force=refresh)

            with col_info:
                if st.session_state.get("history_loaded_at"):
                    st.caption(f"Last updated {st.session_state.history_loaded_at:%H:%M:%S}")

            render_history(
                st.session_state.get("post_runs", []),
                st.session_state.get("comment_runs", []),
                fetch_dataset_as_dataframe,
                fetch_dataset_items,
                fetch_instagram_comments,
            )


if __name__ == "__main__":
    main()
