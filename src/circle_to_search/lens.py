"""Google Lens upload.

===========================================================================
 UNOFFICIAL ENDPOINTS — THIS IS THE FILE THAT WILL BREAK FIRST
===========================================================================

Google does not document an "upload an image and get a result page" API, so
this module reproduces the two requests the web UI itself makes.

**1. Lens** (default)::

    POST https://lens.google.com/v3/upload?ep=ccm&s=&st=<unix_ms>&hl=<lang>
    Content-Type: multipart/form-data
        encoded_image              = <the JPEG file>
        processed_image_dimensions = "<width>,<height>"     (a plain field!)
    User-Agent: a current desktop Chrome UA

The answer is a 302/303 whose ``Location`` is
``https://lens.google.com/search?p=…`` — that URL is opened in the browser.

**2. "Search by image"** (fallback)::

    POST https://www.google.com/searchbyimage/upload
        encoded_image = <the JPEG file>
        sbisrc        = <source tag>

It redirects to a normal ``google.com/search?tbs=sbi:…`` results page.  It is
older and less pretty than Lens, but the URL it produces is a plain search URL
that opens in any browser, which makes it a useful safety net when the Lens
answer cannot be used.

Two things that bite in practice and are handled here:

* **The consent interstitial.**  In the EU (and Ukraine) an anonymous request is
  answered with a redirect to ``consent.google.com``.  Opening *that* in the
  browser ends with an error page and no image.  A ``SOCS``/``CONSENT`` cookie
  is sent to skip it, and if a consent URL comes back anyway its ``continue=``
  parameter is unwrapped.
* **The dimensions field.**  It has to be an ordinary multipart *field*.  Sent
  as a file part with an empty filename (which is what ``files={...}`` does for
  a plain string) Google may ignore the upload.

Test the endpoints without the GUI — this prints the whole redirect chain::

    circle-to-search --test-lens /tmp/shot.png --verbose

or with curl::

    curl -sS -D- -o /dev/null -X POST \\
      "https://lens.google.com/v3/upload?ep=ccm&s=&st=$(date +%s%3N)" \\
      -H 'User-Agent: Mozilla/5.0 …' -H 'Cookie: SOCS=CAESHAgBEhIaAB' \\
      -F 'encoded_image=@/tmp/shot.jpg;type=image/jpeg' \\
      -F 'processed_image_dimensions=1000,562'

Nothing here does OCR or any kind of analysis — the image is uploaded as-is and
all the intelligence happens on Google's side.
"""

from __future__ import annotations

import html
import io
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import requests
from PIL import Image

from .logging_setup import get_logger

log = get_logger("lens")

LENS_UPLOAD_URL = "https://lens.google.com/v3/upload"
SEARCH_BY_IMAGE_URL = "https://www.google.com/searchbyimage/upload"

BACKEND_AUTO = "auto"
BACKEND_LENS = "lens"
BACKEND_SEARCH_BY_IMAGE = "searchbyimage"

#: A current desktop Chrome user agent.  Without a browser-like UA Google
#: answers with a page that never redirects, so this is not optional.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

#: Skips the EU cookie-consent interstitial.  These are the values a browser
#: gets after clicking through it once; they carry no account information.
CONSENT_COOKIES = {
    "SOCS": "CAESHAgBEhIaAB",
    "CONSENT": "YES+cb.20240101-00-p0.en+FX+000",
}

REQUEST_TIMEOUT = 10.0

#: How many redirects to walk before giving up.
MAX_REDIRECTS = 5

#: Hosts that mean "this is not a result page yet".
_INTERSTITIAL_HOSTS = ("consent.google.com", "consent.youtube.com")

#: A URL is accepted as a result when it matches one of these.
_RESULT_PATTERNS = (
    re.compile(r"^https://lens\.google\.com/(?:search|uploadbyurl)\b"),
    re.compile(r"^https://(?:www\.)?google\.[a-z.]+/search\b"),
    re.compile(r"^https://(?:www\.)?google\.[a-z.]+/imgres\b"),
)

