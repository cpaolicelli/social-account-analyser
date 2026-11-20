import re
from concurrent.futures import ThreadPoolExecutor

from apify_client import ApifyClient
import streamlit as st
import pandas as pd

from src.costs import ACTOR_COMMENT_SCRAPER, ACTOR_POST_SCRAPER, get_api_token, to_charge_limit
from src.profiles import write_run_meta


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
    """Scrape posts of a profile, or of specific post URLs when given a list of them."""
    client = get_apify_client()
    if not client:
        return [], 0, None

    if scan_params is None:
        scan_params = {}

    run_input = {
        "username": username_url if isinstance(username_url, list) else [username_url],
        "dataDetailLevel": scan_params.get("dataDetailLevel", "basicData"),
    }
    # Only send filters that were asked for: a date filter would drop older posts given by URL.
    for name in ("onlyPostsNewerThan", "resultsLimit", "skipPinnedPosts"):
        if name in scan_params:
            run_input[name] = scan_params[name]

    try:
        run = client.actor(ACTOR_POST_SCRAPER).call(run_input=run_input)
        dataset_items = client.dataset(run["defaultDatasetId"]).list_items().items

        cost = _get_run_cost(client, run["id"])

        return dataset_items, cost, run["id"]
    except Exception as e:
        st.error(f"Error fetching posts: {str(e)}")
        return [], 0, None

def fetch_instagram_comments(post_urls, per_post_limit, include_replies=True, max_charge_usd=None, meta=None):
    """Scrape comments from post URLs.

    per_post_limit is applied to each URL by the actor (see costs.plan_comment_scrape).
    max_charge_usd is a hard spending cap enforced by Apify; replies count toward it.
    meta (e.g. {"profiles": [...]}) is saved with the run so the History can name it.
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
        if meta and run.get("defaultKeyValueStoreId"):
            write_run_meta(get_api_token(), run["defaultKeyValueStoreId"], meta)

        cost = _get_run_cost(client, run["id"])

        return dataset_items, cost
    except Exception as e:
        st.error(f"Error fetching comments: {str(e)}")
        return [], 0


_POST_URL = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(p|reels?|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def parse_instagram_post_urls(text):
    """Split pasted text into normalised post/reel URLs and the lines that aren't one.

    Returns (urls, invalid_lines); duplicates are dropped, order kept.
    """
    urls, invalid = [], []
    for line in re.split(r"[\s,]+", text or ""):
        line = line.strip()
        if not line:
            continue
        match = _POST_URL.match(line)
        if not match:
            invalid.append(line)
            continue
        kind = "reel" if match.group(1).lower().startswith("reel") else match.group(1).lower()
        url = f"https://www.instagram.com/{kind}/{match.group(2)}/"
        if url not in urls:
            urls.append(url)
    return urls, invalid


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
    """Extract Instagram username, input config and key-value store ID from a run.

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

        # Post scraper uses "username" list, comment scraper uses "directUrls".
        # This is only a fallback label: profiles.resolve_run_profiles names the real profiles.
        usernames = run_input.get("username", [])
        if usernames and all(_POST_URL.match(u.strip()) for u in usernames):
            return f"{len(usernames)} post URLs", run_input, kvs_id
        if usernames:
            clean_names = [_clean_instagram_handle(u) for u in usernames]
            return ", ".join(clean_names), run_input, kvs_id

        direct_urls = run_input.get("directUrls", [])
        if direct_urls:
            return f"{len(direct_urls)} post URLs", run_input, kvs_id

        return "Unknown", run_input, kvs_id
    except Exception:
        return "Unknown", {}, run.get("defaultKeyValueStoreId")


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
        for run, (username, run_input, kvs_id) in zip(runs, details):
            history.append({
                "run_id": run.get("id", ""),
                "username": username,
                "status": run.get("status", "UNKNOWN"),
                "started_at": run.get("startedAt", ""),
                "finished_at": run.get("finishedAt", ""),
                "cost_usd": float(run.get("usageTotalUsd", 0) or 0),
                "dataset_id": run.get("defaultDatasetId", ""),
                "kvs_id": kvs_id,
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
