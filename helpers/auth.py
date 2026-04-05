"""Auth & user context helpers."""
import bcrypt
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

import models
from models import get_level_info

# ── Portfolio-Slots ────────────────────────────────────────────────────────────
BASE_FREE_SLOTS = 7


def get_max_portfolio_slots(db_user: models.User) -> int:
    if db_user.is_premium:
        return 9999
    streak = db_user.streak_days or 0
    if streak >= 14:
        bonus = 3
    elif streak >= 7:
        bonus = 2
    elif streak >= 3:
        bonus = 1
    else:
        bonus = 0
    return BASE_FREE_SLOTS + bonus


# ── Auth ───────────────────────────────────────────────────────────────────────

def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_pw(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def get_login(request: Request, db: Session) -> Optional[models.User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(models.User).filter(models.User.id == uid).first()


# ── User context ───────────────────────────────────────────────────────────────

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


def get_portfolio(db_user: models.User) -> dict:
    return {
        h.video.youtube_id: {"shares": h.shares, "avg_cost": h.avg_cost_basis}
        for h in db_user.holdings
        if h.shares > 0.001 and h.video
    }
