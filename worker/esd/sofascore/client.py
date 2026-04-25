# esd/sofascore/client.py

"""
Sofascore client module with stealth and proxy support
"""

import logging
from .service import SofascoreService
from .types import (
    Event,
    Player,
    Tournament,
    Team,
    Category,
    EntityType,
)


class SofascoreClient:
    """
    A client to interact with the SofaScore service with anti-detection measures.
    """

    def __init__(self, browser_path: str = None, use_proxy: str = None, headless: bool = False):
        """
        Initializes the Sofascore client.
        
        Args:
            browser_path: Optional path to browser executable
            use_proxy: Optional proxy server URL (e.g., 'http://proxy:port')
            headless: Run browser in headless mode (False is more stealthy)
        """
        self.logger = logging.getLogger(__name__)
        self.service: SofascoreService | None = None
        self.browser_path = browser_path
        self.use_proxy = use_proxy
        self.headless = headless
        self.__initialized = False
        self.logger.info("SofascoreClient initialized (service pending).")
        if use_proxy:
            self.logger.info(f"Proxy configured: {use_proxy}")
        if not headless:
            self.logger.info("Running in visible browser mode (stealth)")

    def initialize(self, use_proxy: str = None, headless: bool = None):
        """
        Explicitly initializes the underlying service and resources.
        
        Args:
            use_proxy: Override proxy setting
            headless: Override headless setting
        """
        if self.service is None:
            proxy = use_proxy if use_proxy is not None else self.use_proxy
            headless_mode = headless if headless is not None else self.headless
            
            self.service = SofascoreService(
                browser_path=self.browser_path,
                use_proxy=proxy
            )
            self.service.headless_mode = headless_mode
            if hasattr(self.service, '__init_playwright'):
                self.service.__init_playwright()
            
            self.__initialized = True
            self.logger.info(f"SofascoreService successfully initialized (headless={headless_mode})")
        else:
            self.logger.warning("SofascoreService already initialized.")

    def close(self):
        """
        Closes the underlying service and releases resources (Playwright).
        """
        if self.service:
            self.service.close()
            self.service = None
            self.__initialized = False
            self.logger.info("SofascoreClient resources closed.")

    def get_events(self, date: str = 'today', live: bool = False) -> list[Event]:
        if not self.service:
            self.logger.error("Service not initialized. Cannot fetch events.")
            return []
        try:
            if live:
                return self.service.get_live_events()
            return self.service.get_events(date)
        except Exception as e:
            self.logger.error(f"Error fetching events: {str(e)}")
            return []

    def search(self, query: str, entity: EntityType = EntityType.ALL) -> list[Event | Team | Player | Tournament]:
        if not self.service:
            self.logger.error("Service not initialized. Cannot search.")
            return []
        try:
            return self.service.search(query, entity)
        except Exception as e:
            self.logger.error(f"Error searching for '{query}': {str(e)}")
            return []

    def get_event(self, event_id: int) -> Event:
        if not self.service:
            self.logger.error("Service not initialized. Cannot get event.")
            return None
        try:
            return self.service.get_event(event_id)
        except Exception as e:
            self.logger.error(f"Error getting event {event_id}: {str(e)}")
            return None
    
    def get_player(self, player_id: int) -> Player:
        if not self.service:
            self.logger.error("Service not initialized. Cannot get player.")
            return None
        try:
            return self.service.get_player(player_id)
        except Exception as e:
            self.logger.error(f"Error getting player {player_id}: {str(e)}")
            return None

    def get_team(self, team_id: int) -> Team:
        if not self.service:
            self.logger.error("Service not initialized. Cannot get team.")
            return None
        try:
            return self.service.get_team(team_id)
        except Exception as e:
            self.logger.error(f"Error getting team {team_id}: {str(e)}")
            return None

    def get_team_players(self, team_id: int) -> list[Player]:
        if not self.service:
            self.logger.error("Service not initialized. Cannot get team players.")
            return []
        try:
            return self.service.get_team_players(team_id)
        except Exception as e:
            self.logger.error(f"Error getting players for team {team_id}: {str(e)}")
            return []

    def get_tournaments_by_category(self, category_id: Category) -> list[Tournament]:
        if not self.service:
            self.logger.error("Service not initialized. Cannot get tournaments.")
            return []
        try:
            return self.service.get_tournaments_by_category(category_id)
        except Exception as e:
            self.logger.error(f"Error getting tournaments for category {category_id}: {str(e)}")
            return []

    def get_tournament_standings(self, tournament_id: int, season_id: int) -> list:
        if not self.service:
            self.logger.error("Service not initialized. Cannot get standings.")
            return []
        try:
            return self.service.get_tournament_standings(tournament_id, season_id)
        except Exception as e:
            self.logger.error(f"Error getting standings: {str(e)}")
            return []

    def get_match_stats(self, event_id: int):
        if not self.service:
            self.logger.error("Service not initialized. Cannot get match stats.")
            return None
        try:
            return self.service.get_match_stats(event_id)
        except Exception as e:
            self.logger.error(f"Error getting match stats for event {event_id}: {str(e)}")
            return None

    def get_match_lineups(self, event_id: int):
        if not self.service:
            self.logger.error("Service not initialized. Cannot get lineups.")
            return None
        try:
            return self.service.get_match_lineups(event_id)
        except Exception as e:
            self.logger.error(f"Error getting lineups for event {event_id}: {str(e)}")
            return None

    def get_match_incidents(self, event_id: int) -> list:
        if not self.service:
            self.logger.error("Service not initialized. Cannot get incidents.")
            return []
        try:
            return self.service.get_match_incidents(event_id)
        except Exception as e:
            self.logger.error(f"Error getting incidents for event {event_id}: {str(e)}")
            return []

    def is_initialized(self) -> bool:
        return self.__initialized and self.service is not None