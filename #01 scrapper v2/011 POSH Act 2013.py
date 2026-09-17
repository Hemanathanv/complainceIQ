from pathlib import Path
import os
from urllib.parse import urljoin, urlparse, unquote, parse_qs
import base64
from datetime import datetime
from openpyxl.utils import get_column_letter
import re
import time

from playwright.sync_api import sync_playwright

try:
    from openpyxl import Workbook, load_workbook
except ImportError:
    print(
        "\nERROR: the 'openpyxl' package is required for the Excel "
        "download log used by this script.\n"
        "Install it with:\n\n    pip install openpyxl\n"
    )
    raise


START_URL = "https://wcd.gov.in/documents/legislations"
ORDERS_URL = "https://wcd.gov.in/documents/orders-and-notices"
ARCHIVE_ORDERS_URL = "https://wcd.gov.in/documents/orders-and-notices-archives"

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))
WCD = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR)) / Path(__file__).stem
NOTIFICATION = WCD / "notification"
ACT = WCD / "act"
RULES = WCD / "rules"
ORDERS = WCD / "orders_and_notices"
ARCHIVE_ORDERS = WCD / "archive_orders_and_notices"

for folder in (NOTIFICATION, ACT, RULES, ORDERS, ARCHIVE_ORDERS):
    folder.mkdir(parents=True, exist_ok=True)


# ============================================================
# EXCEL DOWNLOAD LOG — LIVE TRACKING
#
# Two sheets:
#   "Acts and Rules"  — every PDF from download_acts_rules(),
#                        with a Category column showing ACT or
#                        RULE for each row
#   "Notifications"    — every PDF from download_notifications()
#
# LIVE TRACKING: the workbook is opened once at the start of the
# run, kept open in memory, and saved to disk again immediately
# after every single PDF is processed (not just once at the end)
# — so if the script is interrupted partway through, or you open
# the file mid-run, everything processed so far is already in
# it. If Excel has the file open (locking it) when a live save
# is attempted, that one save is skipped with a warning — the
# change stays queued in memory and gets flushed to disk on the
# very next successful save.
#
# Each PDF is identified by (sheet, File Name) — the actual
# saved filename, which is stable across runs — so re-running the
# script never creates duplicate rows, and if a row's data changes
# (status flips, e.g. a previous failure now succeeds) the
# existing row is updated in place instead of duplicated.
# ============================================================

EXCEL_LOG_PATH = WCD / "WCD_Download_Log.xlsx"

ACTS_RULES_SHEET_NAME = "Acts and Rules"
NOTIFICATIONS_SHEET_NAME = "Notifications"
ORDERS_SHEET_NAME = "Orders and Notices"
ARCHIVE_ORDERS_SHEET_NAME = "Archived Orders"

STATUS_NEW = "New Download"
STATUS_OLD = "Old Download"
STATUS_FAILED = "Not Downloaded"

# Column layout is per-sheet: only Acts and Rules needs a
# Category column (to say whether a row is an ACT or a RULE) —
# Notifications has no such distinction.
SHEET_COLUMNS = {
    ACTS_RULES_SHEET_NAME: ["title", "file_name", "category", "timestamp", "path_code", "status"],
    NOTIFICATIONS_SHEET_NAME: ["title", "file_name", "timestamp", "path_code", "status"],
    ORDERS_SHEET_NAME: ["title", "file_name", "timestamp", "path_code", "status"],
    ARCHIVE_ORDERS_SHEET_NAME: ["title", "file_name", "timestamp", "path_code", "status"],
}

FIELD_KEY_TO_HEADER_LABEL = {
    "title": "Title",
    "file_name": "File Name",
    "category": "Category",
    "timestamp": "Timestamp",
    "path_code": "Path Code",
    "status": "Status",
}

FIELD_KEY_TO_COLUMN_WIDTH = {
    "title": 80,
    "file_name": 55,
    "category": 14,
    "timestamp": 22,
    "path_code": 110,
    "status": 18,
}

HEADER_LABEL_TO_FIELD_KEY = {
    "Title": "title",
    "File Name": "file_name",
    "Category": "category",
    "Timestamp": "timestamp",
    "Path Code": "path_code",
    "Status": "status",
}

