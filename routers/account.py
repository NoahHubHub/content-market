"""Account settings routes: view and update profile."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from deps import templates
from helpers import get_login, hash_pw, verify_pw, UserCtx

router = APIRouter()

ALLOWED_EMOJIS = [
    "🐿️","🦊","🐸","🐧","🦄","🐉","🦁","🐯","🐻","🐼",
    "🦋","🐝","🦀","🐙","🦈","🦜","🌸","⚡","🔥","💎",
    "🚀","🎯","🏆","🎸","🎮","🍕","🌊","🌙","☀️","🍀",
]


@router.get("/account")
async def account_page(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "account.html", {
        "user": UserCtx(db_user),
        "db_user": db_user,
        "emojis": ALLOWED_EMOJIS,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@router.post("/account")
async def account_save(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    display_name  = (form.get("display_name") or "").strip()[:50]
    bio           = (form.get("bio") or "").strip()[:160]
    avatar_emoji  = form.get("avatar_emoji") or db_user.avatar_emoji or "🐿️"
    avatar_color  = form.get("avatar_color") or db_user.avatar_color or "#FFB162"
    old_password  = form.get("old_password") or ""
    new_password  = form.get("new_password") or ""

    # Validate emoji
    if avatar_emoji not in ALLOWED_EMOJIS:
        avatar_emoji = db_user.avatar_emoji or "🐿️"

    # Validate color (must be #rrggbb)
    import re
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", avatar_color):
        avatar_color = db_user.avatar_color or "#FFB162"

    db_user.display_name = display_name or None
    db_user.bio          = bio or None
    db_user.avatar_emoji = avatar_emoji
    db_user.avatar_color = avatar_color

    # Password change — optional
    if new_password:
        if len(new_password) < 12:
            db.rollback()
            return RedirectResponse("/account?error=pw_short", status_code=303)
        if not verify_pw(old_password, db_user.password_hash):
            db.rollback()
            return RedirectResponse("/account?error=pw_wrong", status_code=303)
        db_user.password_hash = hash_pw(new_password)

    db.commit()
    return RedirectResponse("/account?saved=1", status_code=303)


@router.post("/comeback-reset")
async def comeback_reset(request: Request, db: Session = Depends(get_db)):
    """Gibt einem Spieler der fast pleite ist $5 000 zurück — einmal pro 30 Tage."""
    import models
    from datetime import datetime, timedelta
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=303)

    active_holdings = [h for h in db_user.holdings if h.shares > 0.001]
    if active_holdings:
        return RedirectResponse("/portfolio?err=reset_has_holdings", status_code=303)
    if db_user.balance >= 2000:
        return RedirectResponse("/portfolio?err=reset_too_rich", status_code=303)

    # Enforce 30-day cooldown via a dedicated achievement flag
    RESET_FLAG = "comeback_reset"
    last_reset = db.query(models.UserAchievement).filter_by(
        user_id=db_user.id, achievement_id=RESET_FLAG
    ).first()
    if last_reset:
        cooldown_end = last_reset.earned_at + timedelta(days=30)
        if datetime.utcnow() < cooldown_end:
            days_left = (cooldown_end - datetime.utcnow()).days + 1
            return RedirectResponse(f"/portfolio?err=reset_cooldown&days={days_left}", status_code=303)
        db.delete(last_reset)

    from models import get_level_info
    db_user.balance = 5000.0
    # XP penalty: lose 200 XP (min 0), recalculate level accordingly
    db_user.xp = max(0, (db_user.xp or 0) - 200)
    db_user.level = get_level_info(db_user.xp)["level"]
    db_user.streak_days = 0
    db.add(models.UserAchievement(user_id=db_user.id, achievement_id=RESET_FLAG))
    db.commit()
    return RedirectResponse("/portfolio?msg=comeback", status_code=303)


@router.post("/account/delete")
async def delete_account(request: Request, db: Session = Depends(get_db)):
    """Permanently deletes the user account and all associated data."""
    from sqlalchemy import or_
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=303)
    db.query(models.UserTask).filter_by(user_id=db_user.id).delete()
    db.query(models.UserAchievement).filter_by(user_id=db_user.id).delete()
    db.query(models.HotTake).filter_by(user_id=db_user.id).delete()
    db.query(models.Holding).filter_by(user_id=db_user.id).delete()
    db.query(models.Transaction).filter_by(user_id=db_user.id).delete()
    db.query(models.LeaderboardEntry).filter_by(username=db_user.username).delete()
    db.query(models.SeasonEntry).filter_by(username=db_user.username).delete()
    db.query(models.Duel).filter(
        or_(models.Duel.challenger_id == db_user.id, models.Duel.opponent_id == db_user.id)
    ).delete(synchronize_session=False)
    db.delete(db_user)
    db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account/export")
async def export_data(request: Request, db: Session = Depends(get_db)):
    """GDPR data export — returns all stored data for the authenticated user as JSON."""
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    data = {
        "account": {
            "username": db_user.username,
            "display_name": db_user.display_name,
            "bio": db_user.bio,
            "balance": db_user.balance,
            "xp": db_user.xp,
            "level": db_user.level,
            "streak_days": db_user.streak_days,
            "is_premium": db_user.is_premium,
            "created_at": db_user.created_at.isoformat() if db_user.created_at else None,
        },
        "holdings": [
            {"youtube_id": h.youtube_id, "shares": h.shares, "avg_buy_price": h.avg_buy_price}
            for h in db_user.holdings
        ],
        "transactions": [
            {
                "youtube_id": t.youtube_id,
                "type": t.transaction_type,
                "shares": t.shares,
                "price": t.price,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            }
            for t in db_user.transactions
        ],
        "achievements": [
            {"achievement_id": a.achievement_id, "earned_at": a.earned_at.isoformat() if a.earned_at else None}
            for a in db_user.achievements
        ],
    }

    headers = {"Content-Disposition": 'attachment; filename="clip-capital-export.json"'}
    return JSONResponse(content=data, headers=headers)
