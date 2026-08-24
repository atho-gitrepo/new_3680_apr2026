#!/usr/bin/env python3
"""
BetBot - Live Match Betting Engine
Strategy: Parlay - Over 0.5 Goals + Over Corners at 25 minutes (0-0)
Filters: xG thresholds for both teams
"""

import requests
import os
import json
import time
import logging
import sys
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple
import firebase_admin
from firebase_admin import credentials, firestore
from esd.sofascore import SofascoreClient

# Import shared metrics registry to prevent circular imports
from metrics import STATE_LOCKS, BET_TRIGGERS, API_FAILURES

# Import the Staking Engine
from staking_engine import StakingEngine, STAKE_SEQUENCE

logger = logging.getLogger("BetBot.ExecutionEngine")

# --- PARAMETERS & ENV EXTRACTION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

# ============================================================
# STAKING ENGINE CONFIGURATION
# ============================================================
ORIGINAL_STAKE = 10.0
MAX_CHASE_LEVEL = 3

# Timing parameters - Check at 25 minutes for 0-0 matches
MINUTES_REGULAR_BET = [25]  # Check at 25 minutes only
SLEEP_TIME = 55

# ============================================================
# xG FILTERING CONFIGURATION
# ============================================================
# These thresholds determine when to place bets based on expected goals
# Lower xG means lower chance of goals, which might indicate a defensive match
# We want to bet on matches that are likely to see goals

XG_THRESHOLDS = {
    # Maximum allowed xG for betting (we don't want matches with already high xG)
    # If xG is too high, goals might have already been scored or will be scored soon
    'max_home_xg': 1.2,      # Maximum home team xG at 25 minutes
    'max_away_xg': 1.2,      # Maximum away team xG at 25 minutes
    'max_total_xg': 2.0,     # Maximum total xG (home + away)
    'min_total_xg': 0.3,     # Minimum total xG (avoid completely dead matches)
}

# Alternative: Use xG difference threshold
XG_DIFF_THRESHOLDS = {
    'max_xg_diff': 0.8,      # Maximum difference between home and away xG
}

# ============================================================
# ADVANCED FILTERS
# ============================================================
ADVANCED_FILTERS = {
    # Require minimum shots on target for both teams
    'min_shots_on_target': 1,      # Minimum shots on target for each team
    'min_total_shots': 3,           # Minimum total shots (on + off target)
    'min_corners_for_bet': 1,       # Minimum corners to place bet
    'max_corners_for_bet': 6,       # Maximum corners (avoid corner-heavy matches)
}

AMATEUR_KEYWORDS = ['amateur', 'youth', 'reserves', 'u18', 'u17', 'u16', 'u19', 'u22', 'u23', 'u21', 'u20', 'college']

PREDICT_START_MIN = 24
PRE_WARM_WINDOW = (23, 27)
MEMORY_PRUNE_TIMEOUT = 5400

# --- VOLATILE MEMORY CACHE MAP ---
LOCAL_TRACKED_MATCHES = {}

# --- GLOBAL STAKING ENGINE INSTANCE ---
_staking_engine: Optional[StakingEngine] = None
_staking_enabled: bool = True

# --- FIXED GEOGRAPHICAL FLAG MAP ---
COUNTRY_FLAGS = {
    "iceland": "🇮🇸", "argentina": "🇦🇷", "england": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "germany": "🇩🇪",
    "spain": "🇪🇸", "italy": "🇮🇹", "france": "🇫🇷", "brazil": "🇧🇷",
    "malaysia": "🇲🇾", "belarus": "🇧🇾", "faroe islands": "🇫🇴",
    "netherlands": "🇳🇱", "portugal": "🇵🇹", "belgium": "🇧🇪", "turkey": "🇹🇷",
    "russia": "🇷🇺", "ukraine": "🇺🇦", "scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "switzerland": "🇨🇭",
    "austria": "🇦🇹", "denmark": "🇩🇰", "sweden": "🇸🇪", "norway": "🇳🇴",
    "greece": "🇬🇷", "croatia": "🇭🇷", "poland": "🇵🇱", "united states": "🇺🇸",
    "mexico": "🇲🇽", "australia": "🇦🇺", "japan": "🇯🇵", "south korea": "🇰🇷",
    "saudi arabia": "🇸🇦", "qatar": "🇶🇦", "uae": "🇦🇪", "china": "🇨🇳",
    "egypt": "🇪🇬", "nigeria": "🇳🇬", "south africa": "🇿🇦", "chile": "🇨🇱",
    "colombia": "🇨🇴", "peru": "🇵🇪", "uruguay": "🇺🇾", "paraguay": "🇵🇾",
    "ecuador": "🇪🇨", "venezuela": "🇻🇪", "bolivia": "🇧🇴", "costarica": "🇨🇷",
    "finland": "🇫🇮", "world": "🌍"
}

# =========================
# STAKING ENGINE MANAGEMENT
# =========================

