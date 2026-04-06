# YouTube API Usage Justification

**App:** Clip Capital  
**Scope:** `youtube.readonly`  
**Date:** 2026-04-06

---

## Why We Need This Scope

Clip Capital is a gamified portfolio simulation where users collect YouTube videos and track their virtual value based on real public YouTube metrics (views, likes, comments). The `youtube.readonly` scope is used to:

1. Authenticate users via Google Sign-In
2. Access public video statistics to calculate in-game portfolio values

---

## Endpoints Used

| Endpoint | Parameters | Purpose | Quota Cost |
|---|---|---|---|
| `videos.list` | `part=snippet,statistics` | Video metadata + stats | 1 unit / 50 videos |
| `videos.list` | `part=statistics` | Stats refresh (scheduler) | 1 unit / 50 videos |
| `search.list` | `type=video, part=id` | User video search | 100 units / call |
| `videos.list` | `chart=mostPopular` | Daily trending market | 1 unit / call |

---

## Data NOT Accessed

We ONLY access public YouTube data. We do NOT:

- Access private channel data or subscriber lists
- Access user's own YouTube account data
- Upload, modify, or delete any YouTube content
- Access video captions, annotations, or reports
- Access user's watch history, playlists, or subscriptions
- Cache or download video files

---

## Quota Management

- Allocated: 10,000 units/day (standard YouTube API free tier)
- Estimated daily usage: ~200–500 units
- Cache: 30-minute in-memory / Redis cache prevents redundant calls
- Quota dashboard: `/admin/quota` (admin-only)
- Scheduler jobs only fetch stats for actively held videos

---

## Data Retention

| Data | Retention | Mechanism |
|---|---|---|
| Video statistics (views/likes/comments) | 30 days | `cleanup_old_stats` scheduler job, 04:00 UTC |
| Video metadata (title, thumbnail, channel) | 30 days (if unused) | `cleanup_inactive_videos`, 04:30 UTC |
| User data | Until account deletion | User-initiated or scheduled (30-day window) |

---

## User Consent

All users must explicitly:
1. Accept Privacy Policy and Terms of Service (checkbox at registration / OAuth consent page)
2. Grant consent via Google OAuth screen (shows scopes)

Users can revoke access at any time via:
- Google Account settings: https://myaccount.google.com/permissions
- Clip Capital dashboard: `/account` → "Konto löschen"
