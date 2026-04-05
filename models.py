from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime
import random


XP_THRESHOLDS = [0, 100, 300, 600, 1000, 1500, 2500, 4000, 6000, 10000]
LEVEL_NAMES = [
    "Rookie", "Entdecker", "Kurator", "Sammler", "Kenner",
    "Experte", "Profi", "Star Collector", "Community Hub", "Legend"
]

ACHIEVEMENTS = {
    "first_buy":    {"name": "Erste Sammlung",    "icon": "🚀", "desc": "Dein erstes Video gesammelt",            "xp": 20},
    "first_profit": {"name": "Wert gestiegen",    "icon": "📈", "desc": "Ein Video mit Wertzuwachs entfernt",     "xp": 25},
    "diversified":  {"name": "Kuratiert",         "icon": "📊", "desc": "3 verschiedene Videos in der Kollektion","xp": 30},
    "whale":        {"name": "Superfan",          "icon": "🐋", "desc": "50+ Units eines Videos gesammelt",       "xp": 50},
    "trader_10":    {"name": "Aktiver Sammler",   "icon": "💼", "desc": "10 Aktionen abgeschlossen",              "xp": 40},
    "daily_drop":   {"name": "Early Adopter",     "icon": "🎯", "desc": "Ersten Hot Drop gesammelt",              "xp": 20},
    "streak_3":     {"name": "Auf Kurs",          "icon": "🔥", "desc": "3 Tage Streak erreicht",                 "xp": 15},
    "streak_7":     {"name": "Unaufhaltsam",      "icon": "⚡", "desc": "7 Tage Streak erreicht",                 "xp": 50},
    "diamond_hands":{"name": "Loyaler Supporter", "icon": "💎", "desc": "Ein Video 7+ Tage in der Kollektion",    "xp": 100},
    "level_5":      {"name": "Aufsteiger",        "icon": "⭐", "desc": "Level 5 erreicht",                       "xp": 75},
}

# ── Task-Pool pro Level-Bereich ────────────────────────────────────────────────
# Jedes Task-Dict: type, name, icon, desc, target
# {target} im desc wird durch die Zahl ersetzt

