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

Three things that bite in practice and are handled here:

* **Session-bound result URLs.**  Since 2025 the Lens surface (``udm=26``)
  answers with ``…/search?vsrid=…&gsessionid=…&lsessionid=…``.  Those session
  ids belong to *this daemon's* HTTP session, so the page opens in the browser
  with the Lens chrome, an empty image slot and loading skeletons — the upload
  worked, the browser simply cannot resolve it.  Several endpoint variants are
  therefore tried (see :data:`VARIANTS`) and the first one whose URL carries no
  session id wins; ``--probe-lens`` shows what each of them returns today.
* **The consent interstitial.**  In the EU (and Ukraine) an anonymous request is
  answered with a redirect to ``consent.google.com``.  Opening *that* in the
  browser ends with an error page and no image.  A ``SOCS``/``CONSENT`` cookie
  is sent to skip it, and if a consent URL comes back anyway its ``continue=``
  parameter is unwrapped.
* **The dimensions field.**  It has to be an ordinary multipart *field*.  Sent
  as a file part with an empty filename (which is what ``files={...}`` does for
  a plain string) Google may ignore the upload.

Test the endpoints without the GUI::

    circle-to-search --test-lens /tmp/shot.png --verbose   # the configured one
    circle-to-search --probe-lens /tmp/shot.png            # all of them, compared

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
LENS_UPLOAD_V1_URL = "https://lens.google.com/upload"
SEARCH_BY_IMAGE_URL = "https://www.google.com/searchbyimage/upload"

#: Query parameters that tie a result page to the uploading HTTP session.
SESSION_PARAMETERS = ("gsessionid", "lsessionid", "sessionid")

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


@dataclass(frozen=True)
class Variant:
    """One way of asking Google to accept an upload."""

    name: str
    #: ``{st}`` = unix milliseconds, ``{hl}`` = UI language.
    url: str
    #: ``lens`` posts encoded_image + processed_image_dimensions,
    #: ``sbi`` posts the classic search-by-image fields.
    kind: str = "lens"

    def build_url(self, language: str) -> str:
        return self.url.format(st=int(time.time() * 1000), hl=language)


#: Ordered from "nicest result page" to "most likely to survive being opened in
#: a different browser session".  ``ep`` selects which Lens surface answers:
#: ``ccm`` is the Chrome context menu, ``crs`` the Chrome region search and
#: ``subb`` the search-bubble one; they do not all hand back the same kind of
#: URL, which is exactly what --probe-lens is for.
VARIANTS: tuple[Variant, ...] = (
    Variant("lens-ccm", LENS_UPLOAD_URL + "?ep=ccm&s=&st={st}&hl={hl}"),
    Variant("lens-crs", LENS_UPLOAD_URL + "?ep=crs&s=&st={st}&hl={hl}"),
    Variant("lens-subb", LENS_UPLOAD_URL + "?ep=subb&s=&st={st}&hl={hl}&re=df"),
    Variant("lens-v1", LENS_UPLOAD_V1_URL + "?ep=ccm&s=&st={st}&hl={hl}"),
    Variant("searchbyimage", SEARCH_BY_IMAGE_URL + "?hl={hl}", kind="sbi"),
)

VARIANTS_BY_NAME = {variant.name: variant for variant in VARIANTS}


def is_stateless_url(url: str) -> bool:
    """True when the URL does not depend on the session that uploaded.

    The 2025 Lens surface (``udm=26``) answers with
    ``…/search?vsrid=…&gsessionid=…&lsessionid=…``.  Those session ids belong to
    the HTTP client that performed the upload — this daemon — so the page opens
    in the browser with the Lens chrome but no image at all.  A URL that carries
    no session id is one the browser can resolve on its own, and that is the
    kind we want to hand to xdg-open.
    """
    query = parse_qs(urlparse(url).query)
    return not any(parameter in query for parameter in SESSION_PARAMETERS)


