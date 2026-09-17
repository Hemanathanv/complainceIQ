

from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse
import os
import re
import time
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://cercind.gov.in/"

HOME_URL = "https://cercind.gov.in/index-en.html"
ACTS_URL = "https://cercind.gov.in/electricity-Act.html"
MOP_RULES_URL = "https://cercind.gov.in/MoP_Rules.html"

# The requested Orders/ROPs scope ends at ROPs-Prior to 2026.
ORDERS_ROP_TV_SECTIONS = [
    ("ROPs-2026", "https://cercind.gov.in/recent_rops.html"),
    ("Orders-2026", "https://cercind.gov.in/recent_orders.html"),
    ("Orders-Prior to 2026", "https://cercind.gov.in/orders.html"),
    ("ROPs-Prior to 2026", "https://cercind.gov.in/record_proceeding.html"),
]

POLICIES_URL = "https://cercind.gov.in/pol_und_act.html"
REGULATIONS_SCOPE = [
    ("Individual", "Current", "https://cercind.gov.in/Current_reg.html"),
    ("Individual", "Repealed", "https://cercind.gov.in/Repeal_Reg.html"),
    ("Consolidated", "Amendments upto July 2016 Incorporated", "https://cercind.gov.in/updated_consolidated_reg1.html"),
    ("Consolidated", "Amendments upto May 2010 Incorporated", "https://cercind.gov.in/updated_consolidated_reg2.html"),
]


# ============================================================
# FOLDERS
# ============================================================

PROJECT_FOLDER = Path(__file__).resolve().parent
ENV_FILE = PROJECT_FOLDER / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))

DOWNLOAD_BASE = Path(os.environ.get("DOWNLOAD_BASE_PATH", PROJECT_FOLDER))

ELECTRICITY_FOLDER = DOWNLOAD_BASE / os.environ.get("DOWNLOAD_PROJECT_FOLDER", "CERC")

ACTS_POLICIES_FOLDER = ELECTRICITY_FOLDER
ACTS_FOLDER = ELECTRICITY_FOLDER / "act"
POLICIES_FOLDER = ELECTRICITY_FOLDER / "policy"
RULES_FOLDER = ELECTRICITY_FOLDER / "Rules"
MOP_RULES_FOLDER = RULES_FOLDER / "MOP_Rules"
NOTIFICATIONS_FOLDER = ELECTRICITY_FOLDER / "Notifications"
REGULATIONS_FOLDER = ELECTRICITY_FOLDER / "Regulations"
ORDERS_ROP_TV_FOLDER = ELECTRICITY_FOLDER / "Orders"
AUDIT_XLSX = ELECTRICITY_FOLDER / "CERC_Audit.xlsx"

# Optional controlled test mode. Default is full crawl. Set CERC_TEST_LIMIT
# to a positive number to process only that many records per listing page.
TEST_LIMIT = int(os.environ.get("CERC_TEST_LIMIT", "0") or 0)
HEADLESS = os.environ.get("CERC_HEADLESS", "0") == "1"

ACTS_FOLDER.mkdir(parents=True, exist_ok=True)
POLICIES_FOLDER.mkdir(parents=True, exist_ok=True)
RULES_FOLDER.mkdir(parents=True, exist_ok=True)
MOP_RULES_FOLDER.mkdir(parents=True, exist_ok=True)
REGULATIONS_FOLDER.mkdir(parents=True, exist_ok=True)

SCOPE_AUDIT_ROWS = []


# ============================================================
# CLEAN FILE NAME
# ============================================================

def clean_filename(name):

    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name)

    return name.strip()


# ============================================================
# OPEN PAGE SAFELY
# ============================================================

def open_page(page, url, wait_seconds=3):

    print()
    print("=" * 60)
    print("OPENING PAGE")
    print("=" * 60)
    print(url)

    for attempt in range(1, 6):

        try:

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=90000
            )

            page.wait_for_timeout(
                wait_seconds * 1000
            )

            print("Page opened successfully.")

            return True

        except Exception as e:

            print(
                f"Attempt {attempt}/5 failed."
            )

            if attempt < 5:

                print(
                    "Retrying in 3 seconds..."
                )

                time.sleep(3)

            else:

                print(
                    "Could not open page."
                )

                print(e)

    return False


# ============================================================
# DIRECT PDF DOWNLOAD
#
# IMPORTANT:
# PDF WILL NOT OPEN IN CHROME
# ============================================================

def download_pdf(context, pdf_url, output_file):

    if output_file.exists():

        print(
            "SKIP:",
            output_file.name
        )

        return "skipped"

    try:

        print(
            "Downloading:",
            output_file.name
        )

        response = context.request.get(
            pdf_url,
            timeout=90000
        )

        if not response.ok:

            print(
                "HTTP ERROR:",
                response.status
            )

            return "failed"

        data = response.body()

        if not data:

            print(
                "Empty response."
            )

            return "failed"

        # Check PDF
        if not data.startswith(b"%PDF"):

            print(
                "ERROR: Response is not a PDF."
            )

            return "failed"

        output_file.write_bytes(data)

        print(
            "DOWNLOADED:",
            output_file.name
        )

        return "downloaded"

    except Exception as e:

        print(
            "Download error:",
            e
        )

        return "failed"


