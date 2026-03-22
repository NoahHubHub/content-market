import os
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from database import Base, engine, get_db
import models
from pricing import calculate_price
from youtube import extract_video_id, get_video_by_id, search_videos

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Content Market")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret"))
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── helpers ──────────────────────────────────────────────────────────────────

def current_user(request: Request, db: Session):
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(models.User).filter(models.User.id == uid).first()


def fmt(n):
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(int(n))


templates.env.filters["fmt"] = fmt


def upsert_video(db: Session, yt: dict) -> models.Video:
    """Add or update a video in the DB and append a price snapshot."""
    video = db.query(models.Video).filter(models.Video.youtube_id == yt["youtube_id"]).first()

    prev_views = None
    if video and video.stats:
        prev_views = video.stats[-1].view_count

    price_data = calculate_price(
        view_count=yt["view_count"],
        like_count=yt["like_count"],
        comment_count=yt["comment_count"],
        published_at=yt["published_at"],
        prev_view_count=prev_views,
    )

    if not video:
        video = models.Video(
            youtube_id=yt["youtube_id"],
            title=yt["title"],
            channel_name=yt["channel_name"],
            thumbnail_url=yt["thumbnail_url"],
            published_at=yt["published_at"],
            current_price=price_data["price"],
        )
        db.add(video)
        db.flush()
    else:
        video.title = yt["title"]
        video.channel_name = yt["channel_name"]
        video.thumbnail_url = yt["thumbnail_url"]
        video.current_price = price_data["price"]
        video.last_updated = datetime.utcnow()

    stat = models.VideoStats(
        video_id=video.id,
        view_count=yt["view_count"],
        like_count=yt["like_count"],
        comment_count=yt["comment_count"],
        price_at_time=price_data["price"],
    )
    db.add(stat)
    db.commit()
    db.refresh(video)
    return video


