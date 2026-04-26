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

# ── Global booking state ──────────────────────────────────────────────────────
booking_state = {
    "running":       False,
    "attempts":      0,
    "status":        "idle",
    "last_response": "",
    "success":       False,
    "class_name":    "",
    "class_date":    "",
    "class_time":    "",
    "class_gym":     "",
    "activity_id":   "",
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


def _dismiss_cookie_banner(page):
    """Click away the CookieScript consent banner (confirmed on nordicwellness.se)."""
    try:
        # CookieScript banner — confirmed present via debug
        for sel in [
            '#cookiescript_accept',           # "Accept all" button
            '#cookiescript_acceptall',
            'button[id*="cookiescript"]',
            'button[class*="cookiescript"]',
            # Fallbacks
            'button:has-text("Acceptera alla")',
            'button:has-text("Acceptera")',
            'button:has-text("Accept all")',
            'button:has-text("Accept")',
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=800):
                    btn.click()
                    log.info("Dismissed cookie banner via: %s", sel)
                    page.wait_for_timeout(1000)
                    return
            except Exception:
                continue
        log.info("No cookie banner found (or already dismissed)")
    except Exception:
        pass


def _do_login(page, email: str, password: str):
    """Fill and submit the login form using JS injection (most reliable)."""
    import os

    log.info("Navigating to login page...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2000)

    # Dismiss cookie consent banner first — it can block inputs
    _dismiss_cookie_banner(page)
    page.wait_for_timeout(1000)

    # Save debug info
    debug_dir = "/tmp/nw_debug"
    os.makedirs(debug_dir, exist_ok=True)
    page.screenshot(path=f"{debug_dir}/login_page.png", full_page=True)
    with open(f"{debug_dir}/login_page.html", "w") as f:
        f.write(page.content())

    inputs = page.evaluate("""
    () => [...document.querySelectorAll('input')].map(i => ({
        name: i.name, type: i.type, id: i.id,
        visible: i.offsetParent !== null,
        display: window.getComputedStyle(i).display,
        visibility: window.getComputedStyle(i).visibility,
        rect: i.getBoundingClientRect(),
    }))
    """)
    log.info("Inputs found on login page:")
    for i in inputs:
        log.info("  %s", i)

    # Confirmed field names from /debug:
    #   email    → input[name="Username"]  type="text"   id="Username"
    #   password → input[name="Password"]  type="password" id="Password"
    def fill_field(name, value):
        page.evaluate(f"""
        () => {{
            const el = document.querySelector('input[name="{name}"]');
            if (!el) return;
            el.focus();
            el.value = {repr(value)};
            el.dispatchEvent(new Event('input',  {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        """)
        log.info("Filled input[name=%s]", name)

    fill_field("Username", email)
    fill_field("Password", password)

    # Small pause so framework registers the values
    page.wait_for_timeout(500)

    # Try multiple submit strategies in order
    submitted = page.evaluate("""
    () => {
        // Strategy 1: click the visible submit button via real mouse event
        const btns = [...document.querySelectorAll('button[type="submit"], input[type="submit"]')];
        const btn = btns.find(b => b.offsetParent !== null) || btns[0];
        if (btn) {
            btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            return 'clicked-' + btn.tagName;
        }
        // Strategy 2: submit the form directly
        const form = document.querySelector('form');
        if (form) { form.submit(); return 'form-submit'; }
        return 'none';
    }
    """)
    log.info("Submit strategy used: %s", submitted)

    # Wait for navigation away from login page
    try:
        page.wait_for_url(lambda url: "logga-in" not in url, timeout=15_000)
        log.info("Navigated away from login page OK")
    except Exception:
        # URL didn't change — try Playwright's own click as fallback
        log.warning("URL did not change after JS submit, trying Playwright click...")
        try:
            page.locator('button[type="submit"]').last.click(timeout=5_000)
            page.wait_for_url(lambda url: "logga-in" not in url, timeout=15_000)
        except Exception as e:
            log.warning("Playwright click also failed: %s", e)

    page.wait_for_load_state("networkidle", timeout=20_000)
    log.info("After login URL: %s", page.url)

    if "logga-in" in page.url:
        page.screenshot(path=f"{debug_dir}/login_failed.png", full_page=True)
        raise ValueError("Login failed – wrong credentials or site is blocking headless browser.")

    log.info("Logged in OK")


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
        page.goto(BOOK_URL, wait_until="networkidle", timeout=60_000)
        # Wait for at least one class h3 to exist in DOM (not necessarily visible)
        page.wait_for_selector("h3.text-sm, h3[class*='font-medium']",
                               state="attached", timeout=20_000)
        # Extra settle time for all cards to render
        page.wait_for_timeout(2000)

        classes = page.evaluate(_SCRAPE_JS)
        log.info("Scraped %d classes", len(classes))
        return classes
    finally:
        browser.close()
        pw.stop()


# ── Booking worker ────────────────────────────────────────────────────────────

def _scrape_my_bookings(session, cookies_dict: dict) -> list:
    """
    Scrape nordicwellness.se/mina-sidor using Playwright (JS-rendered page).
    Returns list of dicts: {name, raw} where raw contains all text in the card.
    """
    pw, browser, ctx = _new_browser()
    try:
        # Inject cookies so we're logged in
        ctx.add_cookies([
            {"name": k, "value": v, "domain": "nordicwellness.se", "path": "/"}
            for k, v in cookies_dict.items()
        ])
        page = ctx.new_page()
        page.goto("https://nordicwellness.se/mina-sidor", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2000)

        bookings = page.evaluate("""
        () => {
            const results = [];
            // Each booked class has an h3 with the class name
            const h3s = [...document.querySelectorAll('h3')].filter(
                h => h.innerText.trim().length > 2
            );
            for (const h3 of h3s) {
                const card = h3.closest('div') || h3.parentElement;
                const allText = card ? card.innerText : h3.innerText;
                results.push({
                    name: h3.innerText.trim(),
                    raw:  allText.toLowerCase(),
                });
            }
            return results;
        }
        """)
        log.info("Found %d entries on mina-sidor", len(bookings))
        return bookings
    finally:
        browser.close()
        pw.stop()


def _booking_confirmed(bookings_before: list, bookings_after: list,
                       class_name: str, class_time: str, class_gym: str) -> bool:
    """
    Return True if the target class appears in bookings_after but not bookings_before.
    Logs every entry so we can debug mismatches.
    """
    # Extract first word of time e.g. "19:00 - 19:45" -> "19:00"
    time_prefix = class_time.split("-")[0].strip()[:5] if class_time else ""
    # First token of gym e.g. "Göteborg Domkyrkan" -> "domkyrkan"
    gym_token   = class_gym.lower().split()[-1][:8] if class_gym else ""
    # First word of class name e.g. "Virtual BODYPUMP® 45" -> "bodypump"
    name_token  = class_name.lower().replace("virtual ", "").split()[0][:8]

    log.info("Matching against: name_token=%r time_prefix=%r gym_token=%r",
             name_token, time_prefix, gym_token)

    def matches(b: dict) -> bool:
        raw  = b["raw"]
        name = b["name"].lower()
        name_ok = name_token in name or name_token in raw
        time_ok = not time_prefix or time_prefix in raw
        gym_ok  = not gym_token   or gym_token  in raw
        log.info("  Entry %r | name_ok=%s time_ok=%s gym_ok=%s | raw=%r",
                 b["name"][:40], name_ok, time_ok, gym_ok, raw[:80])
        return name_ok and time_ok and gym_ok

    before_matches = sum(1 for b in bookings_before if matches(b))
    after_matches  = sum(1 for b in bookings_after  if matches(b))
    log.info("Booking check: before=%d after=%d", before_matches, after_matches)
    return after_matches > before_matches


def do_booking(email: str, password: str, activity_id: str,
               csrf1: str, csrf2: str, form_action: str,
               class_name: str = "", class_date: str = "",
               class_time: str = "", class_gym: str = ""):
    """
    Background thread:
      1. Log in with Playwright, get cookies + fresh CSRF tokens.
      2. Snapshot /mina-sidor#bokningar as baseline.
      3. Hammer the booking POST every 3 ms.
      4. Every 10 s, re-check /mina-sidor — if the class appears, stop.
    """
    import requests as req_lib

    # ── Step 1: Login + fresh tokens ─────────────────────────────────────
    pw, browser, ctx = _new_browser()
    cookies_dict = {}
    try:
        page = ctx.new_page()
        _do_login(page, email, password)

        page.goto(BOOK_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_selector("h3.text-sm, h3[class*='font-medium']",
                               state="attached", timeout=20_000)
        page.wait_for_timeout(2000)

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
            log.warning("Could not refresh CSRF tokens, using cached ones")

        for c in ctx.cookies():
            cookies_dict[c["name"]] = c["value"]
    finally:
        browser.close()
        pw.stop()

    # ── Step 2: Baseline snapshot of mina-sidor ───────────────────────────
    log.info("Taking baseline snapshot of mina-sidor...")
    bookings_before = _scrape_my_bookings(None, cookies_dict)

    # ── Step 3+4: Hammer + periodic confirmation check ────────────────────
    s = req_lib.Session()
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

    with booking_lock:
        booking_state["status"]        = "booking"
        booking_state["attempts"]      = 0
        booking_state["last_response"] = "Starting…"
        booking_state["class_name"]    = class_name
        booking_state["class_date"]    = class_date
        booking_state["class_time"]    = class_time
        booking_state["class_gym"]     = class_gym
        booking_state["activity_id"]   = activity_id

    log.info("Hammering activityId=%s [%s %s %s] every 3ms, checking mina-sidor every 10s",
             activity_id, class_date, class_name, class_time)

    last_check_time = time.time()
    CHECK_INTERVAL  = 10  # seconds between mina-sidor polls

    while True:
        with booking_lock:
            if not booking_state["running"]:
                booking_state["status"] = "stopped"
                break

        # ── POST attempt ──────────────────────────────────────────────────
        try:
            r = s.post(
                target,
                data={
                    "activityId":                 activity_id,
                    "__RequestVerificationToken": csrf1,
                    "ufprt":                      csrf2,
                },
                timeout=5,
                allow_redirects=True,
            )
            with booking_lock:
                booking_state["attempts"]     += 1
                booking_state["last_response"] = f"HTTP {r.status_code} | {r.url[:60]}"

            if r.status_code in (401, 403):
                with booking_lock:
                    booking_state["running"]       = False
                    booking_state["status"]        = "auth_error"
                    booking_state["last_response"] = f"Auth error {r.status_code}"
                break

        except Exception as e:
            with booking_lock:
                booking_state["last_response"] = str(e)[:140]

        # ── Periodic mina-sidor check ─────────────────────────────────────
        now = time.time()
        if now - last_check_time >= CHECK_INTERVAL:
            last_check_time = now
            try:
                log.info("Checking mina-sidor for booking confirmation...")
                bookings_after = _scrape_my_bookings(None, cookies_dict)
                if _booking_confirmed(bookings_before, bookings_after,
                                      class_name, class_time, class_gym):
                    with booking_lock:
                        booking_state["success"] = True
                        booking_state["running"] = False
                        booking_state["status"]  = "success"
                        booking_state["last_response"] = "✓ Confirmed on mina-sidor!"
                    log.info("🎉 Booking confirmed on mina-sidor! Stopping.")
                    break
                else:
                    with booking_lock:
                        booking_state["last_response"] = "Not yet on mina-sidor, continuing…"
            except Exception as e:
                log.warning("mina-sidor check failed: %s", e)

        time.sleep(0.003)   # 3 ms between POST attempts


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/debug")
def debug():
    """Opens the login page in Playwright and returns what it sees as HTML+JSON."""
    import os, base64
    pw, browser, ctx = _new_browser()
    try:
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3000)
        _dismiss_cookie_banner(page)
        page.wait_for_timeout(1000)

        screenshot = page.screenshot(full_page=True)
        img_b64 = base64.b64encode(screenshot).decode()

        inputs = page.evaluate("""
        () => [...document.querySelectorAll('input')].map(i => ({
            name: i.name, type: i.type, id: i.id,
            visible: i.offsetParent !== null,
            display: window.getComputedStyle(i).display,
        }))
        """)

        html = f"""<!DOCTYPE html><html><body style="font-family:monospace;padding:20px;background:#111;color:#eee">
        <h2 style="color:#c8ff00">Login Page Debug</h2>
        <h3>Screenshot:</h3>
        <img src="data:image/png;base64,{img_b64}" style="max-width:100%;border:1px solid #444"/>
        <h3>Inputs found ({len(inputs)}):</h3>
        <pre style="background:#222;padding:12px;overflow:auto">{inputs}</pre>
        <h3>Current URL: {page.url}</h3>
        </body></html>"""
        return html
    finally:
        browser.close()
        pw.stop()


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
    class_name  = data.get("className",  "")
    class_date  = data.get("classDate",  "")
    class_time  = data.get("classTime",  "")
    class_gym   = data.get("classGym",   "")

    if not all([email, password, activity_id]):
        return jsonify({"ok": False, "error": "email, password, activityId required"}), 400

    with booking_lock:
        if booking_state["running"]:
            return jsonify({"ok": False, "error": "Already running"}), 409
        booking_state.update(running=True, success=False, attempts=0, status="starting",
                             class_name=class_name, class_date=class_date,
                             class_time=class_time, class_gym=class_gym,
                             activity_id=activity_id)

    booking_thread = threading.Thread(
        target=do_booking,
        args=(email, password, activity_id, csrf1, csrf2, form_action,
              class_name, class_date, class_time, class_gym),
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