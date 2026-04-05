from datetime import datetime


def calculate_ipo_price(view_count: int = 0, like_count: int = 0) -> float:
    """
    Fixed IPO price — $10.00 for every video regardless of YouTube metrics.

    YouTube API policy requires that no custom score is derived from API data.
    A uniform starting price ensures zero dependency on YouTube metrics for
    price determination. All subsequent price movement is driven exclusively
    by in-app buy/sell trading activity.
    """
    return 10.0


def calculate_display_stats(view_count: int, published_at) -> dict:
    """
    Returns ONLY simple-arithmetic display stats (YouTube API policy compliant).
    No derived scores, no comparisons, no custom metrics.
    Allowed operations: division only.
    """
    now = datetime.utcnow()
    days_old = max((now - published_at).days, 1) if published_at else 30
    return {
        "views_per_day": round(view_count / days_old, 0),
    }
