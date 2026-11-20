import streamlit as st
from src.auth import initialize_firebase, sign_in_with_email_and_password
from src.ui_components import render_login, render_platform_selection, render_post_stats, render_comments_stats, render_comment_scrape_form, render_history
from src.apify_client import fetch_instagram_posts, fetch_instagram_comments, fetch_actor_runs, fetch_dataset_as_dataframe, fetch_dataset_items, ACTOR_POST_SCRAPER, ACTOR_COMMENT_SCRAPER

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

            if st.session_state.posts_data:
                st.markdown("### Configure Comment Analysis")

                posts = st.session_state.posts_data
                total_comments_available = sum(int(p.get('commentsCount') or 0) for p in posts)

                if total_comments_available == 0:
                    st.warning("No comments found in the scanned posts. Scan a profile first in **Post Scraping**.")
                else:
                    st.info(f"**{total_comments_available}** comments available across **{len(posts)}** posts.")

                    plan = render_comment_scrape_form(posts, key="current_scan")

                    if st.button("Start Analysis"):
                        with st.spinner("Fetching comments..."):
                            st.session_state.comments_result = fetch_instagram_comments(
                                plan["urls"],
                                plan["per_post_limit"],
                                include_replies=plan["include_replies"],
                                max_charge_usd=plan["max_charge_usd"],
                            )
                        st.session_state.history_loaded = False

                    # Kept in session state so results survive reruns (downloads, other widgets).
                    if st.session_state.get("comments_result"):
                        comments, cost = st.session_state.comments_result
                        render_comments_stats(comments, cost, key="current_scan")
            else:
                st.warning("No posts data available. Go to **Post Scraping** to scan a profile first.")

        # --- Page: Scraping History ---
        elif page == "Scraping History":
            st.header("Scraping History")

            col_info, col_refresh = st.columns([4, 1])
            with col_refresh:
                refresh = st.button("🔄 Refresh", use_container_width=True)

            if refresh or not st.session_state.get("history_loaded"):
                with st.spinner("Loading run history from Apify..."):
                    st.session_state.post_runs = fetch_actor_runs(ACTOR_POST_SCRAPER)
                    st.session_state.comment_runs = fetch_actor_runs(ACTOR_COMMENT_SCRAPER)
                st.session_state.history_loaded = True
                st.session_state.history_loaded_at = datetime.now()

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
