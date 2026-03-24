import os
import secrets
import traceback
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from database import Base, engine, get_db, SessionLocal
import models
from models import get_level_info, ACHIEVEMENTS, generate_tasks_for_level
from pricing import calculate_price
from youtube import extract_video_id, get_video_by_id, get_video_details, search_videos, get_trending_videos

load_dotenv()
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Content Market")
_session_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_session_key, max_age=86400 * 30)
templates = Jinja2Templates(directory="templates")

# ── XP rewards ────────────────────────────────────────────────────────────────
XP_BUY         = 10
XP_SELL_PROFIT = 30
XP_SELL_LOSS   = 5
XP_DAILY_LOGIN = 5

STREAK_BONUS = {3: 15, 7: 50, 14: 100, 30: 250}  # Streak-Tag → Bonus XP

# ── Scheduler: auto-refresh prices & daily drop ───────────────────────────────

def _auto_refresh_prices():
    """Täglich alle Videos mit aktiven Holdings neu laden."""
    db = SessionLocal()
    try:
        active = db.query(models.Holding).filter(models.Holding.shares > 0.001).all()
        yt_ids = list({h.video.youtube_id for h in active if h.video})
        if not yt_ids:
            return
        for i in range(0, len(yt_ids), 50):
            batch = yt_ids[i:i+50]
            try:
                yt_list = get_video_details(batch)
                for yt in yt_list:
                    upsert_video(db, yt)
            except Exception:
                pass
    finally:
        db.close()


def _generate_daily_drop():
    """Täglich 5 Videos mit höchstem Momentum als Daily Drop featuren."""
    db = SessionLocal()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        existing = db.query(models.DailyDrop).filter_by(date=today).count()
        if existing > 0:
            return

        videos = db.query(models.Video).order_by(models.Video.last_updated.desc()).limit(50).all()
        scored = []
        for v in videos:
            if not v.stats:
                continue
            last = v.stats[-1]
            info = calculate_price(
                last.view_count, last.like_count, last.comment_count, v.published_at
            )
            scored.append((info["momentum_pct"], v))

        scored.sort(key=lambda x: x[0], reverse=True)
        top5 = [v for _, v in scored[:5]]

        for video in top5:
            db.add(models.DailyDrop(
                video_id=video.id,
                date=today,
                total_shares=100.0,
                shares_remaining=100.0,
            ))
        db.commit()
    finally:
        db.close()


def _seed_market():
    """Markt mit Trending-Videos befüllen falls er leer ist."""
    db = SessionLocal()
    try:
        count = db.query(models.Video).count()
        if count >= 15:
            return
        try:
            yt_list = get_trending_videos(region="DE", max_results=20)
            for yt in yt_list:
                upsert_video(db, yt)
        except Exception:
            pass
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(_auto_refresh_prices, "cron", hour=6,  minute=0)
scheduler.add_job(_generate_daily_drop,  "cron", hour=0,  minute=5)
scheduler.add_job(_seed_market,          "cron", hour=3,  minute=0)
scheduler.add_job(_resolve_hot_takes,    "cron", hour=7,  minute=0)
scheduler.add_job(_end_season,           "cron", day_of_week="mon", hour=0, minute=10)
scheduler.start()

# Beim Start sofort seeden falls nötig
try:
    _seed_market()
    _generate_daily_drop()
except Exception:
    pass


def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# ── achievements ───────────────────────────────────────────────────────────────

