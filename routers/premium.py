"""Premium subscription routes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from deps import templates
from helpers import get_login, UserCtx

router = APIRouter()

FREE_SLOTS = 7


@router.get("/premium")
async def premium_page(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=303)
    if db_user.is_premium:
        return RedirectResponse("/account?saved=1", status_code=303)
    return templates.TemplateResponse(request, "premium.html", {
        "user": UserCtx(db_user),
        "ref": request.query_params.get("ref", ""),
        "free_slots": FREE_SLOTS,
    })


@router.post("/premium/upgrade")
async def upgrade(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=303)
    db_user.is_premium = True
    db.commit()
    return RedirectResponse("/premium/welcome", status_code=303)


@router.get("/premium/welcome")
async def welcome(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "premium_welcome.html", {
        "user": UserCtx(db_user),
    })
