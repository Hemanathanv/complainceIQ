"""Page object for the EPFO home page."""

import re

from playwright.sync_api import Page


class epfo_home:
    """Home page locators and actions for epfo.gov.in."""

    url = "https://www.epfo.gov.in/"

    def __init__(self, page: Page) -> None:
        self.page = page

        self.legal_framework = page.get_by_text("Legal Framework", exact=True)

        self.circulars = page.get_by_role("link", name="Circulars", exact=True)

        # Footer information locators.
        self.total_visits = page.get_by_text(
            re.compile(r"Total Visits\s*:", re.IGNORECASE)
        ).first

        self.last_updated = page.get_by_text(
            re.compile(r"Last Updated\s*:", re.IGNORECASE)
        ).first
        
    def check_status_200(self) -> bool:
        """Open the home page and return whether its response status is 200."""
        response = self.page.goto(self.url, wait_until="domcontentloaded")
        return response is not None and response.status == 200

    def display_total_visits(self) -> str:
        """Print and return the total visits text shown on the page."""
        value = self.total_visits.inner_text().strip()
        print(value)
        return value

    def display_last_updated(self) -> str:
        """Print and return the last-updated text shown on the page."""
        value = self.last_updated.inner_text().strip()
        print(value)
        return value

    def hover_legal_framework(self) -> None:
        """Move the mouse pointer over the Legal Framework text."""
        self.legal_framework.hover()

    def hover_and_click_circulars(self) -> None:
        """Reveal Legal Framework, then hover over and click Circulars."""
        self.legal_framework.hover()
        self.circulars.hover()
        self.circulars.click()
