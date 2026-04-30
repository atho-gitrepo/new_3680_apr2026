import time
import signal
import sys
from datetime import datetime
from bot import run_bot_cycle, SLEEP_TIME, initialize_bot_services, shutdown_bot, send_telegram

# --- PHASE 1 RELIABILITY CONFIG ---
WATCHDOG_LIMIT = 300  # 5 mins
REBOOT_LIMIT = 7200    # 2 hours

RUNNING = True
LAST_REBOOT = time.time()
LAST_HEARTBEAT = time.time()

def signal_handler(signum, frame):
    global RUNNING
    RUNNING = False

def main():
    global LAST_REBOOT, LAST_HEARTBEAT
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("🚀 Bot Starting...")
    if not initialize_bot_services():
        print("❌ Init failed.")
        sys.exit(1)
    
    send_telegram("🚀 **Live Score Bot Start**\nBotActive and Healthy.")

    while RUNNING:
        try:
            # 1. Periodic Reboot to prevent memory leaks
            if time.time() - LAST_REBOOT > REBOOT_LIMIT:
                print("🧹 Cleaning browser memory...")
                shutdown_bot()
                time.sleep(5)
                initialize_bot_services()
                LAST_REBOOT = time.time()

            # 2. Cycle with Watchdog
            start = time.time()
            run_bot_cycle()
            
            elapsed = time.time() - start
            if elapsed > WATCHDOG_LIMIT:
                print(f"⚠️ Watchdog triggered ({elapsed}s). Resetting...")
                send_telegram("⚠️ **Watchdog Alert**: Scraper was hung. Resetting browser...")
                shutdown_bot()
                time.sleep(10)
                initialize_bot_services()

            # 3. Heartbeat
            if time.time() - LAST_HEARTBEAT > 14400:
                send_telegram("💓 **Heartbeat**: Scanning active.")
                LAST_HEARTBEAT = time.time()

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)
        finally:
            if RUNNING: time.sleep(SLEEP_TIME)

    print("🛑 Shutdown.")
    shutdown_bot()

if __name__ == "__main__":
    main()
