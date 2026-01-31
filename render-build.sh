#!/usr/bin/env bash
set -o errexit

# Install python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright browser and system dependencies
playwright install chromium
playwright install-deps chromium