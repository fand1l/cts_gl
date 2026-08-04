"""Load the generated launcher in a real browser and inspect the POST it makes.

This is the test for the piece that cannot be checked by reading the code: that
a page written by write_browser_launcher() really turns into a multipart upload
of the exact JPEG bytes, performed *by the browser* — which is the whole point,
because Google ties the result page to whoever uploaded.

Needs Playwright and a Chromium:

    pip install playwright && playwright install chromium
    python3 tests/test_browser_upload.py

It skips itself (exit 0) when either is missing, so it can sit next to the other
suites without becoming a hard dependency.
"""
import contextlib
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from PIL import Image

from circle_to_search.lens import prepare_image, write_browser_launcher

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  playwright is not installed")
    sys.exit(0)


def find_chromium() -> str | None:
    """Prefer an explicit path, then whatever Playwright downloaded."""
    explicit = os.environ.get("CHROMIUM_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    roots = [
        Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")),
        Path.home() / ".cache/ms-playwright",
    ]
    for root in roots:
        if not root or not root.is_dir():
            continue
        for candidate in sorted(root.glob("chromium*/chrome-linux/chrome")):
            return str(candidate)
    return None

img = Image.new("RGB", (600, 300), (30, 140, 220))
prepared = prepare_image(img, max_side=1000, quality=85)
path = write_browser_launcher(prepared, language="uk", variant="lens-ccm",
                              strings={"status": "Надсилаю виділення в Google Lens…"},
                              directory=Path(tempfile.mkdtemp()))
html = path.read_text()
print("launcher size:", len(html)//1024, "KiB")

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        ok = False


def wait_until(page, condition, budget_ms=20000, step_ms=250):
    """Poll instead of sleeping a fixed amount.

    A cold Chromium on a loaded machine needs several seconds to load a page and
    run its script; a flat wait either makes every run slow or makes some runs
    fail, and a test that fails one time in six is worse than no test.
    """
    waited = 0
    while waited < budget_ms:
        if condition():
            return True
        page.wait_for_timeout(step_ms)
        waited += step_ms
    return condition()


captured = {}
with sync_playwright() as pw:
    executable = find_chromium()
    if executable is None:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            print(f"SKIP  no chromium available ({exc})")
            sys.exit(0)
    else:
        browser = pw.chromium.launch(executable_path=executable)
    page = browser.new_page()

    def handle(route):
        req = route.request
        captured["url"] = req.url
        captured["method"] = req.method
        captured["ct"] = req.headers.get("content-type", "")
        captured["body"] = req.post_data_buffer or b""
        # Answer with a redirect, the way Google does.
        route.fulfill(
            status=303,
            headers={"Location": "https://www.google.com/search?vsrid=OK&gsessionid=Z"},
            body="",
        )
    page.route("https://lens.google.com/**", handle)
    page.route(
        "https://www.google.com/**",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<h1>RESULT PAGE</h1>"
        ),
    )

    # --- the launcher's safety nets ---------------------------------------
    baked_st = int(re.search(r"st=(\d+)", path.read_text()).group(1))
    time.sleep(1.2)

    stamps = []
    probe = browser.new_page()
    probe.route(
        "https://lens.google.com/**",
        lambda route: (
            stamps.append(route.request.url),
            route.fulfill(status=200, body="ok"),
        ),
    )
    # The page navigates away by itself the moment it loads, so goto() may never
    # see a load event; that is expected here.
    with contextlib.suppress(Exception):
        probe.goto(path.resolve().as_uri(), wait_until="commit", timeout=20000)
    wait_until(probe, lambda: bool(stamps))
    check("submit happened", bool(stamps))
    if stamps:
        sent_st = int(re.search(r"st=(\d+)", stamps[0]).group(1))
        # A stale st= is one thing Google can legitimately reject, and the file
        # may have waited for a cold browser start.
        check("timestamp refreshed at submit", sent_st > baked_st, f"{baked_st} -> {sent_st}")
    probe.close()

    # When the submit does not navigate at all the page must stop pretending to
    # work instead of spinning forever.
    stuck = browser.new_page()
    stuck.add_init_script("HTMLFormElement.prototype.submit = function () {};")
    # This page does not navigate away (the submit is neutered above), but wait
    # for the DOM rather than "load": the load event is not what is being
    # tested and waiting for it only adds ways to time out.
    stuck.goto(path.resolve().as_uri(), wait_until="domcontentloaded", timeout=30000)
    check("spinner while trying", stuck.is_visible("#spinner"))
    check("quiet at first", not stuck.is_visible("#retry"))
    stuck.wait_for_timeout(16000)
    check("watchdog message", "did not start" in stuck.inner_text("body"),
          stuck.inner_text("body").replace("\n", " ")[:60])
    check("retry offered", stuck.is_visible("#retry"))
    stuck.close()

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # "commit", not the default "load": the page submits itself the moment it is
    # ready, and on a slow machine it navigates away before the load event
    # arrives — which makes goto() raise about a page that is working perfectly.
    with contextlib.suppress(Exception):
        page.goto(path.resolve().as_uri(), wait_until="commit", timeout=30000)

    def result_arrived():
        # Reading the body of a page that is mid-navigation raises; that just
        # means "not yet".
        try:
            return "RESULT PAGE" in page.inner_text("body")
        except Exception:
            return False

    # Loaded, submitted, redirected and rendered — all of it has to have
    # happened before the body is worth reading.
    wait_until(page, result_arrived)
    final_url = page.url
    body_text = page.inner_text("body")
    browser.close()

check("no JS errors", not errors, str(errors))
check("POST issued", captured.get("method") == "POST", str(captured.get("method")))
check(
    "target url",
    "lens.google.com/v3/upload?ep=ccm" in captured.get("url", ""),
    captured.get("url", ""),
)
check("hl passed", "hl=uk" in captured.get("url",""))
ct = captured.get("ct","")
check("multipart", ct.startswith("multipart/form-data; boundary="), ct)

body = captured.get("body", b"")
check(
    "body non-empty",
    len(body) > len(prepared.payload),
    f"{len(body)} bytes vs jpeg {len(prepared.payload)}",
)
check("image field", b'name="encoded_image"' in body)
check("filename", b'filename="image.jpg"' in body)
check("jpeg content-type", b"Content-Type: image/jpeg" in body)
check(
    "dims field",
    b'name="processed_image_dimensions"' in body and prepared.dimensions.encode() in body,
    prepared.dimensions,
)
# The exact JPEG bytes must survive the base64 round trip through the DOM.
check("jpeg bytes intact", prepared.payload in body)
check("followed redirect", "RESULT PAGE" in body_text, final_url[:60])

path.unlink()
print("BROWSER UPLOAD", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