def set_staking_engine(engine: StakingEngine) -> None:
    global _staking_engine
    _staking_engine = engine
    logger.info(f"✅ Staking Engine attached to bot.")
    logger.info(f"📊 Sequence: {STAKE_SEQUENCE}")

def get_staking_engine() -> Optional[StakingEngine]:
    return _staking_engine

def enable_staking(enable: bool = True) -> None:
    global _staking_enabled
    _staking_enabled = enable
    status = "enabled" if enable else "disabled"
    logger.info(f"📊 Staking engine {status}.")
    send_telegram(f"📊 Staking engine {status}.")

def get_staking_stats() -> Dict:
    if _staking_engine:
        return _staking_engine.get_stats()
    return {
        "total_bets": 0,
        "total_wins": 0,
        "total_losses": 0,
        "win_rate": "0%",
        "total_staked": "$0.00",
        "total_profit": "$0.00",
        "roi": "0%",
        "current_bankroll": "$0.00",
        "peak_bankroll": "$0.00",
        "max_drawdown": "$0.00",
        "current_step": 0,
        "current_stake": f"${ORIGINAL_STAKE}",
        "is_paused": False,
        "consecutive_losses": 0,
        "current_streak": 0,
        "max_win_streak": 0,
        "max_loss_streak": 0,
    }

def reset_staking_engine() -> None:
    global _staking_engine
    if _staking_engine:
        _staking_engine.reset()
        logger.info("🔄 Staking engine reset.")
        send_telegram("🔄 Staking engine reset to initial state.")

def get_staking_status_message() -> str:
    if _staking_engine:
        return _staking_engine.get_status_message()
    return "⚠️ Staking engine not initialized."

def get_current_stake() -> float:
    if not _staking_enabled or not _staking_engine:
        return ORIGINAL_STAKE
    return _staking_engine.get_current_stake()

def record_bet_result(is_win: bool, match_info: Optional[Dict] = None) -> Optional[Dict]:
    if _staking_engine:
        return _staking_engine.record_result(is_win, match_info)
    return None

# =========================
# FIREBASE CONFIGURATION
# =========================

class FirebaseManager:
    def __init__(self, creds_json):
        self.creds_json = creds_json
        self.db = None
        self._connect()

    def _connect(self):
        if not self.creds_json:
            logger.error("❌ Firebase Credentials missing from environment variables!")
            return False
        try:
            cred_dict = json.loads(self.creds_json)
            cred = credentials.Certificate(cred_dict)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            logger.info("✅ Firebase Connection Successfully Established.")
            return True
        except Exception as e:
            logger.exception(f"❌ Firebase Initialization Error: {e}")
            self.db = None
            return False

    def _ensure_connection(self) -> bool:
        if self.db is not None:
            return True
        return self._connect()

    def is_state_locked(self) -> bool:
        if not self._ensure_connection():
            return True
        try:
            unresolved_docs = self.db.collection('unresolved_bets').limit(1).get()
            return len(unresolved_docs) > 0
        except Exception as e:
            logger.error(f"❌ Error checking Firebase state lock: {e}")
            return True

    def get_last_resolved_bet(self) -> dict | None:
        if not self._ensure_connection():
            return None
        try:
            query = self.db.collection('resolved_bets')\
                .order_by('resolution_timestamp', direction=firestore.Query.DESCENDING)\
                .limit(1).get()
            for doc in query:
                return doc.to_dict()
            return None
        except Exception as e:
            logger.exception(f"❌ Error pulling last resolved bet: {e}")
            return None

    def add_unresolved_bet(self, match_id: str, data: dict):
        placed_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        data['placed_at'] = placed_time
        if not self._ensure_connection():
            logger.critical(f"❌ Transmit Blocked: Database offline. Drop ID {match_id}!")
            return
        try:
            self.db.collection('unresolved_bets').document(str(match_id)).set(data)
            logger.info(f"✅ Document successfully written to 'unresolved_bets' for ID {match_id}")
        except Exception as e:
            logger.exception(f"❌ Critical: Failed to save unresolved bet for ID {match_id}: {e}")

    def get_unresolved_bet(self, match_id: str) -> dict | None:
        if not self._ensure_connection():
            return None
        try:
            doc = self.db.collection('unresolved_bets').document(str(match_id)).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logger.error(f"❌ Error downloading unresolved document {match_id}: {e}")
            return None

    def move_to_resolved(self, match_id: str, data: dict, outcome: str) -> bool:
        resolved_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        data.update({
            'outcome': outcome,
            'resolved_at': resolved_time,
            'resolution_timestamp': firestore.SERVER_TIMESTAMP
        })
        if not self._ensure_connection():
            return False
        try:
            self.db.collection('resolved_bets').document(str(match_id)).set(data)
            self.db.collection('unresolved_bets').document(str(match_id)).delete()
            return True
        except Exception as e:
            logger.exception(f"❌ Error during database migration lifecycle for Match ID {match_id}: {e}")
            return False

