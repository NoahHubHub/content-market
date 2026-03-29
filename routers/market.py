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
    get_market_feed, get_hidden_gems, sync_watchlist_to_db,
    get_user_active_duels, get_max_portfolio_slots, get_or_create_season,
    XP_DAILY_LOGIN, STREAK_BONUS,
)
from models import ACHIEVEMENTS
from pricing import calculate_price
from youtube import extract_video_id, get_video_by_id, search_videos

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, sort: str = "new", cat: str = "", db: Session = Depends(get_db)):
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

    # Category filter
    all_categories = sorted({v["video"].category for v in video_data if v["video"].category})
    if cat:
        video_data = [v for v in video_data if v["video"].category == cat]

    trending = sorted(video_data, key=lambda x: x["info"]["momentum_pct"], reverse=True)[:3]

    RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2, "Extreme": 3}
    if sort == "price_asc":
        video_data.sort(key=lambda x: x["video"].current_price)
    elif sort == "price_desc":
        video_data.sort(key=lambda x: x["video"].current_price, reverse=True)
    elif sort == "momentum":
        video_data.sort(key=lambda x: x["info"]["momentum_pct"], reverse=True)
    elif sort == "risk_low":
        video_data.sort(key=lambda x: RISK_ORDER.get(x["info"]["risk"], 3))
    elif sort == "risk_high":
        video_data.sort(key=lambda x: RISK_ORDER.get(x["info"]["risk"], 3), reverse=True)

    portfolio_ids = set(get_portfolio(db_user).keys())

    # Read watchlist from DB (persistent) — merge with any legacy session items
    db_wl_rows = db.query(models.UserWatchlist).filter_by(user_id=db_user.id).all()
    db_wl_ids = [row.youtube_id for row in db_wl_rows]
    session_wl = request.session.get("watchlist", [])
    # Migrate session items to DB if not yet there
    for yt_id in session_wl:
        if yt_id not in db_wl_ids:
            db.add(models.UserWatchlist(user_id=db_user.id, youtube_id=yt_id))
            db_wl_ids.append(yt_id)
    if session_wl:
        db.commit()
        request.session["watchlist"] = db_wl_ids  # normalise session
    watchlist = db_wl_ids

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

    # Watchlist Hot Takes: welche Videos hat der Spieler noch NICHT getippt heute?
    watchlist_takes = []
    for yt_id in watchlist:
        wv = db.query(models.Video).filter(models.Video.youtube_id == yt_id).first()
        if not wv:
            continue
        already = db.query(models.HotTake).filter_by(
            user_id=db_user.id, video_id=wv.id, date=today
        ).first()
        watchlist_takes.append({"video": wv, "already_tipped": bool(already)})

    ensure_season_entry(db_user, db)

    # Season teaser data
    season = get_or_create_season(db)
    season_entry = db.query(models.SeasonEntry).filter_by(
        season_id=season.id, username=db_user.username
    ).first()
    my_season_return = None
    if season_entry:
        my_val = calc_total_portfolio_value(db_user)
        my_season_return = round((my_val - season_entry.start_value) / max(season_entry.start_value, 1) * 100, 2)

    # Starter picks for new players: low price + positive momentum + few investors
    starter_picks = []
    if user.holdings_count == 0:
        candidates = [
            item for item in video_data
            if item["video"].current_price <= 60
            and item["info"].get("momentum_pct", 0) > 3
        ]
        candidates.sort(key=lambda x: x["info"]["momentum_pct"], reverse=True)
        starter_picks = candidates[:3]

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
        "market_feed": get_market_feed(db),
        "hidden_gems": get_hidden_gems(video_data),
        "active_duels": get_user_active_duels(db_user, db),
        "watchlist_takes": watchlist_takes,
        "max_slots": get_max_portfolio_slots(db_user),
        "season": season,
        "my_season_return": my_season_return,
        "starter_picks": starter_picks,
        "all_categories": all_categories,
        "active_cat": cat,
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
                     for s in video.stats[-90:] if s.price_at_time]

    # Detect stale price: last 3+ snapshots all at same price
    recent_prices = [s.price_at_time for s in video.stats[-3:] if s.price_at_time]
    price_is_stale = len(recent_prices) >= 3 and len(set(round(p, 2) for p in recent_prices)) == 1

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

    is_watching = db.query(models.UserWatchlist).filter_by(
        user_id=db_user.id, youtube_id=youtube_id
    ).first() is not None
    leveled_up  = request.query_params.get("lvl") == "1"
    new_tasks   = get_current_tasks(db_user, db) if leveled_up else []

    # Schlechteste Position + Slot-Info fürs slot_limit-Messaging
    worst_holding = None
    max_slots = get_max_portfolio_slots(db_user)
    if request.query_params.get("err") == "slot_limit":
        worst, worst_pct = None, 999.0
        for h in db_user.holdings:
            if h.shares > 0.001 and h.video and h.avg_cost_basis > 0:
                pct = (h.video.current_price - h.avg_cost_basis) / h.avg_cost_basis * 100
                if pct < worst_pct:
                    worst_pct = pct
                    worst = h
        if worst:
            worst_holding = {
                "youtube_id": worst.video.youtube_id,
                "title": worst.video.title,
                "pnl_pct": round(worst_pct, 1),
            }

    active_duels = get_user_active_duels(db_user, db)

    return templates.TemplateResponse(request, "video.html", {
        "user": user, "video": video,
        "last_stat": last, "info": info, "price_history": price_history,
        "holding": holding, "holders_count": holders_count,
        "related": related, "is_watching": is_watching,
        "worst_holding": worst_holding,
        "max_slots": max_slots,
        "active_duels": active_duels,
        "price_is_stale": price_is_stale,
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
    existing = db.query(models.UserWatchlist).filter_by(
        user_id=db_user.id, youtube_id=youtube_id
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        msg = "unwatched"
    else:
        MAX_WL = 9999 if db_user.is_premium else 15
        wl_count = db.query(models.UserWatchlist).filter_by(user_id=db_user.id).count()
        if wl_count >= MAX_WL:
            return RedirectResponse(f"/video/{youtube_id}?err=watchlist_full", status_code=302)
        db.add(models.UserWatchlist(user_id=db_user.id, youtube_id=youtube_id))
        db.commit()
        wl_count += 1
        update_tasks(db_user, db, "watchlist", value=wl_count)
        msg = "watched"
    # Keep session in sync for backwards compat
    db_wl_ids = [r.youtube_id for r in db.query(models.UserWatchlist).filter_by(user_id=db_user.id).all()]
    request.session["watchlist"] = db_wl_ids
    return RedirectResponse(f"/video/{youtube_id}?msg={msg}", status_code=302)


@router.post("/refresh/{youtube_id}")
async def refresh_video(youtube_id: str, db: Session = Depends(get_db)):
    yt_list = get_video_by_id(youtube_id)
    if yt_list:
        upsert_video(db, yt_list[0])
    return RedirectResponse(f"/video/{youtube_id}?msg=refreshed", status_code=302)


@router.post("/suggest/{youtube_id}")
async def suggest_video(request: Request, youtube_id: str, db: Session = Depends(get_db)):
    """Spieler schlägt ein Video für den Markt vor → +5 XP Bonus."""
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        # Video noch nicht im Markt — über YouTube-API holen und hinzufügen
        from youtube import get_video_by_id
        yt_list = get_video_by_id(youtube_id)
        if yt_list:
            video = upsert_video(db, yt_list[0])

    if video:
        # Kleiner XP-Bonus fürs Vorschlagen
        db_user.xp = (db_user.xp or 0) + 5
        db.commit()

    return RedirectResponse(f"/video/{youtube_id}?msg=suggested", status_code=302)


@router.get("/channel/{channel_id}", response_class=HTMLResponse)
async def channel_page(request: Request, channel_id: str, db: Session = Depends(get_db)):
    """Alle Videos eines Kanals auf einer Seite."""
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    videos = (db.query(models.Video)
              .filter(models.Video.channel_id == channel_id)
              .order_by(models.Video.current_price.desc())
              .limit(50).all())

    if not videos:
        raise HTTPException(status_code=404, detail="Kanal nicht gefunden")

    channel_name = videos[0].channel_name if videos else channel_id
    portfolio_ids = set(get_portfolio(db_user).keys())

    video_data = []
    for v in videos:
        if not v.stats:
            continue
        last = v.stats[-1]
        prev = v.stats[-2].view_count if len(v.stats) >= 2 else None
        info = calculate_price(
            last.view_count, last.like_count, last.comment_count, v.published_at,
            prev_view_count=prev,
        )
        video_data.append({"video": v, "info": info})

    return templates.TemplateResponse(request, "channel.html", {
        "user": user,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "video_data": video_data,
        "portfolio_ids": portfolio_ids,
    })


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
