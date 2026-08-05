"""Searching for *words*, when the words are already known.

The overlay can read the screen, so by the time you have dragged across a
sentence the text is in hand.  Sending a *picture* of it to Lens at that point is
a round trip through the network to answer a question the program has already
answered — so the text bar searches for the text instead, which needs no upload,
no image and no waiting.

And if what you selected **is** a link, searching for it is the wrong thing
entirely: the button opens it.

Both jobs are one pure function each, deliberately: guessing wrong about a URL
means opening something the user did not ask for, so the guess has to be
conservative and it has to be testable without a browser.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

#: Google's plain web search.  ``{q}`` is the query, ``{hl}`` the UI language —
#: the same two knobs the Lens variants take, for the same reason.
SEARCH_URL = "https://www.google.com/search?q={q}&hl={hl}"

#: Recognised only when it is unmistakable.  A scheme, or a ``www.`` host, or a
#: single dotted token that looks like a domain.  Anything with a space in it is
#: a sentence and never a URL.
_SCHEME = re.compile(r"^https?://\S+$", re.IGNORECASE)
_WWW = re.compile(r"^www\.[^\s/]+\.[a-z]{2,24}(?:[/?#]\S*)?$", re.IGNORECASE)
_BARE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}(?:[/?#]\S*)?$",
    re.IGNORECASE,
)

#: A bare dotted word is far more often a file than a host, and a terminal full
#: of them is exactly where people circle things.  These never become links
#: without a scheme in front of them.
# fmt: off
_NOT_A_TLD = frozenset((
    "bak", "bat", "bin", "bmp", "c", "cfg", "conf", "cpp", "css", "csv",
    "db", "deb", "dll", "doc", "docx", "exe", "gif", "go", "gz", "h",
    "html", "ico", "ini", "ipynb", "jar", "java", "jpeg", "jpg", "js",
    "json", "lock", "log", "md", "mp3", "mp4", "o", "odt", "ogg", "otf",
    "pdf", "php", "pkg", "png", "ppt", "pptx", "py", "pyc", "rar", "rb",
    "rpm", "rs", "rst", "sh", "so", "sql", "svg", "tar", "tgz", "tmp",
    "toml", "ts", "tsv", "ttf", "txt", "wav", "webp", "xls", "xlsx",
    "xml", "yaml", "yml", "zip", "zst",
))
# fmt: on


def looks_like_url(text: str) -> str | None:
    """The text as a URL you could open, or ``None`` if it is not one.

    Conservative on purpose.  Opening the wrong thing because a sentence
    happened to contain a full stop is a great deal worse than making somebody
    press *Copy* and paste it themselves.
    """
    candidate = " ".join(text.split())
    if not candidate or " " in candidate:
        return None

    if _SCHEME.match(candidate):
        return candidate
    if _WWW.match(candidate):
        return f"https://{candidate}"
    if _BARE.match(candidate):
        host = candidate.split("/")[0].split("?")[0].split("#")[0]
        if host.rsplit(".", 1)[-1].lower() in _NOT_A_TLD:
            return None
        return f"https://{candidate}"
    return None


def search_url(text: str, language: str = "") -> str:
    """Where to send a plain text search.

    Newlines become spaces: text recognised off a screen arrives wrapped the way
    it was displayed, and the line breaks are the paragraph's, not the query's.
    """
    query = " ".join(text.split())
    return SEARCH_URL.format(q=quote_plus(query), hl=language or "")
