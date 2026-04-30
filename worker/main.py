import time
import signal
import sys
import os
from bot import run_bot_cycle, SLEEP_TIME, initialize_bot_services, shutdown_bot, send_telegram

WATCHDOG_LIMIT = 300
REBOOT_LIMIT = 7200

RUNNING = True
LAST_REBOOT = time.time()
LAST_HEARTBEAT = time.time()

def signal_handler(signum, frame):
    global RUNNING
    RUNNING = False

def main():
    global LAST_REBOOT, LAST_HEARTBEAT

    print(f"🚀 Bot Starting... PID={os.getpid()}")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not initialize_bot_services():
        print("❌ Init failed.")
        sys.exit(1)

    send_telegram("🚀 **Live Score Bot Started**")

    while RUNNING:
        try:
            # Periodic reboot
            if time.time() - LAST_REBOOT > REBOOT_LIMIT:
                print("🧹 Rebooting services...")
                shutdown_bot()
                time.sleep(5)

                if not initialize_bot_services():
                    send_telegram("❌ Re-init failed after reboot")
                    time.sleep(30)
                    continue

                LAST_REBOOT = time.time()

            start = time.time()
            run_bot_cycle()

            elapsed = time.time() - start
            if elapsed > WATCHDOG_LIMIT:
                send_telegram("⚠️ Watchdog triggered. Restarting services...")
                shutdown_bot()
                time.sleep(10)
                initialize_bot_services()

            # Heartbeat every 4h
            if time.time() - LAST_HEARTBEAT > 14400:
                send_telegram("💓 Bot heartbeat active")
                LAST_HEARTBEAT = time.time()

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

        finally:
            if RUNNING:
                time.sleep(SLEEP_TIME)

    print("🛑 Shutdown.")
    shutdown_bot()

if __name__ == "__main__":
    main()