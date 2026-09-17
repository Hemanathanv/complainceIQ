"""Electricity Act 2003 downloader.

The maintained implementation lives in ``#01 scrapper/cercind.py``.
This legacy entry point delegates to that complete implementation so both
launch paths download the same CERC Acts, policies, rules, regulations,
notifications, orders/ROPs, and Excel audit output.
"""

from pathlib import Path
import runpy
import os


SOURCE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "#01 scrapper"
    / "cercind.py"
)


if __name__ == "__main__":
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))
    os.environ["DOWNLOAD_PROJECT_FOLDER"] = Path(__file__).stem
    runpy.run_path(str(SOURCE_SCRIPT), run_name="__main__")
