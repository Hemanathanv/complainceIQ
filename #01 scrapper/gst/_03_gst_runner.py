"""Run the CBIC GST notification listing and print all visible records."""

from __future__ import annotations

import re
import time
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from openpyxl import Workbook
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Page, sync_playwright

from _01_gst_homepage import gst_homepage
from _02_gst_main import gst_main

NOTIFICATIONS_URL = "https://taxinformation.cbic.gov.in/content-page/explore-notification"
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = next(parent / ".env" for parent in Path(__file__).resolve().parents if (parent / "docker-compose.yml").is_file())

if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

DOWNLOAD = os.environ.get("download", os.environ.get("DOWNLOAD", "False")).strip().lower() in {
    "true", "1", "yes", "on"
}
DOWNLOAD_PATH = Path(os.environ.get("DOWNLOAD_PATH", "../downloads")).expanduser()
if not DOWNLOAD_PATH.is_absolute():
    DOWNLOAD_PATH = (SCRIPT_DIR / DOWNLOAD_PATH).resolve()
RUN_LOG_PATH: Path | None = None


def log(message: str) -> None:
    """Print and append a timestamped line to this run's log."""
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    if RUN_LOG_PATH is not None:
        with RUN_LOG_PATH.open("a", encoding="utf-8") as output:
            output.write(line + "\n")


def download_english_document(page: Page, link, destination: Path) -> tuple[str, str, str]:
    """Open a row's English document, save it, and return status, URL, and path."""
    popup = None
    try:
        href = link.get_attribute("href") or ""
        if href.startswith(("http://", "https://")):
            url = href
        else:
            with page.expect_popup(timeout=15000) as popup_info:
                link.click(timeout=15000)
            popup = popup_info.value
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeoutError:
                pass
            url = popup.url

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "INVALID: no document URL", "", ""
        # CBIC serves PDFs with a NIC certificate chain that is not trusted by
        # every local Node/Playwright installation. Match the TLS behavior used
        # by 004 GST Act.py so valid document responses can still be saved.
        response = page.context.request.get(url, timeout=90000)
        if not response.ok:
            return f"FAILED: HTTP {response.status}", url, ""
        content = response.body()
        if not content:
            return "FAILED: empty file", url, ""

        disposition = response.headers.get("content-disposition", "")
        match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", disposition, re.I)
        filename = unquote(match.group(1) or match.group(2)).strip() if match else Path(unquote(parsed.path)).name
        if not filename or filename in {".", "/"}:
            filename = f"GST_Notification_{datetime.now():%Y%m%d_%H%M%S}.pdf"
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
        if not Path(filename).suffix:
            filename += ".pdf"
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / filename
        counter = 1
        while target.exists():
            target = destination / f"{Path(filename).stem} ({counter}){Path(filename).suffix}"
            counter += 1
        target.write_bytes(content)
        return f"DOWNLOADED: {target.name} (HTTP {response.status})", url, str(target.resolve())
    except Exception as error:
        return f"FAILED: {error}", "", ""
    finally:
        if popup is not None:
            try:
                popup.close()
            except Exception:
                pass


def save_excel_report(records: list[dict[str, str]], report_path: Path) -> None:
    """Save notification and circular records together in one workbook."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "GST Documents"
    columns = ["Circular/Notification", "Selected Category", "Year", "Number", "Date", "Subject", "PDF Link"]
    sheet.append(columns)
    for record in records:
        sheet.append([record.get(key, "") for key in (
            "document_type", "selected_category", "year", "number", "date", "subject", "pdf_url"
        )])
    for cell in sheet[1]:
        cell.font = cell.font.copy(bold=True)
    for column, width in {"A": 24, "B": 30, "C": 12, "D": 24, "E": 18, "F": 72, "G": 90}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(report_path)


def dropdown_options(select) -> list[tuple[str, str]]:
    """Return usable (value, label) options from a native select control."""
    options = []
    for option in select.locator("option").all():
        value = option.get_attribute("value") or option.inner_text().strip()
        label = option.inner_text().strip()
        if value and label and not re.search(r"select|choose", label, re.IGNORECASE):
            options.append((value, label))
    return options


def visible_locator(container, selector):
    """Return the visible control when the page contains duplicate controls."""
    for locator in container.locator(selector).all():
        if locator.is_visible():
            return locator
    return None


def wait_for_options(select, timeout_seconds: int = 25) -> list[tuple[str, str]]:
    """Poll a dropdown while the site asynchronously populates its options."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if select is not None and select.is_visible():
            options = dropdown_options(select)
            if options:
                return options
        time.sleep(0.5)
    return []


def load_category_options(page: Page, home: gst_homepage, section: str,
                          selector: str) -> tuple[object | None, list[tuple[str, str]]]:
    """Wait for category values, reopening the section if the portal is flaky."""
    section_tab = page.locator("#notifications-tab" if section == "notifications" else "#circulars-tab")
    pane = page.locator(f"#{section}")
    for attempt in range(1, 4):
        category_select = visible_locator(pane, selector)
        options = wait_for_options(category_select)
        if options:
            return category_select, options
        if attempt < 3:
            log(f"{section}: category options are empty; reopening section (attempt {attempt + 1}/3)...")
            home.check_status_200()
            # Reopen the shared GST explore page, which contains both tabs.
            home.click_notifications()
            section_tab.click()
            page.wait_for_timeout(1000)
    log(f"{section}: category options stayed empty after 3 attempts; skipping section.")
    return None, []


