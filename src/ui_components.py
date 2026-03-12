import streamlit as st
import pandas as pd
import json
import io
from datetime import date, timedelta

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
        return None, None, {}

    handle = st.text_input("Enter Instagram Profile URL (e.g., https://www.instagram.com/ktm_official/)")

    st.subheader("Scan Parameters")

    # --- Posts newer than ---
    date_mode = st.radio(
        "Extract posts that are newer than",
        ["Relative", "Absolute"],
        horizontal=True,
    )

    if date_mode == "Absolute":
        selected_date = st.date_input(
            "Select a date",
            value=date.today() - timedelta(days=31),
            max_value=date.today(),
        )
        posts_newer_than = selected_date.strftime("%Y-%m-%d")
    else:
        col_qty, col_unit = st.columns(2)
        with col_qty:
            qty = st.number_input("Quantity", min_value=1, value=31, step=1)
        with col_unit:
            unit = st.selectbox("Unit", ["days", "weeks", "months", "years"])
        posts_newer_than = f"{qty} {unit}"

    # --- Results limit ---
    results_limit = st.number_input(
        "Maximum posts to scan",
        min_value=1,
        max_value=5000,
        value=100,
        step=50,
    )

    # --- Skip pinned posts ---
    skip_pinned = st.checkbox("Skip pinned posts", value=False)

    # --- Data detail level ---
    detail_level = st.radio(
        "How detailed do you want the data?",
        ["basic", "detailed"],
        horizontal=True,
    )
    st.caption(
        "**Basic** data is faster and cheaper. "
        "**Detailed** data adds fields like alt text, latest comments, music info, "
        "paid partnership, and video play count. "
        "Please note that this will increase the cost per post by **$0.0007/post**."
    )

    # --- Estimated cost ---
    COST_BASIC = 0.0013
    COST_ADDON = 0.0007
    cost_per_post = COST_BASIC + (COST_ADDON if detail_level == "detailed" else 0)
    estimated_cost = cost_per_post * results_limit

    st.markdown("---")
    col_cost1, col_cost2 = st.columns(2)
    with col_cost1:
        st.metric("Cost per post", f"${cost_per_post:.4f}")
    with col_cost2:
        st.metric("Estimated total cost", f"${estimated_cost:.4f}")
    if detail_level == "detailed":
        st.caption(f"Base: ${COST_BASIC}/post + Add-on: ${COST_ADDON}/post = ${cost_per_post}/post × {results_limit} posts")
    else:
        st.caption(f"Base: ${COST_BASIC}/post × {results_limit} posts")

    scan_params = {
        "onlyPostsNewerThan": posts_newer_than,
        "resultsLimit": results_limit,
        "skipPinnedPosts": skip_pinned,
        "expandOwner": detail_level == "detailed",
    }

    return platform, handle, scan_params

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


def render_history(post_runs, comment_runs, fetch_dataset_fn):
    """Render the scraping history in the main content area."""

    # Inject CSS for colored run cards
    st.markdown("""
    <style>
        .run-card {
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 2px;
            font-size: 14px;
            line-height: 1.4;
        }
        .run-card .run-username {
            font-weight: 700;
            font-size: 15px;
        }
        .run-card .run-meta {
            opacity: 0.85;
            font-size: 13px;
        }
        .run-success {
            border: 2px solid #2e7d32;
            background-color: #e8f5e9;
            color: #1b5e20;
        }
        .run-failed {
            border: 2px solid #c62828;
            background-color: #ffebee;
            color: #b71c1c;
        }
        .run-running {
            border: 2px solid #f57f17;
            background-color: #fff8e1;
            color: #e65100;
        }
    </style>
    """, unsafe_allow_html=True)

    tab_posts, tab_comments = st.tabs(["Posts Scraping", "Comments Scraping"])

    with tab_posts:
        _render_run_list(post_runs, "posts", fetch_dataset_fn)

    with tab_comments:
        _render_run_list(comment_runs, "comments", fetch_dataset_fn)


def _render_run_list(runs, run_type, fetch_dataset_fn):
    if not runs:
        st.info(f"No {run_type} scraping history found.")
        return

    # Session state key for cached CSV data
    cache_key = f"_csv_cache_{run_type}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = {}

    for i, run in enumerate(runs):
        started = run["started_at"]
        if started:
            try:
                dt = pd.to_datetime(started)
                started = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        status = run["status"]
        if status == "SUCCEEDED":
            card_class = "run-success"
        elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
            card_class = "run-failed"
        else:
            card_class = "run-running"

        st.markdown(f"""
        <div class="run-card {card_class}">
            <span class="run-username">{run['username']}</span><br>
            <span class="run-meta">{started} — {status} — ${run['cost_usd']:.4f}</span>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("Details", expanded=False):
            col_detail, col_download = st.columns([3, 1])
            with col_detail:
                st.caption(f"Run ID: `{run['run_id'][:12]}...`")
                st.write(f"**Status:** {status}")
                st.write(f"**Started:** {started}")
                if run["finished_at"]:
                    finished = run["finished_at"]
                    try:
                        finished = pd.to_datetime(finished).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                    st.write(f"**Finished:** {finished}")

                # Show input details
                run_input = run.get("run_input", {})
                if run_input:
                    st.markdown("**Input config:**")
                    if "onlyPostsNewerThan" in run_input:
                        st.write(f"- Period: {run_input['onlyPostsNewerThan']}")
                    if "resultsLimit" in run_input:
                        st.write(f"- Results limit: {run_input['resultsLimit']}")
                    if "skipPinnedPosts" in run_input:
                        st.write(f"- Skip pinned: {run_input['skipPinnedPosts']}")
                    if "directUrls" in run_input:
                        st.write(f"- URLs scraped: {len(run_input['directUrls'])}")
                    if "includeNestedComments" in run_input:
                        st.write(f"- Nested comments: {run_input['includeNestedComments']}")

            with col_download:
                # CSV download
                if run["dataset_id"] and status == "SUCCEEDED":
                    run_id = run["run_id"]
                    if run_id in st.session_state[cache_key]:
                        csv_data = st.session_state[cache_key][run_id]
                        st.download_button(
                            label="Save CSV",
                            data=csv_data,
                            file_name=f"{run_type}_{run['username']}_{run_id[:8]}.csv",
                            mime="text/csv",
                            key=f"save_{run_type}_{run_id}_{i}",
                        )
                    else:
                        if st.button("Prepare CSV", key=f"prep_{run_type}_{run_id}_{i}"):
                            with st.spinner("Fetching dataset..."):
                                df = fetch_dataset_fn(run["dataset_id"])
                                if not df.empty:
                                    csv_buffer = io.StringIO()
                                    df.to_csv(csv_buffer, index=False)
                                    st.session_state[cache_key][run_id] = csv_buffer.getvalue()
                                    st.rerun()
                                else:
                                    st.warning("Dataset is empty.")
