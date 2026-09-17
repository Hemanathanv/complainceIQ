from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import re
import requests
import urllib3

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

START_URL = "https://parivesh.nic.in/#/dw-act-rule"

DOWNLOAD_ROOT = Path("PARIVESH_Acts_Rules_PDFs")

HEADLESS = False
TIMEOUT = 60000


# ============================================================
# SSL WARNING
# ============================================================

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


# ============================================================
# PARIVESH CATEGORIES
# ============================================================

CATEGORIES = [
    (
        "Environmental_Acts_and_Rules",
        "#v-pills-home-tab",
        "#v-pills-home",
        [
            ("Acts", "#flush-collapseOne", "#flush-headingOne"),
            ("Rules", "#flush-collapseTwo", "#flush-headingTwo"),
            ("Regulations", "#flush-collapseFour", "#flush-headingFour"),
            ("Power Conferred by Environment Protection Act", "#flush-collapseThree", "#flush-headingThree", [
                ("Amendments to Principal Rules", "#EC-collapse2", "#EC-heading2"),
                ("Coastal Regulation Zone (CRZ)", "#EC-collapse3", "#EC-heading3"),
                ("Delegation of Powers", "#EC-collapse4", "#EC-heading4"),
                ("Eco-marks Scheme", "#EC-collapse5", "#EC-heading5"),
                ("Eco-sensitive Zone", "#EC-collapse6", "#EC-heading6"),
                ("Environmental Clearance - General", "#EC-collapse7", "#EC-heading7"),
                ("Environmental Impact Assessment Notification - 2006", "#EC-collapse8", "#EC-heading8"),
                ("Environmental Labs", "#EC-collapse9", "#EC-heading9"),
                ("Environmental Standards", "#EC-collapse10", "#EC-heading10"),
                ("Hazardous Substances Management", "#EC-collapse15", "#EC-heading15"),
                ("Loss of Ecology", "#EC-collapse11", "#EC-heading11"),
                ("Noise Pollution", "#EC-collapse16", "#EC-heading16"),
                ("Ozone Layer Depletion", "#EC-collapse12", "#EC-heading12"),
                ("Water Pollution", "#EC-collapse13", "#EC-heading13"),
            ]),
        ]
    ),
    (
        "Air_Acts_and_Rules",
        "#v-pills-air-tab",
        "#v-pills-air",
        [
            ("Acts", "#air-flush-collapseOne", "#air-flush-headingOne"),
            ("Rules", "#air-flush-collapseTwo", "#air-flush-headingTwo"),
            ("Regulations", "#air-flush-collapseThree", "#air-flush-headingThree"),
        ]
    ),
    (
        "Water_Acts_and_Rules",
        "#v-pills-water-tab",
        "#v-pills-water",
        [
            ("Acts", "#water-flush-collapseOne", "#water-flush-headingOne"),
            ("Rules", "#water-flush-collapseTwo", "#water-flush-headingTwo"),
            ("Regulations", "#water-flush-collapseThree", "#water-flush-headingThree"),
        ]
    ),
]


# ============================================================
# SAFE FILE NAME
# ============================================================

def safe_filename(name):

    name = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        name
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    )

    return name.strip(" .")


# ============================================================
# GET PDF FILE NAME
# ============================================================

def get_filename(url):

    path = urlparse(url).path

    name = unquote(
        Path(path).name
    )

    if not name:
        name = "document.pdf"

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return safe_filename(name)


# ============================================================
# CLICK CATEGORY
# ============================================================

def click_category(
    page,
    tab_selector,
    content_selector
):

    tab = page.locator(tab_selector)

    tab.wait_for(
        state="visible",
        timeout=TIMEOUT
    )

    tab.scroll_into_view_if_needed()

    try:
        tab.click(
            timeout=TIMEOUT
        )

    except Exception:

        page.evaluate(
            """
            selector => {
                const element =
                    document.querySelector(selector);

                if (element) {
                    element.click();
                }
            }
            """,
            tab_selector
        )

    page.wait_for_timeout(2000)

    content = page.locator(
        content_selector
    )

    content.wait_for(
        state="visible",
        timeout=TIMEOUT
    )

    page.wait_for_timeout(1500)

    return content


# ============================================================
# OPEN ACCORDIONS
# ============================================================

def open_accordions(
    page,
    content
):

    buttons = content.locator(
        "button.accordion-button"
    )

    count = buttons.count()

    for i in range(count):

        try:

            button = buttons.nth(i)

            if not button.is_visible():
                continue

            expanded = (
                button.get_attribute(
                    "aria-expanded"
                )
                or ""
            ).lower()

            if expanded != "true":

                button.click(
                    timeout=5000
                )

                page.wait_for_timeout(
                    300
                )

        except Exception:

            continue


# ============================================================
# FIND ALL PDF LINKS
# ============================================================

def get_pdf_links(content):

    pdf_links = []

    anchors = content.locator(
        "a[href]"
    )

    count = anchors.count()

    for i in range(count):

        try:

            href = anchors.nth(i).get_attribute(
                "href"
            )

            if not href:
                continue

            href = href.strip()

            if href.lower().startswith(
                "javascript:"
            ):
                continue

            full_url = urljoin(
                "https://parivesh.nic.in",
                href
            )

            if ".pdf" not in full_url.lower():
                continue

            if full_url not in pdf_links:

                pdf_links.append(
                    full_url
                )

        except Exception:

            continue

    return pdf_links


# ============================================================
# DOWNLOAD ONE PDF
# ============================================================

