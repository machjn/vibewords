"""
Parser for the .xw crossword file format.

Produces a Crossword object via parse_xw(source).

File structure
--------------
  KEY = VALUE          ← metadata lines
  ---...               ← separator (3+ dashes)
  A  B  C  -  D  ...   ← grid (space-separated single-char cells)

  ACROSS               ← clue-direction header
  1. Clue text (7) SOL
  11, 17. Linked (5-7) MILLE-FEUILLE
  ...
  DOWN
  17. Voyez 11 Across.  ← reference clue (no length/solution)
  ...
"""

from __future__ import annotations

import re
from typing import Optional

from lark import Discard, Lark, Transformer, v_args

from vibewords.crossword_model import Cell, Clue, Crossword

# ---------------------------------------------------------------------------
# Grammar

_GRAMMAR = r"""
start            : metadata_section grid_section clue_section

metadata_section : meta_entry* SEPARATOR NEWLINE*
meta_entry       : META_KEY "=" META_VAL NEWLINE

grid_section     : grid_row+ NEWLINE*
grid_row         : CELL+ NEWLINE

clue_section     : direction_block+
direction_block  : DIRECTION NEWLINE+ clue_line* NEWLINE*
clue_line        : clue_nums "." clue_body "(" length_spec ")" solution NEWLINE
                 | clue_nums "." clue_body "(" length_spec ")"           NEWLINE
                 | clue_nums "."           ref_body                      NEWLINE

clue_nums        : INT ("," INT)*
clue_body        : /[^\n]+?(?=[ \t]*\(\d[\d,\-]*\))/
length_spec      : INT (("," | "-") INT)*
solution         : /[^\n]+/
ref_body         : /[^\n]+/

META_KEY         : /[A-Za-z][A-Za-z0-9]*/
META_VAL         : /[^\n]+/
SEPARATOR        : /---+[^\n]*/
CELL             : /[A-Za-zÀ-ÿ0-9#\-]/
DIRECTION        : /ACROSS|DOWN/i
INT              : /[0-9]+/
NEWLINE          : /\r?\n/

%ignore /[ \t]+/
"""

_parser = Lark(_GRAMMAR, parser="earley", lexer="dynamic", ambiguity="resolve")


# ---------------------------------------------------------------------------
# Grid helpers

_BLACK = {"-", "0", "#"}


def _is_black(ch: str) -> bool:
    return ch in _BLACK


def _number_cells(grid: list[list[str]]) -> list[list[Optional[int]]]:
    h, w = len(grid), len(grid[0])
    numbers: list[list[Optional[int]]] = [[None] * w for _ in range(h)]
    idx = 0
    for r in range(h):
        for c in range(w):
            if _is_black(grid[r][c]):
                continue
            starts_across = (
                (c == 0 or _is_black(grid[r][c - 1]))
                and c + 1 < w
                and not _is_black(grid[r][c + 1])
            )
            starts_down = (
                (r == 0 or _is_black(grid[r - 1][c]))
                and r + 1 < h
                and not _is_black(grid[r + 1][c])
            )
            if starts_across or starts_down:
                idx += 1
                numbers[r][c] = idx
    return numbers


def _place_solution(
    sol_grid: list[list[str]],
    grid: list[list[str]],
    numbers: list[list[Optional[int]]],
    clue_num: int,
    direction: str,
    solution_str: str,
    chain: list | None = None,
) -> None:
    """Place solution letters into sol_grid, following a link chain if provided.

    chain is a list of [num, "Across"|"Down"] entries for continuation segments.
    """
    h, w = len(grid), len(grid[0])
    num_to_pos = {
        numbers[r][c]: (r, c)
        for r in range(h) for c in range(w)
        if numbers[r][c] is not None
    }

    letters = [ch for ch in solution_str.upper() if ch.isalpha()]
    letter_idx = 0

    segments = (
        [(n, "A" if d == "Across" else "D") for n, d in chain]
        if chain else [(clue_num, direction)]
    )

    for seg_num, seg_dir in segments:
        if letter_idx >= len(letters):
            break
        start = num_to_pos.get(seg_num)
        if start is None:
            continue
        r, c = start
        dr, dc = (0, 1) if seg_dir == "A" else (1, 0)
        while letter_idx < len(letters) and 0 <= r < h and 0 <= c < w and not _is_black(grid[r][c]):
            sol_grid[r][c] = letters[letter_idx]
            letter_idx += 1
            r += dr
            c += dc


# ---------------------------------------------------------------------------
# Transformer

