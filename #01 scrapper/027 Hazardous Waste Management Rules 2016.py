"""CPCB hazardous-waste rules and circular downloader.

Execution order:
1. Hazardous and other Wastes Rules, 2016 amendments.
2. Hazardous Waste Rules, 2008 amendments.
3. All CPCB Office Order/Circular table pages and categories.

Output root: CPCB/
Run with: python "027 Hazardous Waste Management Rules 2016.py"
"""

from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote, parse_qs
from openpyxl.utils import get_column_letter
import base64
import re
import sys
import os
import json
import random
import time

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from openpyxl import Workbook, load_workbook
except ImportError:
    print(
        "\nERROR: the 'openpyxl' package is required for the Excel "
        "download log used by this script.\n"
        "Install it with:\n\n    pip install openpyxl\n"
    )
    raise


# ============================================================
# PAGES TO AUTOMATE
# ============================================================

HAZARDOUS_WASTE_PAGE_URL = "https://cpcb.gov.in/hazardous-waste-rules/"
CIRCULAR_PAGE_URL = "https://cpcb.gov.in/circular/"


# ============================================================
# FOLDER STRUCTURE
#
# CPCB/
# ├── Hazardous waste Rules/
# │   ├── Other waste/        <- Hazardous and other Wastes
# │   │                          (Management & Transboundary
# │   │                          Movement) Rules, 2016 — and
# │   │                          its amendments
# │   └── Hazardous waste/    <- Hazardous Waste (Management,
# │                               Handling & Transboundary
# │                               Movement) Rules, 2008 — and
# │                               its amendments
# └── Circulars/               <- ALL rows from the Office
#                                  Order / Circular table,
#                                  every Category
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = next(parent / ".env" for parent in Path(__file__).resolve().parents if (parent / "docker-compose.yml").is_file())
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))
ROOT_FOLDER = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR)) / Path(__file__).stem
RUNTIME_DIR = ROOT_FOLDER / "logs"
RUN_STATUS = RUNTIME_DIR / "run_status.json"
RUNTIME_LOG = RUNTIME_DIR / "runtime.log"
RESILIENCE_ATTEMPTS = int(os.environ.get("SCRAPER_MAX_ATTEMPTS", "3"))
REQUEST_DELAY_SECONDS = float(os.environ.get("SCRAPER_REQUEST_DELAY_SECONDS", "1.0"))
BLOCK_COOLDOWN = float(os.environ.get("SCRAPER_BLOCK_COOLDOWN_SECONDS", "300"))


class SiteAccessBlocked(RuntimeError):
    pass


BLOCK_DETECTED = ""


def check_response_status(status, url=""):
    global BLOCK_DETECTED
    if status in {403, 429}:
        BLOCK_DETECTED = f"HTTP {status} from {url}"
        raise SiteAccessBlocked(BLOCK_DETECTED)


def write_run_status(state, message="", attempt=0):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {"script": Path(__file__).name, "state": state, "attempt": attempt,
               "message": str(message), "timestamp": stamp}
    temporary = RUN_STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(RUN_STATUS)
    with RUNTIME_LOG.open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp} | {state} | attempt={attempt} | {message}\n")


def guarded_main(entrypoint):
    global BLOCK_DETECTED
    for attempt in range(1, RESILIENCE_ATTEMPTS + 1):
        BLOCK_DETECTED = ""
        write_run_status("running", attempt=attempt)
        try:
            result = entrypoint()
            if BLOCK_DETECTED:
                raise SiteAccessBlocked(BLOCK_DETECTED)
            write_run_status("completed", attempt=attempt)
            return result
        except KeyboardInterrupt:
            write_run_status("stopped", "Stopped by user", attempt)
            raise
        except SiteAccessBlocked as error:
            write_run_status("blocked", error, attempt)
            if attempt == RESILIENCE_ATTEMPTS:
                raise
            time.sleep(BLOCK_COOLDOWN + random.uniform(0, 5))
        except Exception as error:
            write_run_status("error", error, attempt)
            if attempt == RESILIENCE_ATTEMPTS:
                raise
            time.sleep(min(120, 5 * (2 ** (attempt - 1))) + random.uniform(0, 2))

HAZARDOUS_WASTE_RULES_FOLDER = ROOT_FOLDER / "Hazardous waste Rules"
OTHER_WASTE_FOLDER = HAZARDOUS_WASTE_RULES_FOLDER / "Other waste"
HAZARDOUS_WASTE_FOLDER = HAZARDOUS_WASTE_RULES_FOLDER / "Hazardous waste"

CIRCULARS_FOLDER = ROOT_FOLDER / "Circulars"

for folder in [
    OTHER_WASTE_FOLDER,
    HAZARDOUS_WASTE_FOLDER,
    CIRCULARS_FOLDER,
]:
    folder.mkdir(parents=True, exist_ok=True)


# ============================================================
# EXCEL DOWNLOAD LOG — LIVE TRACKING
#
# Two sheets, one per source page:
#   "Hazardous Waste Rules page" — every PDF from both
#                                   amendment lists
#   "Circular page"               — every PDF from the Office
#                                   Order / Circular table
#
# Each sheet's columns: Date, Title, File Name, Timestamp,
#                        Path Code, Status.
#
#   Date       -> upload date pulled from the title text (if any)
#   Title      -> the display title of the PDF (link text / row title)
#   File Name  -> the EXACT file name the PDF was saved under on
#                 disk (the PDF's own original name, not the title)
#   Timestamp  -> when this row was recorded
#   Path Code  -> full resolved path to the saved file on disk
#   Status     -> "New Download"   - downloaded for the first time
#                                     during this run
#                 "Old Download"   - the file already existed on
#                                     disk (skipped re-downloading)
#                 "Not Downloaded" - every attempt (including the
#                                     final retry pass) failed
#
# LIVE TRACKING: the workbook is opened once at the start of the
# run, kept open in memory, and saved to disk again immediately
# after every single row is written or updated (not just once at
# the end) — so if the script is interrupted partway through, or
# you open the file mid-run, everything processed so far is
# already in it. If Excel has the file open (locking it) when a
# live save is attempted, that one save is skipped with a
# warning — the change stays queued in memory and gets flushed
# to disk on the very next successful save, so nothing is lost,
# just delayed until the file is closed.
#
# A given (Date, Title) is only ever ONE row in a sheet. If that
# same PDF is seen again on a later run (or later in the same
# run) with a DIFFERENT status than before — e.g. it previously
# failed and is now downloaded, or it was only backfilled and is
# now freshly downloaded — that existing row is UPDATED in place
# rather than a duplicate row being appended.
# ============================================================

EXCEL_LOG_PATH = ROOT_FOLDER / "CPCB_Download_Log.xlsx"

HAZ_SHEET_NAME = "Hazardous Waste Rules page"
CIRC_SHEET_NAME = "Circular page"

STATUS_NEW = "New Download"
STATUS_OLD = "Old Download"
STATUS_FAILED = "Not Downloaded"

# Column layout is per-sheet: the Circular page sheet has a
# "Category" column (next to File Name) that the Hazardous Waste
# Rules page sheet doesn't need, since that page has no category.
SHEET_COLUMNS = {
    HAZ_SHEET_NAME: ["date", "title", "file_name", "timestamp", "path_code", "status"],
    CIRC_SHEET_NAME: ["date", "division", "title", "file_name", "category", "timestamp", "path_code", "status"],
}

FIELD_KEY_TO_HEADER_LABEL = {
    "date": "Date",
    "title": "Title",
    "file_name": "File Name",
    "category": "Category",
    "division": "Division",
    "timestamp": "Timestamp",
    "path_code": "Path Code",
    "status": "Status",
}

FIELD_KEY_TO_COLUMN_WIDTH = {
    "date": 18,
    "title": 70,
    "file_name": 45,
    "category": 22,
    "division": 18,
    "timestamp": 22,
    "path_code": 110,
    "status": 18,
}

HEADER_LABEL_TO_FIELD_KEY = {
    "PDF Upload Date": "date",
    "Date": "date",
    "PDF Name": "title",
    "Title": "title",
    "File Name": "file_name",
    "Category": "category",
    "Division": "division",
    "Download Date & Time": "timestamp",
    "Timestamp": "timestamp",
    "Code Path": "path_code",
    "Path Code": "path_code",
    "Status": "status",
}


def sheet_headers(sheet_name):
    return [FIELD_KEY_TO_HEADER_LABEL[key] for key in SHEET_COLUMNS[sheet_name]]

DATE_IN_TITLE_REGEX = re.compile(r'(\d{2}\.\d{2}\.\d{4})')

# Set once in main() via init_excel_workbook() and reused for
# every live write for the rest of the run.
EXCEL_WORKBOOK = None

# (sheet_name, file_name) -> {"sheet": sheet_name, "row": row_number, "snapshot": {...}}
# Covers everything already on disk when the run started PLUS
# everything written live during this run — checked before every
# live write so nothing is ever duplicated, and so any changed
# field (status, or a previously-blank Date/Category/Division
# that now has a value) updates the existing row instead of
# adding a new one. Keyed by File Name — the PDF's own stable
# identity — rather than Date, so fixing Date extraction later
# corrects existing rows instead of creating duplicates of them.
LOGGED_ENTRIES = {}


def extract_upload_date_from_title(title):
    match = DATE_IN_TITLE_REGEX.search(title)
    return match.group(1) if match else ""


def extract_original_pdf_filename(href, resolved_url):
    """
    Work out the PDF's own original filename (as it actually
    exists on the CPCB server), so files can be saved under their
    real name instead of a name built from the link's title text.

    CPCB serves PDFs two different ways across these pages:
      1. A direct link straight to a .pdf file
         (e.g. ".../uploads/hwmd/July_Amendment_HOWM.pdf").
      2. A "displaypdf.php?id=<base64>" wrapper, where the id is
         the base64-encoded relative path/filename of the real
         PDF (e.g. decodes to "hwmd/1st_Amendments_Rules.pdf").

    Tries both the raw href and the resolved absolute URL, and
    for each tries (a) decoding a base64 `id` query param, then
    (b) just taking the last segment of the URL path. Returns
    None if nothing usable is found, so the caller can fall back
    to the title-based name.
    """

    for candidate_url in (resolved_url, href):

        if not candidate_url:
            continue

        try:

            parsed = urlparse(candidate_url)
            query = parse_qs(parsed.query)

            if "id" in query and query["id"]:

                raw_id = query["id"][0]

                try:
                    padded = raw_id + "=" * (-len(raw_id) % 4)
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
            continue

    return None


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
    missing a column that belongs there now (e.g. an older log's
    Circular page sheet with no Category column yet), rebuild the
    sheet with the current column layout, preserving every
    existing value by field — and preserving row order/numbers,
    so any row references already loaded via
    load_logged_entries_from_excel() stay valid.
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
    start of a run so:
      - already-on-disk PDFs from a previous run aren't
        re-downloaded
      - a PDF saved to disk but never logged gets backfilled
        exactly once
      - a row whose data later changes (status flips, or a field
        like Date/Category/Division that was blank before now
        has a value) gets UPDATED in place instead of duplicated

    Keyed by File Name (falling back to Title for any very old
    row saved before the File Name column existed) rather than by
    Date, since File Name is the PDF's own stable identity and
    won't change even if Date extraction is fixed or corrected
    later.
    """

    logged_entries = {}

    if not EXCEL_LOG_PATH.exists():
        return logged_entries

    try:

        workbook = load_workbook(EXCEL_LOG_PATH, read_only=True)

        for sheet_name in (HAZ_SHEET_NAME, CIRC_SHEET_NAME):

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

                key_identifier = file_name_value if file_name_value else title_value

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
        except SiteAccessBlocked:
            raise

        except Exception as e:
            print(f"Could not open existing Excel log ({e}) — starting a fresh workbook.")
            EXCEL_WORKBOOK = Workbook()
            EXCEL_WORKBOOK.remove(EXCEL_WORKBOOK.active)
    else:
        EXCEL_WORKBOOK = Workbook()
        EXCEL_WORKBOOK.remove(EXCEL_WORKBOOK.active)

    for sheet_name in (HAZ_SHEET_NAME, CIRC_SHEET_NAME):

        if sheet_name not in EXCEL_WORKBOOK.sheetnames:
            sheet = EXCEL_WORKBOOK.create_sheet(sheet_name)
            sheet.append(sheet_headers(sheet_name))
            style_sheet(sheet, sheet_name)
        else:
            migrate_sheet_headers_if_needed(EXCEL_WORKBOOK[sheet_name], sheet_name)

    EXCEL_WORKBOOK.active = EXCEL_WORKBOOK.sheetnames.index(HAZ_SHEET_NAME)

    try:
        ROOT_FOLDER.mkdir(parents=True, exist_ok=True)
        EXCEL_WORKBOOK.save(EXCEL_LOG_PATH)
    except PermissionError:
        print(
            f"\nCOULD NOT CREATE/OPEN EXCEL LOG — {EXCEL_LOG_PATH} appears to be "
            "open in Excel. Close it and re-run."
        )


