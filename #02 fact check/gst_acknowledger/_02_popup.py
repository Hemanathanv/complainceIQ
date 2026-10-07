"""Locators for the GST popup shown after login."""

import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from _00_page_errors import with_page_error_handling


@with_page_error_handling
class PopupPage:
    """Page object containing locators for the post-login metadata popup."""

    def __init__(self, page):
        self.page = page
        self.popup = page.locator("#caNumpopupV:visible, #adhrtableV:visible")
        # GST has rendered this action as both a link and a button across
        # credential-specific popup variants. Match the visible label instead
        # of assuming one role.
        self.dismiss_action = self.popup.get_by_text(
            re.compile(r"^\s*(?:NO-)?REMIND ME LATER\s*$", re.IGNORECASE),
            exact=True,
        )

    def handle_if_present(self, timeout_ms=20000):
        """Dismiss a post-login popup if it appears within the wait window.

        Return True when a popup was dismissed and False when this credential
        did not show one, allowing the runner to continue either way.
        """
        try:
            self.popup.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            return False

        self.dismiss_action.first.click(timeout=timeout_ms)
        self.popup.wait_for(state="hidden", timeout=timeout_ms)
        return True
