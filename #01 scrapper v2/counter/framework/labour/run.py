"""Monitor Labour Ministry Orders/Notices and Gazette Notifications."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .adapter import LabourAdapter
from .config import LISTINGS


DEFAULT_STATE = Path(__file__).resolve().parents[2] / "data" / "labour_state.sqlite3"
DEFAULT_DOWNLOAD_DIR = DEFAULT_STATE.parent / "labour_documents"


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor Labour Ministry current documents")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--pages", type=int, default=1,
                        help="Current main/category pages per section in monitor mode")
    parser.add_argument("--baseline", action="store_true",
                        help="Scan current main pages and all discovered category pages")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--baseline-timeout", type=float, default=1800.0)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--download-existing", action="store_true",
                        help="Download PDFs for already recorded rows too")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--section", choices=[listing.category for listing in LISTINGS],
                        help="Run only one Labour section")
    parser.add_argument("--request-delay", type=float, default=6.0,
                        help="Minimum seconds between Labour page navigations")
    parser.add_argument("--request-jitter", type=float, default=2.0,
                        help="Small post-navigation pause, capped at 2 seconds")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if args.download_existing and not args.download:
        parser.error("--download-existing requires --download")
    return args


async def main():
    args = parse_args()
    adapter = LabourAdapter(
        args.state,
        headless=not args.headed,
        request_delay=args.request_delay,
        request_jitter=args.request_jitter,
    )
    selected_listings = tuple(
        listing for listing in LISTINGS
        if args.section is None or listing.category == args.section
    )
    try:
        while True:
            result = await adapter.poll_once(
                max_pages=max(1, args.max_pages if args.baseline else args.pages),
                category_timeout=max(5.0, args.baseline_timeout if args.baseline else args.timeout),
                download_new=args.download,
                download_dir=args.download_dir,
                download_existing=args.download_existing,
                listings=selected_listings,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
            if not args.watch:
                break
            await asyncio.sleep(max(5.0, args.interval))
    finally:
        adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