def live_log_entry(
    sheet_name,
    upload_date,
    title,
    file_name,
    path_code,
    status,
    timestamp=None,
    category=None,
    division=None,
):
    """
    Records one PDF's outcome in the given sheet and immediately
    saves the workbook to disk — this IS the live tracking.

    Each PDF is identified by (sheet, File Name) rather than
    (Date, Title) — File Name is the PDF's own stable original
    name, so a row is matched to the same PDF on every run even
    if a field like Date comes out different this time (e.g. a
    previously-blank Date that a fixed extraction can now fill
    in correctly).

    - Never logged before: appends a new row.
    - Already logged with the SAME data: does nothing (prevents
      duplicate/no-op rows on re-runs).
    - Already logged but ANY field differs (status changed, or a
      previously blank Date/Category/Division now has a value):
      updates that same row in place instead of adding a
      duplicate or leaving stale data behind.

    Column layout differs per sheet (e.g. only the Circular page
    sheet has Category/Division columns) — SHEET_COLUMNS[sheet_name]
    drives which fields actually get written and in what order.
    """

    timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sheet = EXCEL_WORKBOOK[sheet_name]
    columns = SHEET_COLUMNS[sheet_name]

    field_values = {
        "date": upload_date,
        "title": title,
        "file_name": file_name,
        "category": category or "",
        "division": division or "",
        "timestamp": timestamp,
        "path_code": path_code,
        "status": status,
    }

    key_identifier = file_name or title
    entry_key = (sheet_name, key_identifier)

    # Timestamp is excluded from change-detection -- it's expected
    # to differ on every write and shouldn't by itself trigger an
    # update.
    comparable_keys = [key for key in columns if key != "timestamp"]
    new_snapshot = {key: field_values[key] for key in comparable_keys}

    existing = LOGGED_ENTRIES.get(entry_key)

    if existing:

        if existing.get("snapshot") == new_snapshot:
            # Nothing changed — skip to avoid a duplicate/no-op write.
            return

        row_num = existing["row"]

        for col_idx, key in enumerate(columns, start=1):
            sheet.cell(row=row_num, column=col_idx, value=field_values[key])

        existing["snapshot"] = new_snapshot
        existing["sheet"] = sheet_name

        print(
            f"Excel log (live) — [{sheet_name}] UPDATED (Status: {status}) | "
            f"Title: {title}"
        )

    else:

        sheet.append([field_values[key] for key in columns])
        row_num = sheet.max_row

        LOGGED_ENTRIES[entry_key] = {
            "sheet": sheet_name,
            "row": row_num,
            "snapshot": new_snapshot,
        }

        print(
            f"Excel log (live) — [{sheet_name}] Status: {status} | "
            f"Date: {upload_date or '(none found)'} | Title: {title}"
        )

    try:
        EXCEL_WORKBOOK.save(EXCEL_LOG_PATH)
    except PermissionError:
        print(
            f"COULD NOT SAVE EXCEL LOG LIVE — {EXCEL_LOG_PATH} appears to be open "
            "in Excel. This row is queued in memory and will be written on the "
            "next successful save (close the file in Excel to let it catch up)."
        )


