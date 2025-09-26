# app/browser/controller.py
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, Page
from app.config.settings import HEADLESS, SLOW_MO
from app.utils.logger import logger

class BrowserController:
    def __init__(self, run_dir: str, user_agent: Optional[str] = None):
        self._pw = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.run_dir = run_dir
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO)
        context = self.browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1366, "height": 820},
            java_script_enabled=True,
        )
        self.page = context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def goto(self, url: str, wait: str = "domcontentloaded", timeout: int = 60000):
        assert self.page
        logger.debug(f"Goto: {url}")
        self.page.goto(url, wait_until=wait, timeout=timeout)

    def click(self, selector: str, timeout: int = 10000):
        assert self.page
        self.page.wait_for_selector(selector, timeout=timeout)
        self.page.click(selector)

    def fill(self, selector: str, value: str, timeout: int = 10000):
        assert self.page
        self.page.wait_for_selector(selector, timeout=timeout)
        self.page.fill(selector, value)

    def type_and_submit(self, selector: str, text: str):
        assert self.page
        self.fill(selector, text)
        self.page.keyboard.press("Enter")

    def screenshot(self, filename: str) -> str:
        assert self.page
        import os
        path = os.path.join(self.run_dir, filename)
        self.page.screenshot(path=path, full_page=True)
        return path
