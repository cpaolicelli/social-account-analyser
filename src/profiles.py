"""Instagram profile info (name, picture) and which profile each history run is about.

Post data has no owner picture, so profiles are scraped with
apify/instagram-profile-scraper once and kept in a named Apify key-value store
(the picture as a small JPEG, since Instagram links expire). Each run's
resolved profiles are written to an APP_META record in the run's own store,
so the History only has to work them out once.

Functions here may run in worker threads: they take the token as an argument
and never call st.*.
"""
import base64
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from apify_client import ApifyClient
from PIL import Image, ImageOps

from src.costs import ACTOR_POST_SCRAPER, ACTOR_PROFILE_SCRAPER, to_charge_limit

STORE_NAME = "social-analyser-profiles"
META_KEY = "APP_META"
AVATAR_SIZE = (96, 96)
HIT_TTL = 3600
MISS_TTL = 300

_USERNAME = re.compile(r"^[a-z0-9._]{1,30}$")
_RESERVED = {"p", "reel", "reels", "tv", "stories", "explore", "accounts"}
_SHORTCODE = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
_OWNER_IN_URL = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)/(?:p|reels?|tv)/", re.IGNORECASE)

_cache = {}  # username -> (expires_at, info or None)
_store_ids = {}
_lock = threading.Lock()


def _client(token):
    return ApifyClient(token, max_retries=2, timeout_secs=30)


def normalize_username(value):
    """'@KTM_Official', 'https://www.instagram.com/ktm_official/?hl=en' -> 'ktm_official'; '' if not a profile."""
    v = str(value or "").strip().split("?")[0].split("#")[0]
    v = re.sub(r"^(?:https?://)?(?:www\.|m\.)?instagram\.com/", "", v, flags=re.IGNORECASE)
    v = v.lstrip("@").strip("/").split("/")[0].lower()
    return v if _USERNAME.match(v) and v not in _RESERVED else ""


def shortcode(url):
    match = _SHORTCODE.search(str(url or ""))
    return match.group(1) if match else None


def owner_from_url(url):
    """Some share links name the author: instagram.com/<username>/p/<code>/."""
    match = _OWNER_IN_URL.search(str(url or ""))
    return normalize_username(match.group(1)) if match else ""


def _store_id(token):
    with _lock:
        if token in _store_ids:
            return _store_ids[token]
    store_id = _client(token).key_value_stores().get_or_create(name=STORE_NAME)["id"]
    with _lock:
        _store_ids[token] = store_id
    return store_id


def _avatar(url):
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        image = ImageOps.fit(Image.open(io.BytesIO(response.content)).convert("RGB"), AVATAR_SIZE)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85)
        return base64.b64encode(out.getvalue()).decode()
    except Exception:
        return None


# ---------- profile store ----------

def get_cached(token, usernames):
    """{username: info or None} from the profile store (no scraping, no cost).

    info = {"username", "fullName", "verified", "private", "picB64", "fetchedAt"} or {"notFound": True, ...}.
    """
    names = list(dict.fromkeys(n for n in map(normalize_username, usernames) if n))
    now = time.monotonic()
    result, missing = {}, []
    with _lock:
        for name in names:
            hit = _cache.get(name)
            if hit and hit[0] > now:
                result[name] = hit[1]
            else:
                missing.append(name)
    if not missing or not token:
        result.update({n: None for n in missing})
        return result

    try:
        store_id = _store_id(token)
    except Exception as e:
        print(f"Profile store unavailable: {e}")
        result.update({n: None for n in missing})
        return result

    def read(name):
        try:
            record = _client(token).key_value_store(store_id).get_record(name)
            return name, (record or {}).get("value"), True
        except Exception:
            return name, None, False

    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, info, ok in pool.map(read, missing):
            result[name] = info
            if ok:
                with _lock:
                    _cache[name] = (now + (HIT_TTL if info else MISS_TTL), info)
    return result


def missing_profiles(token, usernames):
    cached = get_cached(token, usernames)
    return [name for name, info in cached.items() if info is None]


def fetch_profiles(token, usernames, max_charge_usd):
    """Scrape profiles and save them in the store. Returns (number saved, run cost in USD)."""
    names = list(dict.fromkeys(n for n in map(normalize_username, usernames) if n))
    if not names:
        return 0, 0.0
    client = _client(token)
    run = client.actor(ACTOR_PROFILE_SCRAPER).call(
        run_input={"usernames": names},
        max_total_charge_usd=to_charge_limit(max_charge_usd),
    )
    items = client.dataset(run["defaultDatasetId"]).list_items().items or []

    found, not_found = {}, set()
    for item in items:
        name = normalize_username(item.get("username") or item.get("inputUrl"))
        if not name:
            continue
        if item.get("error"):
            not_found.add(name)
        else:
            found[name] = item

    with ThreadPoolExecutor(max_workers=8) as pool:
        pics = dict(zip(found, pool.map(lambda it: _avatar(it.get("profilePicUrlHD") or it.get("profilePicUrl")), found.values())))

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store = client.key_value_store(_store_id(token))
    saved = 0
    for name in names:
        if name in found:
            item = found[name]
            info = {
                "username": name,
                "fullName": item.get("fullName") or "",
                "verified": bool(item.get("verified")),
                "private": bool(item.get("private")),
                "picB64": pics.get(name),
                "fetchedAt": stamp,
            }
        elif name in not_found:
            info = {"username": name, "notFound": True, "fetchedAt": stamp}
        else:
            continue  # not returned (e.g. budget reached): try again another time
        store.set_record(name, info)
        with _lock:
            _cache[name] = (time.monotonic() + HIT_TTL, info)
        saved += 1

    try:
        cost = float((client.run(run["id"]).get() or {}).get("usageTotalUsd") or 0)
    except Exception:
        cost = 0.0
    return saved, cost


