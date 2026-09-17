from pathlib import Path
from urllib.parse import urljoin
import re
import time

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.epfindia.gov.in"

START_URL = "https://www.epfindia.gov.in/site_en/Acts&Manuals.php"

EPF_ACT_URL = "https://www.epfindia.gov.in/epf-mp-act-1952"

CIRCULARS_URL = "https://www.epfindia.gov.in/circulars/"

EPF_PDF_FOLDER = Path("EPF_MP_Act_1952_PDFs")
CIRCULARS_FOLDER = Path("EPFO_Circulars_PDFs")

HEADLESS = False
TIMEOUT = 30000


# ============================================================
# SAFE FILENAME
# ============================================================

def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180]


# ============================================================
# UNIQUE FILE PATH
# ============================================================

def unique_file_path(folder, filename):

    folder.mkdir(parents=True, exist_ok=True)

    path = folder / filename

    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    number = 1

    while True:

        new_path = folder / f"{stem}_{number}{suffix}"

        if not new_path.exists():
            return new_path

        number += 1


# ============================================================
# OPEN LEGAL FRAMEWORK
# ============================================================

def open_legal_framework(page):

    print("\n[STEP] Opening Legal Framework...")

    selectors = [
        "a#menu-item-dropdown-8400",
        'a[title="Legal Framework"]',
        'a:has-text("Legal Framework")'
    ]

    for selector in selectors:

        try:

            locator = page.locator(selector).first

            if locator.count() > 0:

                locator.hover()
                time.sleep(1)

                print("[OK] Legal Framework opened")
                return True

        except Exception:
            continue

    print("[ERROR] Legal Framework not found")
    return False


# ============================================================
# OPEN EPF & MP ACT 1952
# ============================================================

def open_epf_act(page):

    print("\n[STEP] Opening EPF & MP Act 1952...")

    selectors = [
        'a[href="/epf-mp-act-1952"]',
        'a[title="EPF & MP Act 1952"]',
        'a:has-text("EPF & MP Act 1952")'
    ]

    for selector in selectors:

        try:

            locator = page.locator(selector).first

            if locator.count() > 0:

                locator.click()

                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=TIMEOUT
                )

                time.sleep(2)

                print("[OK] EPF & MP Act 1952 opened")
                return True

        except Exception:
            continue

    try:

        print("[INFO] Opening EPF Act directly...")

        page.goto(
            EPF_ACT_URL,
            wait_until="domcontentloaded",
            timeout=TIMEOUT
        )

        time.sleep(2)

        print("[OK] EPF & MP Act 1952 opened")
        return True

    except Exception as e:

        print("[ERROR] EPF Act could not be opened:", e)
        return False


# ============================================================
# SAVE EPF ACT WEBPAGE AS PDF - ONLY ONCE
# ============================================================

def save_epf_act_as_pdf():

    EPF_PDF_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )

    pdf_path = (
        EPF_PDF_FOLDER /
        "EPF_MP_Act_1952.pdf"
    )

    if pdf_path.exists() and pdf_path.stat().st_size > 0:

        print("\n[SKIP] EPF Act PDF already exists")
        print(pdf_path.resolve())

        return True

    print("\n============================================================")
    print(" SAVING EPF & MP ACT 1952 AS PDF")
    print("============================================================")

    try:

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True
            )

            page = browser.new_page(
                viewport={
                    "width": 1366,
                    "height": 900
                }
            )

            page.goto(
                EPF_ACT_URL,
                wait_until="networkidle",
                timeout=TIMEOUT
            )

            time.sleep(3)

            page.pdf(
                path=str(pdf_path.resolve()),
                format="A4",
                print_background=True,
                display_header_footer=False,
                margin={
                    "top": "15mm",
                    "bottom": "15mm",
                    "left": "12mm",
                    "right": "12mm"
                }
            )

            browser.close()

        if pdf_path.exists() and pdf_path.stat().st_size > 0:

            print("[SUCCESS] EPF Act PDF saved")
            print(pdf_path.resolve())

            return True

        print("[ERROR] EPF Act PDF was not created")
        return False

    except Exception as e:

        print("[ERROR] EPF Act PDF error:", e)
        return False


# ============================================================
# OPEN CIRCULARS
# ============================================================

def open_circulars(page):

    print("\n[STEP] Opening Circulars...")

    try:

        page.goto(
            CIRCULARS_URL,
            wait_until="domcontentloaded",
            timeout=TIMEOUT
        )

        time.sleep(3)

        print("[OK] Circulars opened")
        return True

    except Exception as e:

        print("[WARNING] Direct Circulars opening failed:", e)

    return False


# ============================================================
# GET ALL PDF LINKS FROM CIRCULARS
# ============================================================

