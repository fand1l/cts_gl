#!/usr/bin/env python3
"""Check the site the way its own rules describe it.

Not a validator against a DTD — the interesting promises this site makes are not
the ones a DTD knows about:

* **it fetches nothing.**  No font, no CDN, no analytics, no badge.  A product
  that has no telemetry has no business having a page that loads somebody
  else's script, so every ``src``, ``href`` on a stylesheet, ``url()`` and
  ``@import`` has to be a relative path inside this repository.  Ordinary links
  out to GitHub are links, not fetches, and are allowed;
* **every relative path resolves**, including through the ``site/images``
  symlink, and every ``#fragment`` exists in the file it points at;
* **the commands are the README's commands**, character for character, because a
  command you cannot paste is worse than no command at all;
* the ordinary accessibility floor: one ``h1``, no skipped heading levels, an
  ``alt`` and a size on every image, a language on every page.

    python3 tools/check-site.py

Exit code 1 if anything is wrong, and it says which file and which line.
"""

from __future__ import annotations

import html.parser
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

#: Hosts an <a href> is allowed to point at.  Nothing may be *fetched* from
#: them; these are links a person clicks.
LINKABLE = {"github.com", "ogp.me"}

#: Attributes that make the browser go and get something.
FETCHING = {
    "img": ("src", "srcset"),
    "script": ("src",),
    "link": ("href",),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src"),
    "iframe": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "track": ("src",),
    "input": ("src",),
    "use": ("href", "xlink:href"),
}

VOID = {"img", "br", "hr", "meta", "link", "input", "source", "track", "area", "base", "col"}


class Page(html.parser.HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.problems: list[str] = []
        self.ids: set[str] = set()
        self.links: list[tuple[str, int]] = []
        self.fetches: list[tuple[str, int]] = []
        self.headings: list[tuple[int, int]] = []
        self.lang: str | None = None
        self.title = ""
        self.in_title = False
        self.text_of_title = []

    def fail(self, message: str) -> None:
        self.problems.append(f"{self.path.relative_to(ROOT)}:{self.getpos()[0]}: {message}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        got = {name: (value or "") for name, value in attrs}
        line = self.getpos()[0]

        if tag == "html":
            self.lang = got.get("lang")
        if tag == "title":
            self.in_title = True

        if "id" in got:
            if got["id"] in self.ids:
                self.fail(f"duplicate id {got['id']!r}")
            self.ids.add(got["id"])

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append((int(tag[1]), line))

        if tag == "img":
            if "alt" not in got:
                self.fail("<img> without alt")
            if not got.get("width") or not got.get("height"):
                self.fail(f"<img src={got.get('src')!r}> without width/height (layout shift)")
            if got.get("src", "").startswith("data:"):
                self.fail("<img> with a data: URI — assets live in the repository")

        if tag == "a" and got.get("href"):
            self.links.append((got["href"], line))

        for attribute in FETCHING.get(tag, ()):
            value = got.get(attribute)
            if value:
                self.fetches.append((value, line))

        for name, value in got.items():
            if name.startswith("on"):
                self.fail(f"inline {name} handler — behaviour belongs in js/")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.text_of_title.append(data)


def external(url: str) -> str | None:
    """The host, when a URL points off this site."""
    match = re.match(r"^(?:https?:)?//([^/]+)", url.strip())
    if match:
        return match.group(1).lower()
    if url.strip().lower().startswith(("http:", "https:", "//")):
        return url
    return None


def check_page(path: Path, problems: list[str]) -> None:
    source = path.read_text(encoding="utf-8")
    page = Page(path)
    page.feed(source)
    page.close()
    problems.extend(page.problems)
    here = f"{path.relative_to(ROOT)}"

    if not page.lang:
        problems.append(f"{here}: <html> without lang")
    title = "".join(page.text_of_title).strip()
    if not title:
        problems.append(f"{here}: no <title>")

    levels = [level for level, _ in page.headings]
    if levels.count(1) != 1:
        problems.append(f"{here}: {levels.count(1)} <h1> — there must be exactly one")
    previous = 0
    for level, line in page.headings:
        if previous and level > previous + 1:
            problems.append(f"{here}:{line}: heading jumps from h{previous} to h{level}")
        previous = level

    # Nothing may be fetched from anywhere but here.
    for url, line in page.fetches:
        host = external(url)
        if host:
            problems.append(f"{here}:{line}: fetches {url} from outside the site")
        elif not url.startswith("#"):
            resolve(path, url, line, problems, here)

    # Links may leave, but only to somewhere allowed, and internal ones must land.
    for url, line in page.links:
        host = external(url)
        if host:
            if host.split(":")[0] not in LINKABLE:
                problems.append(f"{here}:{line}: link to {host} is not in the allow list")
            continue
        if url.startswith(("mailto:", "tel:")):
            continue
        if url.startswith("#"):
            if url[1:] and url[1:] not in page.ids:
                problems.append(f"{here}:{line}: #{url[1:]} is not on this page")
            continue
        resolve(path, url, line, problems, here)

    # Style sheets and scripts get the same treatment for what they pull in.
    for block, line in inline_blocks(source):
        for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", block) + re.findall(
            r"@import\s+['\"]([^'\"]+)", block
        ):
            if external(url):
                problems.append(f"{here}:{line}: inline style fetches {url}")


def inline_blocks(source: str) -> list[tuple[str, int]]:
    out = []
    for match in re.finditer(r"<style[^>]*>(.*?)</style>", source, re.S | re.I):
        out.append((match.group(1), source[: match.start()].count("\n") + 1))
    for match in re.finditer(r'style="([^"]*)"', source):
        out.append((match.group(1), source[: match.start()].count("\n") + 1))
    return out


def resolve(path: Path, url: str, line: int, problems: list[str], here: str) -> None:
    target, _, fragment = url.partition("#")
    if not target:
        return
    if target.startswith("/"):
        problems.append(
            f"{here}:{line}: {url} is root-relative — the site has to work under a"
            " project path and from a file:// URL"
        )
        return
    landing = (path.parent / target).resolve()
    if not landing.exists():
        problems.append(f"{here}:{line}: {url} does not exist")
        return
    if fragment and landing.suffix == ".html":
        found = set(re.findall(r'\bid="([^"]+)"', landing.read_text(encoding="utf-8")))
        if fragment not in found:
            problems.append(f"{here}:{line}: {url} — no id {fragment!r} in that file")


def check_assets(problems: list[str]) -> None:
    for asset in sorted(SITE.rglob("*")):
        if asset.is_dir() or asset.is_symlink():
            continue
        if asset.suffix in {".css", ".js"}:
            text = asset.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith(("*", "//", "/*")):
                    continue
                for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", line) + re.findall(
                    r"@import\s+['\"]([^'\"]+)", line
                ):
                    if external(url):
                        problems.append(
                            f"{asset.relative_to(ROOT)}:{line_number}: fetches {url}"
                        )