#: Patterns tried, in order, when Google answers 200 instead of a redirect.
#: Backslashes are *not* excluded on purpose: inside inline JavaScript the URL
#: arrives escaped ("…p=Zm9v==&hl=en"), and extract_result_url()
#: unescapes it afterwards.
_URL_PATTERNS = (
    re.compile(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+url=([^"\'>]+)', re.IGNORECASE),
    re.compile(r'https://lens\.google\.com/(?:search|uploadbyurl)\?[^"\'\s<>]+'),
    re.compile(r'https://www\.google\.com/search\?[^"\'\s<>]*tbs=sbi[^"\'\s<>]*'),
)


class LensError(Exception):
    """The upload failed or Google answered with something unexpected."""


@dataclass(frozen=True)
class PreparedImage:
    """A JPEG ready to be uploaded."""

    payload: bytes
    width: int
    height: int

    @property
    def dimensions(self) -> str:
        return f"{self.width},{self.height}"


def prepare_image(image: Image.Image, max_side: int = 1000, quality: int = 85) -> PreparedImage:
    """Downscale to ``max_side`` and encode as JPEG.

    Lens works with roughly 1000 px on the long edge; sending a raw 4K crop only
    makes the upload slower without improving the result.
    """
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    longest = max(rgb.width, rgb.height)
    if max_side > 0 and longest > max_side:
        factor = max_side / longest
        new_size = (max(1, round(rgb.width * factor)), max(1, round(rgb.height * factor)))
        log.debug("resizing %s → %s before upload", rgb.size, new_size)
        rgb = rgb.resize(new_size, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
    payload = buffer.getvalue()
    log.debug(
        "prepared %d KiB JPEG (%dx%d, q=%d)",
        len(payload) // 1024,
        rgb.width,
        rgb.height,
        quality,
    )
    return PreparedImage(payload=payload, width=rgb.width, height=rgb.height)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://lens.google.com",
            "Referer": "https://lens.google.com/",
        }
    )
    for name, value in CONSENT_COOKIES.items():
        session.cookies.set(name, value, domain=".google.com")
    return session


def is_result_url(url: str) -> bool:
    """True when ``url`` looks like a page that will actually show the image."""
    return any(pattern.match(url) for pattern in _RESULT_PATTERNS)


def unwrap_interstitial(url: str) -> str:
    """Turn a consent/redirect wrapper into the URL it is holding.

    ``https://consent.google.com/m?continue=https%3A//lens.google.com/search…``
    becomes the Lens URL.  Anything else is returned unchanged.
    """
    parsed = urlparse(url)
    if parsed.netloc in _INTERSTITIAL_HOSTS or parsed.path in ("/url", "/sorry/index"):
        query = parse_qs(parsed.query)
        for key in ("continue", "q", "url"):
            values = query.get(key)
            if values and values[0].startswith("http"):
                inner = unquote(values[0])
                log.info("unwrapped an interstitial: %s → %s", parsed.netloc, inner)
                return inner
    return url


def extract_result_url(body: str) -> str | None:
    """Dig a result URL out of an HTML answer (no redirect was sent)."""
    for pattern in _URL_PATTERNS:
        match = pattern.search(body)
        if not match:
            continue
        url = match.group(1) if pattern.groups else match.group(0)
        url = html.unescape(url).replace("\\u003d", "=").replace("\\u0026", "&").replace("\\/", "/")
        url = url.strip().strip("'\"")
        if url.startswith("http"):
            return url
    return None


def _follow(
    session: requests.Session,
    response: requests.Response,
    timeout: float,
) -> str:
    """Walk the redirect chain until a usable result URL appears."""
    steps: list[str] = []
    for _ in range(MAX_REDIRECTS):
        log.debug("HTTP %s from %s", response.status_code, response.url)

        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "").strip()
            if not location:
                raise LensError(f"Google answered {response.status_code} without a Location header")
            location = requests.compat.urljoin(response.url, location)
            steps.append(location)
            candidate = unwrap_interstitial(location)
            if is_result_url(candidate):
                log.info("Lens result: %s", candidate)
                return candidate
            # Not a result yet (consent page, another hop): keep going, but with
            # GET, because only the first hop is the upload.
            log.debug("following %s", candidate)
            response = session.get(candidate, timeout=timeout, allow_redirects=False)
            continue

        if response.status_code == 200:
            found = extract_result_url(response.text)
            if found and is_result_url(found):
                log.info("Lens result (extracted from the HTML): %s", found)
                return found
            snippet = response.text[:300].replace("\n", " ")
            raise LensError(
                "Google answered 200 without a usable result URL — the endpoint has most "
                f"likely changed (see lens.py). Body starts with: {snippet!r}"
            )

        raise LensError(f"Google answered HTTP {response.status_code}")

    raise LensError(f"too many redirects, last hops: {steps}")


def upload_lens(
    prepared: PreparedImage, timeout: float = REQUEST_TIMEOUT, language: str = "en"
) -> str:
    """Upload through ``lens.google.com/v3/upload`` and return the result URL."""
    url = f"{LENS_UPLOAD_URL}?ep=ccm&s=&st={int(time.time() * 1000)}&hl={language}"
    session = _session()
    log.info("uploading %d KiB to Google Lens", len(prepared.payload) // 1024)
    try:
        response = session.post(
            url,
            files={"encoded_image": ("image.jpg", prepared.payload, "image/jpeg")},
            # A plain form field, not a file part with an empty filename.
            data={"processed_image_dimensions": prepared.dimensions},
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise LensError(f"Google Lens did not answer within {timeout:.0f} s") from exc
    except requests.ConnectionError as exc:
        raise LensError(f"no connection to Google Lens: {exc}") from exc
    except requests.RequestException as exc:
        raise LensError(f"the request to Google Lens failed: {exc}") from exc
    return _follow(session, response, timeout)


def upload_search_by_image(
    prepared: PreparedImage, timeout: float = REQUEST_TIMEOUT, language: str = "en"
) -> str:
    """Upload through the older "search by image" endpoint (fallback)."""
    session = _session()
    log.info("uploading %d KiB to Google search-by-image", len(prepared.payload) // 1024)
    try:
        response = session.post(
            f"{SEARCH_BY_IMAGE_URL}?hl={language}",
            files={"encoded_image": ("image.jpg", prepared.payload, "image/jpeg")},
            data={"image_url": "", "sbisrc": "Circle to Search"},
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise LensError(f"Google did not answer within {timeout:.0f} s") from exc
    except requests.ConnectionError as exc:
        raise LensError(f"no connection to Google: {exc}") from exc
    except requests.RequestException as exc:
        raise LensError(f"the search-by-image request failed: {exc}") from exc
    return _follow(session, response, timeout)


def upload(
    prepared: PreparedImage,
    timeout: float = REQUEST_TIMEOUT,
    backend: str = BACKEND_AUTO,
    language: str = "en",
) -> str:
    """Upload with the chosen back end.

    ``auto`` tries Lens first and falls back to search-by-image, so a change on
    one endpoint does not take the whole feature down.
    """
    order: tuple[tuple[str, object], ...]
    if backend == BACKEND_LENS:
        order = (("lens", upload_lens),)
    elif backend == BACKEND_SEARCH_BY_IMAGE:
        order = (("searchbyimage", upload_search_by_image),)
    else:
        order = (("lens", upload_lens), ("searchbyimage", upload_search_by_image))

    errors: list[str] = []
    for name, function in order:
        try:
            return function(prepared, timeout, language)  # type: ignore[operator]
        except LensError as exc:
            log.warning("%s back end failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
    raise LensError("; ".join(errors))


def search(
    image: Image.Image,
    max_side: int = 1000,
    quality: int = 85,
    backend: str = BACKEND_AUTO,
    language: str = "en",
) -> str:
    """Prepare, upload and return the result URL in one call."""
    prepared = prepare_image(image, max_side=max_side, quality=quality)
    return upload(prepared, backend=backend, language=language)
