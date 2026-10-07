"""Page object for a filed GSTR-6 return."""

from _00_page_errors import with_page_error_handling


@with_page_error_handling
class GSTR6Page:
    """Locators and actions for viewing and downloading a filed GSTR-6."""

    def __init__(self, page):
        self.page = page
        self.download_filed_pdf_button = page.get_by_role(
            "button", name="DOWNLOAD FILED GSTR-6 (PDF)", exact=True
        )
        self.back_to_returns_link = page.get_by_role(
            "link", name="BACK", exact=True
        )

    def download_filed_pdf(self):
        """Click the filed-return PDF download control."""
        self.download_filed_pdf_button.click()

    def back_to_returns(self):
        """Return to the returns dashboard."""
        self.back_to_returns_link.click()
