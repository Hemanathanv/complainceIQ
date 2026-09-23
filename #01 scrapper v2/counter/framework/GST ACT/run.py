"""Command-line entry point for CBIC GST Circular and Notification monitoring."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .adapter import GSTAdapter


DEFAULT_STATE = Path(__file__).resolve().parents[2] / "data" / "gst_state.sqlite3"
DEFAULT_DOWNLOAD_DIR = DEFAULT_STATE.parent / "gst_documents"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor CBIC GST Circulars and Notifications")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--from-year", type=int, default=2017,
                        help="First year used by --baseline")
    parser.add_argument("--pages", type=int, default=1,
                        help="UI pages per category/year in monitor mode")
    parser.add_argument("--baseline", action="store_true",
                        help="Reconcile all available years and categories")
    parser.add_argument("--max-pages", type=int, default=1000,
                        help="Safety limit per GST filter during baseline")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="Maximum seconds per GST section in monitor mode")
    parser.add_argument("--baseline-timeout", type=float, default=1800.0,
                        help="Maximum seconds per GST section in baseline mode")
    parser.add_argument("--watch", action="store_true",
                        help="Keep polling continuously")
    parser.add_argument("--interval", type=float, default=300.0,
                        help="Seconds between watch polls")
    parser.add_argument("--download", action="store_true",
                        help="Download PDFs only for newly discovered records")
    parser.add_argument("--download-existing", action="store_true",
                        help="Download PDFs for already recorded rows too")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--headed", action="store_true", help="Show the browser")
    parser.add_argument("--counts", action="store_true",
                        help="Report the portal UI total for every category/year filter")
    parser.add_argument("--all", action="store_true",
                        help="Process every notification category/year and every circular record")
    parser.add_argument("--count-timeout", type=float, default=900.0,
                        help="Maximum seconds for --counts")
    args = parser.parse_args()
    if args.download_existing and not args.download:
        parser.error("--download-existing requires --download")
    if args.counts and (args.baseline or args.all or args.watch or args.download):
        parser.error("--counts cannot be combined with --baseline, --all, --watch, or --download")
    return args


async def main() -> None:
    args = parse_args()
    adapter = GSTAdapter(args.state, headless=not args.headed, from_year=args.from_year)
    try:
        if args.counts:
            print(json.dumps(
                await adapter.count_filters(timeout=args.count_timeout),
                indent=2,
            ), flush=True)
            return
        while True:
            full_run = args.baseline or args.all
            result = await adapter.poll_once(
                baseline=full_run,
                max_pages=max(1, args.max_pages if full_run else args.pages),
                category_timeout=max(5.0, args.baseline_timeout if full_run else args.timeout),
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
