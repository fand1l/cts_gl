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
import os
import sys
import tempfile
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

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.resolve().as_uri())
    page.wait_for_timeout(2500)
    final_url = page.url
    body_text = page.inner_text("body")
    browser.close()

ok = True
def check(name, cond, detail=""):
    global ok
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        ok = False

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
