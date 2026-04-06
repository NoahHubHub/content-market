import json
import logging
import os
import re
import threading
from datetime import datetime
from time import time
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
_CACHE_TTL = 1800  # 30 minutes

# ── Singleton API client ───────────────────────────────────────────────────────
_yt_client = None

def _client():
    global _yt_client
    if _yt_client is None:
        _yt_client = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    return _yt_client


# ── Cache backend — Redis if REDIS_URL is set, in-memory otherwise ─────────────
# Redis: shared across workers, survives restarts, no race conditions.
# In-memory: zero dependencies, fine for single-worker Railway deployments.

_REDIS_URL = os.getenv("REDIS_URL")
_redis_client = None

def _get_redis():
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.from_url(_REDIS_URL, decode_responses=False)
            _redis_client.ping()
            log.info("YouTube cache: using Redis at %s", _REDIS_URL.split("@")[-1])
        except Exception:
            log.warning("YouTube cache: Redis unavailable, falling back to in-memory", exc_info=True)
            _redis_client = False
    return _redis_client if _redis_client else None


# In-memory fallback
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 500


def _cache_get(key: str):
    if _REDIS_URL:
        r = _get_redis()
        if r:
            try:
                raw = r.get(f"yt:{key}")
                return json.loads(raw) if raw else None
            except Exception:
                log.warning("Redis cache_get failed for %s", key, exc_info=True)

    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and (time() - entry[1]) < _CACHE_TTL:
            return entry[0]
    return None


def _cache_set(key: str, value):
    if _REDIS_URL:
        r = _get_redis()
        if r:
            try:
                r.setex(f"yt:{key}", _CACHE_TTL, json.dumps(value, default=str))
                return
            except Exception:
                log.warning("Redis cache_set failed for %s", key, exc_info=True)

    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            now = time()
            expired = [k for k, (_, ts) in _CACHE.items() if now - ts >= _CACHE_TTL]
            for k in expired:
                del _CACHE[k]
            if len(_CACHE) >= _CACHE_MAX:
                oldest = sorted(_CACHE.items(), key=lambda x: x[1][1])
                for k, _ in oldest[:_CACHE_MAX // 5]:
                    del _CACHE[k]
        _CACHE[key] = (value, time())


def _log_quota_usage(endpoint: str, units: int) -> None:
    """Async-safe quota tracker — fire and forget, never raises."""
    try:
        from database import SessionLocal
        import models as _models
        today = datetime.utcnow().strftime("%Y-%m-%d")
        db = SessionLocal()
        try:
            entry = (
                db.query(_models.QuotaUsage)
                .filter_by(date=today, endpoint=endpoint)
                .first()
            )
            if entry:
                entry.units_used  += units
                entry.calls_count += 1
            else:
                db.add(_models.QuotaUsage(
                    date=today, endpoint=endpoint,
                    units_used=units, calls_count=1,
                ))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.debug("_log_quota_usage failed silently", exc_info=True)


def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from a YouTube URL or return the input if it looks like an ID."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id.strip())
        if match:
            return match.group(1)
    return url_or_id.strip()


def _parse_items(items: list) -> list:
    results = []
    for item in items:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        published_str = snippet.get("publishedAt", "")
        try:
            published_at = datetime.strptime(published_str, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            published_at = datetime.utcnow()

        results.append(
            {
                "youtube_id": item["id"],
                "title": snippet.get("title", "Unknown"),
                "channel_name": snippet.get("channelTitle", "Unknown"),
                "channel_id": snippet.get("channelId", ""),
                "thumbnail_url": snippet.get("thumbnails", {})
                .get("medium", {})
                .get("url", ""),
                "published_at": published_at,
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "category": CATEGORY_MAP.get(snippet.get("categoryId", ""), None),
            }
        )
    return results


CATEGORY_MAP = {
    "1":  "Film & Animation", "2":  "Autos",    "10": "Music",
    "15": "Tiere",            "17": "Sport",     "20": "Gaming",
    "22": "Vlogs",            "23": "Comedy",    "24": "Entertainment",
    "25": "News",             "26": "Style",     "27": "Education",
    "28": "Tech",             "29": "Soziales",
}


def get_video_details(video_ids: list) -> list:
    """Fetch snippet + statistics for up to 50 video IDs. Costs 1 quota unit per batch.
    Results are cached for 30 minutes. Uses the unified cache — so if get_stats_only
    already fetched a video, the stats portion is returned from cache without an API call."""
    if not video_ids:
        return []

    cached_results = []
    uncached_ids = []
    for vid in video_ids:
        hit = _cache_get(f"vid:{vid}")
        if hit is not None:
            cached_results.append(hit)
        else:
            uncached_ids.append(vid)

    if not uncached_ids:
        return cached_results

    response = (
        _client().videos()
        .list(id=",".join(uncached_ids), part="statistics,snippet")
        .execute()
    )
    _log_quota_usage("videos.list", 1)
    fresh = _parse_items(response.get("items", []))
    for item in fresh:
        _cache_set(f"vid:{item['youtube_id']}", item)

    return cached_results + fresh


def get_video_by_id(video_id: str) -> list:
    """Fetch a single video. Costs 1 quota unit (cached 30 min)."""
    return get_video_details([video_id])


def search_videos(query: str, max_results: int = 8) -> list:
    """Search YouTube. Costs 100 quota units — use sparingly. Cached 30 min."""
    cache_key = f"search:{query}:{max_results}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    search_response = (
        _client().search()
        .list(q=query, type="video", part="id", maxResults=max_results)
        .execute()
    )
    _log_quota_usage("search.list", 100)
    video_ids = [item["id"]["videoId"] for item in search_response.get("items", [])]
    result = get_video_details(video_ids)
    _cache_set(cache_key, result)
    return result


def get_stats_only(video_ids: list) -> list:
    """Fetch ONLY statistics for known videos (no snippet). Costs 1 quota unit per batch.
    Used by the scheduler for periodic refreshes where metadata already exists in DB.

    Cross-checks the full-data cache (vid:) first so a prior get_video_details call
    avoids a redundant API hit."""
    if not video_ids:
        return []

    cached_results = []
    uncached_ids = []
    for vid in video_ids:
        # Check stats-only cache first, then fall back to full-data cache
        hit = _cache_get(f"stats:{vid}") or _cache_get(f"vid:{vid}")
        if hit is not None:
            # Normalise to stats-only shape if needed
            cached_results.append({
                "youtube_id": hit["youtube_id"],
                "view_count": hit["view_count"],
                "like_count": hit["like_count"],
                "comment_count": hit["comment_count"],
            })
        else:
            uncached_ids.append(vid)

    if not uncached_ids:
        return cached_results

    response = (
        _client().videos()
        .list(id=",".join(uncached_ids), part="statistics")
        .execute()
    )
    _log_quota_usage("videos.list", 1)
    fresh = []
    for item in response.get("items", []):
        stats = item.get("statistics", {})
        entry = {
            "youtube_id": item["id"],
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
        }
        _cache_set(f"stats:{entry['youtube_id']}", entry)
        fresh.append(entry)

    return cached_results + fresh


def get_trending_videos(region: str = "DE", max_results: int = 20) -> list:
    """Fetch trending/most popular videos. Costs 1 quota unit per call. Cached 30 min."""
    cache_key = f"trending:{region}:{max_results}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    response = (
        _client().videos()
        .list(chart="mostPopular", regionCode=region,
              part="statistics,snippet", maxResults=max_results)
        .execute()
    )
    _log_quota_usage("videos.list(trending)", 1)
    result = _parse_items(response.get("items", []))
    _cache_set(cache_key, result)
    return result
