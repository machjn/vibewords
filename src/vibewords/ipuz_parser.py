import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Cell:
    row: int
    col: int
    black: bool = False
    number: Optional[int] = None


@dataclass
class Clue:
    number: int
    text: str
    label: str = ""  # display label, e.g. "25, 11" for linked clues


@dataclass
class Puzzle:
    width: int
    height: int
    cells: list  # list[list[Cell]]
    clues_across: list  # list[Clue]
    clues_down: list  # list[Clue]
    solution: Optional[list] = None  # list[list[str]]
    saved: Optional[list] = None    # list[list[str]] — previously entered letters
    title: str = ""
    author: str = ""
    date: str = ""        # ISO format YYYY-MM-DD, if known
    links: dict = field(default_factory=dict)  # {direction: {clue_num: [chain]}}
    source_url: str = ""  # public URL of the original puzzle, if known


def parse_ipuz(data) -> Puzzle:
    if isinstance(data, (str, bytes)):
        raw = json.loads(data)
    else:
        raw = data

    dims = raw.get("dimensions", {})
    width = dims.get("width", 0)
    height = dims.get("height", 0)

    cells = []
    for r, row in enumerate(raw.get("puzzle", [])):
        cells.append([_parse_cell(r, c, val) for c, val in enumerate(row)])

    raw_clues = raw.get("clues", {})
    across = _parse_clues(raw_clues.get("Across", []))
    down = _parse_clues(raw_clues.get("Down", []))

    solution = None
    if "solution" in raw:
        solution = []
        for row in raw["solution"]:
            sol_row = []
            for cell in row:
                if isinstance(cell, str):
                    sol_row.append(cell)
                elif cell is None or cell == 0:
                    sol_row.append("")
                else:
                    sol_row.append(str(cell))
            solution.append(sol_row)

    saved = None
    if "saved" in raw:
        saved = []
        for row in raw["saved"]:
            saved_row = []
            for cell in row:
                if isinstance(cell, str) and cell not in ("#", "0"):
                    saved_row.append(cell.upper()[:1])
                else:
                    saved_row.append("")
            saved.append(saved_row)

    return Puzzle(
        width=width,
        height=height,
        cells=cells,
        clues_across=across,
        clues_down=down,
        solution=solution,
        saved=saved,
        title=raw.get("title", ""),
        author=raw.get("author", ""),
        date=raw.get("date", ""),
        links=raw.get("links", {}),
        source_url=raw.get("origin", ""),
    )


def _parse_cell(row: int, col: int, val) -> Cell:
    if isinstance(val, dict):
        val = val.get("cell", 0)
    if val == "#":
        return Cell(row=row, col=col, black=True)
    number = val if isinstance(val, int) and val > 0 else None
    return Cell(row=row, col=col, black=False, number=number)


def _parse_clues(raw_clues: list) -> list:
    clues = []
    for item in raw_clues:
        if isinstance(item, list) and len(item) >= 2:
            raw_num = item[0]
            label = str(raw_num)
            try:
                number = int(raw_num)
            except (ValueError, TypeError):
                # Linked-clue label e.g. "25, 11" — use leading number for cell matching
                try:
                    number = int(str(raw_num).split(",")[0].strip())
                except ValueError:
                    number = 0
            clues.append(Clue(number=number, text=str(item[1]), label=label))
        elif isinstance(item, dict):
            number = int(item["number"])
            clues.append(Clue(number=number, text=str(item.get("clue", "")), label=str(number)))
    return clues
