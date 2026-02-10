import streamlit as st
import pandas as pd
import json

def render_login():
    st.title("Login")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Sign In")
        return submit, email, password

def render_platform_selection():
    st.header("Social Media Scanner")
    platform = st.selectbox("Select Platform", ["Instagram", "TikTok"])
    
    if platform == "TikTok":
        st.warning("TikTok is currently disabled.")
        return None, None
    
    handle = st.text_input("Enter Instagram Profile URL (e.g., https://www.instagram.com/ktm_official/)")
    return platform, handle

def render_post_stats(posts, cost):
    if not posts:
        st.warning("No posts found.")
        return

    df = pd.DataFrame(posts)
    
    # Check for keys, some posts might be missing fields
    comments_count = df['commentsCount'].sum() if 'commentsCount' in df.columns else 0
    likes_count = df['likesCount'].sum() if 'likesCount' in df.columns else 0
    total_posts = len(posts)
    
    avg_comments = comments_count / total_posts if total_posts > 0 else 0
    avg_likes = likes_count / total_posts if total_posts > 0 else 0
    
    st.subheader("Scan Results")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Posts", total_posts)
    col1.metric("Run Cost", f"${cost:.4f}")
    
    col2.metric("Total Comments", comments_count)
    col2.metric("Avg Comments/Post", f"{avg_comments:.2f}")
    
    col3.metric("Total Likes", likes_count)
    col3.metric("Avg Likes/Post", f"{avg_likes:.2f}")

    st.subheader("Raw Data Preview")
    st.dataframe(df)

def render_comments_stats(comments, cost):
    st.subheader("Analysis Results")
    st.write(f"Total Comments Scanned: {len(comments)}")
    st.write(f"Run Cost: ${cost:.4f}")
    
    if comments:
        # Convert to JSON string
        comments_json = json.dumps(comments, indent=2)
        
        st.download_button(
            label="Download Comments JSON",
            data=comments_json,
            file_name="comments_data.json",
            mime="application/json"
        )
        
        with st.expander("View Comments Data"):
            st.dataframe(pd.DataFrame(comments))
    
    # Placeholder for actual toxicity analysis
    st.info("Toxicity integration pending...")
