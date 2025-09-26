# Web Navigator AI Agent

Automates shopping/product lookups using Playwright + FastAPI.

## Prereqs
- Python 3.10+
- Node not required (Playwright installs browsers itself)

## Setup
```bash
python -m venv .venv
# Windows PowerShell:
. .venv/Scripts/Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
