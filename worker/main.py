#worker/main.py
"""
Orchestration layer and supervisor engine managing execution loops,
watchdog timers, smart maintenance reboots, and signal state handshakes.
"""

import time
import signal
import sys
import os
import logging

from bot import (
    run_bot_cycle,
    SLEEP_TIME,
    initialize_bot_services,
    shutdown_bot,
    send_telegram,
    LOCAL_TRACKED_MATCHES
)

# --- CONFIGURATION SETTINGS ---
WATCHDOG_LIMIT = 300       # Maximum time allowed per cycle run (5 minutes)
REBOOT_LIMIT = 86400       # ⚡ EXTENDED: 24-hour maintenance window (Playwright resource leaks eliminated)
HEARTBEAT_LIMIT = 3600     # Standardized 1-hour heartbeat validation pulse

# --- STATE VARIABLES ---
RUNNING = True
LAST_REBOOT = time.time()
LAST_HEARTBEAT = time.time()

logger = logging.getLogger("BetBot.Supervisor")

# --------------------------------------------------
# SIGNAL MANAGEMENT INTERRUPT HANDLERS
# --------------------------------------------------

def handle_shutdown_signal(signum, frame):
    """Intercepts OS signals to trigger a clean exit."""
    global RUNNING
    logger.warning(f"⚠️ POSIX Interrupt signal received ({signum}). Initiating clean shutdown process...")
    RUNNING = False

# --------------------------------------------------
# COMPONENT RESTORATION UTILITIES
# --------------------------------------------------

def execute_safe_recovery_handshake() -> bool:
    """
    Safely breaks down broken connections and blocks execution in a retry loop 
    until service instances recover fully. Prevents partial state failures.
    """
    logger.warning("🔄 Initiating service recovery sequence...")
    send_telegram("⚠️ Supervisor Core: Initiating automatic service layer recovery sequence...")
    
    try:
        shutdown_bot()
    except Exception as e:
        logger.error(f"Error tearing down services during recovery: {e}")

    retry_count = 0
    while RUNNING:
        retry_count += 1
        logger.info(f"Recovery Attempt #{retry_count}: Re-mounting service drivers...")
        
        if initialize_bot_services():
            logger.info("✅ Service layer successfully restored and recovered.")
            send_telegram("✅ Supervisor Core: Service recovery completed. Bot is back online.")
            return True
            
        logger.error(f"❌ Recovery Attempt #{retry_count} failed. Retrying in 30 seconds...")
        time.sleep(30)
        
    return False

def can_safely_reboot() -> bool:
    """
    Checks if any tracked matches are currently in an active or critical betting window 
    to prevent dropping data tracking frames during a reboot.
    """
    if not LOCAL_TRACKED_MATCHES:
        return True
        
    # Check if any match in the local cache is marked as active
    active_matches = [fid for fid, state in LOCAL_TRACKED_MATCHES.items() if state.get('active', False)]
    if active_matches:
        logger.warning(f"⏳ Scheduled reboot deferred. {len(active_matches)} match(es) are currently inside active evaluation windows.")
        return False
        
    return True

# --------------------------------------------------
# CENTRAL MAIN RUNTIME ENGINE
# --------------------------------------------------

def main():
    global LAST_REBOOT, LAST_HEARTBEAT

    logger.info("=" * 60)
    logger.info(f"🚀 SUPERVISOR DAEMON ONLINE. RUNNING ON PID={os.getpid()}")
    logger.info("=" * 60)

    # Register OS Signal Intercept Bindings
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    # Initial Component Boot Verification Sequence
    if not initialize_bot_services():
        logger.critical("💥 Initial core component initialization failed! Terminating Supervisor process.")
        sys.exit(1)

    send_telegram("🚀 **BetBot Daemon Supervisor Online**\nEnvironment: Production\nStatus: Tracking Active")

    while RUNNING:
        cycle_start_time = time.time()

        try:
            # 1. Execute Core Scraper and Strategy Processing Cycle
            run_bot_cycle()

            # 2. Watchdog Processing Execution Boundary Verification
            elapsed_cycle_time = time.time() - cycle_start_time
            if elapsed_cycle_time > WATCHDOG_LIMIT:
                logger.error(f"🚨 WATCHDOG BREACH: Execution cycle took {elapsed_cycle_time:.2f}s (Limit: {WATCHDOG_LIMIT}s).")
                if not execute_safe_recovery_handshake():
                    break  # Break loop if shutdown was triggered during recovery

            # 3. Smart Maintenance Reboot Check
            if time.time() - LAST_REBOOT > REBOOT_LIMIT:
                if can_safely_reboot():
                    logger.warning("🔄 Executing scheduled maintenance reboot...")
                    send_telegram("🔄 **Supervisor Notice:** Executing scheduled daily maintenance reboot...")
                    
                    if execute_safe_recovery_handshake():
                        LAST_REBOOT = time.time()
                else:
                    # Defer maintenance check by 5 minutes to let critical match windows pass
                    logger.info("Maintenance reboot deferred. Retrying in 5 minutes.")
                    LAST_REBOOT += 300

            # 4. Heartbeat Status Update Verification Node
            if time.time() - LAST_HEARTBEAT > HEARTBEAT_LIMIT:
                active_cache_count = len(LOCAL_TRACKED_MATCHES)
                send_telegram(f"💓 **Heartbeat Pulse:** Bot Status: Active\n📊 Cached Matches: {active_cache_count}")
                LAST_HEARTBEAT = time.time()

        except Exception as e:
            logger.error(f"💥 Uncaught Exception leaked to main supervisor loop context: {e}", exc_info=True)
            time.sleep(15)

        finally:
            # Sleep control check to enforce strict block iteration cycles
            if RUNNING:
                # Calculate remaining time to sleep after subtracting elapsed processing latency
                execution_latency = time.time() - cycle_start_time
                dynamic_sleep = max(1.0, SLEEP_TIME - execution_latency)
                
                logger.debug(f"Loop processing elapsed time: {execution_latency:.2f}s. Sleeping for remaining {dynamic_sleep:.2f}s.")
                time.sleep(dynamic_sleep)

    # --- EXIT AND CLEANUP TERMINATION SEQUENCE ---
    logger.warning("🛑 Stopping bot loop. Beginning system cleanup sequence...")
    send_telegram("🛑 **Supervisor Alert:** Daemon process is stopping. Services are shutting down...")
    
    try:
        shutdown_bot()
        logger.info("✅ All background driver assets closed cleanly.")
    except Exception as e:
        logger.error(f"Error encountered during system shutdown sequence: {e}")
        
    logger.info("👋 System execution closed. Exiting process container.")

# --------------------------------------------------
# SCRIPT INITIAL ENTRY HANDLER
# --------------------------------------------------

if __name__ == "__main__":
    main()
