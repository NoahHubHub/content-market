"""XP, tasks, achievements and level-up logic."""
from datetime import datetime

from sqlalchemy.orm import Session

import models
from models import get_level_info, ACHIEVEMENTS, generate_tasks_for_level

# ── XP rewards ─────────────────────────────────────────────────────────────────
XP_BUY         = 10
XP_SELL_PROFIT = 30
XP_SELL_LOSS   = 5
XP_DAILY_LOGIN = 5
STREAK_BONUS   = {3: 15, 7: 50, 14: 100, 30: 250}


# ── Task system ────────────────────────────────────────────────────────────────

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


# ── Achievements ───────────────────────────────────────────────────────────────

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
