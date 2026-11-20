"""Apify pricing lookup and comment-scrape planning."""
from datetime import datetime, timezone
from decimal import ROUND_UP, Decimal

import streamlit as st
from apify_client import ApifyClient

ACTOR_POST_SCRAPER = "apify/instagram-post-scraper"
ACTOR_COMMENT_SCRAPER = "apify/instagram-comment-scraper"

# USD per event on Apify's FREE tier as of 2026-11-20. Used only when the live
# price list can't be read.
FALLBACK_PRICES = {
    (ACTOR_POST_SCRAPER, "post"): 0.0017,
    (ACTOR_POST_SCRAPER, "post-details"): 0.0010,
    (ACTOR_COMMENT_SCRAPER, "comment"): 0.0026,
}


def _pricing_tier():
    # Apify discounts by subscription tier (FREE, BRONZE, SILVER, GOLD, ...).
    # FREE is the most expensive, so it gives a safe upper bound by default.
    return str(st.secrets.get("apify", {}).get("pricing_tier", "FREE")).upper()


def _as_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _fetch_event_prices(actor_id, tier):
    """Return {event_name: usd} for the actor's price list in effect today."""
    try:
        # Actor pricing is public, so no token is needed.
        actor = ApifyClient(max_retries=1, timeout_secs=10).actor(actor_id).get() or {}
    except Exception as e:
        print(f"Could not read Apify pricing for {actor_id}: {e}")
        return {}

    now = datetime.now(timezone.utc)
    # pricingInfos also lists scheduled future changes; take the latest one already started.
    active = [p for p in actor.get("pricingInfos") or [] if _as_datetime(p.get("startedAt")) <= now]
    if not active:
        return {}
    info = max(active, key=lambda p: _as_datetime(p.get("startedAt")))

    prices = {}
    events = (info.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}
    for name, event in events.items():
        tiers = event.get("eventTieredPricingUsd") or {}
        price = (tiers.get(tier) or {}).get("tieredEventPriceUsd")
        if price is None:
            price = event.get("eventPriceUsd")
        if price is None and tiers:
            price = max(t.get("tieredEventPriceUsd") or 0 for t in tiers.values())
        if price is not None:
            prices[name] = float(price)
    return prices


def get_event_price(actor_id, event):
    """Current USD price of one charged event (e.g. one post or one comment)."""
    prices = _fetch_event_prices(actor_id, _pricing_tier())
    return prices.get(event, FALLBACK_PRICES[(actor_id, event)])


def post_price(detailed):
    price = get_event_price(ACTOR_POST_SCRAPER, "post")
    if detailed:
        price += get_event_price(ACTOR_POST_SCRAPER, "post-details")
    return price


def comment_price():
    return get_event_price(ACTOR_COMMENT_SCRAPER, "comment")


def to_charge_limit(usd):
    """Round a USD amount up to Apify's max_total_charge_usd format."""
    return Decimal(str(usd)).quantize(Decimal("0.0001"), rounding=ROUND_UP)


def plan_comment_scrape(posts, target_total):
    """Pick post URLs and a per-post comment limit that keep the total near target_total.

    The comment scraper's resultsLimit applies to each URL, so passing the total
    as-is would fetch up to len(urls) * total comments. The limit is spread
    evenly: the largest per-post value whose expected total stays <= target_total.

    Returns (urls, per_post_limit, expected_total).
    """
    candidates = [
        (p["url"], int(p.get("commentsCount") or 0))
        for p in posts
        if p.get("url") and (p.get("commentsCount") or 0) > 0
    ]
    target_total = int(target_total)
    if not candidates or target_total < 1:
        return [], 0, 0

    if len(candidates) >= target_total:
        # More posts than comments wanted: one comment from each of the first posts.
        urls = [url for url, _ in candidates[:target_total]]
        return urls, 1, len(urls)

    def expected(limit):
        return sum(min(count, limit) for _, count in candidates)

    low, high = 1, max(count for _, count in candidates)
    while low < high:
        mid = (low + high + 1) // 2
        if expected(mid) <= target_total:
            low = mid
        else:
            high = mid - 1
    return [url for url, _ in candidates], low, expected(low)