# ============================================================
# HAZARDOUS WASTE RULES — FALLBACK PDF LISTS
#
# Used only if the live scan of the Hazardous Waste Rules page
# doesn't confidently find both amendment lists.
# ============================================================

OTHER_WASTE_PDFS = [
    ("../uploads/hwmd/July_Amendment_HOWM.pdf", "First Amendments Rules, 06.07.2016"),
    ("../uploads/hwmd/Feb_Amendment_HOWM.pdf", "Second Amendments Rules, 28.02.2017"),
    ("../uploads/hwmd/June_Amendemnet_HOWM.pdf", "Third Amendments Rules, 11.06.2018"),
    ("../uploads/hwmd/March_Amendment_HOWM.pdf", "Fourth Amendments Rules, 01.03.2019"),
    ("../uploads/hwmd/HOWM-Fifth-Amendment-Rules-2020.pdf", "Fifth Amendments Rules, 09.10.2020"),
    ("../uploads/hwmd/HOWM-Second-Amendment-Rules-2021.pdf", "Second Amendments Rules, 12.11.2021"),
    ("../uploads/hwmd/HOWM-Sixth-Amendment-Rules-2022.pdf", "Sixth Amendments Rules, 21.07.2022"),
]

HAZARDOUS_WASTE_PDFS = [
    ("../displaypdf.php?id=aHdtZC8xc3RfQW1lbmRtZW50c19SdWxlcy5wZGY=", "First Amendments Rules, 21.07.2009"),
    ("../displaypdf.php?id=aHdtZC8ybmRfQW1lbmRtZW50c19SdWxlcy5wZGY=", "Second Amendments Rules, 23.09.2009"),
    ("../displaypdf.php?id=aHdtZC8zcmRfQW1lbmRtZW50X1J1bGVzLnBkZg==", "Third Amendments Rules, 30.03.2010"),
    ("../displaypdf.php?id=aHdtZC80dGhfQW1lbmRtZW50c19SdWxlcy5wZGY=", "Fourth Amendments Rules, 13.08.2010"),
]


