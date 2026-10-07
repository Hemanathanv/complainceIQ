"""Locator for the PDF download on the GST GSTR-1 summary page."""

from _00_page_errors import with_page_error_handling


@with_page_error_handling
class GSTR1SummaryPage:
    """Page object containing the GSTR-1 summary PDF download locator."""

    def __init__(self, page):
        self.page = page
        self.download_pdf_button = page.get_by_role(
            "button",
            name="DOWNLOAD (PDF)",
            exact=True,
        )
