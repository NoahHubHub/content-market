import os
import random
import secrets
import string
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from database import Base, engine, get_db
import models
from pricing import calculate_price
from youtube import extract_video_id, get_video_by_id, get_video_details, search_videos

load_dotenv()
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Content Market")

# Kryptografisch zufälliger Key + Startup-Token bei jedem Start
_session_key   = os.getenv("SECRET_KEY") or secrets.token_hex(32)
_startup_token = secrets.token_hex(8)  # wird in jeder Session geprüft
app.add_middleware(SessionMiddleware, secret_key=_session_key, max_age=86400 * 30)
templates = Jinja2Templates(directory="templates")


# ── session helpers ───────────────────────────────────────────────────────────

class User:
    """Lightweight user object backed by session — no database needed."""
    def __init__(self, username: str, balance: float, portfolio: dict):
        self.username = username
        self.balance = balance
        self.holdings_count = len(portfolio)
        self.cost_basis = sum(h["shares"] * h["avg_cost"] for h in portfolio.values())
        self.estimated_value = round(balance + self.cost_basis, 2)


def get_user(request: Request) -> User:
    # Startup-Token stimmt nicht überein → Server wurde neu gestartet → Session löschen
    if request.session.get("_startup") != _startup_token:
        request.session.clear()
        request.session["_startup"] = _startup_token

    if "username" not in request.session:
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
        request.session["username"] = f"Player_{suffix}"
        request.session["balance"] = 10000.0
        request.session["portfolio"] = {}
    portfolio = request.session.get("portfolio", {})
    return User(request.session["username"], request.session["balance"], portfolio)


def get_portfolio(request: Request) -> dict:
    """Portfolio stored in session: {youtube_id: {shares, avg_cost}}"""
    return request.session.get("portfolio", {})


def save_portfolio(request: Request, portfolio: dict, balance: float):
    request.session["portfolio"] = portfolio
    request.session["balance"] = balance


def fmt(n):
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(int(n))


templates.env.filters["fmt"] = fmt


# ── video helpers ─────────────────────────────────────────────────────────────

def get_channel_videos(db: Session, channel_id: str, exclude_youtube_id: str) -> list:
    """Fetch (view_count, published_at) for other videos from the same creator."""
    others = (
        db.query(models.Video)
        .filter(models.Video.channel_id == channel_id, models.Video.youtube_id != exclude_youtube_id)
        .limit(20)
        .all()
    )
    result = []
    for v in others:
        views = v.stats[-1].view_count if v.stats else 0
        result.append((views, v.published_at))
    return result


def upsert_video(db: Session, yt: dict) -> models.Video:
    video = db.query(models.Video).filter(models.Video.youtube_id == yt["youtube_id"]).first()

    channel_vids = get_channel_videos(db, yt.get("channel_id", ""), yt["youtube_id"])

    price_data = calculate_price(
        view_count=yt["view_count"],
        like_count=yt["like_count"],
        comment_count=yt["comment_count"],
        published_at=yt["published_at"],
        channel_videos=channel_vids,
    )

    if not video:
        video = models.Video(
            youtube_id=yt["youtube_id"],
            title=yt["title"],
            channel_name=yt["channel_name"],
            channel_id=yt.get("channel_id", ""),
            thumbnail_url=yt["thumbnail_url"],
            published_at=yt["published_at"],
            current_price=price_data["price"],
        )
        db.add(video)
        db.flush()
    else:
        video.title = yt["title"]
        video.channel_name = yt["channel_name"]
        video.channel_id = yt.get("channel_id", "")
        video.thumbnail_url = yt["thumbnail_url"]
        video.current_price = price_data["price"]
        video.last_updated = datetime.utcnow()

    db.add(models.VideoStats(
        video_id=video.id,
        view_count=yt["view_count"],
        like_count=yt["like_count"],
        comment_count=yt["comment_count"],
        price_at_time=price_data["price"],
    ))
    db.commit()
    db.refresh(video)
    return video


# ── helpers ───────────────────────────────────────────────────────────────────

def calc_total_portfolio_value(portfolio: dict, balance: float, db: Session) -> float:
    """Sum balance + market value of all holdings."""
    total = balance
    for yt_id, h in portfolio.items():
        v = db.query(models.Video).filter(models.Video.youtube_id == yt_id).first()
        if v:
            total += h["shares"] * v.current_price
    return round(total, 2)


