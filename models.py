from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


XP_THRESHOLDS = [0, 100, 300, 600, 1000, 1500, 2500, 4000, 6000, 10000]
LEVEL_NAMES = [
    "Rookie", "Trader", "Analyst", "Investor", "Broker",
    "Fund Manager", "Hedge Fund", "Whale", "Market Maker", "Legend"
]


def get_level_info(xp: int) -> dict:
    level = 1
    for i, threshold in enumerate(XP_THRESHOLDS):
        if xp >= threshold:
            level = i + 1
    level = min(level, 10)
    xp_current = XP_THRESHOLDS[level - 1]
    xp_next = XP_THRESHOLDS[level] if level < 10 else XP_THRESHOLDS[-1]
    progress = round((xp - xp_current) / max(xp_next - xp_current, 1) * 100, 1)
    return {
        "level": level,
        "name": LEVEL_NAMES[level - 1],
        "xp": xp,
        "xp_current": xp_current,
        "xp_next": xp_next,
        "progress": min(progress, 100),
    }


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    balance = Column(Float, default=10000.0)
    xp = Column(Integer, default=0)
    streak_days = Column(Integer, default=0)
    last_login_date = Column(String, nullable=True)  # "2026-03-24"
    created_at = Column(DateTime, default=datetime.utcnow)

    holdings = relationship("Holding", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True)
    youtube_id = Column(String, unique=True, nullable=False)
    title = Column(String)
    channel_name = Column(String)
    channel_id = Column(String, index=True)
    thumbnail_url = Column(String)
    published_at = Column(DateTime)
    current_price = Column(Float, default=10.0)
    added_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)

    stats = relationship("VideoStats", back_populates="video", order_by="VideoStats.recorded_at")
    holdings = relationship("Holding", back_populates="video")
    transactions = relationship("Transaction", back_populates="video")
    daily_drops = relationship("DailyDrop", back_populates="video")


class VideoStats(Base):
    __tablename__ = "video_stats"

    id = Column(Integer, primary_key=True)
    video_id = Column(Integer, ForeignKey("videos.id"))
    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    price_at_time = Column(Float)
    recorded_at = Column(DateTime, default=datetime.utcnow)

    video = relationship("Video", back_populates="stats")


class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    video_id = Column(Integer, ForeignKey("videos.id"))
    shares = Column(Float, default=0)
    avg_cost_basis = Column(Float, default=0)

    user = relationship("User", back_populates="holdings")
    video = relationship("Video", back_populates="holdings")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    video_id = Column(Integer, ForeignKey("videos.id"))
    transaction_type = Column(String)  # "buy" or "sell"
    shares = Column(Float)
    price_per_share = Column(Float)
    total_amount = Column(Float)
    executed_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="transactions")
    video = relationship("Video", back_populates="transactions")


class LeaderboardEntry(Base):
    __tablename__ = "leaderboard_entries"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    portfolio_value = Column(Float, nullable=False)
    return_pct = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)


class DailyDrop(Base):
    __tablename__ = "daily_drops"

    id = Column(Integer, primary_key=True)
    video_id = Column(Integer, ForeignKey("videos.id"))
    date = Column(String, index=True)          # "2026-03-24"
    total_shares = Column(Float, default=100.0)
    shares_remaining = Column(Float, default=100.0)

    video = relationship("Video", back_populates="daily_drops")
