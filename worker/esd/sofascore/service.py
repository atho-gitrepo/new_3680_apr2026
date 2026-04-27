"""
Sofascore service module - STABLE VERSION (Anti-Block + Version Safe)
"""

from __future__ import annotations
import os
import logging
import subprocess
import sys
import time
import random

from playwright.sync_api import sync_playwright

# ✅ VERSION-SAFE STEALTH IMPORT
try:
    from playwright_stealth import stealth_sync as apply_stealth
except ImportError:
    from playwright_stealth import stealth as apply_stealth

from ..utils import get_json, get_today
from .endpoints import SofascoreEndpoints
from .types import *


# -------------------------------
# OPTIONAL: Install browsers
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
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Browser install error: {e}")
        return False


# Disable in production unless needed
if os.getenv("ENABLE_BROWSER_INSTALL", "false").lower() == "true":
    install_playwright_browsers()


class SofascoreService:
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
    # 🔥 INIT PLAYWRIGHT (ANTI-BLOCK)
    # -------------------------------
    def __init_playwright(self):
        max_retries = 3

        for attempt in range(max_retries):
            try:
                self.logger.info(f"Initializing Playwright (attempt {attempt+1})")

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
                # LAUNCH BROWSER
                # -------------------------------
                self.browser = self.playwright.chromium.launch(
                    headless=True,
                    proxy=proxy_cfg,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                    timeout=60000,
                )

                # -------------------------------
                # CONTEXT (REALISTIC SETTINGS)
                # -------------------------------
                self.context = self.browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="Asia/Bangkok",
                )

                self.page = self.context.new_page()
                self.page.set_default_timeout(45000)

                # -------------------------------
                # HEADERS (ANTI-BOT)
                # -------------------------------
                self.page.set_extra_http_headers({
                    "accept-language": "en-US,en;q=0.9",
                    "referer": "https://www.google.com/",
                })

                # -------------------------------
                # PERFORMANCE BOOST
                # -------------------------------
                self.page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in ["image", "font"]
                    else route.continue_(),
                )

                # -------------------------------
                # STEALTH APPLY
                # -------------------------------
                apply_stealth(self.page)

                # -------------------------------
                # HUMAN-LIKE SESSION INIT
                # -------------------------------
                self.logger.info("Opening Sofascore homepage...")
                self.page.goto("https://www.sofascore.com", wait_until="domcontentloaded")

                time.sleep(random.uniform(2, 4))

                # simulate human movement
                self.page.mouse.move(100, 200)
                self.page.mouse.move(300, 400)

                self.logger.info("✅ Playwright initialized successfully")
                return

            except Exception as exc:
                self.logger.error(f"❌ Init failed: {str(exc)}")
                self.close()

                if attempt == max_retries - 1:
                    raise RuntimeError("Failed to initialize Playwright") from exc

    # -------------------------------
    # 🔥 SAFE REQUEST (ANTI-BLOCK)
    # -------------------------------
    def safe_get_json(self, url):
        for attempt in range(3):
            try:
                time.sleep(random.uniform(1, 2))  # slow down requests
                return get_json(self.page, url)

            except Exception as e:
                self.logger.warning(f"Retry {attempt+1} due to block: {e}")

                try:
                    self.page.goto("https://www.sofascore.com")
                    time.sleep(2)
                except:
                    pass

        raise Exception("Blocked by Sofascore")

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
            self.logger.error(f"Cleanup error: {exc}")

    def __del__(self):
        self.close()

    # -------------------------------
    # API METHODS (SAFE)
    # -------------------------------
    def get_event(self, event_id: int):
        url = self.endpoints.event_endpoint(event_id)
        return parse_event(self.safe_get_json(url)["event"])

    def get_events(self, date: str = "today"):
        if date == "today":
            date = get_today()
        url = self.endpoints.events_endpoint.format(date=date)
        return parse_events(self.safe_get_json(url)["events"])

    def get_live_events(self):
        url = self.endpoints.live_events_endpoint
        return parse_events(self.safe_get_json(url).get("events", []))

    def get_match_stats(self, event_id: int):
        stats = self.safe_get_json(self.endpoints.match_stats_endpoint(event_id))
        prob = self.safe_get_json(self.endpoints.match_probabilities_endpoint(event_id))

        return parse_match_stats(
            stats.get("statistics", {}),
            prob.get("winProbability", {})
        )