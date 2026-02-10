from apify_client import ApifyClient
import streamlit as st
import pandas as pd

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

def fetch_instagram_posts(username_url, days=31, limit=1000):
    client = get_apify_client()
    if not client:
        return []

    run_input = {
        "onlyPostsNewerThan": f"{days} days",
        "resultsLimit": limit,
        "skipPinnedPosts": False,
        "username": [username_url]
    }
    
    try:
        run = client.actor("apify/instagram-post-scraper").call(run_input=run_input)
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
