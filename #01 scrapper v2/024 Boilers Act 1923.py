"""DPIIT Acts and Policies downloader for Boilers Act 1923.

Execution order:
1. All paginated direct PDFs from Acts and Policies.
2. Extra DPIIT document URLs.
3. Explosives Section PDFs.
4. Equal Opportunity Policy PDF.

Run with: python "024 Boilers Act 1923.py"
"""
import os
import json
import random
import re
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ============================================================
# CONFIGURATION
# ============================================================

DPIIT_URL = "https://www.dpiit.gov.in/"
BASE_URL = "https://www.dpiit.gov.in"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, encoding="utf-8") as env_file:
        for line in env_file:
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))
DOWNLOAD_BASE = os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR)
BASE_FOLDER = os.path.join(DOWNLOAD_BASE, os.path.splitext(os.path.basename(__file__))[0])
RUNTIME_DIR = Path(BASE_FOLDER) / "logs"
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


async def guarded_main(entrypoint):
    global BLOCK_DETECTED
    for attempt in range(1, RESILIENCE_ATTEMPTS + 1):
        BLOCK_DETECTED = ""
        write_run_status("running", attempt=attempt)
        try:
            result = await entrypoint()
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
            await asyncio.sleep(BLOCK_COOLDOWN + random.uniform(0, 5))
        except Exception as error:
            write_run_status("error", error, attempt)
            if attempt == RESILIENCE_ATTEMPTS:
                raise
            await asyncio.sleep(min(120, 5 * (2 ** (attempt - 1))) + random.uniform(0, 2))

ACTS_FOLDER = os.path.join(
    BASE_FOLDER,
    "acts and policies"
)

os.makedirs(
    ACTS_FOLDER,
    exist_ok=True
)


# ============================================================
# CLEAN FILE NAME
# ============================================================

def clean_filename(name):

    name = re.sub(
        r'[<>:"/\\|?*]',
        '',
        name
    )

    name = re.sub(
        r'\s+',
        ' ',
        name
    ).strip()

    return name


# ============================================================
# COUNT PDF FILES
# ============================================================

def count_pdfs(folder):

    if not os.path.exists(folder):
        return 0

    return len([
        file
        for file in os.listdir(folder)
        if file.lower().endswith(".pdf")
    ])


# ============================================================
# DOWNLOAD PDF
# ============================================================

async def download_pdf(
    page,
    pdf_url,
    filepath
):

    try:

        await asyncio.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 0.35))
        response = await page.request.get(
            pdf_url,
            timeout=300000
        )

        check_response_status(response.status, pdf_url)

        if response.ok:

            with open(
                filepath,
                "wb"
            ) as f:

                f.write(
                    await response.body()
                )

            print(
                f"Downloaded: {os.path.basename(filepath)}"
            )

        else:

            print(
                f"Download failed: {pdf_url} "
                f"Status: {response.status}"
            )

    except SiteAccessBlocked:

        raise

    except Exception as e:

        print(
            f"Download error: {e}"
        )


# ============================================================
# OPEN ACTS AND POLICIES
# ============================================================

async def open_acts_policies(page):

    await page.goto(
        DPIIT_URL,
        wait_until="domcontentloaded",
        timeout=120000
    )

    await page.wait_for_timeout(
        3000
    )

    documents_button = page.get_by_role(
        "button",
        name="Documents"
    )

    await documents_button.click()

    await page.wait_for_timeout(
        1500
    )

    acts_policies = page.get_by_role(
        "menuitem",
        name="Acts and Policies"
    )

    await acts_policies.click()

    await page.wait_for_timeout(
        4000
    )


# ============================================================
# SET 50 RECORDS
# ============================================================

async def set_50_records(page):

    select = page.locator(
        "div.perPageField select"
    )

    if await select.count() > 0:

        await select.select_option(
            "50"
        )

        await page.wait_for_timeout(
            3000
        )

        print(
            "Records per page set to 50."
        )

    else:

        print(
            "50 records selector not found."
        )


async def download_acts_pages(page, folder):
    """Download every direct PDF on every Acts and Policies page."""

    os.makedirs(folder, exist_ok=True)
    page_number = 1
    seen_pages = set()
    total = 0

    while True:
        rows = page.locator("div[role='row'].announcementbox")
        row_count = await rows.count()
        signature = page.url + "|" + str(row_count)

        if signature in seen_pages:
            break
        seen_pages.add(signature)

        page_pdf_count = 0
        for i in range(row_count):
            row = rows.nth(i)
            pdf_link = row.locator("a[type='pdf']").first
            if await pdf_link.count() == 0:
                continue

            pdf_url = await pdf_link.get_attribute("href")
            if not pdf_url:
                continue
            pdf_url = (
                BASE_URL.rstrip("/") + pdf_url
                if pdf_url.startswith("/")
                else pdf_url
            )

            title_locator = row.locator("p.mb-0").first
            date_locator = row.locator("small.ptype.mb-0").first
            title = (await title_locator.inner_text()).strip() if await title_locator.count() else "DPIIT Document"
            date = (await date_locator.inner_text()).strip() if await date_locator.count() else ""
            pdf_name = pdf_url.split("/")[-1].split("?")[0]
            filename = clean_filename(f"{title} - {date} - {pdf_name}")
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"

            filepath = os.path.join(folder, filename)
            if not os.path.exists(filepath):
                await download_pdf(page, pdf_url, filepath)
            page_pdf_count += 1

        total += page_pdf_count
        print(f"Acts and Policies page {page_number}: {page_pdf_count} PDF(s)")

        next_button = page.locator("button[aria-label='Next page']").first
        if await next_button.count() == 0 or await next_button.is_disabled():
            break

        await next_button.click()
        await page.wait_for_timeout(3000)
        page_number += 1

    print(f"Acts and Policies total direct PDFs: {total}")
    return total


# ============================================================
# DOWNLOAD SECTION
# ============================================================

async def download_section(
    page,
    row_title,
    folder,
    section_name
):

    print(
        f"\nFinding {section_name}..."
    )

    row = page.locator(
        "div[role='row'].announcementbox"
    ).filter(
        has_text=row_title
    ).first

    if await row.count() == 0:

        print(
            f"{section_name} row not found."
        )

        return

    print(
        f"{section_name} row found."
    )

    view_all = row.locator(
        "a"
    ).first

    if await view_all.count() == 0:

        print(
            f"{section_name} View All link not found."
        )

        return

    print(
        "Clicking View All..."
    )

    await view_all.click()

    await page.wait_for_timeout(
        4000
    )

    print(
        f"{section_name} URL:",
        page.url
    )

    os.makedirs(
        folder,
        exist_ok=True
    )

    rows = page.locator(
        "div[role='row'].announcementbox"
    )

    count = await rows.count()

    print(
        f"{section_name} records found: {count}"
    )

    for i in range(count):

        current_row = rows.nth(i)

        pdf_link = current_row.locator(
            "a[type='pdf']"
        ).first

        if await pdf_link.count() == 0:
            continue

        title_locator = current_row.locator(
            "p.mb-0"
        ).first

        date_locator = current_row.locator(
            "small.ptype.mb-0"
        ).first

        title = (
            await title_locator.inner_text()
            if await title_locator.count() > 0
            else f"{section_name} Document"
        )

        date = (
            await date_locator.inner_text()
            if await date_locator.count() > 0
            else ""
        )

        pdf_url = await pdf_link.get_attribute(
            "href"
        )

        if not pdf_url:
            continue

        if pdf_url.startswith("/"):

            pdf_url = (
                BASE_URL.rstrip("/")
                + pdf_url
            )

        pdf_name = (
            pdf_url
            .split("/")[-1]
            .split("?")[0]
        )

        filename = clean_filename(
            f"{title} - {date} - {pdf_name}"
        )

        if not filename.lower().endswith(
            ".pdf"
        ):

            filename += ".pdf"

        filepath = os.path.join(
            folder,
            filename
        )

        if os.path.exists(filepath):

            print(
                f"Already exists: {filename}"
            )

            continue

        await download_pdf(
            page,
            pdf_url,
            filepath
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=False
        )

        context = await browser.new_context()

        page = await context.new_page()


        # ====================================================
        # ACTS AND POLICIES
        # ====================================================

        print("\n")
        print("==============================================")
        print("ACTS AND POLICIES")
        print("==============================================")

        await open_acts_policies(
            page
        )

        await set_50_records(
            page
        )

        await download_acts_pages(
            page,
            ACTS_FOLDER
        )


        # ====================================================
        # EXTRA DOCUMENTS
        # ====================================================

        print("\n")
        print("==============================================")
        print("EXTRA DOCUMENTS")
        print("==============================================")

        extra_documents = [

            (
                "Department of Defence, Ministry of Defence",
                "https://www.dpiit.gov.in/documents/acts-and-policies/department-of-defence-ministry-of-defence-QzN2ATNtQWa"
            ),

            (
                "Notification regarding amendment to Calcium Carbide Rules, 1987",
                "https://www.dpiit.gov.in/static/uploads/2025/06/20e2572687a4d2f6463955dc2c655e4e.pdf"
            ),

            (
                "Department of Science and Technology",
                "https://www.dpiit.gov.in/static/uploads/2025/07/b343eed1e451960757fcfe753981ab71.pdf"
            )
        ]

        for title, url in extra_documents:

            if url.lower().endswith(".pdf"):

                pdf_url = url

            else:

                pdf_page = await context.new_page()

                await pdf_page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=120000
                )

                await pdf_page.wait_for_timeout(
                    3000
                )

                pdf_link = pdf_page.locator(
                    "a[type='pdf']"
                ).first

                if await pdf_link.count() == 0:

                    print(
                        f"PDF not found: {title}"
                    )

                    await pdf_page.close()

                    continue

                pdf_url = await pdf_link.get_attribute(
                    "href"
                )

                await pdf_page.close()

                if not pdf_url:
                    continue

            pdf_name = (
                pdf_url
                .split("/")[-1]
                .split("?")[0]
            )

            filename = clean_filename(
                f"{title} - {pdf_name}"
            )

            if not filename.lower().endswith(
                ".pdf"
            ):

                filename += ".pdf"

            filepath = os.path.join(
                ACTS_FOLDER,
                filename
            )

            if os.path.exists(filepath):

                print(
                    f"Already exists: {filename}"
                )

                continue

            await download_pdf(
                page,
                pdf_url,
                filepath
            )


        # ====================================================
        # EXPLOSIVES SECTION
        # ====================================================

        explosives_folder = os.path.join(
            ACTS_FOLDER,
            "Explosives Section"
        )

        os.makedirs(
            explosives_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("EXPLOSIVES SECTION")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Explosives Section",
            explosives_folder,
            "Explosives Section"
        )

        print(
            "Explosives Section:",
            count_pdfs(explosives_folder)
        )


        # ====================================================
        # EQUAL OPPORTUNITY POLICY
        # ====================================================

        equal_folder = os.path.join(
            ACTS_FOLDER,
            "Equal Opportunity Policy"
        )

        os.makedirs(
            equal_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("EQUAL OPPORTUNITY POLICY")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        row = page.locator(
            "div[role='row'].announcementbox"
        ).filter(
            has_text="Equal Opportunity Policy for Persons with Disabilities"
        ).first

        if await row.count() > 0:

            print(
                "Equal Opportunity Policy row found."
            )

            view_all = row.locator(
                "a"
            ).first

            await view_all.click()

            await page.wait_for_timeout(
                4000
            )

            pdf_link = page.locator(
                "a[type='pdf']"
            ).first

            if await pdf_link.count() > 0:

                pdf_url = await pdf_link.get_attribute(
                    "href"
                )

                if pdf_url:

                    await asyncio.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 0.35))
                    response = await page.request.get(
                        pdf_url,
                        timeout=300000
                    )

                    check_response_status(response.status, pdf_url)

                    if response.ok:

                        filename = (
                            "Equal Opportunity Policy - "
                            "Grievance Redressal Officer.pdf"
                        )

                        filepath = os.path.join(
                            equal_folder,
                            filename
                        )

                        if not os.path.exists(
                            filepath
                        ):

                            with open(
                                filepath,
                                "wb"
                            ) as f:

                                f.write(
                                    await response.body()
                                )

                            print(
                                f"Downloaded: {filename}"
                            )


        # ====================================================
        # DEPARTMENT FOR PROMOTION OF INDUSTRY
        # AND INTERNAL TRADE
        # ====================================================

        dpiit_department_folder = os.path.join(
            ACTS_FOLDER,
            "Department for Promotion of Industry and Internal Trade"
        )

        os.makedirs(
            dpiit_department_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("DEPARTMENT FOR PROMOTION OF INDUSTRY")
        print("AND INTERNAL TRADE")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Department for Promotion of Industry and Internal Trade",
            dpiit_department_folder,
            "Department for Promotion of Industry and Internal Trade"
        )

        print(
            "Department for Promotion of Industry and Internal Trade:",
            count_pdfs(dpiit_department_folder)
        )


        # ====================================================
        # MINISTRY OF HEAVY INDUSTRIES AND
        # PUBLIC ENTERPRISES
        # ====================================================

        heavy_folder = os.path.join(
            ACTS_FOLDER,
            "Ministry of Heavy Industries and Public Enterprises"
        )

        os.makedirs(
            heavy_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("MINISTRY OF HEAVY INDUSTRIES")
        print("AND PUBLIC ENTERPRISES")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Ministry of Heavy Industries and Public Enterprises",
            heavy_folder,
            "Ministry of Heavy Industries and Public Enterprises"
        )

        print(
            "Ministry of Heavy Industries and Public Enterprises:",
            count_pdfs(heavy_folder)
        )


        # ====================================================
        # REGISTRATION GRANTED UNDER RULE 144(XI) GFR
        # ====================================================

        registration_folder = os.path.join(
            ACTS_FOLDER,
            "Registration Granted under Rule 144(xi) GFR"
        )

        os.makedirs(
            registration_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("REGISTRATION GRANTED UNDER RULE 144(XI) GFR")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Registration Granted under Rule 144(xi) GFR",
            registration_folder,
            "Registration Granted under Rule 144(xi) GFR"
        )

        print(
            "Registration Granted under Rule 144(xi) GFR:",
            count_pdfs(registration_folder)
        )


        # ====================================================
        # FORMS
        # ====================================================

        forms_folder = os.path.join(
            ACTS_FOLDER,
            "Forms"
        )

        os.makedirs(
            forms_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("FORMS")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Forms",
            forms_folder,
            "Forms"
        )

        print(
            "Forms:",
            count_pdfs(forms_folder)
        )


        # ====================================================
        # BOILERS ACT
        # ====================================================

        boilers_folder = os.path.join(
            ACTS_FOLDER,
            "Boilers Act"
        )

        os.makedirs(
            boilers_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("BOILERS ACT")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Boilers Act",
            boilers_folder,
            "Boilers Act"
        )

        print(
            "Boilers Act:",
            count_pdfs(boilers_folder)
        )


        # ====================================================
        # EXPLOSIVE ACTS
        # ====================================================

        explosive_acts_folder = os.path.join(
            ACTS_FOLDER,
            "Explosive Acts"
        )

        os.makedirs(
            explosive_acts_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("EXPLOSIVE ACTS")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Explosive Acts",
            explosive_acts_folder,
            "Explosive Acts"
        )

        print(
            "Explosive Acts:",
            count_pdfs(explosive_acts_folder)
        )


        # ====================================================
        # INDUSTRIAL POLICY
        # ====================================================

        industrial_folder = os.path.join(
            ACTS_FOLDER,
            "Industrial Policy"
        )

        os.makedirs(
            industrial_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("INDUSTRIAL POLICY")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Industrial Policy",
            industrial_folder,
            "Industrial Policy"
        )

        print(
            "Industrial Policy:",
            count_pdfs(industrial_folder)
        )


        # ====================================================
        # DEPARTMENT OF CHEMICALS AND PETROCHEMICALS
        # ====================================================

        chemicals_folder = os.path.join(
            ACTS_FOLDER,
            "Department of Chemicals and Petrochemicals"
        )

        os.makedirs(
            chemicals_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("DEPARTMENT OF CHEMICALS AND PETROCHEMICALS")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Department of Chemicals and Petrochemicals",
            chemicals_folder,
            "Department of Chemicals and Petrochemicals"
        )

        print(
            "Department of Chemicals and Petrochemicals:",
            count_pdfs(chemicals_folder)
        )


        # ====================================================
        # MINUTES - ATR OF STANDING COMMITTEE MEETING
        # ====================================================

        minutes_folder = os.path.join(
            ACTS_FOLDER,
            "Minutes - ATR of Standing Committee Meeting"
        )

        os.makedirs(
            minutes_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("MINUTES - ATR OF STANDING COMMITTEE MEETING")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Minutes / ATR of Standing Committee Meeting",
            minutes_folder,
            "Minutes - ATR of Standing Committee Meeting"
        )

        print(
            "Minutes - ATR:",
            count_pdfs(minutes_folder)
        )


        # ====================================================
        # DEPARTMENT OF TELECOMMUNICATIONS
        # ====================================================

        telecom_folder = os.path.join(
            ACTS_FOLDER,
            "Department of Telecommunications"
        )

        os.makedirs(
            telecom_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("DEPARTMENT OF TELECOMMUNICATIONS")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Department of Telecommunications",
            telecom_folder,
            "Department of Telecommunications"
        )

        print(
            "Department of Telecommunications:",
            count_pdfs(telecom_folder)
        )


        # ====================================================
        # DEPARTMENT OF PHARMACEUTICALS
        # ====================================================

        pharmaceuticals_folder = os.path.join(
            ACTS_FOLDER,
            "Department of Pharmaceuticals"
        )

        os.makedirs(
            pharmaceuticals_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("DEPARTMENT OF PHARMACEUTICALS")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Department of Pharmaceuticals",
            pharmaceuticals_folder,
            "Department of Pharmaceuticals"
        )

        print(
            "Department of Pharmaceuticals:",
            count_pdfs(pharmaceuticals_folder)
        )


        # ====================================================
        # MINISTRY OF RAILWAYS
        # ====================================================

        railways_folder = os.path.join(
            ACTS_FOLDER,
            "Ministry of Railways"
        )

        os.makedirs(
            railways_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("MINISTRY OF RAILWAYS")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Ministry of Railways",
            railways_folder,
            "Ministry of Railways"
        )

        print(
            "Ministry of Railways:",
            count_pdfs(railways_folder)
        )


        # ====================================================
        # MINISTRY OF POWER
        # ====================================================

        power_folder = os.path.join(
            ACTS_FOLDER,
            "Ministry of Power"
        )

        os.makedirs(
            power_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("MINISTRY OF POWER")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Ministry of Power",
            power_folder,
            "Ministry of Power"
        )

        print(
            "Ministry of Power:",
            count_pdfs(power_folder)
        )


        # ====================================================
        # MINISTRY OF SHIPPING
        # ====================================================

        shipping_folder = os.path.join(
            ACTS_FOLDER,
            "Ministry of Shipping"
        )

        os.makedirs(
            shipping_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("MINISTRY OF SHIPPING")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Ministry of Shipping",
            shipping_folder,
            "Ministry of Shipping"
        )

        print(
            "Ministry of Shipping:",
            count_pdfs(shipping_folder)
        )


        # ====================================================
        # POLICY GUIDELINES AND FORMS
        # ====================================================

        policy_folder = os.path.join(
            ACTS_FOLDER,
            "Policy Guidelines and Forms"
        )

        os.makedirs(
            policy_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("POLICY GUIDELINES AND FORMS")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Policy Guidelines and Forms",
            policy_folder,
            "Policy Guidelines and Forms"
        )

        print(
            "Policy Guidelines and Forms:",
            count_pdfs(policy_folder)
        )


        # ====================================================
        # MINISTRY OF MINES
        # ====================================================

        mines_folder = os.path.join(
            ACTS_FOLDER,
            "Ministry of Mines"
        )

        os.makedirs(
            mines_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("MINISTRY OF MINES")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Ministry of Mines",
            mines_folder,
            "Ministry of Mines"
        )

        print(
            "Ministry of Mines:",
            count_pdfs(mines_folder)
        )


        # ====================================================
        # GLOBAL TENDER ENQUIRY
        # ====================================================

        global_tender_folder = os.path.join(
            ACTS_FOLDER,
            "Global Tender Enquiry"
        )

        os.makedirs(
            global_tender_folder,
            exist_ok=True
        )

        print("\n")
        print("==============================================")
        print("GLOBAL TENDER ENQUIRY")
        print("==============================================")

        await open_acts_policies(page)

        await set_50_records(page)

        await download_section(
            page,
            "Global Tender Enquiry",
            global_tender_folder,
            "Global Tender Enquiry"
        )

        print(
            "Global Tender Enquiry:",
            count_pdfs(global_tender_folder)
        )


        # ====================================================
        # FINISHED
        # ====================================================

        print("\n")
        print("==============================================")
        print("ALL DPIIT DOWNLOADS COMPLETED")
        print("==============================================")

        await browser.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        guarded_main(main)
    )
