"""Video upsert, price snaps and market feed helpers."""
from datetime import datetime

from sqlalchemy.orm import Session

import models
from pricing import calculate_price, calculate_ipo_price, calculate_display_stats


def get_channel_videos(db: Session, channel_id: str, exclude_youtube_id: str) -> list:
    others = (
        db.query(models.Video)
        .filter(models.Video.channel_id == channel_id, models.Video.youtube_id != exclude_youtube_id)
        .limit(20).all()
    )
    return [
        (v.stats[-1].view_count, v.published_at)
        for v in others
        if v.stats and v.stats[-1].view_count > 0
    ]


def upsert_video(db: Session, yt: dict) -> models.Video:
    video = db.query(models.Video).filter(models.Video.youtube_id == yt["youtube_id"]).first()
    if not video:
        is_ipo = (
            yt["published_at"] is not None and
            (datetime.utcnow() - yt["published_at"]).total_seconds() < 86400
        )
        ipo_price = calculate_ipo_price(yt["view_count"], yt["like_count"])
        video = models.Video(
            youtube_id=yt["youtube_id"], title=yt["title"],
            channel_name=yt["channel_name"], channel_id=yt.get("channel_id", ""),
            thumbnail_url=yt["thumbnail_url"], published_at=yt["published_at"],
            current_price=ipo_price,
            is_ipo=is_ipo,
            category=yt.get("category"),
        )
        db.add(video)
        db.flush()
    else:
        video.title         = yt["title"]
        video.channel_name  = yt["channel_name"]
        video.channel_id    = yt.get("channel_id", "")
        video.thumbnail_url = yt["thumbnail_url"]
        video.last_updated  = datetime.utcnow()
        if yt.get("category"):
            video.category = yt["category"]
    db.add(models.VideoStats(
        video_id=video.id, view_count=yt["view_count"], like_count=yt["like_count"],
        comment_count=yt["comment_count"], price_at_time=video.current_price,
    ))
    db.commit()
    db.refresh(video)
    return video


def record_price_snap(db: Session, video: models.Video):
    """Writes the current market price to VideoStats so the chart reflects trades."""
    last = video.stats[-1] if video.stats else None
    db.add(models.VideoStats(
        video_id=video.id,
        view_count=last.view_count if last else 0,
        like_count=last.like_count if last else 0,
        comment_count=last.comment_count if last else 0,
        price_at_time=video.current_price,
    ))


def compute_price_change_pct(video: models.Video) -> float:
    if not video.stats or len(video.stats) < 2:
        return 0.0
    first_price = video.stats[0].price_at_time
    if not first_price or first_price <= 0:
        return 0.0
    return round((video.current_price - first_price) / first_price * 100, 1)


def get_market_feed(db: Session, limit: int = 10) -> list:
    txs = (
        db.query(models.Transaction)
        .filter(models.Transaction.transaction_type.in_(["buy", "sell"]))
        .order_by(models.Transaction.executed_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for tx in txs:
        if not tx.user or not tx.video:
            continue
        result.append({
            "username": tx.user.username,
            "action": tx.transaction_type,
            "video_title": tx.video.title,
            "youtube_id": tx.video.youtube_id,
            "thumbnail_url": tx.video.thumbnail_url,
            "shares": round(tx.shares, 1),
            "price": round(tx.price_per_share, 2),
            "ts": tx.executed_at,
        })
    return result


def get_hidden_gems(video_data: list) -> list:
    gems = [
        item for item in video_data
        if item["video"].current_price <= 15.0 and item.get("holders", 0) <= 2
    ]
    gems.sort(key=lambda x: x["video"].current_price)
    return gems[:3]
