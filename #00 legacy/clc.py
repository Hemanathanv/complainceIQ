
from playwright.sync_api import sync_playwright
from pathlib import Path
from urllib.parse import urljoin, unquote
import re


# ==========================================================
# FOLDERS
# ==========================================================

BASE = Path(__file__).resolve().parent

CIRCULAR_FOLDER = BASE / "CLC Circulars"
ACTS_FOLDER = BASE / "CLC Acts PDFs"

CIRCULAR_FOLDER.mkdir(exist_ok=True)
ACTS_FOLDER.mkdir(exist_ok=True)


# ==========================================================
# URLS
# ==========================================================

CLC_HOME = "https://clc.gov.in/"

CIRCULAR_URL = "https://clc.gov.in/clc/circulars"

ACTS_URL = "https://clc.gov.in/clc/acts-rules/acts-and-rules-0"


# ==========================================================
# 15 ACTS - EXACT WEBSITE ORDER
# ==========================================================

ACTS = [
    "Industrial Disputes Act 1947",
    "Industrial Disputes Act Amendment 2010.",
    "The Contract Labour (Regulation & Abolition)Act,and Rules, 1970",
    "The Building and Other Construction Works (RE & CE) Act, 1996",
    "The Minimum Wages Act, 1948",
    "The Equal Remuneration Act, 1976",
    "The Payment of Gratuity Act, 1972",
    "The Child Labour (Prohibition & Regulation)Act and Rules 1986",
    "The Payment of Bonus Act, and Rules 1976",
    "The Interstate Migrant Workmen (RE &CS) Act and Rules, 1979",
    "The Payment of Wages Act, 1936",
    "The Maternity Benefit Act, 1961",
    "Labour Law ( Exemption From Furnishing Returns & Maintaining Registers by Certain Establishments ) Act, 1988",
    "Industrial Employment(Standing Orders),Act 1946. Rules",
    "Railway Servants (Hours of Work And Period of Rest)Rules,2005."
]


# ==========================================================
# CLEAN FILE NAME
# ==========================================================

def clean_name(name):
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


# ==========================================================
# FIND PDF ON PAGE
# ==========================================================

def find_pdf(page):

    # Direct PDF links
    links = page.locator("a[href]")

    for i in range(links.count()):

        href = links.nth(i).get_attribute("href")

        if href:

            url = urljoin(page.url, href)

            if ".pdf" in url.lower():
                return url

    # IFRAME
    frames = page.locator("iframe")

    for i in range(frames.count()):

        src = frames.nth(i).get_attribute("src")

        if not src:
            continue

        src = urljoin(page.url, src)

        if ".pdf" in src.lower():
            return src

        match = re.search(
            r'[?&]file=([^&]+)',
            src,
            re.I
        )

        if match:

            pdf = unquote(match.group(1))
            pdf = urljoin(page.url, pdf)

            if ".pdf" in pdf.lower():
                return pdf

    # EMBED
    embeds = page.locator("embed")

    for i in range(embeds.count()):

        src = embeds.nth(i).get_attribute("src")

        if src and ".pdf" in src.lower():
            return urljoin(page.url, src)

    # OBJECT
    objects = page.locator("object")

    for i in range(objects.count()):

        src = objects.nth(i).get_attribute("data")

        if src and ".pdf" in src.lower():
            return urljoin(page.url, src)

    return None


# ==========================================================
# DOWNLOAD PDF WITHOUT OPENING
# USED ONLY FOR CIRCULARS
# ==========================================================

def save_pdf_silently(context, pdf_url, folder, title):

    filename = folder / (
        clean_name(title) + ".pdf"
    )

    if filename.exists():

        print("SKIP:", filename.name)
        return

    try:

        response = context.request.get(
            pdf_url,
            timeout=60000
        )

        if not response.ok:

            print("DOWNLOAD FAILED:", title)
            return

        data = response.body()

        if not data.startswith(b"%PDF"):

            print("NOT A PDF:", title)
            return

        filename.write_bytes(data)

        print("SAVED:", filename.name)

    except Exception as e:

        print("PDF ERROR:", title)
        print(e)


# ==========================================================
# CIRCULARS / ORDERS
#
# ONLY PAGE 1 + PAGE 2 ARE OPENED
# PDF IS NEVER OPENED
# ==========================================================

