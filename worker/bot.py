#!/usr/bin/env python3
"""
BetBot - Live Match Betting Engine
Strategy: Over 0.5 Goals at 25 minutes (0-0)
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
# LOGGING CONFIGURATION
# ============================================================
goal_logger = logging.getLogger("BetBot.Goals")
stats_logger = logging.getLogger("BetBot.Stats")

def setup_data_loggers():
    """Setup separate log files for different data types."""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    goal_handler = logging.FileHandler(f"{log_dir}/goals.log")
    goal_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    goal_logger.addHandler(goal_handler)
    goal_logger.setLevel(logging.INFO)

    stats_handler = logging.FileHandler(f"{log_dir}/match_stats.log")
    stats_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    stats_logger.addHandler(stats_handler)
    stats_logger.setLevel(logging.INFO)

# ============================================================
# STAKING ENGINE CONFIGURATION
# ============================================================
ORIGINAL_STAKE = 10.0
MAX_CHASE_LEVEL = 3

# Timing parameters - Check at 25 minutes for 0-0 matches
MINUTES_REGULAR_BET = [25]
SLEEP_TIME = 55

# ============================================================
# FILTER CONFIGURATION
# ============================================================
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
# LOGGING HELPER FUNCTIONS
# =========================

def log_goal_data(match_name: str, score: str, minute: int, league: str, country: str):
    goal_logger.info(f"GOAL | {match_name} | {score} | {minute}' | {league} | {country}")

def log_match_stats(match_name: str, minute: int, league: str, country: str):
    stats_logger.info(f"STATS | {match_name} | {minute}' | {league} | {country}")

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
            # --- LOG STATS ---
            log_match_stats(match_name, live_pitch_minute, league, country)

            if firebase_manager.is_state_locked():
                STATE_LOCKS.inc()
                logger.warning(f"🚫 Qualification blocked for '{match_name}'. Active DB lock present.")
            else:
                logger.info(f"⚡ QUALIFIED: 0-0 at {live_pitch_minute}' - Firing bet placement routine for {match_name}")
                stake, seq = calculate_stake()

                flag_emoji = COUNTRY_FLAGS.get(country_slug.lower(), COUNTRY_FLAGS.get(country.lower(), "🌍"))

                step_display = ""
                if _staking_engine:
                    step_display = f" | Step {_staking_engine.current_step + 1}/{len(STAKE_SEQUENCE)}"

                data = {
                    'match_name': match_name,
                    'league': league,
                    'country': country,
                    'country_slug': country_slug,
                    'trigger_minute': live_pitch_minute,
                    'trigger_score': score,
                    'stake': stake,
                    'match_sequence': seq,
                    'bet_type': 'over0.5_goals',
                    'data_source': 'livescore',
                    'staking_step': _staking_engine.current_step + 1 if _staking_engine else 0,
                    'staking_sequence': STAKE_SEQUENCE if _staking_engine else [],
                }
                firebase_manager.add_unresolved_bet(fid, data)
                BET_TRIGGERS.inc()

                send_telegram(
                    f"🎯 **BET PLACED - OVER 0.5 GOALS**\n"
                    f"⏱ Min: {live_pitch_minute}' | {match_name}\n"
                    f"{flag_emoji} {country} | 🏆 {league}\n"
                    f"🔢 Score: {score} (Betting on: ANY goal)\n"
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

            goal_scored = (score != '0-0')
            outcome = 'win' if goal_scored else 'loss'

            if goal_scored:
                log_goal_data(match_name, score, 45, league, country)
            else:
                stats_logger.info(f"NO_GOAL | {match_name} | {score} | 45' | {league} | {country}")

            stats_logger.info(
                f"BET_OUTCOME | {match_name} | {outcome.upper()} | "
                f"Score: {score} | Goal: {'YES' if goal_scored else 'NO'}"
            )

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
                    'stake': stake,
                    'bet_type': 'over0.5_goals',
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

            send_telegram(
                f"{'✅ WIN' if outcome == 'win' else '❌ LOSS'} **HT Settlement - Over 0.5 Goals**\n"
                f"⏱ HT (45') | {match_name}\n"
                f"{flag_emoji} {db_country} | 🏆 {db_league}\n"
                f"🔢 Final HT Score: {score} (Started 0-0 at {trigger_minute}')\n"
                f"💰 Stake: ${stake:.2f}{step_display}{bankroll_display}{profit_display}\n"
                f"📊 {goal_status}"
            )
            LOCAL_TRACKED_MATCHES.pop(fid, None)

# =========================
# DRIVER LAYER INTERFACES
# =========================

def initialize_bot_services() -> bool:
    global firebase_manager, SOFASCORE_CLIENT

    setup_data_loggers()

    firebase_manager = FirebaseManager(FIREBASE_CREDENTIALS)
    try:
        SOFASCORE_CLIENT = SofascoreClient()
        SOFASCORE_CLIENT.initialize()

        if _staking_engine:
            logger.info(f"📊 Staking Engine active: {_staking_engine.get_current_step_display()}")
            logger.info(f"📊 Sequence: {STAKE_SEQUENCE}")
        else:
            logger.info("📊 Staking Engine not attached. Using fixed stake.")

        logger.info("📝 Data logging enabled: goals.log, match_stats.log")

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
    ╔═══════════════════════════════════════════════════════════════════╗
    ║                                                                   ║
    ║     🎯 BETBOT - Live Match Betting Engine                        ║
    ║     Strategy: Over 0.5 Goals                                     ║
    ║     Check: 25 minutes | Score: 0-0                              ║
    ║     Data Logging: goals.log, match_stats.log                    ║
    ║                                                                   ║
    ╚═══════════════════════════════════════════════════════════════════╝
    """)

    initial_bankroll = float(os.getenv('INITIAL_BANKROLL', '1000.0'))
    staking_engine = StakingEngine(initial_bankroll=initial_bankroll)
    set_staking_engine(staking_engine)

    if not initialize_bot_services():
        logger.error("❌ Failed to initialize bot services. Exiting.")
        sys.exit(1)

    logger.info("✅ Bot services initialized successfully.")

    send_telegram(
        "🤖 **BetBot Started**\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "📊 Strategy: Over 0.5 Goals\n"
        "⏱ Check: 25 minutes | Score: 0-0\n"
        f"💰 Bankroll: ${initial_bankroll:.2f}"
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