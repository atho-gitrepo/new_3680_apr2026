"""
Sofascore service module - Standard Context Manager Build
Fixed: 'module' object is not callable by using 'with' statement.
"""

from __future__ import annotations
import os
import logging
import subprocess
import sys
import time

# Pre-load libraries
import playwright.sync_api
from playwright_stealth import stealth

def install_playwright_browsers():
    """Install Playwright browsers for Railway environment"""
    logger = logging.getLogger(__name__)
    try:
        if os.environ.get("SKIP_BROWSER_INSTALL"):
            return True
        logger.info("Verifying Playwright Chromium...")
        subprocess.run([
            sys.executable, "-m", "playwright", "install", "chromium"
        ], capture_output=True, text=True, timeout=300)
        return True
    except Exception as e:
        logger.error(f"Browser installation error: {e}")
        return False

install_playwright_browsers()

from ..utils import get_json, get_today
from .endpoints import SofascoreEndpoints
from .types import (
    Event, parse_event, parse_events, parse_player,
    parse_player_attributes, parse_transfer_history,
    parse_team, parse_tournament, parse_tournaments,
    parse_seasons, parse_brackets, parse_standings,
    parse_incidents, parse_top_players_match,
    parse_comments, parse_shots,
    parse_top_tournament_teams, parse_top_tournament_players,
    TopTournamentPlayers, TopTournamentTeams, Shot,
    Comment, TopPlayersMatch, Incident, Bracket,
    Season, Tournament, Standing, Team, Player,
    PlayerAttributes, TransferHistory, MatchStats,
    parse_match_stats, Lineups, parse_lineups,
    EntityType, Category,
)

class SofascoreService:
    def __init__(self, browser_path: str = None, **kwargs):
        """
        Initializes the SofaScore service.
        """
        self.logger = logging.getLogger(__name__)
        self.browser_path = browser_path
        self.endpoints = SofascoreEndpoints()
        
        # Use a dictionary to store Playwright objects
        self.pw = {'instance': None, 'browser': None, 'context': None}
        self.page = None 
        
        self.__init_playwright()

    def __init_playwright(self):
        """
        Initialize Playwright using the sync_api context manager directly.
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.logger.info(f"Initializing Playwright (attempt {attempt + 1})")
                
                # --- THE FIX: USE THE SYNC_API DIRECTLY ---
                from playwright.sync_api import sync_playwright
                
                # We use .start() on the return of the function call
                self.pw['instance'] = sync_playwright().start()
                
                # --- PROXY CONFIG ---
                proxy_server = os.getenv("PROXY_SERVER")
                proxy_user = os.getenv("PROXY_USER")
                proxy_pass = os.getenv("PROXY_PASS")

                proxy_cfg = None
                if proxy_server:
                    clean_url = proxy_server.replace("http://", "").replace("https://", "")
                    proxy_cfg = {"server": f"http://{clean_url}"}
                    if proxy_user and proxy_pass:
                        proxy_cfg["username"] = proxy_user
                        proxy_cfg["password"] = proxy_pass
                    self.logger.info(f"Using Proxy: {proxy_server}")

                launch_options = {
                    'headless': True,
                    'proxy': proxy_cfg,
                    'args': ['--no-sandbox', '--disable-dev-shm-usage'],
                    'timeout': 60000
                }
                
                if self.browser_path and os.path.exists(self.browser_path):
                    launch_options['executable_path'] = self.browser_path
                
                self.pw['browser'] = self.pw['instance'].chromium.launch(**launch_options)
                self.pw['context'] = self.pw['browser'].new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                )
                
                self.page = self.pw['context'].new_page()
                stealth(self.page)
                
                self.logger.info("Priming Sofascore session...")
                self.page.goto("https://www.sofascore.com", wait_until="domcontentloaded", timeout=60000)
                
                self.logger.info("✅ Service Initialized Successfully.")
                return
                
            except Exception as exc:
                self.logger.error(f"❌ Playwright Init Error: {str(exc)}")
                self.close()
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Initialization failed: {str(exc)}") from exc

    def close(self):
        """Clean up Playwright resources."""
        try:
            if self.page:
                self.page.close()
            if self.pw['context']:
                self.pw['context'].close()
            if self.pw['browser']:
                self.pw['browser'].close()
            if self.pw['instance']:
                self.pw['instance'].stop()
        except Exception as exc:
            if hasattr(self, 'logger'):
                self.logger.error(f"Cleanup error: {str(exc)}")
        finally:
            self.page = None
            self.pw = {'instance': None, 'browser': None, 'context': None}

    def __del__(self):
        self.close()

    def get_live_events(self) -> list[Event]:
        try:
            url = self.endpoints.live_events_endpoint
            response = get_json(self.page, url)
            return parse_events(response.get("events", []))
        except Exception as exc:
            self.logger.error(f"Failed to get live events: {str(exc)}")
            return []

    def get_events(self, date: str = 'today') -> list[Event]:
        if date == 'today':
            date = get_today()
        try:
            url = self.endpoints.events_endpoint.format(date=date)
            return parse_events(get_json(self.page, url)["events"])
        except Exception as exc:
            self.logger.error(f"Failed to get events: {str(exc)}")
            return []

    def get_event(self, event_id: int) -> Event:
        url = self.endpoints.event_endpoint(event_id)
        return parse_event(get_json(self.page, url)["event"])

    def get_match_stats(self, event_id: int) -> MatchStats:
        url = self.endpoints.match_stats_endpoint(event_id)
        stats_data = get_json(self.page, url).get("statistics", {})
        prob_url = self.endpoints.match_probabilities_endpoint(event_id)
        prob_data = get_json(self.page, prob_url).get("winProbability", {})
        return parse_match_stats(stats_data, prob_data)
