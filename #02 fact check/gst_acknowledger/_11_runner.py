# """Run the GST acknowledgment workflow using the numbered page objects."""

# import getpass
# import os
# import re
# from pathlib import Path

# from dotenv import dotenv_values
# from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
# from playwright.sync_api import sync_playwright

# from _01_service_page import ServicePage
# from _02_popup import PopupPage
# from _03_landing_page import LandingPage
# from _04_return_dashboard import ReturnDashboardPage
# from _05_offline_download import OfflineDownloadPage
# from _06_gstr1_page import GSTR1Page
# from _07_gstr1_summary import GSTR1SummaryPage
# from _08_logout_page import LogoutPage
# from _09_site_footer import SiteFooterPage


# SCRIPT_DIR = Path(__file__).resolve().parent
# DOTENV_VALUES = dotenv_values(SCRIPT_DIR / ".env")
# UI_TIMEOUT_MS = 60000
# DOWNLOAD_TIMEOUT_MS = 90000
# POPUP_TIMEOUT_MS = int(os.getenv("GST_POPUP_TIMEOUT_MS", "20000"))


# def prompt_for_captcha(page, service_page):
#     """Play the portal's audio CAPTCHA and ask the user to enter its digits."""
#     service_page.captcha_input.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
#     audio_button = page.locator("button:has(i.fa-volume-up)")
#     try:
#         audio_button.wait_for(state="visible", timeout=3000)
#         audio_button.click(timeout=5000)
#         print("CAPTCHA audio played. Listen in the browser and enter the digits below.")
#     except PlaywrightTimeoutError:
#         print("Could not find the CAPTCHA audio control. Read the CAPTCHA in the browser.")

#     captcha_text = input("CAPTCHA digits: ").strip()
#     if not captcha_text:
#         raise RuntimeError("No CAPTCHA was entered.")
#     service_page.fill_captcha(captcha_text)


# def load_page_objects():
#     """Return the page object classes used by this workflow."""
#     return {
#         "ServicePage": ServicePage,
#         "PopupPage": PopupPage,
#         "LandingPage": LandingPage,
#         "ReturnDashboardPage": ReturnDashboardPage,
#         "OfflineDownloadPage": OfflineDownloadPage,
#         "GSTR1Page": GSTR1Page,
#         "GSTR1SummaryPage": GSTR1SummaryPage,
#         "LogoutPage": LogoutPage,
#         "SiteFooterPage": SiteFooterPage,
#     }


# def env_value(*names, default=""):
#     """Read prefixed process variables first, then values from this folder's .env."""
#     for name in names:
#         if name.isupper():
#             value = os.getenv(name)
#             if value and value.strip():
#                 return value.strip()

#     for name in names:
#         value = DOTENV_VALUES.get(name)
#         if value and value.strip():
#             return value.strip()

#     return default


# DOWNLOAD_PATH = Path(
#     env_value("DOWNLOAD_PATH", "download_path", default="gst_downloads")
# ).expanduser()
# DOWNLOAD_BASE = DOWNLOAD_PATH if DOWNLOAD_PATH.is_absolute() else SCRIPT_DIR / DOWNLOAD_PATH
# GSTIN_FOLDER = "GSTIN"
# DOWNLOAD_DIR = DOWNLOAD_BASE / GSTIN_FOLDER


# def prepare_period_folders(filters):
#     """Create the GSTIN/financial-year/month/return folder structure."""
#     global DOWNLOAD_DIR
#     month_folder = (
#         DOWNLOAD_BASE
#         / GSTIN_FOLDER
#         / filters["financial_year"]
#         / filters["period"]
#     )
#     for return_type in ("GSTR-1", "GSTR-3B", "GSTR-6"):
#         (month_folder / return_type).mkdir(parents=True, exist_ok=True)
#     DOWNLOAD_DIR = month_folder
#     return month_folder


# def ask_choice(name, options, default):
#     if not options:
#         raise RuntimeError(f"No options found for {name}.")

#     if default not in options:
#         default = options[0]

#     print(f"\n{name}:")
#     for number, option in enumerate(options, start=1):
#         print(f"  {number}. {option}")

#     while True:
#         answer = input(f"Choose number or type an option [default: {default}]: ").strip()
#         if not answer:
#             return default
#         if answer.isdigit() and 1 <= int(answer) <= len(options):
#             return options[int(answer) - 1]
#         if answer in options:
#             return answer
#         print("Enter a listed number or option.")


# def parse_return_calendar(calendar_text):
#     """Convert the calendar's multiline text into compact display rows."""
#     lines = [line.strip() for line in calendar_text.splitlines() if line.strip()]
#     return_type = ""
#     rows = []
#     index = 0