def upsert_leaderboard(username: str, portfolio_value: float, db: Session):
    return_pct = round((portfolio_value - 10000) / 10000 * 100, 2)
    entry = db.query(models.LeaderboardEntry).filter_by(username=username).first()
    if entry:
        entry.portfolio_value = portfolio_value
        entry.return_pct = return_pct
        entry.recorded_at = datetime.utcnow()
    else:
        db.add(models.LeaderboardEntry(username=username, portfolio_value=portfolio_value, return_pct=return_pct))
    db.commit()


def record_tx(request: Request, tx_type: str, youtube_id: str, title: str,
              shares: float, price: float, total: float):
    txs = request.session.get("transactions", [])
    txs.append({
        "type": tx_type,
        "youtube_id": youtube_id,
        "title": title[:40],
        "shares": round(shares, 4),
        "price": round(price, 2),
        "total": round(total, 2),
        "ts": datetime.utcnow().strftime("%d.%m %H:%M"),
    })
    request.session["transactions"] = txs[-50:]


def record_port_snap(request: Request, portfolio: dict, balance: float):
    total_cost = sum(h["shares"] * h["avg_cost"] for h in portfolio.values())
    snaps = request.session.get("port_snaps", [])
    snaps.append({"ts": datetime.utcnow().strftime("%d.%m %H:%M"), "v": round(total_cost, 2)})
    request.session["port_snaps"] = snaps[-50:]


# ── market ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, sort: str = "new", db: Session = Depends(get_db)):
    user = get_user(request)
    videos = db.query(models.Video).order_by(models.Video.last_updated.desc()).limit(48).all()

    video_data = []
    for v in videos:
        if not v.stats:
            continue
        last = v.stats[-1]
        info = calculate_price(last.view_count, last.like_count, last.comment_count, v.published_at)
        video_data.append({"video": v, "info": info, "last_stat": last})

    # Trending: top 3 by momentum (RPS vs creator avg)
    trending = sorted(video_data, key=lambda x: x["info"]["momentum_pct"], reverse=True)[:3]

    # Sort market list
    if sort == "price_asc":
        video_data.sort(key=lambda x: x["video"].current_price)
    elif sort == "price_desc":
        video_data.sort(key=lambda x: x["video"].current_price, reverse=True)
    elif sort == "momentum":
        video_data.sort(key=lambda x: x["info"]["momentum_pct"], reverse=True)
    # "new" = default, already ordered by last_updated desc

    portfolio = get_portfolio(request)
    portfolio_ids = set(portfolio.keys())
    watchlist = request.session.get("watchlist", [])
    watchlist_data = []
    for yt_id in watchlist:
        wv = db.query(models.Video).filter(models.Video.youtube_id == yt_id).first()
        if wv and wv.stats:
            wlast = wv.stats[-1]
            winfo = calculate_price(wlast.view_count, wlast.like_count, wlast.comment_count, wv.published_at)
            watchlist_data.append({"video": wv, "info": winfo})

    return templates.TemplateResponse("index.html", {
        "request": request, "user": user,
        "video_data": video_data, "trending": trending, "sort": sort,
        "portfolio_ids": portfolio_ids, "watchlist_data": watchlist_data,
    })


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    user = get_user(request)
    results, error = [], None

    portfolio_ids = set(get_portfolio(request).keys())
    if q:
        try:
            vid = extract_video_id(q)
            yt_list = get_video_by_id(vid) if (len(vid) == 11 and " " not in q) else search_videos(q)
            for yt in yt_list:
                video = upsert_video(db, yt)
                info = calculate_price(yt["view_count"], yt["like_count"], yt["comment_count"], yt["published_at"])
                results.append({"video": video, "yt": yt, "info": info})
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse("search.html",
        {"request": request, "user": user, "query": q, "results": results,
         "error": error, "portfolio_ids": portfolio_ids})


