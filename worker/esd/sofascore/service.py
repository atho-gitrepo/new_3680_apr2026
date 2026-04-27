"""
Sofascore service module - FINAL STABLE VERSION
"""

from __future__ import annotations
import os
import logging
import time
import random

from playwright.sync_api import sync_playwright

# ✅ SAFE STEALTH IMPORT (handles all versions)
try:
    from playwright_stealth.stealth import stealth_sync
    STEALTH_MODE = "sync"
except ImportError:
    from playwright_stealth import stealth
    STEALTH_MODE = "legacy"

from ..utils import get_json, get_today
from .endpoints import SofascoreEndpoints
from .types import *


class SofascoreService:
    def __init__(self, browser_path: str = None, use_proxy: str = None, headless: bool = True):
        # ✅ Always define logger first
        self.logger = logging.getLogger(__name__)

        self.browser_path = browser_path
        self.use_proxy = use_proxy
        self.headless = headless

        self.endpoints = SofascoreEndpoints()

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        # Init browser
        self.__init_playwright()

    # -------------------------------
    # INIT PLAYWRIGHT
    # -------------------------------
    def __init_playwright(self):
        max_retries = 3

        for attempt in range(max_retries):
            try:
                self.logger.info(f"Initializing Playwright (attempt {attempt+1})")

                self.playwright = sync_playwright().start()

                proxy_cfg = None
                if self.use_proxy:
                    proxy_cfg = {"server": self.use_proxy}

                self.browser = self.playwright.chromium.launch(
                    headless=self.headless,
                    proxy=proxy_cfg,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )

                self.context = self.browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="Asia/Bangkok",
                )

                self.page = self.context.new_page()
                self.page.set_default_timeout(45000)

                # Headers
                self.page.set_extra_http_headers({
                    "accept-language": "en-US,en;q=0.9",
                    "referer": "https://www.google.com/",
                })

                # Block heavy resources
                self.page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in ["image", "font"]
                    else route.continue_(),
                )

                # ✅ APPLY STEALTH SAFELY
                if STEALTH_MODE == "sync":
                    stealth_sync(self.page)
                else:
                    stealth(self.page)

                # Warm session
                self.page.goto("https://www.sofascore.com", wait_until="domcontentloaded")

                # Human-like behavior
                time.sleep(random.uniform(2, 4))
                self.page.mouse.move(100, 200)
                self.page.mouse.move(300, 400)

                self.logger.info("✅ Playwright initialized successfully")
                return

            except Exception as e:
                self.logger.error(f"Init failed: {e}")
                self.close()

                if attempt == max_retries - 1:
                    raise RuntimeError("Playwright init failed")

    # -------------------------------
    # SAFE REQUEST (ANTI-BLOCK)
    # -------------------------------
    def safe_get_json(self, url):
        for attempt in range(3):
            try:
                time.sleep(random.uniform(1, 2))
                return get_json(self.page, url)
            except Exception as e:
                self.logger.warning(f"Retry {attempt+1}: {e}")

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
        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.error(f"Cleanup error: {e}")

        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    def __del__(self):
        try:
            self.close()
        except:
            pass

    # -------------------------------
    # API METHODS
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