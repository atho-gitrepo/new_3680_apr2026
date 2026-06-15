#worker/bot.py
"""
Core business strategy processing engine.
Evaluates live match metrics against staking parameters and logs execution telemetry.
"""

import requests
import os
import json
import time
import logging
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore
from esd.sofascore import SofascoreClient

# Import shared metrics registry to prevent circular imports
from metrics import STATE_LOCKS, BET_TRIGGERS, API_FAILURES

logger = logging.getLogger("BetBot.ExecutionEngine")

# --- PARAMETERS & ENV EXTRACTION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

ORIGINAL_STAKE = 10.0
MAX_CHASE_LEVEL = 4
MINUTES_REGULAR_BET = [35, 36, 37]
SLEEP_TIME = 55  #Default fallback sleep time between monitoring cycles

AMATEUR_KEYWORDS = ['amateur', 'youth', 'reserves', 'friendly', 'u18', 'u17', 'u16', 'u19', 'u22', 'u23', 'u21', 'u20', 'women', 'college']

PREDICT_START_MIN = 30     
PRE_WARM_WINDOW = (34, 38) 
MEMORY_PRUNE_TIMEOUT = 5400 

# --- VOLATILE MEMORY CACHE MAP ---
LOCAL_TRACKED_MATCHES = {}

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

# =========================
# CORE EVALUATION PIPELINE
# =========================
def process_match(match):
    fid = str(match.id)
    league = match.tournament.name
    country = match.tournament.category.name
    full_info = f"{league} {country}".lower()
    match_name = f"{match.home_team.name} vs {match.away_team.name}"

    if any(keyword.lower() in full_info for keyword in AMATEUR_KEYWORDS):
        return

    status = match.status.description.upper()
    score = f"{match.home_score.current}-{match.away_score.current}"

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
        live_pitch_minute = match.total_elapsed_minutes

    if live_pitch_minute is None or live_pitch_minute < PREDICT_START_MIN:
        return

    logger.info(f"🔍 Match verification: {match_name} | Real Min: {live_pitch_minute}' | Score: {score}")

    state = LOCAL_TRACKED_MATCHES.get(fid, {
        'bet_placed': False,
        'last_seen': time.time(),
        'active': False
    })
    state['last_seen'] = time.time()

    if PRE_WARM_WINDOW[0] <= live_pitch_minute <= PRE_WARM_WINDOW[1]:
        state['active'] = True

    LOCAL_TRACKED_MATCHES[fid] = state

    # --- PHASE 1: EVALUATE PLACEMENT ---
    if is_first_half_phase and (live_pitch_minute in MINUTES_REGULAR_BET) and not state['bet_placed']:
        if firebase_manager.is_state_locked():
            STATE_LOCKS.inc()  # 📊 Telemetry: Lock active. Increment rejection counters.
            logger.warning(f"🚫 Qualification blocked for '{match_name}'. Active DB lock present.")
        else:
            if score in ['1-1', '2-2', '2-1', '2-0']:
                logger.warning(f"⚡ QUALIFIED: Firing placement routine for {match_name} at score {score}")
                stake, seq = calculate_stake()
                data = {
                    'match_name': match_name,
                    'league': league,
                    '36_score': score,
                    'stake': stake,
                    'match_sequence': seq,
                    'bet_type': 'regular'
                }
                firebase_manager.add_unresolved_bet(fid, data)
                BET_TRIGGERS.inc()  # 📊 Telemetry: Log verified bet emission
                send_telegram(f"🎯 **BET PLACED (Match {seq})**\n⏱ Min: {live_pitch_minute}' | {match_name}\n🌍 {full_info} \n🔢 Score: {score}\n💰 Stake: ${stake:.2f}")
        
        state['bet_placed'] = True
        LOCAL_TRACKED_MATCHES[fid] = state

    # --- PHASE 2: HALFTIME RESOLUTION ---
    elif status in ['HT', 'HALFTIME']:
        unresolved = firebase_manager.get_unresolved_bet(fid)
        if unresolved:
            trigger_score = unresolved.get('36_score')
            outcome = 'win' if score == trigger_score else 'loss'
            firebase_manager.move_to_resolved(fid, unresolved, outcome)
            send_telegram(f"{'✅ WIN' if outcome == 'win' else '❌ LOSS'} HT\n{match_name}\nScore: {score}")
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
            return
        for m in events:
            try:
                process_match(m)
            except Exception as inner_ex:
                logger.error(f"Error checking single event match node: {inner_ex}")
        prune_volatile_cache_leaks()
    except Exception as e:
        logger.error(f"Ingestion lifecycle exception: {e}")
        API_FAILURES.inc()  # 📊 Telemetry: Track network extraction error rates