@app.get("/video/{youtube_id}", response_class=HTMLResponse)
async def video_detail(request: Request, youtube_id: str, db: Session = Depends(get_db)):
    user = get_user(request)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        yt_list = get_video_by_id(youtube_id)
        if not yt_list:
            raise HTTPException(status_code=404, detail="Video not found")
        video = upsert_video(db, yt_list[0])

    last = video.stats[-1] if video.stats else None

    channel_vids = get_channel_videos(db, video.channel_id or "", video.youtube_id)
    info = calculate_price(last.view_count, last.like_count, last.comment_count,
                           video.published_at, channel_videos=channel_vids) if last \
        else {"risk": "Unknown", "risk_color": "secondary", "momentum_pct": 0, "views_per_day": 0, "rps": 1.0}

    price_history = [{"t": s.recorded_at.strftime("%d.%m %H:%M"), "p": s.price_at_time,
                      "ts": s.recorded_at.timestamp()}
                     for s in video.stats]

    # holding from session
    portfolio = get_portfolio(request)
    holding = portfolio.get(youtube_id)

    # related videos from same channel
    related = []
    if video.channel_id:
        related = (db.query(models.Video)
                   .filter(models.Video.channel_id == video.channel_id,
                           models.Video.youtube_id != youtube_id)
                   .order_by(models.Video.current_price.desc())
                   .limit(5).all())

    watchlist = request.session.get("watchlist", [])
    is_watching = youtube_id in watchlist

    return templates.TemplateResponse("video.html", {
        "request": request, "user": user, "video": video,
        "last_stat": last, "info": info, "price_history": price_history,
        "holding": holding, "related": related, "is_watching": is_watching,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
    })


@app.post("/watch/{youtube_id}")
async def toggle_watchlist(request: Request, youtube_id: str):
    get_user(request)
    watchlist = request.session.get("watchlist", [])
    if youtube_id in watchlist:
        watchlist.remove(youtube_id)
        msg = "unwatched"
    else:
        watchlist.append(youtube_id)
        msg = "watched"
    request.session["watchlist"] = watchlist
    return RedirectResponse(f"/video/{youtube_id}?msg={msg}", status_code=302)


@app.post("/refresh/{youtube_id}")
async def refresh_video(youtube_id: str, db: Session = Depends(get_db)):
    yt_list = get_video_by_id(youtube_id)
    if yt_list:
        upsert_video(db, yt_list[0])
    return RedirectResponse(f"/video/{youtube_id}?msg=refreshed", status_code=302)


@app.post("/refresh-portfolio")
async def refresh_portfolio(request: Request, db: Session = Depends(get_db)):
    """Refresh YouTube stats for every video currently in the user's portfolio."""
    get_user(request)
    portfolio = get_portfolio(request)
    if portfolio:
        yt_list = get_video_details(list(portfolio.keys()))
        for yt in yt_list:
            upsert_video(db, yt)
    return RedirectResponse("/portfolio?msg=refreshed", status_code=302)


# ── trading ───────────────────────────────────────────────────────────────────

@app.post("/buy/{youtube_id}")
async def buy(request: Request, youtube_id: str, shares: float = Form(...), db: Session = Depends(get_db)):
    get_user(request)  # ensure session exists

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        raise HTTPException(status_code=404)

    if shares <= 0:
        return RedirectResponse(f"/video/{youtube_id}?err=invalid_amount", status_code=302)

    balance = request.session.get("balance", 10000.0)
    total_cost = round(shares * video.current_price, 2)

    if balance < total_cost:
        return RedirectResponse(f"/video/{youtube_id}?err=insufficient_funds", status_code=302)

    portfolio = get_portfolio(request)

    if youtube_id in portfolio:
        existing = portfolio[youtube_id]
        new_total = existing["shares"] + shares
        existing["avg_cost"] = round(
            (existing["shares"] * existing["avg_cost"] + shares * video.current_price) / new_total, 4
        )
        existing["shares"] = round(new_total, 4)
    else:
        portfolio[youtube_id] = {
            "shares": round(shares, 4),
            "avg_cost": round(video.current_price, 4),
        }

    new_balance = round(balance - total_cost, 2)
    save_portfolio(request, portfolio, new_balance)
    record_tx(request, "buy", youtube_id, video.title, shares, video.current_price, total_cost)
    record_port_snap(request, portfolio, new_balance)
    total_val = calc_total_portfolio_value(portfolio, new_balance, db)
    upsert_leaderboard(request.session["username"], total_val, db)
    return RedirectResponse(f"/video/{youtube_id}?msg=bought", status_code=302)


