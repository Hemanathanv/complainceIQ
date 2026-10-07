"""Page object for the GST footer's site-update date."""

import re
from _00_page_errors import with_page_error_handling


@with_page_error_handling
class SiteFooterPage:
    """Locator and capture action for the site's last-updated date."""

    def __init__(self, page):
        self.page = page
        self.site_last_updated = page.get_by_text(
            re.compile(r"Site Last Updated on", re.IGNORECASE)
        )

    def capture_site_last_updated_date(self):
        """Return the date shown in the site footer, if that locator exists."""
        try:
            text = self.site_last_updated.inner_text(timeout=3000).strip()
        except Exception as error:
            print(
                "OPTIONAL PAGE INFO: SiteFooterPage.capture_site_last_updated_date "
                f"could not read locator site_last_updated: {str(error).splitlines()[0]}"
            )
            return "Site update date was not visible on this page."
        match = re.search(
            r"Site Last Updated on\s+(\d{2}-\d{2}-\d{4})",
            text,
            re.IGNORECASE,
        )
        return match.group(1) if match else text
