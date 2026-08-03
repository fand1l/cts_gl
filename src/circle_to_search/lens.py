"""Google Lens upload.

===========================================================================
 UNOFFICIAL ENDPOINT — THIS IS THE FILE THAT WILL BREAK FIRST
===========================================================================

Google does not document an "upload an image and get a Lens result page" API.
What this module uses is the request the Lens web UI itself performs:

    POST https://lens.google.com/v3/upload?ep=ccm&s=&st=<unix_ms>
    Content-Type: multipart/form-data
        encoded_image              = <the JPEG file>
        processed_image_dimensions = "<width>,<height>"
    User-Agent: a current desktop Chrome UA

The response is a 302 whose ``Location`` header points at
``https://lens.google.com/search?p=…``.  That URL is what gets opened in the
browser.  Google can change the path, the field names, the redirect behaviour
or start requiring a consent cookie at any time; when Lens suddenly stops
working, this file is where to look, and :func:`build_request` /
:func:`extract_result_url` are deliberately small so they can be re-checked
against the browser's network tab with ``curl``:

    curl -sS -D- -o/dev/null -X POST \\
      'https://lens.google.com/v3/upload?ep=ccm&s=&st=1700000000000' \\
      -H 'User-Agent: Mozilla/5.0 …' \\
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

import requests
from PIL import Image

from .logging_setup import get_logger

log = get_logger("lens")

UPLOAD_URL = "https://lens.google.com/v3/upload"

#: A current desktop Chrome user agent.  Without a browser-like UA Google
#: answers with a page that never redirects, so this is not optional.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 10.0

#: Patterns tried, in order, when Google answers 200 instead of 302.
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


RequestParts = tuple[str, dict[str, str], dict[str, tuple[str, bytes, str]]]


def build_request(prepared: PreparedImage) -> RequestParts:
    """Return ``(url, headers, files)`` for the upload — handy for testing."""
    url = f"{UPLOAD_URL}?ep=ccm&s=&st={int(time.time() * 1000)}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    files = {
        "encoded_image": ("image.jpg", prepared.payload, "image/jpeg"),
        "processed_image_dimensions": ("", prepared.dimensions.encode("ascii"), "text/plain"),
    }
    return url, headers, files


def extract_result_url(body: str) -> str | None:
    """Dig a Lens result URL out of an HTML answer (no redirect was sent)."""
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


def upload(prepared: PreparedImage, timeout: float = REQUEST_TIMEOUT) -> str:
    """Upload the image and return the Lens result page URL."""
    url, headers, files = build_request(prepared)
    log.info("uploading %d KiB to Google Lens", len(prepared.payload) // 1024)

    try:
        response = requests.post(
            url,
            headers=headers,
            files=files,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise LensError(f"Google Lens did not answer within {timeout:.0f} s") from exc
    except requests.ConnectionError as exc:
        raise LensError(f"no connection to Google Lens: {exc}") from exc
    except requests.RequestException as exc:
        raise LensError(f"the request to Google Lens failed: {exc}") from exc

    log.debug("Lens answered %s", response.status_code)

    if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
        location = response.headers.get("Location", "").strip()
        if not location:
            raise LensError(f"Google answered {response.status_code} without a Location header")
        log.info("Lens result: %s", location)
        return location

    if response.status_code == 200:
        found = extract_result_url(response.text)
        if found:
            log.info("Lens result (extracted from HTML): %s", found)
            return found
        raise LensError(
            "Google answered 200 without a redirect and without a recognisable result URL — "
            "the upload endpoint has most likely changed (see lens.py)"
        )

    raise LensError(f"Google Lens answered HTTP {response.status_code}")


def search(image: Image.Image, max_side: int = 1000, quality: int = 85) -> str:
    """Prepare, upload and return the result URL in one call."""
    return upload(prepare_image(image, max_side=max_side, quality=quality))
