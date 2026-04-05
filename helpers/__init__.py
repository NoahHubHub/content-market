"""helpers package — re-exports all public symbols for backwards compatibility.

Modules:
  auth        — get_login, hash_pw, verify_pw, UserCtx, get_portfolio, get_max_portfolio_slots
  portfolio   — calc_total_portfolio_value, upsert_leaderboard, record_port_snap
  video       — upsert_video, record_price_snap, get_channel_videos, compute_price_change_pct,
                 get_market_feed, get_hidden_gems
  gamification — XP_*, STREAK_BONUS, ensure_tasks, update_tasks, get_current_tasks, check_achievements
  social      — log_league_activity, _build_league_board, get_user_leagues_preview, get_user_active_duels
  market      — get_todays_drops, get_hot_take_video, get_or_create_season, ensure_season_entry,
                 sync_watchlist_to_db
"""

from helpers.auth import (
    get_login, hash_pw, verify_pw, UserCtx, get_portfolio,
    get_max_portfolio_slots, BASE_FREE_SLOTS,
)
from helpers.portfolio import (
    calc_total_portfolio_value, upsert_leaderboard, record_port_snap,
)
from helpers.video import (
    upsert_video, record_price_snap, get_channel_videos,
    compute_price_change_pct, get_market_feed, get_hidden_gems,
)
from helpers.gamification import (
    XP_BUY, XP_SELL_PROFIT, XP_SELL_LOSS, XP_DAILY_LOGIN, STREAK_BONUS,
    ensure_tasks, update_tasks, get_current_tasks, check_achievements,
)
from helpers.social import (
    log_league_activity, _build_league_board,
    get_user_leagues_preview, get_user_active_duels,
)
from helpers.market import (
    get_todays_drops, get_hot_take_video, get_or_create_season,
    ensure_season_entry, sync_watchlist_to_db,
)

__all__ = [
    # auth
    "get_login", "hash_pw", "verify_pw", "UserCtx", "get_portfolio",
    "get_max_portfolio_slots", "BASE_FREE_SLOTS",
    # portfolio
    "calc_total_portfolio_value", "upsert_leaderboard", "record_port_snap",
    # video
    "upsert_video", "record_price_snap", "get_channel_videos",
    "compute_price_change_pct", "get_market_feed", "get_hidden_gems",
    # gamification
    "XP_BUY", "XP_SELL_PROFIT", "XP_SELL_LOSS", "XP_DAILY_LOGIN", "STREAK_BONUS",
    "ensure_tasks", "update_tasks", "get_current_tasks", "check_achievements",
    # social
    "log_league_activity", "_build_league_board",
    "get_user_leagues_preview", "get_user_active_duels",
    # market
    "get_todays_drops", "get_hot_take_video", "get_or_create_season",
    "ensure_season_entry", "sync_watchlist_to_db",
]
