"""Background scheduler jobs and startup migrations."""
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

from apscheduler.schedulers.background import BackgroundScheduler

from database import SessionLocal, engine
import models
from helpers import (
    upsert_video, calc_total_portfolio_value, upsert_leaderboard,
    get_channel_videos,
)
from pricing import calculate_price  # used only in auto_refresh_prices for internal price snap
from youtube import get_video_details, get_stats_only, get_trending_videos


def migrate():
    """Adds missing columns to existing tables (safe for production)."""
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("users")]
    with engine.begin() as conn:
        if "tutorial_step" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN tutorial_step INTEGER DEFAULT 0"))
        if "display_name" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR"))
        if "bio" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN bio VARCHAR(160)"))
        if "avatar_emoji" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN avatar_emoji VARCHAR(8) DEFAULT '🐿️'"))
        if "avatar_color" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN avatar_color VARCHAR(7) DEFAULT '#FFB162'"))
        if "is_premium" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_premium BOOLEAN DEFAULT FALSE"))

    video_cols = [c["name"] for c in insp.get_columns("videos")]
    with engine.begin() as conn:
        if "category" not in video_cols:
            conn.execute(text("ALTER TABLE videos ADD COLUMN category VARCHAR"))

    # Neue Tabellen anlegen falls sie noch nicht existieren
    tables = insp.get_table_names()
    if "user_watchlists" not in tables or "portfolio_snapshots" not in tables:
        # SQLAlchemy erstellt fehlende Tabellen via create_all (bereits in main.py)
        pass

    # Backfill category for existing videos that have none
    _backfill_categories()


def _backfill_categories():
    """One-time: fetch categoryId for existing videos that have no category set."""
    db = SessionLocal()
    try:
        missing = db.query(models.Video).filter(models.Video.category == None).all()  # noqa: E711
        if not missing:
            return
        ids = [v.youtube_id for v in missing[:50]]  # max 1 batch at startup
        try:
            yt_items = get_video_details(ids)
        except Exception:
            log.warning("backfill_categories: YouTube API call failed", exc_info=True)
            return
        yt_map = {item["youtube_id"]: item.get("category") for item in yt_items}
        for v in missing[:50]:
            cat = yt_map.get(v.youtube_id)
            if cat:
                v.category = cat
        db.commit()
    except Exception:
        log.exception("backfill_categories: unexpected error")
    finally:
        db.close()


# ── scheduled jobs ─────────────────────────────────────────────────────────────

def auto_refresh_prices():
    """Refresh all market videos every 3h; also notify watchlist holders of big moves.

    DB-freshness check: skip any video updated within the last 30 minutes to avoid
    redundant API calls (e.g. if seed_market or a manual fetch ran recently).
    """
    db = SessionLocal()
    try:
        # Collect all market videos (not just held ones) so the market feels alive
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        all_videos = db.query(models.Video).order_by(models.Video.last_updated.desc()).limit(100).all()

        # Filter: skip videos that were already refreshed within the last 30 min
        stale_videos = [v for v in all_videos if not v.last_updated or v.last_updated < cutoff]
        fresh_videos = [v for v in all_videos if v.last_updated and v.last_updated >= cutoff]

        yt_ids = [v.youtube_id for v in stale_videos]

        # Snapshot prices for movement detection (all videos, including fresh ones)
        price_before = {v.youtube_id: v.current_price for v in all_videos}

        if not yt_ids:
            notify_watchlist_movers(db, price_before)
            snapshot_portfolio_values(db)
            return

        for i in range(0, len(yt_ids), 50):
            batch = yt_ids[i:i+50]
            try:
                # Use statistics-only part for refresh — we already have snippet metadata
                yt_list = get_stats_only(batch)
                for yt in yt_list:
                    upsert_video(db, yt)
            except Exception:
                log.warning("auto_refresh_prices: batch %s failed", batch, exc_info=True)

        # Send push notifications for watchlist videos that moved ±15%
        notify_watchlist_movers(db, price_before)

        # Record portfolio snapshots for all active users (persistent chart history)
        snapshot_portfolio_values(db)
    finally:
        db.close()


