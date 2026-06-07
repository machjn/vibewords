import re
from dataclasses import dataclass, field
from typing import Optional

from vibewords.clue_format import sanitize_clue_html


def _normalise_date(raw: str) -> str:
    """Normalise a date string to ISO YYYY-MM-DD; return '' if unrecognised."""
    raw = raw.strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
        return raw
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', raw)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return ''


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
    label: str = ""   # display label, e.g. "25, 11" for linked clues
    answer: str = ""  # full answer with separators, e.g. "MILLE-FEUILLE" or "BIG CAT"

    def __post_init__(self):
        self.text = sanitize_clue_html(self.text)


@dataclass
class Crossword:
    width: int
    height: int
    cells: list  # list[list[Cell]]
    clues_across: list  # list[Clue]
    clues_down: list  # list[Clue]
    solution: Optional[list] = None  # list[list[str]]
    saved: Optional[list] = None    # list[list[str]] — previously entered letters
    title: str = ""
    author: str = ""
    publisher: str = ""
    date: str = ""        # ISO format YYYY-MM-DD, if known
    links: dict = field(default_factory=dict)  # {direction: {clue_num: [chain]}}
    source_url: str = ""  # public URL of the original puzzle, if known

    def __post_init__(self):
        self.date = _normalise_date(self.date)

    def __str__(self) -> str:
        lines = []
        header = " — ".join(filter(None, [self.title, self.author]))
        if header:
            lines.append(header)
        if self.date:
            lines.append(f"Published: {self.date}")
        if header or self.date:
            lines.append("")

        for r, row in enumerate(self.cells):
            parts = []
            for c, cell in enumerate(row):
                if cell.black:
                    parts.append("#")
                elif self.solution and self.solution[r][c]:
                    parts.append(self.solution[r][c])
                else:
                    parts.append("·")
            lines.append(" ".join(parts))

        if self.clues_across:
            lines += ["", "ACROSS"]
            for clue in self.clues_across:
                lines.append(f"  {clue.label}. {clue.text}")

        if self.clues_down:
            lines += ["", "DOWN"]
            for clue in self.clues_down:
                lines.append(f"  {clue.label}. {clue.text}")

        return "\n".join(lines)

    def validate(self) -> None:
        """Raise ValueError if the crossword's internal structure is inconsistent."""
        # 1. Grid dimensions
        if len(self.cells) != self.height:
            raise ValueError(
                f"cells has {len(self.cells)} rows but height={self.height}"
            )
        for r, row in enumerate(self.cells):
            if len(row) != self.width:
                raise ValueError(
                    f"row {r} has {len(row)} cells but width={self.width}"
                )

        # 2. Cell numbers are consecutive from 1 with no gaps
        numbered = sorted(
            cell.number
            for row in self.cells
            for cell in row
            if cell.number is not None
        )
        for expected, actual in enumerate(numbered, start=1):
            if actual != expected:
                raise ValueError(
                    f"cell numbers are not consecutive: expected {expected}, got {actual}"
                )

        # 3 & 4. Every clue number maps to a numbered cell that starts the right kind of run
        number_to_cell: dict[int, Cell] = {
            cell.number: cell
            for row in self.cells
            for cell in row
            if cell.number is not None
        }
        for clue in self.clues_across:
            cell = number_to_cell.get(clue.number)
            if cell is None:
                raise ValueError(f"across clue {clue.number}: no cell with that number")
            r, c = cell.row, cell.col
            if self.cells[r][c].black:
                raise ValueError(f"across clue {clue.number}: starting cell is black")
            left_black = c == 0 or self.cells[r][c - 1].black
            if not left_black:
                raise ValueError(
                    f"across clue {clue.number}: cell ({r},{c}) does not start an across run"
                )
            if c + 1 >= self.width or self.cells[r][c + 1].black:
                raise ValueError(
                    f"across clue {clue.number}: run at ({r},{c}) is shorter than 2"
                )

        for clue in self.clues_down:
            cell = number_to_cell.get(clue.number)
            if cell is None:
                raise ValueError(f"down clue {clue.number}: no cell with that number")
            r, c = cell.row, cell.col
            if self.cells[r][c].black:
                raise ValueError(f"down clue {clue.number}: starting cell is black")
            top_black = r == 0 or self.cells[r - 1][c].black
            if not top_black:
                raise ValueError(
                    f"down clue {clue.number}: cell ({r},{c}) does not start a down run"
                )
            if r + 1 >= self.height or self.cells[r + 1][c].black:
                raise ValueError(
                    f"down clue {clue.number}: run at ({r},{c}) is shorter than 2"
                )

        # 5. Solution dimensions match grid
        if self.solution is not None:
            if len(self.solution) != self.height:
                raise ValueError(
                    f"solution has {len(self.solution)} rows but height={self.height}"
                )
            for r, row in enumerate(self.solution):
                if len(row) != self.width:
                    raise ValueError(
                        f"solution row {r} has {len(row)} entries but width={self.width}"
                    )
