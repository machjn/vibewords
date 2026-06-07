"""Guardian crossword scraper and IPUZ converter (library module).

Import and call `fetch_and_convert(url)` for the common case, or use the
lower-level helpers individually.  For the common `Scraper` interface use
the `GuardianScraper` class at the bottom of this module.

Data source
-----------
The Guardian serves crossword data as JSON at:
  https://www.theguardian.com/crosswords/<type>/<number>.json

This is the same as the public crossword page URL with `.json` appended.
The JSON envelope has a `"crossword"` key containing the grid and clues.

To look up a puzzle by date, the series listing page
  https://www.theguardian.com/crosswords/series/cryptic.json
returns HTML embedded in a JSON wrapper.  Each article card embeds an ISO
datetime attribute adjacent to its crossword URL, giving a {date: number}
map for the most recent ~3–4 weeks.  For older dates the number is estimated
from the ~6-per-week publication rate and confirmed with a short linear search.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.request import Request, urlopen

from vibewords.clue_format import sanitize_clue_html
from vibewords.scrapers import Scraper


BLOCK = "#"
EMPTY = "0"

_GUARDIAN_BASE = "https://www.theguardian.com"


class ScraperError(Exception):
    """Raised for any fetch, parse, or conversion failure."""


@dataclass(frozen=True)
class Slot:
    id: str
    direction: str
    number: int
    human_number: str
    x: int
    y: int
    length: int
    clue: str
    solution: str | None
    group: list = field(default_factory=list)
    separator_locations: dict = field(default_factory=dict)


def normalise_url(value: str, crossword_type: str = "cryptic") -> str:
    value = value.strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if re.fullmatch(r"\d+", value):
        return f"https://www.theguardian.com/crosswords/{crossword_type}/{value}"
    if re.fullmatch(r"[a-z-]+/\d+", value):
        return f"https://www.theguardian.com/crosswords/{value}"
    raise ScraperError(f"Unsupported URL or puzzle reference: {value!r}")


def fetch_crossword_data(page_url: str) -> dict[str, Any]:
    """Fetch the Guardian JSON API and return the crossword sub-object."""
    json_url = page_url.rstrip("/") + ".json"
    req = Request(
        json_url,
        headers={
            "User-Agent": "vibewords/0.1 (+personal use)",
            "Accept": "application/json,text/html",
        },
    )
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read().decode(response.headers.get_content_charset() or "utf-8")
    except Exception as exc:
        raise ScraperError(f"Could not fetch {json_url}: {exc}") from exc

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScraperError(f"Guardian returned invalid JSON from {json_url}: {exc}") from exc

    if "crossword" not in envelope:
        raise ScraperError(
            f"Guardian response did not contain a 'crossword' key. "
            f"Top-level keys: {list(envelope.keys())}"
        )
    return envelope["crossword"]


def _fetch_series_listing(crossword_type: str = "cryptic") -> str:
    """Return the HTML blob from the Guardian series listing endpoint for the given type."""
    url = f"{_GUARDIAN_BASE}/crosswords/series/{crossword_type}.json"
    req = Request(url, headers={"User-Agent": "vibewords/0.1 (+personal use)", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get("html", "")
    except Exception as exc:
        raise ScraperError(f"Could not fetch Guardian {crossword_type} series listing: {exc}") from exc


def _parse_series_listing(html_content: str, crossword_type: str = "cryptic") -> dict[date, int]:
    """Extract a {puzzle_date: puzzle_number} map from the series listing HTML.

    The listing HTML embeds ISO datetime attributes next to crossword URLs, e.g.:
      datetime="2026-05-21T23:00:42+0000" ... /crosswords/cryptic/30013

    The Guardian publishes each puzzle at ~23:00 UTC (midnight BST) the evening
    before it is dated.  The datetime attribute therefore reflects the night of
    publication, which is one calendar day before the puzzle's own date label.
    We add one day to align with the date stored in the puzzle's JSON.
    """
    pairs: dict[date, int] = {}
    pattern = (
        r'datetime="(\d{4}-\d{2}-\d{2})T[^"]*".*?/crosswords/'
        + re.escape(crossword_type)
        + r'/(\d+)'
    )
    for m in re.finditer(pattern, html_content, re.DOTALL):
        # +1 day: listing timestamp is the publication night, puzzle date is next day
        d = date.fromisoformat(m.group(1)) + timedelta(days=1)
        num = int(m.group(2))
        # Keep only the first match per date; cards can contain multiple links.
        pairs.setdefault(d, num)
    return pairs


def _puzzle_number_for_date(
    target: date, crossword_type: str = "cryptic", weekly_rate: float = 6
) -> int:
    """Return the crossword number for a given date.

    Strategy:
    1. Check the series listing (covers the most recent ~3–4 weeks).
    2. For older dates, estimate the number from the known publication
       rate (weekly_rate) anchored to the latest known puzzle, then walk
       at most ±14 numbers to find an exact match.
    """
    html_content = _fetch_series_listing(crossword_type)
    listing = _parse_series_listing(html_content, crossword_type)

    if target in listing:
        return listing[target]

    # Anchor on the most recent known puzzle from the listing.
    if not listing:
        raise ScraperError(f"Guardian {crossword_type} series listing returned no puzzle data")
    latest_date, latest_num = max(listing.items(), key=lambda kv: kv[0])

    days_delta = (target - latest_date).days
    estimated_num = latest_num + round(days_delta * weekly_rate / 7)

    # Search outward from the estimate, up to ±14 puzzles (~2 weeks at 1/week).
    def _puzzle_date(num: int) -> date | None:
        try:
            data = fetch_crossword_data(f"{_GUARDIAN_BASE}/crosswords/{crossword_type}/{num}")
        except ScraperError:
            return None
        raw = data.get("date")
        if isinstance(raw, (int, float)):
            from datetime import datetime, timezone
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).date()
        return date.fromisoformat(str(raw)) if raw else None

    for delta in range(0, 15):
        for sign in (1, -1) if delta else (0,):
            candidate = estimated_num + delta * sign
            if _puzzle_date(candidate) == target:
                return candidate

    raise ScraperError(
        f"Could not find a Guardian {crossword_type} crossword published on {target}. "
        "It may be a publication holiday or before the online archive."
    )


def clean_clue_text(clue: str) -> str:
    clue = sanitize_clue_html(clue)
    return re.sub(r"\s+", " ", clue).strip()


def _get_pos(entry: dict[str, Any]) -> tuple[int, int]:
    pos = entry.get("position") or {}
    return int(pos["x"]), int(pos["y"])


def _parse_entries(data: dict[str, Any]) -> list[Slot]:
    slots: list[Slot] = []
    for e in data.get("entries", []):
        direction = str(e["direction"]).title()
        x, y = _get_pos(e)
        slots.append(Slot(
            id=str(e.get("id", f"{direction.lower()}-{e['number']}")),
            direction=direction,
            number=int(e["number"]),
            human_number=str(e.get("humanNumber") or e["number"]),
            x=x, y=y,
            length=int(e["length"]),
            clue=clean_clue_text(str(e.get("clue", ""))),
            solution=(str(e["solution"]).upper() if e.get("solution") else None),
            group=list(e.get("group") or []),
            separator_locations=dict(e.get("separatorLocations") or {}),
        ))
    return slots


def _slot_cells(slot: Slot) -> list[tuple[int, int]]:
    dx, dy = (1, 0) if slot.direction == "Across" else (0, 1)
    return [(slot.x + i * dx, slot.y + i * dy) for i in range(slot.length)]


def _build_puzzle_and_solution(
    width: int, height: int, slots: list[Slot], include_solutions: bool
) -> tuple[list[list[Any]], list[list[Any]] | None]:
    open_cells: set[tuple[int, int]] = set()
    starts: dict[tuple[int, int], int] = {}
    solution: list[list[Any]] | None = (
        [[None] * width for _ in range(height)] if include_solutions else None
    )

    for slot in slots:
        starts.setdefault((slot.x, slot.y), slot.number)
        cells = _slot_cells(slot)
        for x, y in cells:
            if not (0 <= x < width and 0 <= y < height):
                raise ScraperError(
                    f"Slot {slot.direction} {slot.human_number} runs outside the grid"
                )
            open_cells.add((x, y))

        if include_solutions:
            if not slot.solution:
                raise ScraperError(
                    f"No solution for {slot.direction} {slot.human_number}. "
                    "Try without solutions."
                )
            answer = re.sub(r"[^A-Z0-9]", "", slot.solution.upper())
            if len(answer) != slot.length:
                raise ScraperError(
                    f"Solution length mismatch for {slot.direction} {slot.human_number}: "
                    f"{answer!r} has {len(answer)} letters, slot expects {slot.length}"
                )
            assert solution is not None
            for (x, y), ch in zip(cells, answer):
                existing = solution[y][x]
                if existing not in (None, ch):
                    raise ScraperError(
                        f"Conflicting letters at row {y+1}, col {x+1}: {existing!r} vs {ch!r}"
                    )
                solution[y][x] = ch

    puzzle: list[list[Any]] = []
    for y in range(height):
        row: list[Any] = []
        for x in range(width):
            if (x, y) not in open_cells:
                row.append(BLOCK)
                if solution is not None:
                    solution[y][x] = BLOCK
            elif (x, y) in starts:
                row.append({"cell": starts[(x, y)]})
            else:
                row.append(0)
        puzzle.append(row)

    return puzzle, solution


def _slot_answer(slot: Slot) -> str:
    """Reconstruct the answer with separator punctuation from separatorLocations.

    Guardian uses ',' for space-separated words (→ stored as ' ') and '-' for
    hyphenated compounds.  Positions are 1-based offsets after which the
    separator appears within the slot's own letter sequence.
    """
    if not slot.solution:
        return ""
    letters = re.sub(r"[^A-Z0-9]", "", slot.solution)
    if not slot.separator_locations:
        return letters
    seps: list[tuple[int, str]] = sorted(
        (int(pos), " " if char == "," else char)
        for char, positions in slot.separator_locations.items()
        for pos in positions
    )
    result = list(letters)
    for offset, (pos, sep) in enumerate(seps):
        result.insert(pos + offset, sep)
    return "".join(result)


def _build_clues(slots: list[Slot]) -> dict[str, list[list[Any]]]:
    clues: dict[str, list[list[Any]]] = {"Across": [], "Down": []}
    for slot in sorted(slots, key=lambda s: (s.direction != "Across", s.number, s.x, s.y)):
        label: int | str = slot.number if slot.human_number == str(slot.number) else slot.human_number
        answer = _slot_answer(slot)
        entry: list[Any] = [label, slot.clue]
        if answer:
            entry.append({"solution": answer})
        clues[slot.direction].append(entry)
    return clues


def _build_links(slots: list[Slot]) -> dict[str, dict[int, list]]:
    """Return a map {direction: {clue_num: chain}} for linked clues.

    Each chain is an ordered list of [number, direction] pairs covering all
    segments, supporting cross-direction compounds (e.g. 7 down continues as
    27 across, then 30 across, then 1 down).
    """
    links: dict[str, dict[int, list]] = {"Across": {}, "Down": {}}
    id_to_slot = {s.id: s for s in slots}
    seen: set[tuple] = set()

    for slot in slots:
        if len(slot.group) <= 1:
            continue
        key = tuple(slot.group)
        if key in seen:
            continue
        seen.add(key)

        ordered = [id_to_slot[gid] for gid in slot.group if gid in id_to_slot]
        if len(ordered) <= 1:
            continue

        # Chain of [number, direction] pairs; cross-direction compounds are supported.
        chain = [[s.number, s.direction] for s in ordered]
        # Register every slot under its own direction key.
        for s in ordered:
            links[s.direction][s.number] = chain

    # Drop empty direction keys to keep the output tidy
    return {d: m for d, m in links.items() if m}


def convert(data: dict[str, Any], origin: str, include_solutions: bool = True) -> dict[str, Any]:
    """Convert a raw Guardian crossword dict to an IPUZ dict."""
    dims = data["dimensions"]
    width = int(dims.get("cols") or dims.get("width"))
    height = int(dims.get("rows") or dims.get("height"))
    slots = _parse_entries(data)
    puzzle, solution = _build_puzzle_and_solution(width, height, slots, include_solutions)

    creator = data.get("creator") or {}
    number = data.get("number")
    ctype = data.get("crosswordType") or data.get("type", "crossword")
    title = data.get("name") or f"{str(ctype).title()} crossword No {number}"

    raw_date = data.get("date")
    if isinstance(raw_date, (int, float)):
        from datetime import datetime, timezone
        date_str = datetime.fromtimestamp(raw_date / 1000, tz=timezone.utc).date().isoformat()
    else:
        date_str = str(raw_date) if raw_date else date.today().isoformat()

    ipuz: dict[str, Any] = {
        "version": "http://ipuz.org/v2",
        "kind": ["http://ipuz.org/crossword#1"],
        "copyright": "Guardian News & Media Limited",
        "publisher": "The Guardian",
        "title": title,
        "author": creator.get("name") if isinstance(creator, dict) else None,
        "date": date_str,
        "origin": origin,
        "block": BLOCK,
        "empty": EMPTY,
        "dimensions": {"width": width, "height": height},
        "puzzle": puzzle,
        "clues": _build_clues(slots),
    }
    ipuz = {k: v for k, v in ipuz.items() if v is not None}
    if solution is not None:
        ipuz["solution"] = solution
    links = _build_links(slots)
    if links:
        ipuz["links"] = links
    return ipuz


def default_output_name(data: dict[str, Any], include_solutions: bool) -> str:
    ctype = str(data.get("crosswordType") or data.get("type", "crossword"))
    number = str(data.get("number", "unknown"))
    suffix = "_solved" if include_solutions else ""
    return f"guardian_{ctype}_{number}{suffix}.ipuz"


def fetch_and_convert(url: str, include_solutions: bool = True) -> dict[str, Any]:
    """Convenience: fetch a Guardian URL and return an IPUZ dict."""
    data = fetch_crossword_data(url)
    return convert(data, origin=url, include_solutions=include_solutions)


class GuardianScraper(Scraper):
    """Scraper for Guardian crosswords.

    Pass crossword_type to target a specific series (cryptic, quiptic, concise, …)
    and weekly_rate to tune date-based puzzle-number estimation.  Defaults to the
    cryptic crossword (Mon–Sat, ~6/week).

    Also exposes `fetch_by_url` and `fetch_by_number` for direct access.
    """

    def __init__(self, crossword_type: str = "cryptic", weekly_rate: float = 6, schedule: str | None = None):
        self._crossword_type = crossword_type
        self._weekly_rate = weekly_rate
        self._schedule = schedule

    @property
    def connector_id(self) -> str:
        return f"guardian_{self._crossword_type}"

    @property
    def source(self) -> str:
        return "guardian"

    @property
    def source_name(self) -> str:
        return "Guardian"

    @property
    def name(self) -> str:
        return self._crossword_type.title()

    @property
    def schedule(self) -> str | None:
        return self._schedule

    @property
    def supports_url(self) -> bool:
        return True

    def fetch_today(self) -> dict[str, Any]:
        """Fetch the most recently published puzzle for this series.

        Uses the series listing directly rather than date lookup, since the
        latest puzzle may not yet be indexed under today's calendar date.
        """
        html_content = _fetch_series_listing(self._crossword_type)
        m = re.search(rf"/crosswords/{re.escape(self._crossword_type)}/(\d+)", html_content)
        if not m:
            raise ScraperError(
                f"Could not find a crossword number in the Guardian {self._crossword_type} series listing"
            )
        return self.fetch_by_number(int(m.group(1)))

    def fetch_for_date(self, puzzle_date: date) -> dict[str, Any]:
        """Fetch the puzzle for a specific date.

        For recent dates (within ~3–4 weeks) the puzzle number is read directly
        from the series listing.  For older dates the number is estimated from the
        weekly_rate and confirmed with at most ±14 additional fetches.
        Raises ScraperError if no puzzle can be found for that date.
        """
        number = _puzzle_number_for_date(puzzle_date, self._crossword_type, self._weekly_rate)
        return self.fetch_by_number(number)

    def fetch_by_url(self, url: str, include_solutions: bool = True) -> dict[str, Any]:
        """Fetch a crossword by URL, type/number path, or bare number string."""
        return fetch_and_convert(
            normalise_url(url, self._crossword_type), include_solutions=include_solutions
        )

    def fetch_by_number(self, number: int) -> dict[str, Any]:
        """Fetch a crossword by its puzzle number, e.g. fetch_by_number(30012)."""
        url = f"{_GUARDIAN_BASE}/crosswords/{self._crossword_type}/{number}"
        return fetch_and_convert(url)

    def default_output_name(self, ipuz: dict[str, Any]) -> str:
        # Extract type and number from the origin URL, e.g. .../crosswords/cryptic/30012
        origin = ipuz.get("origin", "")
        m = re.search(r"/crosswords/([a-z-]+)/(\d+)", origin)
        if m:
            return f"guardian_{m.group(1)}_{m.group(2)}.ipuz"
        return super().default_output_name(ipuz)
