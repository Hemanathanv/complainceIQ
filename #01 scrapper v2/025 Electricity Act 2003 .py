"""Electricity Act 2003 downloader.

The maintained implementation lives in ``#01 scrapper/cercind.py``.
This legacy entry point delegates to that complete implementation so both
launch paths download the same CERC Acts, policies, rules, regulations,
notifications, orders/ROPs, and Excel audit output.
"""

from pathlib import Path
import runpy
import os
import json
import random
import time
from datetime import datetime


SOURCE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "#01 scrapper v1"
    / "cercind.py"
)


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))
OUTPUT_ROOT = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR)) / Path(__file__).stem
RUNTIME_DIR = OUTPUT_ROOT / "logs"
RUN_STATUS = RUNTIME_DIR / "run_status.json"
RUNTIME_LOG = RUNTIME_DIR / "runtime.log"
RESILIENCE_ATTEMPTS = int(os.environ.get("SCRAPER_MAX_ATTEMPTS", "3"))
BLOCK_COOLDOWN = float(os.environ.get("SCRAPER_BLOCK_COOLDOWN_SECONDS", "300"))


def write_run_status(state, message="", attempt=0):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {"script": Path(__file__).name, "state": state, "attempt": attempt,
               "message": str(message), "timestamp": stamp}
    temporary = RUN_STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(RUN_STATUS)
    with RUNTIME_LOG.open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp} | {state} | attempt={attempt} | {message}\n")


def run_delegated_scraper():
    os.environ.pop("SCRAPER_BLOCK_DETECTED", None)
    os.environ["DOWNLOAD_PROJECT_FOLDER"] = Path(__file__).stem
    runpy.run_path(str(SOURCE_SCRIPT), run_name="__main__")
    blocked = os.environ.pop("SCRAPER_BLOCK_DETECTED", "")
    if blocked:
        raise RuntimeError(blocked)


def guarded_main():
    for attempt in range(1, RESILIENCE_ATTEMPTS + 1):
        write_run_status("running", attempt=attempt)
        try:
            run_delegated_scraper()
            write_run_status("completed", attempt=attempt)
            return
        except KeyboardInterrupt:
            write_run_status("stopped", "Stopped by user", attempt)
            raise
        except BaseException as error:
            if isinstance(error, SystemExit) and error.code in (None, 0):
                write_run_status("completed", attempt=attempt)
                return
            blocked = "HTTP 403" in str(error) or "HTTP 429" in str(error)
            write_run_status("blocked" if blocked else "error", error, attempt)
            if attempt == RESILIENCE_ATTEMPTS:
                raise
            delay = BLOCK_COOLDOWN if blocked else min(120, 5 * (2 ** (attempt - 1)))
            time.sleep(delay + random.uniform(0, 2))


if __name__ == "__main__":
    guarded_main()
