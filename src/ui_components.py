import streamlit as st
import pandas as pd
import json
import io
from datetime import date, timedelta

from src.costs import comment_price, plan_comment_scrape, post_price

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
    # --- Estimated cost (live Apify prices) ---
    cost_basic = post_price(detailed=False)
    cost_addon = post_price(detailed=True) - cost_basic
    st.caption(
        "**Basic** data is faster and cheaper. "
        "**Detailed** data adds fields like alt text, latest comments, music info, "
        "paid partnership, and video play count. "
        f"Please note that this will increase the cost per post by **${cost_addon:.4f}/post**."
    )

    cost_per_post = cost_basic + (cost_addon if detail_level == "detailed" else 0)
    estimated_cost = cost_per_post * results_limit

    st.markdown("---")
    col_cost1, col_cost2 = st.columns(2)
    with col_cost1:
        st.metric("Cost per post", f"${cost_per_post:.4f}")
    with col_cost2:
        st.metric("Estimated total cost", f"${estimated_cost:.4f}")
    if detail_level == "detailed":
        st.caption(f"Base: ${cost_basic:.4f}/post + Add-on: ${cost_addon:.4f}/post = ${cost_per_post:.4f}/post × {results_limit} posts")
    else:
        st.caption(f"Base: ${cost_basic:.4f}/post × {results_limit} posts")

    scan_params = {
        "onlyPostsNewerThan": posts_newer_than,
        "resultsLimit": results_limit,
        "skipPinnedPosts": skip_pinned,
        "dataDetailLevel": "detailedData" if detail_level == "detailed" else "basicData",
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

def render_comment_scrape_form(posts, key):
    """Inputs for a comment scrape over `posts`.

    Returns the plan for fetch_instagram_comments, or None if no post has comments.
    """
    total_available = sum(int(p.get("commentsCount") or 0) for p in posts)
    if total_available == 0:
        return None

    target_key = f"comments_target_{key}"
    if target_key in st.session_state:
        # Keep a previous value inside the range when the post selection shrinks.
        st.session_state[target_key] = max(1, min(int(st.session_state[target_key]), total_available))
        default = {}
    else:
        default = {"value": min(100, total_available)}

    target = st.number_input(
        "Number of comments to scrape",
        min_value=1,
        max_value=total_available,
        step=10,
        key=target_key,
        **default,
    )
    include_replies = st.checkbox(
        "Include replies",
        value=True,
        key=f"include_replies_{key}",
        help="Each reply is charged like a comment and counts toward the same budget.",
    )

    urls, per_post_limit, expected = plan_comment_scrape(posts, target)
    price = comment_price()
    budget = target * price

    st.write(f"**Plan:** up to {per_post_limit} comments from each of {len(urls)} posts (≈ {expected} comments)")
    st.write(f"**Estimated cost:** ${expected * price:.4f} · **Hard cap:** ${budget:.4f}")
    st.caption(
        f"Apify stops the run once it has charged ${budget:.4f} ({target} results"
        + (", replies included)." if include_replies else ").")
    )

    return {
        "urls": urls,
        "per_post_limit": per_post_limit,
        "include_replies": include_replies,
        "max_charge_usd": budget,
    }


def render_comments_stats(comments, cost, key="comments"):
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
            mime="application/json",
            key=f"download_{key}",
            on_click="ignore",
        )
        
        with st.expander("View Comments Data"):
            st.dataframe(pd.DataFrame(comments))
    
    # Placeholder for actual toxicity analysis
    st.info("Toxicity integration pending...")


def render_history(post_runs, comment_runs, fetch_dataset_fn, fetch_items_fn, fetch_comments_fn):
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
        _render_run_list(post_runs, "posts", fetch_dataset_fn, fetch_items_fn, fetch_comments_fn)

    with tab_comments:
        _render_run_list(comment_runs, "comments", fetch_dataset_fn)


def _render_run_list(runs, run_type, fetch_dataset_fn, fetch_items_fn=None, fetch_comments_fn=None):
    if not runs:
        st.info(f"No {run_type} scraping history found.")
        return

    # Session state key for cached CSV data
    cache_key = f"_csv_cache_{run_type}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = {}

    # Session state key for cached post items (for comment scraping)
    posts_cache_key = "_posts_items_cache"
    if posts_cache_key not in st.session_state:
        st.session_state[posts_cache_key] = {}

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
                    if "dataDetailLevel" in run_input:
                        st.write(f"- Detail level: {run_input['dataDetailLevel']}")
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

            # --- Comment scraping from post runs ---
            if run_type == "posts" and status == "SUCCEEDED" and fetch_items_fn and fetch_comments_fn:
                run_id = run["run_id"]
                st.divider()

                # Load posts button
                if run_id not in st.session_state[posts_cache_key]:
                    if st.button("Load Posts for Comment Scraping", key=f"load_posts_{run_id}_{i}"):
                        with st.spinner("Loading posts from this run..."):
                            items = fetch_items_fn(run["dataset_id"])
                            if items:
                                st.session_state[posts_cache_key][run_id] = items
                                st.rerun()
                            else:
                                st.warning("No posts found in this dataset.")
                else:
                    posts = st.session_state[posts_cache_key][run_id]
                    total_comments = sum(p.get("commentsCount", 0) for p in posts)

                    st.markdown(f"**{len(posts)} posts** — {total_comments} total comments available")

                    # Select all / deselect all
                    select_all_key = f"select_all_{run_id}"

                    def _toggle_all(rid=run_id, n=len(posts)):
                        val = st.session_state[f"select_all_{rid}"]
                        for idx in range(n):
                            st.session_state[f"post_cb_{rid}_{idx}"] = val

                    select_all = st.checkbox("Select all", value=True, key=select_all_key, on_change=_toggle_all)

                    # Render each post with checkbox
                    selected_posts = []
                    selected_comments_count = 0
                    for j, post in enumerate(posts):
                        post_url = post.get("url", "")
                        shortcode = post.get("shortCode", post_url.split("/")[-2] if "/p/" in post_url else "")
                        likes = post.get("likesCount", 0)
                        comments = post.get("commentsCount", 0)
                        caption = post.get("caption", "") or ""
                        caption_preview = (caption[:60] + "...") if len(caption) > 60 else caption

                        cb_key = f"post_cb_{run_id}_{j}"
                        if cb_key not in st.session_state:
                            st.session_state[cb_key] = True

                        checked = st.checkbox(
                            f"📷 `{shortcode}` — ❤️ {likes} — 💬 {comments} — {caption_preview}",
                            key=cb_key,
                        )
                        if checked and post_url and comments > 0:
                            selected_posts.append(post)
                            selected_comments_count += comments

                    if selected_posts:
                        st.markdown(f"**Selected:** {len(selected_posts)} posts with {selected_comments_count} comments")

                        plan = render_comment_scrape_form(selected_posts, key=run_id)
                        results = st.session_state.setdefault("_history_comment_results", {})

                        if st.button("Scrape Comments", key=f"scrape_comments_{run_id}_{i}", type="primary"):
                            with st.spinner("Scraping comments..."):
                                results[run_id] = fetch_comments_fn(
                                    plan["urls"],
                                    plan["per_post_limit"],
                                    include_replies=plan["include_replies"],
                                    max_charge_usd=plan["max_charge_usd"],
                                )

                        if run_id in results:
                            comments_data, cost = results[run_id]
                            if comments_data:
                                render_comments_stats(comments_data, cost, key=f"history_{run_id}")
                            else:
                                st.warning("No comments were returned.")
                    else:
                        st.info("Select at least one post with comments to scrape.")