def check_achievements(db_user: models.User, db: Session) -> list:
    """Prüft alle Achievements und gibt neu freigeschaltete zurück."""
    from datetime import timedelta
    earned_ids = {a.achievement_id for a in db_user.achievements}
    newly_earned = []

    def unlock(aid: str):
        if aid not in earned_ids:
            a = ACHIEVEMENTS[aid]
            db.add(models.UserAchievement(user_id=db_user.id, achievement_id=aid))
            db_user.xp = (db_user.xp or 0) + a["xp"]
            earned_ids.add(aid)
            newly_earned.append(aid)

    # Erste Investition
    total_trades = len(db_user.transactions)
    if total_trades >= 1:
        unlock("first_buy")

    # 10 Trades
    if total_trades >= 10:
        unlock("trader_10")

    # Diversifiziert: 3+ aktive Positionen
    active_holdings = [h for h in db_user.holdings if h.shares > 0.001]
    if len(active_holdings) >= 3:
        unlock("diversified")

    # Wal: 50+ Anteile einer einzelnen Position
    for h in active_holdings:
        if h.shares >= 50:
            unlock("whale")
            break

    # Ersten Gewinn
    sell_txs = [t for t in db_user.transactions if t.transaction_type == "sell"]
    for t in sell_txs:
        buy_txs = [b for b in db_user.transactions
                   if b.transaction_type == "buy" and b.video_id == t.video_id
                   and b.executed_at < t.executed_at]
        if buy_txs:
            avg_buy = sum(b.price_per_share for b in buy_txs) / len(buy_txs)
            if t.price_per_share > avg_buy:
                unlock("first_profit")
                break

    # Diamond Hands: Position 7+ Tage gehalten
    for h in active_holdings:
        first_buy = db.query(models.Transaction).filter_by(
            user_id=db_user.id, video_id=h.video_id, transaction_type="buy"
        ).order_by(models.Transaction.executed_at).first()
        if first_buy and (datetime.utcnow() - first_buy.executed_at).days >= 7:
            unlock("diamond_hands")
            break

    # Streak Achievements
    if (db_user.streak_days or 0) >= 3:
        unlock("streak_3")
    if (db_user.streak_days or 0) >= 7:
        unlock("streak_7")

    # Level 5
    if get_level_info(db_user.xp or 0)["level"] >= 5:
        unlock("level_5")

    # Daily Drop gekauft
    for t in db_user.transactions:
        if t.transaction_type == "buy":
            drop = db.query(models.DailyDrop).filter_by(video_id=t.video_id).first()
            if drop:
                unlock("daily_drop")
                break

    if newly_earned:
        db.commit()
    return newly_earned

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
        self.xp              = db_user.xp or 0
        self.level_info      = get_level_info(self.xp)
        self.level           = db_user.level or 1
        self.streak_days     = db_user.streak_days or 0
        self.tutorial_step   = db_user.tutorial_step if db_user.tutorial_step is not None else 0


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
        from datetime import timedelta
        is_ipo = (
            yt["published_at"] is not None and
            (datetime.utcnow() - yt["published_at"]).total_seconds() < 86400
        )
        video = models.Video(
            youtube_id=yt["youtube_id"], title=yt["title"],
            channel_name=yt["channel_name"], channel_id=yt.get("channel_id", ""),
            thumbnail_url=yt["thumbnail_url"], published_at=yt["published_at"],
            current_price=price_data["price"],
            is_ipo=is_ipo,
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


# ── task system ───────────────────────────────────────────────────────────────

def ensure_tasks(db_user: models.User, db: Session):
    """Erstellt Tasks für den User falls noch keine aktiven vorhanden."""
    active = [t for t in db_user.tasks if not t.completed and t.level_assigned == (db_user.level or 1)]
    if not active:
        _assign_new_tasks(db_user, db)


def _assign_new_tasks(db_user: models.User, db: Session):
    """Generiert 3 neue Tasks für das aktuelle Level."""
    level = db_user.level or 1
    chosen = generate_tasks_for_level(level)
    for t in chosen:
        db.add(models.UserTask(
            user_id=db_user.id,
            task_type=t["type"],
            name=t["name"],
            icon=t["icon"],
            desc=t["desc"],
            target=t["target"],
            progress=0,
            completed=False,
            level_assigned=level,
        ))
    db.commit()
    db.refresh(db_user)


def update_tasks(db_user: models.User, db: Session, event: str, value: int = 1) -> bool:
    """
    Aktualisiert Task-Fortschritt nach einem Event.
    Events: 'buy', 'sell', 'profit', 'daily_drop', 'streak', 'portfolio', 'trades', 'invest', 'watchlist'
    Gibt True zurück wenn ein Level-Up passiert ist.
    """
    active_tasks = [t for t in db_user.tasks
                    if not t.completed and t.level_assigned == (db_user.level or 1)
                    and t.task_type == event]

    for task in active_tasks:
        task.progress = min(task.progress + value, task.target)
        if task.progress >= task.target:
            task.completed = True

    db.commit()
    db.refresh(db_user)

    # Prüfen ob alle Tasks abgeschlossen → Level Up
    all_tasks = [t for t in db_user.tasks if t.level_assigned == (db_user.level or 1)]
    if all_tasks and all(t.completed for t in all_tasks):
        return _level_up(db_user, db)
    return False


def _level_up(db_user: models.User, db: Session) -> bool:
    """Level-Up durchführen und neue Tasks generieren."""
    db_user.level = (db_user.level or 1) + 1
    db_user.xp = (db_user.xp or 0) + 200  # Bonus XP für Level-Up
    db.commit()
    _assign_new_tasks(db_user, db)
    return True


def get_current_tasks(db_user: models.User, db: Session) -> list:
    """Holt die aktuellen 3 Tasks des Users."""
    ensure_tasks(db_user, db)
    return [t for t in db_user.tasks
            if t.level_assigned == (db_user.level or 1)]


# ── auth routes ───────────────────────────────────────────────────────────────

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html",
        {"user": None, "error": None})


