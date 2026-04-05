import logging
import random
from fastapi import APIRouter, Depends, Form, HTTPException, Request

log = logging.getLogger(__name__)
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime

import models
from database import get_db
from helpers import (
    get_login, upsert_leaderboard, calc_total_portfolio_value, record_port_snap,
    log_league_activity, check_achievements, update_tasks, get_todays_drops,
    get_max_portfolio_slots, record_price_snap,
    XP_BUY, XP_SELL_PROFIT, XP_SELL_LOSS,
)

router = APIRouter()


@router.post("/buy/{youtube_id}")
async def buy(request: Request, youtube_id: str, shares: float = Form(...),
              db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        raise HTTPException(status_code=404)
    if shares <= 0:
        return RedirectResponse(f"/video/{youtube_id}?err=invalid_amount", status_code=302)

    total_cost = round(shares * video.current_price, 2)
    if db_user.balance < total_cost:
        return RedirectResponse(f"/video/{youtube_id}?err=insufficient_funds", status_code=302)

    # Portfolio-Slot-Limit (Free = 7 Basis + Streak-Bonus)
    h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=video.id).first()
    if not h and not db_user.is_premium:
        active_count = db.query(models.Holding).filter(
            models.Holding.user_id == db_user.id,
            models.Holding.shares > 0.001,
        ).count()
        max_slots = get_max_portfolio_slots(db_user)
        if active_count >= max_slots:
            return RedirectResponse(f"/video/{youtube_id}?err=slot_limit", status_code=302)

    if h:
        new_total = h.shares + shares
        h.avg_cost_basis = round(
            (h.shares * h.avg_cost_basis + shares * video.current_price) / new_total, 4
        )
        h.shares = round(new_total, 4)
    else:
        db.add(models.Holding(
            user_id=db_user.id, video_id=video.id,
            shares=round(shares, 4), avg_cost_basis=round(video.current_price, 4),
        ))

    db_user.balance = round(db_user.balance - total_cost, 2)
    db.add(models.Transaction(
        user_id=db_user.id, video_id=video.id, transaction_type="buy",
        shares=shares, price_per_share=video.current_price, total_amount=total_cost,
    ))

    # Market price impact: demand drives price up (+0.5% per share, max +20%)
    impact = min(shares * 0.005, 0.20)
    video.current_price = round(max(1.0, video.current_price * (1 + impact)), 2)
    record_price_snap(db, video)

    db.commit()
    db.refresh(db_user)

    db_user.xp = (db_user.xp or 0) + XP_BUY
    if (db_user.tutorial_step or 0) <= 2:
        db_user.tutorial_step = 3
    log_league_activity(db, db_user, "buy", video, shares, video.current_price)
    db.commit()

    active_holdings = len([h for h in db_user.holdings if h.shares > 0.001])
    total_invested  = sum(t.total_amount for t in db_user.transactions if t.transaction_type == "buy")
    total_trades    = len(db_user.transactions)
    update_tasks(db_user, db, "buy",       value=1)
    update_tasks(db_user, db, "trades",    value=total_trades)
    update_tasks(db_user, db, "portfolio", value=active_holdings)
    leveled_up = update_tasks(db_user, db, "invest", value=int(total_invested))
    new_achievements = check_achievements(db_user, db)
    record_port_snap(request, db_user, db)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    ach_param = ",".join(new_achievements) if new_achievements else ""
    return RedirectResponse(
        f"/video/{youtube_id}?msg=bought&xp={XP_BUY}&ach={ach_param}&lvl={'1' if leveled_up else ''}",
        status_code=302,
    )


@router.post("/sell/{youtube_id}")
async def sell(request: Request, youtube_id: str, shares: float = Form(...),
               db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        raise HTTPException(status_code=404)

    h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=video.id).first()
    if not h or h.shares < shares:
        return RedirectResponse(f"/video/{youtube_id}?err=insufficient_shares", status_code=302)

    avg_cost = h.avg_cost_basis
    revenue  = round(shares * video.current_price, 2)
    h.shares = round(h.shares - shares, 4)
    if h.shares <= 0.001:
        db.delete(h)

    db_user.balance = round(db_user.balance + revenue, 2)
    db.add(models.Transaction(
        user_id=db_user.id, video_id=video.id, transaction_type="sell",
        shares=shares, price_per_share=video.current_price, total_amount=revenue,
    ))

    # Market price impact: supply drives price down (-0.4% per share, max -15%)
    impact = min(shares * 0.004, 0.15)
    video.current_price = round(max(1.0, video.current_price * (1 - impact)), 2)
    record_price_snap(db, video)

    profit    = video.current_price >= avg_cost
    xp_gained = XP_SELL_PROFIT if profit else XP_SELL_LOSS
    db_user.xp = (db_user.xp or 0) + xp_gained
    log_league_activity(db, db_user, "sell", video, shares, video.current_price)
    db.commit()
    db.refresh(db_user)

    total_trades = len(db_user.transactions)
    leveled_up  = update_tasks(db_user, db, "sell",   value=1)
    leveled_up  = update_tasks(db_user, db, "trades", value=total_trades) or leveled_up
    if profit:
        leveled_up = update_tasks(db_user, db, "profit", value=1) or leveled_up
    new_achievements = check_achievements(db_user, db)
    record_port_snap(request, db_user, db)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    ach_param = ",".join(new_achievements) if new_achievements else ""
    return RedirectResponse(
        f"/video/{youtube_id}?msg=sold&xp={xp_gained}&ach={ach_param}&lvl={'1' if leveled_up else ''}",
        status_code=302,
    )