def snapshot_portfolio_values(db):
    """Schreibt alle 3h einen Portfolio-Snapshot für alle aktiven Nutzer.

    Nutzer gelten als aktiv wenn sie sich in den letzten 30 Tagen eingeloggt haben.
    Includes cash-only players so their chart isn't empty.
    """
    from helpers import calc_total_portfolio_value
    from datetime import timedelta
    try:
        cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        active_users = (
            db.query(models.User)
            .filter(models.User.last_login_date >= cutoff)
            .all()
        )
        for u in active_users:
            val = round(calc_total_portfolio_value(u), 2)
            db.add(models.PortfolioSnapshot(user_id=u.id, value=val))
        db.commit()
    except Exception:
        log.exception("snapshot_portfolio_values: unexpected error")


def notify_watchlist_movers(db, price_before: dict):
    """Push-benachrichtige Nutzer wenn ein Video auf ihrer Watchlist ±15% bewegt hat."""
    try:
        from routers.push import send_push_to_user, PushSubscription
        from sqlalchemy import distinct

        # Find videos that moved significantly
        moved_videos = []
        for youtube_id, old_price in price_before.items():
            if old_price <= 0:
                continue
            video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
            if not video:
                continue
            change_pct = (video.current_price - old_price) / old_price * 100
            if abs(change_pct) >= 15:
                moved_videos.append((video, change_pct))

        if not moved_videos:
            return

        for video, change_pct in moved_videos:
            # Find all users watching this video
            watchers = db.query(models.UserWatchlist).filter(
                models.UserWatchlist.youtube_id == video.youtube_id
            ).all()
            for watcher in watchers:
                direction = "▲" if change_pct > 0 else "▼"
                send_push_to_user(
                    watcher.user_id,
                    title=f"Kurs-Alarm {direction} {abs(change_pct):.0f}%",
                    body=f"{video.title[:50]} hat sich stark bewegt — jetzt handeln!",
                    url=f"/video/{video.youtube_id}",
                    db=db,
                )
    except Exception:
        log.exception("notify_watchlist_movers: unexpected error")


