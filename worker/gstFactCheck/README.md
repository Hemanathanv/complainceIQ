# GST bot

This bot scrapes the official GST taxpayer search portal for one GSTIN and
writes the result to `<GSTIN>.json`.

## Included files

- `gstbot.py` - command-line entry point
- `gstWebsite.py` - scraper and parsing logic
- `model/base.pt` - Whisper CAPTCHA model
- `ffmpeg.exe` - bundled audio dependency for Whisper
- `.venv` - tested Python virtual environment

## Run the bot

Open PowerShell in this folder:

```powershell
cd worker/gstFactCheck
.\.venv\Scripts\python.exe gstbot.py 27AAACR5055K1Z7 --headless
```

The command creates:

```text
27AAACR5055K1Z7.json
```

Replace the example with the required 15-character GSTIN. GSTIN input is
case-insensitive and is written in uppercase in the output filename.

## Output location

By default, JSON is written to the current directory. Choose another folder
with:

```powershell
.\.venv\Scripts\python.exe gstbot.py <GSTIN> --headless --output-dir "C:\path\to\output"
```

The JSON contains `gst_number`, `status`, `business_info`, `filing_tables`,
and `timestamp`. The scraper also writes its legacy CSV and temporary files to
`gst_results` under the current working directory.

## Manual CAPTCHA mode

To show the browser and enter the CAPTCHA yourself:

```powershell
.\.venv\Scripts\python.exe gstbot.py <GSTIN> --manual-captcha
```

## Recreate the environment

If `.venv` is removed, recreate it with:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ../../requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

The bundled `ffmpeg.exe` is automatically added to PATH by the scraper.
