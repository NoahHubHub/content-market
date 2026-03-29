"""Shared helper functions used across route modules."""
import bcrypt
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from models import get_level_info, ACHIEVEMENTS, generate_tasks_for_level
from pricing import calculate_price
from youtube import get_video_details

# ── XP rewards ─────────────────────────────────────────────────────────────────
XP_BUY         = 10
XP_SELL_PROFIT = 30
XP_SELL_LOSS   = 5
XP_DAILY_LOGIN = 5

# ── Portfolio-Slots ─────────────────────────────────────────────────────────────
BASE_FREE_SLOTS = 7

def get_max_portfolio_slots(db_user: models.User) -> int:
    """Berechnet max. Portfolio-Slots: Basis + Streak-Bonus für Free-Nutzer."""
    if db_user.is_premium:
        return 9999
    streak = db_user.streak_days or 0
    bonus = 0
    if streak >= 14:
        bonus = 3
    elif streak >= 7:
        bonus = 2
    elif streak >= 3:
        bonus = 1
    return BASE_FREE_SLOTS + bonus
STREAK_BONUS   = {3: 15, 7: 50, 14: 100, 30: 250}


# ── auth helpers ───────────────────────────────────────────────────────────────

def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_pw(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


class UserCtx:
    """Template-friendly wrapper around models.User with computed portfolio stats."""
    def __init__(self, db_user: models.User):
        active = [h for h in db_user.holdings if h.shares > 0.001]
        self.id              = db_user.id
        self.username        = db_user.username
        self.balance         = db_user.balance
        self.holdings_count  = len(active)
        self.cost_basis      = round(sum(h.shares * h.avg_cost_basis for h in active), 2)
        market_value         = round(sum(h.shares * h.video.current_price for h in active if h.video), 2)
        self.estimated_value = round(db_user.balance + market_value, 2)
        self.xp              = db_user.xp or 0
        self.level_info      = get_level_info(self.xp)
        self.level           = db_user.level or 1
        self.streak_days     = db_user.streak_days or 0
        self.tutorial_step   = db_user.tutorial_step if db_user.tutorial_step is not None else 0
        self.display_name    = db_user.display_name or db_user.username
        self.bio             = db_user.bio or ""
        self.avatar_emoji    = db_user.avatar_emoji or "🐿️"
        self.avatar_color    = db_user.avatar_color or "#FFB162"
        self.is_premium      = bool(db_user.is_premium)


def get_login(request: Request, db: Session) -> Optional[models.User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(models.User).filter(models.User.id == uid).first()


def get_portfolio(db_user: models.User) -> dict:
    return {
        h.video.youtube_id: {"shares": h.shares, "avg_cost": h.avg_cost_basis}
        for h in db_user.holdings
        if h.shares > 0.001 and h.video
    }


# ── video helpers ──────────────────────────────────────────────────────────────

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
    channel_vids = get_channel_videos(db, yt.get("channel_id", ""), yt["youtube_id"])
    price_data = calculate_price(
        view_count=yt["view_count"], like_count=yt["like_count"],
        comment_count=yt["comment_count"], published_at=yt["published_at"],
        channel_videos=channel_vids,
    )
    if not video:
        is_ipo = (
            yt["published_at"] is not None and
            (datetime.utcnow() - yt["published_at"]).total_seconds() < 86400
        )
        video = models.Video(
            youtube_id=yt["youtube_id"], title=yt["title"],
            channel_name=yt["channel_name"], channel_id=yt.get("channel_id", ""),
            thumbnail_url=yt["thumbnail_url"], published_at=yt["published_at"],
            current_price=price_data["price"],
            is_ipo=is_ipo,
        )
        db.add(video)
        db.flush()
    else:
        video.title         = yt["title"]
        video.channel_name  = yt["channel_name"]
        video.channel_id    = yt.get("channel_id", "")
        video.thumbnail_url = yt["thumbnail_url"]
        video.current_price = price_data["price"]
        video.last_updated  = datetime.utcnow()
    db.add(models.VideoStats(
        video_id=video.id, view_count=yt["view_count"], like_count=yt["like_count"],
        comment_count=yt["comment_count"], price_at_time=price_data["price"],
    ))
    db.commit()
    db.refresh(video)
    return video


# ── portfolio helpers ──────────────────────────────────────────────────────────

def calc_total_portfolio_value(db_user: models.User) -> float:
    total = db_user.balance
    for h in db_user.holdings:
        if h.shares > 0.001 and h.video:
            total += h.shares * h.video.current_price
    return round(total, 2)


def upsert_leaderboard(username: str, portfolio_value: float, db: Session):
    return_pct = round((portfolio_value - 10000) / 10000 * 100, 2)
    entry = db.query(models.LeaderboardEntry).filter_by(username=username).first()
    if entry:
        entry.portfolio_value = portfolio_value
        entry.return_pct      = return_pct
        entry.recorded_at     = datetime.utcnow()
    else:
        db.add(models.LeaderboardEntry(
            username=username, portfolio_value=portfolio_value, return_pct=return_pct
        ))
    db.commit()


def record_port_snap(request: Request, db_user: models.User, db: Session = None):
    active = [h for h in db_user.holdings if h.shares > 0.001 and h.video]
    market_value = round(sum(h.shares * h.video.current_price for h in active), 2)
    # Persist to DB so history survives session expiry
    if db is not None:
        db.add(models.PortfolioSnapshot(user_id=db_user.id, value=market_value))
        db.commit()
    # Keep session fallback for backwards compat
    snaps = request.session.get("port_snaps", [])
    snaps.append({"ts": datetime.utcnow().strftime("%d.%m %H:%M"), "v": market_value})
    request.session["port_snaps"] = snaps[-50:]


# ── league helpers ─────────────────────────────────────────────────────────────

def log_league_activity(db: Session, db_user: models.User, action: str,
                        video: models.Video, shares: float, price: float):
    memberships = db.query(models.LeagueMember).filter_by(user_id=db_user.id).all()
    for m in memberships:
        db.add(models.LeagueActivity(
            league_id=m.league_id,
            user_id=db_user.id,
            username=db_user.username,
            action=action,
            video_title=video.title,
            youtube_id=video.youtube_id,
            shares=round(shares, 4),
            price=round(price, 2),
        ))


def _build_league_board(league: models.League, db: Session) -> list:
    member_ids = [m.user_id for m in league.members]
    users_by_id = (
        {u.id: u for u in db.query(models.User).filter(models.User.id.in_(member_ids)).all()}
        if member_ids else {}
    )
    board = []
    for m in league.members:
        u = users_by_id.get(m.user_id)
        if not u:
            continue
        current = calc_total_portfolio_value(u)
        ret = round((current - m.start_value) / max(m.start_value, 1) * 100, 2)
        board.append({
            "username": m.username,
            "start_value": m.start_value,
            "current_value": round(current, 2),
            "return_pct": ret,
            "joined_at": m.joined_at,
        })
    board.sort(key=lambda x: x["return_pct"], reverse=True)
    return board


def get_user_leagues_preview(db_user: models.User, db: Session) -> list:
    memberships = db.query(models.LeagueMember).filter_by(user_id=db_user.id).limit(3).all()
    result = []
    my_val = calc_total_portfolio_value(db_user)
    for m in memberships:
        league = m.league
        ret = round((my_val - m.start_value) / max(m.start_value, 1) * 100, 2)
        board = _build_league_board(league, db)
        my_rank = next((i + 1 for i, e in enumerate(board) if e["username"] == db_user.username), None)
        activities = sorted(league.activities, key=lambda a: a.created_at, reverse=True)
        latest = next((a for a in activities if a.user_id != db_user.id), None)
        result.append({
            "league": league, "my_return": ret, "my_rank": my_rank,
            "latest_activity": latest,
        })
    return result


# ── task system ────────────────────────────────────────────────────────────────

def ensure_tasks(db_user: models.User, db: Session):
    active = [t for t in db_user.tasks if not t.completed and t.level_assigned == (db_user.level or 1)]
    if not active:
        _assign_new_tasks(db_user, db)


def _assign_new_tasks(db_user: models.User, db: Session):
    level = db_user.level or 1
    chosen = generate_tasks_for_level(level)
    for t in chosen:
        db.add(models.UserTask(
            user_id=db_user.id,
            task_type=t["type"],
            name=t["name"],
            icon=t["icon"],
            desc=t["desc"],
            target=t["target"],
            progress=0,
            completed=False,
            level_assigned=level,
        ))
    db.commit()
    db.refresh(db_user)


def update_tasks(db_user: models.User, db: Session, event: str, value: int = 1) -> bool:
    active_tasks = [t for t in db_user.tasks
                    if not t.completed and t.level_assigned == (db_user.level or 1)
                    and t.task_type == event]
    for task in active_tasks:
        task.progress = min(task.progress + value, task.target)
        if task.progress >= task.target:
            task.completed = True
    db.commit()
    db.refresh(db_user)
    all_tasks = [t for t in db_user.tasks if t.level_assigned == (db_user.level or 1)]
    if all_tasks and all(t.completed for t in all_tasks):
        return _level_up(db_user, db)
    return False


def _level_up(db_user: models.User, db: Session) -> bool:
    db_user.level = (db_user.level or 1) + 1
    db_user.xp = (db_user.xp or 0) + 200
    db.commit()
    _assign_new_tasks(db_user, db)
    return True


def get_current_tasks(db_user: models.User, db: Session) -> list:
    ensure_tasks(db_user, db)
    return [t for t in db_user.tasks if t.level_assigned == (db_user.level or 1)]


# ── achievements ───────────────────────────────────────────────────────────────

def check_achievements(db_user: models.User, db: Session) -> list:
    from datetime import timedelta
    earned_ids = {a.achievement_id for a in db_user.achievements}
    newly_earned = []

    def unlock(aid: str):
        if aid not in earned_ids:
            a = ACHIEVEMENTS[aid]
            db.add(models.UserAchievement(user_id=db_user.id, achievement_id=aid))
            db_user.xp = (db_user.xp or 0) + a["xp"]
            earned_ids.add(aid)
            newly_earned.append(aid)

    total_trades = len(db_user.transactions)
    if total_trades >= 1:
        unlock("first_buy")
    if total_trades >= 10:
        unlock("trader_10")

    active_holdings = [h for h in db_user.holdings if h.shares > 0.001]
    if len(active_holdings) >= 3:
        unlock("diversified")

    for h in active_holdings:
        if h.shares >= 50:
            unlock("whale")
            break

    sell_txs = [t for t in db_user.transactions if t.transaction_type == "sell"]
    for t in sell_txs:
        buy_txs = [b for b in db_user.transactions
                   if b.transaction_type == "buy" and b.video_id == t.video_id
                   and b.executed_at < t.executed_at]
        if buy_txs:
            avg_buy = sum(b.price_per_share for b in buy_txs) / len(buy_txs)
            if t.price_per_share > avg_buy:
                unlock("first_profit")
                break

    buy_by_video: dict = {}
    for t in db_user.transactions:
        if t.transaction_type == "buy":
            if t.video_id not in buy_by_video or t.executed_at < buy_by_video[t.video_id]:
                buy_by_video[t.video_id] = t.executed_at
    for h in active_holdings:
        first_buy_at = buy_by_video.get(h.video_id)
        if first_buy_at and (datetime.utcnow() - first_buy_at).days >= 7:
            unlock("diamond_hands")
            break

    if (db_user.streak_days or 0) >= 3:
        unlock("streak_3")
    if (db_user.streak_days or 0) >= 7:
        unlock("streak_7")

    if get_level_info(db_user.xp or 0)["level"] >= 5:
        unlock("level_5")

    for t in db_user.transactions:
        if t.transaction_type == "buy":
            drop = db.query(models.DailyDrop).filter_by(video_id=t.video_id).first()
            if drop:
                unlock("daily_drop")
                break

    if newly_earned:
        db.commit()
    return newly_earned


# ── game helpers ───────────────────────────────────────────────────────────────

def get_todays_drops(db: Session) -> list:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    drops = db.query(models.DailyDrop).filter_by(date=today).all()
    result = []
    for drop in drops:
        if not drop.video or not drop.video.stats:
            continue
        last = drop.video.stats[-1]
        prev = drop.video.stats[-2].view_count if len(drop.video.stats) >= 2 else None
        channel_vids = get_channel_videos(db, drop.video.channel_id or "", drop.video.youtube_id)
        info = calculate_price(
            last.view_count, last.like_count, last.comment_count, drop.video.published_at,
            channel_videos=channel_vids, prev_view_count=prev,
        )
        result.append({"drop": drop, "video": drop.video, "info": info})
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


# ── Market Feed & Hidden Gems ──────────────────────────────────────────────────

def get_market_feed(db: Session, limit: int = 10) -> list:
    """Letzte globale Transaktionen für den Market-Activity-Feed."""
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
    """Videos mit hohem Momentum aber wenigen Investoren — Hidden Gems."""
    gems = [
        item for item in video_data
        if item["info"].get("momentum_pct", 0) >= 8 and item.get("holders", 0) <= 2
    ]
    gems.sort(key=lambda x: x["info"]["momentum_pct"], reverse=True)
    return gems[:3]


# ── Watchlist DB-Sync ──────────────────────────────────────────────────────────

def sync_watchlist_to_db(user_id: int, youtube_id: str, add: bool, db: Session):
    """Watchlist-Änderung in der DB persistieren (für Push-Notifications)."""
    existing = db.query(models.UserWatchlist).filter_by(
        user_id=user_id, youtube_id=youtube_id
    ).first()
    if add and not existing:
        db.add(models.UserWatchlist(user_id=user_id, youtube_id=youtube_id))
        db.commit()
    elif not add and existing:
        db.delete(existing)
        db.commit()


def get_user_active_duels(db_user: models.User, db: Session) -> list:
    """Aktive Duelle des Nutzers für den Home-Screen-Teaser."""
    from datetime import datetime as _dt
    today = _dt.utcnow().strftime("%Y-%m-%d")
    active = []
    for d in db_user.duels_sent + db_user.duels_received:
        if d.status != "active":
            continue
        opponent = d.opponent if d.challenger_id == db_user.id else d.challenger
        is_challenger = d.challenger_id == db_user.id
        my_start  = d.challenger_start if is_challenger else d.opponent_start
        opp_start = d.opponent_start   if is_challenger else d.challenger_start
        my_val    = calc_total_portfolio_value(db_user)
        opp_val   = calc_total_portfolio_value(opponent)
        my_ret    = (my_val  - my_start)  / max(my_start, 1)  * 100
        opp_ret   = (opp_val - opp_start) / max(opp_start, 1) * 100
        days_left = None
        try:
            end_dt = _dt.strptime(d.end_date, "%Y-%m-%d")
            days_left = max(0, (end_dt - _dt.utcnow()).days)
        except Exception:
            pass
        active.append({
            "opponent_username": opponent.username,
            "my_return":  round(my_ret, 1),
            "opp_return": round(opp_ret, 1),
            "leading": my_ret >= opp_ret,
            "end_date": d.end_date,
            "days_left": days_left,
        })
    return active[:2]  # max 2 im Teaser