# =========================
# SYSTEM UTILITY AGENTS
# =========================

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'}, timeout=15)
    except Exception as e:
        logger.error(f"❌ Network error sending Telegram webhook event: {e}")

def calculate_stake() -> tuple[float, int]:
    global _staking_engine, _staking_enabled

    if _staking_enabled and _staking_engine:
        stake = _staking_engine.get_current_stake()
        if stake == 0:
            logger.info("⏸️ Staking engine paused. Using original stake.")
            return ORIGINAL_STAKE, 1
        step = _staking_engine.current_step + 1
        return float(stake), step

    last = firebase_manager.get_last_resolved_bet()
    if not last or last.get('outcome') == 'win':
        return ORIGINAL_STAKE, 1

    seq = last.get('match_sequence', 1)
    if seq < MAX_CHASE_LEVEL:
        return float(ORIGINAL_STAKE * (2**seq)), seq + 1

    logger.error(f"🚨 MAX CHASE TIER HIT ({MAX_CHASE_LEVEL}). Hard reset back to sequence base configurations.")
    return ORIGINAL_STAKE, 1

def prune_volatile_cache_leaks():
    current_time = time.time()
    stale_keys = [
        fid for fid, state in LOCAL_TRACKED_MATCHES.items()
        if current_time - state.get('last_seen', 0.0) > MEMORY_PRUNE_TIMEOUT
    ]
    for key in stale_keys:
        LOCAL_TRACKED_MATCHES.pop(key, None)
    if stale_keys:
        logger.info(f"🧹 Automated Memory Clean: Evicted {len(stale_keys)} stale match contexts from memory maps.")

# ==========================================
# HYBRID PARSING WRAPPER FOR GEOGRAPHY
# ==========================================

def extract_hybrid_geography(match) -> tuple[str, str, str]:
    if hasattr(match, 'tournament'):
        league = match.tournament.name
        country_name = match.tournament.category.name if match.tournament.category else "World"
        country_slug = getattr(match.tournament.category, 'slug', country_name.lower())
        return league, country_name, country_slug

    if isinstance(match, dict):
        tournament_name = "Unknown League"
        if "Stg" in match and isinstance(match["Stg"], dict):
            stage = match["Stg"]
            tournament_name = (
                stage.get("Snm") or
                stage.get("CompN") or
                stage.get("Nm") or
                "Unknown League"
            )
            if tournament_name == "Unknown League":
                tournament_name = match.get("Snm") or match.get("CompN") or "Unknown League"

        if tournament_name == "Unknown League":
            tournament_name = (
                match.get("Snm") or
                match.get("CompN") or
                match.get("league") or
                match.get("tournament") or
                "Unknown League"
            )

        country_name = "World"
        country_slug = "world"

        if "Stg" in match and isinstance(match["Stg"], dict):
            stage = match["Stg"]
            country_name = (
                stage.get("Cnm") or
                stage.get("Rgn") or
                stage.get("Country") or
                "World"
            )
            country_slug = country_name.lower()

        if country_name == "World":
            country_name = (
                match.get("Cnm") or
                match.get("country") or
                "World"
            )
            country_slug = country_name.lower()

        return tournament_name, country_name, country_slug

    return "Unknown League", "World", "world"

# =========================
# LIVESCORE CORNER EXTRACTION FUNCTIONS
# =========================

def extract_livescore_corners(match_data: dict) -> int:
    try:
        corners = 0

        if 'Corner' in match_data:
            return int(match_data.get('Corner', 0))

        if 'Stat' in match_data:
            stat_rows = match_data.get('Stat', [])
            for row in stat_rows:
                if isinstance(row, dict):
                    stat_type = row.get('Type', '')
                    if stat_type == 'Corners':
                        try:
                            home = float(row.get('Value1', 0))
                            away = float(row.get('Value2', 0))
                            corners = int(home + away)
                            return corners
                        except (ValueError, TypeError):
                            pass

        if 'SPrd' in match_data:
            for period in match_data.get('SPrd', []):
                if 'Stat' in period:
                    for row in period.get('Stat', []):
                        if isinstance(row, dict) and row.get('Type') == 'Corners':
                            try:
                                home = float(row.get('Value1', 0))
                                away = float(row.get('Value2', 0))
                                corners = int(home + away)
                                return corners
                            except (ValueError, TypeError):
                                pass

        if 'T1' in match_data:
            for team_data in [match_data.get('T1', []), match_data.get('T2', [])]:
                if team_data and isinstance(team_data, list) and len(team_data) > 0:
                    team = team_data[0] if isinstance(team_data[0], dict) else {}
                    if 'Corner' in team:
                        corners += int(team.get('Corner', 0))
                    elif 'Cnr' in team:
                        corners += int(team.get('Cnr', 0))

        if 'Agg' in match_data:
            agg = match_data.get('Agg', {})
            if isinstance(agg, dict):
                corners = int(agg.get('Corner', 0))

        return corners

    except Exception as e:
        logger.debug(f"Error extracting LiveScore corners: {e}")
        return 0

