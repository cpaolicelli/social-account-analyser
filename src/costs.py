"""Apify pricing lookup and comment-scrape planning."""
import hashlib
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_UP, Decimal

import streamlit as st
from apify_client import ApifyClient

ACTOR_POST_SCRAPER = "apify/instagram-post-scraper"
ACTOR_COMMENT_SCRAPER = "apify/instagram-comment-scraper"
ACTOR_PROFILE_SCRAPER = "apify/instagram-profile-scraper"

# Apify prices Store actors per subscription tier; FREE is the most expensive.
DEFAULT_TIER = "FREE"

# USD per event on Apify's FREE tier as of 2026-11-20. Used only when the live
# price list can't be read or no longer has the event.
FALLBACK_PRICES = {
    (ACTOR_POST_SCRAPER, "post"): 0.0017,
    (ACTOR_POST_SCRAPER, "post-details"): 0.0010,
    (ACTOR_COMMENT_SCRAPER, "comment"): 0.0026,
    (ACTOR_PROFILE_SCRAPER, "profile"): 0.0026,
}

PRICES_TTL = 6 * 3600
TIER_TTL = 3600
FAILURE_TTL = 120  # retry soon after a failed read instead of keeping the fallback for hours

_cache = {}
_cache_lock = threading.Lock()


def _cached(key, loader, ttl):
    """Memoise loader() for ttl seconds; failures (None) are kept only FAILURE_TTL."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        value = loader()
    except Exception as e:
        print(f"Apify pricing lookup failed ({key[0]}): {e}")
        value = None
    with _cache_lock:
        _cache[key] = (now + (ttl if value is not None else FAILURE_TTL), value)
    return value


def clear_cache():
    with _cache_lock:
        _cache.clear()


def get_api_token():
    token = st.secrets.get("apify", {}).get("api_token")
    if not token or token == "YOUR_APIFY_API_TOKEN":
        return None
    return token


def _as_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _load_actor_pricing(actor_id):
    """Price list in effect today: {"events": {name: {"tiers": {...}, "flat": x}}, "min_charge": x}."""
    # Actor pricing is public, so no token is needed.
    actor = ApifyClient(max_retries=1, timeout_secs=10).actor(actor_id).get() or {}
    now = datetime.now(timezone.utc)
    # pricingInfos also lists scheduled future changes; take the latest one already started.
    active = [p for p in actor.get("pricingInfos") or [] if _as_datetime(p.get("startedAt")) <= now]
    if not active:
        raise ValueError("no active pricing info")
    info = max(active, key=lambda p: _as_datetime(p.get("startedAt")))

    events = {}
    for name, event in ((info.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}).items():
        tiers = {
            tier.upper(): float(entry["tieredEventPriceUsd"])
            for tier, entry in (event.get("eventTieredPricingUsd") or {}).items()
            if (entry or {}).get("tieredEventPriceUsd") is not None
        }
        flat = event.get("eventPriceUsd")
        events[name] = {"tiers": tiers, "flat": float(flat) if flat is not None else None}
    min_charge = info.get("minimalMaxTotalChargeUsd")
    return {"events": events, "min_charge": float(min_charge) if min_charge is not None else None}


def _load_account_tier(token):
    user = ApifyClient(token, max_retries=1, timeout_secs=10).user().get() or {}
    tier = str((user.get("plan") or {}).get("tier") or "").upper()
    if not tier:
        raise ValueError("account plan has no tier")
    return tier


def resolve_tier():
    """Return (tier, source); source is "secrets", "account" or "default"."""
    override = st.secrets.get("apify", {}).get("pricing_tier")
    if override:
        return str(override).upper(), "secrets"
    token = get_api_token()
    if token:
        key = ("tier", hashlib.sha256(token.encode()).hexdigest())
        tier = _cached(key, lambda: _load_account_tier(token), TIER_TTL)
        if tier:
            return tier, "account"
    return DEFAULT_TIER, "default"


def get_event_price(actor_id, event):
    """Return (usd, is_live) for one charged event at the account's tier."""
    tier, _ = resolve_tier()
    pricing = _cached(("prices", actor_id), lambda: _load_actor_pricing(actor_id), PRICES_TTL)
    info = (pricing or {}).get("events", {}).get(event)
    if info:
        if tier in info["tiers"]:
            return info["tiers"][tier], True
        if info["flat"] is not None:
            return info["flat"], True
        if info["tiers"]:
            return max(info["tiers"].values()), True  # unknown tier: assume the most expensive
    return FALLBACK_PRICES[(actor_id, event)], False


def min_charge_usd(actor_id):
    """Smallest max_total_charge_usd Apify accepts for this actor, if known."""
    pricing = _cached(("prices", actor_id), lambda: _load_actor_pricing(actor_id), PRICES_TTL)
    return (pricing or {}).get("min_charge")


def post_price(detailed):
    price, _ = get_event_price(ACTOR_POST_SCRAPER, "post")
    if detailed:
        price += get_event_price(ACTOR_POST_SCRAPER, "post-details")[0]
    return price


def comment_price():
    return get_event_price(ACTOR_COMMENT_SCRAPER, "comment")[0]


def profile_price():
    return get_event_price(ACTOR_PROFILE_SCRAPER, "profile")[0]


def pricing_notes(actor_id, events):
    """Messages describing where the prices come from: [(level, text)], level "info" or "warning"."""
    tier, source = resolve_tier()
    notes = []
    if source == "default":
        notes.append(("warning", f"Couldn't read your Apify plan, so prices assume the {tier} tier (the most expensive)."))
    if not all(get_event_price(actor_id, e)[1] for e in events):
        notes.append(("warning", "Couldn't read Apify's live price list: using built-in prices from 2026-11-20, which may be out of date."))
    if not notes:
        origin = "your Apify plan" if source == "account" else "set in secrets.toml"
        notes.append(("info", f"Live Apify prices · {tier} tier ({origin})"))
    return notes


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

    counts = [count for _, count in candidates]
    if target_total >= sum(counts):
        # More than Instagram reports: counts can be stale or leave out replies, so ask
        # every post for an even share of the total (never less than its own count).
        limit = max(max(counts), -(-target_total // len(candidates)))
        return [url for url, _ in candidates], limit, sum(counts)

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