#     while index < len(lines):
#         line = lines[index]
#         if line.upper().startswith("GSTR-1 / IFF"):
#             return_type = "GSTR-1/IFF"
#         elif line.upper().startswith("GSTR-3B"):
#             return_type = "GSTR-3B"
#         else:
#             period_match = re.fullmatch(r"([A-Za-z]{3})\s*-\s*(\d{4})", line)
#             if period_match and return_type:
#                 status = lines[index + 1] if index + 1 < len(lines) else ""
#                 filed_date = "—"
#                 if index + 2 < len(lines) and lines[index + 2].lower().startswith("filed on"):
#                     if index + 3 < len(lines):
#                         filed_date = lines[index + 3]
#                         index += 2
#                 rows.append(
#                     (
#                         return_type,
#                         f"{period_match.group(1)} {period_match.group(2)}",
#                         status,
#                         filed_date,
#                     )
#                 )
#         index += 1

#     return rows


# def print_return_calendar(calendar_text):
#     print("\nReturn calendar")
#     print(f"  {'Return':<12} {'Period':<12} {'Status':<10} Filed on")
#     print(f"  {'-' * 12} {'-' * 12} {'-' * 10} {'-' * 10}")
#     for return_type, period, status, filed_date in parse_return_calendar(calendar_text):
#         print(f"  {return_type:<12} {period:<12} {status:<10} {filed_date}")


# def print_landing_details(details):
#     print("\nLogin and portal details")
#     print(f"  GSTIN          : {details['gstin']}")
#     print(f"  Last login     : {details['last_login']}")
#     print(f"  Current IP     : {details['ip']}")
#     print(f"  Site updated   : {details['site_last_updated']}")
#     print_return_calendar(details["return_calendar"])


# def print_selected_filters(filters):
#     print("\nSelected return period")
#     print(f"  Financial year : {filters['financial_year']}")
#     print(f"  Quarter        : {filters['quarter']}")
#     print(f"  Period         : {filters['period']}")


# def print_return_cards(cards):
#     print("\nReturn dashboard results")
#     print(f"  {'Return':<10} {'Status':<12} {'Actions':<34} Card")
#     print(f"  {'-' * 10} {'-' * 12} {'-' * 34} {'-' * 12}")
#     for name, card in cards.items():
#         status = card.get("status", "—").replace("Status-", "").strip()
#         actions = ", ".join(
#             f"{button['label']}{'' if button['enabled'] else ' (disabled)'}"
#             for button in card.get("buttons", [])
#         ) or "—"
#         card_state = "disabled" if card.get("disabled") else "available"
#         due_date = card.get("due_date")
#         if due_date:
#             status = (
#                 f"Due Date - {due_date}"
#                 if "due date" in status.lower()
#                 else f"{status}; {due_date}" if status != "—" else due_date
#             )
#         print(f"  {name:<10} {status:<12} {actions:<34} {card_state}")


# def save_download(download, description):
#     return_type = "GSTR-3B" if "GSTR-3B" in description.upper() else "GSTR-1"
#     destination_dir = DOWNLOAD_DIR / return_type
#     destination_dir.mkdir(parents=True, exist_ok=True)
#     destination = destination_dir / download.suggested_filename
#     download.save_as(destination)
#     print(f"{description} saved to: {destination}")


# def click_and_monitor_download(page, context, button, description):
#     """Click a control that should directly download a file, as in main.py."""
#     original_url = page.url
#     pages_before = set(context.pages)
#     button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

#     try:
#         with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_event:
#             button.click(timeout=UI_TIMEOUT_MS)
#         save_download(download_event.value, description)
#         return
#     except PlaywrightTimeoutError:
#         new_pages = [candidate for candidate in context.pages if candidate not in pages_before]

#         if new_pages:
#             opened_page = new_pages[-1]
#             print(f"{description} opened in a browser tab: {opened_page.url}")
#             input("Download it from that tab, then press Enter to continue.")
#             return

#         if page.url != original_url:
#             print(f"{description} opened in this tab: {page.url}")
#             input("Download it from the browser, then press Enter to continue.")
#             page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
#             page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)
#             return

#         raise RuntimeError(
#             f"No download or document tab appeared for {description}. "
#             "Check the GST page and try again."
#         )


# def click_download_or_get_page(page, context, button, description):
#     """Return after a direct download, or return the page reached by the click."""
#     original_url = page.url
#     pages_before = set(context.pages)
#     button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

#     try:
#         with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_event:
#             button.click(timeout=UI_TIMEOUT_MS)
#         save_download(download_event.value, description)
#         return page, "download", original_url, False
#     except PlaywrightTimeoutError:
#         new_pages = [candidate for candidate in context.pages if candidate not in pages_before]
#         if new_pages:
#             target_page = new_pages[-1]
#             print(f"{description} opened a page: {target_page.url}")
#             return target_page, "page", original_url, True

#         if page.url != original_url:
#             print(f"{description} opened this page: {page.url}")
#             return page, "page", original_url, False

#         raise RuntimeError(
#             f"No download or page appeared for {description}. "
#             "Check the GST page and try again."
#         )


