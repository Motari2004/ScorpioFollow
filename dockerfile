# Use Python 3.11 slim as the base
FROM python:3.11-slim

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libgbm-dev \
    libnss3 \
    libnspr4 \
    libasound2 \
    libatk-1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libexpat1 \
    libfontconfig1 \
    libgaffy \
    libglib2.0-0 \
    libgtk-3-0 \
    libpango-1.0-0 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxshmfence1 \
    libxtst6 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
# This installs them into the default location INSIDE the container
RUN playwright install chromium

# Copy the rest of the application
COPY . .

# Set Environment Variables
ENV PYTHONUNBUFFERED=1
ENV RENDER=True

# Expose the port Render expects
EXPOSE 10000

# Start command (matching your eventlet worker)
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "--bind", "0.0.0.0:10000", "app:app"]