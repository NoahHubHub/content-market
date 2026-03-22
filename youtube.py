import os
import re
from datetime import datetime
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


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
            }
        )
    return results


def get_video_details(video_ids: list) -> list:
    """Fetch stats for up to 50 video IDs. Costs 1 quota unit per call."""
    if not video_ids:
        return []
    yt = _client()
    response = (
        yt.videos()
        .list(id=",".join(video_ids), part="statistics,snippet")
        .execute()
    )
    return _parse_items(response.get("items", []))


def get_video_by_id(video_id: str) -> list:
    """Fetch a single video. Costs 1 quota unit."""
    return get_video_details([video_id])


def search_videos(query: str, max_results: int = 8) -> list:
    """Search YouTube. Costs 100 quota units — use sparingly."""
    yt = _client()
    search_response = (
        yt.search()
        .list(q=query, type="video", part="id", maxResults=max_results)
        .execute()
    )
    video_ids = [item["id"]["videoId"] for item in search_response.get("items", [])]
    return get_video_details(video_ids)
