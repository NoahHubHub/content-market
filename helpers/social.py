"""League, leaderboard and duel helpers."""
from sqlalchemy.orm import Session

import models
from helpers.portfolio import calc_total_portfolio_value


def log_league_activity(db: Session, db_user: models.User, action: str,
                        video: models.Video, shares: float, price: float):
    memberships = db.query(models.LeagueMember).filter_by(user_id=db_user.id).all()
    for m in memberships:
        db.add(models.LeagueActivity(
            league_id=m.league_id,
            user_id=db_user.id,
            username=db_user.username,
            action=action,
            video_title=video.title,
            youtube_id=video.youtube_id,
            shares=round(shares, 4),
            price=round(price, 2),
        ))


def _build_league_board(league: models.League, db: Session) -> list:
    member_ids = [m.user_id for m in league.members]
    users_by_id = (
        {u.id: u for u in db.query(models.User).filter(models.User.id.in_(member_ids)).all()}
        if member_ids else {}
    )
    board = []
    for m in league.members:
        u = users_by_id.get(m.user_id)
        if not u:
            continue
        current = calc_total_portfolio_value(u)
        ret = round((current - m.start_value) / max(m.start_value, 1) * 100, 2)
        board.append({
            "username": m.username,
            "start_value": m.start_value,
            "current_value": round(current, 2),
            "return_pct": ret,
            "joined_at": m.joined_at,
        })
    board.sort(key=lambda x: x["return_pct"], reverse=True)
    return board


def get_user_leagues_preview(db_user: models.User, db: Session) -> list:
    memberships = db.query(models.LeagueMember).filter_by(user_id=db_user.id).limit(3).all()
    result = []
    my_val = calc_total_portfolio_value(db_user)
    for m in memberships:
        league = m.league
        ret = round((my_val - m.start_value) / max(m.start_value, 1) * 100, 2)
        board = _build_league_board(league, db)
        my_rank = next((i + 1 for i, e in enumerate(board) if e["username"] == db_user.username), None)
        activities = sorted(league.activities, key=lambda a: a.created_at, reverse=True)
        latest = next((a for a in activities if a.user_id != db_user.id), None)
        result.append({
            "league": league, "my_return": ret, "my_rank": my_rank,
            "latest_activity": latest,
        })
    return result


def get_user_active_duels(db_user: models.User, db: Session) -> list:
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    active = []
    for d in db_user.duels_sent + db_user.duels_received:
        if d.status != "active":
            continue
        opponent = d.opponent if d.challenger_id == db_user.id else d.challenger
        is_challenger = d.challenger_id == db_user.id
        my_start  = d.challenger_start if is_challenger else d.opponent_start
        opp_start = d.opponent_start   if is_challenger else d.challenger_start
        my_val    = calc_total_portfolio_value(db_user)
        opp_val   = calc_total_portfolio_value(opponent)
        my_ret    = (my_val  - my_start)  / max(my_start, 1)  * 100
        opp_ret   = (opp_val - opp_start) / max(opp_start, 1) * 100
        days_left = None
        try:
            end_dt = datetime.strptime(d.end_date, "%Y-%m-%d")
            days_left = max(0, (end_dt - datetime.utcnow()).days)
        except Exception:
            pass
        active.append({
            "opponent_username": opponent.username,
            "my_return":  round(my_ret, 1),
            "opp_return": round(opp_ret, 1),
            "leading": my_ret >= opp_ret,
            "end_date": d.end_date,
            "days_left": days_left,
        })
    return active[:2]