# ============================================================
# STEP 1
# ACTS
# ============================================================

def add_scope_audit(section, subsection, sno, title, date_value="", source_page_url="",
                    download_url="", local_file="", status="", actual_size="",
                    gazette_or_petition="", event_date="", site_category="", error=""):
    SCOPE_AUDIT_ROWS.append({
        "timestamp": datetime.now(),
        "section": section,
        "subsection": subsection,
        "sno": sno,
        "title": title,
        "gazette_or_petition": gazette_or_petition,
        "event_date": event_date,
        "date_value": date_value,
        "site_category": site_category,
        "source_page_url": source_page_url,
        "download_url": download_url,
        "local_file": local_file,
        "type": "PDF" if download_url else "",
        "status": status,
        "actual_size": actual_size,
        "error": error,
    })

def download_acts(page, context):

    print()
    print("=" * 60)
    print("STEP 1 - ACTS & POLICIES -> ACTS")
    print("=" * 60)

    # --------------------------------------------------------
    # HOME
    # --------------------------------------------------------

    if not open_page(
        page,
        HOME_URL,
        4
    ):
        return

    print(
        "Home page opened."
    )

    # --------------------------------------------------------
    # ACTS & POLICIES
    #
    # Only hover.
    # No menu click.
    # --------------------------------------------------------

    print()
    print(
        "Opening Acts & Policies..."
    )

    try:

        acts_menu = page.locator(
            "a"
        ).filter(
            has_text=re.compile(
                r"Acts\s*&\s*Policies",
                re.IGNORECASE
            )
        ).first

        if acts_menu.count() > 0:

            acts_menu.hover()

            page.wait_for_timeout(
                3000
            )

            print(
                "Acts & Policies opened."
            )

    except Exception as e:

        print(
            "Acts menu warning:",
            e
        )

    # --------------------------------------------------------
    # DIRECT ACTS PAGE
    # --------------------------------------------------------

    print()
    print(
        "Opening Acts page..."
    )

    if not open_page(
        page,
        ACTS_URL,
        4
    ):
        return

    print(
        "Acts page opened."
    )

    # --------------------------------------------------------
    # 8 ACTS
    # --------------------------------------------------------

    acts = [
        (
            "Electricity Act 2003",
            "act/Act-with-amendment.pdf"
        )
    ]

    print()
    print("=" * 60)
    print("ACTS DOWNLOAD")
    print("=" * 60)

    downloaded = 0
    skipped = 0
    failed = 0

    for act_name, href in acts:

        print()
        print("-" * 60)

        print(
            "Act:",
            act_name
        )

        pdf_url = urljoin(
            BASE_URL,
            href
        )

        print(
            "PDF:",
            pdf_url
        )

        filename = (
            clean_filename(act_name)
            + ".pdf"
        )

        output_file = (
            ACTS_FOLDER / filename
        )

        result = download_pdf(
            context,
            pdf_url,
            output_file
        )

        if result == "downloaded":
            downloaded += 1

        elif result == "skipped":
            skipped += 1

        else:
            failed += 1

        add_scope_audit(
            "Acts & Policies", "Acts", "1", act_name,
            source_page_url=ACTS_URL, download_url=pdf_url,
            local_file=str(output_file) if output_file.exists() else "",
            status=result, actual_size=output_file.stat().st_size if output_file.exists() else "",
        )

    print()
    print("=" * 60)
    print("ACTS RESULT")
    print("=" * 60)

    print(
        "Downloaded :",
        downloaded
    )

    print(
        "Skipped    :",
        skipped
    )

    print(
        "Failed     :",
        failed
    )


# ============================================================
# STEP 1B - POLICIES
# ============================================================

def download_policies_scope(page, context):
    print()
    print("=" * 60)
    print("STEP 1B - POLICIES THROUGH TARIFF POLICY")
    print("=" * 60)
    if not open_page(page, POLICIES_URL, 3):
        return
    policies = [
        ("Rural Electrification Policy", "2018/whatsnew/REP.pdf"),
        ("Amendment to Tariff Policy (English)", "2018/whatsnew/Amendment_Tariff_Policy_2.pdf"),
        ("Amendment to Tariff Policy (Hindi)", "2018/whatsnew/Amendment_Tariff_Policy_Hindi_2.pdf"),
        ("Tariff Policy (English)", "2018/whatsnew/TPEng.pdf"),
        ("Tariff Policy (Hindi)", "2018/whatsnew/TPHindi.pdf"),
    ]
    for sno, (title, href) in enumerate(policies, 1):
        pdf_url = urljoin(BASE_URL, href)
        output_file = POLICIES_FOLDER / f"{clean_filename(title)}.pdf"
        status = download_pdf(context, pdf_url, output_file)
        add_scope_audit("Acts & Policies", "Policies Under the Act", sno, title,
                        source_page_url=POLICIES_URL, download_url=pdf_url,
                        local_file=str(output_file) if output_file.exists() else "",
                        status=status, actual_size=output_file.stat().st_size if output_file.exists() else "")