def circulars(context):

    print()
    print("=" * 60)
    print("CIRCULARS / ORDERS")
    print("=" * 60)

    for page_number in [0, 1]:

        url = (
            CIRCULAR_URL
            + f"?page={page_number}"
        )

        print()
        print(
            f"OPENING CIRCULAR PAGE {page_number + 1}"
        )

        page = context.new_page()

        try:

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(2500)

            print(
                f"CIRCULAR PAGE {page_number + 1} OPENED"
            )

            rows = page.locator(
                "table.views-table tbody tr"
            )

            print(
                "ITEMS:",
                rows.count()
            )

            # Download PDFs silently
            for i in range(rows.count()):

                row = rows.nth(i)

                title_locator = row.locator(
                    ".views-field-title"
                )

                if not title_locator.count():
                    continue

                title = (
                    title_locator
                    .inner_text()
                    .strip()
                )

                link = row.locator(
                    "a[href]"
                ).first

                if not link.count():
                    continue

                href = link.get_attribute("href")

                if not href:
                    continue

                url2 = urljoin(
                    page.url,
                    href
                )

                # Direct PDF
                if ".pdf" in url2.lower():

                    save_pdf_silently(
                        context,
                        url2,
                        CIRCULAR_FOLDER,
                        title
                    )

                    continue

                # Get node page WITHOUT opening it
                try:

                    response = context.request.get(
                        url2,
                        timeout=60000
                    )

                    if not response.ok:
                        continue

                    html = response.text()

                    pdf_url = find_pdf_from_html(
                        html,
                        url2
                    )

                    if pdf_url:

                        save_pdf_silently(
                            context,
                            pdf_url,
                            CIRCULAR_FOLDER,
                            title
                        )

                except Exception as e:

                    print(
                        "Circular error:",
                        title
                    )

                    print(e)

        except Exception as e:

            print("Circular page error:")
            print(e)


# ==========================================================
# FIND PDF URL FROM HTML
# ==========================================================

def find_pdf_from_html(html, base_url):

    # href
    matches = re.findall(
        r'''href\s*=\s*["']([^"']+)["']''',
        html,
        re.I
    )

    for value in matches:

        url = urljoin(
            base_url,
            value
        )

        if ".pdf" in url.lower():
            return url

    # src
    matches = re.findall(
        r'''(?:src|data)\s*=\s*["']([^"']+)["']''',
        html,
        re.I
    )

    for value in matches:

        url = urljoin(
            base_url,
            value
        )

        if ".pdf" in url.lower():
            return url

    # PDF URL anywhere
    match = re.search(
        r'''https?://[^"'<> ]+\.pdf[^"'<> ]*''',
        html,
        re.I
    )

    if match:
        return match.group(0)

    # Relative PDF
    match = re.search(
        r'''["']([^"']+\.pdf[^"']*)["']''',
        html,
        re.I
    )

    if match:

        return urljoin(
            base_url,
            match.group(1)
        )

    return None


# ==========================================================
# OPEN ACT PDF + SAVE
#
# ACT PDF ONLY
# ==========================================================