def wait_for_table(page: Page, section: str = "notifications", timeout_ms: int = 15000,
                   previous_rows: tuple[str, ...] | None = None) -> None:
    """Wait for a section's table to settle after a filter/page change."""
    deadline = time.monotonic() + timeout_ms / 1000
    previous = None
    steady = 0
    while time.monotonic() < deadline:
        state = (
            page.locator(f"#{section} table.table-hover tbody tr").all_inner_texts(),
            page.locator(f"#{section}").inner_text()[-250:],
        )
        if previous_rows is not None and tuple(state[0]) == previous_rows:
            previous, steady = state, 0
            page.wait_for_timeout(400)
            continue
        if state == previous:
            steady += 1
            if steady >= 2:
                return
        else:
            previous, steady = state, 0
        page.wait_for_timeout(400)


def process_current_selection(page: Page, listing: gst_main, section: str,
                              section_label: str, selected_category: str, year: str,
                              records: list[dict[str, str]], run_folder: Path) -> None:
    """Print all rows for one section/category/year, following its pagination."""
    rows = listing.notification_rows if section == "notifications" else listing.circular_rows
    if section == "notifications":
        listing.show_all_notifications()
        get_summary = listing.get_notification_summary
        click_next = listing.click_notification_next_page
    else:
        listing.show_all_circulars()
        get_summary = listing.get_circular_summary
        click_next = listing.click_circular_next_page
    wait_for_table(page, section)
    summary = get_summary()
    log(summary or "Showing count not found")

    seen_pages: set[tuple[str, ...]] = set()
    while True:
        wait_for_table(page, section)
        signature = tuple(rows.all_inner_texts())
        if not signature or signature in seen_pages:
            break
        seen_pages.add(signature)
        entries = listing._show_entries(rows, section_label, emit=log)
        for index, item in enumerate(entries):
            row = rows.nth(index)
            english_link = row.locator('a[aria-label="English"]').first
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            url = ""
            saved_path = ""
            status = "Not requested"
            if DOWNLOAD:
                if english_link.count():
                    log(f"Opening English document for row {item['number']}...")
                    status, url, saved_path = download_english_document(page, english_link, run_folder)
                    if url:
                        log(f"PDF file URL: {url}")
                else:
                    status = "INVALID: English document link not available"
                log(f"Status: {status}")
            records.append({
                "document_type": "Notification" if section == "notifications" else "Circular",
                "selected_category": selected_category,
                "year": year,
                "number": item.get("number", ""),
                "date": item.get("date", ""),
                "subject": item.get("subject", ""),
                "pdf_url": url,
            })

        if not click_next():
            break
        wait_for_table(page, section, previous_rows=signature)
        log(get_summary() or "")


def main() -> None:
    global RUN_LOG_PATH
    run_date = datetime.now().astimezone()
    root = DOWNLOAD_PATH / "GST"
    run_folder = root / str(run_date.year) / run_date.strftime("%B") / f"{run_date:%B} {run_date.day}"
    run_folder.mkdir(parents=True, exist_ok=True)
    RUN_LOG_PATH = run_folder / f"{run_date:%B} {run_date.day}.log"
    report_path = root / "GST_Circulars_and_Notifications.xlsx"
    records: list[dict[str, str]] = []
    log(f"Download mode: {DOWNLOAD}")
    log(f"Run folder: {run_folder.resolve()}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = browser.new_context(no_viewport=True, ignore_https_errors=True)
        page = context.new_page()
        home = gst_homepage(page)
        response_ok = home.check_status_200()
        if not response_ok:
            log("CBIC site check failed: response status was not 200.")
            browser.close()
            return

        log("CBIC site is active (HTTP 200).")
        home.display_visitor_count(emit=log)
        log("Opening GST Notifications from the CBIC homepage...")
        home.click_notifications()
        page.wait_for_load_state("domcontentloaded")
        listing = gst_main(page)
        sections = [
            ("notifications", "Notifications", listing.notifications_tab,
             listing.notifications_category, listing.notifications_year),
            ("circulars", "Circulars", listing.circulars_tab,
             listing.circulars_category, listing.circulars_year),
        ]
        current_year = str(datetime.now().year)
        log(f"Filtering each category to current year: {current_year}")
        for section, section_label, tab, category_select, year_select in sections:
            tab.click()
            category_selector = (
                "#inputGroupSelectCategoryForContentPage"
                if section == "notifications" else "#inputGroupSelectCategory"
            )
            category_select, categories = load_category_options(
                page, home, section, category_selector
            )
            log(f"{section_label} categories found: {len(categories)}")
            if not categories or category_select is None:
                continue
            for category_value, category_label in categories:
                category_select.select_option(category_value)
                wait_for_table(page, section)
                pane = page.locator(f"#{section}")
                year_selector = (
                    "#inputGroupSelectNotificationYearForContentPage"
                    if section == "notifications" else "#inputGroupSelectCircularYearForContentPage"
                )
                active_year_select = visible_locator(pane, year_selector)
                years = [
                    option for option in wait_for_options(active_year_select)
                    if option[1] == current_year
                ]
                if not years:
                    log(f"{section_label} | {category_label}: current year {current_year} is unavailable; skipping category.")
                    continue
                for year_value, year_label in years:
                    active_year_select.select_option(year_value)
                    wait_for_table(page, section)
                    log(f"{section_label} | Selected category: {category_label} | Year: {year_label}")
                    process_current_selection(
                        page, listing, section, section_label, category_label,
                        year_label, records, run_folder
                    )

        save_excel_report(records, report_path)
        log(f"Excel report saved: {report_path.resolve()}")
        log(f"Finished. Run log: {RUN_LOG_PATH.resolve()}")
        browser.close()


if __name__ == "__main__":
    main()
