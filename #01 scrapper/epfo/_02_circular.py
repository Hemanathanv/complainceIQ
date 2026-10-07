"""Page object for the EPFO Circulars page."""

import re

from playwright.sync_api import Page


class circular_page:
    """Locators and actions for the EPFO Circulars listing page."""

    url = "https://www.epfo.gov.in/circulars/"

    def __init__(self, page: Page) -> None:
        self.page = page
        self.page_number_input = page.locator("#gotoPageInput")
        self.go_button = page.get_by_role("button", name="Go", exact=True)
        self.rows = page.locator(".download-box:visible")
        self.row_numbers = page.locator(".download-box:visible .serial-num")
        self.row_titles = page.locator(".download-box:visible .download-title")
        self.row_categories = page.locator(".download-box:visible .meta-row .meta-item:first-child")
        self.published_dates = page.locator(".download-box:visible .meta-row .meta-item:last-child")
        self.row_file_links = page.locator(".download-box:visible a.view-pdf-btn")
        self.results_summary = page.get_by_text(
            re.compile(r"Showing\s+\d+\s*-\s*\d+\s+of\s+[\d,]+\s+circulars", re.IGNORECASE)
        )

    def fill_page_number(self, page_number: int) -> None:
        """Fill the pagination field with the requested page number."""
        self.page_number_input.fill(str(page_number))

    def click_go(self) -> None:
        """Click Go to navigate to the entered page number."""
        self.go_button.click()

    def display_results_summary(self) -> str:
        """Print and return the visible range and total circular count."""
        summary = self.results_summary.inner_text().strip()
        print(summary)
        return summary

    def display_visible_row_numbers(self) -> list[str]:
        """Print and return the row numbers visible on the current page."""
        row_numbers = [
            value.strip().rstrip(".")
            for value in self.row_numbers.all_text_contents()
        ]
        print(row_numbers)
        return row_numbers

    def display_row_titles(self) -> list[str]:
        """Print and return the titles of circular rows currently shown."""
        titles = [title.strip() for title in self.row_titles.all_text_contents()]
        for title in titles:
            print(title)
        return titles

    def display_row_categories(self) -> list[str]:
        """Print and return the categories shown for circular rows."""
        categories = [
            category.strip()
            for category in self.row_categories.all_text_contents()
        ]
        for category in categories:
            print(category)
        return categories

    def display_published_dates(self) -> list[str]:
        """Print and return the publication dates shown for circular rows."""
        dates = [
            date.strip()
            for date in self.published_dates.all_text_contents()
        ]
        for date in dates:
            print(date)
        return dates

    def get_visible_rows(self) -> list[dict[str, str]]:
        """Return the visible circular rows with their metadata and file URLs."""
        visible_rows = []
        for index in range(self.rows.count()):
            row = self.rows.nth(index)
            link = row.locator("a.view-pdf-btn")
            number = row.locator(".serial-num")
            title = row.locator(".download-title")
            category = row.locator(".meta-row .meta-item:first-child")
            published_date = row.locator(".meta-row .meta-item:last-child")
            visible_rows.append({
                "number": number.inner_text().strip().rstrip(".") if number.count() else "",
                "title": title.inner_text().strip() if title.count() else "",
                "category": category.inner_text().strip() if category.count() else "",
                "date": published_date.inner_text().strip() if published_date.count() else "",
                "url": link.get_attribute("href") or "" if link.count() else "",
            })
        return visible_rows

    def get_row_file_links(self) -> list[dict[str, str]]:
        """Return each circular row title and its linked file URL/target."""
        files = []
        for index in range(self.row_file_links.count()):
            link = self.row_file_links.nth(index)
            row = link.locator("xpath=..")
            files.append({
                "title": row.locator(".download-title").inner_text().strip(),
                "url": link.get_attribute("href") or "",
                "target": link.get_attribute("target") or "",
            })
        return files

