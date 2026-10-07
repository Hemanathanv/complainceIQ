"""Locators and actions for GST offline-download pages."""

from _00_page_errors import with_page_error_handling


@with_page_error_handling
class OfflineDownloadPage:
    """Page object shared by the GSTR-2B and GSTR-2A offline-download pages."""

    def __init__(self, page):
        self.page = page
        self.excel_download_button = page.get_by_role(
            "button",
            name="GENERATE EXCEL FILE TO DOWNLOAD",
            exact=True,
        )
        self.back_button = page.get_by_role(
            "button",
            name="BACK",
            exact=True,
        )

    def click_excel_download(self):
        """Generate and download the Excel file for the current return page."""
        self.excel_download_button.click()

    def click_back(self):
        """Return from the offline-download page using its BACK button."""
        self.back_button.click()


@with_page_error_handling
class GSTR6AOfflinePage:
    """Locators and actions for GSTR-6A's offline upload/download page."""

    EXCEL_GENERATION_TIMEOUT_MS = 20 * 60 * 1000

    def __init__(self, page):
        self.page = page
        self.download_tab = page.get_by_role("link", name="Download", exact=True)
        self.excel_download_button = page.get_by_role(
            "button",
            name="GENERATE EXCEL FILE TO DOWNLOAD",
            exact=True,
        )
        self.back_button = page.get_by_role(
            "button",
            name="BACK",
            exact=True,
        )
        self.back_to_file_returns_button = page.get_by_role(
            "button",
            name="BACK TO FILE RETURNS",
            exact=True,
        )

    def open_download_tab(self):
        """Open the Download tab from GSTR-6A's offline-upload page."""
        self.download_tab.click()

    def click_excel_download(self):
        """Generate and download GSTR-6A as an Excel file."""
        self.excel_download_button.click()

    def click_back(self):
        """Return from the GSTR-6A download tab to its offline-upload page."""
        self.back_button.click()

    def back_to_file_returns(self):
        """Return from GSTR-6A offline upload/download to File Returns."""
        self.back_to_file_returns_button.click()
