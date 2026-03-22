import math
from datetime import datetime
from statistics import mean, stdev


def velocity(view_count: int, days_old: int) -> float:
    """
    Views / sqrt(days) — the core normalization.

    A video follows roughly V(t) ≈ A * sqrt(t) in a healthy growth curve.
    Dividing by sqrt(t) gives a constant 'A' (velocity) regardless of age.

    Examples at equal velocity (A = 100 000):
      100k views after  1 day  → velocity = 100 000
      200k views after  4 days → velocity = 100 000
      300k views after  9 days → velocity = 100 000
      600k views after 36 days → velocity = 100 000
    """
    return view_count / math.sqrt(max(days_old, 1))


def calculate_price(
    view_count: int,
    like_count: int,
    comment_count: int,
    published_at: datetime,
    channel_videos: list = None,  # [(view_count, published_at), ...] of same creator
    prev_view_count: int = None,
) -> dict:
    """
    Price = Base × RPS-Factor × Engagement

    Base        — absolute popularity (log scale of velocity)
    RPS-Factor  — how well this video performs vs. creator average
    Engagement  — like / comment multiplier
    """
    now = datetime.utcnow()
    days_old = max((now - published_at).days, 1) if published_at else 30

    v = velocity(view_count, days_old)

    # ── 1. Channel baseline (other videos by the same creator) ───────────────
    rps = 1.0
    risk_label = "Extreme"
    risk_color = "danger"

    if channel_videos and len(channel_videos) >= 1:
        other_v = []
        for cv_views, cv_pub in channel_videos:
            cv_days = max((now - cv_pub).days, 1) if cv_pub else 30
            other_v.append(velocity(cv_views, cv_days))

        avg_v = mean(other_v) if other_v else v

        # RPS: how much faster is this video vs. the creator's average?
        rps = v / avg_v if avg_v > 0 else 1.0

        # Volatility = coefficient of variation of creator velocities
        if len(other_v) >= 2:
            vol = stdev(other_v) / max(avg_v, 1)
        else:
            vol = 1.0  # single video → uncertain

        # Risk rating
        n = len(channel_videos)
        if n < 3 or vol > 1.2:
            risk_label, risk_color = "Extreme", "danger"
        elif vol > 0.6:
            risk_label, risk_color = "High", "warning"
        elif vol > 0.25:
            risk_label, risk_color = "Medium", "info"
        else:
            risk_label, risk_color = "Low", "success"
    else:
        # No channel data → treat as unknown creator, extreme risk
        rps = 1.0

    # ── 2. Base price — log scale of absolute velocity ───────────────────────
    # velocity of ~100 → $10, ~10 000 → $20, ~1 000 000 → $30, ~100 000 000 → $40
    base_price = 10 * (1 + math.log10(max(v / 10, 0.01)))

    # ── 3. RPS factor — sqrt-smoothed so outliers don't explode price ────────
    # RPS = 4 (4× creator avg) → factor = 2.0
    # RPS = 1 (average)        → factor = 1.0
    # RPS = 0.25 (quarter avg) → factor = 0.5
    rps_factor = max(0.2, min(math.sqrt(rps), 4.0))

    # ── 4. Engagement multiplier ─────────────────────────────────────────────
    if view_count > 0:
        like_ratio    = like_count    / view_count
        comment_ratio = comment_count / view_count
        engagement = 1 + min(like_ratio * 5 + comment_ratio * 25, 0.8)
    else:
        engagement = 1.0

    price = round(max(1.0, base_price * rps_factor * engagement), 2)

    # Momentum % for display (how far above/below creator average)
    momentum_pct = round((rps - 1) * 100, 1)

    return {
        "price": price,
        "rps": round(rps, 2),
        "risk": risk_label,
        "risk_color": risk_color,
        "momentum_pct": momentum_pct,
        "views_per_day": round(view_count / days_old, 0),
        "velocity": round(v, 1),
    }
