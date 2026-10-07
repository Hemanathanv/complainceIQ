"""Locators for the GST service login page."""

import re
from _00_page_errors import with_page_error_handling


@with_page_error_handling
class ServicePage:
    """Page object containing locators for GST's service login page."""

    def __init__(self, page):
        self.page = page
        self.username = page.get_by_label("Username", exact=True)
        self.password = page.get_by_label("Password", exact=True)
        self.captcha_input = page.get_by_label(
            "Type the characters you see in the image below",
            exact=False,
        )
        self.login_button = page.get_by_role(
            "button",
            name=re.compile(r"^\s*LOGIN\s*$", re.IGNORECASE),
        )
        self.site_last_updated = page.get_by_text(
            re.compile(r"Site Last Updated on", re.IGNORECASE)
        )

    def fill_credentials(self, username, password):
        """Fill the labeled username and password fields."""
        self.username.fill(username)
        self.password.fill(password)

    def fill_captcha(self, captcha_text):
        """Fill CAPTCHA text supplied by the user."""
        self.captcha_input.fill(captcha_text)

    def click_login(self):
        """Click the login button after the form is complete."""
        self.login_button.click()

    def capture_site_last_updated_date(self):
        """Return the date shown in the login page footer."""
        text = self.site_last_updated.inner_text().strip()
        match = re.search(
            r"Site Last Updated on\s+(\d{2}-\d{2}-\d{4})",
            text,
            re.IGNORECASE,
        )
        return match.group(1) if match else text