def extract_livescore_corners_from_match(match, service) -> int:
    try:
        match_id = None
        if hasattr(match, 'id'):
            match_id = str(match.id)
        elif isinstance(match, dict):
            match_id = str(match.get('Eid') or match.get('id', ''))

        if not match_id:
            return 0

        if isinstance(match, dict):
            corners = extract_livescore_corners(match)
            if corners > 0:
                return corners

        if service and hasattr(service, 'get_raw_statistics'):
            stats_data = service.get_raw_statistics(match_id)
            if stats_data and isinstance(stats_data, dict):
                corners = extract_livescore_corners(stats_data)
                if corners > 0:
                    return corners

        return 0
    except Exception as e:
        logger.debug(f"Failed to get LiveScore corners for match: {e}")
        return 0

# =========================
# xG AND STATS EXTRACTION FUNCTIONS
# =========================

def extract_livescore_xg_and_stats(match_data: dict) -> Dict[str, Any]:
    """
    Extract xG (expected goals) and other statistics from LiveScore match data.
    Returns a dict with home_xg, away_xg, total_xg, shots_on_target, etc.
    """
    stats = {
        'home_xg': 0.0,
        'away_xg': 0.0,
        'total_xg': 0.0,
        'home_shots_on_target': 0,
        'away_shots_on_target': 0,
        'home_total_shots': 0,
        'away_total_shots': 0,
        'home_corners': 0,
        'away_corners': 0,
        'home_fouls': 0,
        'away_fouls': 0,
        'has_xg_data': False
    }

    try:
        # Try to extract xG from statistics
        if 'Stat' in match_data:
            stat_rows = match_data.get('Stat', [])
            for row in stat_rows:
                if isinstance(row, dict):
                    stat_type = row.get('Type', '')
                    try:
                        home_val = float(row.get('Value1', 0))
                        away_val = float(row.get('Value2', 0))
                    except (ValueError, TypeError):
                        home_val, away_val = 0, 0

                    # xG is often stored as 'ExpectedGoals' or 'xG'
                    if stat_type in ['ExpectedGoals', 'xG', 'Xg']:
                        stats['home_xg'] = home_val
                        stats['away_xg'] = away_val
                        stats['total_xg'] = home_val + away_val
                        stats['has_xg_data'] = True

                    elif stat_type == 'ShotsOnTarget':
                        stats['home_shots_on_target'] = int(home_val)
                        stats['away_shots_on_target'] = int(away_val)

                    elif stat_type == 'TotalShots' or stat_type == 'Shots':
                        stats['home_total_shots'] = int(home_val)
                        stats['away_total_shots'] = int(away_val)

                    elif stat_type == 'Corners':
                        stats['home_corners'] = int(home_val)
                        stats['away_corners'] = int(away_val)

                    elif stat_type == 'Fouls':
                        stats['home_fouls'] = int(home_val)
                        stats['away_fouls'] = int(away_val)

        # If no xG in Stat, try SPrd (period statistics)
        if not stats['has_xg_data'] and 'SPrd' in match_data:
            for period in match_data.get('SPrd', []):
                if 'Stat' in period:
                    for row in period.get('Stat', []):
                        if isinstance(row, dict):
                            stat_type = row.get('Type', '')
                            if stat_type in ['ExpectedGoals', 'xG', 'Xg']:
                                try:
                                    home_val = float(row.get('Value1', 0))
                                    away_val = float(row.get('Value2', 0))
                                    stats['home_xg'] = home_val
                                    stats['away_xg'] = away_val
                                    stats['total_xg'] = home_val + away_val
                                    stats['has_xg_data'] = True
                                    break
                                except (ValueError, TypeError):
                                    pass
                    if stats['has_xg_data']:
                        break

        # Try to get xG from direct fields (sometimes Livescore has it directly)
        if not stats['has_xg_data']:
            if 'xG' in match_data:
                try:
                    xg_data = match_data.get('xG', {})
                    if isinstance(xg_data, dict):
                        stats['home_xg'] = float(xg_data.get('home', 0))
                        stats['away_xg'] = float(xg_data.get('away', 0))
                        stats['total_xg'] = stats['home_xg'] + stats['away_xg']
                        stats['has_xg_data'] = True
                except (ValueError, TypeError):
                    pass

        return stats

    except Exception as e:
        logger.debug(f"Error extracting xG from LiveScore: {e}")
        return stats

