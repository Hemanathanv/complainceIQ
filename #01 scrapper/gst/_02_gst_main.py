"""Page object for CBIC GST notifications and circulars."""

import re
from playwright.sync_api import Page


class gst_main:
    """Locators and entry-display methods for GST document tables."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Section tabs.
        self.notifications_tab = page.locator("#notifications-tab")
        self.circulars_tab = page.locator("#circulars-tab")

        # Category and year selectors, scoped to their section panels.
        self.notifications_category = page.locator(
            "#notifications #inputGroupSelectCategoryForContentPage"
        )
        self.notifications_year = page.locator(
            "#notifications #inputGroupSelectNotificationYearForContentPage"
        )
        self.circulars_category = page.locator(
            "#circulars #inputGroupSelectCategory"
        )
        self.circulars_year = page.locator(
            "#circulars #inputGroupSelectCircularYearForContentPage"
        )

        # Table rows and their English/download links.
        self.notification_rows = page.locator(
            "#notifications table.table-hover tbody tr"
        )
        self.circular_rows = page.locator(
            "#circulars table.table-hover tbody tr"
        )
        self.notification_english_links = self.notification_rows.locator(
            'a[aria-label="English"]'
        )
        self.circular_english_links = self.circular_rows.locator(
            'a[aria-label="English"]'
        )
        self.notification_download_links = self.notification_rows.locator(
            'a[aria-label="download"]'
        )
        self.circular_download_links = self.circular_rows.locator(
            'a[aria-label="download"]'
        )

        # Notification table summary, page-size control, and pagination.
        self.notification_summary = page.locator("#notifications").get_by_text(
            re.compile(r"Showing\s+\d+\s*-\s*\d+\s+of\s+\d+\s+items?", re.IGNORECASE)
        )
        self.notification_page_size = page.locator(
            '#notifications select[name="example_length"]'
        )
        self.notification_next_page = page.locator(
            "#notifications ul.pagination a, #notifications ul.pagination button, "
            "#notifications .dataTables_paginate a, #notifications .dataTables_paginate button"
        ).filter(has_text=re.compile(r"^(?:»+|>|next|next page)$", re.IGNORECASE))
        self.circular_summary = page.locator("#circulars").get_by_text(
            re.compile(r"Showing\s+\d+\s*-\s*\d+\s+of\s+\d+\s+items?", re.IGNORECASE)
        )
        self.circular_page_size = page.locator('#circulars select[name="example_length"]')
        self.circular_next_page = page.locator(
            "#circulars ul.pagination a, #circulars ul.pagination button, "
            "#circulars .dataTables_paginate a, #circulars .dataTables_paginate button"
        ).filter(has_text=re.compile(r"^(?:»+|>|next|next page)$", re.IGNORECASE))

    def show_all_notifications(self) -> bool:
        """Set the notification table to its largest available page size."""
        if not self.notification_page_size.count():
            return False
        options = self.notification_page_size.locator("option").all()
        values = [option.get_attribute("value") or option.inner_text().strip() for option in options]
        if not values:
            return False
        largest = max(values, key=lambda value: int(value) if value.isdigit() else 0)
        self.notification_page_size.select_option(largest)
        return True

    def get_notification_summary(self) -> str:
        """Return the visible notification count summary, if present."""
        return self.notification_summary.first.inner_text().strip() if self.notification_summary.count() else ""

    def click_notification_next_page(self) -> bool:
        """Move to the next notification table page when the control is enabled."""
        for control in self.notification_next_page.all():
            parent = control.locator("..").get_attribute("class") or ""
            classes = control.get_attribute("class") or ""
            if control.is_visible() and not control.is_disabled() and "disabled" not in (parent + classes).lower():
                control.click()
                return True
        return False

    def show_all_circulars(self) -> bool:
        """Set the circular table to its largest available page size."""
        if not self.circular_page_size.count():
            return False
        options = self.circular_page_size.locator("option").all()
        values = [option.get_attribute("value") or option.inner_text().strip() for option in options]
        if not values:
            return False
        largest = max(values, key=lambda value: int(value) if value.isdigit() else 0)
        self.circular_page_size.select_option(largest)
        return True

    def get_circular_summary(self) -> str:
        """Return the visible circular count summary, if present."""
        return self.circular_summary.first.inner_text().strip() if self.circular_summary.count() else ""

    def click_circular_next_page(self) -> bool:
        """Move to the next circular table page when the control is enabled."""
        for control in self.circular_next_page.all():
            parent = control.locator("..").get_attribute("class") or ""
            classes = control.get_attribute("class") or ""
            if control.is_visible() and not control.is_disabled() and "disabled" not in (parent + classes).lower():
                control.click()
                return True
        return False

    def _show_entries(self, rows, section_name: str, emit=print) -> list[dict[str, str]]:
        """Print and return table entries with English and download link details."""
        entries = []
        for row in rows.all():
            cells = row.locator("td")
            if cells.count() < 3:
                continue

            english_link = row.locator('a[aria-label="English"]').first
            download_link = row.locator('a[aria-label="download"]').first
            entry = {
                "number": cells.nth(0).inner_text().strip(),
                "date": cells.nth(1).inner_text().strip() if cells.count() > 1 else "",
                "subject": cells.nth(2).inner_text().strip() if cells.count() > 2 else "",
                "english_link": english_link.get_attribute("href") or "" if english_link.count() else "",
                "download_link": download_link.get_attribute("href") or "" if download_link.count() else "",
            }
            link_details = []
            for label, locator in (("English", english_link), ("Download", download_link)):
                if locator.count():
                    href = locator.get_attribute("href") or locator.get_attribute("onclick") or "JS opens document"
                    link_details.append(f"{label}: {href}")
                else:
                    link_details.append(f"{label}: not available")
            entries.append(entry)
            emit(
                f"{entry['number']} | {entry['date']} | {entry['subject']} | "
                + " | ".join(link_details)
            )
        emit(f"{section_name}: {len(entries)} entries shown")
        return entries

    def show_notification_entries(self, emit=print) -> list[dict[str, str]]:
        """Print and return the visible GST notification entries."""
        return self._show_entries(self.notification_rows, "Notifications", emit)

    def show_circular_entries(self, emit=print) -> list[dict[str, str]]:
        """Print and return the visible GST circular entries."""
        return self._show_entries(self.circular_rows, "Circulars", emit)