# ============================================================
# STEP 2
# RULES & REGULATIONS -> MOP RULES
# ============================================================

def download_mop_rules(page, context):

    print()
    print("=" * 60)
    print("STEP 2 - RULES & REGULATIONS -> MOP RULES")
    print("=" * 60)

    # --------------------------------------------------------
    # HOME
    # --------------------------------------------------------

    if not open_page(
        page,
        HOME_URL,
        3
    ):
        return

    print(
        "Home page opened."
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # We DO NOT click Rules & Regulations.
    # We DO NOT click MOP Rules from menu.
    #
    # Direct exact URL prevents unwanted New Delhi page.
    # --------------------------------------------------------

    print()
    print(
        "Opening exact MOP Rules page..."
    )

    if not open_page(
        page,
        MOP_RULES_URL,
        4
    ):
        return

    print(
        "MOP Rules page opened."
    )

    # --------------------------------------------------------
    # WAIT FOR TABLE
    # --------------------------------------------------------

    table = page.locator(
        "table#thetable"
    )

    try:

        table.wait_for(
            state="visible",
            timeout=30000
        )

    except Exception as e:

        print(
            "MOP Rules table not found."
        )

        print(e)

        return

    print(
        "MOP Rules table found."
    )

    # --------------------------------------------------------
    # GET ALL ROWS
    # --------------------------------------------------------

    rows = table.locator(
        "tbody > tr"
    )

    total_rows = rows.count()

    print()
    print("=" * 60)
    print("MOP RULES DOWNLOAD")
    print("=" * 60)

    print(
        "MOP Rules found:",
        total_rows - 1
    )

    downloaded = 0
    skipped = 0
    failed = 0

    # ========================================================
    # PROCESS ALL 7
    # ========================================================

    for i in range(
        1,
        total_rows
    ):

        try:

            row = rows.nth(i)

            cells = row.locator(
                "td"
            )

            if cells.count() < 2:
                continue

            # ------------------------------------------------
            # S.NO
            # ------------------------------------------------

            sno = (
                cells.nth(0)
                .inner_text()
                .strip()
            )

            # ------------------------------------------------
            # RULE NAME
            # ------------------------------------------------

            rule_cell = cells.nth(1)

            span = rule_cell.locator(
                "span.style1"
            )

            if span.count() > 0:

                rule_name = (
                    span.first
                    .inner_text()
                    .strip()
                )

            else:

                rule_name = (
                    rule_cell
                    .inner_text()
                    .strip()
                    .split("\n")[0]
                )

            if rule_name not in {
                "Electricity (Amendment) Rules, 2025",
                "Electricity Rules, 2005",
            }:
                print("OUT OF 28-ACT SCOPE:", rule_name)
                continue

            # ------------------------------------------------
            # NOTIFICATION LINK
            # ------------------------------------------------

            link = rule_cell.locator(
                "a"
            ).first

            if link.count() == 0:

                print(
                    f"S.No {sno}: Notification not found."
                )

                failed += 1
                continue

            href = link.get_attribute(
                "href"
            )

            if not href:

                failed += 1
                continue

            notification_url = urljoin(
                page.url,
                href
            )

            print()
            print("-" * 60)

            print(
                "S.No        :",
                sno
            )

            print(
                "Rule        :",
                rule_name
            )

            print(
                "Notification:",
                notification_url
            )

            # ------------------------------------------------
            # OUTPUT FILE
            # ------------------------------------------------

            filename = (
                clean_filename(rule_name)
                + ".pdf"
            )

            output_file = (
                RULES_FOLDER / filename
            )
            notification_file = NOTIFICATIONS_FOLDER / filename

            # =================================================
            # ROW 1-6
            # DIRECT PDF
            #
            # Browser WILL NOT open.
            # =================================================

            if re.search(
                r"\.pdf(?:$|\?)",
                href,
                re.IGNORECASE
            ):

                result = download_pdf(
                    context,
                    notification_url,
                    output_file
                )

                download_pdf(
                    context,
                    notification_url,
                    notification_file
                )

                if result == "downloaded":
                    downloaded += 1

                elif result == "skipped":
                    skipped += 1

                else:
                    failed += 1

            # =================================================
            # ROW 7
            #
            # order-8.htm
            #
            # Convert HTML notification to PDF.
            # =================================================

            else:

                if output_file.exists() and notification_file.exists():

                    print(
                        "SKIP:",
                        output_file.name
                    )

                    skipped += 1
                    continue

                print(
                    "HTML notification detected."
                )

                notification_page = None

                try:

                    # Create temporary page
                    notification_page = (
                        context.new_page()
                    )

                    # ------------------------------------------------
                    # Disable popups on this temporary page.
                    # ------------------------------------------------

                    notification_page.add_init_script(
                        """
                        window.open = function() {
                            return null;
                        };
                        """
                    )

                    if not open_page(
                        notification_page,
                        notification_url,
                        2
                    ):

                        failed += 1
                        continue

                    notification_page.wait_for_timeout(
                        2000
                    )

                    notification_page.pdf(
                        path=str(output_file),
                        format="A4",
                        print_background=True,
                        margin={
                            "top": "10mm",
                            "bottom": "10mm",
                            "left": "10mm",
                            "right": "10mm"
                        }
                    )

                    notification_page.pdf(
                        path=str(notification_file),
                        format="A4",
                        print_background=True,
                        margin={
                            "top": "10mm",
                            "bottom": "10mm",
                            "left": "10mm",
                            "right": "10mm"
                        }
                    )

                    print(
                        "DOWNLOADED:",
                        output_file.name
                    )

                    downloaded += 1

                except Exception as e:

                    print(
                        "HTML PDF error:",
                        e
                    )

                    failed += 1

                finally:

                    # IMPORTANT:
                    # Close temporary notification page
                    # immediately.
                    if notification_page:

                        try:
                            notification_page.close()
                        except:
                            pass

        except Exception as e:

            print(
                f"Row {i} error:",
                e
            )

            failed += 1

    # ========================================================
    # RESULT
    # ========================================================

    print()
    print("=" * 60)
    print("MOP RULES RESULT")
    print("=" * 60)

    print(
        "Downloaded :",
        downloaded
    )

    print(
        "Skipped    :",
        skipped
    )

    print(
        "Failed     :",
        failed
    )


# ============================================================
# STEP 2B - ALL MOP RULES
# ============================================================

def download_mop_rules_scope(page, context):
    print()
    print("=" * 60)
    print("STEP 2B - MOP RULES (ALL 7)")
    print("=" * 60)
    if not open_page(page, MOP_RULES_URL, 3):
        return
    table = page.locator("table#thetable")
    try:
        table.wait_for(state="visible", timeout=30000)
    except Exception as e:
        print("MOP Rules table not found:", e)
        return
    rows = table.locator("tbody > tr")
    for index in range(1, rows.count()):
        row = rows.nth(index)
        cells = row.locator("td")
        if cells.count() < 4:
            continue
        sno = normalise_text(cells.nth(0).inner_text())
        title = normalise_text(cells.nth(1).inner_text()).split("\n")[0]
        gazette_no = normalise_text(cells.nth(2).inner_text())
        gazette_date = normalise_text(cells.nth(3).inner_text())
        link = row.locator("a[href]").first
        href = link.get_attribute("href") if link.count() else ""
        if not href:
            add_scope_audit("Rules", "MOP Rules", sno, title, date_value=gazette_date,
                            gazette_or_petition=gazette_no, source_page_url=MOP_RULES_URL,
                            status="No PDF link")
            continue
        pdf_url = urljoin(MOP_RULES_URL, href)
        output_file = MOP_RULES_FOLDER / f"{clean_filename(title)}.pdf"
        status = download_pdf(context, pdf_url, output_file)
        add_scope_audit("Rules", "MOP Rules", sno, title, date_value=gazette_date,
                        gazette_or_petition=gazette_no, source_page_url=MOP_RULES_URL,
                        download_url=pdf_url, local_file=str(output_file) if output_file.exists() else "",
                        status=status, actual_size=output_file.stat().st_size if output_file.exists() else "")


# ============================================================
# STEP 3
# REGULATIONS, THEN ORDERS / ROPS
# ============================================================

def normalise_text(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def download_regulations_scope(page, context):
    print()
    print("=" * 60)
    print("STEP 3 - REGULATIONS")
    print("=" * 60)
    for category, subsection, source_url in REGULATIONS_SCOPE:
        if not open_page(page, source_url, 2):
            add_scope_audit("Regulations", subsection, "", "", source_page_url=source_url,
                            status="Page open failed", error="Navigation failed")
            continue
        tables = page.locator("table")
        processed = 0
        for table_index in range(tables.count()):
            table = tables.nth(table_index)
            if table.locator("table").count() > 0:
                continue
            rows = table.locator("tr")
            if rows.count() < 2:
                continue
            header_index = None
            headers = []
            for r in range(min(rows.count(), 5)):
                cells = rows.nth(r).locator("th, td")
                values = [normalise_text(cells.nth(i).inner_text()).lower() for i in range(cells.count())]
                if any("regulation" in value for value in values):
                    header_index, headers = r, values
                    break
            if header_index is None:
                continue

            def find_index(*needles):
                for i, header in enumerate(headers):
                    if any(needle in header for needle in needles):
                        return i
                return None

            sno_index = find_index("sl.no", "s.no", "sl no")
            title_index = find_index("regulations on", "regulation on")
            gazette_index = find_index("gazette no")
            date_index = find_index("date of notification", "gazette date")

            for r in range(header_index + 1, rows.count()):
                row = rows.nth(r)
                cells = row.locator("td")
                if cells.count() == 0:
                    continue
                values = [normalise_text(cells.nth(i).inner_text()) for i in range(cells.count())]

                def value_at(i):
                    return values[i] if i is not None and i < len(values) else ""

                title = value_at(title_index)
                if not title or title.lower() in {"regulations on", "regulation on"}:
                    continue
                links = row.locator("a[href]")
                pdf_links = []
                for li in range(links.count()):
                    href = links.nth(li).get_attribute("href")
                    if href and re.search(r"\.pdf(?:$|\?)", href, re.IGNORECASE):
                        pdf_links.append(urljoin(source_url, href))
                pdf_links = list(dict.fromkeys(pdf_links))
                if not pdf_links:
                    add_scope_audit("Regulations", subsection, value_at(sno_index), title,
                                    date_value=value_at(date_index), gazette_or_petition=value_at(gazette_index),
                                    source_page_url=source_url, status="No PDF link")
                    continue
                for doc_index, pdf_url in enumerate(pdf_links, 1):
                    folder = REGULATIONS_FOLDER / category / subsection
                    folder.mkdir(parents=True, exist_ok=True)
                    serial = clean_filename(value_at(sno_index) or str(processed + 1))
                    suffix = "" if doc_index == 1 else f" - Document {doc_index}"
                    output_file = folder / f"{serial} - {clean_filename(title)[:120]}{suffix}.pdf"
                    status = download_pdf(context, pdf_url, output_file)
                    add_scope_audit(
                        "Regulations", subsection, value_at(sno_index), title,
                        date_value=value_at(date_index), gazette_or_petition=value_at(gazette_index),
                        source_page_url=source_url, download_url=pdf_url,
                        local_file=str(output_file) if output_file.exists() else "", status=status,
                        actual_size=output_file.stat().st_size if output_file.exists() else "",
                    )
                    processed += 1
                    if TEST_LIMIT and processed >= TEST_LIMIT:
                        break
                if TEST_LIMIT and processed >= TEST_LIMIT:
                    break
            if TEST_LIMIT and processed >= TEST_LIMIT:
                break


def page_links(page, selector="a[href]"):
    links = []
    locator = page.locator(selector)
    for i in range(locator.count()):
        item = locator.nth(i)
        href = item.get_attribute("href")
        text = normalise_text(item.inner_text())
        if href:
            links.append((text, urljoin(page.url, href)))
    return links


def discover_year_pages(page, index_url):
    """Return year links from the Orders or ROP index page."""
    if not open_page(page, index_url, 2):
        return []
    result = []
    for text, href in page_links(page):
        if re.fullmatch(r"20\d{2}", text):
            result.append((text, href))
    return result


def discover_technical_validation_pages(page):
    """Return the utility-specific Technical Validation pages."""
    if not open_page(page, "https://cercind.gov.in/Technical_Validation.html", 2):
        return []
    result = []
    seen = set()
    for text, href in page_links(page):
        path = urlparse(href).path.lower()
        if "/tv" in path and href.lower() not in seen:
            seen.add(href.lower())
            result.append((text or Path(path).stem, href))
    return result


def extract_table_records(page, section, subsection, page_url):
    """Extract visible table rows and their document links from a CERC page."""
    records = []
    tables = page.locator("table")
    for table_index in range(tables.count()):
        table = tables.nth(table_index)
        # Older CERC pages use nested layout tables. Only leaf tables contain
        # one real listing; processing wrapper tables duplicates rows and can
        # mistake repeated headings for documents.
        if table.locator("table").count() > 0:
            continue
        rows = table.locator("tr")
        if rows.count() == 0:
            continue

        header_index = None
        headers = []
        for row_index in range(min(rows.count(), 5)):
            candidate = rows.nth(row_index).locator("th, td")
            values = [normalise_text(candidate.nth(i).inner_text()) for i in range(candidate.count())]
            lowered = [value.lower() for value in values]
            if any("subject" in value for value in lowered):
                header_index = row_index
                headers = lowered
                break

        if header_index is None:
            continue

        def index_containing(*needles):
            for index, header in enumerate(headers):
                if any(needle in header for needle in needles):
                    return index
            return None

        sno_index = index_containing("sl.no", "s.no", "sl no")
        petition_index = index_containing("petition no", "pet. no", "pet no")
        subject_index = index_containing("subject matter", "subject")
        event_date_index = index_containing("date of order", "date of hearing", "date")
        posted_date_index = index_containing("date of posting", "posting")
        category_index = index_containing("category")

        for row_index in range(header_index + 1, rows.count()):
            row = rows.nth(row_index)
            cells = row.locator("td")
            if cells.count() == 0:
                continue

            values = [normalise_text(cells.nth(i).inner_text()) for i in range(cells.count())]

            def value_at(index):
                return values[index] if index is not None and index < len(values) else ""

            subject = value_at(subject_index)
            if not subject:
                continue
            if subject.lower() in {"subject", "subject matter"} or "date of hearing" in subject.lower():
                continue

            links = []
            anchors = row.locator("a[href]")
            for link_index in range(anchors.count()):
                anchor = anchors.nth(link_index)
                href = anchor.get_attribute("href")
                if href and not href.startswith("#"):
                    links.append(urljoin(page_url, href))

            records.append({
                "sno": value_at(sno_index),
                "petition_no": value_at(petition_index),
                "subject": subject,
                "event_date": value_at(event_date_index),
                "posted_date": value_at(posted_date_index),
                "site_category": value_at(category_index),
                "section": section,
                "subsection": subsection,
                "source_page_url": page_url,
                "document_urls": list(dict.fromkeys(links)),
            })

            if TEST_LIMIT and len(records) >= TEST_LIMIT:
                return records
    return records


def extract_hearing_records(page, section, subsection, page_url):
    """Extract the PDF entries and displayed last-modified text from Hearing Schedule."""
    records = []
    for text, href in page_links(page):
        if not re.search(r"\.pdf(?:$|\?)", href, re.IGNORECASE):
            continue
        posted = ""
        match = re.search(r"Last modified:\s*(.+)$", text, re.IGNORECASE)
        if match:
            posted = normalise_text(match.group(1))
        records.append({
            "sno": str(len(records) + 1),
            "petition_no": "",
            "subject": text or Path(urlparse(href).path).stem,
            "event_date": "",
            "posted_date": posted,
            "site_category": "",
            "section": section,
            "subsection": subsection,
            "source_page_url": page_url,
            "document_urls": [href],
        })
        if TEST_LIMIT and len(records) >= TEST_LIMIT:
            break
    return records


def resolve_pdf_url(context, document_url):
    """Resolve a direct PDF or the first PDF link on a detail page."""
    if re.search(r"\.pdf(?:$|\?)", document_url, re.IGNORECASE):
        return document_url
    try:
        response = context.request.get(document_url, timeout=90000)
        if not response.ok:
            return ""
        html = response.text()
        candidates = re.findall(
            r"(?:href|src|data)=['\"]([^'\"]+\.pdf(?:\?[^'\"]*)?)['\"]",
            html,
            re.IGNORECASE,
        )
        if candidates:
            return urljoin(document_url, candidates[0])
    except Exception as e:
        print("Detail page PDF resolution error:", e)
    return ""


def order_output_file(record, document_url, document_index):
    section = clean_filename(record["subsection"] or record["section"])
    serial = clean_filename(record["sno"] or str(document_index))
    title = clean_filename(record["subject"] or Path(urlparse(document_url).path).stem)
    title = title[:120].rstrip()
    suffix = "" if document_index == 1 else f" - Document {document_index}"
    folder = ORDERS_ROP_TV_FOLDER / section
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{serial} - {title}{suffix}.pdf"


def process_order_records(page, context, records, url_cache):
    audit_rows = []
    downloaded = skipped = failed = no_pdf = 0
    for record in records:
        document_urls = record["document_urls"]
        if not document_urls:
            no_pdf += 1
            add_scope_audit("Orders/ROPs", record["subsection"], record["sno"], record["subject"],
                            event_date=record["event_date"], date_value=record["posted_date"],
                            gazette_or_petition=record["petition_no"], site_category=record["site_category"],
                            source_page_url=record["source_page_url"], status="No PDF link")
            audit_rows.append({**record, "download_url": "", "local_file": "", "download_status": "No PDF link", "actual_size": "", "error": ""})
            continue

        for document_index, source_document_url in enumerate(document_urls, 1):
            pdf_url = url_cache.get(source_document_url)
            if pdf_url is None:
                pdf_url = resolve_pdf_url(context, source_document_url)
                url_cache[source_document_url] = pdf_url
            output_file = order_output_file(record, pdf_url or source_document_url, document_index)
            status = "failed"
            error = ""
            actual_size = ""
            if not pdf_url:
                error = "No PDF URL found on detail page"
                failed += 1
            else:
                status = download_pdf(context, pdf_url, output_file)
                if status == "downloaded":
                    downloaded += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1
                if output_file.exists():
                    actual_size = output_file.stat().st_size
            audit_rows.append({
                **record,
                "download_url": pdf_url or source_document_url,
                "local_file": str(output_file) if output_file.exists() else "",
                "download_status": status,
                "actual_size": actual_size,
                "error": error,
            })
            add_scope_audit("Orders/ROPs", record["subsection"], record["sno"], record["subject"],
                            event_date=record["event_date"], date_value=record["posted_date"],
                            gazette_or_petition=record["petition_no"], site_category=record["site_category"],
                            source_page_url=record["source_page_url"], download_url=pdf_url or source_document_url,
                            local_file=str(output_file) if output_file.exists() else "", status=status,
                            actual_size=actual_size, error=error)
    return audit_rows, downloaded, skipped, failed, no_pdf


def download_orders_rop_tv(page, context):
    print()
    print("=" * 60)
    print("STEP 3 - ORDERS / ROPS / TECHNICAL VALIDATION")
    print("=" * 60)
    if TEST_LIMIT:
        print(f"TEST MODE: maximum {TEST_LIMIT} records per listing page")

    all_audit_rows = []
    url_cache = {}
    totals = {"downloaded": 0, "skipped": 0, "failed": 0, "no_pdf": 0}

    def process_page(page_url, section, subsection, mode="table"):
        if not open_page(page, page_url, 2):
            all_audit_rows.append({
                "sno": "", "petition_no": "", "subject": "", "event_date": "", "posted_date": "",
                "site_category": "", "section": section, "subsection": subsection,
                "source_page_url": page_url, "document_urls": [], "download_url": "", "local_file": "",
                "download_status": "Page open failed", "actual_size": "", "error": "Navigation failed",
            })
            totals["failed"] += 1
            return
        if mode == "hearing":
            records = extract_hearing_records(page, section, subsection, page_url)
        else:
            records = extract_table_records(page, section, subsection, page_url)
        rows, downloaded, skipped, failed, no_pdf = process_order_records(page, context, records, url_cache)
        all_audit_rows.extend(rows)
        totals["downloaded"] += downloaded
        totals["skipped"] += skipped
        totals["failed"] += failed
        totals["no_pdf"] += no_pdf
        print(f"{subsection}: {len(records)} rows, {len([r for r in rows if r['download_url']])} documents")

    # Current and prior-year Orders/ROPs pages.
    process_page("https://cercind.gov.in/recent_rops.html", "Orders/ROPs", "ROPs-2026")
    process_page("https://cercind.gov.in/recent_orders.html", "Orders/ROPs", "Orders-2026")
    for year, href in discover_year_pages(page, "https://cercind.gov.in/orders.html"):
        process_page(href, "Orders/ROPs", f"Orders-Prior to 2026 - {year}")
    for year, href in discover_year_pages(page, "https://cercind.gov.in/record_proceeding.html"):
        process_page(href, "Orders/ROPs", f"ROPs-Prior to 2026 - {year}")

    print()
    print("ORDERS / ROPS / TV RESULT")
    print("Downloaded :", totals["downloaded"])
    print("Skipped    :", totals["skipped"])
    print("Failed     :", totals["failed"])
    print("No PDF link:", totals["no_pdf"])
    return all_audit_rows


def write_orders_rop_audit(workbook, order_rows):
    sheet_name = "Orders ROP TV"
    sheet = workbook[sheet_name] if sheet_name in workbook.sheetnames else workbook.create_sheet(sheet_name)
    if sheet.max_row:
        sheet.delete_rows(1, sheet.max_row)
    headers = [
        "Timestamp", "S.N.", "Section", "Subsection", "Petition No.", "Subject",
        "Event Date", "Date Posted", "Site Category", "Source Page URL", "Download URL",
        "PDF File Path", "Download Status", "Actual File Size (bytes)", "Type", "Error",
    ]
    sheet.append(headers)
    timestamp = datetime.now()
    for row in order_rows:
        sheet.append([
            timestamp, row.get("sno", ""), row.get("section", ""), row.get("subsection", ""),
            row.get("petition_no", ""), row.get("subject", ""), row.get("event_date", ""),
            row.get("posted_date", ""), row.get("site_category", ""), row.get("source_page_url", ""),
            row.get("download_url", ""), row.get("local_file", ""), row.get("download_status", ""),
            row.get("actual_size", ""), "PDF" if row.get("download_url") else "", row.get("error", ""),
        ])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [20, 8, 20, 34, 24, 90, 18, 24, 18, 70, 80, 90, 18, 22, 10, 40]
    for col, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + col) if col <= 26 else "A"].width = width
    for cell in sheet["A"][1:]:
        cell.number_format = "yyyy-mm-dd hh:mm:ss"


