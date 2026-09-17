# Compliance Scrapper Legacy

## Setup with uv

From this folder:

```powershell
uv venv
uv sync
uv run playwright install chromium
```

Activate the environment when needed:

```powershell
.\.venv\Scripts\Activate.ps1
```

Example:

```powershell
uv run python ".\parivesh.py"
```
