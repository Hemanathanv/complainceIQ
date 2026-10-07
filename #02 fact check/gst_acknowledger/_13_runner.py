
"""Run the GST acknowledgment workflow using the numbered page objects.

Single-file version: includes the Whisper audio CAPTCHA solver.
Requires: playwright, python-dotenv, openai-whisper, numpy, miniaudio
(FFmpeg optional -- a built-in WAV decoder is used when ffmpeg is not found).
"""

import getpass
import os
import re
import calendar
import time
import shutil
import sys
import tempfile
import wave
from datetime import date, datetime
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
from _10_gstr6_page import GSTR6Page


if getattr(sys, "frozen", False):  # PyInstaller exe: use the exe's folder
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent


class TeeStream:
    """Write console output to both the original stream and the run log."""

    def __init__(self, console_stream, log_stream):
        self.console_stream = console_stream
        self.log_stream = log_stream

    def write(self, text):
        self.console_stream.write(text)
        self.log_stream.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.console_stream.flush()
        self.log_stream.flush()

    def isatty(self):
        return self.console_stream.isatty()

    @property
    def encoding(self):
        return self.console_stream.encoding

    def fileno(self):
        return self.console_stream.fileno()


LOG_DIR = SCRIPT_DIR / "downloads"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "gst_acknowledger.log"
_LOG_STREAM = LOG_FILE.open("a", encoding="utf-8", buffering=1)
_CONSOLE_STDOUT = sys.stdout
_CONSOLE_STDERR = sys.stderr
sys.stdout = TeeStream(_CONSOLE_STDOUT, _LOG_STREAM)
sys.stderr = TeeStream(_CONSOLE_STDERR, _LOG_STREAM)
print(f"\n===== GST workflow started {datetime.now().astimezone().isoformat(timespec='seconds')} =====")

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


def prompt_for_captcha(page, service_page, force_manual=False):
    """Solve the audio CAPTCHA automatically; fall back to manual entry."""
    service_page.captcha_input.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    captcha_text = None
    if not force_manual:
        captcha_text = _CAPTCHA_SOLVER.solve_with_retries(page, tries=3)
    if captcha_text:
        service_page.fill_captcha(captcha_text)
        return

    # Fallback: manual entry
    if force_manual:
        print("Automated CAPTCHA was rejected. Read the current CAPTCHA image.")
    else:
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
        "GSTR6Page": GSTR6Page,
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
DOWNLOAD_ROOT = DOWNLOAD_BASE / GSTIN_FOLDER
PERIOD_OUTPUT_DIRS = {}


def return_period_for_month(year, month):
    """Return the portal's financial year, quarter, and label for a month."""
    financial_year_start = year if month >= 4 else year - 1
    financial_year = f"{financial_year_start}-{str(financial_year_start + 1)[-2:]}"
    quarter_number = ((month - 4) % 12) // 3 + 1
    quarter_names = {
        1: "Apr - Jun",
        2: "Jul - Sep",
        3: "Oct - Dec",
        4: "Jan - Mar",
    }
    return {
        "financial_year": financial_year,
        "quarter_number": quarter_number,
        "quarter_hint": quarter_names[quarter_number],
        "month": calendar.month_name[month],
    }


def shift_month(today, offset):
    """Return a date shifted by whole calendar months, preserving the day."""
    month_index = today.year * 12 + today.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def current_and_previous_return_periods(today=None):
    """Return current and previous calendar-month targets in that order."""
    today = today or date.today()
    current = return_period_for_month(today.year, today.month)
    previous_date = shift_month(today, -1)
    previous = return_period_for_month(previous_date.year, previous_date.month)
    return [current, previous]


