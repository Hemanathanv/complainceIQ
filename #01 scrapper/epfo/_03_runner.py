"""Run the EPFO home-page check and process circular rows page by page."""

from datetime import datetime
from math import ceil
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlparse

from openpyxl import Workbook
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from _01_epfo_main_page import epfo_home
from _02_circular import circular_page


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

download_path_setting = os.environ.get(
    "DOWNLOAD_PATH", os.environ.get("download_path", "EPFO_Downloads")
)
DOWNLOAD_PATH = Path(download_path_setting).expanduser()
if not DOWNLOAD_PATH.is_absolute():
    DOWNLOAD_PATH = SCRIPT_DIR / DOWNLOAD_PATH

RUN_LOG_PATH: Path | None = None


def log(message: str) -> None:
    """Print a message and append it to this run's dated log file."""
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    formatted_message = f"[{timestamp}] {message}"
    print(formatted_message)
    if RUN_LOG_PATH is not None:
        with RUN_LOG_PATH.open("a", encoding="utf-8") as output:
            output.write(formatted_message + "\n")


def safe_filename(value: str) -> str:
    """Return a filename-safe string while keeping readable Unicode text."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return value[:180] or "EPFO_Circular"


def filename_from_response(response, item: dict[str, str]) -> str:
    """Choose a downloaded filename from response headers or the URL."""
    disposition = response.headers.get("content-disposition", "")
    match = re.search(
        r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)",
        disposition,
        re.IGNORECASE,
    )
    if match:
        filename = unquote(match.group(1) or match.group(2)).strip()
    else:
        filename = Path(unquote(urlparse(item["url"]).path)).name

    if not filename:
        content_type = response.headers.get("content-type", "").lower()
        extension = ".pdf" if "pdf" in content_type else ".bin"
        filename = f"{item['title']}{extension}"

    return safe_filename(filename)


def unique_path(folder: Path, filename: str) -> Path:
    """Avoid overwriting a previously downloaded file."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    stem, suffix = path.stem, path.suffix
    counter = 1
    while path.exists():
        path = folder / f"{stem} ({counter}){suffix}"
        counter += 1
    return path


def download_row_file(page, link_locator, item: dict[str, str], output_folder: Path) -> tuple[str, str, str]:
    """Click a row's View PDF link, then save the linked file."""
    file_url = item.get("url", "").strip()
    parsed = urlparse(file_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.netloc.lower() == "false"
    ):
        return "INVALID: no valid file URL; moving to next row", "", ""

    popup = None
    try:
        try:
            with page.expect_popup(timeout=10000) as popup_info:
                link_locator.click(timeout=15000)
            popup = popup_info.value
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeoutError:
                pass
        except PlaywrightTimeoutError:
            # Some file links start a download without leaving a popup open.
            pass

        response = page.context.request.get(file_url, timeout=90000)
        if not response.ok:
            return f"FAILED: HTTP {response.status}", "", ""

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type or "application/xhtml" in content_type:
            return f"FAILED: link returned a web page ({content_type})", "", ""

        content = response.body()
        if not content:
            return "FAILED: empty file", "", ""

        filename = filename_from_response(response, item)
        output_path = unique_path(output_folder, filename)
        output_path.write_bytes(content)
        return f"DOWNLOADED: {output_path.name} (HTTP {response.status})", file_url, str(output_path.resolve())
    except Exception as error:
        return f"FAILED: {error}", "", ""
    finally:
        if popup is not None:
            try:
                popup.close()
            except Exception:
                pass


