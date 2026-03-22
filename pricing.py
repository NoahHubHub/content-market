import math
from datetime import datetime


def calculate_price(
    view_count: int,
    like_count: int,
    comment_count: int,
    published_at: datetime,
    prev_view_count: int = None,
) -> dict:
    """
    Calculate video price based on:
    1. Popularity (total views, log scale)
    2. Momentum (view growth since last snapshot)
    3. Engagement (like/comment ratio)
    4. Age decay (very old videos lose value slowly)
    """
    now = datetime.utcnow()
    days_old = max((now - published_at).days, 1) if published_at else 30

    # 1. Base price from total views (log scale)
    # 1k views → $10, 100k → $30, 10M → $50
    base_price = 10 * (1 + math.log10(max(view_count / 1000, 0.01)))

    # 2. Momentum: growth since last check
    momentum = 1.0
    if prev_view_count and prev_view_count > 0 and view_count > prev_view_count:
        growth_rate = (view_count - prev_view_count) / prev_view_count
        momentum = 1 + min(growth_rate * 3, 2.0)  # max 3x spike
    elif prev_view_count and view_count < prev_view_count:
        momentum = 0.98  # tiny decay if no new views

    # 3. Engagement multiplier
    if view_count > 0:
        like_ratio = like_count / view_count
        comment_ratio = comment_count / view_count
        engagement = 1 + min(like_ratio * 5 + comment_ratio * 20, 1.0)
    else:
        engagement = 1.0

    # 4. Age decay (starts after 2 years)
    age_factor = max(0.5, 1 - max(0, days_old - 730) / 1000)

    price = base_price * momentum * engagement * age_factor
    price = round(max(1.0, price), 2)

    # Risk rating based on total views
    if view_count < 10_000:
        risk, risk_color = "Extreme", "danger"
    elif view_count < 100_000:
        risk, risk_color = "High", "warning"
    elif view_count < 1_000_000:
        risk, risk_color = "Medium", "info"
    else:
        risk, risk_color = "Low", "success"

    momentum_pct = round((momentum - 1) * 100, 1)
    views_per_day = round(view_count / days_old, 0)

    return {
        "price": price,
        "risk": risk,
        "risk_color": risk_color,
        "momentum_pct": momentum_pct,
        "views_per_day": views_per_day,
    }
