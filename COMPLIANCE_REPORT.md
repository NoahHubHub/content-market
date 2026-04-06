# YouTube API Compliance Report — Clip Capital

**Date:** 2026-04-06  
**Subject:** YouTube Data API v3 Usage & Pricing Independence

---

## 1. Pricing Model — Fixed at $10.00

All videos in Clip Capital start at a **fixed IPO price of $10.00**, regardless of any YouTube metrics.

**Code reference:** `pricing.py:calculate_ipo_price()`

```python
def calculate_ipo_price(view_count: int = 0, like_count: int = 0) -> float:
    """
    Fixed IPO price — $10.00 for every video regardless of YouTube metrics.

    YouTube API policy requires that no custom score is derived from API data.
    A uniform starting price ensures zero dependency on YouTube metrics for
    price determination. All subsequent price movement is driven exclusively
    by in-app buy/sell trading activity.
    """
    return 10.0
```

The function signature accepts `view_count` and `like_count` parameters but **ignores them** — the return value is always `10.0`.

---

## 2. Price Movement — Driven Entirely by In-App Trading

After the initial $10 IPO price, all price changes are driven by **in-app supply and demand** (user buy/sell transactions), not by YouTube metrics.

**Code reference:** `helpers/market.py` (trading engine)

YouTube view/like/comment counts are:
- Fetched and stored for **display purposes only**
- Used to calculate a simple `views_per_day` display stat (`pricing.py:calculate_display_stats`)
- **Never used as inputs to price calculations**

---

## 3. Daily Drop Selection — In-App Activity Only

The Daily Drop (featured videos) is selected by **transaction count within Clip Capital**, not by YouTube metrics.

**Code reference:** `scheduler.py:generate_daily_drop()`

```python
# Selection algorithm: rank by in-app transaction count (organic user interest).
# YouTube metrics (view_count, like_count, comment_count) are NOT used here —
# only the number of trades that happened inside Clip Capital.
scored = [(len(v.transactions), v) for v in videos]
```

---

## 4. What YouTube Data Is Used For

| YouTube Data Field | Used For | NOT Used For |
|---|---|---|
| `view_count` | Display only (views/day stat) | Pricing, ranking, selection |
| `like_count` | Display only | Pricing, ranking, selection |
| `comment_count` | Display only | Pricing, ranking, selection |
| `title` | Displaying video card | Any calculation |
| `thumbnail_url` | Displaying video card | Any calculation |
| `channel_name` | Attribution display | Any calculation |

---

## 5. OAuth Scope — Identity Only

The Google OAuth integration uses only:
- `openid`
- `https://www.googleapis.com/auth/userinfo.email`
- `https://www.googleapis.com/auth/userinfo.profile`

The `youtube.readonly` scope is **not requested**. All YouTube data is fetched server-side using `YOUTUBE_API_KEY` — a service API key — not via user OAuth tokens.

---

## 6. Data Retention

| Data | Retention |
|---|---|
| YouTube statistics | 30 days (auto-deleted by `cleanup_old_stats`) |
| Video metadata | 30 days if unused (`cleanup_inactive_videos`) |
| Audit logs | 90 days (`cleanup_old_audit_logs`) |

---

## 7. Public Transparency

Live API quota usage is publicly visible at `/api-usage` — no login required.