# def download_with_offline_page(page, context, button, description, offline_page_class):
#     """Handle a direct Excel download or the GST offline Excel-download page."""
#     target_page, outcome, original_url, is_new_page = click_download_or_get_page(
#         page, context, button, description
#     )
#     if outcome == "download":
#         return

#     if "offlinedownload" in target_page.url.lower():
#         offline_page = offline_page_class(target_page)
#         click_and_monitor_download(
#             target_page,
#             context,
#             offline_page.excel_download_button,
#             description,
#         )
#         if is_new_page:
#             target_page.close()
#         else:
#             target_page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
#             target_page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)
#         return

#     input("The download opened another GST page. Complete the download there, then press Enter.")
#     if is_new_page:
#         target_page.close()
#     else:
#         target_page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
#         target_page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)


# def capture_site_date(page, site_footer_class):
#     try:
#         return site_footer_class(page).capture_site_last_updated_date()
#     except PlaywrightTimeoutError:
#         return "Site update date was not visible on this page."


# def best_effort_logout(page, logout_page_class):
#     """Try to end an authenticated GST session without hiding the original error."""
#     if page.is_closed() or "/auth/" not in page.url.lower():
#         return

#     try:
#         # Keep failure cleanup bounded when the page is already in an error state.
#         page.set_default_timeout(5000)
#         logout_page = logout_page_class(page)
#         logout_page.account_dropdown.wait_for(state="visible", timeout=5000)
#         logout_page.open_account_dropdown()
#         logout_page.logout_link.wait_for(state="visible", timeout=5000)
#         logout_page.click_logout()
#         page.get_by_text(
#             "You have successfully logged out of GST Portal.",
#             exact=True,
#         ).wait_for(state="visible", timeout=5000)
#         print("Error cleanup: logout confirmed.")
#     except Exception as cleanup_error:
#         print(f"Error cleanup: logout could not be confirmed: {cleanup_error}")


# def run():
#     page_objects = load_page_objects()

#     with sync_playwright() as playwright:
#         browser = playwright.chromium.launch(headless=False)
#         context = browser.new_context(accept_downloads=True)
#         page = context.new_page()
#         page.set_default_timeout(UI_TIMEOUT_MS)
#         page.set_default_navigation_timeout(UI_TIMEOUT_MS)

#         try:
#             username = env_value("GST_USERNAME", "username") or input("GST username: ").strip()
#             password = env_value("GST_PASSWORD", "password") or getpass.getpass(
#                 "GST password: "
#             )

#             page.goto(
#                 "https://services.gst.gov.in/services/login",
#                 wait_until="domcontentloaded",
#             )

#             service_page = page_objects["ServicePage"](page)
#             service_page.fill_credentials(username, password)
#             prompt_for_captcha(page, service_page)
#             service_page.click_login()

#             try:
#                 page.wait_for_url("**/services/auth/fowelcome", timeout=UI_TIMEOUT_MS)
#             except PlaywrightTimeoutError as error:
#                 page.screenshot(path=SCRIPT_DIR / "gst-login-error.png", full_page=True)
#                 raise RuntimeError("Login did not reach the GST welcome page.") from error

#             popup_page = page_objects["PopupPage"](page)
#             if popup_page.handle_if_present(POPUP_TIMEOUT_MS):
#                 print("Post-login popup dismissed.")
#             else:
#                 print("No post-login popup appeared; continuing.")

#             landing_page = page_objects["LandingPage"](page)
#             global GSTIN_FOLDER, DOWNLOAD_DIR
#             GSTIN_FOLDER = landing_page.capture_gstin()
#             DOWNLOAD_DIR = DOWNLOAD_BASE / GSTIN_FOLDER
#             print_landing_details(
#                 {
#                     "gstin": GSTIN_FOLDER,
#                     "last_login": landing_page.capture_last_login(),
#                     "ip": landing_page.capture_ip(),
#                     "return_calendar": landing_page.capture_return_calendar(),
#                     "site_last_updated": capture_site_date(
#                         page, page_objects["SiteFooterPage"]
#                     ),
#                 }
#             )

#             landing_page.open_return_dashboard()
#             page.wait_for_url("**/returns/auth/dashboard", timeout=UI_TIMEOUT_MS)

#             return_page = page_objects["ReturnDashboardPage"](page)
#             available = return_page.capture_filter_options()
#             financial_year = ask_choice(
#                 "Financial Year", available["financial_year"], "2026-27"
#             )
#             return_page.select_financial_year(financial_year)

#             available = return_page.capture_filter_options()
#             quarter = ask_choice(
#                 "Quarter", available["quarter"], "Quarter 1 (Apr - Jun)"
#             )
#             return_page.select_quarter(quarter)

#             available = return_page.capture_filter_options()
#             period = ask_choice("Period", available["period"], "April")
#             return_page.select_period(period)
#             selected_filters = return_page.capture_filters()
#             print_selected_filters(selected_filters)
#             month_folder = prepare_period_folders(selected_filters)
#             print(f"Downloads will be organized under: {month_folder}")

