"""Dismiss the Income Tax site's first-visit Guided Tour popup."""

import re

from playwright.sync_api import Page


class income_tax_popup:
    """Guided Tour popup locator and dismissal behavior."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.close_tour = page.locator(
            "div#tg-dialog-close-btn[role='button'][aria-label='Close Tour']"
        )

    def dismiss_guided_tour(self) -> bool:
        """Close the Guided Tour if it is visible; otherwise leave the page alone."""
        try:
            self.close_tour.wait_for(state="visible", timeout=1500)
            self.close_tour.click(timeout=2000, force=True)
            self.close_tour.wait_for(state="hidden", timeout=3000)
            return True
        except Exception:
            pass

        if self.page.evaluate("() => Boolean(window.__incomeTaxTourClosed)"):
            return True

        body_text = self.page.locator("body").inner_text()
        if not (re.search(r"\b1\s*of\s*\d+\b", body_text, re.I)
                and re.search(r"\bNext\b", body_text, re.I)):
            return False

        tour_region = self.page.locator("div").filter(
            has_text=re.compile(r"\b1\s*of\s*\d+\b", re.I)
        ).filter(has=self.page.get_by_role("button", name=re.compile(r"^Next$", re.I))).last
        close_selectors = (
            "button[aria-label*='close' i]",
            "button[title*='close' i]",
            "[role='button'][aria-label*='close' i]",
            "[role='button'][title*='close' i]",
            ".introjs-skipbutton",
            ".shepherd-cancel-icon",
            ".driver-popover-close-btn",
            "[class*='tour' i] button[class*='close' i]",
            "[class*='tour' i] [role='button'][aria-label*='close' i]",
        )
        for selector in close_selectors:
            candidates = tour_region.locator(selector)
            for candidate in candidates.all():
                try:
                    if candidate.is_visible():
                        candidate.click(timeout=2500, force=True)
                        self.page.wait_for_timeout(400)
                        remaining = self.page.locator("body").inner_text()
                        if not (re.search(r"\b1\s*of\s*\d+\b", remaining, re.I)
                                and re.search(r"\bNext\b", remaining, re.I)):
                            return True
                except Exception:
                    continue

        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(400)
        remaining = self.page.locator("body").inner_text()
        if not (re.search(r"\b1\s*of\s*\d+\b", remaining, re.I)
                and re.search(r"\bNext\b", remaining, re.I)):
            return True
        raise RuntimeError("The site's Guided Tour popup is active and could not be dismissed")