def parse_total(summary: str) -> int:
    """Read the total circular count from the results summary."""
    match = re.search(r"of\s+([\d,]+)\s+circulars", summary, re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not read total circular count from: {summary}")
    return int(match.group(1).replace(",", ""))


def save_excel_report(records: list[dict[str, str]], report_path: Path) -> None:
    """Write this run's circular rows to the requested Excel workbook."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "circular"
    sheet.append([
        "Row No",
        "Title",
        "Category",
        "Date",
        "Timestamp",
        "PDF File URL",
        "File Saved Path",
        "Download Status",
    ])

    for record in records:
        sheet.append([
            record.get("number", ""),
            record.get("title", ""),
            record.get("category", ""),
            record.get("date", ""),
            record.get("timestamp", ""),
            record.get("pdf_url", ""),
            record.get("saved_path", ""),
            record.get("status", ""),
        ])

    for column, width in {"A": 12, "B": 70, "C": 25, "D": 18, "E": 32, "F": 90, "G": 90, "H": 35}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = cell.font.copy(bold=True)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(report_path)


def main() -> None:
    global RUN_LOG_PATH

    run_date = datetime.now().astimezone()
    run_folder = DOWNLOAD_PATH / "Provident Fund Act 1952" / str(run_date.year) / run_date.strftime("%B") / f"{run_date:%B} {run_date.day}"
    run_folder.mkdir(parents=True, exist_ok=True)
    RUN_LOG_PATH = run_folder / f"{run_date:%B} {run_date.day}.log"
    report_path = DOWNLOAD_PATH / "Provident Fund Act 1952" / "EPFO_Circular_Report.xlsx"
    records: list[dict[str, str]] = []

    log(f"Download mode: {DOWNLOAD}")
    log(f"Run folder: {run_folder.resolve()}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        home = epfo_home(page)

        try:
            log("Checking whether the EPFO site is active...")
            if not home.check_status_200():
                log("EPFO site check failed: response status was not 200.")
                return

            log("EPFO site is active (HTTP 200).")
            log(home.total_visits.inner_text().strip())
            log(home.last_updated.inner_text().strip())

            log("Opening the Circulars page from Legal Framework...")
            home.hover_and_click_circulars()
            page.wait_for_load_state("domcontentloaded")

            circulars = circular_page(page)
            circulars.results_summary.wait_for(state="visible", timeout=60000)
            summary = circulars.results_summary.inner_text().strip()
            log(summary)
            total_circulars = parse_total(summary)

            first_page_rows = circulars.get_visible_rows()
            page_size = len(first_page_rows)
            if page_size == 0:
                log("No circular rows were found.")
                return

            total_pages = ceil(total_circulars / page_size)
            log(f"Processing {total_circulars} circulars across {total_pages} pages.")

            for page_number in range(1, total_pages + 1):
                if page_number > 1:
                    log(f"Entering page {page_number} in the page field...")
                    circulars.fill_page_number(page_number)
                    circulars.click_go()
                    page.wait_for_function(
                        "expected => { const active = document.querySelector('.page-btn.active'); "
                        "return active && active.getAttribute('data-page') === String(expected); }",
                        arg=page_number,
                        timeout=60000,
                    )

                    log(f"Page {page_number} loaded.")
                summary = circulars.results_summary.inner_text().strip()
                log(summary)
                rows = circulars.get_visible_rows()
                log(f"--- Page {page_number}/{total_pages}: {summary} ---")

                for row_index, item in enumerate(rows):
                    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                    details = [
                        f"Row {item.get('number', '')}",
                        item.get("title", ""),
                        item.get("category", ""),
                        item.get("date", ""),
                    ]
                    log(" | ".join(value for value in details if value))

                    status = "Not requested"
                    pdf_url = ""
                    saved_path = ""
                    if DOWNLOAD:
                        link_locator = circulars.row_file_links.nth(row_index)
                        log(f"Starting View PDF for row {item.get('number', '')}: {item.get('url', '')}")
                        status, pdf_url, saved_path = download_row_file(
                            page, link_locator, item, run_folder
                        )
                        if pdf_url:
                            log(f"PDF link: {pdf_url}")
                        log(f"Status: {status}")
                        time.sleep(0.3)

                    records.append({
                        "number": item.get("number", ""),
                        "title": item.get("title", ""),
                        "category": item.get("category", ""),
                        "date": item.get("date", ""),
                        "timestamp": timestamp,
                        "pdf_url": pdf_url,
                        "saved_path": saved_path,
                        "status": status,
                    })

            save_excel_report(records, report_path)
            log(f"Excel report saved: {report_path.resolve()}")
            log(f"Finished. Run log: {RUN_LOG_PATH.resolve()}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()