def download_pdf(
    session,
    url,
    folder,
    number
):

    try:

        response = session.get(
            url,
            timeout=90,
            verify=False,
            allow_redirects=True
        )

        if response.status_code != 200:

            print(
                f"[ERROR] HTTP "
                f"{response.status_code}"
            )

            return False

        data = response.content

        if not data.startswith(
            b"%PDF"
        ):

            print(
                "[ERROR] Not a PDF"
            )

            return False

        filename = get_filename(
            response.url
        )

        output = (
            folder /
            f"{number:03d}_{filename}"
        )

        if (
            output.exists()
            and output.stat().st_size > 0
        ):

            print(
                f"[SKIP] {filename}"
            )

            return True

        output.write_bytes(
            data
        )

        print(
            f"[OK] {filename}"
        )

        return True

    except Exception as e:

        print(
            f"[ERROR] Download failed:"
            f" {url}"
        )

        print(
            f"       {e}"
        )

        return False


# ============================================================
# PROCESS ONE CATEGORY
# ============================================================

def process_category(
    page,
    name,
    tab_selector,
    content_selector,
    sections
):

    print()
    print("=" * 60)
    print(
        f"CATEGORY: {name}"
    )
    print("=" * 60)

    try:

        content = click_category(
            page,
            tab_selector,
            content_selector
        )

        open_accordions(
            page,
            content
        )

        page.wait_for_timeout(
            1000
        )

        classified_links = []

        for section in sections:
            section_name, collapse_selector, heading_selector = section[:3]
            heading = content.locator(heading_selector + " button")
            if heading.is_visible() and (heading.get_attribute("aria-expanded") or "").lower() != "true":
                heading.click()
                page.wait_for_timeout(300)

            section_folder = DOWNLOAD_ROOT / name / section_name
            nested_sections = section[3] if len(section) > 3 else []

            if nested_sections:
                for nested_name, nested_collapse, nested_heading in nested_sections:
                    nested_button = content.locator(nested_heading + " button")
                    if nested_button.is_visible() and (nested_button.get_attribute("aria-expanded") or "").lower() != "true":
                        nested_button.click()
                        page.wait_for_timeout(200)
                    links = get_pdf_links(content.locator(nested_collapse))
                    for url in links:
                        classified_links.append((section_folder / nested_name, url))
            else:
                links = get_pdf_links(content.locator(collapse_selector))
                for url in links:
                    classified_links.append((section_folder, url))

        print(f"PDFs found: {len(classified_links)}")
        return classified_links

    except Exception as e:

        print(
            f"[ERROR] Could not read "
            f"{name}"
        )

        print(
            f"       {e}"
        )

        return []


# ============================================================
# MAIN
# ============================================================

def main():

    DOWNLOAD_ROOT.mkdir(
        parents=True,
        exist_ok=True
    )

    all_categories = []

    # --------------------------------------------------------
    # OPEN WEBSITE AND COLLECT PDF LINKS
    # --------------------------------------------------------

    with sync_playwright() as p:

        print()
        print("=" * 60)
        print("STARTING PARIVESH AUTOMATION")
        print("=" * 60)

        browser = p.chromium.launch(
            headless=HEADLESS
        )

        context = browser.new_context(
            ignore_https_errors=True
        )

        page = context.new_page()

        page.set_default_timeout(
            TIMEOUT
        )

        print(
            "\nOpening PARIVESH..."
        )

        page.goto(
            START_URL,
            wait_until="domcontentloaded",
            timeout=TIMEOUT
        )

        print(
            "Waiting for website..."
        )

        page.wait_for_selector(
            "#v-pills-air-tab",
            timeout=TIMEOUT
        )

        page.wait_for_timeout(
            5000
        )

        print(
            "PARIVESH loaded successfully."
        )

        # ----------------------------------------------------
        # CHECK ALL 9 CATEGORIES
        # ----------------------------------------------------

        for (
            name,
            tab_selector,
            content_selector,
            sections
        ) in CATEGORIES:

            links = process_category(
                page,
                name,
                tab_selector,
                content_selector,
                sections
            )

            all_categories.append(
                (name, links)
            )

        # ----------------------------------------------------
        # CLOSE BROWSER
        # ----------------------------------------------------

        browser.close()

    # ========================================================
    # DOWNLOAD ALL PDFs
    # ========================================================

    print()
    print("=" * 60)
    print("STARTING PDF DOWNLOAD")
    print("=" * 60)

    session = requests.Session()

    session.verify = False

    session.headers.update(
        {
            "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 "
                "Safari/537.36",

            "Accept":
                "application/pdf,"
                "application/octet-stream,"
                "*/*",

            "Referer":
                "https://parivesh.nic.in/"
        }
    )

    total_found = 0
    total_downloaded = 0

    # --------------------------------------------------------
    # EACH CATEGORY
    # --------------------------------------------------------

    for name, links in all_categories:

        print()
        print("-" * 60)
        print(
            f"DOWNLOADING: {name}"
        )
        print("-" * 60)

        total_found += len(links)

        counters = {}

        for folder, url in links:
            counters[folder] = counters.get(folder, 0) + 1

            success = download_pdf(
                session,
                url,
                folder,
                counters[folder]
            )

            if success:
                total_downloaded += 1

    session.close()

    # ========================================================
    # FINAL RESULT
    # ========================================================

    print()
    print("=" * 60)
    print("PARIVESH DOWNLOAD COMPLETED")
    print("=" * 60)

    print(
        f"Total PDF links found : "
        f"{total_found}"
    )

    print(
        f"Total PDFs downloaded  : "
        f"{total_downloaded}"
    )

    print()
    print(
        "PDFs saved here:"
    )

    print(
        DOWNLOAD_ROOT.resolve()
    )

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
