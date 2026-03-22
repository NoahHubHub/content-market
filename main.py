import os
import secrets
import traceback
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from database import Base, engine, get_db
import models
from pricing import calculate_price
from youtube import extract_video_id, get_video_by_id, get_video_details, search_videos

load_dotenv()
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Content Market")
_session_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_session_key, max_age=86400 * 30)
templates = Jinja2Templates(directory="templates")
def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def _verify_pw(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ── auth helpers ───────────────────────────────────────────────────────────────

class UserCtx:
    """Template-friendly wrapper around models.User with computed portfolio stats."""
    def __init__(self, db_user: models.User):
        active = [h for h in db_user.holdings if h.shares > 0.001]
        self.id              = db_user.id
        self.username        = db_user.username
        self.balance         = db_user.balance
        self.holdings_count  = len(active)
        self.cost_basis      = round(sum(h.shares * h.avg_cost_basis for h in active), 2)
        self.estimated_value = round(db_user.balance + self.cost_basis, 2)


def get_login(request: Request, db: Session) -> Optional[models.User]:
    """Return the logged-in DB user, or None."""
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(models.User).filter(models.User.id == uid).first()


def get_portfolio(db_user: models.User) -> dict:
    """Build {youtube_id: {shares, avg_cost}} from DB holdings (matches old session format)."""
    return {
        h.video.youtube_id: {"shares": h.shares, "avg_cost": h.avg_cost_basis}
        for h in db_user.holdings
        if h.shares > 0.001 and h.video
    }


def fmt(n):
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(int(n))


templates.env.filters["fmt"] = fmt


@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    print(f"[ERROR] {request.method} {request.url}\n{tb}", flush=True)
    return PlainTextResponse(f"500 – {type(exc).__name__}: {exc}\n\n{tb}", status_code=500)


# ── video helpers ─────────────────────────────────────────────────────────────

def get_channel_videos(db: Session, channel_id: str, exclude_youtube_id: str) -> list:
    others = (
        db.query(models.Video)
        .filter(models.Video.channel_id == channel_id, models.Video.youtube_id != exclude_youtube_id)
        .limit(20).all()
    )
    return [(v.stats[-1].view_count if v.stats else 0, v.published_at) for v in others]


def upsert_video(db: Session, yt: dict) -> models.Video:
    video = db.query(models.Video).filter(models.Video.youtube_id == yt["youtube_id"]).first()
    channel_vids = get_channel_videos(db, yt.get("channel_id", ""), yt["youtube_id"])
    price_data = calculate_price(
        view_count=yt["view_count"], like_count=yt["like_count"],
        comment_count=yt["comment_count"], published_at=yt["published_at"],
        channel_videos=channel_vids,
    )
    if not video:
        video = models.Video(
            youtube_id=yt["youtube_id"], title=yt["title"],
            channel_name=yt["channel_name"], channel_id=yt.get("channel_id", ""),
            thumbnail_url=yt["thumbnail_url"], published_at=yt["published_at"],
            current_price=price_data["price"],
        )
        db.add(video)
        db.flush()
    else:
        video.title         = yt["title"]
        video.channel_name  = yt["channel_name"]
        video.channel_id    = yt.get("channel_id", "")
        video.thumbnail_url = yt["thumbnail_url"]
        video.current_price = price_data["price"]
        video.last_updated  = datetime.utcnow()
    db.add(models.VideoStats(
        video_id=video.id, view_count=yt["view_count"], like_count=yt["like_count"],
        comment_count=yt["comment_count"], price_at_time=price_data["price"],
    ))
    db.commit()
    db.refresh(video)
    return video


# ── portfolio helpers ─────────────────────────────────────────────────────────

def calc_total_portfolio_value(db_user: models.User) -> float:
    total = db_user.balance
    for h in db_user.holdings:
        if h.shares > 0.001 and h.video:
            total += h.shares * h.video.current_price
    return round(total, 2)


def upsert_leaderboard(username: str, portfolio_value: float, db: Session):
    return_pct = round((portfolio_value - 10000) / 10000 * 100, 2)
    entry = db.query(models.LeaderboardEntry).filter_by(username=username).first()
    if entry:
        entry.portfolio_value = portfolio_value
        entry.return_pct      = return_pct
        entry.recorded_at     = datetime.utcnow()
    else:
        db.add(models.LeaderboardEntry(
            username=username, portfolio_value=portfolio_value, return_pct=return_pct
        ))
    db.commit()


def record_port_snap(request: Request, db_user: models.User):
    """Sparkline snapshots stay in session — lightweight display data only."""
    active = [h for h in db_user.holdings if h.shares > 0.001]
    total_cost = sum(h.shares * h.avg_cost_basis for h in active)
    snaps = request.session.get("port_snaps", [])
    snaps.append({"ts": datetime.utcnow().strftime("%d.%m %H:%M"), "v": round(total_cost, 2)})
    request.session["port_snaps"] = snaps[-50:]


# ── auth routes ───────────────────────────────────────────────────────────────

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html",
        {"request": request, "user": None, "error": None})


