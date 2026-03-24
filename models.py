from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime
import random


XP_THRESHOLDS = [0, 100, 300, 600, 1000, 1500, 2500, 4000, 6000, 10000]
LEVEL_NAMES = [
    "Rookie", "Trader", "Analyst", "Investor", "Broker",
    "Fund Manager", "Hedge Fund", "Whale", "Market Maker", "Legend"
]

ACHIEVEMENTS = {
    "first_buy":    {"name": "Erste Investition", "icon": "🚀", "desc": "Dein erstes Video gekauft",              "xp": 20},
    "first_profit": {"name": "Im Gewinn",         "icon": "📈", "desc": "Einen Verkauf mit Gewinn abgeschlossen", "xp": 25},
    "diversified":  {"name": "Diversifiziert",    "icon": "📊", "desc": "3 verschiedene Videos im Portfolio",     "xp": 30},
    "whale":        {"name": "Wal",               "icon": "🐋", "desc": "50+ Anteile eines Videos gekauft",       "xp": 50},
    "trader_10":    {"name": "Aktiver Trader",    "icon": "💼", "desc": "10 Trades abgeschlossen",                "xp": 40},
    "daily_drop":   {"name": "Early Adopter",     "icon": "🎯", "desc": "Ersten Daily Drop gekauft",              "xp": 20},
    "streak_3":     {"name": "Auf Kurs",          "icon": "🔥", "desc": "3 Tage Streak erreicht",                 "xp": 15},
    "streak_7":     {"name": "Unaufhaltsam",      "icon": "⚡", "desc": "7 Tage Streak erreicht",                 "xp": 50},
    "diamond_hands":{"name": "Diamond Hands",     "icon": "💎", "desc": "Eine Position 7+ Tage gehalten",         "xp": 100},
    "level_5":      {"name": "Aufsteiger",        "icon": "⭐", "desc": "Level 5 erreicht",                       "xp": 75},
}

# ── Task-Pool pro Level-Bereich ────────────────────────────────────────────────
# Jedes Task-Dict: type, name, icon, desc, target
# {target} im desc wird durch die Zahl ersetzt