# Set once in main() via init_excel_workbook() and reused for
# every live write for the rest of the run.
EXCEL_WORKBOOK = None

# (sheet_name, file_name) -> {"sheet", "row", "snapshot"}
# Covers everything already on disk when the run started PLUS
# everything written live during this run — checked before every
# live write so nothing is ever duplicated, and so any changed
# field (e.g. status) updates the existing row instead of adding
# a new one. Keyed by File Name — the PDF's own stable saved
# name — not Title, so it stays correct even if title text is
# re-scraped slightly differently on a later run.
LOGGED_ENTRIES = {}


def sheet_headers(sheet_name):
    return [FIELD_KEY_TO_HEADER_LABEL[key] for key in SHEET_COLUMNS[sheet_name]]


def style_sheet(sheet, sheet_name):

    columns = SHEET_COLUMNS[sheet_name]
    last_col_letter = get_column_letter(len(columns))

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{last_col_letter}1"

    for idx, key in enumerate(columns, start=1):
        col_letter = get_column_letter(idx)
        sheet.column_dimensions[col_letter].width = FIELD_KEY_TO_COLUMN_WIDTH.get(key, 20)


def migrate_sheet_headers_if_needed(sheet, sheet_name):
    """
    If an existing Excel log already has this sheet but it's
    missing a column that belongs there now, rebuild the sheet
    with the current column layout, preserving every existing
    value by field — and preserving row order/numbers, so any row
    references already loaded via load_logged_entries_from_excel()
    stay valid.
    """

    desired_columns = SHEET_COLUMNS[sheet_name]
    desired_headers = sheet_headers(sheet_name)

    current_headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]

    while current_headers and current_headers[-1] is None:
        current_headers.pop()

    if current_headers == desired_headers:
        return

    current_keys = [
        HEADER_LABEL_TO_FIELD_KEY.get((h or "").strip()) for h in current_headers
    ]

    missing_keys = [key for key in desired_columns if key not in current_keys]

    if not missing_keys:
        return

    print(
        f"Updating '{sheet_name}' sheet layout — adding missing column(s): "
        f"{', '.join(FIELD_KEY_TO_HEADER_LABEL[key] for key in missing_keys)}"
    )

    rows_data = []

    for row in sheet.iter_rows(min_row=2, values_only=True):
        row_dict = {}
        for idx, key in enumerate(current_keys):
            if key and idx < len(row):
                row_dict[key] = row[idx]
        rows_data.append(row_dict)

    sheet.delete_rows(1, sheet.max_row)

    sheet.append(desired_headers)

    for row_dict in rows_data:
        sheet.append([row_dict.get(key, "") for key in desired_columns])

    style_sheet(sheet, sheet_name)


def load_logged_entries_from_excel():
    """
    Reads whatever's already in the two sheets (if the Excel log
    already exists) and returns a dict of
    (sheet, File Name) -> {"sheet", "row", "snapshot"}
    for every row already recorded — read-only, used once at the
    start of a run so already-on-disk PDFs from a previous run
    aren't re-logged, and so a row whose data later changes gets
    UPDATED in place instead of duplicated.
    """

    logged_entries = {}

    if not EXCEL_LOG_PATH.exists():
        return logged_entries

    try:

        workbook = load_workbook(EXCEL_LOG_PATH, read_only=True)

        for sheet_name in (ACTS_RULES_SHEET_NAME, NOTIFICATIONS_SHEET_NAME):

            if sheet_name not in workbook.sheetnames:
                continue

            sheet = workbook[sheet_name]
            rows = sheet.iter_rows(values_only=True)
            header_row = next(rows, None)

            col_idx = {}

            if header_row:
                for idx, value in enumerate(header_row):
                    key = HEADER_LABEL_TO_FIELD_KEY.get((value or "").strip())
                    if key:
                        col_idx[key] = idx

            file_name_col_idx = col_idx.get("file_name")
            title_col_idx = col_idx.get("title")

            if file_name_col_idx is None and title_col_idx is None:
                continue

            comparable_keys = [
                key for key in SHEET_COLUMNS[sheet_name] if key != "timestamp"
            ]

            row_num = 1  # header occupies row 1; data starts at row 2

            for row in rows:

                row_num += 1

                def cell_value(field_key, _row=row, _col_idx=col_idx):
                    idx = _col_idx.get(field_key)
                    if idx is not None and idx < len(_row) and _row[idx] is not None:
                        return str(_row[idx]).strip()
                    return ""

                file_name_value = cell_value("file_name")
                title_value = cell_value("title")
                category_value = cell_value("category")

                key_identifier = file_name_value if file_name_value else title_value

                if category_value:
                    key_identifier = f"{category_value}::{key_identifier}"

                if not key_identifier:
                    continue

                snapshot = {key: cell_value(key) for key in comparable_keys}

                logged_entries[(sheet_name, key_identifier)] = {
                    "sheet": sheet_name,
                    "row": row_num,
                    "snapshot": snapshot,
                }

        workbook.close()

    except Exception as e:
        print(f"Could not read existing Excel log for backfill check: {e}")

    return logged_entries


