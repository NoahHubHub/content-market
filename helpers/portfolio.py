"""Portfolio value and snapshot helpers."""
from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session

import models


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
    if db is not None:
        db.add(models.PortfolioSnapshot(user_id=db_user.id, value=market_value))
        db.commit()
    snaps = request.session.get("port_snaps", [])
    snaps.append({"ts": datetime.utcnow().strftime("%d.%m %H:%M"), "v": market_value})
    request.session["port_snaps"] = snaps[-50:]
