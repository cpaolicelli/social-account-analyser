import streamlit as st
from src.auth import initialize_firebase, sign_in_with_email_and_password
from src.ui_components import render_login, render_platform_selection, render_post_stats, render_comments_stats
from src.apify_client import fetch_instagram_posts, fetch_instagram_comments
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
        # Main App Flow
        with st.sidebar:
            st.write(f"Logged in as: {st.session_state.user.get('email', 'User')}")
            if st.button("Logout"):
                st.session_state.user = None
                st.session_state.posts_data = None
                # Clear session cookie
                cookie_manager.delete("user_session")
                st.rerun()

        platform, handle = render_platform_selection()
        
        if platform == "Instagram" and handle:
            if st.button("Scan Profile"):
                with st.spinner("Scanning profile... This may take a while."):
                    posts, cost, run_id = fetch_instagram_posts(handle)
                    st.session_state.posts_data = posts
                    st.session_state.scan_cost = cost
                    st.success("Scan detailed successfully!")

        # Display Results
        if st.session_state.posts_data:
            render_post_stats(st.session_state.posts_data, st.session_state.scan_cost)
            
            st.divider()
            
            # Toxicity Analysis Section (Hidden for now)
            # col1, col2 = st.columns([1, 4])
            # with col1:
            #     if st.button("Start Toxicity Analysis"):
            #         st.session_state.show_toxicity_modal = True

            # if st.session_state.get("show_toxicity_modal"):
            #     with st.container():
            #         st.markdown("### Configure Toxicity Analysis")
                    
            #         # Calculate max comments available
            #         total_comments_available = sum(p.get('commentsCount', 0) for p in st.session_state.posts_data)
                    
            #         if total_comments_available == 0:
            #             st.warning("No comments found to analyze.")
            #         else:
            #             comments_to_scan = st.number_input(
            #                 "Number of comments to analyze", 
            #                 min_value=1, 
            #                 max_value=total_comments_available, 
            #                 value=min(100, total_comments_available),
            #                 step=10
            #             )
                        
            #             estimated_cost = (comments_to_scan / 1000) * 4
            #             st.write(f"**Estimated Cost:** ${estimated_cost:.4f}")
                        
            #             if st.button("Start Analysis"):
            #                 st.session_state.show_toxicity_modal = False # Close modal
            #                 with st.spinner("Fetching comments..."):
            #                     # Logic to select posts until comment limit is reached
            #                     target_posts_urls = []
            #                     accumulated_comments = 0
            #                     
            #                     for post in st.session_state.posts_data:
            #                         if accumulated_comments >= comments_to_scan:
            #                             break
            #                         
            #                         post_comments = post.get('commentsCount', 0)
            #                         if post_comments > 0:
            #                             target_posts_urls.append(post['url'])
            #                             accumulated_comments += post_comments
            #                     
            #                     comments, cost = fetch_instagram_comments(target_posts_urls, comments_to_scan)
            #                     render_comments_stats(comments, cost)


if __name__ == "__main__":
    main()