def init_excel_workbook():
    """
    Opens (or creates) the Excel log once at the start of the
    run and makes sure both required sheets exist. Kept open in
    EXCEL_WORKBOOK for the rest of the run so live_log_entry()
    can append/update + save after every download without
    re-reading the whole file each time.
    """

    global EXCEL_WORKBOOK

    if EXCEL_LOG_PATH.exists():
        try:
            EXCEL_WORKBOOK = load_workbook(EXCEL_LOG_PATH)
        except Exception as e:
            print(f"Could not open existing Excel log ({e}) — starting a fresh workbook.")
            EXCEL_WORKBOOK = Workbook()
            EXCEL_WORKBOOK.remove(EXCEL_WORKBOOK.active)
    else:
        EXCEL_WORKBOOK = Workbook()
        EXCEL_WORKBOOK.remove(EXCEL_WORKBOOK.active)

    for sheet_name in (ACTS_RULES_SHEET_NAME, NOTIFICATIONS_SHEET_NAME):

        if sheet_name not in EXCEL_WORKBOOK.sheetnames:
            sheet = EXCEL_WORKBOOK.create_sheet(sheet_name)
            sheet.append(sheet_headers(sheet_name))
            style_sheet(sheet, sheet_name)
        else:
            migrate_sheet_headers_if_needed(EXCEL_WORKBOOK[sheet_name], sheet_name)

    # Put Acts and Rules first, Notifications second.
    EXCEL_WORKBOOK.move_sheet(ACTS_RULES_SHEET_NAME, offset=-len(EXCEL_WORKBOOK.sheetnames))
    EXCEL_WORKBOOK.active = 0

    try:
        WCD.mkdir(parents=True, exist_ok=True)
        EXCEL_WORKBOOK.save(EXCEL_LOG_PATH)
    except PermissionError:
        print(
            f"\nCOULD NOT CREATE/OPEN EXCEL LOG — {EXCEL_LOG_PATH} appears to be "
            "open in Excel. Close it and re-run."
        )


