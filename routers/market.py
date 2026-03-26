from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from collections import defaultdict

import models
from database import get_db
from deps import limiter, templates
from helpers import (
    get_login, UserCtx, get_portfolio, get_channel_videos, upsert_video,
    get_todays_drops, get_hot_take_video, ensure_season_entry, get_current_tasks,
    ensure_tasks, update_tasks, calc_total_portfolio_value, get_user_leagues_preview,
    XP_DAILY_LOGIN, STREAK_BONUS,
)
from models import ACHIEVEMENTS
from pricing import calculate_price
from youtube import extract_video_id, get_video_by_id, search_videos

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, sort: str = "new", db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    videos = db.query(models.Video).order_by(models.Video.last_updated.desc()).limit(48).all()

    video_ids = [v.id for v in videos]
    holders_counts = {}
    if video_ids:
        rows = (db.query(models.Holding.video_id, func.count(models.Holding.id))
                .filter(models.Holding.video_id.in_(video_ids), models.Holding.shares > 0.001)
                .group_by(models.Holding.video_id).all())
        holders_counts = {vid: cnt for vid, cnt in rows}

    channel_map: dict = defaultdict(list)
    for v in videos:
        if v.channel_id:
            channel_map[v.channel_id].append(v)

    video_data = []
    for v in videos:
        if not v.stats:
            continue
        last = v.stats[-1]
        prev = v.stats[-2].view_count if len(v.stats) >= 2 else None
        channel_vids = [
            (other.stats[-1].view_count, other.published_at)
            for other in channel_map[v.channel_id]
            if other.youtube_id != v.youtube_id and other.stats and other.stats[-1].view_count > 0
        ]
        info = calculate_price(
            last.view_count, last.like_count, last.comment_count, v.published_at,
            channel_videos=channel_vids, prev_view_count=prev,
        )
        video_data.append({"video": v, "info": info, "last_stat": last,
                           "holders": holders_counts.get(v.id, 0)})

    trending = sorted(video_data, key=lambda x: x["info"]["momentum_pct"], reverse=True)[:3]

    if sort == "price_asc":
        video_data.sort(key=lambda x: x["video"].current_price)
    elif sort == "price_desc":
        video_data.sort(key=lambda x: x["video"].current_price, reverse=True)
    elif sort == "momentum":
        video_data.sort(key=lambda x: x["info"]["momentum_pct"], reverse=True)

    portfolio_ids = set(get_portfolio(db_user).keys())
    watchlist = request.session.get("watchlist", [])
    watchlist_data = []
    for yt_id in watchlist:
        wv = db.query(models.Video).filter(models.Video.youtube_id == yt_id).first()
        if wv and wv.stats:
            wlast = wv.stats[-1]
            wchannel_vids = get_channel_videos(db, wv.channel_id or "", wv.youtube_id)
            wprev = wv.stats[-2].view_count if len(wv.stats) >= 2 else None
            winfo = calculate_price(
                wlast.view_count, wlast.like_count, wlast.comment_count, wv.published_at,
                channel_videos=wchannel_vids, prev_view_count=wprev,
            )
            watchlist_data.append({"video": wv, "info": winfo})

    streak_bonus = 0
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    if db_user.last_login_date != today_str:
        from datetime import timedelta
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        if db_user.last_login_date == yesterday:
            db_user.streak_days = (db_user.streak_days or 0) + 1
        else:
            db_user.streak_days = 1
        db_user.last_login_date = today_str
        xp_gain = XP_DAILY_LOGIN
        streak_bonus = STREAK_BONUS.get(db_user.streak_days, 0)
        xp_gain += streak_bonus
        db_user.xp = (db_user.xp or 0) + xp_gain
        db.commit()
        update_tasks(db_user, db, "streak", value=db_user.streak_days)
        ensure_tasks(db_user, db)

    morning_brief = None
    if user.holdings_count > 0:
        best_holding, best_pct = None, -999.0
        for h in db_user.holdings:
            if h.shares > 0.001 and h.video and h.avg_cost_basis > 0:
                pct = (h.video.current_price - h.avg_cost_basis) / h.avg_cost_basis * 100
                if pct > best_pct:
                    best_pct = pct
                    best_holding = h
        morning_brief = {
            "portfolio_value": user.estimated_value,
            "best": best_holding,
            "best_pct": round(best_pct, 1),
        }

    today = datetime.utcnow().strftime("%Y-%m-%d")
    bonus_available = db_user.last_bonus_date != today
    hot_take_video  = get_hot_take_video(db)
    today_hot_take  = db.query(models.HotTake).filter_by(
        user_id=db_user.id, date=today
    ).first() if hot_take_video else None
    from datetime import timedelta as _td
    yesterday       = (datetime.utcnow() - _td(days=1)).strftime("%Y-%m-%d")
    yesterday_take  = db.query(models.HotTake).filter_by(
        user_id=db_user.id, date=yesterday, resolved=True
    ).first()

    ensure_season_entry(db_user, db)

    return templates.TemplateResponse(request, "index.html", {
        "user": user,
        "video_data": video_data, "trending": trending, "sort": sort,
        "portfolio_ids": portfolio_ids, "watchlist_data": watchlist_data,
        "daily_drops": get_todays_drops(db),
        "streak_bonus": streak_bonus,
        "current_tasks": get_current_tasks(db_user, db),
        "bonus_available": bonus_available,
        "bonus_msg": request.query_params.get("bonus"),
        "bonus_amount": request.query_params.get("amount"),
        "hot_take_video": hot_take_video,
        "today_hot_take": today_hot_take,
        "yesterday_take": yesterday_take,
        "morning_brief": morning_brief,
        "user_leagues": get_user_leagues_preview(db_user, db),
    })