#             return_page.search()
#             print_return_cards(return_page.capture_search_results())

#             if (
#                 return_page.gstr3b_download_button.count()
#                 and return_page.gstr3b_download_button.is_visible()
#             ):
#                 click_and_monitor_download(
#                     page,
#                     context,
#                     return_page.gstr3b_download_button,
#                     "GSTR-3B return",
#                 )

#             gstr1_view_button = return_page.gstr1_card.get_by_role(
#                 "button", name=re.compile(r"^\s*VIEW\s*$", re.IGNORECASE)
#             )
#             if gstr1_view_button.count() and gstr1_view_button.is_visible():
#                 gstr1_view_button.click(timeout=UI_TIMEOUT_MS)
#                 page.wait_for_url("**/returns/auth/gstr1", timeout=UI_TIMEOUT_MS)

#                 gstr1_page = page_objects["GSTR1Page"](page)
#                 if (
#                     gstr1_page.view_summary_button.count()
#                     and gstr1_page.view_summary_button.is_visible()
#                 ):
#                     gstr1_page.view_summary_button.click(timeout=UI_TIMEOUT_MS)
#                     page.wait_for_url(
#                         "**/returns/auth/gstr1/gstr1sum",
#                         timeout=UI_TIMEOUT_MS,
#                     )
#                     summary_page = page_objects["GSTR1SummaryPage"](page)
#                     click_and_monitor_download(
#                         page,
#                         context,
#                         summary_page.download_pdf_button,
#                         "GSTR-1 summary PDF",
#                     )
#             else:
#                 print("GSTR-1 VIEW button is not available for the selected period.")

#             logout_page = page_objects["LogoutPage"](page)
#             logout_page.account_dropdown.wait_for(
#                 state="visible", timeout=UI_TIMEOUT_MS
#             )
#             logout_page.open_account_dropdown()
#             logout_page.logout_link.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
#             logout_page.click_logout()
#             page.get_by_text(
#                 "You have successfully logged out of GST Portal.",
#                 exact=True,
#             ).wait_for(state="visible", timeout=UI_TIMEOUT_MS)
#             print("Logout confirmed. Workflow complete.")
#         except Exception:
#             print("Workflow failed; attempting logout before closing the browser.")
#             best_effort_logout(page, page_objects["LogoutPage"])
#             raise
#         finally:
#             browser.close()


# if __name__ == "__main__":
#     run()
"""Run the GST acknowledgment workflow using the numbered page objects.

Single-file version: includes the Whisper audio CAPTCHA solver.
Requires: playwright, python-dotenv, openai-whisper, numpy, miniaudio
(FFmpeg optional -- a built-in WAV decoder is used when ffmpeg is not found).
"""

import getpass
import os
import re
import shutil
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from dotenv import dotenv_values
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

try:
    import whisper
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

from _01_service_page import ServicePage
from _02_popup import PopupPage
from _03_landing_page import LandingPage
from _04_return_dashboard import ReturnDashboardPage
from _05_offline_download import OfflineDownloadPage
from _06_gstr1_page import GSTR1Page
from _07_gstr1_summary import GSTR1SummaryPage
from _08_logout_page import LogoutPage
from _09_site_footer import SiteFooterPage


if getattr(sys, "frozen", False):  # PyInstaller exe: use the exe's folder
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
# Paths are configured in this script's .env file.
DOTENV_VALUES = dotenv_values(SCRIPT_DIR / ".env")
FFMPEG_PATH = Path(DOTENV_VALUES.get("FFMPEG_PATH", "")).expanduser()
MODEL_DIR = Path(DOTENV_VALUES.get("WHISPER_MODEL_DIR", "")).expanduser()
if not FFMPEG_PATH.is_absolute():
    FFMPEG_PATH = SCRIPT_DIR / FFMPEG_PATH
if not MODEL_DIR.is_absolute():
    MODEL_DIR = SCRIPT_DIR / MODEL_DIR

os.environ["PATH"] = str(FFMPEG_PATH.parent) + os.pathsep + os.environ.get("PATH", "")
print(f"[setup] ffmpeg  : {shutil.which('ffmpeg') or 'NOT FOUND'}")
print(f"[setup] base.pt : {'found' if (MODEL_DIR / 'base.pt').exists() else 'NOT FOUND'}")


# ======================= AUDIO CAPTCHA SOLVER =======================

AUDIO_URL_PART = "/services/audiocaptcha"
CAPTCHA_DIGITS = 6

_NUM_WORDS = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2",
    "three": "3", "tree": "3",
    "four": "4", "for": "4", "fore": "4",
    "five": "5",
    "six": "6", "sex": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9",
}


