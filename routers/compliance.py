"""Public compliance & transparency endpoints."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from deps import templates

router = APIRouter()


@router.get("/api-usage", response_class=HTMLResponse)
async def api_usage_public(request: Request, db: Session = Depends(get_db)):
    """Public YouTube API quota transparency page — visible to all users."""
    today = datetime.utcnow()
    month_start = today.replace(day=1).strftime("%Y-%m-%d")
    today_str   = today.strftime("%Y-%m-%d")
    last_30_str = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    rows = (
        db.query(models.QuotaUsage)
        .filter(models.QuotaUsage.date >= last_30_str)
        .order_by(models.QuotaUsage.date.desc())
        .all()
    )

    # Aggregate per day
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(r.date, 0)
        by_day[r.date] += r.units_used

    # Aggregate per endpoint (last 30 days)
    by_endpoint: dict = {}
    for r in rows:
        by_endpoint.setdefault(r.endpoint, {"units": 0, "calls": 0})
        by_endpoint[r.endpoint]["units"] += r.units_used
        by_endpoint[r.endpoint]["calls"] += r.calls_count

    quota_limit  = 10_000
    today_units  = by_day.get(today_str, 0)
    month_units  = sum(v for k, v in by_day.items() if k >= month_start)
    total_30d    = sum(by_day.values())
    daily_avg    = round(total_30d / 30, 0)

    return templates.TemplateResponse(request, "api_usage.html", {
        "user":         None,
        "quota_limit":  quota_limit,
        "today_units":  today_units,
        "today_pct":    round(today_units / quota_limit * 100, 1),
        "month_units":  month_units,
        "daily_avg":    daily_avg,
        "by_endpoint":  by_endpoint,
        "by_day":       dict(list(sorted(by_day.items(), reverse=True))[:14]),
        "updated_at":   today.strftime("%Y-%m-%d %H:%M UTC"),
    })
