from datetime import datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from deps import templates
from helpers import get_login, UserCtx, calc_total_portfolio_value
from models import ACHIEVEMENTS, get_level_info

router = APIRouter()


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, psort: str = "value", db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    if (db_user.tutorial_step or 0) == 3:
        db_user.tutorial_step = 99
        db.commit()

    user = UserCtx(db_user)
    holdings_data  = []
    total_invested = 0.0
    total_current  = 0.0

    for h in db_user.holdings:
        if h.shares <= 0.001 or not h.video:
            continue
        video        = h.video
        current_val  = round(h.shares * video.current_price, 2)
        invested_val = round(h.shares * h.avg_cost_basis, 2)
        pnl          = round(current_val - invested_val, 2)
        pnl_pct      = round(pnl / invested_val * 100, 2) if invested_val else 0
        holdings_data.append({
            "youtube_id": video.youtube_id, "video": video,
            "shares": h.shares, "avg_cost": h.avg_cost_basis,
            "current_val": current_val, "invested_val": invested_val,
            "pnl": pnl, "pnl_pct": pnl_pct,
        })
        total_invested += invested_val
        total_current  += current_val

    if psort == "pnl":
        holdings_data.sort(key=lambda x: x["pnl_pct"], reverse=True)
    elif psort == "name":
        holdings_data.sort(key=lambda x: x["video"].title.lower())
    else:
        holdings_data.sort(key=lambda x: x["current_val"], reverse=True)

    total_pnl     = round(total_current - total_invested, 2)
    total_pnl_pct = round(total_pnl / total_invested * 100, 2) if total_invested else 0
    portfolio_value = round(user.balance + total_current, 2)

    donut_labels = [h["video"].title[:30] for h in holdings_data]
    donut_values = [h["current_val"] for h in holdings_data]
    if user.balance > 0:
        donut_labels.append("Cash")
        donut_values.append(round(user.balance, 2))

    txs = (db.query(models.Transaction)
           .filter(models.Transaction.user_id == db_user.id)
           .order_by(models.Transaction.executed_at.desc())
           .limit(50).all())
    transactions = [{
        "type":  t.transaction_type,
        "youtube_id": t.video.youtube_id if t.video else "",
        "title": (t.video.title[:40] if t.video else "?"),
        "shares": t.shares,
        "price":  t.price_per_share,
        "total":  t.total_amount,
        "ts":     t.executed_at.strftime("%d.%m %H:%M"),
    } for t in txs]

    # Prefer DB snapshots (persistent); fall back to session for legacy data
    db_snaps = (db.query(models.PortfolioSnapshot)
                .filter_by(user_id=db_user.id)
                .order_by(models.PortfolioSnapshot.recorded_at)
                .limit(90).all())
    if db_snaps:
        port_snaps = [{"ts": s.recorded_at.strftime("%d.%m %H:%M"), "v": s.value}
                      for s in db_snaps]
    else:
        port_snaps = request.session.get("port_snaps", [])

    total_trades_count = len(db_user.transactions)
    longest_held_days  = 0
    for h in db_user.holdings:
        if h.shares > 0.001:
            first_buy = (db.query(models.Transaction)
                         .filter_by(user_id=db_user.id, video_id=h.video_id, transaction_type="buy")
                         .order_by(models.Transaction.executed_at).first())
            if first_buy:
                longest_held_days = max(longest_held_days,
                                        (datetime.utcnow() - first_buy.executed_at).days)
    sell_txs = [t for t in db_user.transactions if t.transaction_type == "sell"]
    profitable_sells = 0
    for t in sell_txs:
        buy_txs = [b for b in db_user.transactions
                   if b.transaction_type == "buy" and b.video_id == t.video_id
                   and b.executed_at < t.executed_at]
        if buy_txs:
            avg_buy = sum(b.price_per_share for b in buy_txs) / len(buy_txs)
            if t.price_per_share > avg_buy:
                profitable_sells += 1
    ach_stats = {
        "total_trades":    total_trades_count,
        "active_holdings": len([h for h in db_user.holdings if h.shares > 0.001]),
        "streak":          db_user.streak_days or 0,
        "level":           db_user.level or 1,
        "longest_held":    longest_held_days,
        "profitable_sells": profitable_sells,
    }

    return templates.TemplateResponse(request, "portfolio.html", {
        "user": user,
        "holdings_data":  holdings_data,
        "total_current":  round(total_current, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl": total_pnl, "total_pnl_pct": total_pnl_pct,
        "portfolio_value": portfolio_value,
        "donut_labels": donut_labels, "donut_values": donut_values,
        "port_snaps": port_snaps, "transactions": transactions,
        "psort": psort,
        "net_pnl":     round(portfolio_value - 10000, 2),
        "net_pnl_pct": round((portfolio_value - 10000) / 10000 * 100, 2),
        "all_achievements": ACHIEVEMENTS,
        "user_achievements": {a.achievement_id for a in db_user.achievements},
        "ach_stats": ach_stats,
    })