def write_scope_audit(workbook):
    sheet_name = "CERC Scope Audit"
    sheet = workbook[sheet_name] if sheet_name in workbook.sheetnames else workbook.create_sheet(sheet_name)
    if sheet.max_row:
        sheet.delete_rows(1, sheet.max_row)
    headers = [
        "Timestamp", "Section", "Subsection", "S.N.", "Title / Subject",
        "Gazette No. / Petition No.", "Event Date", "Published / Gazette Date",
        "Site Category", "Source Page URL", "Download URL", "PDF File Path",
        "Type", "Download Status", "Actual File Size (bytes)", "Error",
    ]
    sheet.append(headers)
    for row in SCOPE_AUDIT_ROWS:
        sheet.append([
            row.get("timestamp"), row.get("section", ""), row.get("subsection", ""),
            row.get("sno", ""), row.get("title", ""), row.get("gazette_or_petition", ""),
            row.get("event_date", ""), row.get("date_value", ""), row.get("site_category", ""),
            row.get("source_page_url", ""), row.get("download_url", ""), row.get("local_file", ""),
            row.get("type", ""), row.get("status", ""), row.get("actual_size", ""), row.get("error", ""),
        ])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [20, 20, 42, 8, 100, 26, 18, 24, 18, 70, 80, 90, 10, 18, 22, 40]
    for col, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + col)].width = width
    for cell in sheet["A"][1:]:
        cell.number_format = "yyyy-mm-dd hh:mm:ss"

    # Dedicated audit sheet for each site subsection.
    grouped = {}
    for row in SCOPE_AUDIT_ROWS:
        grouped.setdefault(row.get("subsection") or "Other", []).append(row)
    for subsection, rows in grouped.items():
        name = re.sub(r"[\\/*?:\[\]]", "", subsection)[:31] or "Other"
        dedicated = workbook[name] if name in workbook.sheetnames else workbook.create_sheet(name)
        if dedicated.max_row:
            dedicated.delete_rows(1, dedicated.max_row)
        dedicated.append(headers)
        for row in rows:
            dedicated.append([
                row.get("timestamp"), row.get("section", ""), row.get("subsection", ""),
                row.get("sno", ""), row.get("title", ""), row.get("gazette_or_petition", ""),
                row.get("event_date", ""), row.get("date_value", ""), row.get("site_category", ""),
                row.get("source_page_url", ""), row.get("download_url", ""), row.get("local_file", ""),
                row.get("type", ""), row.get("status", ""), row.get("actual_size", ""), row.get("error", ""),
            ])
        for cell in dedicated[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        dedicated.freeze_panes = "A2"
        dedicated.auto_filter.ref = dedicated.dimensions


# ============================================================
# MAIN
# ============================================================

def write_audit(order_rows=None):
    headers = [
        "Timestamp", "S.N.", "Category", "Classification", "Subcategory",
        "Title", "Size", "Type", "Source URL", "Local File", "Download Status",
        "Actual File Size (bytes)", "Error"
    ]
    if AUDIT_XLSX.exists():
        workbook = load_workbook(AUDIT_XLSX)
        sheet = workbook["Audit"] if "Audit" in workbook.sheetnames else workbook.create_sheet("Audit")
        header_rows = [r for r in range(1, sheet.max_row + 1) if sheet.cell(r, 1).value == headers[0]]
        if header_rows:
            if header_rows[0] > 1:
                sheet.delete_rows(1, header_rows[0] - 1)
            for r in range(sheet.max_row, 1, -1):
                if sheet.cell(r, 1).value == headers[0]:
                    sheet.delete_rows(r)
        else:
            sheet.delete_rows(1, sheet.max_row)
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Audit"
    if sheet.max_row == 1 and sheet.cell(1, 1).value is None:
        sheet.delete_rows(1)
    if sheet.cell(1, 1).value is None:
        sheet.append(headers)
    timestamp = datetime.now()
    row_number = 0
    for folder, classification in [(ACTS_FOLDER, "Acts"), (RULES_FOLDER, "Rules"), (NOTIFICATIONS_FOLDER, "Notifications")]:
        for path in sorted(folder.glob("*.pdf")):
            row_number += 1
            sheet.append([timestamp, row_number, "CERC", classification, "Electricity Act 2003", path.stem, "N/A", "PDF", "", str(path), "Downloaded", path.stat().st_size, ""])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for col, width in enumerate([20, 8, 12, 18, 28, 70, 16, 10, 80, 80, 18, 22, 40], 1):
        sheet.column_dimensions[chr(64 + col)].width = width
    for cell in sheet["A"][1:]:
        cell.number_format = "yyyy-mm-dd hh:mm:ss"
    write_scope_audit(workbook)
    if order_rows:
        write_orders_rop_audit(workbook, order_rows)
    workbook.save(AUDIT_XLSX)
    print("Audit workbook:", AUDIT_XLSX)

def main():

    print()
    print("=" * 60)
    print("CERC ELECTRICITY AUTOMATION")
    print("=" * 60)

    print()
    print(
        "Acts folder:"
    )

    print(
        ACTS_FOLDER
    )

    print()
    print(
        "Rules folder:"
    )

    print(
        RULES_FOLDER
    )

    with sync_playwright() as p:

        # ----------------------------------------------------
        # GOOGLE CHROME
        # ----------------------------------------------------

        try:

            browser = p.chromium.launch(
                channel="chrome",
                headless=HEADLESS,
                args=[
                    "--start-maximized",
                    "--disable-popup-blocking",
                    "--disable-notifications",
                    "--disable-features=Translate"
                ]
            )

            print()
            print(
                "Google Chrome opened."
            )

        except Exception as e:

            print(
                "Google Chrome opening failed."
            )

            print(e)

            browser = p.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--start-maximized",
                    "--disable-popup-blocking",
                    "--disable-notifications"
                ]
            )

        # ----------------------------------------------------
        # CONTEXT
        # ----------------------------------------------------

        context = browser.new_context(
            accept_downloads=True,
            ignore_https_errors=True,
            viewport=None
        )

        page = context.new_page()

        page.set_default_timeout(
            30000
        )

        # ====================================================
        # STEP 1
        # ====================================================

        download_acts(page, context)
        download_policies_scope(page, context)
        download_mop_rules_scope(page, context)
        download_regulations_scope(page, context)
        # order_rows = download_orders_rop_tv(page, context)
        order_rows = []
        write_audit(order_rows)

        # ====================================================
        # COMPLETED
        # ====================================================

        print()
        print("=" * 60)
        print("AUTOMATION COMPLETED")
        print("=" * 60)

        print()
        print(
            "Acts folder:"
        )

        print(
            ACTS_FOLDER
        )

        print()
        print(
            "Rules folder:"
        )

        print(
            RULES_FOLDER
        )

        print()
        print(
            "Orders / ROPs / TV folder:"
        )

        print(
            ORDERS_ROP_TV_FOLDER
        )

        # ----------------------------------------------------
        # KEEP CHROME OPEN FOR 10 SECONDS
        # ----------------------------------------------------

        print()
        print(
            "Chrome will close in 10 seconds..."
        )

        time.sleep(10)

        # ----------------------------------------------------
        # CLOSE ALL OPEN PAGES
        # ----------------------------------------------------

        for browser_page in context.pages:

            try:

                if not browser_page.is_closed():

                    browser_page.close()

            except:
                pass

        browser.close()

        print()
        print(
            "Chrome closed."
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
