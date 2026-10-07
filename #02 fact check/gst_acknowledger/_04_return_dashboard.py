"""Page object for GST's return dashboard filters."""

import re
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from _00_page_errors import with_page_error_handling


@with_page_error_handling
class ReturnDashboardPage:
    """Locators and actions for the financial year, quarter, and period filters."""

    UI_TIMEOUT_MS = 60000

    def __init__(self, page):
        self.page = page
        self.financial_year = page.locator('select[name="fin"]')
        self.quarter = page.locator('select[name="quarter"]')
        self.period = page.locator('select[name="mon"]')
        self.gstin = page.get_by_text(
            re.compile(r"\b\d{2}[A-Z0-9]{13}\b", re.IGNORECASE)
        ).first
        self.search_button = page.get_by_role(
            "button", name=re.compile(r"^\s*SEARCH\s*$", re.IGNORECASE)
        )
        self.search_results = page.locator("div.col-sm-4.col-md-4")
        self.gstr1_result = self.search_results.filter(
            has_text=re.compile(r"\bGSTR-?1\b", re.IGNORECASE)
        )
        self.gstr1a_result = self.search_results.filter(
            has_text=re.compile(r"\bGSTR-?1A\b", re.IGNORECASE)
        )
        self.gstr2b_result = self.search_results.filter(
            has_text=re.compile(r"\bGSTR-?2B\b", re.IGNORECASE)
        )
        self.gstr3b_result = self.search_results.filter(
            has_text=re.compile(r"\bGSTR-?3B\b", re.IGNORECASE)
        )
        self.gstr2a_result = self.search_results.filter(
            has_text=re.compile(r"\bGSTR-?2A\b", re.IGNORECASE)
        )
        self.gstr6_result = self.search_results.filter(
            has_text=re.compile(r"\bGSTR-?6\b", re.IGNORECASE)
        )
        self.gstr6a_result = self.search_results.filter(
            has_text=re.compile(r"\bGSTR-?6A\b", re.IGNORECASE)
        )
        self.gstr1_card = self.gstr1_result
        self.gstr1a_card = self.gstr1a_result
        self.gstr2b_card = self.gstr2b_result
        self.gstr3b_card = self.gstr3b_result
        self.gstr2a_card = self.gstr2a_result
        self.gstr6_card = self.gstr6_result
        self.gstr6a_card = self.gstr6a_result

        self.gstr1_due_date = self.gstr1_card.get_by_text(
            re.compile(r"Due Date", re.IGNORECASE)
        )
        self.gstr1_prepare_online_button = self.gstr1_card.get_by_role(
            "button", name=re.compile(r"PREPARE ONLINE", re.IGNORECASE)
        )
        self.gstr1_prepare_offline_button = self.gstr1_card.get_by_role(
            "button", name=re.compile(r"PREPARE OFFLINE", re.IGNORECASE)
        )
        self.gstr1a_prepare_online_button = self.gstr1a_card.get_by_role(
            "button", name=re.compile(r"PREPARE ONLINE", re.IGNORECASE)
        )
        self.gstr2b_view_button = self.gstr2b_card.get_by_role(
            "button", name="VIEW", exact=False
        )
        self.gstr2b_download_button = self.gstr2b_card.get_by_role(
            "button", name="DOWNLOAD", exact=False
        )
        self.gstr2a_view_button = self.gstr2a_card.get_by_role(
            "button", name="VIEW", exact=False
        )
        self.gstr2a_download_button = self.gstr2a_card.get_by_role(
            "button", name="DOWNLOAD", exact=False
        )
        self.gstr6_due_date = self.gstr6_card.get_by_text(
            re.compile(r"Due Date", re.IGNORECASE)
        )
        self.gstr6_view_button = self.gstr6_card.get_by_role(
            "button", name=re.compile(r"^\s*VIEW\s+GSTR6\s*$", re.IGNORECASE)
        )
        self.gstr6_prepare_online_button = self.gstr6_card.get_by_role(
            "button", name=re.compile(r"PREPARE ONLINE", re.IGNORECASE)
        )
        self.gstr6_prepare_offline_button = self.gstr6_card.get_by_role(
            "button", name=re.compile(r"PREPARE OFFLINE", re.IGNORECASE)
        )
        self.gstr6a_view_button = self.gstr6a_card.get_by_role(
            "button", name="VIEW", exact=False
        )
        self.gstr6a_download_button = self.gstr6a_card.get_by_role(
            "button", name="DOWNLOAD", exact=False
        )
        self.gstr3b_view_button = self.gstr3b_card.get_by_role(
            "button", name=re.compile(r"VIEW\s+GSTR3B", re.IGNORECASE)
        )
        self.gstr3b_download_button = self.gstr3b_card.get_by_role(
            "button", name="DOWNLOAD", exact=False
        )

    @staticmethod
    def _option_labels(select):
        return [
            label.strip()
            for label in select.locator("option").all_text_contents()
            if label.strip()
        ]

    @staticmethod
    def _selected_label(select):
        label = select.locator("option:checked").text_content()
        return label.strip() if label else ""

    def capture_financial_year(self):
        """Return the currently selected financial year."""
        return self._selected_label(self.financial_year)

    def capture_quarter(self):
        """Return the currently selected quarter."""
        if not self.quarter.count():
            return "Not available"
        return self._selected_label(self.quarter)

    def capture_period(self):
        """Return the currently selected return period."""
        return self._selected_label(self.period)

    def capture_gstin(self):
        """Return the GSTIN displayed in the dashboard header."""
        text = self.gstin.inner_text().strip()
        match = re.search(r"\b\d{2}[A-Z0-9]{13}\b", text, re.IGNORECASE)
        return match.group(0).upper() if match else text

    def capture_filters(self):
        """Return the currently selected year, quarter, and period."""
        return {
            "financial_year": self.capture_financial_year(),
            "quarter": self.capture_quarter(),
            "period": self.capture_period(),
        }

    def capture_filter_options(self):
        """Return the live option labels for all three filters."""
        self.page.wait_for_function(
            """() => {
                const financialYear = document.querySelector('select[name="fin"]');
                return financialYear && Array.from(financialYear.options).some(
                    option => /\\b(?:19|20)\\d{2}\\b/.test(option.textContent || "")
                );
            }""",
            timeout=self.UI_TIMEOUT_MS,
        )
        return {
            "financial_year": self._option_labels(self.financial_year),
            "quarter": (
                self._option_labels(self.quarter) if self.quarter.count() else []
            ),
            "period": self._option_labels(self.period),
        }

    def _wait_for_selection(self, selector, label):
        self.page.wait_for_function(
            """({ selector, label }) => {
                const element = document.querySelector(selector);
                return element
                    && element.options[element.selectedIndex]
                    && element.options[element.selectedIndex].textContent.trim() === label;
            }""",
            arg={"selector": selector, "label": label},
            timeout=self.UI_TIMEOUT_MS,
        )

    def _wait_for_filter_update(self):
        """Give the portal's dependent dropdown request time to finish."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            # Some portal pages keep background requests open; option waits below
            # still determine whether the dependent filter actually populated.
            pass

    def select_financial_year(self, label):
        """Select a financial year and wait for the page to update."""
        self.financial_year.select_option(label=label)
        self._wait_for_selection('select[name="fin"]', label)
        self._wait_for_filter_update()
        self.page.wait_for_function(
            """() => {
                const quarter = document.querySelector('select[name="quarter"]');
                const period = document.querySelector('select[name="mon"]');
                const hasChoice = element => element && Array.from(element.options).some(
                    option => {
                        const text = (option.textContent || "").trim();
                        return text && !/^(select|choose|--)/i.test(text);
                    }
                );
                return hasChoice(quarter)
                    || (!hasChoice(quarter) && hasChoice(period));
            }""",
            timeout=self.UI_TIMEOUT_MS,
        )

    def select_quarter(self, label):
        """Select a quarter and wait for its dependent period options."""
        if not self.quarter.count():
            return True
        current_quarter = self.capture_quarter()
        previous_periods = self._option_labels(self.period)
        self.quarter.select_option(label=label)
        self._wait_for_selection('select[name="quarter"]', label)
        self._wait_for_filter_update()
        self.page.wait_for_function(
            """({ previousPeriods, sameQuarter }) => {
                const period = document.querySelector('select[name="mon"]');
                if (!period) return false;
                const labels = Array.from(period.options).map(
                    option => (option.textContent || "").trim()
                );
                const hasMonth = labels.some(
                    text => text && !/^(select|choose|--)/i.test(text)
                );
                return hasMonth
                    && (sameQuarter || JSON.stringify(labels)
                        !== JSON.stringify(previousPeriods));
            }""",
            arg={
                "previousPeriods": previous_periods,
                "sameQuarter": current_quarter == label,
            },
            timeout=self.UI_TIMEOUT_MS,
        )
        return True

    def select_period(self, label):
        """Select a return period and wait for the page to update."""
        self.period.select_option(label=label)
        self._wait_for_selection('select[name="mon"]', label)

    def select_filters(self, financial_year, quarter, period):
        """Apply year, quarter, and period selections in dependency order."""
        self.select_financial_year(financial_year)
        self.select_quarter(quarter)
        self.select_period(period)
        return self.capture_filters()

    def search(self):
        """Apply the selected return filters."""
        self.search_button.click()

    def capture_search_results(self):
        """Capture recognized return cards, their status, due date, and buttons."""
        self.search_results.first.wait_for(
            state="visible",
            timeout=self.UI_TIMEOUT_MS,
        )
        results = {}
        for label, locator in (
            ("GSTR-1", self.gstr1_card),
            ("GSTR-1A", self.gstr1a_card),
            ("GSTR-2B", self.gstr2b_card),
            ("GSTR-3B", self.gstr3b_card),
            ("GSTR-2A", self.gstr2a_card),
            ("GSTR-6", self.gstr6_card),
            ("GSTR-6A", self.gstr6a_card),
        ):
            visible_matches = [
                candidate
                for candidate in locator.all()
                if candidate.is_visible()
            ]
            if visible_matches:
                # The portal can render hidden responsive duplicates of a card.
                # Read one visible match so Playwright strict mode does not fail.
                locator = visible_matches[0]
                card = {
                    "text": locator.inner_text().strip(),
                    "buttons": [
                        {
                            "label": button.inner_text().strip(),
                            "enabled": button.is_enabled(),
                        }
                        for button in locator.locator("button").all()
                        if button.inner_text().strip()
                    ],
                    "disabled": locator.locator(
                        ".disableClick, .disableTile1A"
                    ).count() > 0,
                }
                status = locator.get_by_text(
                    re.compile(r"\bStatus\b", re.IGNORECASE)
                )
                if status.count():
                    card["status"] = status.first.inner_text().strip()
                due_date = locator.get_by_text(
                    re.compile(r"Due Date", re.IGNORECASE)
                )
                if due_date.count():
                    due_text = due_date.first.locator("xpath=..").inner_text().strip()
                    date_match = re.search(
                        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", due_text
                    )
                    card["due_date"] = (
                        date_match.group(0) if date_match else due_text
                    )
                results[label] = card
        return results