def get_pdf_links(page):

    print("\n[STEP] Searching for ALL PDF links...")

    pdf_links = []

    anchors = page.locator("a[href]")

    total = anchors.count()

    print(f"[INFO] Total links found: {total}")

    for i in range(total):

        try:

            anchor = anchors.nth(i)

            href = anchor.get_attribute("href")

            if not href:
                continue

            full_url = urljoin(
                page.url,
                href
            )

            if ".pdf" not in full_url.lower():
                continue

            if full_url not in pdf_links:

                pdf_links.append(full_url)

                text = anchor.inner_text().strip()

                print(
                    f"[PDF {len(pdf_links)}] "
                    f"{text[:80]}"
                )

        except Exception:
            continue

    print(
        f"\n[INFO] TOTAL PDFs FOUND: "
        f"{len(pdf_links)}"
    )

    return pdf_links


# ============================================================
# DOWNLOAD PDF
# ============================================================

def download_pdf(page, pdf_url, folder, index):

    try:

        response = page.context.request.get(
            pdf_url,
            timeout=60000
        )

        if not response.ok:

            print(
                f"[ERROR] HTTP {response.status}"
            )

            return False

        content = response.body()

        if not content.startswith(b"%PDF"):

            print("[ERROR] File is not a valid PDF")
            return False

        filename = (
            pdf_url
            .split("/")[-1]
            .split("?")[0]
        )

        filename = safe_filename(filename)

        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        filename = f"{index:03d}_{filename}"

        file_path = unique_file_path(
            folder,
            filename
        )

        file_path.write_bytes(content)

        print(
            f"[DOWNLOADED] {file_path.name}"
        )

        return True

    except Exception as e:

        print(
            f"[ERROR] Download failed: {e}"
        )

        return False


# ============================================================
# DOWNLOAD ALL CIRCULAR PDFs
# ============================================================

def download_all_circular_pdfs(page):

    print("\n============================================================")
    print(" DOWNLOADING ALL CIRCULAR PDFs")
    print("============================================================")

    CIRCULARS_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )

    pdf_links = get_pdf_links(page)

    if not pdf_links:

        print("[WARNING] No PDF files found")
        return

    successful = 0

    for index, pdf_url in enumerate(
        pdf_links,
        start=1
    ):

        if download_pdf(
            page,
            pdf_url,
            CIRCULARS_FOLDER,
            index
        ):

            successful += 1

    print("\n============================================================")
    print(" CIRCULAR DOWNLOAD COMPLETED")
    print("============================================================")

    print(
        f"PDFs found      : {len(pdf_links)}"
    )

    print(
        f"PDFs downloaded : {successful}"
    )

    print(
        f"Folder          : "
        f"{CIRCULARS_FOLDER.resolve()}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("\n")
    print("============================================================")
    print("                 EPFO AUTOMATION")
    print("============================================================")

    # --------------------------------------------------------
    # STEP 1
    # EPF ACT PDF - ONLY ONCE
    # --------------------------------------------------------

    save_epf_act_as_pdf()

    # --------------------------------------------------------
    # STEP 2
    # OPEN BROWSER
    # --------------------------------------------------------

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=HEADLESS
        )

        page = browser.new_page()

        page.set_default_timeout(
            TIMEOUT
        )

        # ----------------------------------------------------
        # STEP 3
        # OPEN EPFO
        # ----------------------------------------------------

        print("\n[STEP] Opening EPFO website...")

        page.goto(
            START_URL,
            wait_until="domcontentloaded",
            timeout=TIMEOUT
        )

        time.sleep(3)

        print("[OK] EPFO website opened")

        # ----------------------------------------------------
        # STEP 4
        # LEGAL FRAMEWORK
        # ----------------------------------------------------

        if not open_legal_framework(page):

            browser.close()
            return

        # ----------------------------------------------------
        # STEP 5
        # EPF ACT
        # ----------------------------------------------------

        if not open_epf_act(page):

            browser.close()
            return

        # ----------------------------------------------------
        # STEP 6
        # CIRCULARS
        # ----------------------------------------------------

        if not open_circulars(page):

            browser.close()
            return

        # ----------------------------------------------------
        # STEP 7
        # DOWNLOAD ALL PDFs
        # ----------------------------------------------------

        download_all_circular_pdfs(page)

        # ----------------------------------------------------
        # FINISH
        # ----------------------------------------------------

        print("\n============================================================")
        print("                 ALL WORK COMPLETED")
        print("============================================================")

        print(
            "\nEPF Act PDF:"
        )
        print(
            EPF_PDF_FOLDER.resolve()
        )

        print(
            "\nCircular PDFs:"
        )
        print(
            CIRCULARS_FOLDER.resolve()
        )

        print("\n============================================================")

        browser.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()