def generate_daily_drop():
    db = SessionLocal()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if db.query(models.DailyDrop).filter_by(date=today).count() > 0:
            return

        videos = db.query(models.Video).order_by(models.Video.last_updated.desc()).limit(50).all()
        channel_map: dict = defaultdict(list)
        for v in videos:
            if v.channel_id:
                channel_map[v.channel_id].append(v)

        # Select by raw view count — simple comparison, no derived scoring
        scored = [
            (v.stats[-1].view_count, v)
            for v in videos if v.stats
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        for video in [v for _, v in scored[:5]]:
            db.add(models.DailyDrop(
                video_id=video.id, date=today,
                total_shares=100.0, shares_remaining=100.0,
            ))
        db.commit()
    finally:
        db.close()


def seed_market():
    db = SessionLocal()
    try:
        if db.query(models.Video).count() >= 15:
            return
        try:
            yt_list = get_trending_videos(region="DE", max_results=20)
            for yt in yt_list:
                upsert_video(db, yt)
        except Exception:
            log.warning("seed_market: YouTube API call failed", exc_info=True)
    finally:
        db.close()


def resolve_hot_takes():
    db = SessionLocal()
    try:
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        takes = db.query(models.HotTake).filter_by(date=yesterday, resolved=False).all()
        for take in takes:
            if not take.video or not take.video.stats:
                take.resolved = True
                continue
            current_views = take.video.stats[-1].view_count
            went_up = current_views > take.views_at_prediction
            correct = (take.prediction == "up" and went_up) or (take.prediction == "down" and not went_up)
            take.resolved = True
            take.correct = correct
            user = db.query(models.User).filter_by(id=take.user_id).first()
            if user:
                user.xp = (user.xp or 0) + (25 if correct else 5)
        db.commit()
    finally:
        db.close()


def end_season():
    db = SessionLocal()
    try:
        season = db.query(models.Season).filter_by(active=True).first()
        if not season:
            return
        entries = db.query(models.SeasonEntry).filter_by(season_id=season.id).all()
        for entry in entries:
            user = db.query(models.User).filter_by(username=entry.username).first()
            if user:
                val = calc_total_portfolio_value(user)
                entry.end_value = val
                entry.return_pct = round((val - entry.start_value) / max(entry.start_value, 1) * 100, 2)

        ranked = sorted([e for e in entries if e.return_pct is not None],
                        key=lambda x: x.return_pct, reverse=True)
        badges = {1: "season_gold", 2: "season_silver", 3: "season_bronze"}
        for i, entry in enumerate(ranked[:3], 1):
            entry.rank = i
            user = db.query(models.User).filter_by(username=entry.username).first()
            if user:
                badge_id = f"{badges[i]}_s{season.season_number}"
                if not db.query(models.UserAchievement).filter_by(
                    user_id=user.id, achievement_id=badge_id
                ).first():
                    db.add(models.UserAchievement(user_id=user.id, achievement_id=badge_id))
                user.xp = (user.xp or 0) + [200, 100, 50][i - 1]

        season.active = False
        season.end_date = datetime.utcnow().strftime("%Y-%m-%d")
        db.commit()

        db.add(models.Season(
            season_number=season.season_number + 1,
            start_date=datetime.utcnow().strftime("%Y-%m-%d"),
            active=True,
        ))
        db.commit()
    finally:
        db.close()


def refresh_leaderboard():
    db = SessionLocal()
    try:
        for u in db.query(models.User).all():
            upsert_leaderboard(u.username, calc_total_portfolio_value(u), db)
    finally:
        db.close()


def cleanup_old_stats():
    """Deletes VideoStats older than 30 days (YouTube API data retention policy)."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=30)
    db = SessionLocal()
    try:
        deleted = db.query(models.VideoStats).filter(
            models.VideoStats.recorded_at < cutoff
        ).delete()
        db.commit()
        if deleted:
            print(f"[cleanup] Removed {deleted} VideoStats entries older than 30 days", flush=True)
    finally:
        db.close()


def cleanup_inactive_videos():
    """Deletes Video records that have had no holders for 30+ days (YouTube API data retention policy).

    YouTube API metadata (title, channel, thumbnail URL) must not be stored indefinitely
    for videos that are no longer actively used. A video qualifies for deletion when:
      - no user currently holds any shares in it, AND
      - it has not been updated in the last 30 days (i.e., nobody has interacted with it).
    """
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=30)
    db = SessionLocal()
    try:
        # Find videos with no current holdings and not updated in 30 days
        held_video_ids = db.query(models.Holding.video_id).distinct().subquery()
        inactive = (
            db.query(models.Video)
            .filter(
                models.Video.last_updated < cutoff,
                ~models.Video.id.in_(held_video_ids),
            )
            .all()
        )
        count = 0
        for video in inactive:
            # Cascade: remove related stats, watchlist entries, and daily drops first
            db.query(models.VideoStats).filter(models.VideoStats.video_id == video.id).delete()
            db.query(models.UserWatchlist).filter(models.UserWatchlist.youtube_id == video.youtube_id).delete()
            db.query(models.DailyDrop).filter(models.DailyDrop.video_id == video.id).delete()
            db.delete(video)
            count += 1
        db.commit()
        if count:
            print(f"[cleanup] Removed {count} inactive Video records (no holders, 30+ days old)", flush=True)
    finally:
        db.close()


# ── start ──────────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()
scheduler.add_job(auto_refresh_prices, "cron", hour="*/3", minute=0)
scheduler.add_job(generate_daily_drop, "cron", hour=0,  minute=5)
scheduler.add_job(seed_market,         "cron", hour=3,  minute=0)
scheduler.add_job(resolve_hot_takes,   "cron", hour=7,  minute=0)
scheduler.add_job(refresh_leaderboard, "cron", hour=8,  minute=0)
scheduler.add_job(end_season,          "cron", day_of_week="mon", hour=0, minute=10)
scheduler.add_job(cleanup_old_stats,      "cron", hour=4,  minute=0)
scheduler.add_job(cleanup_inactive_videos, "cron", hour=4,  minute=30)
scheduler.start()
