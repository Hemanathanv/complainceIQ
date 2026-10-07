"""Page object for the Income Tax Department Circulars listing."""

import re

from playwright.sync_api import Page


class circular_page:
    """Locators and actions for the Circulars listing page."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.last_updated = page.get_by_text(
            re.compile(r"Last\s+Updated\s*:", re.IGNORECASE)
        )
        self.items_summary = page.get_by_text(
            re.compile(
                r"[\d,]+\s*-\s*[\d,]+\s+of\s+[\d,]+\s+items?",
                re.IGNORECASE,
            )
        )
        self.page_number_input = page.locator("#pagination-input-box")

    def print_last_updated_date(self) -> str:
        """Print and return the date shown after the listing's Last Updated label."""
        text = self.last_updated.first.inner_text().replace("\u00a0", " ").strip()
        match = re.search(r"Last\s+Updated\s*:\s*([^\r\n]+)", text, re.IGNORECASE)
        date_text = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
        print(f"Last Updated: {date_text}" if date_text else "Last Updated date not found.")
        return date_text

    def print_visible_and_total_count(self) -> tuple[int, int]:
        """Print and return the number of visible items and the total count."""
        self.items_summary.first.wait_for(state="visible", timeout=15000)
        summary = self.items_summary.first.inner_text().replace("\u00a0", " ")
        match = re.search(
            r"([\d,]+)\s*-\s*([\d,]+)\s+of\s+([\d,]+)\s+items?",
            summary,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"Could not parse Circulars count summary: {summary!r}")
        first, last, total = (
            int(value.replace(",", "")) for value in match.groups()
        )
        currently_shown = last - first + 1
        print(f"Currently shown: {currently_shown} | Total available: {total}")
        return currently_shown, total

    def read_page_position(self) -> tuple[int, int]:
        """Return the current page number and the total number of pages."""
        aria_label = self.page_number_input.get_attribute("aria-label") or ""
        match = re.search(
            r"Page(?:\s+number)?\s+([\d,]+)\s+of\s+([\d,]+)",
            aria_label,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"Could not read current page position: {aria_label!r}")
        return tuple(int(value.replace(",", "")) for value in match.groups())

    def print_current_page_number(self) -> int:
        """Print and return the current page number and total page count."""
        current_page, total_pages = self.read_page_position()
        print(f"Current page: {current_page} of {total_pages}")
        return current_page

    def enter_page_number(self, page_number: int) -> int:
        """Enter a page number from 1 through the listing's total page count."""
        if isinstance(page_number, bool) or not isinstance(page_number, int):
            raise TypeError("Page number must be an integer")
        current_page, total_pages = self.read_page_position()
        if not 1 <= page_number <= total_pages:
            raise ValueError(f"Page number must be from 1 to {total_pages}")
        if page_number == current_page:
            print(f"Already on page {page_number} of {total_pages}")
            return page_number

        self.page_number_input.fill(str(page_number))
        self.page_number_input.press("Enter")
        expected_first_item = (page_number - 1) * 10 + 1
        self.page.wait_for_function(
            "expected => { const input = document.querySelector('#pagination-input-box'); "
            "const text = document.body.innerText; "
            "const match = text.match(/Showing\\s+items\\s+([\\d,]+)\\s+to\\s+([\\d,]+)/i) "
            "|| text.match(/([\\d,]+)\\s*-\\s*([\\d,]+)\\s+of\\s+[\\d,]+\\s+items/i); "
            "return input && Number(input.value) === expected.page && match && "
            "Number(match[1].replace(/,/g, '')) === expected.first; }",
            arg={"page": page_number, "first": expected_first_item},
            timeout=30000,
        )
        print(f"Entered page {page_number} of {total_pages}")
        return page_number
