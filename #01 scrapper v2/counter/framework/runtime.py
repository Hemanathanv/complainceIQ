from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx
from curl_cffi import requests as curl_requests
from playwright.async_api import Browser, BrowserContext, Page, async_playwright


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class BrowserPage:
    page: Page
    context: BrowserContext


class BrowserRuntime:
    """Shared Playwright runtime with the site's one-time language handshake."""

    def __init__(self, headless: bool = True, ignore_https_errors: bool = False,
                 channel: str | None = None):
        self.headless = headless
        self.ignore_https_errors = ignore_https_errors
        self.channel = channel
        self._playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def __aenter__(self) -> "BrowserRuntime":
        self._playwright = await async_playwright().start()
        launch_options = {"headless": self.headless}
        if self.channel:
            launch_options["channel"] = self.channel
        self.browser = await self._playwright.chromium.launch(**launch_options)
        self.context = await self.browser.new_context(
            ignore_https_errors=self.ignore_https_errors
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self) -> Page:
        if not self.context:
            raise RuntimeError("BrowserRuntime must be used as an async context manager")
        return await self.context.new_page()

    async def choose_english(self, home_url: str) -> None:
        page = await self.new_page()
        try:
            await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
            button = page.locator("#btnEnglish")
            if await button.count() and await button.first.is_visible():
                await button.first.click()
                await page.wait_for_timeout(500)
        finally:
            await page.close()


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def fetch_html_httpx(url: str, timeout: float = 15.0) -> str:
    """Fetch a listing page without a browser as the first fallback."""
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout),
        limits=limits,
        http2=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_html_curl(url: str, timeout: float = 15.0) -> str:
    """Fetch a listing page with curl_cffi as the last transport fallback."""
    def request() -> str:
        response = curl_requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            impersonate="chrome",
        )
        response.raise_for_status()
        return response.text

    return await asyncio.to_thread(request)


async def fetch_bytes_httpx(url: str, timeout: float = 30.0) -> bytes:
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout),
        http2=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


async def fetch_bytes_curl(url: str, timeout: float = 30.0) -> bytes:
    def request() -> bytes:
        response = curl_requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            impersonate="chrome",
        )
        response.raise_for_status()
        return response.content

    return await asyncio.to_thread(request)


async def with_retries(
    operation: Callable[[], Awaitable[object]],
    attempts: int = 3,
    base_delay: float = 2.0,
) -> object:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as error:
            last_error = error
            if attempt == attempts:
                raise
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
    raise last_error  # pragma: no cover