@v_args(inline=True)
class _XwTransformer(Transformer):

    # Discard structural tokens — they shape the parse but aren't data.
    def NEWLINE(self, _):   return Discard
    def SEPARATOR(self, _): return Discard

    # Terminals → Python scalars
    def INT(self, t):       return int(t)
    def META_KEY(self, t):  return str(t).strip()
    def META_VAL(self, t):  return str(t).strip()
    def CELL(self, t):      return str(t)
    def DIRECTION(self, t): return str(t).upper()

    # Metadata
    def meta_entry(self, key, val):
        return (key.lower(), val)

    def metadata_section(self, *entries):
        return dict(entries)

    # Grid
    def grid_row(self, *cells):
        return list(cells)

    def grid_section(self, *rows):
        return list(rows)

    # Clues
    def clue_nums(self, *ints):
        return list(ints)

    def length_spec(self, *parts):
        return list(parts)

    def clue_body(self, token):
        return str(token).strip()

    _TRAILING_LENGTH = re.compile(r'\s*\(\d[\d,\-]*\)\s*$')

    def solution(self, token):
        return self._TRAILING_LENGTH.sub('', str(token)).strip()

    def ref_body(self, token):
        return str(token).strip()

    def clue_line(self, *args):
        nums, text = args[0], args[1]
        if len(args) == 4:
            return {"nums": nums, "text": text, "length": args[2], "solution": args[3]}
        if len(args) == 3 and isinstance(args[2], list):
            return {"nums": nums, "text": text, "length": args[2], "solution": None}
        return {"nums": nums, "text": text, "length": None, "solution": None}

    def direction_block(self, direction, *clue_lines):
        return (direction[:1], list(clue_lines))  # "A" or "D"

    def clue_section(self, *blocks):
        return list(blocks)

    # Root: assemble Crossword
    def start(self, meta, grid, clue_blocks):
        h = len(grid)
        w = max(len(row) for row in grid)
        numbers = _number_cells(grid)

        cells = [
            [
                Cell(row=r, col=c, black=_is_black(grid[r][c]), number=numbers[r][c])
                for c in range(w)
            ]
            for r in range(h)
        ]

        sol_grid: list[list[str]] = [[""] * w for _ in range(h)]

        clues_across: list[Clue] = []
        clues_down: list[Clue] = []
        # Collect clues and defer solution placement until links are built.
        parsed_clues: list[tuple[str, dict]] = []

        for direction, clue_list in clue_blocks:
            for parsed in clue_list:
                nums: list[int] = parsed["nums"]
                clue = Clue(
                    number=nums[0],
                    text=parsed["text"],
                    label=", ".join(str(n) for n in nums),
                )
                if direction == "A":
                    clues_across.append(clue)
                else:
                    clues_down.append(clue)
                parsed_clues.append((direction, parsed))

        # Build links in the format main.py expects:
        # {"Across": {"11": [[17, "Down"]]}, ...}
        across_nums = {c.number for c in clues_across}
        down_nums = {c.number for c in clues_down}
        links: dict = {}
        for direction, parsed in parsed_clues:
            nums = parsed["nums"]
            if len(nums) < 2:
                continue
            other_dir = "D" if direction == "A" else "A"
            # Chain starts with the primary so chain_entries(chain)[1:] correctly
            # identifies continuations and register_head walks all segments.
            chain: list = [[nums[0], "Across" if direction == "A" else "Down"]]
            for n in nums[1:]:
                only_a = n in across_nums and n not in down_nums
                only_d = n in down_nums and n not in across_nums
                seg_dir = "A" if only_a else ("D" if only_d else other_dir)
                chain.append([n, "Across" if seg_dir == "A" else "Down"])
            dir_key = "Across" if direction == "A" else "Down"
            links.setdefault(dir_key, {})[str(nums[0])] = chain
            # Register every continuation under its own direction so getChain
            # works from either end of the chain.
            for seg_num, seg_dir_str in (e for e in chain[1:]):
                links.setdefault(seg_dir_str, {})[str(seg_num)] = chain

        # Place solutions now that links are available.
        for direction, parsed in parsed_clues:
            if not parsed["solution"]:
                continue
            nums = parsed["nums"]
            dir_key = "Across" if direction == "A" else "Down"
            chain = (links.get(dir_key) or {}).get(str(nums[0]))
            _place_solution(sol_grid, grid, numbers, nums[0], direction, parsed["solution"], chain)

        return Crossword(
            width=w,
            height=h,
            cells=cells,
            clues_across=clues_across,
            clues_down=clues_down,
            solution=sol_grid,
            title=meta.get("title", ""),
            author=meta.get("author", ""),
            date=meta.get("date", ""),
            links=links,
        )


_transformer = _XwTransformer()


# ---------------------------------------------------------------------------
# Public API

def parse_xw(source: str | bytes) -> Crossword:
    """Parse a .xw file and return a validated Crossword."""
    if isinstance(source, bytes):
        source = source.decode()
    if not source.endswith("\n"):
        source += "\n"
    tree = _parser.parse(source)
    crossword = _transformer.transform(tree)
    crossword.validate()
    return crossword
