import os
import re
from datetime import datetime
from time import time
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# ── In-memory cache ────────────────────────────────────────────────────────────
_CACHE: dict = {}
_CACHE_TTL = 1800  # 30 minutes


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    return None


def _cache_set(key: str, value):
    _CACHE[key] = (value, time())


def _client():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


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
    """Fetch stats for up to 50 video IDs. Costs 1 quota unit per call.
    Results are cached for 30 minutes to minimise quota usage."""
    if not video_ids:
        return []

    # Split into cached and uncached IDs
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

    yt = _client()
    response = (
        yt.videos()
        .list(id=",".join(uncached_ids), part="statistics,snippet")
        .execute()
    )
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

    yt = _client()
    search_response = (
        yt.search()
        .list(q=query, type="video", part="id", maxResults=max_results)
        .execute()
    )
    video_ids = [item["id"]["videoId"] for item in search_response.get("items", [])]
    result = get_video_details(video_ids)
    _cache_set(cache_key, result)
    return result


def get_stats_only(video_ids: list) -> list:
    """Fetch ONLY statistics for known videos (no snippet). Costs 1 quota unit.
    Used for periodic price refreshes where metadata already exists in DB.
    Cached 30 min like get_video_details."""
    if not video_ids:
        return []

    cached_results = []
    uncached_ids = []
    for vid in video_ids:
        hit = _cache_get(f"stats:{vid}")
        if hit is not None:
            cached_results.append(hit)
        else:
            uncached_ids.append(vid)

    if not uncached_ids:
        return cached_results

    yt = _client()
    response = (
        yt.videos()
        .list(id=",".join(uncached_ids), part="statistics")
        .execute()
    )
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
    """Fetch trending/most popular videos. Costs 1 quota unit. Cached 30 min."""
    cache_key = f"trending:{region}:{max_results}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    yt = _client()
    response = (
        yt.videos()
        .list(chart="mostPopular", regionCode=region,
              part="statistics,snippet", maxResults=max_results)
        .execute()
    )
    result = _parse_items(response.get("items", []))
    _cache_set(cache_key, result)
    return result