# ============================================================
# CLEAN FILE NAME
# ============================================================

def clean_filename(filename):

    filename = filename.strip()
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = re.sub(r'\s+', ' ', filename)
    filename = filename.strip(" .")

    if not filename:
        filename = "document"

    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    return filename


def clean_display_title(text):
    text = (text or "").strip()
    text = re.sub(r'\s+', ' ', text)
    return text


# ============================================================
# GLOBAL — TRACK ANY PDF THAT FAILS ALL RETRIES SO IT CAN GET
# ONE MORE ATTEMPT IN THE FINAL RETRY PASS AT THE END OF THE RUN
# ============================================================

FAILED_DOWNLOADS = []

# Set True after the Circular table's detected column headers are
# printed once, so the diagnostic line doesn't repeat on every
# paginated table page.
_PRINTED_CIRCULAR_HEADERS = False


# ============================================================
# SAVE PDF FROM PDF LINK
#
# open_in_tab=True  -> visual confirmation (open in new tab,
#                       ~2s pause, close tab) before downloading.
#                       Used for the Hazardous Waste Rules lists.
# open_in_tab=False -> skip the tab step, go straight to
#                       download. Used for the Circular page.
#
# excel_sheet_name selects which of the 2 live-tracked sheets
# this download's row goes into.
#
# The file is always saved under its OWN original PDF filename
# (recovered from the link/URL), not the title text — `filename`
# here is only the title-based fallback used if that can't be
# determined.
# ============================================================

