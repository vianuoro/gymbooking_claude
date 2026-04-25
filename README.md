# gymbooking_claude
NW booking app

## Setup

### Quick Setup (Recommended)
Run the automated setup script to install everything:

```bash
./setup.sh
```

### Manual Setup

#### System Dependencies (Ubuntu/Debian)
First, install the required system libraries for Playwright:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates fonts-liberation libappindicator3-1 libasound2t64 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcups2 libdbus-1-3 libdrm2 libgdk-pixbuf2.0-0 libgtk-3-0 libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxss1 libxtst6 xdg-utils
```

Alternatively, you can use Playwright's built-in dependency installer:
```bash
playwright install-deps
```

#### Python Dependencies
Install the required Python packages:

```bash
pip install flask flask-cors playwright requests
```

#### Browser Installation
Install Playwright browsers:

```bash
playwright install
```

## Usage

python server.py
...open index.html and enjoy!

## Simple booking with curl

If you want to book a class directly from Bash, use the included `book_class.sh` script.
You need a valid cookie jar and current form tokens from the Nordic Wellness booking page.

```bash
./book_class.sh ACTIVITY_ID REQUEST_TOKEN UFPRT COOKIE_JAR [DATE]
```

Example:

```bash
./book_class.sh 147573892 CfDJ8...Nfm cookies.txt 2026-04-27
```

The script sends a form POST to:

```bash
https://nordicwellness.se/boka/boka-grupptraning/
```

It uses `curl` with multipart form data and preserves cookies from `COOKIE_JAR`.