def get_match_xg_and_stats(match, service) -> Dict[str, Any]:
    """
    Get xG and other statistics for a match using the LiveScore service.
    """
    try:
        match_id = None
        if hasattr(match, 'id'):
            match_id = str(match.id)
        elif isinstance(match, dict):
            match_id = str(match.get('Eid') or match.get('id', ''))

        if not match_id:
            return {'has_xg_data': False}

        # First check if stats are already in the match data
        if isinstance(match, dict):
            stats = extract_livescore_xg_and_stats(match)
            if stats['has_xg_data']:
                return stats

        # If not, fetch statistics from LiveScore service
        if service and hasattr(service, 'get_raw_statistics'):
            stats_data = service.get_raw_statistics(match_id)
            if stats_data and isinstance(stats_data, dict):
                stats = extract_livescore_xg_and_stats(stats_data)
                return stats

        return {'has_xg_data': False}
    except Exception as e:
        logger.debug(f"Failed to get xG for match: {e}")
        return {'has_xg_data': False}

# =========================
# FILTER VALIDATION FUNCTIONS
# =========================

def check_xg_filters(stats: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Check if the match passes all xG filters.
    Returns (passed, reason_message)
    """
    if not stats.get('has_xg_data', False):
        return True, "No xG data available, skipping filter"

    home_xg = stats.get('home_xg', 0)
    away_xg = stats.get('away_xg', 0)
    total_xg = stats.get('total_xg', 0)

    # Check home xG
    if home_xg > XG_THRESHOLDS['max_home_xg']:
        return False, f"Home xG too high: {home_xg:.2f} > {XG_THRESHOLDS['max_home_xg']}"

    # Check away xG
    if away_xg > XG_THRESHOLDS['max_away_xg']:
        return False, f"Away xG too high: {away_xg:.2f} > {XG_THRESHOLDS['max_away_xg']}"

    # Check total xG
    if total_xg > XG_THRESHOLDS['max_total_xg']:
        return False, f"Total xG too high: {total_xg:.2f} > {XG_THRESHOLDS['max_total_xg']}"

    if total_xg < XG_THRESHOLDS['min_total_xg']:
        return False, f"Total xG too low: {total_xg:.2f} < {XG_THRESHOLDS['min_total_xg']}"

    # Check xG difference (avoid one-sided matches)
    xg_diff = abs(home_xg - away_xg)
    if xg_diff > XG_DIFF_THRESHOLDS['max_xg_diff']:
        return False, f"xG difference too high: {xg_diff:.2f} > {XG_DIFF_THRESHOLDS['max_xg_diff']}"

    return True, f"xG: H={home_xg:.2f}, A={away_xg:.2f}, Total={total_xg:.2f}"

def check_advanced_filters(stats: Dict[str, Any], current_corners: int) -> Tuple[bool, str]:
    """
    Check advanced filters like shots on target.
    Returns (passed, reason_message)
    """
    home_shots_on_target = stats.get('home_shots_on_target', 0)
    away_shots_on_target = stats.get('away_shots_on_target', 0)
    home_total_shots = stats.get('home_total_shots', 0)
    away_total_shots = stats.get('away_total_shots', 0)

    # Check shots on target
    if home_shots_on_target < ADVANCED_FILTERS['min_shots_on_target']:
        return False, f"Home shots on target too low: {home_shots_on_target} < {ADVANCED_FILTERS['min_shots_on_target']}"

    if away_shots_on_target < ADVANCED_FILTERS['min_shots_on_target']:
        return False, f"Away shots on target too low: {away_shots_on_target} < {ADVANCED_FILTERS['min_shots_on_target']}"

    # Check total shots
    total_shots = home_total_shots + away_total_shots
    if total_shots < ADVANCED_FILTERS['min_total_shots']:
        return False, f"Total shots too low: {total_shots} < {ADVANCED_FILTERS['min_total_shots']}"

    # Check corners
    if current_corners < ADVANCED_FILTERS['min_corners_for_bet']:
        return False, f"Too few corners: {current_corners} < {ADVANCED_FILTERS['min_corners_for_bet']}"

    if current_corners > ADVANCED_FILTERS['max_corners_for_bet']:
        return False, f"Too many corners: {current_corners} > {ADVANCED_FILTERS['max_corners_for_bet']}"

    return True, "All advanced filters passed"

# =========================
# CORE EVALUATION PIPELINE
# =========================

def process_match(match):
    global SOFASCORE_CLIENT

    fid = str(match.id) if hasattr(match, 'id') else str(match.get('match_id') or match.get('id') or match.get('Eid', ''))

    if hasattr(match, 'home_team'):
        match_name = f"{match.home_team.name} vs {match.away_team.name}"
    else:
        match_name = match.get('match_name') or f"{match.get('home_name', 'Home')} vs {match.get('away_name', 'Away')}"

    league, country, country_slug = extract_hybrid_geography(match)
    full_info = f"{league} {country}"

    if any(keyword.lower() in full_info.lower() for keyword in AMATEUR_KEYWORDS):
        logger.debug(f"⏭️ Skipping amateur match: {match_name} ({full_info})")
        return

    if hasattr(match, 'status'):
        status = match.status.description.upper()
        score = f"{match.home_score.current}-{match.away_score.current}"
    else:
        status = str(match.get('status_string') or match.get('status') or match.get('Eps', '')).upper()
        score = f"{match.get('home_score', 0)}-{match.get('away_score', 0)}"

    live_pitch_minute = None
    is_first_half_phase = False

    if status.isdigit():
        live_pitch_minute = int(status)
        if live_pitch_minute <= 45:
            is_first_half_phase = True
    elif status in ['HT', 'HALFTIME', 'HALF']:
        live_pitch_minute = 45
        is_first_half_phase = True
    elif '1ST' in status:
        is_first_half_phase = True
        live_pitch_minute = getattr(match, 'total_elapsed_minutes', match.get('total_elapsed_minutes', 25))

    if live_pitch_minute is None or live_pitch_minute < PREDICT_START_MIN:
        return

    logger.info(f"🔍 Match verification: {match_name} | Real Min: {live_pitch_minute}' | Score: {score} | League: {league} | Country: {country}")

    state = LOCAL_TRACKED_MATCHES.get(fid, {
        'bet_placed': False,
        'last_seen': time.time(),
        'active': False
    })
    state['last_seen'] = time.time()

    if PRE_WARM_WINDOW[0] <= live_pitch_minute <= PRE_WARM_WINDOW[1]:
        state['active'] = True

    LOCAL_TRACKED_MATCHES[fid] = state

    # --- PHASE 1: EVALUATE PLACEMENT - Check for 0-0 at 25 minutes ---
    if is_first_half_phase and (live_pitch_minute in MINUTES_REGULAR_BET) and not state['bet_placed']:
        if score == '0-0':
            # Get current corner count and xG stats
            current_corners = 0
            xg_stats = {}

            try:
                if isinstance(match, dict):
                    current_corners = extract_livescore_corners(match)
                    xg_stats = extract_livescore_xg_and_stats(match)

                if current_corners == 0 and SOFASCORE_CLIENT and SOFASCORE_CLIENT.service:
                    current_corners = extract_livescore_corners_from_match(match, SOFASCORE_CLIENT.service)

                if not xg_stats.get('has_xg_data', False) and SOFASCORE_CLIENT and SOFASCORE_CLIENT.service:
                    xg_stats = get_match_xg_and_stats(match, SOFASCORE_CLIENT.service)
            except Exception as e:
                logger.warning(f"Could not extract stats for {match_name}: {e}")
                current_corners = 0
                xg_stats = {'has_xg_data': False}

            # --- Apply xG Filters ---
            xg_passed, xg_reason = check_xg_filters(xg_stats)
            if not xg_passed:
                logger.info(f"⏭️ {match_name} - xG filter failed: {xg_reason}")
                state['bet_placed'] = True
                LOCAL_TRACKED_MATCHES[fid] = state
                return

            # --- Apply Advanced Filters ---
            advanced_passed, advanced_reason = check_advanced_filters(xg_stats, current_corners)
            if not advanced_passed:
                logger.info(f"⏭️ {match_name} - Advanced filter failed: {advanced_reason}")
                state['bet_placed'] = True
                LOCAL_TRACKED_MATCHES[fid] = state
                return

            if firebase_manager.is_state_locked():
                STATE_LOCKS.inc()
                logger.warning(f"🚫 Qualification blocked for '{match_name}'. Active DB lock present.")
            else:
                logger.info(f"⚡ QUALIFIED: 0-0 at {live_pitch_minute}' with {current_corners} corners - Firing parlay placement routine for {match_name}")
                stake, seq = calculate_stake()

                flag_emoji = COUNTRY_FLAGS.get(country_slug.lower(), COUNTRY_FLAGS.get(country.lower(), "🌍"))

                step_display = ""
                if _staking_engine:
                    step_display = f" | Step {_staking_engine.current_step + 1}/{len(STAKE_SEQUENCE)}"

                # Build filter info string for storage and display
                home_xg = xg_stats.get('home_xg', 0)
                away_xg = xg_stats.get('away_xg', 0)
                total_xg = xg_stats.get('total_xg', 0)
                shots_on_target = f"{xg_stats.get('home_shots_on_target', 0)}-{xg_stats.get('away_shots_on_target', 0)}"

                data = {
                    'match_name': match_name,
                    'league': league,
                    'country': country,
                    'country_slug': country_slug,
                    'trigger_minute': live_pitch_minute,
                    'trigger_score': score,
                    'current_corners': current_corners,
                    'home_xg': home_xg,
                    'away_xg': away_xg,
                    'total_xg': total_xg,
                    'shots_on_target': shots_on_target,
                    'stake': stake,
                    'match_sequence': seq,
                    'bet_type': 'parlay_over0.5_corners',
                    'data_source': 'livescore',
                    'xg_filter_passed': True,
                    'staking_step': _staking_engine.current_step + 1 if _staking_engine else 0,
                    'staking_sequence': STAKE_SEQUENCE if _staking_engine else [],
                }
                firebase_manager.add_unresolved_bet(fid, data)
                BET_TRIGGERS.inc()

                # Build filter info for Telegram message
                filter_info = f"xG: H={home_xg:.2f}, A={away_xg:.2f} | Shots OT: {shots_on_target} | Corners: {current_corners}"

                send_telegram(
                    f"🎯 **PARLAY BET PLACED - OVER 0.5 GOALS + OVER {current_corners} CORNERS**\n"
                    f"⏱ Min: {live_pitch_minute}' | {match_name}\n"
                    f"{flag_emoji} {country} | 🏆 {league}\n"
                    f"🔢 Score: {score} (Betting on: ANY goal + at least 1 more corner)\n"
                    f"🔄 Current Corners: {current_corners} (Need +1 corner)\n"
                    f"📊 {filter_info}\n"
                    f"💰 Stake: ${stake:.2f}{step_display}"
                )
        else:
            logger.debug(f"⏭️ Match {match_name} at {live_pitch_minute}' has score {score}, not 0-0. Skipping.")

        state['bet_placed'] = True
        LOCAL_TRACKED_MATCHES[fid] = state

    # --- PHASE 2: HALFTIME RESOLUTION ---
    elif status in ['HT', 'HALFTIME', 'HALF']:
        unresolved = firebase_manager.get_unresolved_bet(fid)
        if unresolved:
            trigger_score = unresolved.get('trigger_score', '0-0')
            trigger_corners = unresolved.get('current_corners', 0)
            trigger_home_xg = unresolved.get('home_xg', 0)
            trigger_away_xg = unresolved.get('away_xg', 0)

            final_corners = 0
            try:
                if isinstance(match, dict):
                    final_corners = extract_livescore_corners(match)

                if final_corners == 0 and SOFASCORE_CLIENT and SOFASCORE_CLIENT.service:
                    final_corners = extract_livescore_corners_from_match(match, SOFASCORE_CLIENT.service)
            except Exception as e:
                logger.warning(f"Could not extract final corners for {match_name}: {e}")
                final_corners = trigger_corners

            goal_scored = (score != '0-0')
            corner_increased = (final_corners > trigger_corners)
            outcome = 'win' if (goal_scored and corner_increased) else 'loss'

            db_league = unresolved.get('league', league)
            db_country = unresolved.get('country', country)
            db_slug = unresolved.get('country_slug', country_slug)
            flag_emoji = COUNTRY_FLAGS.get(db_slug.lower(), COUNTRY_FLAGS.get(db_country.lower(), "🌍"))

            stake = unresolved.get('stake', 0)
            trigger_minute = unresolved.get('trigger_minute', 25)

            if _staking_engine:
                is_win = (outcome == 'win')
                match_info = {
                    'match_name': match_name,
                    'league': db_league,
                    'country': db_country,
                    'score': score,
                    'trigger_score': trigger_score,
                    'trigger_minute': trigger_minute,
                    'trigger_corners': trigger_corners,
                    'final_corners': final_corners,
                    'trigger_home_xg': trigger_home_xg,
                    'trigger_away_xg': trigger_away_xg,
                    'stake': stake,
                    'bet_type': 'parlay_over0.5_corners',
                }
                result = _staking_engine.record_result(is_win, match_info)

                step_display = _staking_engine.get_current_step_display()
                bankroll_display = f" | 💰 Bankroll: ${_staking_engine.current_bankroll:.2f}"
                profit_display = f" | 📈 Profit: ${_staking_engine.total_profit:.2f}"
            else:
                step_display = ""
                bankroll_display = ""
                profit_display = ""

            firebase_manager.move_to_resolved(fid, unresolved, outcome)

            goal_status = "✅ Goal scored" if goal_scored else "❌ No goal"
            corner_status = f"✅ Corners increased ({trigger_corners} → {final_corners})" if corner_increased else f"❌ No corner increase ({trigger_corners} → {final_corners})"

            # Add xG info to settlement message if available
            xg_info = ""
            if trigger_home_xg > 0 or trigger_away_xg > 0:
                xg_info = f"\n📊 xG at bet: H={trigger_home_xg:.2f}, A={trigger_away_xg:.2f}"

            send_telegram(
                f"{'✅ WIN' if outcome == 'win' else '❌ LOSS'} **HT Settlement - Parlay (Over 0.5 + Corners)**\n"
                f"⏱ HT (45') | {match_name}\n"
                f"{flag_emoji} {db_country} | 🏆 {db_league}\n"
                f"🔢 Final HT Score: {score} (Started 0-0 at {trigger_minute}')\n"
                f"🔄 Corners: {trigger_corners} → {final_corners} (Need +1){xg_info}\n"
                f"💰 Stake: ${stake:.2f}{step_display}{bankroll_display}{profit_display}\n"
                f"📊 {goal_status} | {corner_status}"
            )
            LOCAL_TRACKED_MATCHES.pop(fid, None)

# =========================
# DRIVER LAYER INTERFACES
# =========================

def initialize_bot_services() -> bool:
    global firebase_manager, SOFASCORE_CLIENT
    firebase_manager = FirebaseManager(FIREBASE_CREDENTIALS)
    try:
        SOFASCORE_CLIENT = SofascoreClient()
        SOFASCORE_CLIENT.initialize()

        if _staking_engine:
            logger.info(f"📊 Staking Engine active: {_staking_engine.get_current_step_display()}")
            logger.info(f"📊 Sequence: {STAKE_SEQUENCE}")
        else:
            logger.info("📊 Staking Engine not attached. Using fixed stake.")

        # Log filter settings
        logger.info(f"📊 xG Filters: Max Home={XG_THRESHOLDS['max_home_xg']}, Max Away={XG_THRESHOLDS['max_away_xg']}, "
                   f"Max Total={XG_THRESHOLDS['max_total_xg']}, Min Total={XG_THRESHOLDS['min_total_xg']}")
        logger.info(f"📊 Advanced Filters: Min SOT={ADVANCED_FILTERS['min_shots_on_target']}, "
                   f"Min Total Shots={ADVANCED_FILTERS['min_total_shots']}, "
                   f"Corners Range={ADVANCED_FILTERS['min_corners_for_bet']}-{ADVANCED_FILTERS['max_corners_for_bet']}")

        return True
    except Exception as e:
        logger.exception(f"❌ Failed to instantiate data engine driver context: {e}")
        API_FAILURES.inc()
        return False

def shutdown_bot():
    global SOFASCORE_CLIENT
    if SOFASCORE_CLIENT:
        try:
            SOFASCORE_CLIENT.close()
        except Exception as e:
            logger.error(f"Error shutting down client: {e}")

def run_bot_cycle():
    if not SOFASCORE_CLIENT:
        return
    try:
        events = SOFASCORE_CLIENT.get_events(live=True)
        if not events:
            logger.debug("No live events found in current cycle.")
            return
        for m in events:
            try:
                process_match(m)
            except Exception as inner_ex:
                logger.error(f"Error checking single event match node: {inner_ex}")
        prune_volatile_cache_leaks()
    except Exception as e:
        logger.error(f"Ingestion lifecycle exception: {e}")

# =========================
# MAIN ENTRY POINT
# =========================

def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    sys.exit(0)

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('betbot.log')
        ]
    )

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║     🎯 BETBOT - Live Match Betting Engine                    ║
    ║     Strategy: Parlay - Over 0.5 Goals + Over Corners         ║
    ║     Check: 25 minutes | Score: 0-0                          ║
    ║     Filters: xG, Shots on Target, Corners                   ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """)

    initial_bankroll = float(os.getenv('INITIAL_BANKROLL', '1000.0'))
    staking_engine = StakingEngine(initial_bankroll=initial_bankroll)
    set_staking_engine(staking_engine)

    if not initialize_bot_services():
        logger.error("❌ Failed to initialize bot services. Exiting.")
        sys.exit(1)

    logger.info("✅ Bot services initialized successfully.")

    # Send startup message with filter settings
    send_telegram(
        "🤖 **BetBot Started**\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "📊 Strategy: Parlay (Over 0.5 Goals + Over Corners)\n"
        "⏱ Check: 25 minutes | Score: 0-0\n"
        f"💰 Bankroll: ${initial_bankroll:.2f}\n"
        "📊 Filters:\n"
        f"  • xG: H≤{XG_THRESHOLDS['max_home_xg']}, A≤{XG_THRESHOLDS['max_away_xg']}, Total {XG_THRESHOLDS['min_total_xg']}-{XG_THRESHOLDS['max_total_xg']}\n"
        f"  • Shots OT: ≥{ADVANCED_FILTERS['min_shots_on_target']} each\n"
        f"  • Corners: {ADVANCED_FILTERS['min_corners_for_bet']}-{ADVANCED_FILTERS['max_corners_for_bet']}"
    )

    cycle_count = 0
    sleep_interval = int(os.getenv('BOT_SLEEP_INTERVAL', '60'))

    try:
        while True:
            cycle_count += 1
            logger.info(f"🔄 Running cycle #{cycle_count} at {datetime.now().strftime('%H:%M:%S')}")
            run_bot_cycle()

            if cycle_count % 10 == 0:
                stats = get_staking_stats()
                logger.info(f"📊 Staking Stats: Bankroll=${stats['current_bankroll']}, "
                          f"Profit=${stats['total_profit']}, "
                          f"Win Rate={stats['win_rate']}")

            time.sleep(sleep_interval)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
    finally:
        shutdown_bot()
        send_telegram(f"🛑 BetBot stopped after {cycle_count} cycles.")
        logger.info("👋 BetBot stopped successfully.")

if __name__ == "__main__":
    main()
