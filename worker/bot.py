import requests
import os
import json
import time
import logging
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from esd.sofascore import SofascoreClient

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
)

logger = logging.getLogger("BetBot")

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

# --- SETTINGS ---
ORIGINAL_STAKE = 10.0
MAX_CHASE_LEVEL = 4
SLEEP_TIME = 120
CACHE_TTL = 120

MINUTES_REGULAR_BET = [36, 37]

# --- GLOBALS ---
SOFASCORE_CLIENT = None
firebase_manager = None
LOCAL_TRACKED_MATCHES = {}

LAST_FETCH_TIME = 0
CACHED_EVENTS = []

# --------------------------------------------------
# FIREBASE
# --------------------------------------------------

class FirebaseManager:
    def __init__(self, creds_json):
        self.db = None

        if not creds_json:
            logger.error("Firebase credentials missing")
            return

        try:
            cred = credentials.Certificate(json.loads(creds_json))

            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)

            self.db = firestore.client()
            logger.info("✅ Firebase Connected")

        except Exception as e:
            logger.error(f"Firebase init error: {e}")

    def is_state_locked(self):
        return len(self.db.collection('unresolved_bets').limit(1).get()) > 0

    def get_last_resolved_bet(self):
        try:
            docs = self.db.collection('resolved_bets')\
                .order_by('resolution_timestamp', direction=firestore.Query.DESCENDING)\
                .limit(1).get()

            for d in docs:
                return d.to_dict()
        except:
            return None

    def add_unresolved_bet(self, match_id, data):
        data['placed_at'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        self.db.collection('unresolved_bets').document(str(match_id)).set(data)

    def get_unresolved_bet(self, match_id):
        doc = self.db.collection('unresolved_bets').document(str(match_id)).get()
        return doc.to_dict() if doc.exists else None

    def move_to_resolved(self, match_id, data, outcome):
        data.update({
            "outcome": outcome,
            "resolved_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            "resolution_timestamp": firestore.SERVER_TIMESTAMP
        })

        self.db.collection('resolved_bets').document(str(match_id)).set(data)
        self.db.collection('unresolved_bets').document(str(match_id)).delete()

# --------------------------------------------------
# TELEGRAM
# --------------------------------------------------

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "Markdown"
            },
            timeout=10
        )
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# --------------------------------------------------
# STAKE LOGIC
# --------------------------------------------------

def calculate_stake():
    last = firebase_manager.get_last_resolved_bet()

    if not last or last.get("outcome") == "win":
        return ORIGINAL_STAKE, 1

    seq = last.get("match_sequence", 1)

    if seq < MAX_CHASE_LEVEL:
        return ORIGINAL_STAKE * (2 ** seq), seq + 1

    return ORIGINAL_STAKE, 1

# --------------------------------------------------
# MATCH PROCESS
# --------------------------------------------------

def process_match(match):
    try:
        fid = str(match.id)
        league = match.tournament.name

        minute = match.total_elapsed_minutes
        status = match.status.description.upper()
        score = f"{match.home_score.current}-{match.away_score.current}"

        # Initialize state if not exists
        state = LOCAL_TRACKED_MATCHES.get(fid, {"bet": False})
        LOCAL_TRACKED_MATCHES[fid] = state

        match_name = f"{match.home_team.name} vs {match.away_team.name}"

        # --- BET LOGIC ---
        if "1ST" in status and minute in MINUTES_REGULAR_BET and not state["bet"]:
            if not firebase_manager.is_state_locked():

                if score in ["1-1", "2-2", "3-3"]:

                    stake, seq = calculate_stake()

                    firebase_manager.add_unresolved_bet(fid, {
                        "match_name": match_name,
                        "league": league,
                        "36_score": score,
                        "stake": stake,
                        "match_sequence": seq
                    })

                    send_telegram(
                        f"🎯 *BET PLACED*\n\n*Match:* {match_name}\n*Score:* {score}\n*Stake:* {stake}"
                    )

            # Store timestamp instead of True to allow time-based cleanup
            state["bet"] = time.time()

        # --- RESOLUTION LOGIC ---
        elif "HALFTIME" in status:
            unresolved = firebase_manager.get_unresolved_bet(fid)

            if unresolved:
                outcome = "win" if score == unresolved["36_score"] else "loss"

                firebase_manager.move_to_resolved(fid, unresolved, outcome)

                send_telegram(
                    f"{'✅ *WIN*' if outcome == 'win' else '❌ *LOSS*'}\n\n{match_name}\nFinal HT Score: {score}"
                )

                LOCAL_TRACKED_MATCHES.pop(fid, None)

    except Exception as e:
        logger.error(f"Process error: {e}")

# --------------------------------------------------
# INIT
# --------------------------------------------------

def initialize_bot_services():
    global firebase_manager, SOFASCORE_CLIENT

    firebase_manager = FirebaseManager(FIREBASE_CREDENTIALS)

    try:
        SOFASCORE_CLIENT = SofascoreClient()
        SOFASCORE_CLIENT.initialize()

        logger.info("✅ Sofascore initialized")
        return True

    except Exception as e:
        logger.error(f"Sofascore init error: {e}")
        return False

# --------------------------------------------------
# MAIN CYCLE
# --------------------------------------------------

def run_bot_cycle():
    global LAST_FETCH_TIME, CACHED_EVENTS

    try:
        now = time.time()

        # Cache Logic
        if now - LAST_FETCH_TIME > CACHE_TTL:
            events = SOFASCORE_CLIENT.get_events(live=True)

            if events:
                CACHED_EVENTS = events
                LAST_FETCH_TIME = now
                logger.info(f"🔄 Fresh fetch: {len(events)} matches")
            else:
                logger.warning("⚠️ Using cached data (blocked or no events)")

        else:
            logger.info(f"♻️ Using cache: {len(CACHED_EVENTS)} matches")

        # Process each match
        for match in CACHED_EVENTS:
            process_match(match)

        # --- CLEANUP LOGIC (Fixed Syntax) ---
        now_cleanup = time.time()
        to_remove = [
            k for k, v in LOCAL_TRACKED_MATCHES.items()
            if (now_cleanup - v["bet"] if isinstance(v["bet"], (int, float)) else 0) > 3600
        ]

        for k in to_remove:
            LOCAL_TRACKED_MATCHES.pop(k, None)

    except Exception as e:
        logger.error(f"Cycle error: {e}")

# --------------------------------------------------
# ENTRY POINT (Example usage)
# --------------------------------------------------
if __name__ == "__main__":
    if initialize_bot_services():
        while True:
            run_bot_cycle()
            time.sleep(SLEEP_TIME)
