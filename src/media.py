"""Post thumbnails, downloaded server-side.

Instagram CDN links can't be embedded reliably from another site and expire
after a few days, so images are fetched here, shrunk, and kept in memory.
"""
import io
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageOps

THUMB_SIZE = (256, 320)  # 4:5, like Instagram's profile grid, so cards line up
OK_TTL = 6 * 3600
FAILURE_TTL = 600
MAX_ENTRIES = 1500

_cache = OrderedDict()  # url -> (expires_at, jpeg_bytes or None)
_lock = threading.Lock()


def _download(url):
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        image = ImageOps.fit(image.convert("RGB"), THUMB_SIZE)  # centre crop
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=80)
        return out.getvalue()
    except Exception:
        return None  # expired link, blocked or not an image: caller shows a placeholder


def get_thumbnails(urls):
    """Return {url: jpeg_bytes or None}, downloading missing ones in parallel."""
    now = time.monotonic()
    result, missing = {}, []
    with _lock:
        for url in dict.fromkeys(u for u in urls if u):
            hit = _cache.get(url)
            if hit and hit[0] > now:
                _cache.move_to_end(url)
                result[url] = hit[1]
            else:
                missing.append(url)

    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            fetched = dict(zip(missing, pool.map(_download, missing)))
        with _lock:
            for url, data in fetched.items():
                _cache[url] = (now + (OK_TTL if data else FAILURE_TTL), data)
                _cache.move_to_end(url)
            while len(_cache) > MAX_ENTRIES:
                _cache.popitem(last=False)
        result.update(fetched)
    return result


def post_image_url(post):
    """Best thumbnail URL for a post item from apify/instagram-post-scraper."""
    if post.get("displayUrl"):
        return post["displayUrl"]
    images = post.get("images") or []
    return images[0] if images else None