def words_to_digits(text: str) -> str:
    """Convert spelled-out numbers (and common Whisper homophones) to digits."""
    out = []
    for tok in re.findall(r"[a-zA-Z]+|\d", (text or "").lower()):
        out.append(tok if tok.isdigit() else _NUM_WORDS.get(tok, tok))
    return "".join(out)


def load_wav_16k(path: Path) -> np.ndarray:
    """Decode a PCM WAV to mono float32 at 16 kHz without FFmpeg."""
    with wave.open(str(path), "rb") as wf:
        channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {width} bytes")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        n_out = int(len(data) * 16000 / rate)
        data = np.interp(
            np.linspace(0, len(data) - 1, n_out), np.arange(len(data)), data
        ).astype(np.float32)
    return data


def decode_audio_16k(data: bytes) -> np.ndarray:
    """Decode audio bytes (WAV, MP3, FLAC, OGG) to mono float32 at 16 kHz
    without FFmpeg. WAV uses the stdlib; other formats use `miniaudio`
    (pip install miniaudio)."""
    if data[:4] == b"RIFF":
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        path = Path(name)
        try:
            path.write_bytes(data)
            return load_wav_16k(path)
        finally:
            path.unlink(missing_ok=True)
    try:
        import miniaudio
    except ImportError as e:
        raise RuntimeError(
            f"Audio is not WAV (first bytes: {data[:12]!r}). "
            "Install a decoder: pip install miniaudio  (or install FFmpeg)."
        ) from e
    decoded = miniaudio.decode(
        data,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=16000,
    )
    return np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0


class AudioCaptchaSolver:
    """Clicks the speaker button, captures the browser's audio response and
    transcribes it with Whisper. The model loads lazily on first use."""

    def __init__(self, digits: int = CAPTCHA_DIGITS, model_name: str = "base", log=print):
        self.digits = digits
        self.model_name = model_name
        self.log = log
        self._model = None
        self._load_failed = False

    @property
    def model(self):
        if self._model is None and not self._load_failed:
            if not HAS_WHISPER:
                self.log("openai-whisper not installed; audio CAPTCHA solving disabled.")
                self._load_failed = True
            else:
                try:
                    model_dir = MODEL_DIR
                    model_dir.mkdir(exist_ok=True)
                    self._model = whisper.load_model(
                        self.model_name, download_root=str(model_dir)
                    )
                    self.log("Whisper model loaded.")
                except Exception as e:
                    self.log(f"Whisper init failed: {e}")
                    self._load_failed = True
        return self._model

    @property
    def available(self) -> bool:
        return self.model is not None

    def solve(self, page: Page) -> Optional[str]:
        """One attempt. Returns the digits, or None on any failure."""
        if not self.available:
            return None
        tmp = None
        try:
            # The <audio> element only exists after the speaker button is
            # clicked, so capture the browser's own request/response.
            play_btn = page.locator("button:has(i.fa-volume-up)")
            play_btn.wait_for(state="visible", timeout=10_000)
            with page.expect_response(
                lambda r: AUDIO_URL_PART in r.url, timeout=15_000
            ) as resp_info:
                play_btn.click()
            resp = resp_info.value
            body = resp.body() if resp.ok else b""
            if len(body) < 1000:
                self.log(
                    f"Audio fetch failed: status={resp.status}, size={len(body)}, "
                    f"content-type={resp.headers.get('content-type')!r}"
                )
                return None

            fd, name = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            tmp = Path(name)
            tmp.write_bytes(body)

            self.log(f"Audio: {len(body)} bytes, starts with {body[:8]!r}")
            audio_input = str(tmp) if shutil.which("ffmpeg") else decode_audio_16k(body)
            result = self.model.transcribe(
                audio_input, language="en", fp16=False,
                initial_prompt="Digits: 1 2 3 4 5 6 7 8 9 0",
            )
            heard = result.get("text", "")
            text = re.sub(r"\D", "", words_to_digits(heard))
            if len(text) != self.digits:
                self.log(f"Heard {heard!r} -> {text!r}, expected {self.digits} digits.")
                return None
            self.log(f"CAPTCHA transcribed: {text}")
            return text
        except Exception as e:
            self.log(f"Audio CAPTCHA solve error: {e}")
            return None
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)

    def refresh(self, page: Page):
        """Click the refresh icon to draw a new CAPTCHA."""
        try:
            btn = page.locator("button:has(i.fa-refresh)")
            btn.wait_for(state="visible", timeout=5_000)
            btn.click(timeout=15_000)  # waits while disabled during playback
            page.wait_for_timeout(1_000)
        except Exception as e:
            self.log(f"Could not refresh CAPTCHA: {e}")

    def solve_with_retries(self, page: Page, tries: int = 3) -> Optional[str]:
        """Try up to `tries` times, refreshing the CAPTCHA between attempts."""
        for i in range(1, tries + 1):
            text = self.solve(page)
            if text:
                return text
            self.log(f"CAPTCHA audio attempt {i}/{tries} failed.")
            if i < tries:
                self.refresh(page)
        return None


