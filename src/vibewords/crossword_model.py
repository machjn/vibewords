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
    date: str = ""        # ISO format YYYY-MM-DD, if known
    links: dict = field(default_factory=dict)  # {direction: {clue_num: [chain]}}
    source_url: str = ""  # public URL of the original puzzle, if known