@app.post("/register", response_class=HTMLResponse)
async def register(request: Request, username: str = Form(...), password: str = Form(...),
                   db: Session = Depends(get_db)):
    if len(username) < 3:
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": "Username zu kurz (min. 3 Zeichen)"})
    if db.query(models.User).filter(models.User.username == username).first():
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": "Username bereits vergeben"})
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
    return templates.TemplateResponse(request, "login.html",
        {"user": None, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...),
                db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.username == username).first()
    if not db_user or not _verify_pw(password, db_user.password_hash):
        return templates.TemplateResponse(request, "login.html",
            {"user": None, "error": "Ungültige Zugangsdaten"})
    request.session.clear()
    request.session["user_id"] = db_user.id
    return RedirectResponse("/", status_code=302)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.post("/vy/dismiss")
async def vy_dismiss(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if db_user and (db_user.tutorial_step or 0) == 0:
        db_user.tutorial_step = 1
        db.commit()
    return RedirectResponse("/", status_code=302)

@app.post("/tutorial/next")
async def tutorial_next(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if db_user and (db_user.tutorial_step or 0) < 99:
        db_user.tutorial_step = (db_user.tutorial_step or 0) + 1
        db.commit()
    return RedirectResponse(request.headers.get("referer", "/"), status_code=302)


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

    # Streak & Daily Login XP
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

    daily_drops   = get_todays_drops(db)
    current_tasks = get_current_tasks(db_user, db)
    _ensure_season_entry(db_user, db)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    bonus_available   = db_user.last_bonus_date != today
    hot_take_video    = get_hot_take_video(db)
    today_hot_take    = db.query(models.HotTake).filter_by(
        user_id=db_user.id, date=today
    ).first() if hot_take_video else None
    yesterday         = (datetime.utcnow() - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_take    = db.query(models.HotTake).filter_by(
        user_id=db_user.id, date=yesterday, resolved=True
    ).first()

    return templates.TemplateResponse(request, "index.html", {
        "user": user,
        "video_data": video_data, "trending": trending, "sort": sort,
        "portfolio_ids": portfolio_ids, "watchlist_data": watchlist_data,
        "daily_drops": daily_drops,
        "streak_bonus": streak_bonus,
        "current_tasks": current_tasks,
        "bonus_available": bonus_available,
        "bonus_msg": request.query_params.get("bonus"),
        "bonus_amount": request.query_params.get("amount"),
        "hot_take_video": hot_take_video,
        "today_hot_take": today_hot_take,
        "yesterday_take": yesterday_take,
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

    return templates.TemplateResponse(request, "search.html",
        {"user": user, "query": q, "results": results,
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

    # Tutorial: Schritt 1→2 wenn User erstmals ein Video öffnet
    if (db_user.tutorial_step or 0) == 1:
        db_user.tutorial_step = 2
        db.commit()
        user = UserCtx(db_user)

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

    return templates.TemplateResponse(request, "video.html", {
        "user": user, "video": video,
        "last_stat": last, "info": info, "price_history": price_history,
        "holding": holding, "related": related, "is_watching": is_watching,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
        "xp":  request.query_params.get("xp"),
        "new_achievements": [
            ACHIEVEMENTS[aid] for aid in
            (request.query_params.get("ach") or "").split(",")
            if aid in ACHIEVEMENTS
        ],
    })


@app.post("/watch/{youtube_id}")
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

    db_user.xp = (db_user.xp or 0) + XP_BUY
    # Tutorial: Schritt 2→3 nach erstem Kauf
    if (db_user.tutorial_step or 0) <= 2:
        db_user.tutorial_step = 3
    db.commit()
    # Task-Updates
    active_holdings = len([h for h in db_user.holdings if h.shares > 0.001])
    total_invested  = sum(t.total_amount for t in db_user.transactions if t.transaction_type == "buy")
    total_trades    = len(db_user.transactions)
    update_tasks(db_user, db, "buy",       value=1)
    update_tasks(db_user, db, "trades",    value=total_trades)
    update_tasks(db_user, db, "portfolio", value=active_holdings)
    leveled_up = update_tasks(db_user, db, "invest", value=int(total_invested))
    new_achievements = check_achievements(db_user, db)
    record_port_snap(request, db_user)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    ach_param = ",".join(new_achievements) if new_achievements else ""
    return RedirectResponse(f"/video/{youtube_id}?msg=bought&xp={XP_BUY}&ach={ach_param}&lvl={'1' if leveled_up else ''}", status_code=302)


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

    avg_cost = h.avg_cost_basis
    revenue = round(shares * video.current_price, 2)
    h.shares = round(h.shares - shares, 4)
    if h.shares <= 0.001:
        db.delete(h)

    db_user.balance = round(db_user.balance + revenue, 2)
    db.add(models.Transaction(
        user_id=db_user.id, video_id=video.id, transaction_type="sell",
        shares=shares, price_per_share=video.current_price, total_amount=revenue,
    ))

    profit = video.current_price >= avg_cost
    xp_gained = XP_SELL_PROFIT if profit else XP_SELL_LOSS
    db_user.xp = (db_user.xp or 0) + xp_gained
    db.commit()
    db.refresh(db_user)
    # Task-Updates
    total_trades = len(db_user.transactions)
    update_tasks(db_user, db, "sell",   value=1)
    update_tasks(db_user, db, "trades", value=total_trades)
    if profit:
        update_tasks(db_user, db, "profit", value=1)
    leveled_up = False  # sell allein löst kein Level-Up aus (invest/portfolio fehlen)
    new_achievements = check_achievements(db_user, db)
    record_port_snap(request, db_user)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    ach_param = ",".join(new_achievements) if new_achievements else ""
    return RedirectResponse(f"/video/{youtube_id}?msg=sold&xp={xp_gained}&ach={ach_param}", status_code=302)


# ── portfolio ─────────────────────────────────────────────────────────────────

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, psort: str = "value", db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    # Tutorial: Schritt 3→99 (fertig) wenn Portfolio besucht wird
    if (db_user.tutorial_step or 0) == 3:
        db_user.tutorial_step = 99
        db.commit()

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

    return templates.TemplateResponse(request, "portfolio.html", {
        "user": user,
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
        "all_achievements": ACHIEVEMENTS,
        "user_achievements": {a.achievement_id for a in db_user.achievements},
    })


# ── daily drop ────────────────────────────────────────────────────────────────

def get_todays_drops(db: Session) -> list:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    drops = db.query(models.DailyDrop).filter_by(date=today).all()
    result = []
    for drop in drops:
        if not drop.video or not drop.video.stats:
            continue
        last = drop.video.stats[-1]
        info = calculate_price(
            last.view_count, last.like_count, last.comment_count, drop.video.published_at
        )
        result.append({"drop": drop, "video": drop.video, "info": info})
    return result


@app.post("/daily-drop/buy/{drop_id}")
async def buy_daily_drop(request: Request, drop_id: int, shares: float = Form(...),
                         db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    drop = db.query(models.DailyDrop).filter_by(id=drop_id).first()
    if not drop or not drop.video:
        raise HTTPException(status_code=404)

    shares = round(min(shares, drop.shares_remaining), 4)
    if shares <= 0:
        return RedirectResponse(f"/video/{drop.video.youtube_id}?err=drop_sold_out", status_code=302)

    total_cost = round(shares * drop.video.current_price, 2)
    if db_user.balance < total_cost:
        return RedirectResponse(f"/video/{drop.video.youtube_id}?err=insufficient_funds", status_code=302)

    drop.shares_remaining = round(drop.shares_remaining - shares, 4)

    h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=drop.video_id).first()
    if h:
        new_total = h.shares + shares
        h.avg_cost_basis = round(
            (h.shares * h.avg_cost_basis + shares * drop.video.current_price) / new_total, 4
        )
        h.shares = round(new_total, 4)
    else:
        db.add(models.Holding(
            user_id=db_user.id, video_id=drop.video_id,
            shares=round(shares, 4), avg_cost_basis=round(drop.video.current_price, 4),
        ))

    db_user.balance = round(db_user.balance - total_cost, 2)
    db.add(models.Transaction(
        user_id=db_user.id, video_id=drop.video_id, transaction_type="buy",
        shares=shares, price_per_share=drop.video.current_price, total_amount=total_cost,
    ))
    db_user.xp = (db_user.xp or 0) + XP_BUY + 5  # Bonus XP für Daily Drop
    db.commit()
    db.refresh(db_user)
    total_drops = sum(1 for t in db_user.transactions
                      if t.transaction_type == "buy" and
                      db.query(models.DailyDrop).filter_by(video_id=t.video_id).first())
    update_tasks(db_user, db, "buy",        value=1)
    update_tasks(db_user, db, "daily_drop", value=total_drops)
    leveled_up = update_tasks(db_user, db, "trades", value=len(db_user.transactions))
    new_achievements = check_achievements(db_user, db)
    upsert_leaderboard(db_user.username, calc_total_portfolio_value(db_user), db)
    ach_param = ",".join(new_achievements) if new_achievements else ""
    return RedirectResponse(f"/video/{drop.video.youtube_id}?msg=bought&xp={XP_BUY + 5}&ach={ach_param}&lvl={'1' if leveled_up else ''}", status_code=302)


# ── daily bonus ───────────────────────────────────────────────────────────────

@app.post("/bonus/claim")
async def claim_bonus(request: Request, db: Session = Depends(get_db)):
    import random as _random
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if db_user.last_bonus_date == today:
        return RedirectResponse("/?bonus=already", status_code=302)

    db_user.last_bonus_date = today
    roll = _random.random()
    if roll < 0.33:
        xp_gain = _random.randint(15, 50)
        db_user.xp = (db_user.xp or 0) + xp_gain
        db.commit()
        return RedirectResponse(f"/?bonus=xp&amount={xp_gain}", status_code=302)
    elif roll < 0.66:
        cash = _random.choice([50, 75, 100, 150, 200])
        db_user.balance = round(db_user.balance + cash, 2)
        db.commit()
        return RedirectResponse(f"/?bonus=cash&amount={cash}", status_code=302)
    else:
        video = db.query(models.Video).order_by(models.Video.last_updated.desc()).first()
        if video:
            h = db.query(models.Holding).filter_by(user_id=db_user.id, video_id=video.id).first()
            if h:
                new_total = h.shares + 1
                h.avg_cost_basis = round(
                    (h.shares * h.avg_cost_basis + video.current_price) / new_total, 4
                )
                h.shares = round(new_total, 4)
            else:
                db.add(models.Holding(
                    user_id=db_user.id, video_id=video.id,
                    shares=1.0, avg_cost_basis=round(video.current_price, 4),
                ))
            db.commit()
            return RedirectResponse(f"/?bonus=share&title={video.title[:30]}", status_code=302)
        db.commit()
        return RedirectResponse("/?bonus=xp&amount=20", status_code=302)


# ── hot take ───────────────────────────────────────────────────────────────────

def get_hot_take_video(db: Session) -> models.Video:
    """Holt das 'Video des Tages' — dasselbe für alle User."""
    import hashlib
    today = datetime.utcnow().strftime("%Y-%m-%d")
    videos = db.query(models.Video).filter(models.Video.stats.any()).all()
    if not videos:
        return None
    idx = int(hashlib.md5(today.encode()).hexdigest(), 16) % len(videos)
    return videos[idx]


@app.post("/hottake/{video_id}")
async def submit_hot_take(request: Request, video_id: int,
                          prediction: str = Form(...), db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    existing = db.query(models.HotTake).filter_by(user_id=db_user.id, date=today).first()
    if existing:
        return RedirectResponse("/?hottake=already", status_code=302)

    video = db.query(models.Video).filter_by(id=video_id).first()
    if not video:
        return RedirectResponse("/", status_code=302)

    views_now = video.stats[-1].view_count if video.stats else 0
    db.add(models.HotTake(
        user_id=db_user.id, video_id=video_id,
        date=today, prediction=prediction,
        views_at_prediction=views_now, resolved=False,
    ))
    db.commit()
    return RedirectResponse("/?hottake=submitted", status_code=302)


def _resolve_hot_takes():
    """Löst gestrige Hot Takes auf und vergibt XP."""
    from datetime import timedelta
    db = SessionLocal()
    try:
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        takes = db.query(models.HotTake).filter_by(date=yesterday, resolved=False).all()
        for take in takes:
            if not take.video or not take.video.stats:
                take.resolved = True
                continue
            current_views = take.video.stats[-1].view_count
            went_up = current_views > take.views_at_prediction
            correct = (take.prediction == "up" and went_up) or (take.prediction == "down" and not went_up)
            take.resolved = True
            take.correct = correct
            user = db.query(models.User).filter_by(id=take.user_id).first()
            if user:
                user.xp = (user.xp or 0) + (25 if correct else 5)
        db.commit()
    finally:
        db.close()


# ── season ─────────────────────────────────────────────────────────────────────

def _get_or_create_season(db: Session) -> models.Season:
    season = db.query(models.Season).filter_by(active=True).first()
    if not season:
        last = db.query(models.Season).order_by(models.Season.season_number.desc()).first()
        num = (last.season_number + 1) if last else 1
        season = models.Season(
            season_number=num,
            start_date=datetime.utcnow().strftime("%Y-%m-%d"),
            active=True,
        )
        db.add(season)
        db.commit()
        db.refresh(season)
    return season


def _ensure_season_entry(db_user: models.User, db: Session):
    season = _get_or_create_season(db)
    entry = db.query(models.SeasonEntry).filter_by(
        season_id=season.id, username=db_user.username
    ).first()
    if not entry:
        portfolio_val = calc_total_portfolio_value(db_user)
        db.add(models.SeasonEntry(
            season_id=season.id, username=db_user.username,
            start_value=portfolio_val,
        ))
        db.commit()


def _end_season():
    """Beendet die aktuelle Season, vergibt Badges, startet neue."""
    db = SessionLocal()
    try:
        season = db.query(models.Season).filter_by(active=True).first()
        if not season:
            return
        entries = db.query(models.SeasonEntry).filter_by(season_id=season.id).all()
        for entry in entries:
            user = db.query(models.User).filter_by(username=entry.username).first()
            if user:
                val = calc_total_portfolio_value(user)
                entry.end_value = val
                entry.return_pct = round((val - entry.start_value) / max(entry.start_value, 1) * 100, 2)

        ranked = sorted([e for e in entries if e.return_pct is not None],
                        key=lambda x: x.return_pct, reverse=True)
        badges = {1: "season_gold", 2: "season_silver", 3: "season_bronze"}
        for i, entry in enumerate(ranked[:3], 1):
            entry.rank = i
            user = db.query(models.User).filter_by(username=entry.username).first()
            if user:
                badge_id = f"{badges[i]}_s{season.season_number}"
                existing = db.query(models.UserAchievement).filter_by(
                    user_id=user.id, achievement_id=badge_id
                ).first()
                if not existing:
                    db.add(models.UserAchievement(user_id=user.id, achievement_id=badge_id))
                user.xp = (user.xp or 0) + [200, 100, 50][i - 1]

        season.active = False
        season.end_date = datetime.utcnow().strftime("%Y-%m-%d")
        db.commit()

        # Neue Season starten
        last_num = season.season_number
        new_season = models.Season(
            season_number=last_num + 1,
            start_date=datetime.utcnow().strftime("%Y-%m-%d"),
            active=True,
        )
        db.add(new_season)
        db.commit()
    finally:
        db.close()


@app.get("/season", response_class=HTMLResponse)
async def season_page(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    season = _get_or_create_season(db)
    _ensure_season_entry(db_user, db)

    entries = (db.query(models.SeasonEntry)
               .filter_by(season_id=season.id)
               .all())
    board = []
    for e in entries:
        u = db.query(models.User).filter_by(username=e.username).first()
        current = calc_total_portfolio_value(u) if u else e.start_value
        ret = round((current - e.start_value) / max(e.start_value, 1) * 100, 2)
        board.append({"username": e.username, "return_pct": ret,
                      "current": round(current, 2), "is_me": e.username == db_user.username})
    board.sort(key=lambda x: x["return_pct"], reverse=True)

    return templates.TemplateResponse(request, "season.html", {
        "user": user, "season": season, "board": board,
    })


# ── duel ───────────────────────────────────────────────────────────────────────

@app.post("/duel/challenge")
async def challenge_duel(request: Request, opponent_username: str = Form(...),
                         db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    opponent = db.query(models.User).filter_by(username=opponent_username).first()
    if not opponent or opponent.id == db_user.id:
        return RedirectResponse("/duels?err=not_found", status_code=302)

    from datetime import timedelta
    today = datetime.utcnow().strftime("%Y-%m-%d")
    end = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")

    db.add(models.Duel(
        challenger_id=db_user.id, opponent_id=opponent.id,
        start_date=today, end_date=end,
        challenger_start=calc_total_portfolio_value(db_user),
        opponent_start=calc_total_portfolio_value(opponent),
        status="active",
    ))
    db.commit()
    return RedirectResponse("/duels?msg=challenged", status_code=302)


@app.get("/duels", response_class=HTMLResponse)
async def duels_page(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    all_duels = db_user.duels_sent + db_user.duels_received
    duel_data = []
    today = datetime.utcnow().strftime("%Y-%m-%d")

    for d in all_duels:
        if d.status == "active" and d.end_date <= today:
            # Auflösen
            c_val = calc_total_portfolio_value(d.challenger)
            o_val = calc_total_portfolio_value(d.opponent)
            c_ret = (c_val - d.challenger_start) / max(d.challenger_start, 1) * 100
            o_ret = (o_val - d.opponent_start) / max(d.opponent_start, 1) * 100
            d.status = "completed"
            if c_ret >= o_ret:
                d.winner_id = d.challenger_id
                winner = d.challenger
            else:
                d.winner_id = d.opponent_id
                winner = d.opponent
            winner.xp = (winner.xp or 0) + 100
            db.commit()

        opponent = d.opponent if d.challenger_id == db_user.id else d.challenger
        is_challenger = d.challenger_id == db_user.id
        my_start = d.challenger_start if is_challenger else d.opponent_start
        my_val = calc_total_portfolio_value(db_user)
        opp_val = calc_total_portfolio_value(opponent)
        opp_start = d.opponent_start if is_challenger else d.challenger_start

        duel_data.append({
            "duel": d,
            "opponent": opponent,
            "my_return": round((my_val - my_start) / max(my_start, 1) * 100, 2),
            "opp_return": round((opp_val - opp_start) / max(opp_start, 1) * 100, 2),
            "i_am_winning": (my_val - my_start) / max(my_start, 1) >=
                            (opp_val - opp_start) / max(opp_start, 1),
            "i_won": d.status == "completed" and d.winner_id == db_user.id,
        })

    return templates.TemplateResponse(request, "duels.html", {
        "user": user, "duel_data": duel_data,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
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
    board = []
    for e in entries:
        u = db.query(models.User).filter_by(username=e.username).first()
        board.append({
            "username": e.username,
            "portfolio_value": e.portfolio_value,
            "return_pct": e.return_pct,
            "streak": u.streak_days if u else 0,
            "level": get_level_info(u.xp or 0)["level"] if u else 1,
        })
    if not board:
        board = [{"username": user.username, "portfolio_value": user.balance,
                  "return_pct": round((user.balance - 10000) / 10000 * 100, 2)}]

    return templates.TemplateResponse(request, "leaderboard.html", {
        "user": user, "board": board,
    })