# ============================ WORKFLOW ============================

UI_TIMEOUT_MS = 60000
DOWNLOAD_TIMEOUT_MS = 90000
POPUP_TIMEOUT_MS = int(os.getenv("GST_POPUP_TIMEOUT_MS", "20000"))

# Whisper model loads lazily on first solve
_CAPTCHA_SOLVER = AudioCaptchaSolver()


def prompt_for_captcha(page, service_page):
    """Solve the audio CAPTCHA automatically; fall back to manual entry."""
    service_page.captcha_input.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    captcha_text = _CAPTCHA_SOLVER.solve_with_retries(page, tries=3)
    if captcha_text:
        service_page.fill_captcha(captcha_text)
        return

    # Fallback: manual entry
    audio_button = page.locator("button:has(i.fa-volume-up)")
    try:
        audio_button.wait_for(state="visible", timeout=3000)
        audio_button.click(timeout=5000)
        print("Auto-solve failed. CAPTCHA audio played; enter the digits below.")
    except PlaywrightTimeoutError:
        print("Auto-solve failed. Read the CAPTCHA in the browser.")

    captcha_text = input("CAPTCHA digits: ").strip()
    if not captcha_text:
        raise RuntimeError("No CAPTCHA was entered.")
    service_page.fill_captcha(captcha_text)


def load_page_objects():
    """Return the page object classes used by this workflow."""
    return {
        "ServicePage": ServicePage,
        "PopupPage": PopupPage,
        "LandingPage": LandingPage,
        "ReturnDashboardPage": ReturnDashboardPage,
        "OfflineDownloadPage": OfflineDownloadPage,
        "GSTR1Page": GSTR1Page,
        "GSTR1SummaryPage": GSTR1SummaryPage,
        "LogoutPage": LogoutPage,
        "SiteFooterPage": SiteFooterPage,
    }


def env_value(*names, default=""):
    """Read prefixed process variables first, then values from this folder's .env."""
    for name in names:
        if name.isupper():
            value = os.getenv(name)
            if value and value.strip():
                return value.strip()

    for name in names:
        value = DOTENV_VALUES.get(name)
        if value and value.strip():
            return value.strip()

    return default


DOWNLOAD_PATH = Path(
    env_value("DOWNLOAD_PATH", "download_path", default="gst_downloads")
).expanduser()
DOWNLOAD_BASE = DOWNLOAD_PATH if DOWNLOAD_PATH.is_absolute() else SCRIPT_DIR / DOWNLOAD_PATH
GSTIN_FOLDER = "GSTIN"
DOWNLOAD_DIR = DOWNLOAD_BASE / GSTIN_FOLDER


def prepare_period_folders(filters):
    """Create the GSTIN/financial-year/month/return folder structure."""
    global DOWNLOAD_DIR
    month_folder = (
        DOWNLOAD_BASE
        / GSTIN_FOLDER
        / filters["financial_year"]
        / filters["period"]
    )
    for return_type in ("GSTR-1", "GSTR-3B", "GSTR-6"):
        (month_folder / return_type).mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR = month_folder
    return month_folder


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


def parse_return_calendar(calendar_text):
    """Convert the calendar's multiline text into compact display rows."""
    lines = [line.strip() for line in calendar_text.splitlines() if line.strip()]
    return_type = ""
    rows = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if line.upper().startswith("GSTR-1 / IFF"):
            return_type = "GSTR-1/IFF"
        elif line.upper().startswith("GSTR-3B"):
            return_type = "GSTR-3B"
        else:
            period_match = re.fullmatch(r"([A-Za-z]{3})\s*-\s*(\d{4})", line)
            if period_match and return_type:
                status = lines[index + 1] if index + 1 < len(lines) else ""
                filed_date = "—"
                if index + 2 < len(lines) and lines[index + 2].lower().startswith("filed on"):
                    if index + 3 < len(lines):
                        filed_date = lines[index + 3]
                        index += 2
                rows.append(
                    (
                        return_type,
                        f"{period_match.group(1)} {period_match.group(2)}",
                        status,
                        filed_date,
                    )
                )
        index += 1

    return rows


def print_return_calendar(calendar_text):
    print("\nReturn calendar")
    print(f"  {'Return':<12} {'Period':<12} {'Status':<10} Filed on")
    print(f"  {'-' * 12} {'-' * 12} {'-' * 10} {'-' * 10}")
    for return_type, period, status, filed_date in parse_return_calendar(calendar_text):
        print(f"  {return_type:<12} {period:<12} {status:<10} {filed_date}")


def print_landing_details(details):
    print("\nLogin and portal details")
    print(f"  GSTIN          : {details['gstin']}")
    print(f"  Last login     : {details['last_login']}")
    print(f"  Current IP     : {details['ip']}")
    print(f"  Site updated   : {details['site_last_updated']}")
    print_return_calendar(details["return_calendar"])