@router.get("/search", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)
    results, error = [], None
    portfolio_ids = set(get_portfolio(db_user).keys())

    if q:
        try:
            vid = extract_video_id(q)
            yt_list = get_video_by_id(vid) if (len(vid) == 11 and " " not in q) else search_videos(q)
            for yt in yt_list:
                video = upsert_video(db, yt)
                channel_vids = get_channel_videos(db, yt.get("channel_id", ""), yt["youtube_id"])
                info = calculate_price(
                    yt["view_count"], yt["like_count"], yt["comment_count"], yt["published_at"],
                    channel_videos=channel_vids,
                )
                results.append({"video": video, "yt": yt, "info": info})
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse(request, "search.html",
        {"user": user, "query": q, "results": results,
         "error": error, "portfolio_ids": portfolio_ids})


@router.get("/video/{youtube_id}", response_class=HTMLResponse)
async def video_detail(request: Request, youtube_id: str, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        yt_list = get_video_by_id(youtube_id)
        if not yt_list:
            raise HTTPException(status_code=404, detail="Video not found")
        video = upsert_video(db, yt_list[0])

    if (db_user.tutorial_step or 0) == 1:
        db_user.tutorial_step = 2
        db.commit()
        user = UserCtx(db_user)

    last = video.stats[-1] if video.stats else None
    channel_vids = get_channel_videos(db, video.channel_id or "", video.youtube_id)
    info = calculate_price(last.view_count, last.like_count, last.comment_count,
                           video.published_at, channel_videos=channel_vids) if last \
        else {"risk": "Unknown", "risk_color": "secondary", "momentum_pct": 0,
              "views_per_day": 0, "rps": 1.0}

    price_history = [{"t": s.recorded_at.strftime("%d.%m %H:%M"), "p": s.price_at_time,
                      "ts": s.recorded_at.timestamp()}
                     for s in video.stats[-90:]]

    h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=video.id).first()
    holding = {"shares": h.shares, "avg_cost": h.avg_cost_basis} if h and h.shares > 0.001 else None

    holders_count = (db.query(models.Holding)
                     .filter_by(video_id=video.id)
                     .filter(models.Holding.shares > 0.001)
                     .count())

    related = []
    if video.channel_id:
        related = (db.query(models.Video)
                   .filter(models.Video.channel_id == video.channel_id,
                           models.Video.youtube_id != youtube_id)
                   .order_by(models.Video.current_price.desc())
                   .limit(5).all())

    watchlist   = request.session.get("watchlist", [])
    is_watching = youtube_id in watchlist
    leveled_up  = request.query_params.get("lvl") == "1"
    new_tasks   = get_current_tasks(db_user, db) if leveled_up else []

    return templates.TemplateResponse(request, "video.html", {
        "user": user, "video": video,
        "last_stat": last, "info": info, "price_history": price_history,
        "holding": holding, "holders_count": holders_count,
        "related": related, "is_watching": is_watching,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
        "xp":  request.query_params.get("xp"),
        "new_achievements": [
            ACHIEVEMENTS[aid] for aid in
            (request.query_params.get("ach") or "").split(",")
            if aid in ACHIEVEMENTS
        ],
        "new_tasks": new_tasks,
    })


@router.post("/watch/{youtube_id}")
async def toggle_watchlist(request: Request, youtube_id: str, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    watchlist = request.session.get("watchlist", [])
    if youtube_id in watchlist:
        watchlist.remove(youtube_id)
        msg = "unwatched"
    else:
        watchlist.append(youtube_id)
        msg = "watched"
        update_tasks(db_user, db, "watchlist", value=len(watchlist))
    request.session["watchlist"] = watchlist
    return RedirectResponse(f"/video/{youtube_id}?msg={msg}", status_code=302)


@router.post("/refresh/{youtube_id}")
async def refresh_video(youtube_id: str, db: Session = Depends(get_db)):
    yt_list = get_video_by_id(youtube_id)
    if yt_list:
        upsert_video(db, yt_list[0])
    return RedirectResponse(f"/video/{youtube_id}?msg=refreshed", status_code=302)


@router.post("/refresh-portfolio")
async def refresh_portfolio(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    portfolio = get_portfolio(db_user)
    if portfolio:
        from youtube import get_video_details
        yt_list = get_video_details(list(portfolio.keys()))
        for yt in yt_list:
            upsert_video(db, yt)
    return RedirectResponse("/portfolio?msg=refreshed", status_code=302)
