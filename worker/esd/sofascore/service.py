# esd/sofascore/service.py

"""
Sofascore service module with anti-detection patches
"""

from __future__ import annotations
import playwright
import os
import logging
import subprocess
import sys
import random
import time
from typing import Optional

def install_playwright_browsers():
    logger = logging.getLogger(__name__)
    try:
        logger.info("Checking Playwright browser installation...")
        result = subprocess.run([
            sys.executable, "-m", "playwright", "install", "chromium", "--force"
        ], capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            logger.info("Playwright browsers installed successfully")
            return True
        else:
            logger.error(f"Browser installation failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Browser installation error: {e}")
        return False

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

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)

class SofascoreService:
    def __init__(self, browser_path: str = None, use_proxy: str = None):
        self.logger = logging.getLogger(__name__)
        self.browser_path = browser_path
        self.use_proxy = use_proxy
        self.headless_mode = False
        self.endpoints = SofascoreEndpoints()
        self.playwright = None
        self.browser = None
        self.page = None
        self.__init_playwright()

    def _get_stealth_script(self) -> str:
        return """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : originalQuery(parameters)
            );
            Object.defineProperty(navigator, 'connection', { get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false }) });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
            Object.defineProperty(screen, 'availHeight', { get: () => 1080 });
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter(parameter);
            };
        """

    def __init_playwright(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.logger.info(f"Initializing Playwright (attempt {attempt + 1})")
                self.playwright = playwright.sync_api.sync_playwright().start()
                launch_options = {
                    'headless': self.headless_mode,
                    'args': [
                        '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                        '--disable-web-security', '--disable-features=VizDisplayCompositor',
                        '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
                        '--disable-renderer-backgrounding', '--disable-blink-features=AutomationControlled',
                        '--disable-features=IsolateOrigins,site-per-process', '--disable-site-isolation-trials',
                        '--disable-accelerated-2d-canvas', '--disable-component-extensions-with-background-pages',
                        '--disable-default-apps', '--disable-extensions', '--disable-sync', '--hide-scrollbars',
                        '--mute-audio', '--no-default-browser-check', '--no-first-run',
                        '--password-store=basic', '--use-mock-keychain'
                    ],
                    'timeout': 60000
                }
                if self.use_proxy:
                    launch_options['proxy'] = {'server': self.use_proxy}
                    self.logger.info(f"Using proxy: {self.use_proxy}")
                if self.browser_path and os.path.exists(self.browser_path):
                    launch_options['executable_path'] = self.browser_path
                    self.logger.info(f"Using browser at: {self.browser_path}")
                    self.browser = self.playwright.chromium.launch(**launch_options)
                else:
                    self.logger.info("Using Playwright's bundled Chromium")
                    self.browser = self.playwright.chromium.launch(**launch_options)
                context = self.browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=get_random_user_agent(),
                    locale='en-US',
                    timezone_id='America/New_York',
                    extra_http_headers={
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9', 'Accept-Encoding': 'gzip, deflate, br',
                        'DNT': '1', 'Connection': 'keep-alive', 'Upgrade-Insecure-Requests': '1',
                        'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'none',
                        'Sec-Fetch-User': '?1', 'Cache-Control': 'max-age=0',
                    }
                )
                self.page = context.new_page()
                self.page.add_init_script(self._get_stealth_script())
                self.page.set_default_timeout(60000)
                self.page.set_default_navigation_timeout(60000)
                self.logger.info("Navigating to Sofascore homepage...")
                self.page.goto('https://www.sofascore.com/', wait_until='networkidle')
                time.sleep(2)
                self.logger.info("Playwright initialized successfully with stealth patches")
                return
            except Exception as exc:
                self.logger.error(f"Playwright initialization failed (attempt {attempt + 1}): {str(exc)}")
                if "Executable doesn't exist" in str(exc) and attempt == 0:
                    install_playwright_browsers()
                    time.sleep(2)
                    continue
                if self.playwright:
                    self.playwright.stop()
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Failed to initialize browser after {max_retries} attempts: {str(exc)}") from exc

    def close(self):
        try:
            if self.page:
                self.page.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            self.logger.info("Playwright resources closed successfully")
        except Exception as exc:
            self.logger.error(f"Failed to close browser: {str(exc)}")

    def __del__(self):
        self.close()

    def get_live_events(self) -> list[Event]:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                url = self.endpoints.live_events_endpoint
                self.logger.info(f"Fetching live events from: {url}")
                if attempt > 0:
                    time.sleep(3 * attempt)
                result = get_json(self.page, url)
                if "events" not in result:
                    self.logger.warning(f"'events' key not found. Response keys: {result.keys() if result else 'None'}")
                    if attempt == max_retries - 1:
                        raise KeyError("'events'")
                    continue
                events = parse_events(result["events"])
                self.logger.info(f"Successfully fetched {len(events)} live events")
                return events
            except Exception as exc:
                self.logger.error(f"Failed to get live events (attempt {attempt + 1}): {str(exc)}")
                if attempt == max_retries - 1:
                    raise

    # All other methods (get_event, get_events, etc.) remain unchanged from your original file.
    # To keep this complete, I include a minimal stub – you must copy your existing implementations.
    # For brevity, I'm showing only the essential methods; replace with your full implementations.

    def get_event(self, event_id: int) -> Event:
        try:
            url = self.endpoints.event_endpoint(event_id)
            data = get_json(self.page, url)["event"]
            return parse_event(data)
        except Exception as exc:
            self.logger.error(f"Failed to get event {event_id}: {str(exc)}")
            raise

    def get_events(self, date: str = 'today') -> list[Event]:
        if date == 'today':
            date = get_today()
        try:
            url = self.endpoints.events_endpoint.format(date=date)
            return parse_events(get_json(self.page, url)["events"])
        except Exception as exc:
            self.logger.error(f"Failed to get events for date {date}: {str(exc)}")
            raise

    # IMPORTANT: Copy the rest of your original methods (get_player, get_team, etc.) here.
    # They are omitted for brevity but must be present in your actual file.