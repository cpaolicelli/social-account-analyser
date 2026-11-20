import streamlit as st
from src.auth import initialize_firebase, sign_in_with_email_and_password
import hashlib

from src.ui_components import render_login, render_platform_selection, render_post_stats, render_comments_stats, render_comment_scrape_form, render_history, render_post_picker, render_url_comment_form, run_profiles_label, post_owners, profile_line_html, AVATAR_CSS
from src.apify_client import fetch_instagram_posts, fetch_instagram_comments, fetch_actor_runs, fetch_dataset_as_dataframe, fetch_dataset_items, parse_instagram_post_urls, ACTOR_POST_SCRAPER, ACTOR_COMMENT_SCRAPER
from src.costs import ACTOR_PROFILE_SCRAPER, get_api_token, min_charge_usd, post_price, profile_price
from src import profiles as prof

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
            post_runs = fetch_actor_runs(ACTOR_POST_SCRAPER)
            comment_runs = fetch_actor_runs(ACTOR_COMMENT_SCRAPER)
            try:
                prof.resolve_run_profiles(get_api_token(), post_runs, comment_runs)
            except Exception as e:
                print(f"Could not resolve run profiles: {e}")
            st.session_state.post_runs = post_runs
            st.session_state.comment_runs = comment_runs
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
    return f"{run_profiles_label(run)} · {started}"


def profile_infos(usernames):
    """Saved profile info (name, picture) for these usernames; never scrapes."""
    return prof.get_cached(get_api_token(), usernames)


def save_profiles(usernames):
    """Scrape and save the profiles not saved yet (small cost, once per profile)."""
    token = get_api_token()
    missing = prof.missing_profiles(token, usernames)
    if not missing:
        return 0
    cap = max(len(missing) * profile_price(), min_charge_usd(ACTOR_PROFILE_SCRAPER) or 0)
    try:
        saved, _ = prof.fetch_profiles(token, missing, cap)
        return saved
    except Exception as e:
        st.warning(f"Couldn't save profile pictures: {e}")
        return 0


