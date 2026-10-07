"""Page object for the GST post-login landing page."""

import re
from _00_page_errors import with_page_error_handling


@with_page_error_handling
class LandingPage:
    """Locators and read/navigation actions for the GST landing page."""

    def __init__(self, page):
        self.page = page

        self.last_login = page.locator("p").filter(
            has_text=re.compile(r"Last logged in on", re.IGNORECASE)
        )
        self.logged_in_ip = page.locator("p").filter(
            has_text=re.compile(r"Currently logged in from IP", re.IGNORECASE)
        )
        self.return_calendar = page.get_by_role("table").first
        self.gstin = page.get_by_text(
            re.compile(r"\b\d{2}[A-Z0-9]{13}\b", re.IGNORECASE)
        ).first

        self.return_dashboard_button = page.get_by_role(
            "button", name=re.compile(r"RETURN DASHBOARD", re.IGNORECASE)
        )
        self.create_challan_button = page.get_by_role(
            "button", name=re.compile(r"CREATE CHALLAN", re.IGNORECASE)
        )
        self.view_notices_and_orders_button = page.get_by_role(
            "button", name=re.compile(r"VIEW NOTICE\(S\) AND ORDER\(S\)", re.IGNORECASE)
        )
        self.annual_return_button = page.get_by_role(
            "button", name=re.compile(r"ANNUAL RETURN", re.IGNORECASE)
        )
        self.continue_to_dashboard_button = page.get_by_role(
            "button", name=re.compile(r"CONTINUE TO DASHBOARD", re.IGNORECASE)
        )

    def capture_last_login(self):
        """Return the visible last-login label and timestamp."""
        return self.last_login.inner_text().strip()

    def capture_ip(self):
        """Return the visible logged-in IP label and address."""
        return self.logged_in_ip.inner_text().strip()

    def capture_return_calendar(self):
        """Return the landing-page returns calendar when the portal shows it."""
        if not self.return_calendar.count():
            return ""
        try:
            return self.return_calendar.inner_text(timeout=3000).strip()
        except Exception:
            # The portal's welcome page can omit the calendar table entirely.
            # This is informational only; return periods are read later from
            # the Return Dashboard, so do not stop the workflow here.
            return ""

    def capture_gstin(self):
        """Return the GSTIN displayed on the landing page."""
        text = self.gstin.inner_text().strip()
        match = re.search(r"\b\d{2}[A-Z0-9]{13}\b", text, re.IGNORECASE)
        return match.group(0).upper() if match else text

    def identify_landing_actions(self):
        """Return locators for the landing page's main action buttons."""
        return {
            "return_dashboard": self.return_dashboard_button,
            "create_challan": self.create_challan_button,
            "view_notices_and_orders": self.view_notices_and_orders_button,
            "annual_return": self.annual_return_button,
            "continue_to_dashboard": self.continue_to_dashboard_button,
        }

    def continue_to_dashboard(self):
        """Click the landing page's Continue to Dashboard button."""
        self.continue_to_dashboard_button.click()

    def open_return_dashboard(self):
        """Open Return Dashboard."""
        self.return_dashboard_button.click()

    def open_create_challan(self):
        """Open Create Challan."""
        self.create_challan_button.click()

    def open_notices_and_orders(self):
        """Open View Notices and Orders."""
        self.view_notices_and_orders_button.click()

    def open_annual_return(self):
        """Open Annual Return."""
        self.annual_return_button.click()