def save_pdf_from_link(
    page,
    context,
    href,
    filename,
    save_folder,
    number,
    total,
    excel_sheet_name,
    section_label="",
    max_retries=3,
    upload_date="",
    excel_title=None,
    open_in_tab=True,
    category="",
    division="",
):

    pdf_page = None

    print("\n==================================================")
    print(f"[{number}/{total}] {filename}")
    print("==================================================")

    try:

        pdf_url = page.evaluate(
            """
            href => new URL(
                href,
                document.baseURI
            ).href
            """,
            href
        )

    except Exception as e:

        print(f"COULD NOT RESOLVE URL: {e}")

        FAILED_DOWNLOADS.append(
            (section_label, href, filename, save_folder, upload_date, excel_title, category, division, open_in_tab, excel_sheet_name)
        )

        return False

    print("PDF URL:")
    print(pdf_url)

    if open_in_tab:

        try:

            with context.expect_page(timeout=5000) as new_page_info:
                page.locator(f'a[href="{href}"]').first.click()

            pdf_page = new_page_info.value

            try:
                pdf_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

            print("PDF OPENED IN BROWSER TAB")
            time.sleep(2)
            print("2 SECONDS COMPLETED")

        except Exception as open_error:
            print(f"PDF OPEN WARNING (continuing anyway): {open_error}")

    # Use the PDF's own original filename whenever it can be
    # determined; only fall back to a title-based name otherwise.
    original_name = extract_original_pdf_filename(href, pdf_url)
    target_filename = clean_filename(original_name if original_name else filename)
    target_path = save_folder / target_filename

    display_title = clean_display_title(
        excel_title if excel_title else target_filename
    )

    if target_path.exists():

        print(f"ALREADY SAVED — skipping duplicate save: {target_path}")

        try:
            backfilled_timestamp = datetime.fromtimestamp(
                target_path.stat().st_mtime
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            backfilled_timestamp = None

        live_log_entry(
            excel_sheet_name,
            upload_date,
            display_title,
            target_filename,
            str(target_path.resolve()),
            STATUS_OLD,
            timestamp=backfilled_timestamp,
            category=category,
            division=division,
        )

        if pdf_page is not None:
            try:
                pdf_page.close()
                print("PDF TAB CLOSED")
            except Exception:
                pass

        return True

    saved = False

    for attempt in range(1, max_retries + 1):

        try:

            time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 0.35))
            response = context.request.get(pdf_url, timeout=60000)
            check_response_status(response.status, pdf_url)

            print(f"Attempt {attempt}/{max_retries} — Response Status: {response.status}")

            if response.ok:

                body = response.body()

                if not body:
                    raise Exception("Empty response body")

                save_folder.mkdir(parents=True, exist_ok=True)

                with open(target_path, "wb") as file:
                    file.write(body)

                if target_path.stat().st_size == 0:
                    raise Exception("Saved file is 0 bytes")

                print("PDF SAVED SUCCESSFULLY")
                print(f"Location: {target_path}")

                live_log_entry(
                    excel_sheet_name,
                    upload_date,
                    display_title,
                    target_filename,
                    str(target_path.resolve()),
                    STATUS_NEW,
                    category=category,
                    division=division,
                )

                saved = True
                break

            else:
                print(f"DOWNLOAD FAILED: {response.status} {response.status_text}")

        except Exception as e:
            print(f"ERROR ON ATTEMPT {attempt}: {e}")

        if attempt < max_retries:
            wait_time = attempt * 2
            print(f"Retrying in {wait_time}s...")
            time.sleep(wait_time)

    if pdf_page is not None:
        try:
            pdf_page.close()
            print("PDF TAB CLOSED")
        except Exception:
            pass

    if saved:
        return True

    print(f"GIVING UP FOR NOW AFTER {max_retries} ATTEMPTS: {filename}")

    FAILED_DOWNLOADS.append(
        (section_label, pdf_url, filename, save_folder, upload_date, excel_title, category, division, open_in_tab, excel_sheet_name)
    )

    return False


# ============================================================
# FINAL RETRY PASS
# ============================================================

def retry_failed_downloads(context, max_retries=3):

    if not FAILED_DOWNLOADS:
        print("\nNo failed downloads to retry — everything saved cleanly.")
        return

    print("\n\n")
    print("##################################################")
    print(f"# FINAL RETRY PASS — {len(FAILED_DOWNLOADS)} PDF(S) TO RECHECK")
    print("##################################################")

    pending = FAILED_DOWNLOADS[:]
    FAILED_DOWNLOADS.clear()

    still_failed = []

    for section_label, pdf_url, filename, save_folder, upload_date, excel_title, category, division, open_in_tab, excel_sheet_name in pending:

        print(f"\nRetrying: [{section_label}] {filename}")

        original_name = extract_original_pdf_filename(pdf_url, pdf_url)
        target_filename = clean_filename(original_name if original_name else filename)
        target_path = save_folder / target_filename

        display_title = clean_display_title(excel_title if excel_title else target_filename)

        if target_path.exists():

            print(f"ALREADY SAVED — skipping (no duplicate): {target_path}")

            try:
                backfilled_timestamp = datetime.fromtimestamp(
                    target_path.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                backfilled_timestamp = None

            live_log_entry(
                excel_sheet_name,
                upload_date,
                display_title,
                target_filename,
                str(target_path.resolve()),
                STATUS_OLD,
                timestamp=backfilled_timestamp,
                category=category,
                division=division,
            )

            continue

        recovered = False

        for attempt in range(1, max_retries + 1):

            try:

                time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 0.35))
                response = context.request.get(pdf_url, timeout=60000)
                check_response_status(response.status, pdf_url)

                if response.ok:

                    body = response.body()

                    if body:

                        save_folder.mkdir(parents=True, exist_ok=True)

                        with open(target_path, "wb") as file:
                            file.write(body)

                        if target_path.stat().st_size > 0:
                            print(f"RECOVERED: {target_path}")
                            live_log_entry(
                                excel_sheet_name,
                                upload_date,
                                display_title,
                                target_filename,
                                str(target_path.resolve()),
                                STATUS_NEW,
                                category=category,
                                division=division,
                            )
                            recovered = True
                            break

            except SiteAccessBlocked:
                raise

            except Exception as e:
                print(f"Retry attempt {attempt} error: {e}")

            time.sleep(attempt * 2)

        if not recovered:

            print(f"STILL FAILED: {filename}")

            # Log it as "Not Downloaded" now that every attempt --
            # including this final retry pass -- has been used up.
            live_log_entry(
                excel_sheet_name,
                upload_date,
                display_title,
                target_filename,
                "",
                STATUS_FAILED,
                category=category,
                division=division,
            )

            still_failed.append(
                (section_label, pdf_url, filename, save_folder, upload_date, excel_title, category, division, open_in_tab, excel_sheet_name)
            )

    if still_failed:
        print(f"\n{len(still_failed)} PDF(s) could NOT be downloaded even after the final retry pass:")
        for section_label, pdf_url, filename, save_folder, upload_date, excel_title, category, division, open_in_tab, excel_sheet_name in still_failed:
            print(f"  - [{section_label}] {filename}")
            print(f"      {pdf_url}")
            print(f"      -> {save_folder}")
    else:
        print("\nAll previously failed PDFs were recovered on retry.")