def print_selected_filters(filters):
    print("\nSelected return period")
    print(f"  Financial year : {filters['financial_year']}")
    print(f"  Quarter        : {filters['quarter']}")
    print(f"  Period         : {filters['period']}")


def print_return_cards(cards):
    print("\nReturn dashboard results")
    print(f"  {'Return':<10} {'Status':<12} {'Actions':<34} Card")
    print(f"  {'-' * 10} {'-' * 12} {'-' * 34} {'-' * 12}")
    for name, card in cards.items():
        status = card.get("status", "—").replace("Status-", "").strip()
        actions = ", ".join(
            f"{button['label']}{'' if button['enabled'] else ' (disabled)'}"
            for button in card.get("buttons", [])
        ) or "—"
        card_state = "disabled" if card.get("disabled") else "available"
        due_date = card.get("due_date")
        if due_date:
            status = (
                f"Due Date - {due_date}"
                if "due date" in status.lower()
                else f"{status}; {due_date}" if status != "—" else due_date
            )
        print(f"  {name:<10} {status:<12} {actions:<34} {card_state}")


def save_download(download, description):
    return_type = "GSTR-3B" if "GSTR-3B" in description.upper() else "GSTR-1"
    destination_dir = DOWNLOAD_DIR / return_type
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / download.suggested_filename
    download.save_as(destination)
    print(f"{description} saved to: {destination}")


def click_and_monitor_download(page, context, button, description):
    """Click a control that should directly download a file, as in main.py."""
    original_url = page.url
    pages_before = set(context.pages)
    button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_event:
            button.click(timeout=UI_TIMEOUT_MS)
        save_download(download_event.value, description)
        return
    except PlaywrightTimeoutError:
        new_pages = [candidate for candidate in context.pages if candidate not in pages_before]

        if new_pages:
            opened_page = new_pages[-1]
            print(f"{description} opened in a browser tab: {opened_page.url}")
            input("Download it from that tab, then press Enter to continue.")
            return

        if page.url != original_url:
            print(f"{description} opened in this tab: {page.url}")
            input("Download it from the browser, then press Enter to continue.")
            page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
            page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)
            return

        raise RuntimeError(
            f"No download or document tab appeared for {description}. "
            "Check the GST page and try again."
        )


def click_download_or_get_page(page, context, button, description):
    """Return after a direct download, or return the page reached by the click."""
    original_url = page.url
    pages_before = set(context.pages)
    button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_event:
            button.click(timeout=UI_TIMEOUT_MS)
        save_download(download_event.value, description)
        return page, "download", original_url, False
    except PlaywrightTimeoutError:
        new_pages = [candidate for candidate in context.pages if candidate not in pages_before]
        if new_pages:
            target_page = new_pages[-1]
            print(f"{description} opened a page: {target_page.url}")
            return target_page, "page", original_url, True

        if page.url != original_url:
            print(f"{description} opened this page: {page.url}")
            return page, "page", original_url, False

        raise RuntimeError(
            f"No download or page appeared for {description}. "
            "Check the GST page and try again."
        )


def download_with_offline_page(page, context, button, description, offline_page_class):
    """Handle a direct Excel download or the GST offline Excel-download page."""
    target_page, outcome, original_url, is_new_page = click_download_or_get_page(
        page, context, button, description
    )
    if outcome == "download":
        return

    if "offlinedownload" in target_page.url.lower():
        offline_page = offline_page_class(target_page)
        click_and_monitor_download(
            target_page,
            context,
            offline_page.excel_download_button,
            description,
        )
        if is_new_page:
            target_page.close()
        else:
            target_page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
            target_page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)
        return

    input("The download opened another GST page. Complete the download there, then press Enter.")
    if is_new_page:
        target_page.close()
    else:
        target_page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
        target_page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)


def capture_site_date(page, site_footer_class):
    try:
        return site_footer_class(page).capture_site_last_updated_date()
    except PlaywrightTimeoutError:
        return "Site update date was not visible on this page."


def best_effort_logout(page, logout_page_class):
    """Try to end an authenticated GST session without hiding the original error."""
    if page.is_closed() or "/auth/" not in page.url.lower():
        return

    try:
        # Keep failure cleanup bounded when the page is already in an error state.
        page.set_default_timeout(5000)
        logout_page = logout_page_class(page)
        logout_page.account_dropdown.wait_for(state="visible", timeout=5000)
        logout_page.open_account_dropdown()
        logout_page.logout_link.wait_for(state="visible", timeout=5000)
        logout_page.click_logout()
        page.get_by_text(
            "You have successfully logged out of GST Portal.",
            exact=True,
        ).wait_for(state="visible", timeout=5000)
        print("Error cleanup: logout confirmed.")
    except Exception as cleanup_error:
        print(f"Error cleanup: logout could not be confirmed: {cleanup_error}")


