"""Locators and actions for logging out of the GST portal."""

from _00_page_errors import with_page_error_handling


@with_page_error_handling
class LogoutPage:
    """Page object for the account dropdown and Logout link."""

    def __init__(self, page):
        self.page = page
        self.account_dropdown = page.locator("a.lang-dpwn")
        # GST includes an icon glyph in the accessible name and renders a hidden
        # responsive duplicate. Target the single visible logout anchor.
        self.logout_link = page.locator(
            'a[href*="/services/logout"]:visible'
        )

    def open_account_dropdown(self):
        """Open the account dropdown and wait for its Logout item to appear."""
        if self.logout_link.count():
            return
        self.account_dropdown.click()
        self.logout_link.wait_for(state="visible")

    def click_logout(self):
        """Click Logout from the open account menu."""
        self.logout_link.click()