# ── auth ─────────────────────────────────────────────────────────────────────

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if db.query(models.User).filter(models.User.username == username).first():
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "Username already taken"}
        )
    user = models.User(username=username, password_hash=pwd_context.hash(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid username or password"}
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── market ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    videos = db.query(models.Video).order_by(models.Video.last_updated.desc()).limit(24).all()

    video_data = []
    for v in videos:
        if not v.stats:
            continue
        last = v.stats[-1]
        prev = v.stats[-2] if len(v.stats) > 1 else None
        info = calculate_price(
            last.view_count, last.like_count, last.comment_count,
            v.published_at, prev.view_count if prev else None,
        )
        video_data.append({"video": v, "info": info, "last_stat": last})

    return templates.TemplateResponse(
        "index.html", {"request": request, "user": user, "video_data": video_data}
    )


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    results = []
    error = None

    if q:
        try:
            vid = extract_video_id(q)
            # If it looks like a video ID (11 chars, no spaces) → direct lookup (1 unit)
            if len(vid) == 11 and " " not in q:
                yt_list = get_video_by_id(vid)
            else:
                yt_list = search_videos(q)

            for yt in yt_list:
                video = upsert_video(db, yt)
                info = calculate_price(
                    yt["view_count"], yt["like_count"], yt["comment_count"], yt["published_at"]
                )
                results.append({"video": video, "yt": yt, "info": info})
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse(
        "search.html",
        {"request": request, "user": user, "query": q, "results": results, "error": error},
    )


@app.get("/video/{youtube_id}", response_class=HTMLResponse)
async def video_detail(
    request: Request, youtube_id: str, db: Session = Depends(get_db)
):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        yt_list = get_video_by_id(youtube_id)
        if not yt_list:
            raise HTTPException(status_code=404, detail="Video not found")
        video = upsert_video(db, yt_list[0])

    last = video.stats[-1] if video.stats else None
    prev = video.stats[-2] if len(video.stats) > 1 else None

    info = (
        calculate_price(
            last.view_count, last.like_count, last.comment_count,
            video.published_at, prev.view_count if prev else None,
        )
        if last
        else {"risk": "Unknown", "risk_color": "secondary", "momentum_pct": 0, "views_per_day": 0}
    )

    price_history = [
        {"t": s.recorded_at.strftime("%d.%m %H:%M"), "p": s.price_at_time}
        for s in video.stats[-30:]
    ]

    holding = (
        db.query(models.Holding)
        .filter(models.Holding.user_id == user.id, models.Holding.video_id == video.id)
        .first()
    )

    msg = request.query_params.get("msg")
    err = request.query_params.get("err")

    return templates.TemplateResponse(
        "video.html",
        {
            "request": request,
            "user": user,
            "video": video,
            "last_stat": last,
            "info": info,
            "price_history": price_history,
            "holding": holding,
            "msg": msg,
            "err": err,
        },
    )


@app.post("/refresh/{youtube_id}")
async def refresh_video(youtube_id: str, db: Session = Depends(get_db)):
    yt_list = get_video_by_id(youtube_id)
    if yt_list:
        upsert_video(db, yt_list[0])
    return RedirectResponse(f"/video/{youtube_id}?msg=refreshed", status_code=302)


# ── trading ───────────────────────────────────────────────────────────────────

@app.post("/buy/{youtube_id}")
async def buy(
    request: Request,
    youtube_id: str,
    shares: float = Form(...),
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        raise HTTPException(status_code=404)

    if shares <= 0:
        return RedirectResponse(f"/video/{youtube_id}?err=invalid_amount", status_code=302)

    total_cost = round(shares * video.current_price, 2)
    if user.balance < total_cost:
        return RedirectResponse(f"/video/{youtube_id}?err=insufficient_funds", status_code=302)

    user.balance = round(user.balance - total_cost, 2)

    holding = (
        db.query(models.Holding)
        .filter(models.Holding.user_id == user.id, models.Holding.video_id == video.id)
        .first()
    )
    if holding:
        new_total = holding.shares + shares
        holding.avg_cost_basis = (
            holding.shares * holding.avg_cost_basis + shares * video.current_price
        ) / new_total
        holding.shares = new_total
    else:
        db.add(
            models.Holding(
                user_id=user.id,
                video_id=video.id,
                shares=shares,
                avg_cost_basis=video.current_price,
            )
        )

    db.add(
        models.Transaction(
            user_id=user.id,
            video_id=video.id,
            transaction_type="buy",
            shares=shares,
            price_per_share=video.current_price,
            total_amount=total_cost,
        )
    )
    db.commit()
    return RedirectResponse(f"/video/{youtube_id}?msg=bought", status_code=302)


@app.post("/sell/{youtube_id}")
async def sell(
    request: Request,
    youtube_id: str,
    shares: float = Form(...),
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)

    video = db.query(models.Video).filter(models.Video.youtube_id == youtube_id).first()
    if not video:
        raise HTTPException(status_code=404)

    holding = (
        db.query(models.Holding)
        .filter(models.Holding.user_id == user.id, models.Holding.video_id == video.id)
        .first()
    )
    if not holding or holding.shares < shares:
        return RedirectResponse(f"/video/{youtube_id}?err=insufficient_shares", status_code=302)

    revenue = round(shares * video.current_price, 2)
    user.balance = round(user.balance + revenue, 2)
    holding.shares -= shares
    if holding.shares <= 0.0001:
        db.delete(holding)

    db.add(
        models.Transaction(
            user_id=user.id,
            video_id=video.id,
            transaction_type="sell",
            shares=shares,
            price_per_share=video.current_price,
            total_amount=revenue,
        )
    )
    db.commit()
    return RedirectResponse(f"/video/{youtube_id}?msg=sold", status_code=302)


# ── portfolio & leaderboard ───────────────────────────────────────────────────

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    holdings_data = []
    total_invested = 0.0
    total_current = 0.0

    for h in user.holdings:
        if h.shares <= 0:
            continue
        current_val = round(h.shares * h.video.current_price, 2)
        invested_val = round(h.shares * h.avg_cost_basis, 2)
        pnl = round(current_val - invested_val, 2)
        pnl_pct = round(pnl / invested_val * 100, 2) if invested_val else 0
        holdings_data.append(
            {
                "holding": h,
                "video": h.video,
                "current_val": current_val,
                "invested_val": invested_val,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
        )
        total_invested += invested_val
        total_current += current_val

    holdings_data.sort(key=lambda x: x["current_val"], reverse=True)

    txns = (
        db.query(models.Transaction)
        .filter(models.Transaction.user_id == user.id)
        .order_by(models.Transaction.executed_at.desc())
        .limit(15)
        .all()
    )

    total_pnl = round(total_current - total_invested, 2)
    total_pnl_pct = round(total_pnl / total_invested * 100, 2) if total_invested else 0
    portfolio_value = round(user.balance + total_current, 2)

    return templates.TemplateResponse(
        "portfolio.html",
        {
            "request": request,
            "user": user,
            "holdings_data": holdings_data,
            "total_current": round(total_current, 2),
            "total_invested": round(total_invested, 2),
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "portfolio_value": portfolio_value,
            "txns": txns,
        },
    )


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    all_users = db.query(models.User).all()
    board = []
    for u in all_users:
        val = u.balance
        for h in u.holdings:
            if h.shares > 0:
                val += h.shares * h.video.current_price
        board.append(
            {
                "user": u,
                "portfolio_value": round(val, 2),
                "return_pct": round((val - 10000) / 10000 * 100, 2),
            }
        )
    board.sort(key=lambda x: x["portfolio_value"], reverse=True)

    return templates.TemplateResponse(
        "leaderboard.html",
        {"request": request, "user": user, "board": board},
    )
