"""Guardian crossword scraper and IPUZ converter (library module).

Import and call `fetch_and_convert(url)` for the common case, or use the
lower-level helpers individually.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.request import Request, urlopen


BLOCK = "#"
EMPTY = "0"


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
            "User-Agent": "vibeword/0.1 (+personal use)",
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


def clean_clue_text(clue: str) -> str:
    clue = html.unescape(clue)
    clue = re.sub(r"<[^>]+>", "", clue)
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


def _build_clues(slots: list[Slot]) -> dict[str, list[list[Any]]]:
    clues: dict[str, list[list[Any]]] = {"Across": [], "Down": []}
    for slot in sorted(slots, key=lambda s: (s.direction != "Across", s.number, s.x, s.y)):
        label: int | str = slot.number if slot.human_number == str(slot.number) else slot.human_number
        clues[slot.direction].append([label, slot.clue])
    return clues


def _build_links(slots: list[Slot]) -> dict[str, dict[int, list[int]]]:
    """Return a map {direction: {clue_num: [ordered chain of clue nums]}} for linked clues."""
    links: dict[str, dict[int, list[int]]] = {"Across": {}, "Down": {}}
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
        # All entries in a group must share a direction for our purposes
        dirs = {s.direction for s in ordered}
        if len(dirs) != 1:
            continue

        chain = [s.number for s in ordered]
        dir_key = ordered[0].direction
        for num in chain:
            links[dir_key][num] = chain

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