def open_act_pdf(
    context,
    page,
    pdf_url,
    title
):

    filename = ACTS_FOLDER / (
        clean_name(title) + ".pdf"
    )

    if filename.exists():

        print(
            "ALREADY EXISTS:",
            filename.name
        )

        return

    print()
    print("OPENING ACT PDF:")
    print(pdf_url)

    try:

        # Open PDF in Chrome PDF Viewer
        page.goto(
            pdf_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(4000)

        print(
            "PDF OPENED IN CHROME"
        )

        # Save actual PDF
        response = context.request.get(
            pdf_url,
            timeout=60000
        )

        if response.ok:

            data = response.body()

            if data.startswith(b"%PDF"):

                filename.write_bytes(data)

                print(
                    "SAVED:",
                    filename.name
                )

            else:

                print(
                    "NOT A PDF:",
                    title
                )

        else:

            print(
                "PDF SAVE FAILED:",
                title
            )

    except Exception as e:

        print(
            "ACT PDF ERROR:",
            title
        )

        print(e)


# ==========================================================
# CREATE PDF FROM ACT CONTENT
# ==========================================================

def create_act_pdf(
    context,
    url,
    title
):

    filename = ACTS_FOLDER / (
        clean_name(title) + ".pdf"
    )

    if filename.exists():

        print(
            "ALREADY EXISTS:",
            filename.name
        )

        return

    page = context.new_page()

    try:

        print()
        print(
            "NO PDF FOUND"
        )

        print(
            "CREATING PDF FROM ACT CONTENT"
        )

        page.goto(
            url,
            wait_until="networkidle",
            timeout=60000
        )

        page.wait_for_timeout(
            2500
        )

        # Click Read More if available
        read_more = page.locator(
            "a",
            has_text=re.compile(
                r"read\s*more",
                re.I
            )
        ).first

        if read_more.count():

            print(
                "READ MORE FOUND"
            )

            try:

                read_more.click(
                    timeout=10000
                )

                page.wait_for_timeout(
                    2500
                )

            except:

                pass

        # Keep main content
        page.evaluate("""
        () => {

            const content =
                document.querySelector('#post-content') ||
                document.querySelector('main');

            if (content) {

                document.body.innerHTML =
                    content.outerHTML;
            }

            document.querySelectorAll(
                'script, style, nav, footer, header'
            ).forEach(
                e => e.remove()
            );
        }
        """)

        page.wait_for_timeout(1000)

        # Create PDF
        page.pdf(
            path=str(filename),
            format="A4",
            print_background=True,
            display_header_footer=False,
            margin={
                "top": "25px",
                "bottom": "25px",
                "left": "25px",
                "right": "25px"
            }
        )

        print(
            "CREATED:",
            filename.name
        )

    except Exception as e:

        print(
            "CREATE PDF ERROR:",
            title
        )

        print(e)

    finally:

        page.close()


# ==========================================================
# ACTS AND RULES
# ==========================================================

def acts(context):

    print()
    print("=" * 60)
    print("ACTS → ACTS AND RULES")
    print("=" * 60)

    acts_page = context.new_page()

    try:

        acts_page.goto(
            ACTS_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        acts_page.wait_for_timeout(
            3000
        )

        print(
            "ACTS AND RULES PAGE OPENED"
        )

        rows = acts_page.locator(
            "#post-content .node-acts-rules "
            "table tbody tr"
        )

        print(
            "ACT ROWS FOUND:",
            rows.count()
        )

        # ==================================================
        # PROCESS ALL 15 IN EXACT ORDER
        # ==================================================

        for number in range(15):

            act_name = ACTS[number]

            print()
            print("=" * 60)
            print(
                f"ACT {number + 1}/15"
            )
            print(act_name)
            print("=" * 60)

            # Find exact act link
            link = acts_page.locator(
                "#post-content .node-acts-rules "
                "table tbody tr a",
                has_text=re.compile(
                    re.escape(act_name),
                    re.I
                )
            ).first

            # Fallback by row number
            if (
                not link.count()
                and number < rows.count()
            ):

                link = rows.nth(
                    number
                ).locator(
                    "a"
                ).first

            if not link.count():

                print(
                    "ACT LINK NOT FOUND"
                )

                continue

            href = link.get_attribute(
                "href"
            )

            if not href:

                print(
                    "HREF NOT FOUND"
                )

                continue

            act_url = urljoin(
                ACTS_URL,
                href
            )

            print(
                "ACT URL:",
                act_url
            )

            # ==================================================
            # DIRECT PDF
            # ==================================================

            if ".pdf" in act_url.lower():

                act_pdf_page = context.new_page()

                open_act_pdf(
                    context,
                    act_pdf_page,
                    act_url,
                    act_name
                )

                # Keep only necessary page
                act_pdf_page.close()

                continue

            # ==================================================
            # ACT PAGE
            # ==================================================

            act_page = context.new_page()

            try:

                act_page.goto(
                    act_url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                act_page.wait_for_timeout(
                    2500
                )

                print(
                    "ACT PAGE OPENED"
                )

                # --------------------------------------------------
                # Find existing PDF
                # --------------------------------------------------

                pdf_url = find_pdf(
                    act_page
                )

                # --------------------------------------------------
                # Read More
                # --------------------------------------------------

                if not pdf_url:

                    read_more = act_page.locator(
                        "a",
                        has_text=re.compile(
                            r"read\s*more",
                            re.I
                        )
                    ).first

                    if read_more.count():

                        print(
                            "READ MORE → CLICK"
                        )

                        try:

                            read_more.click(
                                timeout=10000
                            )

                            act_page.wait_for_timeout(
                                3000
                            )

                        except:

                            pass

                        pdf_url = find_pdf(
                            act_page
                        )

                # --------------------------------------------------
                # PDF EXISTS
                # --------------------------------------------------

                if pdf_url:

                    print(
                        "PDF FOUND"
                    )

                    open_act_pdf(
                        context,
                        act_page,
                        pdf_url,
                        act_name
                    )

                # --------------------------------------------------
                # NO PDF
                # --------------------------------------------------

                else:

                    print(
                        "NO PDF FOUND"
                    )

                    create_act_pdf(
                        context,
                        act_url,
                        act_name
                    )

            except Exception as e:

                print(
                    "ACT ERROR:",
                    act_name
                )

                print(e)

            finally:

                act_page.close()

    except Exception as e:

        print(
            "ACTS ERROR:"
        )

        print(e)

    finally:

        acts_page.close()


# ==========================================================
# MAIN
# ==========================================================

def main():

    with sync_playwright() as p:

        print()
        print("=" * 60)
        print("STARTING CLC AUTOMATION")
        print("=" * 60)

        # ==================================================
        # ONLY ONE CHROME
        # ==================================================

        browser = p.chromium.launch(
            headless=False,
            channel="chrome"
        )

        context = browser.new_context(
            accept_downloads=True
        )

        # ==================================================
        # OPEN CLC
        # ==================================================

        home = context.new_page()

        home.goto(
            CLC_HOME,
            wait_until="domcontentloaded",
            timeout=60000
        )

        home.wait_for_timeout(
            3000
        )

        print(
            "CLC WEBSITE OPENED"
        )

        # ==================================================
        # 1. CIRCULARS
        # ==================================================

        circulars(
            context
        )

        # ==================================================
        # 2. ACTS
        # ==================================================

        acts(
            context
        )

        # ==================================================
        # COMPLETED
        # ==================================================

        print()
        print("=" * 60)
        print("AUTOMATION COMPLETED")
        print("=" * 60)

        print()
        print(
            "CLC Circulars:"
        )
        print(
            CIRCULAR_FOLDER
        )

        print()
        print(
            "CLC Acts PDFs:"
        )
        print(
            ACTS_FOLDER
        )

        print()
        print(
            "Circular Page 1 + Page 2 are open."
        )

        print(
            "Circular PDFs were NOT opened."
        )

        print(
            "Acts PDFs were opened and saved."
        )

        print(
            "Acts without PDFs were converted to PDFs."
        )

        # Keep Chrome open
        print()
        print(
            "Chrome is kept open."
        )
        print(
            "Press CTRL+C in terminal to stop."
        )

        try:

            while True:

                home.wait_for_timeout(
                    1000
                )

        except KeyboardInterrupt:

            print()
            print(
                "Stopping..."
            )

        finally:

            context.close()
            browser.close()


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    main()


BASE = Path(__file__).resolve().parent

CIRCULAR_FOLDER = BASE / "CLC Circulars"
ACTS_FOLDER = BASE / "CLC Acts PDFs"

CIRCULAR_FOLDER.mkdir(exist_ok=True)
ACTS_FOLDER.mkdir(exist_ok=True)


# ==========================================================
# URLS
# ==========================================================

CLC_HOME = "https://clc.gov.in/"

CIRCULAR_URL = "https://clc.gov.in/clc/circulars"

ACTS_URL = "https://clc.gov.in/clc/acts-rules/acts-and-rules-0"


# ==========================================================
# 15 ACTS - EXACT WEBSITE ORDER
# ==========================================================

ACTS = [
    "Industrial Disputes Act 1947",
    "Industrial Disputes Act Amendment 2010.",
    "The Contract Labour (Regulation & Abolition)Act,and Rules, 1970",
    "The Building and Other Construction Works (RE & CE) Act, 1996",
    "The Minimum Wages Act, 1948",
    "The Equal Remuneration Act, 1976",
    "The Payment of Gratuity Act, 1972",
    "The Child Labour (Prohibition & Regulation)Act and Rules 1986",
    "The Payment of Bonus Act, and Rules 1976",
    "The Interstate Migrant Workmen (RE &CS) Act and Rules, 1979",
    "The Payment of Wages Act, 1936",
    "The Maternity Benefit Act, 1961",
    "Labour Law ( Exemption From Furnishing Returns & Maintaining Registers by Certain Establishments ) Act, 1988",
    "Industrial Employment(Standing Orders),Act 1946. Rules",
    "Railway Servants (Hours of Work And Period of Rest)Rules,2005."
]


# ==========================================================
# CLEAN FILE NAME
# ==========================================================

def clean_name(name):
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


# ==========================================================
# FIND PDF ON PAGE
# ==========================================================

def find_pdf(page):

    # Direct PDF links
    links = page.locator("a[href]")

    for i in range(links.count()):

        href = links.nth(i).get_attribute("href")

        if href:

            url = urljoin(page.url, href)

            if ".pdf" in url.lower():
                return url

    # IFRAME
    frames = page.locator("iframe")

    for i in range(frames.count()):

        src = frames.nth(i).get_attribute("src")

        if not src:
            continue

        src = urljoin(page.url, src)

        if ".pdf" in src.lower():
            return src

        match = re.search(
            r'[?&]file=([^&]+)',
            src,
            re.I
        )

        if match:

            pdf = unquote(match.group(1))
            pdf = urljoin(page.url, pdf)

            if ".pdf" in pdf.lower():
                return pdf

    # EMBED
    embeds = page.locator("embed")

    for i in range(embeds.count()):

        src 

