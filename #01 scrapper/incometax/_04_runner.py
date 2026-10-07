"""Open the Income Tax homepage, report its status/date, and open Circulars."""

from playwright.sync_api import sync_playwright

from _00_popup import income_tax_popup
from _01_incometax_home_page import incometax_home_page


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        home = incometax_home_page(page)

        print("Checking Income Tax website status...")
        if not home.check_status_200():
            print("Income Tax website is not active (HTTP status was not 200).")
            browser.close()
            return

        print("Income Tax website is active (HTTP 200).")
        popup = income_tax_popup(page)
        if popup.dismiss_guided_tour():
            print("Guided Tour popup dismissed.")
        home.print_last_reviewed_date()

        print("Opening Circulars...")
        home.hover_tax_laws_rules()
        home.hover_circulars_notifications()
        home.click_circulars()
        page.wait_for_load_state("domcontentloaded")
        print("Circulars page opened:", page.url)

        browser.close()


if __name__ == "__main__":
    main()