# ============================================================
# SCAN THE HAZARDOUS WASTE RULES PAGE FOR BOTH AMENDMENT LISTS
# ============================================================

def scan_hazardous_waste_lists(page):

    try:

        raw_lists = page.evaluate(
            """
            () => Array.from(
                document.querySelectorAll('ul.list-icn.link')
            ).map(ul =>
                Array.from(
                    ul.querySelectorAll('li > a[href]')
                ).map(a => ({
                    href: a.getAttribute('href') || '',
                    text: (a.innerText || '').trim()
                }))
            )
            """
        )

    except Exception as e:

        print(f"Could not scan the live page for amendment lists: {e}")
        raw_lists = []

    cleaned_lists = []

    # The live page keeps the two base rules outside the amendment
    # <ul> blocks. Include them so the folder count matches the site:
    # 2016 base rules + 7 amendments, and 2008 base rules + 4 amendments.
    try:
        base_rules = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]'))
                .filter(a => {
                    const href = a.getAttribute('href') || '';
                    const text = (a.innerText || '').toLowerCase();
                    return href.includes('displaypdf.php') &&
                        text.includes('rules') &&
                        !text.includes('amendment');
                })
                .map(a => ({
                    href: a.getAttribute('href') || '',
                    text: (a.innerText || '').trim()
                }))
            """
        )
    except Exception:
        base_rules = []

    for block in raw_lists:

        items = []
        seen = set()

        for item in block:

            href = item.get("href", "")

            if not href or href.strip() in ("#", "javascript:void(0)"):
                continue

            if href in seen:
                continue

            seen.add(href)
            items.append((href, item.get("text", "")))

        if items:
            cleaned_lists.append(items)

    # Prepend the two base-rule PDFs to their corresponding amendment lists.
    for item in base_rules:
        text = item.get("text", "")
        target = 0 if "2016" in text else 1
        if target < len(cleaned_lists):
            href = item.get("href", "")
            if href and not any(href == existing for existing, _ in cleaned_lists[target]):
                cleaned_lists[target].insert(0, (href, text))

    if len(cleaned_lists) >= 2:

        print(
            f"Live page scan: {len(cleaned_lists[0])} PDF(s) found in "
            f"the 2016 Rules amendment list, {len(cleaned_lists[1])} "
            "PDF(s) found in the 2008 Rules amendment list "
            "(this includes any newly added ones)."
        )

        return cleaned_lists[0], cleaned_lists[1]

    print(
        "Could not confidently find both amendment lists on the "
        "live page — falling back to the known hardcoded list."
    )

    return OTHER_WASTE_PDFS, HAZARDOUS_WASTE_PDFS


# ============================================================
# PROCESS A LIST OF (href, text) PDFS INTO A GIVEN FOLDER
# ============================================================

def process_pdf_list(page, context, pdf_list, save_folder, section_label):

    print("\n\n")
    print("##################################################")
    print(f"# {section_label}")
    print("##################################################")

    for number, (href, text) in enumerate(pdf_list, start=1):

        filename = clean_filename(text)
        upload_date = extract_upload_date_from_title(text)

        save_pdf_from_link(
            page,
            context,
            href,
            filename,
            save_folder,
            number,
            len(pdf_list),
            excel_sheet_name=HAZ_SHEET_NAME,
            section_label=section_label,
            upload_date=upload_date,
            excel_title=clean_display_title(text),
            open_in_tab=True,
        )


# ============================================================
# OFFICE ORDER / CIRCULAR PAGE
#
# "Show ... entries" dropdown set to "All", every row downloads
# regardless of Category, no tab-open step.
# ============================================================

def extract_circular_table_rows(page):
    """
    Reads the Office Order / Circular table by mapping each
    column NAME (Division, Category, Title, Date, ...) from the
    table's own <th> headers to its column index, instead of
    assuming a fixed td position. This is what makes Category and
    Division line up correctly with the right Title even if CPCB
    reorders or adds columns.

    Date gets an extra safety net: if no header label matches
    "date" at all (the live site may not literally call it that),
    the column is instead detected by CONTENT — whichever column's
    cells actually look like a date across a sample of rows — so
    the Date field doesn't come back blank just because the exact
    wording of that header differs from what was expected.

    The PDF link is found by searching the whole row for an
    <a href> rather than assuming which column it's in, so it
    doesn't matter where the "View"/download column sits.
    """

    global _PRINTED_CIRCULAR_HEADERS

    try:

        result = page.evaluate(
            """
            () => {
                const headerCells = Array.from(
                    document.querySelectorAll('#report thead th')
                );

                const headerLabels = headerCells.map(th => {
                    let label = th.getAttribute('aria-label') || th.innerText || '';
                    return label.split(':')[0].trim().toLowerCase();
                });

                // Exact label match first (e.g. 'date'); if that
                // isn't found, fall back to a substring match so
                // variants like 'upload date' or 'pdf upload date'
                // still resolve correctly instead of coming back
                // empty.
                const findColIdx = (name) => {
                    let idx = headerLabels.indexOf(name);
                    if (idx !== -1) return idx;
                    idx = headerLabels.findIndex(l => l.includes(name));
                    return idx;
                };

                const divisionIdx = findColIdx('division');
                const categoryIdx = findColIdx('category');
                const titleIdx = findColIdx('title');
                let dateIdx = findColIdx('date');

                const bodyRows = Array.from(
                    document.querySelectorAll('#report tbody tr')
                );

                // Fallback: detect the date column by CONTENT when
                // no header label matched "date" by name at all.
                if (dateIdx === -1 && bodyRows.length) {

                    const DATE_PATTERN =
                        /\\b\\d{1,2}[.\\-\\/]\\d{1,2}[.\\-\\/]\\d{2,4}\\b|\\b\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4}\\b/;

                    const sampleRows = bodyRows.slice(0, 15);
                    const maxCols = Math.max(
                        ...sampleRows.map(tr => tr.querySelectorAll('td').length)
                    );

                    let bestIdx = -1;
                    let bestScore = 0;

                    for (let c = 0; c < maxCols; c++) {

                        if (c === divisionIdx || c === categoryIdx || c === titleIdx) {
                            continue;
                        }

                        let score = 0;

                        for (const tr of sampleRows) {
                            const tds = tr.querySelectorAll('td');
                            const text = tds[c] ? tds[c].innerText.trim() : '';
                            if (DATE_PATTERN.test(text)) {
                                score++;
                            }
                        }

                        if (score > bestScore) {
                            bestScore = score;
                            bestIdx = c;
                        }
                    }

                    if (bestScore >= Math.ceil(sampleRows.length / 2)) {
                        dateIdx = bestIdx;
                    }
                }

                const getCell = (tds, idx) =>
                    (idx !== -1 && tds[idx]) ? tds[idx].innerText.trim() : '';

                return {
                    headers: headerLabels,
                    dateColumnResolvedByContent: dateIdx !== -1 && findColIdx('date') === -1,
                    rows: bodyRows.map(tr => {
                        const tds = tr.querySelectorAll('td');
                        const link = tr.querySelector('a[href]');
                        return {
                            division: getCell(tds, divisionIdx),
                            category: getCell(tds, categoryIdx),
                            title: getCell(tds, titleIdx),
                            date: getCell(tds, dateIdx),
                            href: link ? link.getAttribute('href') : ''
                        };
                    })
                };
            }
            """
        )

        headers = result.get("headers", [])
        rows = result.get("rows", [])

        if not _PRINTED_CIRCULAR_HEADERS:

            print(f"Circular table column headers detected: {headers}")

            if result.get("dateColumnResolvedByContent"):
                print(
                    "No header was literally labelled 'date' — the Date column "
                    "was instead detected by looking at which column's values "
                    "actually look like dates."
                )

            _PRINTED_CIRCULAR_HEADERS = True

    except Exception as e:
        print(f"Could not read table rows: {e}")
        rows = []

    return rows


def set_table_length_to_all(page):

    try:

        select_locator = page.locator(
            "#report_length select[name='report_length']"
        )

        if select_locator.count() == 0:
            print("Could not find the entries-per-page dropdown — leaving default paging.")
            return False

        select_locator.select_option("-1")

        page.wait_for_timeout(2000)

        print("Entries-per-page dropdown set to 'All'.")

        return True

    except Exception as e:
        print(f"Could not switch entries dropdown to 'All' (continuing with default paging): {e}")
        return False


def go_to_next_table_page(page):

    next_button = page.locator("#report_next")

    try:

        if next_button.count() == 0:
            return False

        classes = next_button.get_attribute("class") or ""

    except Exception:
        return False

    if "disabled" in classes:
        return False

    try:
        next_button.click()
    except Exception:
        return False

    page.wait_for_timeout(1500)

    return True


def truncate_to_byte_limit(text, max_bytes=180):

    while len(text.encode("utf-8")) > max_bytes:
        text = text[:-1]

    return text


def build_circular_filename(row):

    title = row.get("title", "").strip()
    date = row.get("date", "").strip()

    raw = f"{date} - {title}" if date else title
    raw = truncate_to_byte_limit(raw, max_bytes=180)

    return clean_filename(raw)


def process_circular_page(page, context):

    print("\n\n")
    print("##################################################")
    print("# EMPLOYEE CORNER — OFFICE ORDER / CIRCULAR")
    print("# (ALL categories, entries set to 'All')")
    print("##################################################")

    print(f"\nOpening: {CIRCULAR_PAGE_URL}")

    page.goto(CIRCULAR_PAGE_URL, wait_until="domcontentloaded", timeout=60000)

    page.wait_for_timeout(1500)
    page.bring_to_front()

    set_table_length_to_all(page)

    processed_count = 0
    table_page_number = 1
    seen_page_signatures = set()

    all_rows = []

    while True:

        print(f"\n--- Reading table page {table_page_number} ---")

        rows = extract_circular_table_rows(page)

        print(f"{len(rows)} row(s) on this table page.")

        page_signature = tuple(row.get("href", "") for row in rows)
        if page_signature in seen_page_signatures:
            print("Repeated table page detected; stopping pagination.")
            break
        seen_page_signatures.add(page_signature)

        all_rows.extend(rows)

        moved_to_next = go_to_next_table_page(page)

        if not moved_to_next:
            break

        table_page_number += 1

    seen_hrefs = set()
    rows_to_download = []

    for row in all_rows:

        href = row.get("href")

        if not href or href in seen_hrefs:
            continue

        seen_hrefs.add(href)
        rows_to_download.append(row)

    total = len(rows_to_download)

    print(f"\n{total} total row(s) found across all categories — downloading all of them.")

    for row in rows_to_download:

        processed_count += 1

        filename = build_circular_filename(row)

        save_pdf_from_link(
            page,
            context,
            row["href"],
            filename,
            CIRCULARS_FOLDER,
            processed_count,
            total,
            excel_sheet_name=CIRC_SHEET_NAME,
            section_label="Circulars",
            upload_date=row.get("date", ""),
            excel_title=row.get("title", ""),
            open_in_tab=False,
            category=row.get("category", ""),
            division=row.get("division", ""),
        )

    print(f"\nDone — saved {processed_count} PDF(s) from the Circular page (all categories).")


# ============================================================
# DOWNLOAD SUMMARY — TODAY vs YESTERDAY (console only)
# ============================================================

def print_download_summary():

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    today_count = 0
    yesterday_count = 0
    earlier_count = 0

    for pdf_path in ROOT_FOLDER.rglob("*.pdf"):

        try:
            saved_date = datetime.fromtimestamp(pdf_path.stat().st_mtime).date()
        except Exception:
            continue

        if saved_date == today:
            today_count += 1
        elif saved_date == yesterday:
            yesterday_count += 1
        else:
            earlier_count += 1

    total = today_count + yesterday_count + earlier_count

    print("\n")
    print("==================================================")
    print("DOWNLOAD SUMMARY (by file save date)")
    print("==================================================")
    print(f"Downloaded today      ({today}):     {today_count} PDF(s)")
    print(f"Downloaded yesterday  ({yesterday}): {yesterday_count} PDF(s)")

    if earlier_count:
        print(f"Downloaded earlier than that:  {earlier_count} PDF(s)")

    print(f"Total PDFs currently saved: {total}")


# ============================================================
# MAIN
# ============================================================

def main():

    print("\n")
    print("==================================================")
    print("CPCB — HAZARDOUS WASTE RULES + CIRCULAR PAGE AUTOMATION")
    print("==================================================")

    print(f"\nMain folder:\n{ROOT_FOLDER}")

    LOGGED_ENTRIES.update(load_logged_entries_from_excel())

    print(f"Loaded {len(LOGGED_ENTRIES)} already-logged entrie(s) from the existing Excel file (if any).")

    init_excel_workbook()

    print(f"Excel log ready (live tracking): {EXCEL_LOG_PATH}")

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=False, args=["--start-maximized"])

        context = browser.new_context(no_viewport=True, accept_downloads=True)

        page = context.new_page()

        # ====================================================
        # HAZARDOUS WASTE RULES PAGE
        # ====================================================

        print(f"\nOpening: {HAZARDOUS_WASTE_PAGE_URL}")

        page.goto(HAZARDOUS_WASTE_PAGE_URL, wait_until="domcontentloaded", timeout=60000)

        page.wait_for_timeout(1000)

        print("Page opened successfully.")

        other_waste_items, hazardous_waste_items = scan_hazardous_waste_lists(page)

        process_pdf_list(
            page,
            context,
            other_waste_items,
            OTHER_WASTE_FOLDER,
            "Other waste (2016 Rules amendments)"
        )

        process_pdf_list(
            page,
            context,
            hazardous_waste_items,
            HAZARDOUS_WASTE_FOLDER,
            "Hazardous waste (2008 Rules amendments)"
        )

        # ====================================================
        # EMPLOYEE CORNER — OFFICE ORDER / CIRCULAR
        # ====================================================

        process_circular_page(page, context)

        # ====================================================
        # FINAL RETRY PASS
        # ====================================================

        retry_failed_downloads(context)

        # ====================================================
        # FINAL SUMMARY
        # ====================================================

        print("\n")
        print("==================================================")
        print("ALL PROCESSING COMPLETED")
        print("==================================================")

        print("\nFOLDER STRUCTURE:\n")
        print("CPCB/")
        print("├── Hazardous waste Rules/")
        print("│   ├── Other waste/")
        print("│   └── Hazardous waste/")
        print("└── Circulars/")

        print(f"\nExcel log (already up to date — live tracked): {EXCEL_LOG_PATH}")
        print(f"  Sheet 1: {HAZ_SHEET_NAME}")
        print(f"  Sheet 2: {CIRC_SHEET_NAME}")

        print_download_summary()

        print("\nClosing Chrome...")

        browser.close()

        print("Chrome closed. All done.")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    guarded_main(main)
    
