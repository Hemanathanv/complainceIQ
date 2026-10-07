"""Page object for the CBIC GST homepage."""

from playwright.sync_api import Page


class gst_homepage:
    """URL, locators, and actions for the CBIC Tax Information homepage."""

    url = "https://taxinformation.cbic.gov.in/"

    def __init__(self, page: Page) -> None:
        self.page = page
        self.visitor_count_digits = page.locator(".footer .visiter + div .number")
        self.gst_navigation = page.locator("#navGST").first
        self.gst_menu = self.gst_navigation.locator("xpath=..").locator(".dropdown-menu")
        self.notifications_menu_items = page.get_by_text("Notifications", exact=True)
        self.circulars_menu_items = page.get_by_text("Circulars", exact=True)

    def check_status_200(self) -> bool:
        """Open the homepage and return whether its HTTP response is 200."""
        response = self.page.goto(
            self.url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        return response is not None and response.status == 200

    def display_visitor_count(self, emit=print) -> str:
        """Print and return the visitor count assembled from its digit elements."""
        digits = self.visitor_count_digits.all()
        visitor_count = "".join(
            digit.get_attribute("data-number") or ""
            for digit in digits
        )
        emit(f"Visitor's Count: {visitor_count}")
        return visitor_count

    def click_gst(self) -> None:
        """Click the GST navigation item in the CBIC header."""
        self.gst_navigation.click()

    def _ensure_gst_menu_open(self) -> None:
        """Open the GST navigation dropdown if it is currently closed."""
        menu_is_visible = any(item.is_visible() for item in self.notifications_menu_items.all())
        if not menu_is_visible:
            self.gst_navigation.click(force=True)
            self.page.wait_for_timeout(700)

    def _click_visible_menu_item(self, menu_items, label: str) -> None:
        """Click the visible matching menu item, skipping hidden duplicate text."""
        for _ in range(20):
            for item in menu_items.all():
                if item.is_visible():
                    item.click(force=True, timeout=15000)
                    return
            self.page.wait_for_timeout(500)
        raise TimeoutError(f"GST menu item '{label}' did not become visible")

    def click_notifications(self) -> None:
        """Open Notifications from the GST navigation dropdown."""
        self._ensure_gst_menu_open()
        self._click_visible_menu_item(self.notifications_menu_items, "Notifications")

    def click_circulars(self) -> None:
        """Open Circulars from the GST navigation dropdown."""
        self._ensure_gst_menu_open()
        self._click_visible_menu_item(self.circulars_menu_items, "Circulars")