def run():
    page_objects = load_page_objects()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(UI_TIMEOUT_MS)
        page.set_default_navigation_timeout(UI_TIMEOUT_MS)

        try:
            username = env_value("GST_USERNAME", "username") or input("GST username: ").strip()
            password = env_value("GST_PASSWORD", "password") or getpass.getpass(
                "GST password: "
            )

            page.goto(
                "https://services.gst.gov.in/services/login",
                wait_until="domcontentloaded",
            )

            service_page = page_objects["ServicePage"](page)
            service_page.fill_credentials(username, password)
            prompt_for_captcha(page, service_page)
            service_page.click_login()

            try:
                page.wait_for_url("**/services/auth/fowelcome", timeout=UI_TIMEOUT_MS)
            except PlaywrightTimeoutError as error:
                page.screenshot(path=SCRIPT_DIR / "gst-login-error.png", full_page=True)
                raise RuntimeError("Login did not reach the GST welcome page.") from error

            popup_page = page_objects["PopupPage"](page)
            if popup_page.handle_if_present(POPUP_TIMEOUT_MS):
                print("Post-login popup dismissed.")
            else:
                print("No post-login popup appeared; continuing.")

            landing_page = page_objects["LandingPage"](page)
            global GSTIN_FOLDER, DOWNLOAD_DIR
            GSTIN_FOLDER = landing_page.capture_gstin()
            DOWNLOAD_DIR = DOWNLOAD_BASE / GSTIN_FOLDER
            print_landing_details(
                {
                    "gstin": GSTIN_FOLDER,
                    "last_login": landing_page.capture_last_login(),
                    "ip": landing_page.capture_ip(),
                    "return_calendar": landing_page.capture_return_calendar(),
                    "site_last_updated": capture_site_date(
                        page, page_objects["SiteFooterPage"]
                    ),
                }
            )

            landing_page.open_return_dashboard()
            page.wait_for_url("**/returns/auth/dashboard", timeout=UI_TIMEOUT_MS)

            return_page = page_objects["ReturnDashboardPage"](page)
            available = return_page.capture_filter_options()
            financial_year = ask_choice(
                "Financial Year", available["financial_year"], "2026-27"
            )
            return_page.select_financial_year(financial_year)

            available = return_page.capture_filter_options()
            quarter = ask_choice(
                "Quarter", available["quarter"], "Quarter 1 (Apr - Jun)"
            )
            return_page.select_quarter(quarter)

            available = return_page.capture_filter_options()
            period = ask_choice("Period", available["period"], "April")
            return_page.select_period(period)
            selected_filters = return_page.capture_filters()
            print_selected_filters(selected_filters)
            month_folder = prepare_period_folders(selected_filters)
            print(f"Downloads will be organized under: {month_folder}")

            return_page.search()
            print_return_cards(return_page.capture_search_results())

            if (
                return_page.gstr3b_download_button.count()
                and return_page.gstr3b_download_button.is_visible()
            ):
                click_and_monitor_download(
                    page,
                    context,
                    return_page.gstr3b_download_button,
                    "GSTR-3B return",
                )

            gstr1_view_button = return_page.gstr1_card.get_by_role(
                "button", name=re.compile(r"^\s*VIEW\s*$", re.IGNORECASE)
            )
            if gstr1_view_button.count() and gstr1_view_button.is_visible():
                gstr1_view_button.click(timeout=UI_TIMEOUT_MS)
                page.wait_for_url("**/returns/auth/gstr1", timeout=UI_TIMEOUT_MS)

                gstr1_page = page_objects["GSTR1Page"](page)
                if (
                    gstr1_page.view_summary_button.count()
                    and gstr1_page.view_summary_button.is_visible()
                ):
                    gstr1_page.view_summary_button.click(timeout=UI_TIMEOUT_MS)
                    page.wait_for_url(
                        "**/returns/auth/gstr1/gstr1sum",
                        timeout=UI_TIMEOUT_MS,
                    )
                    summary_page = page_objects["GSTR1SummaryPage"](page)
                    click_and_monitor_download(
                        page,
                        context,
                        summary_page.download_pdf_button,
                        "GSTR-1 summary PDF",
                    )
            else:
                print("GSTR-1 VIEW button is not available for the selected period.")

            logout_page = page_objects["LogoutPage"](page)
            logout_page.account_dropdown.wait_for(
                state="visible", timeout=UI_TIMEOUT_MS
            )
            logout_page.open_account_dropdown()
            logout_page.logout_link.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
            logout_page.click_logout()
            page.get_by_text(
                "You have successfully logged out of GST Portal.",
                exact=True,
            ).wait_for(state="visible", timeout=UI_TIMEOUT_MS)
            print("Logout confirmed. Workflow complete.")
        except Exception:
            print("Workflow failed; attempting logout before closing the browser.")
            best_effort_logout(page, page_objects["LogoutPage"])
            raise
        finally:
            browser.close()


def main():
    """Entry point. Set a breakpoint on run() and step in with F11."""
    run()


if __name__ == "__main__":
    main()