def upload_variant(
    prepared: PreparedImage,
    variant: Variant,
    timeout: float = REQUEST_TIMEOUT,
    language: str = "en",
) -> str:
    """Upload through one specific endpoint variant and return the result URL."""
    session = _session()
    url = variant.build_url(language)
    if variant.kind == "sbi":
        files = {"encoded_image": ("image.jpg", prepared.payload, "image/jpeg")}
        data = {"image_url": "", "sbisrc": "Circle to Search"}
    else:
        files = {"encoded_image": ("image.jpg", prepared.payload, "image/jpeg")}
        # A plain form field, not a file part with an empty filename.
        data = {"processed_image_dimensions": prepared.dimensions}

    log.info("uploading %d KiB via %s", len(prepared.payload) // 1024, variant.name)
    try:
        response = session.post(
            url, files=files, data=data, timeout=timeout, allow_redirects=False
        )
    except requests.Timeout as exc:
        raise LensError(f"{variant.name}: no answer within {timeout:.0f} s") from exc
    except requests.ConnectionError as exc:
        raise LensError(f"{variant.name}: no connection to Google: {exc}") from exc
    except requests.RequestException as exc:
        raise LensError(f"{variant.name}: the request failed: {exc}") from exc
    return _follow(session, response, timeout)


def upload_lens(
    prepared: PreparedImage, timeout: float = REQUEST_TIMEOUT, language: str = "en"
) -> str:
    """Upload through the default Lens variant."""
    return upload_variant(prepared, VARIANTS_BY_NAME["lens-ccm"], timeout, language)


def upload_search_by_image(
    prepared: PreparedImage, timeout: float = REQUEST_TIMEOUT, language: str = "en"
) -> str:
    """Upload through the classic search-by-image endpoint."""
    return upload_variant(prepared, VARIANTS_BY_NAME["searchbyimage"], timeout, language)


def candidates_for(backend: str) -> tuple[Variant, ...]:
    """Which variants to try, in order, for a configured back end."""
    if backend in VARIANTS_BY_NAME:
        return (VARIANTS_BY_NAME[backend],)
    if backend == BACKEND_LENS:
        return tuple(variant for variant in VARIANTS if variant.kind == "lens")
    if backend == BACKEND_SEARCH_BY_IMAGE:
        return tuple(variant for variant in VARIANTS if variant.kind == "sbi")
    return VARIANTS


def upload(
    prepared: PreparedImage,
    timeout: float = REQUEST_TIMEOUT,
    backend: str = BACKEND_AUTO,
    language: str = "en",
) -> str:
    """Upload and return a URL that is worth opening in a browser.

    Variants are tried in order and the first *stateless* result wins.  A
    session-bound URL is remembered but only used when nothing better turned up,
    because opening it is still better than showing the user an error — it just
    tends to render the Lens page without the picture.
    """
    errors: list[str] = []
    fallback: str | None = None

    for variant in candidates_for(backend):
        try:
            url = upload_variant(prepared, variant, timeout, language)
        except LensError as exc:
            log.warning("%s", exc)
            errors.append(str(exc))
            continue

        if is_stateless_url(url):
            log.info("%s produced a stateless result URL", variant.name)
            return url

        log.warning(
            "%s produced a session-bound URL (gsessionid/lsessionid): the browser "
            "would show the Lens page without the image, trying the next endpoint",
            variant.name,
        )
        fallback = fallback or url

    if fallback is not None:
        log.warning("no stateless URL from any endpoint, opening the session-bound one anyway")
        return fallback
    raise LensError("; ".join(errors) if errors else "no upload endpoint answered")


@dataclass(frozen=True)
class ProbeResult:
    """One row of ``--probe-lens``."""

    variant: str
    url: str = ""
    error: str = ""
    stateless: bool = False

    @property
    def shape(self) -> str:
        """A short label for the kind of URL that came back."""
        if not self.url:
            return "-"
        query = parse_qs(urlparse(self.url).query)
        for key in ("p", "vsrid", "tbs", "q"):
            if key in query:
                return f"{key}=…"
        return "?"


def probe(
    prepared: PreparedImage, timeout: float = REQUEST_TIMEOUT, language: str = "en"
) -> list[ProbeResult]:
    """Try every variant and report what each one answers.

    Google keeps moving this; when the result page opens without an image this
    is the fastest way to find an endpoint that still works.
    """
    results: list[ProbeResult] = []
    for variant in VARIANTS:
        try:
            url = upload_variant(prepared, variant, timeout, language)
        except LensError as exc:
            results.append(ProbeResult(variant=variant.name, error=str(exc)))
            continue
        results.append(
            ProbeResult(variant=variant.name, url=url, stateless=is_stateless_url(url))
        )
    return results


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
