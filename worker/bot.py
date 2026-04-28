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
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler("bot_activity.log"), logging.StreamHandler()]
)
logger = logging.getLogger("BetBot")

# --- ENV VARS ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

# --- PROXY CONFIG ---
def get_proxy_config():
    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    user = os.getenv("PROXY_USER")
    password = os.getenv("PROXY_PASS")

    if host and port:
        if user and password:
            proxy_url = f"http://{user}:{password}@{host}:{port}"
        else:
            proxy_url = f"http://{host}:{port}"

        return {
            "http": proxy_url,
            "https": proxy_url
        }
    return None

# --- GLOBAL SESSION ---
SESSION = requests.Session()

proxy_config = get_proxy_config()
if proxy_config:
    SESSION.proxies.update(proxy_config)
    logger.info(f"✅ Proxy configured")
else:
    logger.warning("⚠️ No proxy configured!")

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
})

# --- SETTINGS ---
ORIGINAL_STAKE = 10.0
MAX_CHASE_LEVEL = 4
SLEEP_TIME = 60
MINUTES_REGULAR_BET = [36, 37]

# --- FILTERS ---
ALLOWED_LEAGUES = [
    'Campeonato Brasileiro Série A',
    'Segunda Division, Apertura',
    'Copa do Brasil',
    'Premier League'
]

EXCLUDED_LEAGUES = [
    'USA', 'Poland', 'Australia', 'Mexico', 'Wales',
    'Germany', 'England Amateur', 'U19', 'U21', 'Friendly'
]

AMATEUR_KEYWORDS = [
    'amateur', 'youth', 'reserves', 'friendly',
    'u23', 'u21', 'u20', 'women', 'college'
]

# --- GLOBALS ---
SOFASCORE_CLIENT = None
firebase_manager = None
LOCAL_TRACKED_MATCHES = {}

# --- FIREBASE ---
class FirebaseManager:
    def __init__(self, creds_json):
        self.db = None
        if not creds_json:
            logger.error("Firebase Credentials missing!")
            return

        try:
            cred_dict = json.loads(creds_json)
            cred = credentials.Certificate(cred_dict)

            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)

            self.db = firestore.client()
            logger.info("✅ Firebase Connected")

        except Exception as e:
            logger.error(f"Firebase Init Error: {e}")

    def is_state_locked(self):
        try:
            return len(self.db.collection('unresolved_bets').limit(1).get()) > 0
        except:
            return False

    def get_last_resolved_bet(self):
        try:
            query = self.db.collection('resolved_bets')\
                .order_by('resolution_timestamp', direction=firestore.Query.DESCENDING)\
                .limit(1).get()

            for doc in query:
                return doc.to_dict()
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
            'outcome': outcome,
            'resolved_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'resolution_timestamp': firestore.SERVER_TIMESTAMP
        })

        self.db.collection('resolved_bets').document(str(match_id)).set(data)
        self.db.collection('unresolved_bets').document(str(match_id)).delete()
        return True

# --- TELEGRAM ---
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        r = SESSION.post(
            url,
            data={
                'chat_id': TELEGRAM_CHAT_ID,
                'text': msg,
                'parse_mode': 'Markdown'
            },
            timeout=15
        )
        return r.status_code == 200

    except Exception as e:
        logger.error(f"Telegram Error: {e}")
        return False

# --- STAKE ---
def calculate_stake():
    last = firebase_manager.get_last_resolved_bet()

    if not last or last.get('outcome') == 'win':
        return ORIGINAL_STAKE, 1

    seq = last.get('match_sequence', 1)

    if seq < MAX_CHASE_LEVEL:
        return float(ORIGINAL_STAKE * (2 ** seq)), seq + 1

    return ORIGINAL_STAKE, 1

# --- MATCH PROCESS ---
def process_match(match):
    try:
        fid = str(match.id)
        league = match.tournament.name
        country = match.tournament.category.name
        full_info = f"{league} {country}".lower()

        if not any(x.lower() in league.lower() for x in ALLOWED_LEAGUES):
            if any(x.lower() in full_info for x in EXCLUDED_LEAGUES + AMATEUR_KEYWORDS):
                return

        min_elapsed = match.total_elapsed_minutes
        status = match.status.description.upper()
        score = f"{match.home_score.current}-{match.away_score.current}"

        match_info = {
            'match_name': f"{match.home_team.name} vs {match.away_team.name}",
            'league': league,
            'country': country
        }

        state = LOCAL_TRACKED_MATCHES.get(fid, {'bet_placed': False})
        LOCAL_TRACKED_MATCHES[fid] = state

        # --- BET AT 36 ---
        if '1ST' in status and min_elapsed in MINUTES_REGULAR_BET and not state['bet_placed']:
            if not firebase_manager.is_state_locked():

                if score in ['1-1', '2-2', '3-3']:
                    stake, seq = calculate_stake()

                    data = {
                        **match_info,
                        '36_score': score,
                        'stake': stake,
                        'match_sequence': seq,
                        'bet_type': 'regular'
                    }

                    firebase_manager.add_unresolved_bet(fid, data)

                    send_telegram(
                        f"🎯 BET PLACED (Match {seq})\n"
                        f"⏱ 36' | {match_info['match_name']}\n"
                        f"{country} | {league}\n"
                        f"Score: {score}\n"
                        f"Stake: ${stake:.2f}"
                    )

            state['bet_placed'] = True

        # --- HALF TIME ---
        elif 'HALFTIME' in status:
            unresolved = firebase_manager.get_unresolved_bet(fid)

            if unresolved:
                outcome = 'win' if score == unresolved['36_score'] else 'loss'

                if firebase_manager.move_to_resolved(fid, unresolved, outcome):

                    send_telegram(
                        f"{'✅ WIN' if outcome == 'win' else '❌ LOSS'}\n"
                        f"{match_info['match_name']}\n"
                        f"Score: {score}"
                    )

                    if fid in LOCAL_TRACKED_MATCHES:
                        del LOCAL_TRACKED_MATCHES[fid]

    except Exception as e:
        logger.error(f"Process Match Error: {e}")

# --- INIT ---
def initialize_bot_services():
    global firebase_manager, SOFASCORE_CLIENT

    firebase_manager = FirebaseManager(FIREBASE_CREDENTIALS)

    try:
        # 🔥 IMPORTANT: pass session to Sofascore
        SOFASCORE_CLIENT = SofascoreClient(session=SESSION)

        SOFASCORE_CLIENT.initialize()

        logger.info("✅ Sofascore Client Initialized")
        return True

    except Exception as e:
        logger.error(f"Sofascore Init Error: {e}")
        return False

# --- SHUTDOWN ---
def shutdown_bot():
    if SOFASCORE_CLIENT:
        try:
            SOFASCORE_CLIENT.close()
        except:
            pass

# --- MAIN LOOP ---
def run_bot_cycle():
    if not SOFASCORE_CLIENT:
        return

    try:
        events = SOFASCORE_CLIENT.get_events(live=True)

        # 🛑 HANDLE BLOCK / INVALID RESPONSE
        if not events or not isinstance(events, list):
            logger.error("❌ Sofascore blocked or invalid response")
            return

        logger.info(f"Scanning {len(events)} matches...")

        for match in events:
            process_match(match)

    except Exception as e:
        logger.error(f"Cycle Error: {e}")