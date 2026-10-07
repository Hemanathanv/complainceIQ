"""Locators for the GST GSTR-1 details page."""

from _00_page_errors import with_page_error_handling


@with_page_error_handling
class GSTR1Page:
    """Page object containing the GSTR-1 Excel download and summary locators."""

    def __init__(self, page):
        self.page = page
        self.download_e_invoice_excel_button = page.get_by_role(
            "button",
            name="DOWNLOAD DETAILS FROM E-INVOICES (EXCEL)",
            exact=True,
        )
        self.view_summary_button = page.get_by_role(
            "button",
            name="VIEW SUMMARY",
            exact=True,
        )
