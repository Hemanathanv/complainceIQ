

from pathlib import Path
from urllib.parse import urljoin
import re
import time

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://cercind.gov.in/"

HOME_URL = "https://cercind.gov.in/index-en.html"
ACTS_URL = "https://cercind.gov.in/electricity-Act.html"
MOP_RULES_URL = "https://cercind.gov.in/MoP_Rules.html"


# ============================================================
# FOLDERS
# ============================================================

PROJECT_FOLDER = Path(__file__).resolve().parent

ELECTRICITY_FOLDER = PROJECT_FOLDER / "electricity"

ACTS_FOLDER = ELECTRICITY_FOLDER / "acts_pdfs"
RULES_FOLDER = ELECTRICITY_FOLDER / "rules_pdfs"

ACTS_FOLDER.mkdir(parents=True, exist_ok=True)
RULES_FOLDER.mkdir(parents=True, exist_ok=True)


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
            "Electricity Act, 2003",
            "act/Act-with-amendment.pdf"
        ),

        (
            "Electricity (Amendment) Act, 2003",
            "act/Electricity_Amendment_Act-2004.pdf"
        ),

        (
            "Electricity (Amendment) Act, 2007",
            "act/Electricity_Act_2007.pdf"
        ),

        (
            "Energy Conservation Act, 2001",
            "act/ecact2001.pdf"
        ),

        (
            "Energy Conservation (Amendment) Act, 2022",
            "act/The_Energy_Conservation_Amendment_Act_2022.pdf"
        ),

        (
            "Electricity Regulatory Commissions Act, 1998",
            "act/ElectReguCommiAct1998.pdf"
        ),

        (
            "Electricity (Supply) Act, 1948",
            "act/ElectSupplyAct1948.pdf"
        ),

        (
            "Indian Electricity Act, 1910",
            "act/IEA1910.pdf"
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

                if output_file.exists():

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
# MAIN
# ============================================================

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
                headless=False,
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
                headless=False,
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

        download_acts(
            page,
            context
        )

        # ====================================================
        # STEP 2
        # ====================================================

        download_mop_rules(
            page,
            context
        )

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
