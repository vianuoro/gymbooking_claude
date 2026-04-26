# 🏋️ NW Booking Bot

Automated class booking for [Nordic Wellness](https://nordicwellness.se) gyms.  
Logs in, fetches the live schedule, and hammers the booking endpoint every 3 ms the moment you hit **Start** — stopping automatically once your spot is confirmed.

---

## How it works

1. **Login** — enter your Nordic Wellness credentials in the web UI
2. **Browse** — the app fetches and displays the full class schedule, grouped by date, with gym, time and trainer
3. **Select** — click the class you want to book
4. **Hammer** — hit *Start Hammering*; the server POSTs the booking form every 3 ms
5. **Confirm** — every 10 seconds the app checks your *Mina sidor* bookings page; the moment your class appears there, hammering stops automatically

---

## Requirements

- Python 3.10+
- A Nordic Wellness account

---

## Installation

### Quick setup (recommended)

```bash
git clone https://github.com/vianuoro/gymbooking_claude
cd gymbooking_claude
./setup.sh
```

### Manual setup

**1. System dependencies** (Ubuntu / Debian / UserLand on Android):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates fonts-liberation libappindicator3-1 \
  libasound2t64 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcups2 \
  libdbus-1-3 libdrm2 libgdk-pixbuf2.0-0 libgtk-3-0 libnspr4 libnss3 \
  libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxss1 \
  libxtst6 xdg-utils
```

Or let Playwright handle it:

```bash
playwright install-deps
```

**2. Python packages:**

```bash
pip install flask flask-cors playwright requests
```

**3. Browser:**

```bash
playwright install chromium
```

---

## Usage

```bash
python server.py
```

Then open **http://localhost:5000** in your browser.

> Running on Android via UserLand? Use the IP shown in the terminal (e.g. `http://10.33.68.25:5000`) to open it in your phone's browser.

---

## Web UI walkthrough

| Step | What happens |
|------|-------------|
| **01 · Credentials** | Enter your Nordic Wellness email and password, click *Fetch Classes* |
| **02 · Select Class** | Classes appear grouped by date — pick the one you want |
| **03 · Status** | Shows the booking target, attempt counter, and live server response. Turns green when booked. |

The **BOOKING TARGET** row always shows the exact class being hammered (date · name · time · gym) so you can verify the right one is selected before and during booking.

---

## Advanced: book directly with curl

If you prefer the command line, use the included `book_class.sh` script. You'll need a valid cookie jar and CSRF tokens from the booking page.

```bash
./book_class.sh ACTIVITY_ID REQUEST_TOKEN UFPRT COOKIE_JAR [DATE]
```

Example:

```bash
./book_class.sh 147573892 CfDJ8...Nfm ufprt_value cookies.txt 2026-04-27
```

---

## Debugging

Visit **http://localhost:5000/debug** to see a screenshot of what the headless browser renders on the login page, plus a list of all form fields found. Useful if login stops working after a site update.

---

## Notes

- The booking window on Nordic Wellness typically opens at a fixed time (e.g. 7 days before the class). Start the bot just before that time for best results.
- CSRF tokens are refreshed from the live page immediately before hammering starts — no stale token issues.
- The bot confirms booking by polling your personal bookings page (`/mina-sidor`) rather than relying on the POST response, which makes detection reliable regardless of how the site responds.
- **Do not share your credentials.** The server runs locally — credentials are never sent anywhere except directly to nordicwellness.se.

---

## Stack

| | |
|---|---|
| Backend | Python · Flask · Playwright |
| Frontend | Vanilla HTML/CSS/JS |
| Browser automation | Playwright (headless Chromium) |