def available_return_periods(return_page, through_date=None):
    """Read current-financial-year period choices in oldest-first order."""
    through_date = through_date or date.today()
    current_financial_year_start = (
        through_date.year if through_date.month >= 4 else through_date.year - 1
    )
    periods = {}
    filters = return_page.capture_filter_options()
    year_labels = []
    for label in filters["financial_year"]:
        match = re.search(r"\b((?:19|20)\d{2})\b", label)
        if match:
            year_labels.append((int(match.group(1)), label))

    quarter_numbers = {
        "apr - jun": 1,
        "jul - sep": 2,
        "oct - dec": 3,
        "jan - mar": 4,
    }
    month_numbers = {
        name.casefold(): number
        for number, name in enumerate(calendar.month_name)
        if name
    }

    for financial_year_start, year_label in sorted(year_labels):
        # The dashboard can retain prior financial years. Only scan the current
        # Indian financial year (April through March) and ignore older options.
        if financial_year_start < current_financial_year_start:
            continue
        return_page.select_financial_year(year_label)
        filters = return_page.capture_filter_options()
        quarters = filters["quarter"]
        if quarters:
            quarter_choices = []
            for quarter_label in quarters:
                match = re.search(r"\b([1-4])\b", quarter_label)
                quarter_number = int(match.group(1)) if match else None
                if quarter_number is None:
                    normalized = re.sub(r"\s+", " ", quarter_label.casefold())
                    quarter_number = next(
                        (number for hint, number in quarter_numbers.items()
                         if hint in normalized),
                        None,
                    )
                if quarter_number is not None:
                    quarter_choices.append((quarter_number, quarter_label))
        else:
            quarter_choices = [(None, None)]

        for quarter_number, quarter_label in sorted(
            quarter_choices, key=lambda choice: choice[0] or 0
        ):
            if quarter_label is not None:
                quarter_start_month = {1: 4, 2: 7, 3: 10, 4: 1}[quarter_number]
                quarter_start_year = (
                    financial_year_start + 1
                    if quarter_number == 4
                    else financial_year_start
                )
                if date(quarter_start_year, quarter_start_month, 1) > date(
                    through_date.year, through_date.month, 1
                ):
                    continue
                return_page.select_quarter(quarter_label)
            filters = return_page.capture_filter_options()
            for period_label in filters["period"]:
                normalized_period = period_label.strip().casefold()
                month_number = next(
                    (
                        number
                        for name, number in month_numbers.items()
                        if normalized_period == name
                        or normalized_period.startswith(name[:3])
                    ),
                    None,
                )
                if month_number is None:
                    continue
                calendar_year = (
                    financial_year_start
                    if month_number >= 4
                    else financial_year_start + 1
                )
                period_date = date(calendar_year, month_number, 1)
                if period_date > date(through_date.year, through_date.month, 1):
                    continue
                target = return_period_for_month(calendar_year, month_number)
                target["financial_year"] = year_label
                periods[(calendar_year, month_number)] = target

    return [periods[key] for key in sorted(periods)]


def return_card_state(card):
    """Classify a return card as filed, due, missing, or unknown."""
    if not card:
        return "missing"
    text = " ".join(
        str(card.get(key, "")) for key in ("status", "due_date", "text")
    )
    pending_label = re.search(
        r"\b(?:to\s+be|not)\s+filed\b", text, re.IGNORECASE
    )
    if re.search(r"\bFiled\b", text, re.IGNORECASE) and not pending_label:
        return "filed"
    if pending_label or re.search(r"\bDue\s*Date\b|\bDue\b", text, re.IGNORECASE):
        return "due"
    return "unknown"


def matching_option(options, target, label):
    """Find a live option by case-insensitive exact label, ignoring spacing."""
    normalize = lambda value: re.sub(r"\s+", " ", value.strip()).casefold()
    for option in options:
        if normalize(option) == normalize(target):
            return option
    raise RuntimeError(
        f"{label} '{target}' is not available in the GST portal options: {options}"
    )



def select_return_period(return_page, target):
    """Select a target month using the portal's live filter labels."""
    options = return_page.capture_filter_options()
    financial_year = matching_option(
        options["financial_year"], target["financial_year"], "Financial year"
    )
    return_page.select_financial_year(financial_year)

    options = return_page.capture_filter_options()
    if options["quarter"]:
        quarter_candidates = [
            option for option in options["quarter"]
            if re.search(rf"\b{target['quarter_number']}\b", option)
            and target["quarter_hint"].casefold() in option.casefold()
        ]
        if not quarter_candidates:
            quarter_candidates = [
                option for option in options["quarter"]
                if target["quarter_hint"].casefold() in option.casefold()
            ]
        if len(quarter_candidates) != 1:
            raise RuntimeError(
                f"Could not uniquely identify quarter {target['quarter_number']} "
                f"({target['quarter_hint']}) from portal options: {options['quarter']}"
            )
        return_page.select_quarter(quarter_candidates[0])

    options = return_page.capture_filter_options()
    period = matching_option(options["period"], target["month"], "Period")
    return_page.select_period(period)
    selected = return_page.capture_filters()
    return selected