def live_log_entry(sheet_name, title, file_name, path_code, status, timestamp=None, category=None):
    """
    Records one PDF's outcome in the given sheet and immediately
    saves the workbook to disk — this IS the live tracking.

    - Never logged before: appends a new row.
    - Already logged with the SAME data: does nothing (prevents
      duplicate/no-op rows on re-runs).
    - Already logged but something differs (status changed, etc.):
      updates that same row in place instead of adding a duplicate.
    """

    timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sheet = EXCEL_WORKBOOK[sheet_name]
    columns = SHEET_COLUMNS[sheet_name]

    field_values = {
        "title": title,
        "file_name": file_name,
        "category": category or "",
        "timestamp": timestamp,
        "path_code": path_code,
        "status": status,
    }

    # Include category in the identity key (when this sheet has
    # one). The same underlying PDF file can legitimately appear
    # under two different catalog entries here — e.g. an Act and
    # its associated Rules sometimes share one uploaded file — so
    # File Name alone isn't a safe identifier within this sheet;
    # without Category too, the second entry would be treated as
    # "already logged" and overwrite the first one's row instead
    # of getting its own.
    key_identifier = file_name or title
    if category:
        key_identifier = f"{category}::{key_identifier}"
    entry_key = (sheet_name, key_identifier)

    comparable_keys = [key for key in columns if key != "timestamp"]
    new_snapshot = {key: field_values[key] for key in comparable_keys}

    existing = LOGGED_ENTRIES.get(entry_key)

    if existing:

        if existing.get("snapshot") == new_snapshot:
            return

        row_num = existing["row"]

        for col_idx, key in enumerate(columns, start=1):
            sheet.cell(row=row_num, column=col_idx, value=field_values[key])

        existing["snapshot"] = new_snapshot
        existing["sheet"] = sheet_name

        print(f"Excel log (live) — [{sheet_name}] UPDATED (Status: {status}) | Title: {title}")

    else:

        sheet.append([field_values[key] for key in columns])
        row_num = sheet.max_row

        LOGGED_ENTRIES[entry_key] = {
            "sheet": sheet_name,
            "row": row_num,
            "snapshot": new_snapshot,
        }

        print(f"Excel log (live) — [{sheet_name}] Status: {status} | Title: {title}")

    try:
        EXCEL_WORKBOOK.save(EXCEL_LOG_PATH)
    except PermissionError:
        print(
            f"COULD NOT SAVE EXCEL LOG LIVE — {EXCEL_LOG_PATH} appears to be open "
            "in Excel. This row is queued in memory and will be written on the "
            "next successful save (close the file in Excel to let it catch up)."
        )


# ============================================================
# HELPERS
# ============================================================

