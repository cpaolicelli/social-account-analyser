from apify_client import ApifyClient
import streamlit as st
import pandas as pd
from datetime import datetime

ACTOR_POST_SCRAPER = "apify/instagram-post-scraper"
ACTOR_COMMENT_SCRAPER = "apify/instagram-comment-scraper"

def get_apify_client():
    token = st.secrets.get("apify", {}).get("api_token")
    if not token or token == "YOUR_APIFY_API_TOKEN":
        st.error("Apify API token not configured.")
        return None
    return ApifyClient(token)

def _get_run_cost(client, run_id):
    try:
        run_details = client.run(run_id).get()
        # Cost might be in 'usageTotalUsd' or inside 'stats'
        cost = run_details.get("usageTotalUsd")
        if cost is None:
            stats = run_details.get("stats", {})
            cost = stats.get("totalCostJs", 0) # Some actors use this
            if not cost:
                cost = stats.get("cost", 0)
        return float(cost) if cost else 0.0
    except Exception as e:
        print(f"Error fetching cost: {e}")
        return 0.0

def fetch_instagram_posts(username_url, scan_params=None):
    client = get_apify_client()
    if not client:
        return [], 0, None

    if scan_params is None:
        scan_params = {}

    run_input = {
        "onlyPostsNewerThan": scan_params.get("onlyPostsNewerThan", "31 days"),
        "resultsLimit": scan_params.get("resultsLimit", 100),
        "skipPinnedPosts": scan_params.get("skipPinnedPosts", False),
        "username": [username_url],
    }

    try:
        run = client.actor(ACTOR_POST_SCRAPER).call(run_input=run_input)
        dataset_items = client.dataset(run["defaultDatasetId"]).list_items().items

        cost = _get_run_cost(client, run["id"])

        return dataset_items, cost, run["id"]
    except Exception as e:
        st.error(f"Error fetching posts: {str(e)}")
        return [], 0, None

def fetch_instagram_comments(post_urls, limit):
    client = get_apify_client()
    if not client:
        return []

    run_input = {
        "directUrls": post_urls,
        "includeNestedComments": True,
        "isNewestComments": False,
        "resultsLimit": limit
    }

    try:
        run = client.actor("apify/instagram-comment-scraper").call(run_input=run_input)
        dataset_items = client.dataset(run["defaultDatasetId"]).list_items().items
         
        cost = _get_run_cost(client, run["id"])

        return dataset_items, cost
    except Exception as e:
        st.error(f"Error fetching comments: {str(e)}")
        return [], 0


def _clean_instagram_handle(url_or_handle):
    """Extract clean Instagram handle from a URL or handle string."""
    handle = url_or_handle.strip().rstrip("/")
    handle = handle.replace("https://www.instagram.com/", "")
    handle = handle.replace("http://www.instagram.com/", "")
    handle = handle.replace("https://instagram.com/", "")
    # Remove any remaining path segments (e.g., /p/xxx)
    if "/" in handle:
        handle = handle.split("/")[0]
    return handle or url_or_handle


def _extract_run_details(client, run):
    """Extract Instagram username and input config from a run."""
    try:
        run_detail = client.run(run["id"]).get()
        kvs_id = run_detail.get("defaultKeyValueStoreId")
        run_input = {}
        if kvs_id:
            record = client.key_value_store(kvs_id).get_record("INPUT")
            if record:
                run_input = record.get("value", {}) or {}

        # Post scraper uses "username" list, comment scraper uses "directUrls"
        usernames = run_input.get("username", [])
        if usernames:
            clean_names = [_clean_instagram_handle(u) for u in usernames]
            return ", ".join(clean_names), run_input

        direct_urls = run_input.get("directUrls", [])
        if direct_urls:
            return f"{len(direct_urls)} post URLs", run_input

        return "Unknown", run_input
    except Exception:
        return "Unknown", {}


def fetch_actor_runs(actor_id, limit=50):
    """Fetch recent runs for a given Apify actor."""
    client = get_apify_client()
    if not client:
        return []

    try:
        runs_list = client.actor(actor_id).runs().list(limit=limit, desc=True)
        runs = runs_list.items if runs_list else []

        history = []
        for run in runs:
            status = run.get("status", "UNKNOWN")
            started = run.get("startedAt", "")
            finished = run.get("finishedAt", "")
            cost = run.get("usageTotalUsd", 0) or 0
            dataset_id = run.get("defaultDatasetId", "")
            run_id = run.get("id", "")

            username, run_input = _extract_run_details(client, run)

            history.append({
                "run_id": run_id,
                "username": username,
                "status": status,
                "started_at": started,
                "finished_at": finished,
                "cost_usd": float(cost),
                "dataset_id": dataset_id,
                "run_input": run_input,
            })

        return history
    except Exception as e:
        st.error(f"Error fetching run history: {str(e)}")
        return []


def fetch_dataset_as_dataframe(dataset_id):
    """Fetch all items from an Apify dataset and return as a DataFrame."""
    client = get_apify_client()
    if not client:
        return pd.DataFrame()

    try:
        items = client.dataset(dataset_id).list_items().items
        if items:
            return pd.DataFrame(items)
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Error fetching dataset: {str(e)}")
        return pd.DataFrame()
