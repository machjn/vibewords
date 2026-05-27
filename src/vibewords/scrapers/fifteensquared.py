"""
Parse crossword clue lists from fifteensquared.net blog posts.

Extracts WordSpec objects (index, direction, length) for use with the
grid reconstructor.  The post format varies by publication; this parser
handles the two common patterns:

  FT-style    : "9 ITINERATE Travel to assess... (9)"
  Indie-style : "9 Travel to assess... (9)"

Both share the invariant that a clue line starts with a clue number and
ends with a parenthetical length indicator.

Limitations
-----------
- Linked clues listed in reverse reading order (e.g. "30/8" where cell 8
  precedes cell 30) with asymmetric lengths get their parts reversed to
  match ascending index order.  This heuristic may be wrong for unusual grids.
- Clues whose length indicator is missing are silently skipped.
"""
from __future__ import annotations

import re
import urllib.request
from html.parser import HTMLParser
from typing import Optional

from vibewords.grid_reconstructor import WordSpec


# ---------------------------------------------------------------------------
# HTML → plain text

class _TextExtractor(HTMLParser):
    """Strip HTML to plain text, inserting newlines at block element boundaries."""

    _BLOCK = frozenset(
        "p div h1 h2 h3 h4 h5 h6 li ul ol blockquote section article".split()
    )
    _SKIP = frozenset("script style noscript".split())

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._depth = 0  # nesting depth inside a skipped element

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._depth += 1
        elif tag == "br" or tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._depth = max(0, self._depth - 1)
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._depth == 0:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        _ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "nbsp": " ", "quot": '"'}
        if self._depth == 0:
            self._parts.append(_ENTITIES.get(name, ""))

    def handle_charref(self, name: str) -> None:
        if self._depth == 0:
            try:
                code = int(name[1:], 16) if name.startswith("x") else int(name)
                self._parts.append(chr(code))
            except (ValueError, OverflowError):
                pass

    def get_text(self) -> str:
        return "".join(self._parts)


# ---------------------------------------------------------------------------
# Clue line parser

# Matches: <number(s)> <anything> (<digits with commas/hyphens>)
# The number may be a linked clue like "30/8" or "7, 23".
_CLUE_RE = re.compile(
    r"^(\d+(?:\s*[/,]\s*\d+)*)"   # clue number(s): "9", "30/8", "7, 23"
    r"\s+"                          # whitespace
    r".+"                           # answer and/or clue text (non-empty)
    r"\(([0-9][0-9,\-]*)\)"        # parenthetical length: (9), (5-5), (8,3,4)
    r"\s*$"
)


def _parse_lengths(paren: str) -> list[int]:
    """'5-5' → [5, 5],  '8,3,4' → [8, 3, 4],  '9' → [9]."""
    return [int(p) for p in re.split(r"[,\-]", paren) if p.strip().isdigit()]


def _parse_clue_line(line: str, direction: str) -> list[WordSpec]:
    m = _CLUE_RE.match(line.strip())
    if not m:
        return []

    num_str = m.group(1)
    parts = _parse_lengths(m.group(2))
    if not parts:
        return []

    raw_indices = re.split(r"\s*[/,]\s*", num_str.strip())

    # Single clue → one WordSpec with the total letter count.
    if len(raw_indices) == 1:
        return [WordSpec(index=int(raw_indices[0]), direction=direction, length=sum(parts))]

    # Linked clue (e.g. "30/8") → one WordSpec per index.
    listing = [int(n) for n in raw_indices]
    ascending = sorted(listing)

    if len(ascending) != len(parts):
        # Can't align one-to-one; fall back to a single total-length spec.
        return [WordSpec(index=ascending[0], direction=direction, length=sum(parts))]

    # Assign lengths to indices.  The convention varies by publication:
    # some list clue numbers in reading order ("7/23" → 7 first), others list
    # the "main" number first even when it falls later in the grid ("30/8" →
    # cell 8 actually precedes cell 30).  We detect the latter by checking
    # whether the listing order is descending, and reverse the parts if so.
    if listing == listing[::-1]:
        # Descending listing order → reverse parts to align with reading order.
        parts_in_reading_order = list(reversed(parts))
    else:
        parts_in_reading_order = parts if listing == ascending else list(reversed(parts))

    return [
        WordSpec(index=idx, direction=direction, length=ln)
        for idx, ln in zip(ascending, parts_in_reading_order)
    ]


# ---------------------------------------------------------------------------
# Public API

def specs_from_fifteensquared(url: str) -> list[WordSpec]:
    """
    Fetch a fifteensquared.net post and return WordSpecs for the crossword.

    Handles two common formats:

    Single-line (Independent, Guardian, etc.)::

        1 Straggle headed by elderly goose (7)

    Multi-line (Financial Times)::

        9
        ITINERATE
        Travel to assess passing international point (9)
        I (international) TINE (point) RATE (assess) …

    Parameters
    ----------
    url : str
        Full URL of a fifteensquared.net crossword blog post.

    Returns
    -------
    list[WordSpec]
        Word specs ready for ``reconstruct()``.  Empty if no ACROSS/DOWN
        clue section is found.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    extractor = _TextExtractor()
    extractor.feed(html)
    text = extractor.get_text()

    specs: list[WordSpec] = []
    direction: Optional[str] = None
    pending_num: Optional[str] = None  # buffered clue number (multi-line format)

    _STANDALONE_NUM = re.compile(r"^(\d+(?:\s*[/,]\s*\d+)*)\s*$")
    _HAS_LENGTH = re.compile(r"\(([0-9][0-9,\-]*)\)\s*$")

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Section headers always take priority.
        if re.match(r"^across\s*:?\s*$", stripped, re.I):
            direction = "A"
            pending_num = None
            continue
        if re.match(r"^down\s*:?\s*$", stripped, re.I):
            direction = "D"
            pending_num = None
            continue

        if direction is None:
            continue

        if pending_num is not None:
            # Multi-line mode: we already have a clue number buffered.
            # Check for a new number first (next clue started before we found
            # a length line — shouldn't happen in well-formed posts, but be safe).
            m = _STANDALONE_NUM.match(stripped)
            if m:
                pending_num = m.group(1)
                continue
            # If this line ends with a length indicator it's the clue-text line.
            if _HAS_LENGTH.search(stripped):
                specs.extend(_parse_clue_line(pending_num + " " + stripped, direction))
                pending_num = None
            # Otherwise (answer line, explanation line) → skip.
            continue

        # Single-line format: number + clue text + (length) on one line.
        single = _parse_clue_line(stripped, direction)
        if single:
            specs.extend(single)
            continue

        # Standalone number → enter multi-line mode.
        m = _STANDALONE_NUM.match(stripped)
        if m:
            pending_num = m.group(1)

    return specs
