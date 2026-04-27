"""
Sofascore service module - FIXED + Production Ready (Stealth + Anti-Detection)
"""

from __future__ import annotations
import os
import logging
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync  # ✅ FIXED

# -------------------------------
# OPTIONAL: Install browsers (safe guard)
# -------------------------------
def install_playwright_browsers():
    logger = logging.getLogger(__name__)
    try:
        logger.info("Checking Playwright browser installation...")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            logger.info("Playwright browsers verified/installed successfully")
            return True
        else:
            logger.error(f"Browser install failed: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Browser installation error: {e}")
        return False


# ⚠️ Disable in production if already installed
if os.getenv("ENABLE_BROWSER_INSTALL", "false").lower() == "true":
    install_playwright_browsers()


from ..utils import get_json, get_today
from .endpoints import SofascoreEndpoints
from .types import *

class SofascoreService:
    """
    SofaScore service with stealth + retry-safe Playwright
    """

    def __init__(self, browser_path: str = None, use_proxy: bool = False):
        self.logger = logging.getLogger(__name__)
        self.browser_path = browser_path
        self.use_proxy_enabled = use_proxy

        self.endpoints = SofascoreEndpoints()

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.__init_playwright()

    # -------------------------------
    # 🔥 PLAYWRIGHT INIT (FIXED)
    # -------------------------------
    def __init_playwright(self):
        max_retries = 3

        for attempt in range(max_retries):
            try:
                self.logger.info(f"Initializing Playwright (attempt {attempt+1})")

                # ✅ SAFE INIT
                self.playwright = sync_playwright().start()

                # -------------------------------
                # PROXY CONFIG
                # -------------------------------
                proxy_cfg = None
                proxy_server = os.getenv("PROXY_SERVER")
                proxy_user = os.getenv("PROXY_USER")
                proxy_pass = os.getenv("PROXY_PASS")

                if proxy_server:
                    proxy_cfg = {"server": proxy_server}
                    if proxy_user and proxy_pass:
                        proxy_cfg["username"] = proxy_user
                        proxy_cfg["password"] = proxy_pass

                # -------------------------------
                # BROWSER LAUNCH
                # -------------------------------
                launch_options = {
                    "headless": True,
                    "proxy": proxy_cfg,
                    "args": [
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--disable-web-security",
                    ],
                    "timeout": 60000,
                }

                if self.browser_path and os.path.exists(self.browser_path):
                    launch_options["executable_path"] = self.browser_path

                self.browser = self.playwright.chromium.launch(**launch_options)

                # -------------------------------
                # CONTEXT
                # -------------------------------
                self.context = self.browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                )

                self.page = self.context.new_page()
                self.page.set_default_timeout(45000)

                # -------------------------------
                # 🚀 PERFORMANCE BOOST
                # -------------------------------
                self.page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in ["image", "font"]
                    else route.continue_(),
                )

                # -------------------------------
                # 🕵️ STEALTH FIX
                # -------------------------------
                stealth_sync(self.page)  # ✅ FIXED

                # -------------------------------
                # SESSION PRIME
                # -------------------------------
                self.logger.info("Opening Sofascore...")
                self.page.goto(
                    "https://www.sofascore.com",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                time.sleep(2)

                self.logger.info("✅ Playwright initialized successfully")
                return

            except Exception as exc:
                self.logger.error(f"❌ Init failed: {str(exc)}")
                self.close()

                if attempt == max_retries - 1:
                    raise RuntimeError(
                        "Failed to initialize Playwright after retries"
                    ) from exc

    # -------------------------------
    # CLEANUP
    # -------------------------------
    def close(self):
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()

            self.page = None
            self.context = None
            self.browser = None
            self.playwright = None

            self.logger.info("Playwright resources closed")

        except Exception as exc:
            self.logger.error(f"Cleanup error: {str(exc)}")

    def __del__(self):
        self.close()

    # -------------------------------
    # API METHODS
    # -------------------------------
    def get_event(self, event_id: int):
        url = self.endpoints.event_endpoint(event_id)
        return parse_event(get_json(self.page, url)["event"])

    def get_events(self, date: str = "today"):
        if date == "today":
            date = get_today()
        url = self.endpoints.events_endpoint.format(date=date)
        return parse_events(get_json(self.page, url)["events"])

    def get_live_events(self):
        url = self.endpoints.live_events_endpoint
        return parse_events(get_json(self.page, url).get("events", []))

    def get_match_stats(self, event_id: int):
        stats = get_json(self.page, self.endpoints.match_stats_endpoint(event_id))
        prob = get_json(self.page, self.endpoints.match_probabilities_endpoint(event_id))
        return parse_match_stats(stats.get("statistics", {}), prob.get("winProbability", {}))
