"""Background scheduler jobs and startup migrations."""
import os
from collections import defaultdict
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from database import SessionLocal, engine
import models
from helpers import (
    upsert_video, calc_total_portfolio_value, upsert_leaderboard,
    get_channel_videos,
)
from pricing import calculate_price
from youtube import get_video_details, get_trending_videos


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


# ── scheduled jobs ─────────────────────────────────────────────────────────────

def auto_refresh_prices():
    db = SessionLocal()
    try:
        active = db.query(models.Holding).filter(models.Holding.shares > 0.001).all()
        yt_ids = list({h.video.youtube_id for h in active if h.video})
        if not yt_ids:
            return
        for i in range(0, len(yt_ids), 50):
            batch = yt_ids[i:i+50]
            try:
                yt_list = get_video_details(batch)
                for yt in yt_list:
                    upsert_video(db, yt)
            except Exception:
                pass
    finally:
        db.close()


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

        scored = []
        for v in videos:
            if not v.stats:
                continue
            last = v.stats[-1]
            prev = v.stats[-2].view_count if len(v.stats) >= 2 else None
            channel_vids = [
                (other.stats[-1].view_count, other.published_at)
                for other in channel_map[v.channel_id]
                if other.youtube_id != v.youtube_id and other.stats and other.stats[-1].view_count > 0
            ]
            info = calculate_price(
                last.view_count, last.like_count, last.comment_count, v.published_at,
                channel_videos=channel_vids, prev_view_count=prev,
            )
            scored.append((info["momentum_pct"], v))

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
            pass
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


# ── start ──────────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()
scheduler.add_job(auto_refresh_prices, "cron", hour=6,  minute=0)
scheduler.add_job(generate_daily_drop, "cron", hour=0,  minute=5)
scheduler.add_job(seed_market,         "cron", hour=3,  minute=0)
scheduler.add_job(resolve_hot_takes,   "cron", hour=7,  minute=0)
scheduler.add_job(refresh_leaderboard, "cron", hour=8,  minute=0)
scheduler.add_job(end_season,          "cron", day_of_week="mon", hour=0, minute=10)
scheduler.start()
