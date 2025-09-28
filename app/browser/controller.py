# app/browser/controller.py
from __future__ import annotations

import os, random
from typing import Optional
from playwright.sync_api import sync_playwright

class BrowserController:
    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        self._pw = None
        self.browser = None
        self.page = None

    def __enter__(self):
        os.makedirs(self.run_dir, exist_ok=True)
        self._pw = sync_playwright().start()
        headless = (os.getenv("HEADLESS", "true").lower() == "true")

        self.browser = self._pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        # Randomize viewport a bit to avoid obvious bot fingerprint
        vw = random.randint(1280, 1440)
        vh = random.randint(820, 920)

        context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": vw, "height": vh},
            extra_http_headers={
                "Accept-Language": "en-IN,en;q=0.9",
                "Sec-CH-UA": '"Chromium";v="124", "Not:A-Brand";v="99"',
                "Sec-CH-UA-Platform": '"Windows"',
            },
        )

        self.page = context.new_page()

        # Hint Amazon about currency/locale via cookies (best-effort)
        try:
            self.page.context.add_cookies([
                {"name": "lc-main", "value": "en_IN", "domain": ".amazon.in", "path": "/"},
                {"name": "i18n-prefs", "value": "INR", "domain": ".amazon.in", "path": "/"},
            ])
        except Exception:
            pass

        try:
            self.page.set_default_timeout(10_000)
            self.page.set_default_navigation_timeout(20_000)
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.page:
                self.page.context.close()
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def goto(self, url: str, timeout: Optional[int] = 60_000, wait_until: str = "domcontentloaded"):
        if not self.page:
            raise RuntimeError("BrowserController: page not initialized")
        return self.page.goto(url, timeout=timeout, wait_until=wait_until)

    def screenshot(self, filename: str, full_page: bool = True) -> str:
        if not self.page:
            raise RuntimeError("BrowserController: page not initialized")
        path = os.path.join(self.run_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.page.screenshot(path=path, full_page=full_page)
        return path

    def save_html(self, filename: str) -> str:
        if not self.page:
            raise RuntimeError("BrowserController: page not initialized")
        path = os.path.join(self.run_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.page.content())
        return path
