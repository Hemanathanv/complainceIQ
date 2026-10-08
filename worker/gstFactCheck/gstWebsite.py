import re
import os
from pathlib import Path
from typing import List, Dict, Optional
from playwright.sync_api import sync_playwright, Page
import io
from datetime import datetime
from bs4 import BeautifulSoup
import pandas as pd
from loguru import logger

# Add local ffmpeg to PATH (for bundling with exe)
_SCRIPT_DIR = Path(__file__).parent.resolve()
os.environ["PATH"] = str(_SCRIPT_DIR) + os.pathsep + os.environ.get("PATH", "")


# Try to import Whisper for audio CAPTCHA
try:
    import whisper
    _HAS_WHISPER = True
except ImportError:
    _HAS_WHISPER = False
    logger.warning("CAPTCHA solving unavailable.")


# Whisper often transcribes the numeric audio captcha as spoken words
# ("one four three...") rather than digits. GST search captchas are numeric,
# so map number-words (and the common homophones Whisper emits) back to digits.
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


def _words_to_digits(text: str) -> str:
    """Convert spelled-out numbers in a captcha transcription to digits,
    leaving any genuine digits/letters intact."""
    out = []
    for tok in re.findall(r"[a-zA-Z]+|\d", (text or "").lower()):
        out.append(tok if tok.isdigit() else _NUM_WORDS.get(tok, tok))
    return "".join(out)