def prepare_period_folders(selected, return_cards):
    """Create GSTIN/FY/month folders for the return types in this search grid."""
    year_folder = DOWNLOAD_ROOT / selected["financial_year"]
    month_folder = year_folder / selected["period"]
    PERIOD_OUTPUT_DIRS.clear()
    for return_type in return_cards:
        folder = month_folder / return_type
        PERIOD_OUTPUT_DIRS[return_type] = folder
        folder.mkdir(parents=True, exist_ok=True)
    return month_folder


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
    rows = parse_return_calendar(calendar_text)
    if not rows:
        print("\nReturn calendar not found on the landing page.")
        return
    print("\nReturn calendar")
    print(f"  {'Return':<12} {'Period':<12} {'Status':<10} Filed on")
    print(f"  {'-' * 12} {'-' * 12} {'-' * 10} {'-' * 10}")
    for return_type, period, status, filed_date in rows:
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


def save_download(download, description, return_type=None):
    if return_type is None:
        return_type = "GSTR-3B" if "GSTR-3B" in description.upper() else "GSTR-1"
    destination_dir = PERIOD_OUTPUT_DIRS.get(return_type, DOWNLOAD_ROOT)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / download.suggested_filename
    download.save_as(destination)
    print(f"{description} saved to: {destination}")


def click_and_monitor_download(page, context, button, description, return_type=None):
    """Click a control that should directly download a file, as in main.py."""
    original_url = page.url
    pages_before = set(context.pages)
    button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_event:
            button.click(timeout=UI_TIMEOUT_MS)
        save_download(download_event.value, description, return_type)
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


def return_to_return_dashboard(page):
    """Navigate back from GSTR-1 summary/detail pages to the return dashboard."""
    current_url = page.url.lower()
    if "/returns/auth/gstr1/gstr1sum" in current_url:
        page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
        page.wait_for_url("**/returns/auth/gstr1", timeout=UI_TIMEOUT_MS)
    if "/returns/auth/gstr1" in page.url.lower():
        page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
        page.wait_for_url("**/returns/auth/dashboard", timeout=UI_TIMEOUT_MS)


