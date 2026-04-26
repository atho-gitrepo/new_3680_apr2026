#esd/sofascore/service.py
"""
Sofascore service module - Patched for Stealth and Anti-Detection
"""

from __future__ import annotations
import playwright.sync_api
from playwright_stealth import stealth
import os
import logging
import subprocess
import sys
import time

# Browser installation check for Cloud Environments (Railway/VPS)
def install_playwright_browsers():
    """Install Playwright browsers if missing"""
    logger = logging.getLogger(__name__)
    try:
        logger.info("Checking Playwright browser installation...")
        result = subprocess.run([
            sys.executable, "-m", "playwright", "install", "chromium"
        ], capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            logger.info("Playwright browsers verified/installed successfully")
            return True
        else:
            logger.error(f"Browser installation failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Browser installation error: {e}")
        return False

# Ensure browsers are available on startup
install_playwright_browsers()

from ..utils import get_json, get_today
from .endpoints import SofascoreEndpoints
from .types import (
    Event,
    parse_event,
    parse_events,
    parse_player,
    parse_player_attributes,
    parse_transfer_history,
    parse_team,
    parse_tournament,
    parse_tournaments,
    parse_seasons,
    parse_brackets,
    parse_standings,
    parse_incidents,
    parse_top_players_match,
    parse_comments,
    parse_shots,
    parse_top_tournament_teams,
    parse_top_tournament_players,
    TopTournamentPlayers,
    TopTournamentTeams,
    Shot,
    Comment,
    TopPlayersMatch,
    Incident,
    Bracket,
    Season,
    Tournament,
    Standing,
    Team,
    Player,
    PlayerAttributes,
    TransferHistory,
    MatchStats,
    parse_match_stats,
    Lineups,
    parse_lineups,
    EntityType,
    Category,
)

class SofascoreService:
    """
    A class to represent the SofaScore service with built-in stealth.
    """

    def __init__(self, browser_path: str = None):
        """
        Initializes the SofaScore service.
        """
        self.logger = logging.getLogger(__name__)
        self.browser_path = browser_path
        self.endpoints = SofascoreEndpoints()
        self.playwright = self.browser = self.page = self.context = None
        self.__init_playwright()

    def __init_playwright(self):
        """
        Initialize Playwright with stealth settings and session priming.
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.logger.info(f"Initializing Playwright with Stealth (attempt {attempt + 1})")
                self.playwright = playwright.sync_api.sync_playwright().start()
                
                # --- PROXY CONFIGURATION ---
                # It is highly recommended to use environment variables for these.
                # Example: PROXY_SERVER="http://proxy.example.com:8080"
                proxy_server = os.getenv("PROXY_SERVER")
                proxy_user = os.getenv("PROXY_USER")
                proxy_pass = os.getenv("PROXY_PASS")

                proxy_cfg = None
                if proxy_server:
                    proxy_cfg = {"server": proxy_server}
                    if proxy_user and proxy_pass:
                        proxy_cfg["username"] = proxy_user
                        proxy_cfg["password"] = proxy_pass

                launch_options = {
                    'headless': True,
                    'proxy': proxy_cfg,
                    'args': [
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-blink-features=AutomationControlled',
                        '--disable-infobars',
                        '--disable-web-security',
                    ],
                    'timeout': 60000
                }
                
                if self.browser_path and os.path.exists(self.browser_path):
                    launch_options['executable_path'] = self.browser_path
                
                self.browser = self.playwright.chromium.launch(**launch_options)
                
                # Create a realistic browser context
                self.context = self.browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-US'
                )
                
                self.page = self.context.new_page()
                self.page.set_default_timeout(45000)
                
                # Apply Stealth to hide Playwright fingerprint
                stealth_sync(self.page)
                
                # --- SESSION PRIMING ---
                # Sofascore blocks direct API calls without proper cookies/headers.
                # Visiting the homepage first mimics a real user session.
                self.logger.info("Navigating to Sofascore to establish session...")
                self.page.goto("https://www.sofascore.com", wait_until="domcontentloaded", timeout=60000)
                time.sleep(2) # Small delay to allow cookies to set
                
                self.logger.info("Playwright initialized successfully with Stealth.")
                return
                
            except Exception as exc:
                self.logger.error(f"Playwright initialization failed: {str(exc)}")
                self.close()
                if attempt == max_retries - 1:
                    raise RuntimeError("Failed to bypass Sofascore blocks after retries.") from exc

    def close(self):
        """
        Close all Playwright resources.
        """
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            self.page = self.context = self.browser = self.playwright = None
            self.logger.info("Playwright resources closed successfully")
        except Exception as exc:
            self.logger.error(f"Error during cleanup: {str(exc)}")

    def __del__(self):
        self.close()

    # --- API Retrieval Methods ---

    def get_event(self, event_id: int) -> Event:
        try:
            url = self.endpoints.event_endpoint(event_id)
            data = get_json(self.page, url)["event"]
            return parse_event(data)
        except Exception as exc:
            self.logger.error(f"Failed to get event {event_id}: {str(exc)}")
            raise exc

    def get_events(self, date: str = 'today') -> list[Event]:
        if date == 'today':
            date = get_today()
        try:
            url = self.endpoints.events_endpoint.format(date=date)
            return parse_events(get_json(self.page, url)["events"])
        except Exception as exc:
            self.logger.error(f"Failed to get events for date {date}: {str(exc)}")
            raise exc

    def get_live_events(self) -> list[Event]:
        try:
            url = self.endpoints.live_events_endpoint
            response = get_json(self.page, url)
            return parse_events(response.get("events", []))
        except Exception as exc:
            self.logger.error(f"Failed to get live events: {str(exc)}")
            raise exc

    def get_player(self, player_id: int) -> Player:
        try:
            url = self.endpoints.player_endpoint(player_id)
            data = get_json(self.page, url)
            if "player" in data:
                player = parse_player(data["player"])
                player.attributes = self.get_player_attributes(player_id)
                player.transfer_history = self.get_player_transfer_history(player_id)
                return player
            return Player()
        except Exception as exc:
            self.logger.error(f"Failed to get player {player_id}: {str(exc)}")
            raise exc

    def get_player_attributes(self, player_id: int) -> PlayerAttributes:
        try:
            url = self.endpoints.player_attributes_endpoint(player_id)
            data = get_json(self.page, url)
            return parse_player_attributes(data.get("playerAttributes", {}))
        except Exception as exc:
            self.logger.error(f"Failed to get player attributes {player_id}: {str(exc)}")
            raise exc

    def get_player_transfer_history(self, player_id: int) -> TransferHistory:
        try:
            url = self.endpoints.player_transfer_history_endpoint(player_id)
            data = get_json(self.page, url)
            return parse_transfer_history(data) if data else TransferHistory()
        except Exception as exc:
            self.logger.error(f"Failed to get transfer history {player_id}: {str(exc)}")
            raise exc

    def get_match_lineups(self, event_id: int) -> Lineups:
        try:
            url = self.endpoints.match_lineups_endpoint(event_id)
            return parse_lineups(get_json(self.page, url))
        except Exception as exc:
            self.logger.error(f"Failed to get lineups for event {event_id}: {str(exc)}")
            raise exc

    def get_match_incidents(self, event_id: int) -> list[Incident]:
        try:
            url = self.endpoints.match_events_endpoint(event_id)
            data = get_json(self.page, url).get("incidents", [])
            return parse_incidents(data)
        except Exception as exc:
            self.logger.error(f"Failed to get incidents for event {event_id}: {str(exc)}")
            raise exc

    def get_match_stats(self, event_id: int) -> MatchStats:
        try:
            url = self.endpoints.match_stats_endpoint(event_id)
            stats_data = get_json(self.page, url).get("statistics", {})
            prob_url = self.endpoints.match_probabilities_endpoint(event_id)
            prob_data = get_json(self.page, prob_url).get("winProbability", {})
            return parse_match_stats(stats_data, prob_data)
        except Exception as exc:
            self.logger.error(f"Failed to get stats for event {event_id}: {str(exc)}")
            raise exc

    def get_team(self, team_id: int) -> Team:
        try:
            url = self.endpoints.team_endpoint(team_id)
            data = get_json(self.page, url)["team"]
            return parse_team(data)
        except Exception as exc:
            self.logger.error(f"Failed to get team {team_id}: {str(exc)}")
            raise exc

    def search(self, query: str, entity: EntityType = EntityType.ALL) -> list:
        try:
            url = self.endpoints.search_endpoint(query=query, entity_type=entity.value)
            results = get_json(self.page, url).get("results", [])
            # Logic for parsing based on EntityType can be added here
            return results
        except Exception as exc:
            self.logger.error(f"Search failed for '{query}': {str(exc)}")
            return []