def identify_post_owners(token, urls, max_charge_usd):
    """Scrape posts by URL (basic data) to learn their authors: {shortcode: username}."""
    if not urls:
        return {}
    client = _client(token)
    run = client.actor(ACTOR_POST_SCRAPER).call(
        run_input={"username": list(urls), "dataDetailLevel": "basicData"},
        max_total_charge_usd=to_charge_limit(max_charge_usd),
    )
    items = client.dataset(run["defaultDatasetId"]).list_items().items or []
    owners = {}
    for item in items:
        code = item.get("shortCode") or shortcode(item.get("url") or item.get("inputUrl"))
        owner = normalize_username(item.get("ownerUsername"))
        if code and owner:
            owners[code] = owner
    return owners


# ---------- which profile each run is about ----------

def write_run_meta(token, kvs_id, meta):
    try:
        _client(token).key_value_store(kvs_id).set_record(META_KEY, meta)
    except Exception as e:
        print(f"Could not save run metadata: {e}")


def _read_meta(token, kvs_ids):
    def read(kvs_id):
        try:
            record = _client(token).key_value_store(kvs_id).get_record(META_KEY)
            return kvs_id, (record or {}).get("value")
        except Exception:
            return kvs_id, None
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(read, kvs_ids))


def _read_items(token, dataset_ids, fields):
    def read(dataset_id):
        try:
            return dataset_id, _client(token).dataset(dataset_id).list_items(fields=fields, limit=10000).items or []
        except Exception:
            return dataset_id, []
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(read, dataset_ids))


def _input_profiles(run):
    usernames = (run.get("run_input") or {}).get("username") or []
    if usernames and not any(shortcode(u) for u in usernames):
        return [n for n in map(normalize_username, usernames) if n]
    return None


def resolve_run_profiles(token, post_runs, comment_runs):
    """Set run["profiles"] (usernames) and run["unknown_urls"] on every run, in place."""
    for run in post_runs + comment_runs:
        run["profiles"], run["unknown_urls"] = [], []

    needs_meta = []
    for run in post_runs:
        profiles = _input_profiles(run)
        if profiles is not None:
            run["profiles"] = profiles
        else:
            needs_meta.append(run)
    needs_meta += comment_runs

    metas = _read_meta(token, [r["kvs_id"] for r in needs_meta if r.get("kvs_id")]) if token else {}
    unresolved = []
    for run in needs_meta:
        meta = metas.get(run.get("kvs_id")) or {}
        if meta.get("profiles"):
            run["profiles"] = meta["profiles"]
        else:
            unresolved.append(run)
    if not unresolved or not token:
        return

    post_ids = {id(r) for r in post_runs}

    def run_urls(run):
        run_input = run.get("run_input") or {}
        if id(run) in post_ids:
            return run_input.get("username") or []
        return [u.get("url") if isinstance(u, dict) else u for u in run_input.get("directUrls") or []]

    # 1) A post run started from post URLs names its authors in its own posts.
    own = [r for r in unresolved if id(r) in post_ids and r.get("dataset_id") and r.get("status") == "SUCCEEDED"]
    own_items = _read_items(token, [r["dataset_id"] for r in own], ["ownerUsername"])
    for run in own:
        owners = [n for n in (normalize_username(i.get("ownerUsername")) for i in own_items.get(run["dataset_id"]) or []) if n]
        run["profiles"] = list(dict.fromkeys(owners))

    # 2) Other runs: authors from share links, else from an index of every scanned post.
    pending = [r for r in unresolved if not r["profiles"] and run_urls(r)]
    owner_of = {}
    if any(not owner_from_url(u) for r in pending for u in run_urls(r)):
        datasets = [r["dataset_id"] for r in post_runs if r.get("dataset_id") and r.get("status") == "SUCCEEDED"]
        for items in _read_items(token, datasets, ["shortCode", "url", "ownerUsername"]).values():
            for item in items:
                code = item.get("shortCode") or shortcode(item.get("url"))
                owner = normalize_username(item.get("ownerUsername"))
                if code and owner:
                    owner_of[code] = owner
    for run in pending:
        profiles, unknown = [], []
        for url in run_urls(run):
            owner = owner_from_url(url) or owner_of.get(shortcode(url))
            if owner:
                profiles.append(owner)
            else:
                unknown.append(url)
        run["profiles"] = list(dict.fromkeys(profiles))
        run["unknown_urls"] = unknown

    # Save what was worked out, so the next load reads one record per run.
    for run in unresolved:
        if run["profiles"] and not run["unknown_urls"] and run.get("kvs_id"):
            write_run_meta(token, run["kvs_id"], {"profiles": run["profiles"]})


def apply_identified_owners(token, runs, owners):
    """After identify_post_owners: complete runs with unknown URLs and save their metadata."""
    for run in runs:
        if not run.get("unknown_urls"):
            continue
        still_unknown = []
        for url in run["unknown_urls"]:
            owner = owners.get(shortcode(url))
            if owner:
                run["profiles"] = list(dict.fromkeys(run["profiles"] + [owner]))
            else:
                still_unknown.append(url)
        run["unknown_urls"] = still_unknown
        if run["profiles"] and not still_unknown and run.get("kvs_id"):
            write_run_meta(token, run["kvs_id"], {"profiles": run["profiles"]})
