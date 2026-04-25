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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
USE_PROXY = os.getenv("SOFASCORE_PROXY", None)
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "false").lower() == "true"

# --- SETTINGS ---
ORIGINAL_STAKE = 10.0
MAX_CHASE_LEVEL = 4
SLEEP_TIME = 60
MINUTES_REGULAR_BET = [36, 37]

# --- FILTERS ---
ALLOWED_LEAGUES = ['Campeonato Brasileiro Série A', 'Segunda Division, Apertura', 'Copa do Brasil', 'Premier League']
EXCLUDED_LEAGUES = ['USA', 'Poland','Australia', 'Mexico', 'Wales', 'Germany', 'England Amateur', 'U19', 'U21', 'Friendly']
AMATEUR_KEYWORDS = ['amateur', 'youth', 'reserves', 'friendly', 'u23', 'u21','u20', 'women', 'college']

# --- GLOBALS ---
SOFASCORE_CLIENT = None
firebase_manager = None
LOCAL_TRACKED_MATCHES = {}

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
            logger.info("✅ Firebase Connection Ready.")
        except Exception as e:
            logger.error(f"❌ Firebase Init Error: {e}")

    def is_state_locked(self):
        try:
            return len(self.db.collection('unresolved_bets').limit(1).get()) > 0
        except:
            return False

    def get_last_resolved_bet(self):
        try:
            query = self.db.collection('resolved_bets').order_by('resolution_timestamp', direction=firestore.Query.DESCENDING).limit(1).get()
            for doc in query:
                return doc.to_dict()
        except:
            return None
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

def send_telegram(msg):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "YOUR_TOKEN_HERE":
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'}, timeout=15)
        return r.status_code == 200
    except:
        return False

def calculate_stake():
    last = firebase_manager.get_last_resolved_bet()
    if not last or last.get('outcome') == 'win':
        return ORIGINAL_STAKE, 1
    seq = last.get('match_sequence', 1)
    if seq < MAX_CHASE_LEVEL:
        return float(ORIGINAL_STAKE * (2**seq)), seq + 1
    return ORIGINAL_STAKE, 1

def should_process_match(match):
    try:
        league = match.tournament.name
        country = match.tournament.category.name
        full_info = f"{league} {country}".lower()
        if any(x.lower() in league.lower() for x in ALLOWED_LEAGUES):
            return True
        if any(x.lower() in full_info for x in EXCLUDED_LEAGUES + AMATEUR_KEYWORDS):
            return False
        return False
    except:
        return False

def process_match(match):
    try:
        fid = str(match.id)
        league = match.tournament.name
        country = match.tournament.category.name
        if not should_process_match(match):
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

        if '1ST' in status and min_elapsed in MINUTES_REGULAR_BET and not state['bet_placed']:
            if not firebase_manager.is_state_locked():
                if score in ['1-1', '2-2', '3-3']:
                    stake, seq = calculate_stake()
                    data = {**match_info, '36_score': score, 'stake': stake, 'match_sequence': seq, 'bet_type': 'regular'}
                    firebase_manager.add_unresolved_bet(fid, data)
                    send_telegram(
                        f"🎯 **BET PLACED (Match {seq})**\n⏱ 36' | {match_info['match_name']}\n"
                        f"🌍 {country} | 🏆 {league}\n🔢 Score: {score}\n💰 Stake: ${stake:.2f}"
                    )
                    logger.info(f"Bet placed on {match_info['match_name']} at score {score}")
            state['bet_placed'] = True

        elif 'HALFTIME' in status:
            unresolved = firebase_manager.get_unresolved_bet(fid)
            if unresolved:
                outcome = 'win' if score == unresolved['36_score'] else 'loss'
                if firebase_manager.move_to_resolved(fid, unresolved, outcome):
                    emo = "✅ WIN" if outcome == 'win' else "❌ LOSS"
                    send_telegram(f"{emo} **HT Result**\n⚽️ {match_info['match_name']}\n🔢 Score: {score}\n🔓 System Unlocked.")
                    logger.info(f"Bet resolved on {match_info['match_name']}: {outcome}")
                    if fid in LOCAL_TRACKED_MATCHES:
                        del LOCAL_TRACKED_MATCHES[fid]
    except Exception as e:
        logger.error(f"Error processing match: {e}")

def initialize_bot_services():
    global firebase_manager, SOFASCORE_CLIENT
    logger.info("🚀 Bot Starting...")
    firebase_manager = FirebaseManager(FIREBASE_CREDENTIALS)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"Initializing Sofascore client (attempt {attempt + 1}/{max_retries})")
            SOFASCORE_CLIENT = SofascoreClient(browser_path=None, use_proxy=USE_PROXY, headless=HEADLESS_MODE)
            SOFASCORE_CLIENT.initialize()
            test_events = SOFASCORE_CLIENT.get_events(live=True)
            if test_events is not None:
                logger.info(f"✅ Sofascore client initialized successfully. Found {len(test_events)} live events")
                return True
            else:
                raise Exception("Test fetch returned None")
        except Exception as e:
            logger.error(f"Failed to initialize Sofascore client (attempt {attempt + 1}): {str(e)}")
            if attempt == max_retries - 1:
                return False
            time.sleep(5)
    return False

def shutdown_bot():
    logger.info("🛑 Shutting down bot...")
    if SOFASCORE_CLIENT:
        try:
            SOFASCORE_CLIENT.close()
            logger.info("Sofascore client closed")
        except Exception as e:
            logger.error(f"Error closing Sofascore client: {e}")

def run_bot_cycle():
    if not SOFASCORE_CLIENT:
        logger.error("Sofascore client not initialized")
        return False
    try:
        events = SOFASCORE_CLIENT.get_events(live=True)
        if events is None:
            logger.warning("No events returned from Sofascore")
            return False
        logger.info(f"Scanning {len(events)} live matches...")
        for match in events:
            process_match(match)
        return True
    except KeyError as e:
        if str(e) == "'events'":
            logger.error("API response missing 'events' key - possible access denied or API change")
        else:
            logger.error(f"KeyError in bot cycle: {e}")
        return False
    except Exception as e:
        logger.error(f"Error in bot cycle: {e}")
        return False

def main():
    cycle_count = 0
    consecutive_failures = 0
    max_consecutive_failures = 5

    if not initialize_bot_services():
        logger.error("Failed to initialize bot services. Exiting...")
        return

    logger.info(f"🎯 Bot is now running. Cycle interval: {SLEEP_TIME} seconds")
    logger.info(f"Stealth mode: {'Headless' if HEADLESS_MODE else 'Visible browser'}")
    if USE_PROXY:
        logger.info(f"Using proxy: {USE_PROXY}")

    try:
        while True:
            cycle_count += 1
            logger.info(f"--- Cycle #{cycle_count} ---")
            success = run_bot_cycle()
            if success:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                logger.warning(f"Cycle failed. Consecutive failures: {consecutive_failures}/{max_consecutive_failures}")
                if consecutive_failures >= max_consecutive_failures:
                    logger.error("Too many consecutive failures. Attempting to restart client...")
                    shutdown_bot()
                    time.sleep(10)
                    if initialize_bot_services():
                        consecutive_failures = 0
                        logger.info("Client restarted successfully")
                    else:
                        logger.error("Failed to restart client")
                        break
            logger.info(f"💤 Sleeping for {SLEEP_TIME} seconds...")
            time.sleep(SLEEP_TIME)
    except KeyboardInterrupt:
        logger.info("⚠️ Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error in main loop: {e}")
    finally:
        shutdown_bot()
        logger.info("✅ Bot shutdown complete")

if __name__ == "__main__":
    main()