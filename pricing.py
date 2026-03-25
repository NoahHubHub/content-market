import math
from datetime import datetime
from statistics import mean, stdev


def velocity(view_count: int, days_old: int) -> float:
    """
    Views / sqrt(days) — cumulative velocity, age-adjusted.

    Assumes V(t) ≈ A * sqrt(t) for a healthy growth curve, so dividing by
    sqrt(t) gives a constant 'A' regardless of age.

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
    prev_view_count: int = None,  # views at the previous snapshot (for recent momentum)
) -> dict:
    """
    Price = Base × RPS-Factor × Engagement

    Base        — absolute popularity (log scale of blended velocity)
    RPS-Factor  — how well this video performs vs. creator average
    Engagement  — like / comment multiplier

    When prev_view_count is provided, velocity is a blend of cumulative
    age-adjusted velocity (60%) and recent 1-day acceleration (40%). This
    makes rising videos more expensive and declining videos cheaper.
    """
    now = datetime.utcnow()
    days_old = max((now - published_at).days, 1) if published_at else 30

    cumulative_v = velocity(view_count, days_old)

    # ── Blend cumulative and recent velocity ──────────────────────────────────
    if prev_view_count is not None and view_count > prev_view_count:
        # Recent delta assumed over ~1 day between snapshots
        recent_v = max(view_count - prev_view_count, 0)
        v = 0.6 * cumulative_v + 0.4 * recent_v
    else:
        v = cumulative_v

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
            vol = 0.5  # single comparison → moderate uncertainty

        # Risk rating — thresholds tuned for YouTube reality
        n = len(channel_videos)
        if n < 2 or vol > 2.5:
            risk_label, risk_color = "Extreme", "danger"
        elif vol > 1.0:
            risk_label, risk_color = "High", "warning"
        elif vol > 0.4:
            risk_label, risk_color = "Medium", "info"
        else:
            risk_label, risk_color = "Low", "success"
    else:
        # No channel data → use absolute velocity as proxy for risk
        if v > 300_000:
            risk_label, risk_color = "Low", "success"
        elif v > 50_000:
            risk_label, risk_color = "Medium", "info"
        elif v > 5_000:
            risk_label, risk_color = "High", "warning"
        else:
            risk_label, risk_color = "Extreme", "danger"
        rps = 1.0

    # ── 2. Base price — log scale of absolute velocity ───────────────────────
    # Uses a wider multiplier (15) for more price spread across videos:
    # velocity ~100 → ~$15, ~10k → ~$30, ~1M → ~$45, ~100M → ~$60
    base_price = 15 * (1 + math.log10(max(v / 10, 0.01)))

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
