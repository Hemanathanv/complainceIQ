"""Command-line entry point for the SEBI monitoring business flow."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .adapter import SEBIAdapter

DEFAULT_STATE = Path(__file__).resolve().parents[2] / "data" / "sebi_state.sqlite3"
DEFAULT_DOWNLOAD_DIR = DEFAULT_STATE.parent / "sebi_documents"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor SEBI Circulars and Gazette Notifications")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--pages", type=int, default=1,
                        help="Pages to inspect per category; use 1 for continuous polling")
    parser.add_argument("--baseline", action="store_true",
                        help="Run a bounded historical reconciliation")
    parser.add_argument("--max-pages", type=int, default=120,
                        help="Safety limit for --baseline (default: 120)")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="Maximum seconds per category in monitor mode")
    parser.add_argument("--baseline-timeout", type=float, default=300.0,
                        help="Maximum seconds per category in baseline mode")
    parser.add_argument("--page-timeout", type=float, default=25.0,
                        help="Maximum seconds for a listing page navigation")
    parser.add_argument("--watch", action="store_true",
                        help="Keep polling continuously")
    parser.add_argument("--interval", type=float, default=300.0,
                        help="Seconds between --watch polls (default: 300)")
    parser.add_argument("--download", action="store_true",
                        help="Download PDFs only for newly discovered records")
    parser.add_argument("--download-existing", action="store_true",
                        help="Download PDFs for already recorded rows too")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR,
                        help="Directory for newly downloaded PDFs")
    parser.add_argument("--headed", action="store_true", help="Show the browser during discovery")
    args = parser.parse_args()
    if args.download_existing and not args.download:
        parser.error("--download-existing requires --download")
    return args


async def main() -> None:
    args = parse_args()
    adapter = SEBIAdapter(args.state, headless=not args.headed)
    try:
        if args.baseline:
            pages = max(1, args.max_pages)
            timeout = max(1.0, args.baseline_timeout)
        else:
            pages = max(1, args.pages)
            timeout = max(1.0, args.timeout)
        while True:
            try:
                result = await adapter.poll_once(
                    max_pages=pages,
                    category_timeout=timeout,
                    page_timeout=max(1.0, args.page_timeout),
                    download_new=args.download,
                    download_dir=args.download_dir,
                    download_existing=args.download_existing,
                )
                print(json.dumps(result, indent=2), flush=True)
            except Exception as error:
                # A watch process must survive a transient site or browser
                # failure. The next cycle retries with the same durable state.
                print(json.dumps({"source": "SEBI", "status": "failed", "error": str(error)}), flush=True)
                if not args.watch:
                    raise
            if not args.watch:
                break
            await asyncio.sleep(max(5.0, args.interval))
    finally:
        adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