def visible_button_with_text(page, expected_text):
    """Find a visible clickable control by rendered text, ignoring spacing/case."""
    expected = " ".join(expected_text.split()).casefold()
    controls = page.locator(
        "button, a, [role='button'], input[type='button'], input[type='submit']"
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for control in controls.all():
            try:
                actual = control.evaluate(
                    "el => el.innerText || el.value || el.getAttribute('aria-label') || ''"
                )
                actual = " ".join(actual.split()).casefold()
                if (
                    expected in actual
                    and control.is_visible()
                    and control.is_enabled()
                ):
                    return control
            except Exception:
                continue
        page.wait_for_timeout(250)
    return None


def download_filed_gstr6(page, context, page_objects):
    """Download a filed GSTR-6 PDF and return to the dashboard."""
    return_page = page_objects["ReturnDashboardPage"](page)
    return_page.gstr6_view_button.wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    return_page.gstr6_view_button.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url("**/returns/auth/gstr6", timeout=UI_TIMEOUT_MS)
    gstr6_page = page_objects["GSTR6Page"](page)
    click_and_monitor_download(
        page, context, gstr6_page.download_filed_pdf_button,
        "Filed GSTR-6 PDF", return_type="GSTR-6"
    )
    gstr6_page.back_to_returns_link.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url("**/returns/auth/dashboard", timeout=UI_TIMEOUT_MS)


def download_gstr2b(page, context, page_objects):
    """Open a GSTR-2B summary page, download its PDF, then return."""
    return_page = page_objects["ReturnDashboardPage"](page)
    view_button = None
    for candidate in return_page.gstr2b_card.locator("button").all():
        try:
            label = " ".join(candidate.inner_text().split()).casefold()
            if label == "view" and candidate.is_visible() and candidate.is_enabled():
                view_button = candidate
                break
        except Exception:
            continue
    if view_button is None:
        print("GSTR-2B has no visible VIEW button for this period; skipping it.")
        return False

    original_url = page.url
    pages_before = set(context.pages)
    detail_page = None
    is_new_page = False
    try:
        with page.expect_popup(timeout=5000) as popup_event:
            view_button.click(timeout=UI_TIMEOUT_MS)
        detail_page = popup_event.value
        is_new_page = True
    except PlaywrightTimeoutError:
        new_pages = [candidate for candidate in context.pages if candidate not in pages_before]
        if new_pages:
            detail_page = new_pages[-1]
            is_new_page = True
        elif page.url != original_url:
            detail_page = page
        else:
            raise RuntimeError("Clicking GSTR-2B VIEW did not open its summary page.")

    try:
        detail_page.wait_for_url("**/gstr2b/auth/gstr2b/summary", timeout=UI_TIMEOUT_MS)
        summary_pdf = visible_button_with_text(
            detail_page, "DOWNLOAD GSTR-2B SUMMARY (PDF)"
        )
        if summary_pdf is None:
            page_text = detail_page.locator("body").inner_text().casefold()
            if "gstr-2b could not be generated by the system" in page_text:
                print(
                    "GSTR-2B unavailable for this period: the GST portal could not "
                    "generate it. The portal says to compute it manually from the "
                    "IMS Dashboard (Services > Returns > Invoice Management System > "
                    "Inward Supplies). Skipping GSTR-2B and continuing with other returns."
                )
            else:
                print(
                    "GSTR-2B summary PDF button was not found. The browser is paused "
                    "so you can inspect the page."
                )
                print(f"Current page: {detail_page.url}")
                input("Inspect the GST page in the browser, then press Enter to continue.")
            return False
        click_and_monitor_download(
            detail_page, context, summary_pdf, "GSTR-2B summary PDF", return_type="GSTR-2B"
        )
    finally:
        if is_new_page and detail_page and not detail_page.is_closed():
            detail_page.close()
        elif detail_page is page and page.url != original_url:
            page.go_back(wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
            page.wait_for_url(original_url, timeout=UI_TIMEOUT_MS)
    return True


def download_filed_gstr1(page, context, page_objects):
    """Download the filed GSTR-1 summary PDF and return to the dashboard."""
    return_page = page_objects["ReturnDashboardPage"](page)
    view_button = return_page.gstr1_card.get_by_role(
        "button", name=re.compile(r"^\s*VIEW\s*$", re.IGNORECASE)
    )
    if not view_button.count() or not view_button.is_visible():
        print("Filed GSTR-1 has no visible VIEW button; leaving it pending.")
        return False

    view_button.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url("**/returns/auth/gstr1", timeout=UI_TIMEOUT_MS)
    gstr1_page = page_objects["GSTR1Page"](page)
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('button')).some(button => {
            if (!button.getClientRects().length) return false;
            const text = (button.innerText || '').replace(/\\s+/g, ' ').trim().toUpperCase();
            return text.includes('VIEW SUMMARY') || text.includes('DOWNLOAD FILED (PDF)');
        })""",
        timeout=UI_TIMEOUT_MS,
    )
    summary_button = gstr1_page.view_summary_button
    if summary_button.count() and summary_button.is_visible():
        summary_button.click(timeout=UI_TIMEOUT_MS)
        page.wait_for_url("**/returns/auth/gstr1/gstr1sum", timeout=UI_TIMEOUT_MS)
        summary_page = page_objects["GSTR1SummaryPage"](page)
        click_and_monitor_download(
            page,
            context,
            summary_page.download_pdf_button,
            "GSTR-1 summary PDF",
            return_type="GSTR-1",
        )
    else:
        direct_pdf = visible_button_with_text(page, "DOWNLOAD FILED (PDF)")
        if direct_pdf is None:
            print(
                "Filed GSTR-1 page has neither VIEW SUMMARY nor DOWNLOAD FILED "
                "(PDF); leaving it pending."
            )
            return_to_return_dashboard(page)
            return False
        click_and_monitor_download(
            page,
            context,
            direct_pdf,
            "GSTR-1 filed PDF",
            return_type="GSTR-1",
        )
    return_to_return_dashboard(page)
    return True


def download_filed_return(return_type, page, context, page_objects):
    """Download a filed return using its return-specific page flow."""
    if return_type == "GSTR-2B":
        return download_gstr2b(page, context, page_objects)
    if return_type == "GSTR-1":
        return download_filed_gstr1(page, context, page_objects)
    if return_type == "GSTR-3B":
        return_page = page_objects["ReturnDashboardPage"](page)
        button = None
        # Prefer the portal's explicit Angular handler when it is present.
        explicit_download = page.locator(
            'button[data-ng-click="downloadGSTR3Bpdf()"]'
        )
        for candidate in explicit_download.all():
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    button = candidate
                    break
            except Exception:
                continue
        # Fall back to the labeled control inside the GSTR-3B dashboard card.
        if button is None:
            for candidate in return_page.gstr3b_card.locator("button").all():
                try:
                    label = " ".join(candidate.inner_text().split()).casefold()
                    if (
                        label == "download"
                        and candidate.is_visible()
                        and candidate.is_enabled()
                    ):
                        button = candidate
                        break
                except Exception:
                    continue
        if button is None:
            print("Filed GSTR-3B has no visible DOWNLOAD button; leaving it pending.")
            return False
        click_and_monitor_download(
            page, context, button, "GSTR-3B return", return_type="GSTR-3B"
        )
        return True
    if return_type == "GSTR-6":
        download_filed_gstr6(page, context, page_objects)
        return True
    return False


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
            login_complete = False
            # A valid-looking Whisper transcription can still be wrong. Retry
            # with a fresh CAPTCHA up to three times, then offer manual entry.
            for login_attempt in range(1, 5):
                # GST clears password and CAPTCHA fields after a rejected attempt.
                service_page.fill_credentials(username, password)
                prompt_for_captcha(
                    page, service_page, force_manual=(login_attempt == 4)
                )
                print("CAPTCHA filled. Submitting login...")
                service_page.click_login()

                try:
                    page.wait_for_function(
                        """() => {
                            const url = location.href.toLowerCase();
                            const text = (document.body?.innerText || '').toLowerCase();
                            return url.includes('/services/auth/fowelcome') ||
                                text.includes('enter valid letters shown') ||
                                /invalid captcha|captcha.*invalid|incorrect username|incorrect password/.test(text);
                        }""",
                        timeout=UI_TIMEOUT_MS,
                    )
                except PlaywrightTimeoutError:
                    # Some portal errors do not include readable validation text;
                    # if still on login, treat the submission as rejected and retry.
                    pass

                if "/services/auth/fowelcome" in page.url.lower():
                    login_complete = True
                    break

                if "/services/login" in page.url.lower() and login_attempt < 4:
                    error_text = " ".join(
                        text.strip()
                        for text in page.locator(
                            ".text-danger, .alert-danger, .has-error"
                        ).all_inner_texts()
                        if text.strip()
                    )
                    if error_text:
                        print(f"GST login response: {error_text}")
                    if login_attempt < 3:
                        print(
                            f"CAPTCHA was rejected; refreshing and retrying "
                            f"automatically ({login_attempt}/3)."
                        )
                        _CAPTCHA_SOLVER.refresh(page)
                    else:
                        print(
                            "Three automatic CAPTCHA submissions were rejected. "
                            "Please enter the CAPTCHA shown in the browser."
                        )
                        _CAPTCHA_SOLVER.refresh(page)
                    continue

                if "/services/login" in page.url.lower():
                    page.screenshot(
                        path=SCRIPT_DIR / "gst-login-error.png", full_page=True
                    )
                    raise RuntimeError(
                        "Login did not succeed after three automatic CAPTCHA "
                        "retries and one manual attempt. See gst-login-error.png "
                        "for the portal response."
                    )

                raise RuntimeError(
                    f"Login ended on an unexpected page: {page.url}"
                )

            if not login_complete:
                raise RuntimeError("Login did not reach the GST welcome page.")

            popup_page = page_objects["PopupPage"](page)
            if popup_page.handle_if_present(POPUP_TIMEOUT_MS):
                print("Post-login popup dismissed.")
            else:
                print("No post-login popup appeared; continuing.")

            landing_page = page_objects["LandingPage"](page)
            global GSTIN_FOLDER, DOWNLOAD_ROOT
            GSTIN_FOLDER = landing_page.capture_gstin()
            DOWNLOAD_ROOT = DOWNLOAD_BASE / GSTIN_FOLDER
            return_calendar = landing_page.capture_return_calendar()
            print_landing_details(
                {
                    "gstin": GSTIN_FOLDER,
                    "last_login": landing_page.capture_last_login(),
                    "ip": landing_page.capture_ip(),
                    "return_calendar": return_calendar,
                    "site_last_updated": capture_site_date(
                        page, page_objects["SiteFooterPage"]
                    ),
                }
            )

            if not parse_return_calendar(return_calendar):
                print("Looking for the Return Dashboard button to continue.")
                landing_page.return_dashboard_button.wait_for(
                    state="visible", timeout=UI_TIMEOUT_MS
                )
            landing_page.open_return_dashboard()
            page.wait_for_url("**/returns/auth/dashboard", timeout=UI_TIMEOUT_MS)

            return_page = page_objects["ReturnDashboardPage"](page)
            execution_date = date.today()
            available_periods = available_return_periods(return_page, execution_date)
            if not available_periods:
                raise RuntimeError(
                    "No return periods were found in the GST portal dropdowns."
                )
            first_period = available_periods[0]
            last_period = available_periods[-1]
            print(
                f"Found {len(available_periods)} available periods in the portal; "
                f"processing oldest to newest ({first_period['month']} "
                f"{first_period['financial_year']} through {last_period['month']} "
                f"{last_period['financial_year']})."
            )

            def process_period(target_period):
                selected_filters = select_return_period(return_page, target_period)
                print(
                    f"Automatic period: {target_period['month']} "
                    f"{selected_filters['financial_year']}"
                    + (
                        f" (quarter {target_period['quarter_number']})."
                        if selected_filters["quarter"] != "Not available"
                        else "."
                    )
                )
                print_selected_filters(selected_filters)

                return_page.search()
                cards = return_page.capture_search_results()
                print_return_cards(cards)
                month_folder = prepare_period_folders(selected_filters, cards)
                print(f"Downloads will be organized under: {month_folder}")

                return_states = {
                    return_type: return_card_state(cards.get(return_type))
                    for return_type in ("GSTR-1", "GSTR-2B", "GSTR-3B", "GSTR-6")
                }
                grid_needs_reload = False
                for return_type in ("GSTR-2B", "GSTR-3B", "GSTR-1", "GSTR-6"):
                    if grid_needs_reload:
                        print(
                            f"Refreshing dashboard for {target_period['month']} "
                            f"{target_period['financial_year']} before checking "
                            f"{return_type}."
                        )
                        select_return_period(return_page, target_period)
                        return_page.search()
                        cards = return_page.capture_search_results()
                        return_states = {
                            name: return_card_state(cards.get(name))
                            for name in ("GSTR-1", "GSTR-2B", "GSTR-3B", "GSTR-6")
                        }
                    if return_type == "GSTR-2B":
                        action = return_page.gstr2b_view_button
                    elif return_type == "GSTR-3B":
                        action = return_page.gstr3b_download_button
                    elif return_type == "GSTR-1":
                        action = return_page.gstr1_card.get_by_role(
                            "button", name=re.compile(r"^\s*VIEW\s*$", re.IGNORECASE)
                        )
                    else:
                        action = return_page.gstr6_view_button
                    action_visible = any(
                        candidate.is_visible() for candidate in action.all()
                    )
                    if return_states[return_type] != "filed" and not action_visible:
                        continue
                    download_filed_return(return_type, page, context, page_objects)
                    # Navigating to a return page or downloading can reset the
                    # dashboard filters/results; reapply this period next time.
                    grid_needs_reload = True

                if (
                    "GSTR-1" in cards
                    and return_states["GSTR-1"] != "filed"
                ):
                    print(
                        "GSTR-1 VIEW button is not available for this selected period."
                    )

                return cards

            for target_period in available_periods:
                process_period(target_period)

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


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(f"WORKFLOW ERROR: {error}")
        raise SystemExit(1) from None

