# esd/sofascore/service.py

from __future__ import annotations
import os
import logging
import subprocess
import sys
import json
import time
from playwright.sync_api import sync_playwright

from ..utils import get_today
from .endpoints import SofascoreEndpoints
from .types import parse_events

logger = logging.getLogger(__name__)


# --- INSTALL PLAYWRIGHT BROWSERS ---
def install_playwright():
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info("✅ Playwright browser installed")
    except Exception as e:
        logger.warning(f"Playwright install warning: {e}")


install_playwright()


# --- PROXY CONFIG ---
def get_proxy():
    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    user = os.getenv("PROXY_USER")
    password = os.getenv("PROXY_PASS")

    if host and port:
        proxy = {"server": f"http://{host}:{port}"}
        if user and password:
            proxy["username"] = user
            proxy["password"] = password
        return proxy
    return None


# --- SAFE FETCH ---
def safe_fetch_json(page, url, retries=3):
    for attempt in range(retries):
        try:
            page.goto(url, timeout=30000)

            content = page.content()

            # 🚨 Detect blocking
            if "Access denied" in content or "Forbidden" in content:
                raise Exception("Blocked by Sofascore")

            text = page.evaluate("() => document.body.innerText")
            data = json.loads(text)

            return data

        except Exception as e:
            logger.warning(f"Retry {attempt+1}/{retries} failed: {e}")
            time.sleep(2)

    logger.error(f"❌ Failed fetching: {url}")
    return None


# --- SERVICE CLASS ---
class SofascoreService:

    # ✅ Accept any args (fixes your init error)
    def __init__(self, *args, **kwargs):
        self.logger = logger
        self.endpoints = SofascoreEndpoints()
        self.playwright = None
        self.browser = None
        self.page = None
        self._init_browser()

    def _init_browser(self):
        proxy = get_proxy()

        for attempt in range(3):
            try:
                self.logger.info(f"Starting browser (attempt {attempt+1})")

                self.playwright = sync_playwright().start()

                launch_options = {
                    "headless": True,
                    "args": [
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-web-security"
                    ]
                }

                # ✅ Apply proxy
                if proxy:
                    launch_options["proxy"] = proxy
                    self.logger.info(f"✅ Proxy enabled: {proxy['server']}")

                self.browser = self.playwright.chromium.launch(**launch_options)
                self.page = self.browser.new_page()

                # ✅ Anti-detection headers
                self.page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9"
                })

                self.logger.info("✅ Browser ready")
                return

            except Exception as e:
                self.logger.error(f"Browser init failed: {e}")
                time.sleep(2)

        raise RuntimeError("❌ Could not initialize browser")

    def close(self):
        try:
            if self.page:
                self.page.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except:
            pass

    # --- LIVE EVENTS ---
    def get_live_events(self):
        try:
            url = self.endpoints.live_events_endpoint
            data = safe_fetch_json(self.page, url)

            if not data or "events" not in data:
                self.logger.error("❌ Invalid or blocked live events response")
                return []

            return parse_events(data["events"])

        except Exception as e:
            self.logger.error(f"Live events error: {e}")
            return []

    # --- EVENTS BY DATE ---
    def get_events(self, date="today"):
        if date == "today":
            date = get_today()

        try:
            url = self.endpoints.events_endpoint.format(date=date)
            data = safe_fetch_json(self.page, url)

            if not data or "events" not in data:
                self.logger.error("❌ Invalid or blocked events response")
                return []

            return parse_events(data["events"])

        except Exception as e:
            self.logger.error(f"Events error: {e}")
            return []