TASK_POOL = [
    # Level 1-2 (Einsteiger)
    {"type": "buy",        "min_level": 1, "max_level": 2, "target": 1,    "icon": "🛒", "name": "Erste Sammlung",     "desc": "Sammle dein erstes Video"},
    {"type": "sell",       "min_level": 1, "max_level": 2, "target": 1,    "icon": "💸", "name": "Erste Entfernung",   "desc": "Entferne ein Video aus deiner Kollektion"},
    {"type": "streak",     "min_level": 1, "max_level": 2, "target": 2,    "icon": "🔥", "name": "2 Tage dabei",       "desc": "Melde dich 2 Tage in Folge an"},
    {"type": "watchlist",  "min_level": 1, "max_level": 2, "target": 1,    "icon": "★",  "name": "Beobachter",         "desc": "Füge ein Video zur Watchlist hinzu"},
    # Level 2-4 (Mittelstufe)
    {"type": "buy",        "min_level": 2, "max_level": 4, "target": 3,    "icon": "🛒", "name": "Sammeltour",         "desc": "Sammle 3 verschiedene Videos"},
    {"type": "sell",       "min_level": 2, "max_level": 4, "target": 3,    "icon": "💸", "name": "Aktiv dabei",        "desc": "Entferne 3 Videos aus deiner Kollektion"},
    {"type": "trades",     "min_level": 2, "max_level": 4, "target": 5,    "icon": "📊", "name": "Aktiver Sammler",    "desc": "Führe 5 Aktionen durch"},
    {"type": "daily_drop", "min_level": 2, "max_level": 4, "target": 1,    "icon": "🎯", "name": "Early Bird",         "desc": "Sammle einen Hot Drop"},
    {"type": "profit",     "min_level": 2, "max_level": 4, "target": 1,    "icon": "📈", "name": "Erster Wertzuwachs", "desc": "Entferne ein Video mit Wertzuwachs"},
    {"type": "portfolio",  "min_level": 2, "max_level": 4, "target": 3,    "icon": "📁", "name": "Kuratiert",          "desc": "Halte 3 Videos gleichzeitig in der Kollektion"},
    {"type": "streak",     "min_level": 2, "max_level": 4, "target": 3,    "icon": "🔥", "name": "3 Tage Streak",      "desc": "Melde dich 3 Tage in Folge an"},
    {"type": "invest",     "min_level": 2, "max_level": 4, "target": 500,  "icon": "💵", "name": "Engagiert",          "desc": "Setze insgesamt $500 Budget ein"},
    # Level 4-7 (Fortgeschritten)
    {"type": "trades",     "min_level": 4, "max_level": 7, "target": 10,   "icon": "📊", "name": "Viel Erfahrung",     "desc": "Führe 10 Aktionen durch"},
    {"type": "portfolio",  "min_level": 4, "max_level": 7, "target": 5,    "icon": "📁", "name": "Große Kollektion",   "desc": "Halte 5 Videos gleichzeitig in der Kollektion"},
    {"type": "profit",     "min_level": 4, "max_level": 7, "target": 3,    "icon": "📈", "name": "Wertzuwachs-Serie",  "desc": "Entferne 3 Videos mit Wertzuwachs"},
    {"type": "invest",     "min_level": 4, "max_level": 7, "target": 2000, "icon": "💵", "name": "Super-Engagiert",    "desc": "Setze insgesamt $2.000 Budget ein"},
    {"type": "streak",     "min_level": 4, "max_level": 7, "target": 7,    "icon": "🔥", "name": "7 Tage Streak",      "desc": "Melde dich 7 Tage in Folge an"},
    {"type": "daily_drop", "min_level": 4, "max_level": 7, "target": 3,    "icon": "🎯", "name": "Drop-Jäger",         "desc": "Sammle 3 Hot Drops"},
    # Level 7-10 (Profi)
    {"type": "trades",     "min_level": 7, "max_level": 10, "target": 25,  "icon": "📊", "name": "Profi-Sammler",      "desc": "Führe 25 Aktionen durch"},
    {"type": "portfolio",  "min_level": 7, "max_level": 10, "target": 8,   "icon": "📁", "name": "Mega-Kollektion",    "desc": "Halte 8 Videos gleichzeitig in der Kollektion"},
    {"type": "profit",     "min_level": 7, "max_level": 10, "target": 10,  "icon": "📈", "name": "Wertzuwachs-Profi",  "desc": "Entferne 10 Videos mit Wertzuwachs"},
    {"type": "invest",     "min_level": 7, "max_level": 10, "target": 5000,"icon": "💵", "name": "Budget-Profi",       "desc": "Setze insgesamt $5.000 Budget ein"},
    {"type": "streak",     "min_level": 7, "max_level": 10, "target": 14,  "icon": "🔥", "name": "14 Tage Streak",     "desc": "Melde dich 14 Tage in Folge an"},
    {"type": "daily_drop", "min_level": 7, "max_level": 10, "target": 7,   "icon": "🎯", "name": "Drop-König",         "desc": "Sammle 7 Hot Drops"},
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
    last_bonus_date = Column(String, nullable=True)
    tutorial_step = Column(Integer, default=0)  # 0-4 aktiv, 99=fertig
    created_at = Column(DateTime, default=datetime.utcnow)
    # Profile customization
    display_name  = Column(String, nullable=True)
    bio           = Column(String(160), nullable=True)
    avatar_emoji  = Column(String(8), default="🐿️")
    avatar_color  = Column(String(7), default="#FFB162")
    # Subscription
    is_premium    = Column(Boolean, default=False)
    # Login security
    failed_login_attempts = Column(Integer, default=0)
    locked_until          = Column(DateTime, nullable=True)

    holdings = relationship("Holding", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    achievements = relationship("UserAchievement", back_populates="user")
    tasks = relationship("UserTask", back_populates="user")
    hot_takes = relationship("HotTake", back_populates="user")
    duels_sent = relationship("Duel", foreign_keys="Duel.challenger_id", back_populates="challenger")
    duels_received = relationship("Duel", foreign_keys="Duel.opponent_id", back_populates="opponent")
    league_memberships = relationship("LeagueMember", back_populates="user")


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
    is_ipo = Column(Boolean, default=False)
    category = Column(String, nullable=True)   # e.g. "Gaming", "Music", "Education"
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


class HotTake(Base):
    __tablename__ = "hot_takes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    video_id = Column(Integer, ForeignKey("videos.id"))
    date = Column(String, index=True)          # "2026-03-24"
    prediction = Column(String)                # "up" oder "down"
    views_at_prediction = Column(Integer, default=0)
    resolved = Column(Boolean, default=False)
    correct = Column(Boolean, nullable=True)

    user = relationship("User", back_populates="hot_takes")
    video = relationship("Video")


class Season(Base):
    __tablename__ = "seasons"

    id = Column(Integer, primary_key=True)
    season_number = Column(Integer, unique=True)
    start_date = Column(String)
    end_date = Column(String, nullable=True)
    active = Column(Boolean, default=True)

    entries = relationship("SeasonEntry", back_populates="season")


class SeasonEntry(Base):
    __tablename__ = "season_entries"

    id = Column(Integer, primary_key=True)
    season_id = Column(Integer, ForeignKey("seasons.id"))
    username = Column(String, index=True)
    start_value = Column(Float, default=10000.0)
    end_value = Column(Float, nullable=True)
    return_pct = Column(Float, nullable=True)
    rank = Column(Integer, nullable=True)

    season = relationship("Season", back_populates="entries")


class Duel(Base):
    __tablename__ = "duels"

    id = Column(Integer, primary_key=True)
    challenger_id = Column(Integer, ForeignKey("users.id"))
    opponent_id = Column(Integer, ForeignKey("users.id"))
    start_date = Column(String)
    end_date = Column(String)
    challenger_start = Column(Float)
    opponent_start = Column(Float)
    status = Column(String, default="active")  # active, completed
    winner_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    challenger = relationship("User", foreign_keys=[challenger_id], back_populates="duels_sent")
    opponent   = relationship("User", foreign_keys=[opponent_id],   back_populates="duels_received")


# ── Liga-System ────────────────────────────────────────────────────────────────

class League(Base):
    __tablename__ = "leagues"

    id          = Column(Integer, primary_key=True)
    name        = Column(String, nullable=False)
    invite_code = Column(String, unique=True, nullable=False, index=True)
    creator_id  = Column(Integer, ForeignKey("users.id"))
    created_at  = Column(DateTime, default=datetime.utcnow)

    creator    = relationship("User", foreign_keys=[creator_id])
    members    = relationship("LeagueMember",  back_populates="league", cascade="all, delete-orphan")
    activities = relationship("LeagueActivity", back_populates="league", cascade="all, delete-orphan")


class LeagueMember(Base):
    __tablename__ = "league_members"

    id          = Column(Integer, primary_key=True)
    league_id   = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    user_id     = Column(Integer, ForeignKey("users.id"),   nullable=False)
    username    = Column(String, nullable=False)
    start_value = Column(Float,  nullable=False)
    joined_at   = Column(DateTime, default=datetime.utcnow)

    league = relationship("League",    back_populates="members")
    user   = relationship("User",      back_populates="league_memberships")


class LeagueActivity(Base):
    __tablename__ = "league_activities"

    id          = Column(Integer, primary_key=True)
    league_id   = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    user_id     = Column(Integer, ForeignKey("users.id"),   nullable=False)
    username    = Column(String,  nullable=False)
    action      = Column(String,  nullable=False)   # "buy" | "sell"
    video_title = Column(String)
    youtube_id  = Column(String)
    shares      = Column(Float)
    price       = Column(Float)
    created_at  = Column(DateTime, default=datetime.utcnow)

    league = relationship("League", back_populates="activities")


class UserWatchlist(Base):
    """DB-backed watchlist so the scheduler can send push notifications."""
    __tablename__ = "user_watchlists"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    youtube_id = Column(String, nullable=False, index=True)
    added_at   = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class PortfolioSnapshot(Base):
    """Persistente Portfolio-Verlaufsdaten — überleben Session-Ablauf."""
    __tablename__ = "portfolio_snapshots"

    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    value        = Column(Float, nullable=False)
    recorded_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")


class AuditLog(Base):
    """Protokolliert sicherheitsrelevante Aktionen für Debugging und Compliance."""
    __tablename__ = "audit_logs"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username   = Column(String, nullable=True)   # bleibt erhalten nach Account-Löschung
    action     = Column(String, nullable=False)  # login | login_failed | logout | password_change | account_delete
    ip_address = Column(String, nullable=True)
    timestamp  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