@router.post("/daily-drop/buy/{drop_id}")
async def buy_daily_drop(request: Request, drop_id: int, shares: float = Form(...),
                         db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    drop = db.query(models.DailyDrop).filter_by(id=drop_id).first()
    if not drop or not drop.video:
        raise HTTPException(status_code=404)

    shares = round(min(shares, drop.shares_remaining), 4)
    if shares <= 0:
        return RedirectResponse(f"/video/{drop.video.youtube_id}?err=drop_sold_out", status_code=302)

    total_cost = round(shares * drop.video.current_price, 2)
    if db_user.balance < total_cost:
        return RedirectResponse(f"/video/{drop.video.youtube_id}?err=insufficient_funds", status_code=302)

    drop.shares_remaining = round(drop.shares_remaining - shares, 4)

    h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=drop.video_id).first()
    if h:
        new_total = h.shares + shares
        h.avg_cost_basis = round(
            (h.shares * h.avg_cost_basis + shares * drop.video.current_price) / new_total, 4
        )
        h.shares = round(new_total, 4)
    else:
        db.add(models.Holding(
            user_id=db_user.id, video_id=drop.video_id,
            shares=round(shares, 4), avg_cost_basis=round(drop.video.current_price, 4),
        ))

    db_user.balance = round(db_user.balance - total_cost, 2)
    db.add(models.Transaction(
        user_id=db_user.id, video_id=drop.video_id, transaction_type="buy",
        shares=shares, price_per_share=drop.video.current_price, total_amount=total_cost,
    ))
    # Market price impact on daily drop purchase
    impact = min(shares * 0.005, 0.20)
    drop.video.current_price = round(max(1.0, drop.video.current_price * (1 + impact)), 2)
    record_price_snap(db, drop.video)

    db_user.xp = (db_user.xp or 0) + XP_BUY + 5
    log_league_activity(db, db_user, "buy", drop.video, shares, drop.video.current_price)
    db.commit()
    db.refresh(db_user)

    # Push: "Fast ausverkauft" wenn < 20% verbleibend
    try:
        remaining_pct = drop.shares_remaining / max(drop.total_shares, 1) * 100
        if remaining_pct < 20:
            from routers.push import send_push_to_user
            watchers = db.query(models.UserWatchlist).filter(
                models.UserWatchlist.youtube_id == drop.video.youtube_id
            ).all()
            for w in watchers:
                if w.user_id != db_user.id:
                    send_push_to_user(
                        w.user_id,
                        title="⚡ Daily Drop fast ausverkauft!",
                        body=f"Nur noch {drop.shares_remaining:.0f} Anteile von \"{drop.video.title[:35]}\" übrig!",
                        url=f"/video/{drop.video.youtube_id}",
                        db=db,
                    )
    except Exception:
        log.warning("buy_daily_drop: push notification failed", exc_info=True)

    total_drops = sum(1 for t in db_user.transactions
                      if t.transaction_type == "buy" and
                      db.query(models.DailyDrop).filter_by(video_id=t.video_id).first())
    update_tasks(db_user, db, "buy",        value=1)
    update_tasks(db_user, db, "daily_drop", value=total_drops)
    leveled_up = update_tasks(db_user, db, "trades", value=len(db_user.transactions))
    new_achievements = check_achievements(db_user, db)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    ach_param = ",".join(new_achievements) if new_achievements else ""
    return RedirectResponse(
        f"/video/{drop.video.youtube_id}?msg=bought&xp={XP_BUY + 5}&ach={ach_param}&lvl={'1' if leveled_up else ''}",
        status_code=302,
    )


@router.post("/bonus/claim")
async def claim_bonus(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if db_user.last_bonus_date == today:
        return RedirectResponse("/?bonus=already", status_code=302)

    db_user.last_bonus_date = today
    roll = random.random()
    if roll < 0.33:
        xp_gain = random.randint(15, 50)
        db_user.xp = (db_user.xp or 0) + xp_gain
        db.commit()
        return RedirectResponse(f"/?bonus=xp&amount={xp_gain}", status_code=302)
    elif roll < 0.66:
        cash = random.choice([50, 75, 100, 150, 200])
        db_user.balance = round(db_user.balance + cash, 2)
        db.commit()
        return RedirectResponse(f"/?bonus=cash&amount={cash}", status_code=302)
    else:
        video = db.query(models.Video).order_by(models.Video.last_updated.desc()).first()
        if video:
            h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=video.id).first()
            if h:
                new_total = h.shares + 1
                h.avg_cost_basis = round(
                    (h.shares * h.avg_cost_basis + video.current_price) / new_total, 4
                )
                h.shares = round(new_total, 4)
            else:
                db.add(models.Holding(
                    user_id=db_user.id, video_id=video.id,
                    shares=1.0, avg_cost_basis=round(video.current_price, 4),
                ))
            db.add(models.Transaction(
                user_id=db_user.id, video_id=video.id, transaction_type="buy",
                shares=1.0, price_per_share=video.current_price, total_amount=video.current_price,
            ))
            db.commit()
            db.refresh(db_user)
            active_holdings = len([ho for ho in db_user.holdings if ho.shares > 0.001])
            total_invested  = sum(t.total_amount for t in db_user.transactions if t.transaction_type == "buy")
            update_tasks(db_user, db, "buy",       value=1)
            update_tasks(db_user, db, "trades",    value=len(db_user.transactions))
            update_tasks(db_user, db, "portfolio", value=active_holdings)
            update_tasks(db_user, db, "invest",    value=int(total_invested))
            check_achievements(db_user, db)
            record_port_snap(request, db_user, db)
            upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
            return RedirectResponse(f"/?bonus=share&title={video.title[:30]}", status_code=302)
        db.commit()
        return RedirectResponse("/?bonus=xp&amount=20", status_code=302)
