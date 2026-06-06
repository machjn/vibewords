import json
from typing import Optional

from vibewords.crossword_model import Cell, Clue, Crossword


def parse_ipuz(data) -> Crossword:
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

    crossword = Crossword(
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
    crossword.validate()
    return crossword


def to_ipuz(crossword: Crossword) -> bytes:
    """Serialise a Crossword to ipuz JSON bytes."""
    puzzle_grid = []
    for row in crossword.cells:
        puzzle_grid.append([
            "#" if cell.black else ({"cell": cell.number} if cell.number else 0)
            for cell in row
        ])

    doc = {
        "version": "http://ipuz.org/v2",
        "kind": ["http://ipuz.org/crossword#1"],
        "dimensions": {"width": crossword.width, "height": crossword.height},
        "puzzle": puzzle_grid,
        "clues": {
            "Across": [_clue_entry(c) for c in crossword.clues_across],
            "Down":   [_clue_entry(c) for c in crossword.clues_down],
        },
    }

    for key, val in [
        ("title",  crossword.title),
        ("author", crossword.author),
        ("date",   crossword.date),
        ("origin", crossword.source_url),
    ]:
        if val:
            doc[key] = val

    if crossword.links:
        doc["links"] = crossword.links

    if crossword.solution is not None:
        doc["solution"] = [
            ["#" if crossword.cells[r][c].black else (v or 0)
             for c, v in enumerate(row)]
            for r, row in enumerate(crossword.solution)
        ]

    if crossword.saved is not None:
        doc["saved"] = [
            ["#" if crossword.cells[r][c].black else (v or 0)
             for c, v in enumerate(row)]
            for r, row in enumerate(crossword.saved)
        ]

    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()


def _parse_cell(row: int, col: int, val) -> Cell:
    if isinstance(val, dict):
        val = val.get("cell", 0)
    if val == "#":
        return Cell(row=row, col=col, black=True)
    number = val if isinstance(val, int) and val > 0 else None
    return Cell(row=row, col=col, black=False, number=number)


def _clue_entry(c: Clue) -> list:
    label = c.number if c.label == str(c.number) else c.label
    entry: list = [label, c.text]
    if c.answer:
        entry.append({"solution": c.answer})
    return entry


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
            answer = ""
            if len(item) >= 3 and isinstance(item[2], dict):
                answer = str(item[2].get("solution", "") or "")
            clues.append(Clue(number=number, text=str(item[1]), label=label, answer=answer))
        elif isinstance(item, dict):
            number = int(item["number"])
            clues.append(Clue(number=number, text=str(item.get("clue", "")), label=str(number)))
    return clues
