"""Season, drops, hot-takes and watchlist helpers."""
from datetime import datetime

from sqlalchemy.orm import Session

import models
from helpers.portfolio import calc_total_portfolio_value
from helpers.video import compute_price_change_pct
from pricing import calculate_display_stats


def get_todays_drops(db: Session) -> list:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    drops = db.query(models.DailyDrop).filter_by(date=today).all()
    result = []
    for drop in drops:
        if not drop.video or not drop.video.stats:
            continue
        last = drop.video.stats[-1]
        info = calculate_display_stats(last.view_count, drop.video.published_at)
        result.append({
            "drop": drop,
            "video": drop.video,
            "info": info,
            "price_change_pct": compute_price_change_pct(drop.video),
        })
    return result


def get_hot_take_video(db: Session) -> models.Video:
    import hashlib
    today = datetime.utcnow().strftime("%Y-%m-%d")
    videos = db.query(models.Video).filter(models.Video.stats.any()).all()
    if not videos:
        return None
    idx = int(hashlib.md5(today.encode()).hexdigest(), 16) % len(videos)
    return videos[idx]


def get_or_create_season(db: Session) -> models.Season:
    season = db.query(models.Season).filter_by(active=True).first()
    if not season:
        last = db.query(models.Season).order_by(models.Season.season_number.desc()).first()
        num = (last.season_number + 1) if last else 1
        season = models.Season(
            season_number=num,
            start_date=datetime.utcnow().strftime("%Y-%m-%d"),
            active=True,
        )
        db.add(season)
        db.commit()
        db.refresh(season)
    return season


def ensure_season_entry(db_user: models.User, db: Session):
    season = get_or_create_season(db)
    entry = db.query(models.SeasonEntry).filter_by(
        season_id=season.id, username=db_user.username
    ).first()
    if not entry:
        portfolio_val = calc_total_portfolio_value(db_user)
        db.add(models.SeasonEntry(
            season_id=season.id, username=db_user.username,
            start_value=portfolio_val,
        ))
        db.commit()


def sync_watchlist_to_db(user_id: int, youtube_id: str, add: bool, db: Session):
    existing = db.query(models.UserWatchlist).filter_by(
        user_id=user_id, youtube_id=youtube_id
    ).first()
    if add and not existing:
        db.add(models.UserWatchlist(user_id=user_id, youtube_id=youtube_id))
        db.commit()
    elif not add and existing:
        db.delete(existing)
        db.commit()
