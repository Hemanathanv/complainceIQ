import getpass
import os
import re
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DOTENV_VALUES = dotenv_values(SCRIPT_DIR / ".env")
UI_TIMEOUT_MS = 60000
DOWNLOAD_TIMEOUT_MS = 90000


def env_value(*names, default=""):
    # Prefer explicit, prefixed process variables such as GST_USERNAME.
    for name in names:
        if name.isupper():
            value = os.getenv(name)
            if value and value.strip():
                return value.strip()

    # Read .env values directly: on Windows, its built-in USERNAME variable
    # can otherwise mask a lowercase `username` entry in .env.
    for name in names:
        value = DOTENV_VALUES.get(name)
        if value and value.strip():
            return value.strip()

    return default


download_path = Path(
    env_value("DOWNLOAD_PATH", "download_path", default="gst_downloads")
).expanduser()
DOWNLOAD_DIR = download_path if download_path.is_absolute() else SCRIPT_DIR / download_path


def get_options(select):
    return [
        text.strip()
        for text in select.locator("option").all_text_contents()
        if text.strip()
    ]


def ask_choice(name, options, default):
    if not options:
        raise RuntimeError(f"No options found for {name}.")

    if default not in options:
        default = options[0]

    print(f"\n{name}:")
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")

    while True:
        answer = input(f"Choose number or type an option [default: {default}]: ").strip()

        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        if answer in options:
            return answer

        print("Enter a listed number or option.")


def choose_select_option(page, selector, name, default):
    """List a GST dropdown's current options and apply the terminal choice."""
    select = page.locator(selector)
    select.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    page.wait_for_function(
        """selector => {
            const element = document.querySelector(selector);
            return element && Array.from(element.options).some(
                option => option.textContent.trim()
            );
        }""",
        arg=selector,
        timeout=UI_TIMEOUT_MS,
    )
    options = get_options(select)
    choice = ask_choice(name, options, default)
    select.select_option(label=choice)

    # Wait until Angular reflects the selected label before reading the next
    # dependent dropdown (quarter depends on year; period depends on quarter).
    page.wait_for_function(
        """({ selector, label }) => {
            const element = document.querySelector(selector);
            return element && element.options[element.selectedIndex]
                && element.options[element.selectedIndex].textContent.trim() === label;
        }""",
        arg={"selector": selector, "label": choice},
        timeout=UI_TIMEOUT_MS,
    )
    return choice


def save_download(download, description):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = DOWNLOAD_DIR / download.suggested_filename
    download.save_as(destination)
    print(f"{description} saved to: {destination}")