@app.post("/register", response_class=HTMLResponse)
async def register(request: Request, username: str = Form(...), password: str = Form(...),
                   db: Session = Depends(get_db)):
    if len(username) < 3:
        return templates.TemplateResponse("register.html",
            {"request": request, "user": None, "error": "Username zu kurz (min. 3 Zeichen)"})
    if db.query(models.User).filter(models.User.username == username).first():
        return templates.TemplateResponse("register.html",
            {"request": request, "user": None, "error": "Username bereits vergeben"})
    db_user = models.User(
        username=username,
        password_hash=_hash_pw(password),
        balance=10000.0,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    request.session.clear()
    request.session["user_id"] = db_user.id
    return RedirectResponse("/", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html",
        {"request": request, "user": None, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...),
                db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.username == username).first()
    if not db_user or not _verify_pw(password, db_user.password_hash):
        return templates.TemplateResponse("login.html",
            {"request": request, "user": None, "error": "Ungültige Zugangsdaten"})
    request.session.clear()
    request.session["user_id"] = db_user.id
    return RedirectResponse("/", status_code=302)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── market ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, sort: str = "new", db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    videos = db.query(models.Video).order_by(models.Video.last_updated.desc()).limit(48).all()
    video_data = []
    for v in videos:
        if not v.stats:
            continue
        last = v.stats[-1]
        info = calculate_price(last.view_count, last.like_count, last.comment_count, v.published_at)
        video_data.append({"video": v, "info": info, "last_stat": last})

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
            winfo = calculate_price(wlast.view_count, wlast.like_count, wlast.comment_count, wv.published_at)
            watchlist_data.append({"video": wv, "info": winfo})

    return templates.TemplateResponse("index.html", {
        "request": request, "user": user,
        "video_data": video_data, "trending": trending, "sort": sort,
        "portfolio_ids": portfolio_ids, "watchlist_data": watchlist_data,
    })


@app.get("/search", response_class=HTMLResponse)
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
                info = calculate_price(yt["view_count"], yt["like_count"], yt["comment_count"], yt["published_at"])
                results.append({"video": video, "yt": yt, "info": info})
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse("search.html",
        {"request": request, "user": user, "query": q, "results": results,
         "error": error, "portfolio_ids": portfolio_ids})


@app.get("/video/{youtube_id}", response_class=HTMLResponse)
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

    last = video.stats[-1] if video.stats else None
    channel_vids = get_channel_videos(db, video.channel_id or "", video.youtube_id)
    info = calculate_price(last.view_count, last.like_count, last.comment_count,
                           video.published_at, channel_videos=channel_vids) if last \
        else {"risk": "Unknown", "risk_color": "secondary", "momentum_pct": 0, "views_per_day": 0, "rps": 1.0}

    price_history = [{"t": s.recorded_at.strftime("%d.%m %H:%M"), "p": s.price_at_time,
                      "ts": s.recorded_at.timestamp()}
                     for s in video.stats]

    # Holding from DB
    h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=video.id).first()
    holding = {"shares": h.shares, "avg_cost": h.avg_cost_basis} if h and h.shares > 0.001 else None

    related = []
    if video.channel_id:
        related = (db.query(models.Video)
                   .filter(models.Video.channel_id == video.channel_id,
                           models.Video.youtube_id != youtube_id)
                   .order_by(models.Video.current_price.desc())
                   .limit(5).all())

    watchlist   = request.session.get("watchlist", [])
    is_watching = youtube_id in watchlist

    return templates.TemplateResponse("video.html", {
        "request": request, "user": user, "video": video,
        "last_stat": last, "info": info, "price_history": price_history,
        "holding": holding, "related": related, "is_watching": is_watching,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
    })


@app.post("/watch/{youtube_id}")
async def toggle_watchlist(request: Request, youtube_id: str, db: Session = Depends(get_db)):
    if not get_login(request, db):
        return RedirectResponse("/login", status_code=302)
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
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    portfolio = get_portfolio(db_user)
    if portfolio:
        yt_list = get_video_details(list(portfolio.keys()))
        for yt in yt_list:
            upsert_video(db, yt)
    return RedirectResponse("/portfolio?msg=refreshed", status_code=302)


# ── trading ───────────────────────────────────────────────────────────────────

@app.post("/buy/{youtube_id}")
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

    h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=video.id).first()
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
    db.commit()
    db.refresh(db_user)

    record_port_snap(request, db_user)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    return RedirectResponse(f"/video/{youtube_id}?msg=bought", status_code=302)


@app.post("/sell/{youtube_id}")
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

    revenue = round(shares * video.current_price, 2)
    h.shares = round(h.shares - shares, 4)
    if h.shares <= 0.001:
        db.delete(h)

    db_user.balance = round(db_user.balance + revenue, 2)
    db.add(models.Transaction(
        user_id=db_user.id, video_id=video.id, transaction_type="sell",
        shares=shares, price_per_share=video.current_price, total_amount=revenue,
    ))
    db.commit()
    db.refresh(db_user)

    record_port_snap(request, db_user)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    return RedirectResponse(f"/video/{youtube_id}?msg=sold", status_code=302)


# ── portfolio ─────────────────────────────────────────────────────────────────

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, psort: str = "value", db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    holdings_data  = []
    total_invested = 0.0
    total_current  = 0.0

    for h in db_user.holdings:
        if h.shares <= 0.001 or not h.video:
            continue
        video       = h.video
        current_val = round(h.shares * video.current_price, 2)
        invested_val = round(h.shares * h.avg_cost_basis, 2)
        pnl         = round(current_val - invested_val, 2)
        pnl_pct     = round(pnl / invested_val * 100, 2) if invested_val else 0
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

    total_pnl      = round(total_current - total_invested, 2)
    total_pnl_pct  = round(total_pnl / total_invested * 100, 2) if total_invested else 0
    portfolio_value = round(user.balance + total_current, 2)

    donut_labels = [h["video"].title[:30] for h in holdings_data]
    donut_values = [h["current_val"] for h in holdings_data]
    if user.balance > 0:
        donut_labels.append("Cash")
        donut_values.append(round(user.balance, 2))

    # Transactions from DB (replaces session list)
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

    port_snaps = request.session.get("port_snaps", [])

    return templates.TemplateResponse("portfolio.html", {
        "request": request, "user": user,
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
    })


# ── leaderboard ───────────────────────────────────────────────────────────────

@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

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
