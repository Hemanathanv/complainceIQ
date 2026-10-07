"""Page object for the Income Tax Department homepage."""

import re

from playwright.sync_api import Page


class incometax_home_page:
    """Homepage URL, locators, and basic site checks."""

    url = "https://www.incometaxindia.gov.in/home"

    def __init__(self, page: Page) -> None:
        self.page = page
        self.last_reviewed = page.get_by_text(
            "Last reviewed and updated on:",
            exact=False,
        )
        self.body_text = page.locator("body")
        self.tax_laws_rules = page.get_by_text("Tax Laws & Rules", exact=True)
        self.circulars_notifications = page.get_by_text(
            "Circulars and Notifications",
            exact=True,
        )
        self.circulars = page.get_by_text("Circulars", exact=True)
        self.notifications = page.get_by_text("Notifications", exact=True)

    def print_last_reviewed_date(self) -> str:
        """Print and return the homepage's last-reviewed date text."""
        body_text = self.body_text.inner_text().replace("\u00a0", " ")
        match = re.search(
            r"Last\s+reviewed\s+and\s+updated\s+on\s*:\s*([^\r\n]+)",
            body_text,
            re.IGNORECASE,
        )
        date_text = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
        print(
            f"Last reviewed and updated on: {date_text}"
            if date_text
            else "Last reviewed date not found."
        )
        return date_text

    def check_status_200(self) -> bool:
        """Open the homepage and return whether its response status is 200."""
        response = self.page.goto(
            self.url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        return response is not None and response.status == 200

    def _visible_element(self, locator, label: str):
        """Find a visible matching element, skipping hidden duplicate text."""
        for _ in range(20):
            for element in locator.all():
                if element.is_visible():
                    return element
            self.page.wait_for_timeout(500)
        raise TimeoutError(f"Could not find visible '{label}' element")

    def hover_tax_laws_rules(self) -> None:
        """Hover over the Tax Laws & Rules navigation item."""
        self._visible_element(self.tax_laws_rules, "Tax Laws & Rules").hover(
            timeout=15000,
            force=True,
        )

    def click_circulars_notifications(self) -> None:
        """Click the Circulars and Notifications menu entry revealed on hover."""
        self._visible_element(
            self.circulars_notifications,
            "Circulars and Notifications",
        ).click(timeout=15000, force=True)

    def hover_circulars_notifications(self) -> None:
        """Hover over Circulars and Notifications to reveal its category links."""
        self._visible_element(
            self.circulars_notifications,
            "Circulars and Notifications",
        ).hover(timeout=15000, force=True)

    def click_circulars(self) -> None:
        """Click the visible Circulars option."""
        self._visible_element(self.circulars, "Circulars").click(
            timeout=15000,
            force=True,
        )

    def click_notifications(self) -> None:
        """Click the visible Notifications option."""
        self._visible_element(self.notifications, "Notifications").click(
            timeout=15000,
            force=True,
        )