#: Blocks that must be exactly what the README says, because people paste them.
COMMANDS = [
    (SITE / "index.html", "install-commands", ROOT / "README.md"),
    (SITE / "install.html", "install-commands", ROOT / "README.md"),
    (SITE / "install.html", "update-command", ROOT / "README.md"),
    (SITE / "install.html", "other-commands", ROOT / "README.md"),
    (SITE / "install.html", "doctor-command", ROOT / "README.md"),
    (SITE / "en" / "install.html", "install-commands", ROOT / "README.md"),
    (SITE / "en" / "install.html", "update-command", ROOT / "README.md"),
    (SITE / "en" / "install.html", "other-commands", ROOT / "README.md"),
    (SITE / "en" / "install.html", "doctor-command", ROOT / "README.md"),
]


def unescape(text: str) -> str:
    import html as html_module

    return html_module.unescape(re.sub(r"<[^>]+>", "", text))


def check_commands(problems: list[str]) -> None:
    """Every line of every command block has to appear in the README."""
    readmes = {
        path: path.read_text(encoding="utf-8")
        for path in (ROOT / "README.md", ROOT / "README.uk.md")
    }
    for page, block_id, _ in COMMANDS:
        if not page.exists():
            continue
        source = page.read_text(encoding="utf-8")
        match = re.search(rf'<pre id="{block_id}">(.*?)</pre>', source, re.S)
        if not match:
            problems.append(f"{page.relative_to(ROOT)}: no command block #{block_id}")
            continue
        for line in unescape(match.group(1)).strip().splitlines():
            line = line.rstrip()
            if not line:
                continue
            if not any(line in text for text in readmes.values()):
                problems.append(
                    f"{page.relative_to(ROOT)} #{block_id}: {line!r} is in no README"
                )


def main() -> int:
    problems: list[str] = []
    pages = sorted(SITE.rglob("*.html"))
    if not pages:
        print("no pages found", file=sys.stderr)
        return 1
    for page in pages:
        check_page(page, problems)
    check_assets(problems)
    check_commands(problems)

    if problems:
        for problem in problems:
            print(problem)
        print(f"\n{len(problems)} problem(s) across {len(pages)} page(s)")
        return 1
    print(f"{len(pages)} pages: no external fetches, every path lands, commands match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