def render_profile_completion(all_runs, missing, unknown_urls):
    """Offer to fetch missing profile pictures and identify posts of unknown profiles."""
    estimate = len(missing) * profile_price() + len(unknown_urls) * (post_price(detailed=False) + profile_price())
    parts = []
    if missing:
        parts.append(f"{len(missing)} profile{'s' if len(missing) != 1 else ''} without a picture")
    if unknown_urls:
        parts.append(f"{len(unknown_urls)} post{'s' if len(unknown_urls) != 1 else ''} whose profile is unknown")
    with st.container(border=True):
        st.markdown("**Complete profile info:** " + " · ".join(parts))
        st.caption("Names and pictures are saved once in your Apify account and reused by every scan.")
        if st.button(f"Complete profiles (≈ \\${estimate:.4f})", key="complete_profiles"):
            token = get_api_token()
            with st.spinner("Completing profile info..."):
                try:
                    if unknown_urls:
                        cap = max(len(unknown_urls) * post_price(detailed=False), min_charge_usd(ACTOR_POST_SCRAPER) or 0)
                        owners = prof.identify_post_owners(token, unknown_urls, cap)
                        prof.apply_identified_owners(token, all_runs, owners)
                    names = list(dict.fromkeys(u for r in all_runs for u in r.get("profiles") or []))
                    still_missing = prof.missing_profiles(token, names)
                    if still_missing:
                        cap = max(len(still_missing) * profile_price(), min_charge_usd(ACTOR_PROFILE_SCRAPER) or 0)
                        prof.fetch_profiles(token, still_missing, cap)
                except Exception as e:
                    st.error(f"Couldn't complete profile info: {e}")
                    return
            st.rerun()


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
                username = prof.normalize_username(handle)
                needs_profile = bool(username) and bool(prof.missing_profiles(get_api_token(), [username]))
                if needs_profile:
                    st.caption(f"+ \\${profile_price():.4f} once, to save @{username}'s name and profile picture")
                if st.button("Scan Profile"):
                    with st.spinner("Scanning profile... This may take a while."):
                        posts, cost, run_id = fetch_instagram_posts(handle, scan_params)
                        st.session_state.posts_data = posts
                        st.session_state.scan_cost = cost
                        st.session_state.posts_source = {
                            "username": username,
                            "label": f"@{username}" if username else handle,
                            "at": datetime.now(),
                            "run_id": run_id or "session",
                        }
                        # New profile: old comment results no longer apply; history has a new run.
                        st.session_state.pop("comments_result", None)
                        st.session_state.history_loaded = False
                        if run_id:
                            if needs_profile:
                                save_profiles([username])
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
            plan, source_label, plan_owners = None, "", []

            if source == SOURCE_CURRENT:
                posts = st.session_state.posts_data
                meta = st.session_state.get("posts_source") or {}
                if not posts:
                    st.info("No profile scanned in this session yet. Scan one in **Post Scraping**, or pick a past scan or paste post URLs.")
                else:
                    when = f" at {meta['at']:%H:%M}" if meta.get("at") else ""
                    owners = post_owners(posts) or ([meta["username"]] if meta.get("username") else [])
                    infos = profile_infos(owners)
                    if meta.get("username"):
                        st.markdown(AVATAR_CSS + profile_line_html(meta["username"], infos.get(meta["username"]), size=36), unsafe_allow_html=True)
                    st.caption(f"Posts from your scan of **{meta.get('label', 'this session')}**{when} · {len(posts)} posts")
                    source_label = f"scan of {meta.get('label', 'this session')}{when}"
                    selected = render_post_picker(posts, key=f"current_{meta.get('run_id', 'session')}", page_fallback=meta.get("username"), profiles=infos)
                    plan_owners = post_owners(selected) or owners
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
                        owners = list(dict.fromkeys(post_owners(posts) + (run.get("profiles") or [])))
                        selected = render_post_picker(
                            posts, key=f"hist_{run_id}",
                            page_fallback=(run.get("profiles") or [None])[0],
                            profiles=profile_infos(owners),
                        )
                        plan_owners = post_owners(selected) or run.get("profiles") or []
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
                        preview_posts = previews[url_key]
                        selected = render_post_picker(preview_posts, key=f"urls_{url_key}", profiles=profile_infos(post_owners(preview_posts)))
                        plan_owners = post_owners(selected)
                        plan = comment_form(selected, key=f"urls_{url_key}")
                    else:
                        st.markdown("\n".join(f"- [{u}]({u})" for u in urls))
                        # Upper bound: every post by a different profile not saved yet.
                        preview_cost = len(urls) * (post_price(detailed=False) + profile_price())
                        if st.button(f"Load previews (≈ \\${preview_cost:.4f})", help="Scrapes these posts to show image, caption, page and comment counts, and saves the authors' profile pictures."):
                            with st.spinner("Loading post previews..."):
                                preview_posts, _, _ = fetch_instagram_posts(urls, {"dataDetailLevel": "basicData"})
                                if preview_posts:
                                    save_profiles(post_owners(preview_posts))
                            st.session_state.history_loaded = False
                            if preview_posts:
                                previews[url_key] = preview_posts
                                st.rerun()
                            st.warning("No previews returned; you can still scrape comments directly.")
                        st.markdown("**How many comments**")
                        plan = render_url_comment_form(urls, key="urls")
                        derivable = [prof.owner_from_url(u) for u in urls]
                        plan_owners = list(dict.fromkeys(derivable)) if all(derivable) else []

            if plan and plan["urls"]:
                if st.button("Start Analysis", type="primary"):
                    with st.spinner("Fetching comments..."):
                        result = fetch_instagram_comments(
                            plan["urls"],
                            plan["per_post_limit"],
                            include_replies=plan["include_replies"],
                            max_charge_usd=plan["max_charge_usd"],
                            meta={"profiles": plan_owners} if plan_owners else None,
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

            post_runs = st.session_state.get("post_runs", [])
            comment_runs = st.session_state.get("comment_runs", [])
            all_runs = post_runs + comment_runs
            usernames = list(dict.fromkeys(u for r in all_runs for u in r.get("profiles") or []))
            infos = profile_infos(usernames)
            missing = [u for u in usernames if infos.get(u) is None]
            unknown_urls = list({prof.shortcode(u) or u: u for r in all_runs for u in r.get("unknown_urls") or []}.values())
            if missing or unknown_urls:
                render_profile_completion(all_runs, missing, unknown_urls)

            render_history(
                post_runs,
                comment_runs,
                fetch_dataset_as_dataframe,
                fetch_dataset_items,
                fetch_instagram_comments,
                profiles=infos,
            )


if __name__ == "__main__":
    main()