@app.post("/sell/{youtube_id}")
async def sell(request: Request, youtube_id: str, shares: float = Form(...), db: Session = Depends(get_db)):
    get_user(request)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        raise HTTPException(status_code=404)

    portfolio = get_portfolio(request)
    holding = portfolio.get(youtube_id)

    if not holding or holding["shares"] < shares:
        return RedirectResponse(f"/video/{youtube_id}?err=insufficient_shares", status_code=302)

    balance = request.session.get("balance", 10000.0)
    revenue = round(shares * video.current_price, 2)

    holding["shares"] = round(holding["shares"] - shares, 4)
    if holding["shares"] <= 0.001:
        del portfolio[youtube_id]

    new_balance = round(balance + revenue, 2)
    save_portfolio(request, portfolio, new_balance)
    record_tx(request, "sell", youtube_id, video.title, shares, video.current_price, revenue)
    record_port_snap(request, portfolio, new_balance)
    total_val = calc_total_portfolio_value(portfolio, new_balance, db)
    upsert_leaderboard(request.session["username"], total_val, db)
    return RedirectResponse(f"/video/{youtube_id}?msg=sold", status_code=302)


# ── portfolio ─────────────────────────────────────────────────────────────────

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, psort: str = "value", db: Session = Depends(get_db)):
    user = get_user(request)
    portfolio = get_portfolio(request)

    holdings_data = []
    total_invested = 0.0
    total_current = 0.0

    for youtube_id, h in portfolio.items():
        video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
        if not video or h["shares"] <= 0:
            continue
        current_val = round(h["shares"] * video.current_price, 2)
        invested_val = round(h["shares"] * h["avg_cost"], 2)
        pnl = round(current_val - invested_val, 2)
        pnl_pct = round(pnl / invested_val * 100, 2) if invested_val else 0
        holdings_data.append({
            "youtube_id": youtube_id,
            "video": video,
            "shares": h["shares"],
            "avg_cost": h["avg_cost"],
            "current_val": current_val,
            "invested_val": invested_val,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        })
        total_invested += invested_val
        total_current += current_val

    if psort == "pnl":
        holdings_data.sort(key=lambda x: x["pnl_pct"], reverse=True)
    elif psort == "name":
        holdings_data.sort(key=lambda x: x["video"].title.lower())
    else:
        holdings_data.sort(key=lambda x: x["current_val"], reverse=True)
    total_pnl = round(total_current - total_invested, 2)
    total_pnl_pct = round(total_pnl / total_invested * 100, 2) if total_invested else 0
    portfolio_value = round(user.balance + total_current, 2)

    # Donut chart data: each holding + cash
    donut_labels = [h["video"].title[:30] for h in holdings_data]
    donut_values = [h["current_val"] for h in holdings_data]
    if user.balance > 0:
        donut_labels.append("Cash")
        donut_values.append(round(user.balance, 2))

    port_snaps = request.session.get("port_snaps", [])
    transactions = request.session.get("transactions", [])

    return templates.TemplateResponse("portfolio.html", {
        "request": request, "user": user,
        "holdings_data": holdings_data,
        "total_current": round(total_current, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "portfolio_value": portfolio_value,
        "donut_labels": donut_labels,
        "donut_values": donut_values,
        "port_snaps": port_snaps,
        "transactions": list(reversed(transactions)),
        "psort": psort,
        "net_pnl": round(portfolio_value - 10000, 2),
        "net_pnl_pct": round((portfolio_value - 10000) / 10000 * 100, 2),
    })


# ── leaderboard ───────────────────────────────────────────────────────────────

@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(request: Request, db: Session = Depends(get_db)):
    user = get_user(request)
    entries = (db.query(models.LeaderboardEntry)
               .order_by(models.LeaderboardEntry.portfolio_value.desc())
               .limit(20).all())
    board = [{"username": e.username, "portfolio_value": e.portfolio_value, "return_pct": e.return_pct}
             for e in entries]
    if not board:
        board = [{"username": user.username, "portfolio_value": user.balance,
                  "return_pct": round((user.balance - 10000) / 10000 * 100, 2)}]
    return templates.TemplateResponse("leaderboard.html", {
        "request": request, "user": user, "board": board,
    })
