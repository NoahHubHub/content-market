import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from deps import templates
from helpers import (
    get_login, UserCtx, calc_total_portfolio_value, _build_league_board,
    get_or_create_season, ensure_season_entry, get_hot_take_video,
    upsert_leaderboard, get_max_portfolio_slots,
)
from models import get_level_info

router = APIRouter()


@router.post("/hottake/{video_id}")
async def submit_hot_take(request: Request, video_id: int,
                          prediction: str = Form(...), db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    # Erlaube einen Tipp pro Video pro Tag (nicht mehr nur einen insgesamt)
    if db.query(models.HotTake).filter_by(user_id=db_user.id, video_id=video_id, date=today).first():
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


@router.get("/season", response_class=HTMLResponse)
async def season_page(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    season = get_or_create_season(db)
    ensure_season_entry(db_user, db)

    entries = db.query(models.SeasonEntry).filter_by(season_id=season.id).all()
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


@router.post("/duel/challenge")
async def challenge_duel(request: Request, opponent_username: str = Form(...),
                         db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    opponent = db.query(models.User).filter_by(username=opponent_username).first()
    if not opponent or opponent.id == db_user.id:
        return RedirectResponse("/duels?err=not_found", status_code=302)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    end   = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
    db.add(models.Duel(
        challenger_id=db_user.id, opponent_id=opponent.id,
        start_date=today, end_date=end,
        challenger_start=calc_total_portfolio_value(db_user),
        opponent_start=calc_total_portfolio_value(opponent),
        status="active",
    ))
    db.commit()

    # Notify the opponent via push (best-effort)
    try:
        from routers.push import send_push_to_user
        send_push_to_user(
            opponent.id,
            title="⚔️ Duell-Herausforderung!",
            body=f"{db_user.username} fordert dich heraus — 7 Tage, beste Rendite gewinnt.",
            url="/duels",
            db=db,
        )
    except Exception:
        pass

    return RedirectResponse("/duels?msg=challenged", status_code=302)


@router.get("/duels", response_class=HTMLResponse)
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

        opponent     = d.opponent if d.challenger_id == db_user.id else d.challenger
        is_challenger = d.challenger_id == db_user.id
        my_start  = d.challenger_start if is_challenger else d.opponent_start
        my_val    = calc_total_portfolio_value(db_user)
        opp_val   = calc_total_portfolio_value(opponent)
        opp_start = d.opponent_start if is_challenger else d.challenger_start

        days_left = None
        if d.status == "active":
            try:
                end_dt = datetime.strptime(d.end_date, "%Y-%m-%d")
                days_left = max(0, (end_dt - datetime.utcnow()).days)
            except Exception:
                pass

        duel_data.append({
            "duel": d, "opponent": opponent,
            "my_return":  round((my_val  - my_start)  / max(my_start, 1)  * 100, 2),
            "opp_return": round((opp_val - opp_start) / max(opp_start, 1) * 100, 2),
            "i_am_winning": (my_val - my_start) / max(my_start, 1) >=
                            (opp_val - opp_start) / max(opp_start, 1),
            "i_won": d.status == "completed" and d.winner_id == db_user.id,
            "days_left": days_left,
        })

    return templates.TemplateResponse(request, "duels.html", {
        "user": user, "duel_data": duel_data,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
    })


@router.get("/leagues", response_class=HTMLResponse)
async def leagues_page(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        # Preserve invite code through login redirect
        code = request.query_params.get("code", "")
        return RedirectResponse(f"/login?next=/leagues{'?code=' + code if code else ''}", status_code=302)
    user = UserCtx(db_user)

    memberships = db.query(models.LeagueMember).filter_by(user_id=db_user.id).all()
    my_leagues  = []
    for m in memberships:
        league = m.league
        my_val = calc_total_portfolio_value(db_user)
        ret    = round((my_val - m.start_value) / max(m.start_value, 1) * 100, 2)
        board  = _build_league_board(league, db)
        my_rank = next((i + 1 for i, e in enumerate(board) if e["username"] == db_user.username), None)
        my_leagues.append({
            "league": league, "my_return": ret, "my_rank": my_rank,
            "member_count": len(league.members),
            "latest_activity": league.activities[-1] if league.activities else None,
        })

    return templates.TemplateResponse(request, "leagues.html", {
        "user": user, "my_leagues": my_leagues,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
    })


@router.post("/leagues/create")
async def create_league(request: Request, league_name: str = Form(...),
                        db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    # Free users can create exactly 1 league; Premium users have no limit
    if not db_user.is_premium:
        created_count = db.query(models.League).filter_by(creator_id=db_user.id).count()
        if created_count >= 1:
            return RedirectResponse("/premium?ref=leagues_create_limit", status_code=302)

    for _ in range(10):
        code = secrets.token_hex(3).upper()
        if not db.query(models.League).filter_by(invite_code=code).first():
            break

    league = models.League(name=league_name.strip(), invite_code=code, creator_id=db_user.id)
    db.add(league)
    db.flush()
    db.add(models.LeagueMember(
        league_id=league.id, user_id=db_user.id, username=db_user.username,
        start_value=calc_total_portfolio_value(db_user),
    ))
    db.commit()
    return RedirectResponse(f"/leagues/{league.id}?msg=created", status_code=302)


@router.post("/leagues/join")
async def join_league(request: Request, invite_code: str = Form(...),
                      db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    league = db.query(models.League).filter_by(invite_code=invite_code.strip().upper()).first()
    if not league:
        return RedirectResponse("/leagues?err=code_not_found", status_code=302)

    if db.query(models.LeagueMember).filter_by(league_id=league.id, user_id=db_user.id).first():
        return RedirectResponse(f"/leagues/{league.id}?msg=already_member", status_code=302)

    # Free-tier: max 1 league
    if not db_user.is_premium:
        current_count = db.query(models.LeagueMember).filter_by(user_id=db_user.id).count()
        if current_count >= 1:
            return RedirectResponse("/premium?ref=leagues_join", status_code=302)

    db.add(models.LeagueMember(
        league_id=league.id, user_id=db_user.id, username=db_user.username,
        start_value=calc_total_portfolio_value(db_user),
    ))
    db.commit()
    return RedirectResponse(f"/leagues/{league.id}?msg=joined", status_code=302)


@router.get("/leagues/{league_id}", response_class=HTMLResponse)
async def league_detail(request: Request, league_id: int, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    league = db.query(models.League).filter_by(id=league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="Liga nicht gefunden")

    if not db.query(models.LeagueMember).filter_by(league_id=league_id, user_id=db_user.id).first():
        return RedirectResponse("/leagues?err=not_member", status_code=302)

    board      = _build_league_board(league, db)
    activities = sorted(league.activities, key=lambda a: a.created_at, reverse=True)[:30]

    return templates.TemplateResponse(request, "league_detail.html", {
        "user": user, "league": league,
        "board": board, "activities": activities,
        "is_creator": league.creator_id == db_user.id,
        "msg": request.query_params.get("msg"),
    })


@router.get("/hottakes", response_class=HTMLResponse)
async def hottakes_page(request: Request, db: Session = Depends(get_db)):
    """Globales Hot-Take-Scoreboard: beste Predictor + eigene Hit-Rate."""
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    resolved = db.query(models.HotTake).filter_by(resolved=True).all()

    # Aggregate per user
    stats: dict = {}
    for ht in resolved:
        if not ht.user:
            continue
        uname = ht.user.username
        if uname not in stats:
            stats[uname] = {"correct": 0, "total": 0}
        stats[uname]["total"] += 1
        if ht.correct:
            stats[uname]["correct"] += 1

    board = []
    for uname, s in stats.items():
        if s["total"] < 2:
            continue
        hit_rate = round(s["correct"] / s["total"] * 100, 1)
        board.append({
            "username": uname,
            "correct": s["correct"],
            "total": s["total"],
            "hit_rate": hit_rate,
            "is_me": uname == db_user.username,
        })
    board.sort(key=lambda x: (x["hit_rate"], x["total"]), reverse=True)

    my_stats = stats.get(db_user.username)
    my_hit_rate = None
    if my_stats and my_stats["total"] > 0:
        my_hit_rate = round(my_stats["correct"] / my_stats["total"] * 100, 1)
    my_total = my_stats["total"] if my_stats else 0
    my_correct = my_stats["correct"] if my_stats else 0

    return templates.TemplateResponse(request, "hottakes.html", {
        "user": user, "board": board[:20],
        "my_hit_rate": my_hit_rate,
        "my_total": my_total,
        "my_correct": my_correct,
    })


@router.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    user = UserCtx(db_user)

    all_entries = (db.query(models.LeaderboardEntry)
                   .order_by(models.LeaderboardEntry.portfolio_value.desc()).all())
    usernames = [e.username for e in all_entries]
    user_map  = (
        {u.username: u for u in db.query(models.User).filter(models.User.username.in_(usernames)).all()}
        if usernames else {}
    )

    board, my_rank = [], None
    for i, e in enumerate(all_entries, 1):
        u = user_map.get(e.username)
        entry = {
            "username": e.username,
            "portfolio_value": e.portfolio_value,
            "return_pct": e.return_pct,
            "streak": u.streak_days if u else 0,
            "level": get_level_info(u.xp or 0)["level"] if u else 1,
        }
        if e.username == db_user.username:
            my_rank = i
        if i <= 20:
            board.append(entry)

    if not board:
        board = [{"username": user.username, "portfolio_value": user.balance,
                  "return_pct": round((user.balance - 10000) / 10000 * 100, 2),
                  "streak": db_user.streak_days or 0, "level": user.level_info["level"]}]
        my_rank = 1

    me_in_top20 = any(e["username"] == db_user.username for e in board)
    my_entry = None
    if my_rank and not me_in_top20:
        my_val = calc_total_portfolio_value(db_user)
        my_entry = {
            "rank": my_rank, "username": db_user.username,
            "portfolio_value": round(my_val, 2),
            "return_pct": round((my_val - 10000) / 10000 * 100, 2),
            "streak": db_user.streak_days or 0,
            "level": user.level_info["level"],
        }

    return templates.TemplateResponse(request, "leaderboard.html", {
        "user": user, "board": board,
        "my_rank": my_rank, "total_players": len(all_entries),
        "my_entry": my_entry,
    })


@router.get("/u/{username}", response_class=HTMLResponse)
async def player_profile(request: Request, username: str, db: Session = Depends(get_db)):
    """Öffentliches Spielerprofil."""
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    viewer = UserCtx(db_user)

    profile_user = db.query(models.User).filter_by(username=username).first()
    if not profile_user:
        raise HTTPException(status_code=404, detail="Spieler nicht gefunden")

    profile = UserCtx(profile_user)
    portfolio_val = calc_total_portfolio_value(profile_user)
    return_pct = round((portfolio_val - 10000) / 10000 * 100, 2)

    # Top 3 öffentliche Holdings (nur Videotitel + P&L, keine Anteile/Preise)
    top_holdings = []
    for h in profile_user.holdings:
        if h.shares > 0.001 and h.video and h.avg_cost_basis > 0:
            pct = (h.video.current_price - h.avg_cost_basis) / h.avg_cost_basis * 100
            top_holdings.append({
                "youtube_id": h.video.youtube_id,
                "title": h.video.title,
                "thumbnail_url": h.video.thumbnail_url,
                "pnl_pct": round(pct, 1),
            })
    top_holdings.sort(key=lambda x: x["pnl_pct"], reverse=True)

    earned_achievements = [
        {**models.ACHIEVEMENTS[a.achievement_id], "id": a.achievement_id}
        for a in profile_user.achievements
        if a.achievement_id in models.ACHIEVEMENTS
    ]

    trade_count = len(profile_user.transactions)
    is_own_profile = profile_user.id == db_user.id

    # Laufendes Duell zwischen Viewer und Profil?
    active_duel = None
    for d in db_user.duels_sent + db_user.duels_received:
        opp_id = d.opponent_id if d.challenger_id == db_user.id else d.challenger_id
        if opp_id == profile_user.id and d.status == "active":
            active_duel = d
            break

    return templates.TemplateResponse(request, "user_profile.html", {
        "user": viewer, "profile": profile,
        "portfolio_val": portfolio_val, "return_pct": return_pct,
        "top_holdings": top_holdings[:3],
        "earned_achievements": earned_achievements,
        "trade_count": trade_count,
        "is_own_profile": is_own_profile,
        "active_duel": active_duel,
        "max_slots": get_max_portfolio_slots(profile_user),
    })
