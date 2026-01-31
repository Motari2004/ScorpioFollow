#!/usr/bin/env bash
# exit on error
set -o errexit

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Force Playwright to install inside the project directory
export PLAYWRIGHT_BROWSERS_PATH=$HOME/pw-browsers
playwright install chromium