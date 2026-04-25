#!/usr/bin/env python3
"""
Nordic Wellness Gym Class Booking Automator
Uses Playwright to render JS, scrape classes, and hammer the booking form.

Install deps:
    pip install flask flask-cors playwright requests
    playwright install chromium
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder=".")
CORS(app)

LOGIN_URL = "https://nordicwellness.se/logga-in?returnUrl=/"
BOOK_URL  = (
    "https://nordicwellness.se/boka/boka-grupptraning/"
    "?klubb=6037&klubb=32938&klubb=159&klubb=30444&klubb=32937"
    "&klubb=24200&klubb=13898&klubb=6809&klubb=164&klubb=31049"
    "&typ=5517&typ=17915&typ=4622"
)
REQUEST_TIMEOUT = 30

# ── Global booking state ──────────────────────────────────────────────────────
booking_state = {
    "running":       False,
    "attempts":      0,
    "status":        "idle",
    "last_response": "",
    "success":       False,
}
booking_lock   = threading.Lock()
booking_thread = None


# ── Playwright helpers ────────────────────────────────────────────────────────

def _new_browser():
    """Start Playwright + headless Chromium. Returns (pw, browser, context)."""
    from playwright.sync_api import sync_playwright
    pw      = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx     = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="sv-SE",
    )
    return pw, browser, ctx


def _do_login(page, email: str, password: str):
    """Fill and submit the login form, raise on failure."""
    log.info("Navigating to login page...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)

    # Wait a bit and log all input elements for debugging
    page.wait_for_timeout(2000)
    inputs = page.query_selector_all('input')
    log.info("Found %d input elements on page:", len(inputs))
    for i, inp in enumerate(inputs[:10]):  # Log first 10 inputs
        attrs = page.evaluate("""
        (el) => ({
            type: el.type,
            name: el.name,
            id: el.id,
            placeholder: el.placeholder,
            visible: el.offsetWidth > 0 && el.offsetHeight > 0,
            className: el.className
        })
        """, inp)
        log.info("  Input %d: %s", i, attrs)

    # Wait for the login form to be visible
    log.info("Waiting for login form elements...")
    try:
        # The login form uses 'Username' not 'Email'
        page.wait_for_selector('input[name="Username"]', timeout=30_000)
    except Exception as e:
        log.error("Username input not found. Page title: %s", page.title())
        log.error("Page URL: %s", page.url)
        raise ValueError(f"Login form not found: {e}")

    # Additional wait to ensure the element is interactable
    page.wait_for_timeout(1000)

    log.info("Filling username field...")
    page.fill('input[name="Username"]', email)

    log.info("Filling password field...")
    page.fill('input[name="Password"]', password)

    # Handle cookie consent dialog
    log.info("Checking for cookie consent dialog...")
    try:
        # Try to hide the cookie dialog
        page.evaluate("""
        () => {
            const cookieDialog = document.getElementById('cookiescript_injected_wrapper') || 
                               document.getElementById('cookiescript_injected');
            if (cookieDialog) {
                cookieDialog.style.display = 'none';
                cookieDialog.remove();
            }
        }
        """)
        log.info("Removed cookie dialog")
        page.wait_for_timeout(500)
    except Exception as e:
        log.info("Could not remove cookie dialog: %s", e)

    log.info("Submitting login form...")
    try:
        # Try clicking the submit button
        page.click('button[type="submit"]', timeout=10000)
    except:
        # If click fails, try pressing Enter in the password field
        log.info("Click failed, trying Enter key...")
        page.press('input[name="Password"]', 'Enter')

    log.info("Waiting for login to complete...")
    page.wait_for_load_state("networkidle", timeout=20_000)

    if "logga-in" in page.url:
        raise ValueError("Login failed – check your credentials")

    log.info("Logged in OK, url=%s", page.url)


# ── Scraping ──────────────────────────────────────────────────────────────────

# JS that runs inside the live browser to collect all class cards.
# Structure confirmed from DevTools:
#
#   div[data-date="2026-04-26"]          ← date wrapper (ancestor of innerCard)
#     …
#     div.grid.grid-cols-2               ← outerCard  (contains form + info)
#       form[action=BOOK_URL]
#         input[name="activityId"]       ← booking ID
#         input[name="__RequestVerificationToken"]
#         input[name="ufprt"]
#         button[type="submit"]          "Boka"
#       div.gap-8                        ← innerCard
#         div
#           h3.text-sm.font-medium       ← class name
#           p.mt-1.hidden                ← gym (desktop)
#         div.w-20
#           p.pt-3.text-xs              ← time range "06:30 - 07:15"
#           p.pt-1.text-xs.md:hidden    ← gym (mobile duplicate)
#           p.pt-1.text-xs.md:hidden    ← trainer

_SCRAPE_JS = """
() => {
    const results = [];

    const h3s = [...document.querySelectorAll('h3')].filter(
        h => h.innerText.trim().length > 2 && /[A-ZÅÄÖ]/.test(h.innerText)
    );

    for (const h3 of h3s) {
        // innerCard = div.gap-8
        const innerCard = h3.closest('div[class*="gap-8"]');
        if (!innerCard) continue;

        // outerCard = div.grid-cols-2
        const outerCard = innerCard.closest('div[class*="grid-cols-2"]');
        if (!outerCard) continue;

        // date: walk up from innerCard to find the nearest [data-date] ancestor
        let dateEl = innerCard.parentElement;
        let date   = '';
        for (let i = 0; i < 6 && dateEl; i++) {
            if (dateEl.dataset && dateEl.dataset.date) {
                date = dateEl.dataset.date;
                break;
            }
            dateEl = dateEl.parentElement;
        }

        // name
        const name = h3.innerText.trim();

        // gym: hidden-on-mobile <p> inside innerCard
        const gymEl = innerCard.querySelector('p[class*="hidden"]');
        const gym   = gymEl ? gymEl.innerText.trim() : '';

        // time: first <p> inside the w-20 sibling div
        const timeDiv = outerCard.querySelector('div[class*="w-20"]');
        const timeEl  = timeDiv ? timeDiv.querySelector('p') : null;
        const time    = timeEl ? timeEl.innerText.trim() : '';

        // trainer: third <p> inside w-20 (mobile-only paragraphs)
        const mobileParagraphs = timeDiv ? [...timeDiv.querySelectorAll('p')] : [];
        const trainer = mobileParagraphs.length >= 3 ? mobileParagraphs[2].innerText.trim() : '';

        // form fields
        const form       = outerCard.querySelector('form');
        const activityId = form?.querySelector('input[name="activityId"]')?.value        || '';
        const csrf1      = form?.querySelector('input[name="__RequestVerificationToken"]')?.value || '';
        const csrf2      = form?.querySelector('input[name="ufprt"]')?.value             || '';
        const formAction = form?.action || '';

        if (!activityId) continue;

        results.push({ name, gym, time, trainer, date, activityId, csrf1, csrf2, formAction });
    }

    return results;
}
"""


def login_and_fetch(email: str, password: str):
    """Log in, render booking page with Playwright, return list of class dicts."""
    pw, browser, ctx = _new_browser()
    try:
        page = ctx.new_page()
        _do_login(page, email, password)

        log.info("Loading booking page…")
        page.goto(BOOK_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("h3.text-sm, h3[class*='font-medium']", timeout=20_000)

        classes = page.evaluate(_SCRAPE_JS)
        log.info("Scraped %d classes", len(classes))
        return classes
    finally:
        browser.close()
        pw.stop()


# ── Booking worker ────────────────────────────────────────────────────────────

def do_booking(email: str, password: str, activity_id: str,
               csrf1: str, csrf2: str, form_action: str):
    """
    Background thread:
      1. Log in with Playwright to get a valid cookie jar.
      2. Re-scrape fresh CSRF tokens for this activityId.
      3. POST the booking form every 3 ms until success or stopped.
    """
    import requests as req_lib
    from playwright.sync_api import sync_playwright

    # Step 1 & 2: fresh login + fresh tokens via Playwright
    pw, browser, ctx = _new_browser()
    cookies_dict = {}
    try:
        page = ctx.new_page()
        _do_login(page, email, password)

        page.goto(BOOK_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("h3.text-sm, h3[class*='font-medium']", timeout=20_000)

        # Refresh tokens from the live DOM (they may differ from those scraped earlier)
        fresh = page.evaluate(f"""
        () => {{
            const inp = document.querySelector('input[name="activityId"][value="{activity_id}"]');
            if (!inp) return null;
            const form = inp.closest('form');
            return {{
                csrf1:      form?.querySelector('input[name="__RequestVerificationToken"]')?.value || '',
                csrf2:      form?.querySelector('input[name="ufprt"]')?.value || '',
                formAction: form?.action || '',
            }};
        }}
        """)

        if fresh and fresh.get("csrf1"):
            csrf1       = fresh["csrf1"]
            csrf2       = fresh["csrf2"]
            form_action = fresh["formAction"]
            log.info("Refreshed CSRF tokens OK")
        else:
            log.warning("Could not find form for activityId=%s in DOM, using cached tokens", activity_id)

        # Export browser cookies → requests
        for c in ctx.cookies():
            cookies_dict[c["name"]] = c["value"]

    finally:
        browser.close()
        pw.stop()

    # Step 3: hammer with requests (much faster than Playwright for rapid POSTs)
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = req_lib.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": BOOK_URL,
        "Origin":  "https://nordicwellness.se",
    })
    s.cookies.update(cookies_dict)
    target = form_action or BOOK_URL

    consecutive_errors = 0
    with booking_lock:
        booking_state["status"]        = "booking"
        booking_state["attempts"]      = 0
        booking_state["last_response"] = "Starting…"

    log.info("Hammering activityId=%s every 3 ms", activity_id)

    while True:
        with booking_lock:
            if not booking_state["running"]:
                booking_state["status"] = "stopped"
                break

        try:
            r = s.post(
                target,
                data={
                    "activityId":                 activity_id,
                    "__RequestVerificationToken": csrf1,
                    "ufprt":                      csrf2,
                },
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            snippet = r.text[:300].replace("\n", " ")
            success = any(w in r.text.lower() for w in (
                "bokad", "booked", "bekräft", "confirmed",
                "din bokning", "tack för", "success",
            ))

            with booking_lock:
                booking_state["attempts"]     += 1
                booking_state["last_response"] = f"HTTP {r.status_code} — {snippet[:140]}"

                if success:
                    booking_state["success"] = True
                    booking_state["running"] = False
                    booking_state["status"]  = "success"
                    log.info("🎉 Booked! activityId=%s", activity_id)
                    break

                if r.status_code in (401, 403):
                    booking_state["running"]       = False
                    booking_state["status"]        = "auth_error"
                    booking_state["last_response"] = f"Auth error {r.status_code} – session expired"
                    break

                if r.status_code >= 500:
                    consecutive_errors += 1
                    booking_state["last_response"] = f"Server error {r.status_code} — retrying"
                    sleep_time = min(1.0, 0.05 * (2 ** consecutive_errors))
                else:
                    consecutive_errors = 0
                    sleep_time = 0.003

        except req_lib.exceptions.ReadTimeout as e:
            consecutive_errors += 1
            sleep_time = min(1.0, 0.05 * (2 ** consecutive_errors))
            with booking_lock:
                booking_state["last_response"] = f"Read timeout, retrying: {str(e)[:120]}"
            log.warning("Read timeout while posting booking, retrying")
        except req_lib.exceptions.RequestException as e:
            consecutive_errors += 1
            sleep_time = min(1.0, 0.05 * (2 ** consecutive_errors))
            with booking_lock:
                booking_state["last_response"] = f"Request error, retrying: {str(e)[:120]}"
            log.warning("Request exception while posting booking: %s", e)
        else:
            sleep_time = 0.003

        time.sleep(sleep_time)


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/login", methods=["POST"])
def login():
    data     = request.json or {}
    email    = data.get("email",    "").strip()
    password = data.get("password", "").strip()
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"}), 400
    try:
        classes = login_and_fetch(email, password)
        return jsonify({"ok": True, "classes": classes})
    except Exception as e:
        log.exception("Login/fetch error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/book", methods=["POST"])
def book():
    global booking_state, booking_thread
    data = request.json or {}

    email       = data.get("email",      "").strip()
    password    = data.get("password",   "").strip()
    activity_id = data.get("activityId", "").strip()
    csrf1       = data.get("csrf1",      "")
    csrf2       = data.get("csrf2",      "")
    form_action = data.get("formAction", "")

    if not all([email, password, activity_id]):
        return jsonify({"ok": False, "error": "email, password, activityId required"}), 400

    with booking_lock:
        if booking_state["running"]:
            return jsonify({"ok": False, "error": "Already running"}), 409
        booking_state.update(running=True, success=False, attempts=0, status="starting")

    booking_thread = threading.Thread(
        target=do_booking,
        args=(email, password, activity_id, csrf1, csrf2, form_action),
        daemon=True,
    )
    booking_thread.start()
    return jsonify({"ok": True})


@app.route("/stop", methods=["POST"])
def stop():
    with booking_lock:
        booking_state["running"] = False
        booking_state["status"]  = "stopped"
    return jsonify({"ok": True})


@app.route("/status")
def status():
    with booking_lock:
        return jsonify(dict(booking_state))


if __name__ == "__main__":
    print("\n🏋️  Nordic Wellness Booking Automator")
    print("   Open  http://localhost:5000  in your browser\n")
    print("   First-time setup:")
    print("     pip install flask flask-cors playwright requests")
    print("     playwright install chromium\n")
    app.run(host="0.0.0.0", port=5000, debug=False)