def clean_name(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
    return name.rstrip(" .") or "document"


def extract_original_pdf_filename(url):
    """
    Work out the PDF's own original filename as it exists on the
    server (from the URL itself), so the file gets saved under
    its real name instead of a name built from the row's title
    text.

    Tries, in order:
      1. A base64-looking `id=`/`file=` query param some sites use
         to wrap the real relative path/filename.
      2. The last segment of the URL's path.

    Returns None if nothing usable is found, so the caller can
    fall back to a title-based name.
    """

    if not url:
        return None

    try:

        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        for param_name in ("id", "file", "filename"):

            if param_name in query and query[param_name]:

                raw_value = query[param_name][0]

                try:
                    padded = raw_value + "=" * (-len(raw_value) % 4)
                    decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
                    decoded_name = decoded.rsplit("/", 1)[-1].strip()

                    if decoded_name.lower().endswith(".pdf"):
                        return decoded_name

                except Exception:
                    pass

        path_name = unquote(parsed.path.rsplit("/", 1)[-1]).strip()

        if path_name.lower().endswith(".pdf"):
            return path_name

    except Exception:
        pass

    return None


def valid_pdf(path):
    try:
        return (
            path.exists()
            and path.stat().st_size > 1000
            and path.read_bytes()[:5] == b"%PDF-"
        )
    except:
        return False


def scroll_page(page):
    last = 0
    while True:
        height = page.evaluate(
            "document.documentElement.scrollHeight"
        )
        page.evaluate(
            "window.scrollTo(0, document.documentElement.scrollHeight)"
        )
        time.sleep(1)
        new_height = page.evaluate(
            "document.documentElement.scrollHeight"
        )
        if new_height == last:
            break
        last = new_height
    page.evaluate("window.scrollTo(0, 0)")


# ============================================================
# DOWNLOAD — now also live-logs every outcome to the Excel sheet
# passed in via excel_sheet_name (Acts and Rules / Notifications).
# category is only meaningful for the Acts and Rules sheet
# ("ACT" or "RULE") and is ignored for Notifications.
# ============================================================

def download(page, url, folder, title, excel_sheet_name, category=""):

    # Prefer the PDF's own original filename (as it exists on the
    # server) over a name built from the row's title text — falls
    # back to the title-based name only if the original name can't
    # be determined from the URL.
    original_name = extract_original_pdf_filename(url)

    if original_name:
        filename = clean_name(original_name)
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
    else:
        filename = clean_name(title)
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

    path = folder / filename

    if valid_pdf(path):

        print(f"✓ Already exists: {filename}")

        try:
            backfilled_timestamp = datetime.fromtimestamp(
                path.stat().st_mtime
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            backfilled_timestamp = None

        live_log_entry(
            excel_sheet_name,
            title,
            filename,
            str(path.resolve()),
            STATUS_OLD,
            timestamp=backfilled_timestamp,
            category=category,
        )

        return True

    for attempt in range(1, 4):
        try:
            print(f"↓ [{attempt}/3] {filename}")
            # Some direct-file URLs on this site reject a bare
            # request with no Referer (basic anti-hotlink check) —
            # sending one matching the page we found the link on
            # is what a real click would send, and costs nothing
            # for URLs that don't need it.
            response = page.context.request.get(
                url,
                timeout=60000,
                headers={"Referer": page.url},
            )
            data = response.body()
            if response.ok and data[:5] == b"%PDF-":
                path.write_bytes(data)
                if valid_pdf(path):
                    print(f"✓ Saved: {filename}")
                    live_log_entry(
                        excel_sheet_name,
                        title,
                        filename,
                        str(path.resolve()),
                        STATUS_NEW,
                        category=category,
                    )
                    return True
                else:
                    print(f"  Downloaded file failed validation ({len(data)} bytes) — retrying")
            else:
                # This is the case that used to fail completely
                # silently: a 200 OK that isn't actually a PDF
                # (an HTML error/login/redirect page instead), or
                # a non-200 status. Surface exactly what came back
                # so the real cause is visible instead of a bare
                # "FAILED" with no explanation.
                content_type = response.headers.get("content-type", "")
                snippet = data[:120].decode("utf-8", errors="replace") if data else ""
                print(
                    f"  Unexpected response — status={response.status}, "
                    f"content-type={content_type!r}"
                )
                if snippet:
                    print(f"  Response starts with: {snippet!r}")
        except Exception as e:
            print(f"  Retry error: {e}")
        time.sleep(2)

    # Last resort: some servers only serve the real file to an
    # actual browser navigation (full referer/cookies/headers),
    # not a bare API request. Try opening the URL in a real tab
    # and reading whatever the browser itself downloaded/loaded.
    print("  Falling back to opening the PDF in a browser tab...")
    try:
        new_page = page.context.new_page()
        try:
            with new_page.expect_download(timeout=20000) as download_info:
                new_page.goto(url, timeout=30000)
            downloaded = download_info.value
            downloaded.save_as(str(path))
            if valid_pdf(path):
                print(f"✓ Saved via browser tab: {filename}")
                live_log_entry(
                    excel_sheet_name,
                    title,
                    filename,
                    str(path.resolve()),
                    STATUS_NEW,
                    category=category,
                )
                new_page.close()
                return True
        except Exception:
            # No download event fired — the PDF likely rendered
            # directly in the tab instead of triggering a browser
            # download. Read the raw response bytes from that
            # navigation instead.
            try:
                response = new_page.goto(url, timeout=30000)
                if response is not None:
                    data = response.body()
                    if data[:5] == b"%PDF-":
                        path.write_bytes(data)
                        if valid_pdf(path):
                            print(f"✓ Saved via browser tab: {filename}")
                            live_log_entry(
                                excel_sheet_name,
                                title,
                                filename,
                                str(path.resolve()),
                                STATUS_NEW,
                                category=category,
                            )
                            new_page.close()
                            return True
            except Exception as e:
                print(f"  Browser-tab fallback error: {e}")
        new_page.close()
    except Exception as e:
        print(f"  Could not open browser-tab fallback: {e}")

    print(f"✗ FAILED: {filename}")

    live_log_entry(
        excel_sheet_name,
        title,
        filename,
        "",
        STATUS_FAILED,
        category=category,
    )

    return False


def download_notifications(page):
    print("\n========== NOTIFICATIONS ==========")
    page.goto(
        START_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )
    page.wait_for_timeout(2000)
    offerings = page.get_by_text(
        "Offerings",
        exact=True
    )
    if offerings.count():
        offerings.first.click()
        page.wait_for_timeout(1000)
    notification = page.locator(
        "a[href*='/offerings/whatsnew']"
    ).first
    if not notification.count():
        print("✗ Notifications link not found")
        return
    notification.click()
    page.wait_for_timeout(2500)
    scroll_page(page)
    links = page.locator("a[href*='.pdf']")
    print(f"PDF links found: {links.count()}")
    for i in range(links.count()):
        try:
            link = links.nth(i)
            href = link.get_attribute("href")
            if not href:
                continue
            url = urljoin(page.url, href)
            row = link.locator(
                "xpath=ancestor::*["
                "self::tr or "
                "contains(@class,'list_det_bx') or "
                "contains(@class,'row')"
                "][1]"
            )
            title = ""
            if row.count():
                paragraphs = row.locator("p")
                for j in range(paragraphs.count()):
                    text = paragraphs.nth(j).inner_text().strip()
                    if text and text.lower() not in {
                        "view",
                        "notification",
                        "notifications"
                    }:
                        title = text
                        break
            if not title:
                title = f"notification_{i + 1}"
            print(f"\nNotification: {title}")
            download(
                page,
                url,
                NOTIFICATION,
                title,
                excel_sheet_name=NOTIFICATIONS_SHEET_NAME,
            )
        except Exception as e:
            print(f"Notification error: {e}")


def download_wcd_pdf_list(page, url, folder, sheet_name, label):
    """Download every PDF listed by a paginated WCD document page."""
    print(f"\n========== {label.upper()} ==========")
    seen = set()
    for page_no in range(1, 100):
        target = url if page_no == 1 else f"{url}?page={page_no}"
        page.goto(target, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1800)
        scroll_page(page)
        links = page.locator("a[href*='/documents/uploaded/'][href*='.pdf']")
        page_seen = 0
        for i in range(links.count()):
            link = links.nth(i)
            href = link.get_attribute("href")
            if not href:
                continue
            pdf_url = urljoin(page.url, href)
            title = clean_title_from_link(
                link.get_attribute("aria-label"), link.inner_text()
            )
            if not title:
                title = f"{label}_{page_no}_{i + 1}"
            key = (title, pdf_url)
            if key in seen:
                continue
            seen.add(key)
            page_seen += 1
            download(page, pdf_url, folder, title, excel_sheet_name=sheet_name)
        print(f"{label}: page {page_no}, found {page_seen} new PDFs")
        next_link = page.locator("a", has_text=re.compile(r"^Next", re.I)).last
        if not next_link.count() or not next_link.get_attribute("href"):
            break
    print(f"{label}: total PDFs processed {len(seen)}")


def clean_title_from_link(aria_label, link_text):
    """
    Some rows (like the alternate "det_cont" layout that has no
    separate title cell) only carry the real title inside the PDF
    link's aria-label, typically formatted as:
        "<Title> - PDF file (282.16 KB), opens in a new tab"
    Strips that trailing description off to get the plain title.
    Falls back to the link's own visible text (e.g. "View") only
    if it isn't just a generic label.
    """

    text = (aria_label or "").strip()

    if text:
        text = re.sub(r'\s*-\s*PDF file.*$', '', text, flags=re.IGNORECASE)
        text = text.strip()
        if text:
            return text

    link_text = (link_text or "").strip()

    if link_text and link_text.lower() not in {"view", "download", "pdf"}:
        return link_text

    return ""


def guess_category_from_title(title):
    """
    Used only when a row has no explicit category cell (the
    alternate "det_cont" layout doesn't have one). Titles on this
    site read like "Rules under the XXX Act, YYYY" (a Rules
    document) or "The XXX Act, YYYY" (an Act) — so whichever word,
    "rules" or "act", appears FIRST in the title decides it.
    Returns "ACT", "RULE", or None if neither word appears.
    """

    lowered = (title or "").lower()
    rules_pos = lowered.find("rules")
    act_pos = lowered.find("act")

    if rules_pos != -1 and (act_pos == -1 or rules_pos <= act_pos):
        return "RULE"

    if act_pos != -1:
        return "ACT"

    return None


MAX_ACTS_RULES_PAGES = 40


def download_acts_rules(page):
    print("\n========== ACTS + RULES ==========")
    processed = set()
    failed = []
    consecutive_empty_pages = 0

    for page_no in range(1, MAX_ACTS_RULES_PAGES + 1):
        url = (
            START_URL
            if page_no == 1
            else f"{START_URL}?page={page_no}"
        )
        print(f"\n--- Page {page_no} ---")
        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )
            page.wait_for_timeout(2000)
            scroll_page(page)
        except Exception as e:
            print(f"✗ Page error: {e}")
            continue

        # Anchor on every PDF link on the page rather than a
        # specific row container class — the site uses more than
        # one row template (e.g. "list_det_bx tender_list" for
        # most rows, but "det_cont last_vw" for at least one — the
        # naming suggests it's the LAST item of the whole list,
        # which is exactly why a hardcoded page cap could miss it),
        # and a class-specific row selector silently misses any PDF
        # sitting in a layout it doesn't match.
        links = page.locator("a[href*='.pdf']")
        print(f"PDF links found: {links.count()}")

        new_on_this_page = 0

        for i in range(links.count()):
            try:
                link = links.nth(i)

                href = link.get_attribute("href")
                if not href:
                    continue

                pdf_url = urljoin(page.url, href)

                # 'det_cont' is a generic wrapper used at MULTIPLE
                # nesting levels in this site's markup (it wraps
                # the title text, and separately wraps just the
                # view/download button) — matching on it directly
                # can lock onto a tiny inner wrapper instead of the
                # actual row, losing the Category/Title cells that
                # live alongside it. Look for the true row boundary
                # first (Bootstrap ".row", "list_det_bx", or a
                # table "tr") and only fall back to 'det_cont' if
                # none of those exist for this row's layout.
                row = link.locator(
                    "xpath=ancestor::*["
                    "self::tr or "
                    "contains(@class,'list_det_bx') or "
                    "contains(@class,'row')"
                    "][1]"
                )

                if not row.count():
                    row = link.locator(
                        "xpath=ancestor::*[contains(@class,'det_cont')][1]"
                    )

                category_text = ""
                title = ""

                if row.count():

                    category_cell = row.locator(".col-md-2 p")
                    if category_cell.count():
                        category_text = category_cell.first.inner_text().strip()

                    title_cell = row.locator(".col-md-5 p")
                    if title_cell.count():
                        title = title_cell.first.inner_text().strip()

                if not title:
                    aria_label = link.get_attribute("aria-label") or ""
                    link_text = link.inner_text().strip()
                    title = clean_title_from_link(aria_label, link_text)

                if not title:
                    title = f"document_{i + 1}"

                category = category_text.strip().upper()

                if category == "ACTS":
                    label = "ACT"
                elif category == "RULES":
                    label = "RULE"
                else:
                    # No explicit category cell on this row's
                    # layout — fall back to guessing from the
                    # title text itself.
                    label = guess_category_from_title(title)

                if not label:
                    print(f"? Unknown category: {title}")
                    continue

                # De-dup by (category, URL), NOT by URL alone. The
                # site sometimes points an Act's entry and its
                # associated Rules' entry at the exact same PDF
                # file (e.g. "The Commission of Sati (Prevention)
                # Act, 1987" and "Rules under the Commission of
                # Sati (Prevention) Act, 1987" share one file). A
                # URL-only dedup would treat the second one as an
                # already-seen duplicate and silently skip it —
                # even though it's a distinct catalog entry that
                # belongs in a different folder (Rules, not Acts).
                dedup_key = (label, pdf_url)

                if dedup_key in processed:
                    continue

                processed.add(dedup_key)
                new_on_this_page += 1

                folder = ACT if label == "ACT" else RULES

                print(f"\n{label}: {title}")

                if not download(
                    page,
                    pdf_url,
                    folder,
                    title,
                    excel_sheet_name=ACTS_RULES_SHEET_NAME,
                    category=label,
                ):
                    failed.append(title)

            except Exception as e:
                print(
                    f"Row {i + 1} error: {e}"
                )

        # Stop once pagination genuinely runs out of new content,
        # instead of stopping at a fixed page count that might cut
        # the list off before its last item. Two consecutive empty
        # pages (rather than just one) guards against a single
        # page failing to render in time.
        if new_on_this_page == 0:
            consecutive_empty_pages += 1
            print("No new PDFs found on this page.")
            if consecutive_empty_pages >= 2:
                print("Two consecutive pages with no new PDFs — stopping pagination.")
                break
        else:
            consecutive_empty_pages = 0

    print("\n========== ACT/RULE SUMMARY ==========")
    print(f"Processed PDFs : {len(processed)}")
    print(f"Failed PDFs    : {len(failed)}")
    if failed:
        print("\nFAILED:")
        for item in failed:
            print(f" - {item}")
    else:
        print("✓ All Acts and Rules downloaded")


def verify(folder, name):
    files = list(folder.glob("*.pdf"))
    bad = [f.name for f in files if not valid_pdf(f)]
    print(
        f"{name}: {len(files)} PDFs | "
        f"{len(bad)} invalid"
    )
    if bad:
        for item in bad:
            print(f"  ✗ {item}")
    return not bad


def main():

    LOGGED_ENTRIES.update(load_logged_entries_from_excel())
    print(f"Loaded {len(LOGGED_ENTRIES)} already-logged entrie(s) from the existing Excel file (if any).")

    init_excel_workbook()
    print(f"Excel log ready (live tracking): {EXCEL_LOG_PATH}")

    with sync_playwright() as p:
        print("\n======================================")
        print("WCD PDF DOWNLOADER")
        print("======================================")
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--start-maximized",
                "--window-position=0,0"
            ]
        )
        context = browser.new_context(
            viewport=None,
            accept_downloads=True,
        )
        page = context.new_page()
        try:
            page.evaluate("""
                () => {
                    window.moveTo(0, 0);
                    window.resizeTo(
                        screen.availWidth,
                        screen.availHeight
                    );
                }
            """)
        except:
            pass
        download_notifications(page)
        download_acts_rules(page)
        download_wcd_pdf_list(
            page,
            ORDERS_URL,
            ORDERS,
            ORDERS_SHEET_NAME,
            "Orders and Notices",
        )
        download_wcd_pdf_list(
            page,
            ARCHIVE_ORDERS_URL,
            ARCHIVE_ORDERS,
            ARCHIVE_ORDERS_SHEET_NAME,
            "Archived Orders and Notices",
        )
        print("\n========== FINAL VERIFICATION ==========")
        n_ok = verify(
            NOTIFICATION,
            "Notifications"
        )
        a_ok = verify(
            ACT,
            "Acts"
        )
        r_ok = verify(
            RULES,
            "Rules"
        )
        o_ok = verify(ORDERS, "Orders and Notices")
        ao_ok = verify(ARCHIVE_ORDERS, "Archived Orders and Notices")
        print("\n======================================")
        if n_ok and a_ok and r_ok and o_ok and ao_ok:
            print("✓ ALL DOWNLOADED PDFs VERIFIED")
        else:
            print("⚠ CHECK FAILED/INVALID PDFs")
        print("======================================")
        print("\nFolders:")
        print("WCD/notification")
        print("WCD/act")
        print("WCD/rules")
        print("WCD/orders_and_notices")
        print("WCD/archive_orders_and_notices")
        print(f"\nExcel log (live tracked): {EXCEL_LOG_PATH}")
        print(f"  Sheet 1: {ACTS_RULES_SHEET_NAME}")
        print(f"  Sheet 2: {NOTIFICATIONS_SHEET_NAME}")
        print(f"  Sheet 3: {ORDERS_SHEET_NAME}")
        print(f"  Sheet 4: {ARCHIVE_ORDERS_SHEET_NAME}")
        print("\nClosing Chrome...")
        browser.close()
        print("Chrome closed.")


if __name__ == "__main__":
    main()
