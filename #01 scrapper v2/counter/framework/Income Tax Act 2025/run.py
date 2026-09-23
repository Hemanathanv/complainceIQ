"""Income Tax Department Circular and Notification monitor."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .adapter import IncomeTaxAdapter


DEFAULT_STATE = Path(__file__).resolve().parents[2] / "data" / "income_tax_state.sqlite3"
DEFAULT_DOWNLOAD_DIR = DEFAULT_STATE.parent / "income_tax_documents"


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor Income Tax circulars and notifications")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--pages", type=int, default=1,
                        help="Pages per category in monitor mode")
    parser.add_argument("--baseline", action="store_true",
                        help="Reconcile all available listing pages up to --max-pages")
    parser.add_argument("--max-pages", type=int, default=1500,
                        help="Safety limit for baseline pagination")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--baseline-timeout", type=float, default=1800.0)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--download", action="store_true",
                        help="Download only newly discovered documents")
    parser.add_argument("--download-existing", action="store_true",
                        help="Download PDFs for already recorded rows too")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if args.download_existing and not args.download:
        parser.error("--download-existing requires --download")
    return args


async def main():
    args = parse_args()
    adapter = IncomeTaxAdapter(args.state, headless=not args.headed)
    try:
        while True:
            result = await adapter.poll_once(
                max_pages=max(1, args.max_pages if args.baseline else args.pages),
                category_timeout=max(5.0, args.baseline_timeout if args.baseline else args.timeout),
                download_new=args.download,
                download_dir=args.download_dir,
                download_existing=args.download_existing,
            )
            print(json.dumps(result, indent=2), flush=True)
            if not args.watch:
                break
            await asyncio.sleep(max(5.0, args.interval))
    finally:
        adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
