"""
Sofascore client module - FIXED VERSION
"""

import logging
from .service import SofascoreService
from .types import Event, Player, Tournament, Team, Category, EntityType


class SofascoreClient:

    def __init__(self, browser_path: str = None, use_proxy: str = None, headless: bool = False):
        self.logger = logging.getLogger(__name__)
        self.service: SofascoreService | None = None
        self.browser_path = browser_path
        self.use_proxy = use_proxy
        self.headless = headless
        self.__initialized = False

        self.logger.info("SofascoreClient initialized (service pending).")

    def initialize(self, use_proxy: str = None, headless: bool = None):

        if self.service is None:

            proxy = use_proxy if use_proxy is not None else self.use_proxy
            headless_mode = headless if headless is not None else self.headless

            self.service = SofascoreService(
                browser_path=self.browser_path,
                use_proxy=proxy,
                headless=headless_mode
            )

            self.__initialized = True
            self.logger.info(f"✅ Service initialized (headless={headless_mode})")

        else:
            self.logger.warning("Service already initialized.")

    def close(self):
        if self.service:
            self.service.close()
            self.service = None
            self.__initialized = False
            self.logger.info("SofascoreClient resources closed.")

    def get_events(self, date: str = 'today', live: bool = False):
        if not self.service:
            return []
        try:
            return self.service.get_live_events() if live else self.service.get_events(date)
        except Exception as e:
            self.logger.error(f"Error fetching events: {e}")
            return []

    def get_event(self, event_id: int):
        if not self.service:
            return None
        try:
            return self.service.get_event(event_id)
        except Exception as e:
            self.logger.error(f"Error getting event: {e}")
            return None

    def get_match_stats(self, event_id: int):
        if not self.service:
            return None
        try:
            return self.service.get_match_stats(event_id)
        except Exception as e:
            self.logger.error(f"Error getting stats: {e}")
            return None

    def is_initialized(self) -> bool:
        return self.__initialized