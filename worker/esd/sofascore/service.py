# esd/sofascore/service.py

from __future__ import annotations
import os
import logging
import subprocess
import sys
import json
import time
import random
from playwright.sync_api import sync_playwright

from ..utils import get_today
from .endpoints import SofascoreEndpoints
from .types import parse_events

logger = logging.getLogger(__name__)

# --- INSTALL PLAYWRIGHT ---
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

# --- RANDOM USER AGENTS ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119 Safari/537.36",
]

# --- PROXY ---
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
            # random delay (VERY IMPORTANT)
            time.sleep(random.uniform(1.5, 3.5))

            page.goto(url, timeout=30000, wait_until="domcontentloaded")

            content = page.content()

            # 🚨 detect blocking
            if any(x in content for x in ["Access denied", "Forbidden", "cloudflare"]):
                raise Exception("Blocked by Sofascore")

            text = page.evaluate("() => document.body.innerText")

            data = json.loads(text)
            return data

        except Exception as e:
            logger.warning(f"Retry {attempt+1}/{retries}: {e}")
            time.sleep(2 ** attempt)

    logger.error(f"❌ Failed fetching: {url}")
    return None

# --- SERVICE ---
class SofascoreService:

    def __init__(self, *args, **kwargs):
        self.logger = logger
        self.endpoints = SofascoreEndpoints()
        self.playwright = None
        self.browser = None
        self.context = None
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
                        "--disable-blink-features=AutomationControlled"
                    ]
                }

                if proxy:
                    launch_options["proxy"] = proxy
                    self.logger.info(f"✅ Proxy enabled: {proxy['server']}")

                self.browser = self.playwright.chromium.launch(**launch_options)

                # ✅ REALISTIC CONTEXT
                self.context = self.browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": 1280, "height": 800},
                    locale="en-US"
                )

                # ✅ Anti-detection script
                self.context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)

                self.page = self.context.new_page()

                self.logger.info("✅ Browser ready")
                return

            except Exception as e:
                self.logger.error(f"Browser init failed: {e}")
                time.sleep(2)

        raise RuntimeError("❌ Browser init failed")

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
        except:
            pass

    # --- LIVE ---
    def get_live_events(self):
        try:
            url = self.endpoints.live_events_endpoint
            data = safe_fetch_json(self.page, url)

            if not data or "events" not in data:
                self.logger.error("❌ Blocked or invalid live events")
                return []

            return parse_events(data["events"])

        except Exception as e:
            self.logger.error(f"Live events error: {e}")
            return []

    # --- BY DATE ---
    def get_events(self, date="today"):
        if date == "today":
            date = get_today()

        try:
            url = self.endpoints.events_endpoint.format(date=date)
            data = safe_fetch_json(self.page, url)

            if not data or "events" not in data:
                self.logger.error("❌ Blocked or invalid events")
                return []

            return parse_events(data["events"])

        except Exception as e:
            self.logger.error(f"Events error: {e}")
            return []