class GSTSearcher:
    """Automates GST taxpayer searches on GST portal using sync Playwright"""
    
    def __init__(
        self,
        gst_numbers: List[str],
        headless: bool = False,
        use_audio_captcha: bool = True,
        manual_captcha: bool = False,
        timeout: int = 30000
    ):
        """
        Initialize GST Searcher
        
        Args:
            gst_numbers: List of GST IDs to search
            headless: Run browser in headless mode
            use_ocr: Use PaddleOCR for image CAPTCHA solving
            use_audio_captcha: Use Whisper STT for audio CAPTCHA solving (preferred)
            manual_captcha: Pause for manual CAPTCHA entry
            timeout: Timeout in milliseconds for operations
        """
        self.gst_numbers = gst_numbers
        self.headless = headless
        self.use_audio_captcha = use_audio_captcha and _HAS_WHISPER
        self.manual_captcha = manual_captcha
        self.timeout = timeout
        self.results = []
        self.url = "https://services.gst.gov.in/services/searchtp"
        self.output_dir = Path("gst_results")
        self.output_dir.mkdir(exist_ok=True)
        
        # # Initialize PaddleOCR if available
        # self.ocr = None
        
        # if self.use_ocr:
        #     try:
        #         # PaddleOCR v3.x - models are managed automatically in ~/.paddlex
        #         self.ocr = PaddleOCR(lang='en')
        #         logger.info("PaddleOCR initialized successfully")
        #     except Exception as e:
        #         logger.error(f"Failed to initialize PaddleOCR: {e}")
        #         self.use_ocr = False
        
        # Initialize Whisper if available
        self.whisper_model = None
        
        if self.use_audio_captcha:
            try:
                # Determine the base path (works for both script and PyInstaller exe)
                import sys
                if getattr(sys, 'frozen', False):
                    # Running as compiled exe (PyInstaller)
                    base_path = Path(sys.executable).parent
                else:
                    # Running as script
                    base_path = Path(__file__).parent.resolve()
                
                # Model folder path
                model_dir = base_path / "model"
                model_dir.mkdir(exist_ok=True)
                
                # Use 'base' model for good balance of speed and accuracy
                # NOTE: Whisper requires FFmpeg installed on the system
                self.whisper_model = whisper.load_model("base", download_root=str(model_dir))
                logger.info(f"Initialized captcha model successfully (model dir: {model_dir})")
            except Exception as e:
                logger.error(f"Failed to initialize Whisper: {e}")
                logger.error("Make sure FFmpeg is installed: https://ffmpeg.org/download.html")
                self.use_audio_captcha = False
    
    def preprocess_captcha_image(self, image_path: Path) -> Path:
        """
        Preprocess CAPTCHA image to improve OCR accuracy
        - Remove background grid/checkered pattern using multiple techniques
        - Keep only the text characters
        """
        try:
            import cv2
            import numpy as np
            
            # Read image with OpenCV
            img = cv2.imread(str(image_path))
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Method 1: Detect grid lines using morphological operations
            # Create a mask for vertical and horizontal lines
            
            # Detect vertical lines
            vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 10))
            vertical_lines = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, vertical_kernel)
            vertical_lines = cv2.morphologyEx(vertical_lines, cv2.MORPH_OPEN, vertical_kernel)
            
            # Detect horizontal lines
            horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (10, 1))
            horizontal_lines = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, horizontal_kernel)
            horizontal_lines = cv2.morphologyEx(horizontal_lines, cv2.MORPH_OPEN, horizontal_kernel)
            
            # Combine to get grid mask
            grid_mask = cv2.addWeighted(vertical_lines, 0.5, horizontal_lines, 0.5, 0)
            _, grid_mask = cv2.threshold(grid_mask, 200, 255, cv2.THRESH_BINARY)
            
            # Inpaint to remove grid lines
            grid_mask_inv = cv2.bitwise_not(grid_mask)
            inpainted = cv2.inpaint(img, grid_mask_inv, 3, cv2.INPAINT_TELEA)
            
            # Convert to grayscale
            gray_inpainted = cv2.cvtColor(inpainted, cv2.COLOR_BGR2GRAY)
            
            # Method 2: Use Otsu thresholding for better binarization
            _, binary = cv2.threshold(gray_inpainted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # Method 3: Filter by contour area (keep only large contours = text)
            # Find contours
            contours, _ = cv2.findContours(cv2.bitwise_not(binary), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Create a clean image with only large contours (text characters)
            result = np.ones_like(binary) * 255  # White background
            
            for contour in contours:
                area = cv2.contourArea(contour)
                # Keep contours with reasonable area (text characters)
                # Grid noise has very small area, text has larger area
                if area > 50:  # Adjust this threshold based on your CAPTCHA
                    cv2.drawContours(result, [contour], -1, 0, cv2.FILLED)
            
            # Save processed image
            processed_path = self.output_dir / "temp_captcha_processed.png"
            cv2.imwrite(str(processed_path), result)
            
            # Also save intermediate results for debugging
            cv2.imwrite(str(self.output_dir / "temp_captcha_inpainted.png"), gray_inpainted)
            cv2.imwrite(str(self.output_dir / "temp_captcha_binary.png"), binary)
            
            logger.info(f"CAPTCHA preprocessed (grid removed via inpainting + contour filter): {processed_path}")
            return processed_path
            
        except ImportError:
            logger.warning("OpenCV not available, using basic preprocessing")
            return self._basic_preprocess(image_path)
        except Exception as e:
            logger.warning(f"Image preprocessing failed: {e}")
            return image_path
    
    def _basic_preprocess(self, image_path: Path) -> Path:
        """Basic preprocessing fallback without OpenCV"""
        try:
            from PIL import ImageEnhance, ImageFilter
            
            img = Image.open(image_path)
            img = img.convert('L')
            
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(2.0)
            img = img.filter(ImageFilter.SHARPEN)
            
            threshold = 128
            img = img.point(lambda p: 255 if p > threshold else 0)
            
            processed_path = self.output_dir / "temp_captcha_processed.png"
            img.save(processed_path)
            
            return processed_path
        except Exception as e:
            logger.warning(f"Basic preprocessing failed: {e}")
            return image_path
    
    def extract_captcha_text(self, page: Page) -> str:
        """
        Extract CAPTCHA text using PaddleOCR v3.x
        
        Args:
            page: Playwright page object
            
        Returns:
            Extracted CAPTCHA text or empty string
        """
        try:
            captcha_element = page.locator('#imgCaptcha')
            
            if not captcha_element.count():
                logger.error("CAPTCHA image element not found")
                return ""
            
            # Take screenshot of CAPTCHA
            screenshot_bytes = captcha_element.screenshot()
            
            # Save temporarily for OCR
            temp_path = self.output_dir / "temp_captcha.png"
            temp_path.write_bytes(screenshot_bytes)
            
            # Preprocess image for better OCR
            processed_path = self.preprocess_captcha_image(temp_path)
            
            # Use PaddleOCR v3.x predict() API
            result = self.ocr.predict(str(processed_path))
            
            # Debug: log the result structure
            logger.debug(f"OCR Result: {result}")
            
            # Extract text from OCR result (v3.x format)
            text = ""
            if result:
                for item in result:
                    # Check for rec_texts attribute (v3.x format)
                    if hasattr(item, 'rec_texts') and item.rec_texts:
                        text += ''.join(item.rec_texts)
                        logger.info(f"Found rec_texts: {item.rec_texts}")
                    # Check for dict format
                    elif isinstance(item, dict):
                        if 'rec_texts' in item and item['rec_texts']:
                            text += ''.join(item['rec_texts'])
                        elif 'rec_text' in item:
                            text += item['rec_text']
            
            # Clean up text - remove spaces and convert to uppercase
            text = ''.join(text.split()).upper()
            
            # Clean up temp files
            temp_path.unlink(missing_ok=True)
            if processed_path != temp_path:
                processed_path.unlink(missing_ok=True)
            
            if text:
                logger.info(f"CAPTCHA extracted: {text}")
            else:
                logger.warning("CAPTCHA text extraction failed - no text detected")
            
            return text
            
        except Exception as e:
            logger.error(f"Error extracting CAPTCHA: {e}")
            return ""
    
    def extract_audio_captcha_text(self, page: Page) -> str:
        """
        Extract CAPTCHA text from audio using Whisper STT
        
        Args:
            page: Playwright page object
            
        Returns:
            Extracted CAPTCHA text or empty string
        """
        try:
            
            # Directly fetch audio from the audiocaptcha endpoint (no need to play)
            audio_captcha_url = "https://services.gst.gov.in/services/audiocaptcha"
            
            # Get cookies from the page session
            cookies = page.context.cookies()
            cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            
            # Fetch audio directly using requests with session cookies
            import requests
            headers = {
                "Cookie": cookie_header,
                "Referer": "https://services.gst.gov.in/services/searchtp",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            response = requests.get(audio_captcha_url, headers=headers)
            
            if response.status_code == 200 and len(response.content) > 1000:
                audio_path = self.output_dir / "temp_captcha_audio.wav"
                audio_path.write_bytes(response.content)
                # logger.info(f"Audio fetched directly (no playback): {audio_path}")
            else:
                logger.warning(f"Failed to fetch : status={response.status_code}, size={len(response.content)}")
                return ""
            
            # Transcribe using Whisper (fp16=False for CPU)
            logger.info("Analysing captcha...")
            result = self.whisper_model.transcribe(str(audio_path), language="en", fp16=False)
            transcribed_text = result.get("text", "").strip()
            
            
            # Clean up the text - CAPTCHA audio usually spells out letters/numbers.
            # Convert spoken number-words to digits first, then strip and upper.
            cleaned_text = re.sub(r'[^a-zA-Z0-9]', '', _words_to_digits(transcribed_text)).upper()
            
            # Clean up temp file
            if audio_path.exists():
                audio_path.unlink(missing_ok=True)
            
            if cleaned_text:
                logger.info(f"CAPTCHA extracted: {cleaned_text}")
            else:
                logger.warning("CAPTCHA resulted in empty text")
            
            return cleaned_text
            
        except Exception as e:
            logger.error(f"Error extracting CAPTCHA: {e}")
            import traceback
            traceback.print_exc()
            return ""
    
    def manual_captcha_entry(self, page: Page) -> bool:
        """
        Wait for manual CAPTCHA entry
        
        Args:
            page: Playwright page object
            
        Returns:
            True if CAPTCHA was filled, False otherwise
        """
        try:
            logger.info("=" * 50)
            logger.info("MANUAL CAPTCHA ENTRY REQUIRED")
            logger.info("Please enter the CAPTCHA in the browser window")
            logger.info("You have 60 seconds...")
            logger.info("=" * 50)
            
            captcha_input = page.locator('#fo-captcha')
            
            # Wait for user to type in CAPTCHA (poll every 500ms for 60 seconds)
            import time
            start_time = time.time()
            
            while time.time() - start_time < 60:
                try:
                    value = captcha_input.input_value()
                    if value and len(value) >= 4:
                        logger.info(f"CAPTCHA entered: {value}")
                        return True
                except:
                    pass
                time.sleep(0.5)
            
            logger.warning("CAPTCHA entry timeout")
            return False
            
        except Exception as e:
            logger.error(f"Error in manual CAPTCHA entry: {e}")
            return False
    
    def search_gst(self, page: Page, gst_number: str) -> Dict:
        """
        Search for a single GST number
        
        Args:
            page: Playwright page object
            gst_number: GST ID to search
            
        Returns:
            Dictionary with search result
        """
        try:
            logger.info(f"=== Searching for GST: {gst_number} ===")

            # A GST search captcha is exactly 6 digits. Whisper sometimes mis-hears
            # (5 digits / a wrong digit), and submitting a wrong captcha silently
            # returns an empty result. So we validate the transcription is 6 digits
            # and, on any failure (bad length or empty result), reload the page —
            # which draws a FRESH captcha — and try again, instead of accepting a
            # silent empty fetch. Manual/non-audio mode keeps the single-shot flow.
            use_audio = self.use_audio_captcha and not self.manual_captcha
            max_attempts = 4 if use_audio else 1
            business_info = {}

            for attempt in range(1, max_attempts + 1):
                # each navigation issues a new captcha, so retries get fresh audio
                # The GST portal is an Angular page.  The document's `load`
                # event can complete before Angular renders the search form,
                # especially from a headless Docker browser.  Wait for the
                # actual input instead of assuming it exists after two seconds.
                page.goto(self.url, wait_until='domcontentloaded')
                gst_input = page.locator('#for_gstin')
                try:
                    gst_input.wait_for(state='visible', timeout=15000)
                except Exception:
                    page_title = page.title()
                    logger.warning(
                        f"GST search form did not render on attempt {attempt}/{max_attempts} "
                        f"(url={page.url!r}, title={page_title!r})"
                    )
                    if attempt < max_attempts:
                        continue
                    raise Exception("GST input field not found after waiting for portal page")
                gst_input.clear()
                gst_input.fill(gst_number)
                logger.info(f"Filled GST: {gst_number}")
                page.wait_for_timeout(1500)

                captcha_input = page.locator('#fo-captcha')
                if not captcha_input.count():
                    raise Exception("CAPTCHA input field not found")

                if use_audio:
                    logger.info(f"Extracting CAPTCHA Text (attempt {attempt}/{max_attempts})...")
                    captcha_text = self.extract_audio_captcha_text(page)
                    if not (captcha_text.isdigit() and len(captcha_text) == 6):
                        logger.warning(f"Captcha '{captcha_text}' not 6 digits — drawing a fresh one")
                        continue  # reload for a new captcha
                    captcha_input.fill(captcha_text)
                    logger.info(f"CAPTCHA filled: {captcha_text}")
                else:
                    if not self.manual_captcha_entry(page):
                        return {'gst_number': gst_number, 'status': 'skipped',
                                'reason': 'CAPTCHA entry timeout'}

                # Click search button
                search_button = page.locator('#lotsearch')
                if not search_button.count():
                    raise Exception("Search button not found")
                search_button.click()
                logger.info("Search button clicked")
                page.wait_for_timeout(4000)

                # Parse business info (includes Goods/Services table)
                initial_page_content = page.content()
                business_info = self.parse_business_info(initial_page_content)
                if business_info:
                    logger.info(f"Extracted {len(business_info)} business info fields")
                    break
                logger.warning(f"Empty result — likely wrong captcha; retry {attempt}/{max_attempts}")

            if not business_info:
                return {'gst_number': gst_number, 'status': 'error',
                        'error': f'no result after {max_attempts} captcha attempts',
                        'timestamp': datetime.now().isoformat()}
            
            # Click "Show Filing Table" button, then pull EVERY financial year
            # the portal lists for this taxpayer (default view is only the
            # latest FY — full history needs each FY selected in turn).
            filing_tables_dict = {}
            filing_table_btn = page.locator('#filingTable')
            if filing_table_btn.count() and filing_table_btn.is_visible():
                filing_table_btn.click()
                logger.info("Show Filing Table button clicked")
                page.wait_for_timeout(2000)
                filing_tables_dict = self._fetch_filing_history(page, gst_number)

            frequencies = self._fetch_return_frequencies(page)
            if frequencies:
                business_info["Return Filing Frequency"] = frequencies
            
            # Save all data to a single CSV file
            csv_file = self.output_dir / f"gst_{gst_number}.csv"
            self.save_combined_csv(gst_number, business_info, filing_tables_dict, csv_file)
            logger.info(f"All data saved to: {csv_file}")
            
            return {
                'gst_number': gst_number,
                'status': 'success',
                'csv_file': str(csv_file),
                'business_info': business_info,
                'filing_tables': {k: v.to_dict('records') for k, v in filing_tables_dict.items()},
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error searching {gst_number}: {e}")
            import traceback
            traceback.print_exc()
            return {
                'gst_number': gst_number,
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
    
    def _click_filing_search(self, page: Page) -> bool:
        """Click the Search button inside the filing-details panel."""
        btn = page.locator("button.srchbtn")
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            return True
        return False

    def _parse_return_frequency(self, html_content: str) -> list[dict]:
        """Parse the portal's 4-quarter return-frequency table."""
        soup = BeautifulSoup(html_content, "html.parser")
        rows = []
        for table in soup.find_all("table"):
            headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
            if "Financial Year" not in headers or "Frequency" not in headers:
                continue
            for tr in table.select("tbody tr"):
                values = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
                if len(values) < 9:
                    continue
                fy = values[0].strip()
                if not re.fullmatch(r"\d{4}-\d{2,4}", fy):
                    continue
                if len(fy) == 7:
                    fy = f"{fy[:4]}-20{fy[-2:]}"
                rows.append({
                    "Financial Year": fy,
                    "Quarter": values[1], "Frequency": values[2],
                    "Quarter_2": values[3], "Frequency_2": values[4],
                    "Quarter_3": values[5], "Frequency_3": values[6],
                    "Quarter_4": values[7], "Frequency_4": values[8],
                })
        return rows

    def _fetch_return_frequencies(self, page: Page) -> list[dict]:
        """Fetch current and previous FY frequency rows exposed by the portal."""
        button = page.locator("#profileTable")
        if not button.count() or not button.is_visible():
            logger.warning("Return-frequency button not available")
            return []
        button.click()
        page.wait_for_timeout(1800)
        rows = self._parse_return_frequency(page.content())
        previous = page.locator("button[data-ng-click*='changeYear']")
        if previous.count() and previous.first.is_visible():
            previous.first.click()
            page.wait_for_timeout(1800)
            rows.extend(self._parse_return_frequency(page.content()))
        return list({r["Financial Year"]: r for r in rows}.values())

    def _fetch_filing_history(self, page: Page, gst_number: str) -> Dict[str, pd.DataFrame]:
        """Fetch filing tables for EVERY financial year the portal offers for
        this taxpayer. The default view shows only the latest FY; the panel has
        a 'Financial Year' <select> whose options run from the registration FY
        to the current one. We iterate each option, search, and merge the rows
        (each row already carries its own 'Financial Year' column).

        Falls back to the single default view if no FY dropdown is found, so a
        DOM change can never make this worse than the old behaviour.
        """
        # dump the filing-panel HTML once so the real DOM can be inspected
        if os.getenv("GST_DUMP_FILING_HTML", "0") == "1":
            try:
                (self.output_dir / f"_filing_{gst_number}.html").write_text(
                    page.content(), encoding="utf-8")
                logger.info(f"Dumped filing-panel HTML for {gst_number}")
            except Exception as e:
                logger.warning(f"Could not dump filing HTML: {e}")

        # locate a native <select> whose options look like financial years
        fy_select, fy_options = None, []
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            try:
                labels = sel.locator("option").all_inner_texts()
                values = sel.locator("option").evaluate_all("els => els.map(e => e.value)")
            except Exception:
                continue
            pairs = [(v, (l or "").strip()) for v, l in zip(values, labels)
                     if re.search(r"\d{4}\s*-\s*\d{2,4}", (l or ""))]
            if pairs:
                fy_select, fy_options = sel, pairs
                break

        if not fy_select:
            logger.warning("No Financial Year dropdown found — fetching default view only")
            self._click_filing_search(page)
            page.wait_for_timeout(3000)
            return self.parse_filing_tables(page.content())

        logger.info(f"Filing history: {len(fy_options)} financial years available: "
                    f"{[l for _, l in fy_options]}")
        merged: Dict[str, list] = {}
        for value, label in fy_options:
            try:
                try:
                    fy_select.select_option(value=value)
                except Exception:
                    fy_select.select_option(label=label)
                page.wait_for_timeout(800)
                self._click_filing_search(page)
                page.wait_for_timeout(2500)
                tables = self.parse_filing_tables(page.content())
                for name, df in tables.items():
                    merged.setdefault(name, []).extend(df.to_dict("records"))
                logger.info(f"Filing history fetched for FY {label}: "
                            + (", ".join(f"{n}={len(v)}" for n, v in tables.items()) or "no tables"))
            except Exception as e:
                logger.warning(f"FY {label} fetch failed: {e}")

        # merge + de-duplicate across years
        out: Dict[str, pd.DataFrame] = {}
        for name, records in merged.items():
            seen, uniq = set(), []
            for r in records:
                key = tuple(sorted(r.items()))
                if key not in seen:
                    seen.add(key)
                    uniq.append(r)
            out[name] = pd.DataFrame(uniq)
        return out

    def save_combined_csv(self, gst_number: str, business_info: Dict, filing_tables: Dict[str, pd.DataFrame], csv_path: Path):
        """
        Save all GST data to a single CSV file with sections
        
        Args:
            gst_number: GST number
            business_info: Dictionary of business information
            filing_tables: Dictionary of filing table DataFrames
            csv_path: Path to save the CSV file
        """
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            import csv
            writer = csv.writer(f)
            
            # Separate business_info into simple key-values and table data
            simple_info = {}
            table_info = {}
            
            for key, value in business_info.items():
                # Check if value is a table (list of dicts)
                if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                    table_info[key] = value
                else:
                    simple_info[key] = value
            
            # Section 1: Basic Business Information (simple key-value pairs)
            writer.writerow(['=== BUSINESS INFORMATION ==='])
            writer.writerow(['GSTIN', gst_number])
            
            for key, value in simple_info.items():
                if isinstance(value, list):
                    # Join list values with semicolon
                    writer.writerow([key, '; '.join(str(v) for v in value)])
                else:
                    writer.writerow([key, value])
            
            writer.writerow([])  # Empty row separator
            
            # Section 2: Tables from business_info (dynamically detected)
            for table_name, table_data in table_info.items():
                if table_data:
                    writer.writerow([f'=== {table_name.upper()} ==='])
                    
                    # Get headers from first record
                    headers = list(table_data[0].keys())
                    writer.writerow(headers)
                    
                    for row in table_data:
                        writer.writerow([row.get(h, '') for h in headers])
                    
                    writer.writerow([])  # Empty row separator
            
            # Section 3: Filing Tables (DataFrames)
            for table_name, df in filing_tables.items():
                if not df.empty:
                    writer.writerow([f'=== {table_name.upper()} ==='])
                    writer.writerow(df.columns.tolist())
                    
                    for _, row in df.iterrows():
                        writer.writerow(row.tolist())
                    
                    writer.writerow([])  # Empty row separator
    
    def parse_filing_tables(self, html_content: str) -> Dict[str, pd.DataFrame]:
        """
        Parse all filing tables from HTML dynamically
        
        Args:
            html_content: HTML page content
            
        Returns:
            Dictionary of {table_title: DataFrame} for all tables found
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        
        tables_dict = {}
        
        # Find all table-responsive divs containing filing tables
        table_divs = soup.find_all('div', class_='table-responsive')
        
        for div in table_divs:
            h4 = div.find('h4')
            if not h4:
                continue
            
            title = h4.get_text(strip=True)
            table = div.find('table')
            
            if not table:
                continue
            
            # Extract column headers dynamically from thead
            headers = []
            thead = table.find('thead')
            if thead:
                # Look for header row with th elements
                header_row = thead.find('tr', class_='ng-table-sort-header')
                if header_row:
                    for th in header_row.find_all('th'):
                        # Get header text from span or directly
                        span = th.find('span')
                        if span:
                            headers.append(span.get_text(strip=True))
                        else:
                            header_text = th.get_text(strip=True)
                            if header_text:
                                headers.append(header_text)
            
            # If no headers found in thead, try data-title-text attributes from first row
            if not headers:
                tbody = table.find('tbody')
                if tbody:
                    first_row = tbody.find('tr')
                    if first_row:
                        for td in first_row.find_all('td'):
                            header = td.get('data-title-text', '')
                            if header:
                                headers.append(header)
            
            # Parse table rows
            rows = []
            tbody = table.find('tbody')
            if tbody and headers:
                for tr in tbody.find_all('tr'):
                    cells = tr.find_all('td')
                    if cells:
                        row = {}
                        for i, cell in enumerate(cells):
                            if i < len(headers):
                                row[headers[i]] = cell.get_text(strip=True)
                        if row:
                            rows.append(row)
            
            if rows:
                df = pd.DataFrame(rows)
                tables_dict[title] = df
                # logger.info(f"Parsed table '{title}': {len(rows)} records, {len(headers)} columns")
        
        return tables_dict
    
    def parse_business_info(self, html_content: str) -> Dict[str, any]:
        """
        Parse business info (key-value pairs) from the search result page dynamically
        
        Args:
            html_content: HTML page content
            
        Returns:
            Dictionary of all extracted business information
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        info = {}
        
        # Find the main result container
        lottable = soup.find('div', id='lottable')
        if not lottable:
            return info
        
        # Parse tbl-format section (main business details)
        tbl_format = lottable.find('div', class_='tbl-format')
        if tbl_format:
            # Find all columns with strong (label) and following content
            for col in tbl_format.find_all('div', class_=lambda x: x and ('col-sm-4' in x or 'col-sm-12' in x)):
                strong = col.find('strong')
                if strong:
                    key = strong.get_text(strip=True)
                    
                    # Check for ul list (like Administrative Office, Other Office)
                    ul = col.find('ul')
                    if ul:
                        items = [li.get_text(strip=True) for li in ul.find_all('li')]
                        info[key] = items
                    else:
                        # Get value from next p tag (not the one containing strong)
                        all_p = col.find_all('p')
                        for p in all_p:
                            if not p.find('strong'):
                                info[key] = p.get_text(strip=True)
                                break
        
        # Parse panel sections (Nature of Core Business, Nature of Business Activities)
        for panel in lottable.find_all('div', class_='panel'):
            heading = panel.find('h4', class_='panel-title')
            if heading:
                # Get key from p tag in heading
                key_elem = heading.find('p')
                if key_elem:
                    key = key_elem.get_text(strip=True)
                else:
                    key = heading.get_text(strip=True).strip()
                
                # Get value from panel-body
                body = panel.find('div', class_='panel-body')
                if body:
                    # Check for list
                    ul = body.find('ul')
                    if ul:
                        items = [li.get_text(strip=True) for li in ul.find_all('li')]
                        info[key] = items
                    else:
                        # Get span or direct text
                        span = body.find('span')
                        if span:
                            info[key] = span.get_text(strip=True)
                        else:
                            info[key] = body.get_text(strip=True)
        
        # Parse tables within lottable (like "Dealing In Goods and Services")
        # class_ returns a list, so check if 'table-responsive' is in it
        table_divs = lottable.find_all('div', class_='table-responsive')
        # logger.info(f"Found {len(table_divs)} table-responsive divs in lottable")
        
        for table_div in table_divs:
            # Find any heading element (h1-h6) - not hardcoded to h4
            heading = table_div.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            if heading:
                table_title = heading.get_text(strip=True)
            else:
                table_title = f"Table_{len(info) + 1}"
            
            # Skip if this is a filing table (those are handled separately)
            if 'Filing details' in table_title or 'GSTR' in table_title:
                continue
            
            # Find table anywhere inside this div (may be nested)
            table = table_div.find('table')
            # logger.debug(f"Found table with title: '{table_title}', table exists: {table is not None}")
            
            if table:
                tbody = table.find('tbody')
                if tbody:
                    all_trs = tbody.find_all('tr')
                    
                    # Get headers from first row of tbody
                    headers = []
                    if all_trs:
                        first_row = all_trs[0]
                        for td in first_row.find_all('td'):
                            header = td.get_text(strip=True)
                            # Make headers unique by adding suffix if duplicate
                            if header in headers:
                                count = headers.count(header) + sum(1 for h in headers if h.startswith(header + '_'))
                                header = f"{header}_{count + 1}"
                            headers.append(header)
                    
                    # logger.info(f"Table '{table_title}' headers: {headers}")
                    
                    # Get data rows (skip first row which is headers)
                    rows = []
                    for tr in all_trs[1:]:
                        cells = tr.find_all('td')
                        if cells and headers:
                            row = {}
                            for i, cell in enumerate(cells):
                                if i < len(headers):
                                    row[headers[i]] = cell.get_text(strip=True)
                            if row and any(row.values()):
                                rows.append(row)
                    
                    if rows:
                        info[table_title] = rows
                        logger.info(f"Parsed table '{table_title}': {len(rows)} records")
        
        return info
    
    def parse_all_tables(self, html_content: str) -> Dict[str, pd.DataFrame]:
        """
        Parse ALL tables from the page dynamically (including Goods/Services table)
        
        Args:
            html_content: HTML page content
            
        Returns:
            Dictionary of {table_title: DataFrame} for all tables found
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        tables_dict = {}
        
        # Find all tables in the page
        for table in soup.find_all('table'):
            # Try to find a title for this table
            title = None
            
            # Check parent for h4 title
            parent = table.find_parent('div', class_='table-responsive')
            if parent:
                h4 = parent.find('h4')
                if h4:
                    title = h4.get_text(strip=True)
            
            # If no title, try to find nearby h4
            if not title:
                prev = table.find_previous_sibling('h4')
                if prev:
                    title = prev.get_text(strip=True)
            
            # Default title if none found
            if not title:
                title = f"Table_{len(tables_dict) + 1}"
            
            # Extract headers from thead or first row
            headers = []
            thead = table.find('thead')
            if thead:
                # Try to get headers from th elements
                for tr in thead.find_all('tr'):
                    for th in tr.find_all('th'):
                        # Check for span with header text
                        span = th.find('span', class_='sort-indicator')
                        if span:
                            headers.append(span.get_text(strip=True))
                        else:
                            text = th.get_text(strip=True)
                            if text:
                                headers.append(text)
                    if headers:
                        break
            
            # Parse tbody rows
            rows = []
            tbody = table.find('tbody')
            if tbody:
                all_trs = tbody.find_all('tr')
                
                # If no headers from thead, try to get from first tbody row or data-title-text
                if not headers and all_trs:
                    first_row = all_trs[0]
                    for td in first_row.find_all('td'):
                        # Check for data-title-text attribute
                        title_text = td.get('data-title-text')
                        if title_text:
                            headers.append(title_text)
                        else:
                            # Use cell content as header (like HSN, Description)
                            headers.append(td.get_text(strip=True))
                    
                    # If first row was headers (no data-title-text), skip it for data
                    if not any(td.get('data-title-text') for td in first_row.find_all('td')):
                        all_trs = all_trs[1:]  # Skip header row
                
                # Extract data rows
                for tr in all_trs:
                    cells = tr.find_all('td')
                    if cells and headers:
                        row = {}
                        for i, cell in enumerate(cells):
                            if i < len(headers):
                                row[headers[i]] = cell.get_text(strip=True)
                        if row and any(row.values()):  # Only add non-empty rows
                            rows.append(row)
            
            if rows:
                df = pd.DataFrame(rows)
                tables_dict[title] = df
                logger.info(f"Parsed table '{title}': {len(rows)} records, {len(headers)} columns")
        
        return tables_dict
    
    def run(self) -> List[Dict]:
        """
        Run the GST search automation
        
        Returns:
            List of search results
        """
        logger.info(f"Starting GST Search for {len(self.gst_numbers)} GST number(s)")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            
            try:
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                
                for idx, gst_number in enumerate(self.gst_numbers, 1):
                    logger.info(f"Progress: {idx}/{len(self.gst_numbers)}")
                    
                    result = self.search_gst(page, gst_number)
                    self.results.append(result)
                    
                    # Wait between searches
                    if idx < len(self.gst_numbers):
                        page.wait_for_timeout(3000)
                
                return self.results
                
            finally:
                context.close()
                browser.close()
    
    

def search_gst_details(
    gst_numbers: List[str],
    headless: bool = False,
    use_audio_captcha: bool = True,
) -> List[Dict]:
    """
    Convenience function to search GST details

    Args:
        gst_numbers: List of GST IDs to search
        headless: Run browser in headless mode
        use_audio_captcha: Use Whisper STT for audio CAPTCHA solving (preferred)

    Returns:
        List of search results
    """
    searcher = GSTSearcher(
        gst_numbers=gst_numbers,
        headless=headless,
        use_audio_captcha=use_audio_captcha,
    )

    return searcher.run()


if __name__ == "__main__":
    import sys

    gst_numbers = sys.argv[1:]

    if not gst_numbers:
        env_path = _SCRIPT_DIR.parents[1] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GST_NUMBERS="):
                    gst_numbers = [g.strip() for g in line.split("=", 1)[1].split(",") if g.strip()]
                    break

    if not gst_numbers:
        print("Usage: python gstWebsite.py <GSTIN1> [GSTIN2 ...]")
        print("(or set GST_NUMBERS=<comma-separated GSTINs> in .env)")
        sys.exit(1)

    results = search_gst_details(gst_numbers=gst_numbers)

    for r in results:
        print(f"{r['gst_number']}: {r['status']}")
