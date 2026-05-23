"""Independent cryptic crossword scraper and IPUZ converter.

For the common `Scraper` interface use the `IndependentScraper` class at the
bottom of this module.  The module-level `fetch_and_convert(date)` is kept as
a convenience entry-point.

Reverse-engineering notes
--------------------------
The Independent's crossword page (puzzles.independent.co.uk/games/cryptic-crossword-independent)
is a JavaScript single-page app served by Arkadium's "Arena" platform.  There is
no server-rendered puzzle data in the initial HTML.

To find where the puzzle data lives, the game SDK JavaScript bundle was located by
reading the embedded `__INITIAL_STATE__` config in the page HTML, which reveals the
game's `sdkName` ("independentCrypticCrossword") and `version` ("Release-58").
The arena platform constructs the SDK URL as:
  ${GAMES_BLOB}/${sdkName}/${version}/main.min.js
  → https://arenacloud.cdn.arkadiumhosted.com/arenaxstorage-blob/arenax-games/
      independentCrypticCrossword/Release-58/main.min.js

Searching that bundle for puzzle-loading logic reveals a game config object:
  {
    "feedUrl": "//ams.cdn.arkadiumhosted.com/assets/gamesfeed/independent/daily-crossword/",
    "feedId":  "independentCryptic",
    "prefix":  "c_",
    "postfix": ".xml",
    "dateFormat": "YYMMDD",
    "firstPuzzleDate": "190902",       ← archive starts 2019-09-02
    "archivingMode": "yearWorthOfPuzzles"
  }

The bundle also shows the URL is assembled as:
  feedUrl + prefix + date(YYMMDD) + postfix
  → https://ams.cdn.arkadiumhosted.com/assets/gamesfeed/independent/daily-crossword/c_YYMMDD.xml

The XML uses the Crossword Compiler format (namespace crossword.info/xml/):
  <crossword-compiler>
    <rectangular-puzzle>
      <metadata> <title> <creator> ...
      <crossword>
        <grid width="15" height="15">
          <cell x="COL" y="ROW" solution="LETTER" number="N"/>   ← numbered cells
          <cell x="COL" y="ROW" solution="LETTER"/>               ← unnumbered cells
          <cell x="COL" y="ROW" type="block"/>                    ← black squares
        </grid>
        <word id="N" x="C1-C2" y="ROW" solution="WORD"/>   ← Across: fixed y, x is a range
        <word id="N" x="COL" y="R1-R2" solution="WORD"/>   ← Down: fixed x, y is a range
        <clues ordering="normal">
          <title><b>Across</b></title>
          <clue word="ID" number="N" format="7">Clue text</clue>
          <clue word="ID" number="N" format="5,5">Clue text</clue>
          ...
        </clues>
        <clues ordering="normal">
          <title><b>Down</b></title>
          ...
        </clues>
      </crossword>
    </rectangular-puzzle>
  </crossword-compiler>

Coordinates: x = column (1 = leftmost), y = row (1 = top).  The `format`
attribute gives the letter-count annotation shown in parentheses after clues.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Any
from urllib.request import Request, urlopen

from vibeword.scrapers import Scraper


BLOCK = "#"
EMPTY = "0"

_FEED_BASE = "https://ams.cdn.arkadiumhosted.com/assets/gamesfeed/independent/daily-crossword/"
_PREFIX = "c_"


class ScraperError(Exception):
    """Raised for any fetch, parse, or conversion failure."""


def puzzle_url_for_date(d: date) -> str:
    """Return the XML URL for a given puzzle date."""
    return f"{_FEED_BASE}{_PREFIX}{d.strftime('%y%m%d')}.xml"


def fetch_puzzle_xml(url: str) -> str:
    req = Request(url, headers={"User-Agent": "vibeword/0.1 (+personal use)"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode(resp.headers.get_content_charset() or "utf-8")
    except Exception as exc:
        raise ScraperError(f"Could not fetch {url}: {exc}") from exc


def _iter_tag(parent: ET.Element, local_name: str):
    """Yield all descendants with the given local tag name (ignores XML namespace)."""
    for el in parent.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == local_name:
            yield el


def _find_tag(parent: ET.Element, local_name: str) -> ET.Element | None:
    return next(_iter_tag(parent, local_name), None)


def parse_xml(xml_text: str) -> dict[str, Any]:
    """Parse Crossword Compiler XML into an intermediate dict."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ScraperError(f"Invalid XML: {exc}") from exc

    metadata = _find_tag(root, "metadata")
    title = ""
    creator = ""
    if metadata is not None:
        title_el = _find_tag(metadata, "title")
        creator_el = _find_tag(metadata, "creator")
        title = (title_el.text or "").strip() if title_el is not None else ""
        creator = (creator_el.text or "").strip() if creator_el is not None else ""

    grid_el = _find_tag(root, "grid")
    if grid_el is None:
        raise ScraperError("No <grid> element found in XML")
    width = int(grid_el.get("width", 15))
    height = int(grid_el.get("height", 15))

    # cells[(x, y)] uses 1-indexed grid coordinates (x=col, y=row from top)
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for cell in _iter_tag(grid_el, "cell"):
        x, y = int(cell.get("x")), int(cell.get("y"))
        num = cell.get("number")
        cells[(x, y)] = {
            "solution": cell.get("solution", "").upper(),
            "number": int(num) if num else None,
            "block": cell.get("type") == "block",
        }

    crossword = _find_tag(root, "crossword")
    if crossword is None:
        raise ScraperError("No <crossword> element found in XML")

    # Words define across/down entries via x or y ranges
    words: dict[str, dict[str, Any]] = {}
    for word in _iter_tag(crossword, "word"):
        wid = word.get("id")
        x_attr, y_attr = word.get("x", ""), word.get("y", "")
        if "-" in x_attr:
            x0, x1 = map(int, x_attr.split("-"))
            words[wid] = {"direction": "Across", "x": x0, "y": int(y_attr), "length": x1 - x0 + 1}
        elif "-" in y_attr:
            y0, y1 = map(int, y_attr.split("-"))
            words[wid] = {"direction": "Down", "x": int(x_attr), "y": y0, "length": y1 - y0 + 1}

    clues: dict[str, list[list[Any]]] = {"Across": [], "Down": []}
    for clue in _iter_tag(crossword, "clue"):
        wid = clue.get("word")
        num = clue.get("number")
        fmt = clue.get("format", "")
        text = (clue.text or "").strip()
        if not wid or wid not in words or not num:
            continue
        clue_text = f"{text} ({fmt})" if fmt else text
        direction = words[wid]["direction"]
        clues[direction].append([int(num), clue_text])

    for direction in clues:
        clues[direction].sort(key=lambda c: c[0])

    return {
        "title": title,
        "creator": creator or None,
        "width": width,
        "height": height,
        "cells": cells,
        "words": words,
        "clues": clues,
    }


