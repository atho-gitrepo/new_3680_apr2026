# esd/sofascore/client.py
"""
Sofascore client module wrapped with high-availability life-cycle monitoring
and proactive service recovery capabilities.
"""

import logging
import time
from .service import SofascoreService
from .types import (
    Event,
    Player,
    Tournament,
    Team,
    Category,
    EntityType,
)
from .types.match_stats import parse_match_stats, MatchStats


class SofascoreClient:
    """
    An anti-fragile high-availability client acting as a facade for the underlying
    SofascoreService scraper worker, equipped with self-healing driver recovery.
    """

    def __init__(self, browser_path: str = None):
        """
        Initializes the Sofascore client controller.
        """
        self.logger = logging.getLogger("BetBot.Client")
        self.service: SofascoreService | None = None
        self.browser_path = browser_path
        self._initialized = False
        self._last_recovery_attempt = 0.0
        self.logger.info("⚙️ SofascoreClient wrapper instance built. Service instantiation deferred.")

    def initialize(self) -> bool:
        """
        Explicitly boots up the underlying automated browser context and resources.
        Returns True if successful, False otherwise.
        """
        if self._initialized and self.service:
            self.logger.debug("SofascoreService context is already alive. Skipping init sequence.")
            return True

        try:
            self.logger.info("🚀 Instantiating headless automated scraping driver layers...")
            self.service = SofascoreService(self.browser_path)
            self.service.initialize() # Ensure underlying resources execute setup cleanly
            self._initialized = True
            self.logger.info("✅ SofascoreService driver layer successfully mounted and online.")
            return True
        except Exception as e:
            self.logger.error(f"💥 Critical driver boot failure during initialization: {e}", exc_info=True)
            self._initialized = False
            self.service = None
            return False

    def _ensure_healthy_service(self) -> bool:
        """
        Internal guard clause to check driver health and attempt self-healing 
        if the underlying browser container has terminated or glitched.
        """
        if self._initialized and self.service:
            return True

        current_time = time.time()
        # Prevent rapid infinite retry loops (Throttle recovery to once every 30 seconds)
        if current_time - self._last_recovery_attempt < 30.0:
            self.logger.warning("⏳ Service health check failed, but recovery is throttled. Skipping retry.")
            return False

        self._last_recovery_attempt = current_time
        self.logger.warning("🚨 Service driver is uninitialized or dead! Triggering automatic self-healing recovery...")
        return self.initialize()

    def close(self):
        """
        Safely shuts down the browser interfaces and releases Playwright framework ports.
        """
        if self.service:
            try:
                self.logger.info("🔌 Closing active scraping drivers and browser instances safely...")
                self.service.close()
            except Exception as e:
                self.logger.error(f"⚠️ Error encountered during driver shutdown phase: {e}")
            finally:
                self.service = None
                self._initialized = False
                self.logger.info("🧹 SofascoreClient background context terminated cleanly.")

    # --- Data Retrieval Refactored Methods ---

    def get_events(self, date: str = 'today', live: bool = False) -> list[Event]:
        """
        Fetches events for a targeted date calendar or pulls active live match objects.
        Leverages automated fallback layers transparently inside the service execution thread.
        """
        if not self._ensure_healthy_service():
            self.logger.error("❌ High-Availability Block: Scraper service layer is offline. Cannot get events.")
            return []
            
        try:
            if live:
                return self.service.get_live_events()
            return self.service.get_events(date)
        except Exception as e:
            self.logger.error(f"❌ Exception leak intercepted at client event interface: {e}", exc_info=True)
            return []

    def search(self, query: str, entity: EntityType = EntityType.ALL) -> list[Event | Team | Player | Tournament]:
        """
        Executes an automated keyword lookup across events, active teams, players, or leagues.
        """
        if not self._ensure_healthy_service():
            self.logger.error("❌ High-Availability Block: Scraper service layer is offline. Cannot execute search.")
            return []
            
        try:
            return self.service.search(query, entity)
        except Exception as e:
            self.logger.error(f"❌ Exception leak intercepted at client search interface: {e}")
            return []

    def get_event(self, event_id: int) -> Event | None:
        """
        Fetches detailed structural info metrics for a unique match ID event node.
        """
        if not self._ensure_healthy_service():
            self.logger.error(f"❌ High-Availability Block: Scraper service layer is offline. Cannot pull event ID {event_id}.")
            return None
            
        try:
            return self.service.get_event(event_id)
        except Exception as e:
            self.logger.error(f"❌ Exception leak intercepted for event retrieval sequence (ID: {event_id}): {e}")
            return None
    
    def get_player(self, player_id: int) -> Player | None:
        """
        Fetches bio profiling metrics data for a unique player asset node.
        """
        if not self._ensure_healthy_service():
            self.logger.error(f"❌ High-Availability Block: Scraper service layer is offline. Cannot pull player ID {player_id}.")
            return None
            
        try:
            return self.service.get_player(player_id)
        except Exception as e:
            self.logger.error(f"❌ Exception leak intercepted for player retrieval sequence (ID: {player_id}): {e}")
            return None

    def get_stats(self, event_id: int) -> MatchStats:
        """
        Extracts, merges, and parses multi-platform tracking statistics arrays for a target match event.
        Guarantees structured empty schemas if network providers block the data payload stream.
        """
        if not self._ensure_healthy_service():
            self.logger.error(f"❌ High-Availability Block: Scraper service layer is offline. Skipping statistic compilation.")
            return MatchStats()
            
        try:
            raw_stats_data = self.service.get_raw_statistics(event_id)
            raw_probabilities = self.service.get_raw_probabilities(event_id)
            return parse_match_stats(raw_stats_data, raw_probabilities)
        except Exception as e:
            self.logger.error(f"❌ Critical structural failure parsing match statistical metrics matrices inside Client Layer: {e}")
            return MatchStats()
