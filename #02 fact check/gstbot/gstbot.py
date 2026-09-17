"""Command-line GSTIN scraper that writes one JSON file per GSTIN."""

import argparse
import json
import re
from pathlib import Path

GSTIN_RE = re.compile(r"^[0-9A-Z]{15}$", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape GST taxpayer details by GSTIN")
    parser.add_argument("gstin", help="15-character GSTIN")
    parser.add_argument("--headless", action="store_true", help="run browser without a visible window")
    parser.add_argument("--manual-captcha", action="store_true", help="enter CAPTCHA in the browser window")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(), help="directory for the JSON output")
    args = parser.parse_args()

    gstin = args.gstin.strip().upper()
    if not GSTIN_RE.fullmatch(gstin):
        parser.error("GSTIN must be exactly 15 letters/digits")

    # The original scraper uses manual_captcha=False by default and attempts
    # the GST portal audio CAPTCHA. Manual mode is available for reliability.
    from gstWebsite import GSTSearcher
    searcher = GSTSearcher(
        [gstin],
        headless=args.headless,
        use_audio_captcha=not args.manual_captcha,
        manual_captcha=args.manual_captcha,
        timeout=30000,
    )
    result = searcher.run()[0]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"{gstin}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(output_path.resolve())
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