TASK_POOL = [
    # Level 1-2 (Einsteiger)
    {"type": "buy",        "min_level": 1, "max_level": 2, "target": 1,    "icon": "🛒", "name": "Erste Investition",  "desc": "Kaufe dein erstes Video"},
    {"type": "sell",       "min_level": 1, "max_level": 2, "target": 1,    "icon": "💸", "name": "Erste Verkauf",      "desc": "Verkaufe ein Video"},
    {"type": "streak",     "min_level": 1, "max_level": 2, "target": 2,    "icon": "🔥", "name": "2 Tage dabei",       "desc": "Melde dich 2 Tage in Folge an"},
    {"type": "watchlist",  "min_level": 1, "max_level": 2, "target": 1,    "icon": "★",  "name": "Beobachter",         "desc": "Füge ein Video zur Watchlist hinzu"},
    # Level 2-4 (Mittelstufe)
    {"type": "buy",        "min_level": 2, "max_level": 4, "target": 3,    "icon": "🛒", "name": "Einkaufstour",       "desc": "Kaufe 3 verschiedene Videos"},
    {"type": "sell",       "min_level": 2, "max_level": 4, "target": 3,    "icon": "💸", "name": "Händler",            "desc": "Verkaufe 3 mal"},
    {"type": "trades",     "min_level": 2, "max_level": 4, "target": 5,    "icon": "📊", "name": "Aktiver Trader",     "desc": "Mache 5 Trades"},
    {"type": "daily_drop", "min_level": 2, "max_level": 4, "target": 1,    "icon": "🎯", "name": "Early Bird",         "desc": "Kaufe einen Daily Drop"},
    {"type": "profit",     "min_level": 2, "max_level": 4, "target": 1,    "icon": "📈", "name": "Erster Gewinn",      "desc": "Erziele einen gewinnbringenden Verkauf"},
    {"type": "portfolio",  "min_level": 2, "max_level": 4, "target": 3,    "icon": "📁", "name": "Diversifiziert",     "desc": "Halte 3 Videos gleichzeitig"},
    {"type": "streak",     "min_level": 2, "max_level": 4, "target": 3,    "icon": "🔥", "name": "3 Tage Streak",      "desc": "Melde dich 3 Tage in Folge an"},
    {"type": "invest",     "min_level": 2, "max_level": 4, "target": 500,  "icon": "💵", "name": "Investor",           "desc": "Investiere insgesamt $500"},
    # Level 4-7 (Fortgeschritten)
    {"type": "trades",     "min_level": 4, "max_level": 7, "target": 10,   "icon": "📊", "name": "Viel Erfahrung",     "desc": "Mache 10 Trades"},
    {"type": "portfolio",  "min_level": 4, "max_level": 7, "target": 5,    "icon": "📁", "name": "Großes Portfolio",   "desc": "Halte 5 Videos gleichzeitig"},
    {"type": "profit",     "min_level": 4, "max_level": 7, "target": 3,    "icon": "📈", "name": "Gewinn-Serie",       "desc": "Erziele 3 gewinnbringende Verkäufe"},
    {"type": "invest",     "min_level": 4, "max_level": 7, "target": 2000, "icon": "💵", "name": "Großinvestor",       "desc": "Investiere insgesamt $2.000"},
    {"type": "streak",     "min_level": 4, "max_level": 7, "target": 7,    "icon": "🔥", "name": "7 Tage Streak",      "desc": "Melde dich 7 Tage in Folge an"},
    {"type": "daily_drop", "min_level": 4, "max_level": 7, "target": 3,    "icon": "🎯", "name": "Drop-Jäger",         "desc": "Kaufe 3 Daily Drops"},
    # Level 7-10 (Profi)
    {"type": "trades",     "min_level": 7, "max_level": 10, "target": 25,  "icon": "📊", "name": "Profi-Trader",       "desc": "Mache 25 Trades"},
    {"type": "portfolio",  "min_level": 7, "max_level": 10, "target": 8,   "icon": "📁", "name": "Mega-Portfolio",     "desc": "Halte 8 Videos gleichzeitig"},
    {"type": "profit",     "min_level": 7, "max_level": 10, "target": 10,  "icon": "📈", "name": "Gewinn-Profi",       "desc": "Erziele 10 gewinnbringende Verkäufe"},
    {"type": "invest",     "min_level": 7, "max_level": 10, "target": 5000,"icon": "💵", "name": "Millionär",          "desc": "Investiere insgesamt $5.000"},
    {"type": "streak",     "min_level": 7, "max_level": 10, "target": 14,  "icon": "🔥", "name": "14 Tage Streak",     "desc": "Melde dich 14 Tage in Folge an"},
    {"type": "daily_drop", "min_level": 7, "max_level": 10, "target": 7,   "icon": "🎯", "name": "Drop-König",         "desc": "Kaufe 7 Daily Drops"},
]


def generate_tasks_for_level(level: int) -> list:
    """Wählt 3 zufällige Tasks passend zum aktuellen Level aus."""
    eligible = [t for t in TASK_POOL if t["min_level"] <= level <= t["max_level"]]
    if len(eligible) < 3:
        eligible = TASK_POOL  # Fallback
    chosen = random.sample(eligible, min(3, len(eligible)))
    return chosen


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
    level = Column(Integer, default=1)
    streak_days = Column(Integer, default=0)
    last_login_date = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    holdings = relationship("Holding", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    achievements = relationship("UserAchievement", back_populates="user")
    tasks = relationship("UserTask", back_populates="user")


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
    transaction_type = Column(String)
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


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    achievement_id = Column(String, nullable=False)
    earned_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="achievements")


class UserTask(Base):
    __tablename__ = "user_tasks"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    task_type = Column(String, nullable=False)
    name = Column(String)
    icon = Column(String)
    desc = Column(String)
    target = Column(Integer, nullable=False)
    progress = Column(Integer, default=0)
    completed = Column(Boolean, default=False)
    level_assigned = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="tasks")


class DailyDrop(Base):
    __tablename__ = "daily_drops"

    id = Column(Integer, primary_key=True)
    video_id = Column(Integer, ForeignKey("videos.id"))
    date = Column(String, index=True)
    total_shares = Column(Float, default=100.0)
    shares_remaining = Column(Float, default=100.0)

    video = relationship("Video", back_populates="daily_drops")
