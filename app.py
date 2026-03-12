import streamlit as st
from src.auth import initialize_firebase, sign_in_with_email_and_password
from src.ui_components import render_login, render_platform_selection, render_post_stats, render_comments_stats, render_history
from src.apify_client import fetch_instagram_posts, fetch_instagram_comments, fetch_actor_runs, fetch_dataset_as_dataframe, ACTOR_POST_SCRAPER, ACTOR_COMMENT_SCRAPER
import math

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
                        st.success("Scan completed successfully!")

            # Display Results
            if st.session_state.posts_data:
                render_post_stats(st.session_state.posts_data, st.session_state.scan_cost)

        # --- Page: Comments Scraping ---
        elif page == "Comments Scraping":
            st.header("Comments Scraping")

            if st.session_state.posts_data:
                st.markdown("### Configure Comment Analysis")

                total_comments_available = sum(p.get('commentsCount', 0) for p in st.session_state.posts_data)

                if total_comments_available == 0:
                    st.warning("No comments found in the scanned posts. Scan a profile first in **Post Scraping**.")
                else:
                    st.info(f"**{total_comments_available}** comments available across **{len(st.session_state.posts_data)}** posts.")

                    comments_to_scan = st.number_input(
                        "Number of comments to analyze",
                        min_value=1,
                        max_value=total_comments_available,
                        value=min(100, total_comments_available),
                        step=10,
                    )

                    estimated_cost = (comments_to_scan / 1000) * 4
                    st.write(f"**Estimated Cost:** ${estimated_cost:.4f}")

                    if st.button("Start Analysis"):
                        with st.spinner("Fetching comments..."):
                            target_posts_urls = []
                            accumulated_comments = 0

                            for post in st.session_state.posts_data:
                                if accumulated_comments >= comments_to_scan:
                                    break
                                post_comments = post.get('commentsCount', 0)
                                if post_comments > 0:
                                    target_posts_urls.append(post['url'])
                                    accumulated_comments += post_comments

                            comments, cost = fetch_instagram_comments(target_posts_urls, comments_to_scan)
                            render_comments_stats(comments, cost)
            else:
                st.warning("No posts data available. Go to **Post Scraping** to scan a profile first.")

        # --- Page: Scraping History ---
        elif page == "Scraping History":
            st.header("Scraping History")

            if not st.session_state.get("post_runs") and not st.session_state.get("comment_runs"):
                with st.spinner("Loading run history from Apify..."):
                    st.session_state.post_runs = fetch_actor_runs(ACTOR_POST_SCRAPER)
                    st.session_state.comment_runs = fetch_actor_runs(ACTOR_COMMENT_SCRAPER)

            if st.session_state.get("post_runs") or st.session_state.get("comment_runs"):
                render_history(
                    st.session_state.get("post_runs", []),
                    st.session_state.get("comment_runs", []),
                    fetch_dataset_as_dataframe,
                )


if __name__ == "__main__":
    main()
