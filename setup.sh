#!/bin/bash

# gymbooking_claude setup script
# This script installs all dependencies needed to run the application

set -e  # Exit on any error

echo "🚀 Setting up gymbooking_claude environment..."

# Update package list
echo "📦 Updating package list..."
sudo apt-get update

# Install system dependencies for Playwright
echo "🔧 Installing system dependencies..."
sudo apt-get install -y \
    ca-certificates \
    fonts-liberation \
    libappindicator3-1 \
    libasound2t64 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgdk-pixbuf2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    xdg-utils

# Install Python dependencies
echo "🐍 Installing Python dependencies..."
pip install flask flask-cors playwright requests

# Install Playwright browsers
echo "🌐 Installing Playwright browsers..."
playwright install

echo "✅ Setup complete! You can now run 'python server.py' to start the application."