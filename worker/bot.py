import os
import time
import json
import logging
import requests
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from esd.sofascore import SofascoreClient

# --------------------------------------------------
# LOGGING
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("BetBot")

# --------------------------------------------------
# ENV
# --------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

# --------------------------------------------------
# SETTINGS (SAFE MODE)
# --------------------------------------------------

SLEEP_TIME = 150
FETCH_INTERVAL = 180

ORIGINAL_STAKE = 10.0

# --------------------------------------------------
# GLOBAL STATE
# --------------------------------------------------

SOFASCORE_CLIENT = None
firebase_manager = None

CACHE = []
LAST_FETCH = 0
LOCAL_TRACKED = {}

# --------------------------------------------------
# FIREBASE
# --------------------------------------------------

class FirebaseManager:
    def __init__(self, creds):
        self.db = None

        if not creds:
            logger.error("Missing Firebase credentials")
            return

        try:
            data = json.loads(creds)
            cred = credentials.Certificate(data)

            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)

            self.db = firestore.client()
            logger.info("✅ Firebase Connected")

        except Exception as e:
            logger.error(f"Firebase error: {e}")

    def is_locked(self):
        return len(self.db.collection("unresolved_bets").limit(1).get()) > 0

    def add_bet(self, mid, data):
        data["placed_at"] = datetime.utcnow().isoformat()
        self.db.collection("unresolved_bets").document(str(mid)).set(data)

    def get_bet(self, mid):
        doc = self.db.collection("unresolved_bets").document(str(mid)).get()
        return doc.to_dict() if doc.exists else None

    def resolve(self, mid, data, outcome):
        data.update({
            "outcome": outcome,
            "resolved_at": datetime.utcnow().isoformat()
        })

        self.db.collection("resolved_bets").document(str(mid)).set(data)
        self.db.collection("unresolved_bets").document(str(mid)).delete()

# --------------------------------------------------
# TELEGRAM
# --------------------------------------------------

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# --------------------------------------------------
# CORE PROCESS
# --------------------------------------------------

def process_match(match):
    try:
        fid = str(match.id)
        league = match.tournament.name

        minute = getattr(match, "total_elapsed_minutes", 0)
        status = match.status.description.upper()
        score = f"{match.home_score.current}-{match.away_score.current}"

        state = LOCAL_TRACKED.get(fid, {"bet": False})
        LOCAL_TRACKED[fid] = state

        name = f"{match.home_team.name} vs {match.away_team.name}"

        # BET
        if "1ST" in status and minute in [36, 37] and not state["bet"]:
            if not firebase_manager.is_locked():

                if score in ["1-1", "2-2", "3-3"]:
                    firebase_manager.add_bet(fid, {
                        "match": name,
                        "league": league,
                        "score": score,
                        "stake": ORIGINAL_STAKE
                    })

                    send_telegram(f"🎯 BET\n{name}\n{league}\n{score}")

            state["bet"] = True

        # RESULT
        elif "HALFTIME" in status:
            bet = firebase_manager.get_bet(fid)

            if bet:
                outcome = "win" if score == bet["score"] else "loss"

                firebase_manager.resolve(fid, bet, outcome)

                send_telegram(f"{'✅ WIN' if outcome=='win' else '❌ LOSS'}\n{name}\n{score}")

                LOCAL_TRACKED.pop(fid, None)

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

        logger.info("✅ Sofascore ready")
        return True

    except Exception as e:
        logger.error(f"Sofascore init error: {e}")
        return False

# --------------------------------------------------
# CLEAN SHUTDOWN
# --------------------------------------------------

def shutdown_bot():
    try:
        if SOFASCORE_CLIENT:
            SOFASCORE_CLIENT.close()
    except:
        pass

# --------------------------------------------------
# MAIN CYCLE
# --------------------------------------------------

def run_bot_cycle():
    global CACHE, LAST_FETCH

    if not SOFASCORE_CLIENT:
        return

    try:
        now = time.time()

        # FETCH ONLY EVERY 3 MIN
        if now - LAST_FETCH > FETCH_INTERVAL:
            events = SOFASCORE_CLIENT.get_events(live=True)

            if events:
                CACHE = events
                LAST_FETCH = now
                logger.info(f"🔄 Fresh fetch: {len(events)} matches")

        for match in CACHE:
            process_match(match)

    except Exception as e:
        logger.error(f"Cycle error: {e}")