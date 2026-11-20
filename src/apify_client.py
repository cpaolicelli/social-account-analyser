from concurrent.futures import ThreadPoolExecutor

from apify_client import ApifyClient
import streamlit as st
import pandas as pd

from src.costs import ACTOR_COMMENT_SCRAPER, ACTOR_POST_SCRAPER, get_api_token, to_charge_limit


def get_apify_client():
    token = get_api_token()
    if not token:
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
        "dataDetailLevel": scan_params.get("dataDetailLevel", "basicData"),
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

def fetch_instagram_comments(post_urls, per_post_limit, include_replies=True, max_charge_usd=None):
    """Scrape comments from post URLs.

    per_post_limit is applied to each URL by the actor (see costs.plan_comment_scrape).
    max_charge_usd is a hard spending cap enforced by Apify; replies count toward it.
    """
    client = get_apify_client()
    if not client:
        return [], 0

    run_input = {
        "directUrls": post_urls,
        "includeNestedComments": include_replies,
        "resultsLimit": per_post_limit,
    }

    try:
        run = client.actor(ACTOR_COMMENT_SCRAPER).call(
            run_input=run_input,
            max_total_charge_usd=to_charge_limit(max_charge_usd) if max_charge_usd else None,
        )
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


def _extract_run_details(token, run):
    """Extract Instagram username and input config from a run.

    Runs in a worker thread, so it builds its own client and must not call st.*.
    """
    try:
        client = ApifyClient(token)
        # The runs list already carries the store ID; only fall back to a full fetch if not.
        kvs_id = run.get("defaultKeyValueStoreId")
        if not kvs_id:
            kvs_id = (client.run(run["id"]).get() or {}).get("defaultKeyValueStoreId")
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

        # Each run's input lives in its own key-value store: fetch them in parallel.
        token = get_api_token()
        with ThreadPoolExecutor(max_workers=8) as pool:
            details = list(pool.map(lambda r: _extract_run_details(token, r), runs))

        history = []
        for run, (username, run_input) in zip(runs, details):
            history.append({
                "run_id": run.get("id", ""),
                "username": username,
                "status": run.get("status", "UNKNOWN"),
                "started_at": run.get("startedAt", ""),
                "finished_at": run.get("finishedAt", ""),
                "cost_usd": float(run.get("usageTotalUsd", 0) or 0),
                "dataset_id": run.get("defaultDatasetId", ""),
                "run_input": run_input,
            })

        return history
    except Exception as e:
        st.error(f"Error fetching run history: {str(e)}")
        return []


def fetch_dataset_items(dataset_id):
    """Fetch all items from an Apify dataset and return as a list."""
    client = get_apify_client()
    if not client:
        return []
    try:
        return client.dataset(dataset_id).list_items().items or []
    except Exception as e:
        st.error(f"Error fetching dataset: {str(e)}")
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
