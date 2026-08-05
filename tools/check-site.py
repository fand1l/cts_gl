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
  symlink and including the paths that live in the string files, and every
  ``#fragment`` exists in the file it points at;
* **there is one page per page.**  The prose is not in the markup: it is in
  ``site/i18n/en_US.json`` and ``site/i18n/uk_UA.json``, and ``js/i18n.js``
  puts the chosen one in.  So the two files have to carry exactly the same
  keys, every key a page asks for has to exist in both, and a key no page asks
  for is a string nobody will ever read.  The few English lines still written
  into ``<head>`` — the ones scrapers read without running a script — have to
  say exactly what ``en_US.json`` says;
* **the commands are the README's commands**, character for character, because a
  command you cannot paste is worse than no command at all — the ``#`` comments
  beside them are prose and are translated, the commands themselves are not;
* the ordinary accessibility floor: one ``h1``, no skipped heading levels, an
  ``alt`` and a size on every image, a language on every page.

    python3 tools/check-site.py

Exit code 1 if anything is wrong, and it says which file and which line.
"""

from __future__ import annotations

import html.parser
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
I18N = SITE / "i18n"

#: The locales that ship.  Both files carry every key; the first is the one the
#: site falls back to and the one the <head> defaults are held against.
LOCALES = ("en_US", "uk_UA")

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


def pairs(value: str) -> dict[str, str]:
    """``"alt: a.b; src: a.c"`` — what data-i18n-attr is written in."""
    out: dict[str, str] = {}
    for item in value.split(";"):
        item = item.strip()
        if not item or ":" not in item:
            continue
        attribute, _, key = item.partition(":")
        out[attribute.strip()] = key.strip()
    return out


class Page(html.parser.HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.problems: list[str] = []
        self.ids: set[str] = set()
        self.links: list[tuple[str, int]] = []
        self.fetches: list[tuple[str, int]] = []
        self.headings: list[tuple[int, int]] = []
        self.keys: list[tuple[str, int]] = []
        #: (key, what the markup says, line) — the <head> defaults, which have
        #: to agree with the first locale.
        self.defaults: list[tuple[str, str, int]] = []
        self.lang: str | None = None
        self.in_title = False
        self.title_key: str | None = None
        self.title_line = 0
        self.text_of_title: list[str] = []

    def fail(self, message: str) -> None:
        self.problems.append(f"{self.path.relative_to(ROOT)}:{self.getpos()[0]}: {message}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        got = {name: (value or "") for name, value in attrs}
        line = self.getpos()[0]
        translated = pairs(got.get("data-i18n-attr", ""))

        if tag == "html":
            self.lang = got.get("lang")
        if tag == "title":
            self.in_title = True
            self.title_key = got.get("data-i18n")
            self.title_line = line

        if got.get("data-i18n"):
            self.keys.append((got["data-i18n"], line))
        for attribute, key in translated.items():
            self.keys.append((key, line))
            #: An attribute written out as well as translated is a default for
            #: whoever does not run the script.  It has to say the same thing.
            if attribute in got:
                self.defaults.append((key, got[attribute], line))

        if "id" in got:
            if got["id"] in self.ids:
                self.fail(f"duplicate id {got['id']!r}")
            self.ids.add(got["id"])

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append((int(tag[1]), line))

        if tag == "img":
            if "alt" not in got and "alt" not in translated:
                self.fail("<img> without alt")
            if not got.get("width") or not (got.get("height") or "height" in translated):
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


def check_page(path: Path, problems: list[str]) -> Page:
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
    if page.title_key:
        page.defaults.append((page.title_key, title, page.title_line))

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

    return page


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


# ------------------------------------------------------------------ the strings


def flatten(data: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in data.items():
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            out.update(flatten(value, key))
        elif isinstance(value, str):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def keys_in_scripts() -> set[str]:
    """Keys a script asks for by name, which no element carries.

    ``js/i18n.js`` reads ``lang.code`` to put on <html>; there is no attribute
    that could say so.  A quoted dotted string in js/ counts as an asking.
    """
    found: set[str] = set()
    for script in sorted((SITE / "js").glob("*.js")):
        text = script.read_text(encoding="utf-8")
        found.update(re.findall(r"['\"]([a-z][a-zA-Z0-9]*(?:\.[a-zA-Z0-9]+)+)['\"]", text))
    return found


def load_strings(problems: list[str]) -> dict[str, dict[str, str]]:
    loaded: dict[str, dict[str, str]] = {}
    for locale in LOCALES:
        path = I18N / f"{locale}.json"
        if not path.exists():
            problems.append(f"site/i18n/{locale}.json: missing — the pages have no text")
            continue
        try:
            loaded[locale] = flatten(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as error:
            problems.append(f"site/i18n/{locale}.json:{error.lineno}: {error.msg}")
    return loaded


def check_strings(
    strings: dict[str, dict[str, str]], asked: dict[str, list[tuple[Path, int]]],
    problems: list[str],
) -> None:
    if len(strings) != len(LOCALES):
        return
    first, *rest = LOCALES

    # Both files, the same keys — a key in one and not the other is a sentence
    # that vanishes when somebody presses the switch.
    for locale in rest:
        for key in sorted(set(strings[first]) - set(strings[locale])):
            problems.append(f"site/i18n/{locale}.json: {key} is in {first} and not here")
        for key in sorted(set(strings[locale]) - set(strings[first])):
            problems.append(f"site/i18n/{first}.json: {key} is in {locale} and not here")

    # Every key a page asks for exists, and every key that exists is asked for.
    for key, wheres in sorted(asked.items()):
        for locale in LOCALES:
            if key not in strings[locale]:
                path, line = wheres[0]
                problems.append(
                    f"{path.relative_to(ROOT)}:{line}: {key} is in no {locale}.json"
                )
    for key in sorted(set(strings[first]) - set(asked) - keys_in_scripts()):
        problems.append(f"site/i18n/{first}.json: nothing on any page asks for {key}")

    # The paths inside the strings resolve, and nothing in them is fetched from
    # off the site.  Links out are links, and go by the same allow list.
    for locale in LOCALES:
        for key, value in sorted(strings[locale].items()):
            where = f"site/i18n/{locale}.json"
            if key.endswith(".src") and (external(value) or not (SITE / value).exists()):
                problems.append(f"{where}: {key} — {value} does not exist")
            for href in re.findall(r'href="([^"]+)"', value):
                host = external(href)
                if host:
                    if host.split(":")[0] not in LINKABLE:
                        problems.append(f"{where}: {key} links to {host}, not in the allow list")
                elif not href.startswith("#") and not (SITE / href.partition("#")[0]).exists():
                    problems.append(f"{where}: {key} links to {href}, which does not exist")
            for url in re.findall(r"(?:src|url\()\s*=?\s*['\"]?(https?://[^'\")\s]+)", value):
                problems.append(f"{where}: {key} fetches {url} from outside the site")


def check_defaults(
    strings: dict[str, dict[str, str]], defaults: list[tuple[Path, str, str, int]],
    problems: list[str],
) -> None:
    """The English still written into <head> is what en_US.json says.

    Those few lines exist for the scrapers that never run a script.  They are
    the only prose left in the markup, and the only way they stay honest is by
    being held against the file the page itself reads.
    """
    first = LOCALES[0]
    if first not in strings:
        return
    for path, key, literal, line in defaults:
        wanted = strings[first].get(key)
        if wanted is None:
            continue
        if literal.strip() != wanted.strip():
            problems.append(
                f"{path.relative_to(ROOT)}:{line}: the fallback for {key} is not what"
                f" {first}.json says ({literal.strip()[:48]!r} vs {wanted.strip()[:48]!r})"
            )


#: Command blocks that must be exactly what the README says, because people
#: paste them.  They are strings like everything else now, so they are checked
#: in every language: the comments beside them are translated, the commands are
#: not, and that is the thing worth catching.
COMMANDS = ("commands.install", "commands.update", "commands.other", "commands.doctor")


def unescape(text: str) -> str:
    import html as html_module

    return html_module.unescape(re.sub(r"<[^>]+>", "", text))


def command_of(line: str) -> str:
    """The part of a line somebody pastes, without the comment beside it."""
    return line.split("#", 1)[0].rstrip()


def check_commands(strings: dict[str, dict[str, str]], problems: list[str]) -> None:
    """Every command in every block has to appear in the README.

    The command, not the whole line: the ``#`` comments beside them are prose
    and are translated with the rest of the page, while the thing to the left
    of the ``#`` is what gets pasted into a terminal and has to be the
    README's own, character for character, in every language.
    """
    readmes = {
        path: path.read_text(encoding="utf-8")
        for path in (ROOT / "README.md", ROOT / "README.uk.md")
    }
    for locale in LOCALES:
        if locale not in strings:
            continue
        for key in COMMANDS:
            block = strings[locale].get(key)
            if block is None:
                problems.append(f"site/i18n/{locale}.json: no command block {key}")
                continue
            for line in unescape(block).strip().splitlines():
                command = command_of(line.rstrip())
                if not command:
                    continue
                if not any(command in text for text in readmes.values()):
                    problems.append(
                        f"site/i18n/{locale}.json {key}: {command!r} is in no README"
                    )


def main() -> int:
    problems: list[str] = []
    pages = sorted(SITE.rglob("*.html"))
    if not pages:
        print("no pages found", file=sys.stderr)
        return 1

    asked: dict[str, list[tuple[Path, int]]] = {}
    defaults: list[tuple[Path, str, str, int]] = []
    for path in pages:
        page = check_page(path, problems)
        for key, line in page.keys:
            asked.setdefault(key, []).append((path, line))
        for key, literal, line in page.defaults:
            defaults.append((path, key, literal, line))

    strings = load_strings(problems)
    check_strings(strings, asked, problems)
    check_defaults(strings, defaults, problems)
    check_assets(problems)
    check_commands(strings, problems)

    if problems:
        for problem in problems:
            print(problem)
        print(f"\n{len(problems)} problem(s) across {len(pages)} page(s)")
        return 1
    counted = len(strings.get(LOCALES[0], {}))
    print(
        f"{len(pages)} pages, {counted} strings × {len(LOCALES)} languages:"
        " no external fetches, every path lands, commands match"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
