"""Account settings routes: view and update profile."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

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
        if len(new_password) < 6:
            db.rollback()
            return RedirectResponse("/account?error=pw_short", status_code=303)
        if not verify_pw(old_password, db_user.password_hash):
            db.rollback()
            return RedirectResponse("/account?error=pw_wrong", status_code=303)
        db_user.password_hash = hash_pw(new_password)

    db.commit()
    return RedirectResponse("/account?saved=1", status_code=303)
