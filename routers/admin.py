"""Admin routes — only accessible to users with is_admin=True."""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from deps import templates
from helpers import get_login

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")


def _require_admin(request: Request, db: Session):
    db_user = get_login(request, db)
    if not db_user or not db_user.is_admin:
        return None
    return db_user


@router.get("/quota", response_class=HTMLResponse)
async def quota_dashboard(request: Request, db: Session = Depends(get_db)):
    """YouTube API quota usage — last 30 days."""
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/", status_code=302)

    last_30_str = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = (
        db.query(models.QuotaUsage)
        .filter(models.QuotaUsage.date >= last_30_str)
        .order_by(models.QuotaUsage.date.desc(), models.QuotaUsage.endpoint)
        .all()
    )

    today_str   = datetime.utcnow().strftime("%Y-%m-%d")
    total_units = sum(r.units_used for r in rows)
    today_units = sum(r.units_used for r in rows if r.date == today_str)
    quota_limit = 10_000

    return templates.TemplateResponse(request, "admin_quota.html", {
        "user":        admin,
        "rows":        rows,
        "total_units": total_units,
        "daily_avg":   round(total_units / 30, 1),
        "today_units": today_units,
        "utilization": round(today_units / quota_limit * 100, 1),
        "quota_limit": quota_limit,
    })


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    action: str = "",
    username: str = "",
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/", status_code=302)

    PAGE_SIZE = 50
    query = db.query(models.AuditLog)

    if action:
        query = query.filter(models.AuditLog.action == action)
    if username:
        query = query.filter(models.AuditLog.username == username)

    total = query.count()
    entries = (
        query.order_by(models.AuditLog.timestamp.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    actions = [r[0] for r in db.query(models.AuditLog.action).distinct().all()]

    return templates.TemplateResponse(request, "admin_audit.html", {
        "user": admin,
        "entries": entries,
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "filter_action": action,
        "filter_username": username,
        "actions": actions,
    })
