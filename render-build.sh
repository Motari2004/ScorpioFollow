#!/usr/bin/env bash
# exit on error
set -o errexit

# Upgrade pip and install requirements
pip install --upgrade pip
pip install -r requirements.txt

# Install the browser binary only (no system deps)
playwright install chromium