def click_and_monitor_download(page, context, button, description):
    original_url = page.url
    pages_before = set(context.pages)
    button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_event:
            button.click(timeout=UI_TIMEOUT_MS)

        save_download(download_event.value, description)
        return

    except PlaywrightTimeoutError:
        new_pages = [p for p in context.pages if p not in pages_before]

        if new_pages:
            opened_page = new_pages[-1]
            print(f"{description} opened in a browser tab: {opened_page.url}")
            input("Download it from that tab, then press Enter to continue.")
            return

        if page.url != original_url:
            print(f"{description} opened in this tab: {page.url}")
            input("Download it from the browser, then press Enter to continue.")
            page.go_back(
                wait_until="domcontentloaded",
                timeout=UI_TIMEOUT_MS,
            )
            page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)
            return

        raise RuntimeError(
            f"No download or document tab appeared for {description}. "
            "Check the GST page and try again."
        )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(UI_TIMEOUT_MS)
    page.set_default_navigation_timeout(UI_TIMEOUT_MS)

    username = env_value("GST_USERNAME", "username") or input("GST username: ").strip()
    password = env_value("GST_PASSWORD", "password") or getpass.getpass("GST password: ")

    # Stage 1: Open the GST login page directly.
    page.goto(
        "https://services.gst.gov.in/services/login",
        wait_until="domcontentloaded",
    )

    # Stage 2: Fill credentials.
    username_field = page.get_by_label("Username", exact=True)
    username_field.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    username_field.fill(username)
    password_field = page.get_by_label("Password", exact=True)
    password_field.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    password_field.fill(password)

    # CAPTCHA handling intentionally left for your logic.
    # Replace this pause with your CAPTCHA handling when ready.
    # captcha_value = your_captcha_logic()
    # page.locator("YOUR_CAPTCHA_INPUT_SELECTOR").fill(captcha_value)
    input("Complete the CAPTCHA in the browser, then press Enter here.")

    login_button = None
    for frame in page.frames:
        candidate = frame.get_by_role(
            "button",
            name=re.compile(r"^\s*LOGIN\s*$", re.IGNORECASE),
        )
        if candidate.count():
            login_button = candidate.first
            break

    if login_button is None:
        print("Current URL:", page.url)
        print("Frames:", [frame.url for frame in page.frames])
        print("Buttons:", page.locator("button").all_text_contents())
        raise RuntimeError("Could not find the GST LOGIN button.")

    login_button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    login_button.click(timeout=UI_TIMEOUT_MS)

    # Wait for successful login; capture common login errors if it fails.
    try:
        page.wait_for_url("**/services/auth/fowelcome", timeout=UI_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        login_errors = [
            "Invalid Username or Password. Please try again.",
            "Enter valid Letters shown",
        ]

        for message in login_errors:
            error = page.get_by_text(message, exact=True)
            try:
                error.wait_for(state="visible", timeout=5000)
            except PlaywrightTimeoutError:
                continue
            else:
                print("Login error:", message)
                page.screenshot(path="gst-login-error.png", full_page=True)
                print("Saved screenshot: gst-login-error.png")
                raise SystemExit(1)

        page.screenshot(path="gst-login-unknown-error.png", full_page=True)
        raise RuntimeError("Login did not reach the GST welcome page.")

    popup = page.locator("#caNumpopupV")
    try:
        popup.wait_for(state="visible", timeout=15000)
    except PlaywrightTimeoutError:
        print("Metadata popup not shown; continuing.")
    else:
        dismiss = popup.get_by_role(
            "link", name="NO-REMIND ME LATER", exact=True
        )
        dismiss.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
        dismiss.click(timeout=UI_TIMEOUT_MS)
        popup.wait_for(state="hidden", timeout=UI_TIMEOUT_MS)

    # Go to the return filing dashboard.
    return_dashboard_button = page.get_by_role(
        "button", name=re.compile("RETURN DASHBOARD", re.IGNORECASE)
    )
    return_dashboard_button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    return_dashboard_button.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url("**/returns/auth/dashboard", timeout=UI_TIMEOUT_MS)

    print("URL:", page.url)

    # These are the actual GST dashboard select names. Read each dropdown's
    # live options because the available quarters and periods depend on the
    # preceding selection.
    year_choice = choose_select_option(
        page, 'select[name="fin"]', "Financial Year", "2026-27"
    )
    quarter_choice = choose_select_option(
        page,
        'select[name="quarter"]',
        "Quarter",
        "Quarter 1 (Apr - Jun)",
    )
    period_choice = choose_select_option(
        page, 'select[name="mon"]', "Period", "April"
    )

    print(f"\nSearching: {year_choice}, {quarter_choice}, {period_choice}")
    search_button = page.get_by_role(
        "button", name=re.compile(r"^\s*SEARCH\s*$", re.IGNORECASE)
    )
    search_button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    search_button.click(timeout=UI_TIMEOUT_MS)

    # GST renders this card as an <a> with ng-click and no href, so it has no
    # accessible "link" role. Locate the visible card by its own text instead.
    gstr1_details = page.locator(
        'a[data-ng-click^="page_rtp"]'
    ).filter(
        has_text="Details of outward supplies of goods or services"
    ).filter(has_text="GSTR-1")
    gstr1_details.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    # Download the GSTR-3B monthly return using the action on its card.
    gstr3b_download = page.locator(
        'button[data-ng-click="downloadGSTR3Bpdf()"]'
    )
    gstr3b_download.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    click_and_monitor_download(
        page,
        context,
        gstr3b_download,
        "GSTR-3B return",
    )

    # Open GSTR-1 details, then its summary.
    gstr1_view = page.get_by_role("button", name="VIEW", exact=True).first
    gstr1_view.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    gstr1_view.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url("**/returns/auth/gstr1", timeout=UI_TIMEOUT_MS)

    summary_button = page.get_by_role(
        "button", name="VIEW SUMMARY", exact=True
    )
    summary_button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    summary_button.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url("**/returns/auth/gstr1/gstr1sum", timeout=UI_TIMEOUT_MS)

    # Download the GSTR-1 summary PDF.
    pdf_button = page.get_by_role(
        "button", name="DOWNLOAD (PDF)", exact=True
    )
    pdf_button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    click_and_monitor_download(
        page,
        context,
        pdf_button,
        "GSTR-1 summary PDF",
    )

    # Open the account dropdown and log out.
    # The profile trigger is an <a> without an href, so it is not consistently
    # exposed as a Playwright link role. Use the GST header's observed class.
    account_menu = page.locator("a.lang-dpwn")
    account_menu.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    account_menu.click(timeout=UI_TIMEOUT_MS)

    logout_link = page.get_by_role("link", name="Logout", exact=True)
    logout_link.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    logout_link.click(timeout=UI_TIMEOUT_MS)

    page.get_by_text(
        "You have successfully logged out of GST Portal.",
        exact=True,
    ).wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    print("Logout confirmed. Stopping script.")
    raise SystemExit(0)