def convert(parsed: dict[str, Any], origin: str, puzzle_date: date) -> dict[str, Any]:
    """Convert parsed crossword data to an IPUZ dict."""
    width, height = parsed["width"], parsed["height"]
    cells = parsed["cells"]

    puzzle: list[list[Any]] = []
    solution: list[list[Any]] = []

    for y in range(1, height + 1):
        p_row: list[Any] = []
        s_row: list[Any] = []
        for x in range(1, width + 1):
            cell = cells.get((x, y))
            if cell is None or cell["block"]:
                p_row.append(BLOCK)
                s_row.append(BLOCK)
            elif cell["number"] is not None:
                p_row.append({"cell": cell["number"]})
                s_row.append(cell["solution"] or EMPTY)
            else:
                p_row.append(0)
                s_row.append(cell["solution"] or EMPTY)
        puzzle.append(p_row)
        solution.append(s_row)

    ipuz: dict[str, Any] = {
        "version": "http://ipuz.org/v2",
        "kind": ["http://ipuz.org/crossword#1"],
        "copyright": "The Independent",
        "publisher": "The Independent",
        "title": parsed["title"] or f"Independent Cryptic {puzzle_date.isoformat()}",
        "date": puzzle_date.isoformat(),
        "origin": origin,
        "block": BLOCK,
        "empty": EMPTY,
        "dimensions": {"width": width, "height": height},
        "puzzle": puzzle,
        "clues": parsed["clues"],
        "solution": solution,
    }
    if parsed["creator"]:
        ipuz["author"] = parsed["creator"]
    return ipuz


def default_output_name(puzzle_date: date) -> str:
    return f"independent_cryptic_{puzzle_date.isoformat()}.ipuz"


def fetch_and_convert(puzzle_date: date | None = None) -> dict[str, Any]:
    """Fetch an Independent cryptic crossword and return an IPUZ dict."""
    if puzzle_date is None:
        puzzle_date = date.today()
    url = puzzle_url_for_date(puzzle_date)
    xml_text = fetch_puzzle_xml(url)
    parsed = parse_xml(xml_text)
    return convert(parsed, origin=url, puzzle_date=puzzle_date)


class IndependentScraper(Scraper):
    """Scraper for Independent cryptic crosswords.

    Archive is available back to 2019-09-02 (firstPuzzleDate in the game config).
    """

    @property
    def name(self) -> str:
        return "Independent Cryptic"

    def fetch_for_date(self, puzzle_date: date) -> dict[str, Any]:
        """Fetch a crossword for a specific date from the archive."""
        return fetch_and_convert(puzzle_date)

    def default_output_name(self, ipuz: dict[str, Any]) -> str:
        return f"independent_cryptic_{ipuz.get('date', 'unknown')}.ipuz"
