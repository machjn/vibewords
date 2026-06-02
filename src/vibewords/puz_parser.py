"""
Parser for Across Lite .puz crossword files.

Produces a Crossword object via parse_puz(source).

--------------------------------------------------------------------
.puz binary layout
--------------------------------------------------------------------
Offset  Len  Content
------  ---  -------
0x00     2   Global checksum (CRC of header + solution + player grids)
0x02    12   Magic string: b"ACROSS&DOWN\\0"
0x0E     2   Header CRC (CRC of just the fixed-size header block)
0x10     8   "Masked" checksums — used to detect file corruption
0x18     4   Version string, e.g. b"1.3\\0"
0x1C     2   Reserved / high byte of scrambled checksum
0x1E     2   Scrambled checksum (only meaningful when puzzle is locked)
0x20    12   Reserved padding
0x2C     1   Width  (number of columns)
0x2D     1   Height (number of rows)
0x2E     2   Number of clues (uint16 LE) — across + down combined
0x30     2   Puzzle type bitmask (0x0001 = diagramless)
0x32     2   Scrambled tag: 0 = unscrambled, 4 = scrambled with key
0x34   W*H  Solution grid — row-major, '.' = black, letter = answer
0x34+  W*H  Player grid  — same layout; '-' = unfilled, '.' = black

After the two grids come null-terminated latin-1 strings in this order:
  1. Title
  2. Author
  3. Copyright
  4. All clues — see ordering note below
  5. Notes (optional)

--------------------------------------------------------------------
Clue ordering (critical detail)
--------------------------------------------------------------------
Clues are stored in a flat array.  They are NOT "all across then all
down."  Instead they are interleaved in cell-number order: for each
numbered cell, the across clue (if this cell starts one) is emitted
first, then the down clue (if this cell starts one).

Example: if cell 1 starts both an across and a down word, cells 2-5
start only down words, and cell 6 starts only an across word, the
flat order is:

  [0] 1-Across   [1] 1-Down   [2] 2-Down   [3] 3-Down
  [4] 4-Down     [5] 5-Down   [6] 6-Across

--------------------------------------------------------------------
Cell numbering
--------------------------------------------------------------------
Computed by the client (not stored in the file).  A white cell is
numbered if it starts a valid across run (length ≥ 2) or a valid
down run (length ≥ 2).  Cells are visited left-to-right,
top-to-bottom; numbers are assigned sequentially starting at 1.

--------------------------------------------------------------------
Links (linked clues)
--------------------------------------------------------------------
The .puz format has no explicit linked-clue field.  Continuation
clues conventionally have text matching "See N [Across|Down]".
We reconstruct the links dict by scanning for this pattern.
"""

from __future__ import annotations

import re
import struct
from typing import Optional

from vibewords.crossword_model import Cell, Clue, Crossword

_MAGIC = b"ACROSS&DOWN\x00"

# Continuation clue: entire text is "See N [Across|Down]".
_SEE_RE = re.compile(
    r"^\s*[Ss]ee\s+(\d+)(?:\s*[-\s]\s*(across|down))?\s*[.]?\s*$",
    re.IGNORECASE,
)

# Primary linked clue: text begins with comma-separated clue numbers optionally
# followed by a direction, e.g. "14, 11 Down, ..." or "9 and 14 Across. ...".
# Group 1 = all digit tokens, group 2 = optional direction word.
# NOTE: untested — we have no linked-clue .puz sample to verify against.
_LEADING_NUMS_RE = re.compile(
    r"^\s*((?:\d+(?:\s*(?:,|and)\s*))+\d+)\s*(across|down)?\s*[.,]?\s*",
    re.IGNORECASE,
)


def parse_puz(source: bytes) -> Crossword:
    """Parse a .puz file and return a validated Crossword."""
    if source[2:14] != _MAGIC:
        raise ValueError("Not a valid .puz file (magic bytes not found)")

    # Scrambled puzzles have their solution encrypted with a numeric key.
    # We can still parse the structure but the solution letters are garbage.
    scrambled = struct.unpack_from("<H", source, 0x32)[0] == 4

    width     = source[0x2C]
    height    = source[0x2D]
    num_clues = struct.unpack_from("<H", source, 0x2E)[0]

    grid_size  = width * height
    sol_start  = 0x34
    play_start = sol_start + grid_size
    str_start  = play_start + grid_size

    sol_bytes  = source[sol_start:sol_start + grid_size]
    play_bytes = source[play_start:play_start + grid_size]

    def ch(data: bytes, r: int, c: int) -> str:
        return chr(data[r * width + c])

    def is_black(r: int, c: int) -> bool:
        return ch(sol_bytes, r, c) == "."

    # Read null-terminated latin-1 strings starting at str_start.
    def _read_strings(start: int, count: int) -> list[str]:
        result, pos = [], start
        while len(result) < count and pos < len(source):
            end = source.index(b"\x00", pos)
            result.append(source[pos:end].decode("latin-1"))
            pos = end + 1
        return result

    strings   = _read_strings(str_start, 3 + num_clues)
    title     = strings[0].strip() if len(strings) > 0 else ""
    author    = strings[1].strip() if len(strings) > 1 else ""
    raw_clues = strings[3:3 + num_clues]

    # ------------------------------------------------------------------
    # Compute cell numbers and build the interleaved clue-assignment order.
    #
    # For each numbered cell (visited in reading order):
    #   - consume one clue for across (if this cell starts an across run)
    #   - consume one clue for down   (if this cell starts a down run)
    # ------------------------------------------------------------------
    numbers: list[list[Optional[int]]] = [[None] * width for _ in range(height)]
    # Each entry: (number, 'A'|'D', row, col)
    clue_slots: list[tuple[int, str, int, int]] = []
    idx = 0

    for r in range(height):
        for c in range(width):
            if is_black(r, c):
                continue
            starts_a = (
                (c == 0 or is_black(r, c - 1))
                and c + 1 < width
                and not is_black(r, c + 1)
            )
            starts_d = (
                (r == 0 or is_black(r - 1, c))
                and r + 1 < height
                and not is_black(r + 1, c)
            )
            if starts_a or starts_d:
                idx += 1
                numbers[r][c] = idx
            # Interleaved order: across before down for the same cell.
            if numbers[r][c] is not None:
                if starts_a:
                    clue_slots.append((numbers[r][c], "A", r, c))
                if starts_d:
                    clue_slots.append((numbers[r][c], "D", r, c))

    cells = [
        [Cell(row=r, col=c, black=is_black(r, c), number=numbers[r][c])
         for c in range(width)]
        for r in range(height)
    ]

    solution = None if scrambled else [
        ["" if is_black(r, c) else ch(sol_bytes, r, c)
         for c in range(width)]
        for r in range(height)
    ]

    saved_raw = [
        ["" if ch(play_bytes, r, c) in (".", "-") else ch(play_bytes, r, c)
         for c in range(width)]
        for r in range(height)
    ]
    saved = saved_raw if any(v for row in saved_raw for v in row) else None

    # Assign clue texts to across/down lists in interleaved order.
    clues_across: list[Clue] = []
    clues_down:   list[Clue] = []
    for i, (num, direction, _r, _c) in enumerate(clue_slots):
        text = raw_clues[i] if i < len(raw_clues) else ""
        clue = Clue(number=num, text=text, label=str(num))
        if direction == "A":
            clues_across.append(clue)
        else:
            clues_down.append(clue)

    # ------------------------------------------------------------------
    # Reconstruct linked-clue links from "See N [Across|Down]" patterns.
    #
    # A continuation clue's text matches _SEE_RE.  We find its primary
    # (clue N in the stated direction, defaulting to the opposite direction
    # of the continuation) and build the links dict in the same format
    # used by ipuz_parser and xw_parser.
    # ------------------------------------------------------------------
    across_set = {c.number for c in clues_across}
    down_set   = {c.number for c in clues_down}
    links: dict = {}

    for clue in clues_across:
        m = _SEE_RE.match(clue.text)
        if m:
            # Continuation: this Across clue defers to another.
            primary_num = int(m.group(1))
            stated = (m.group(2) or "").lower()
            primary_dir = "Down" if stated in ("", "down") else "Across"
            _register_link(links, primary_num, primary_dir, clue.number, "Across",
                            across_set, down_set)
            continue
        m = _LEADING_NUMS_RE.match(clue.text)
        if m:
            # Primary: text starts with "N, M [direction]" — link to continuations.
            nums = [int(t) for t in re.split(r"[,\s]+(?:and\s+)?|\s+and\s+", m.group(1)) if t.strip().isdigit()]
            if len(nums) > 1 and nums[0] == clue.number:
                stated = (m.group(2) or "").lower()
                # Direction applies to the continuation number(s).
                cont_dir = "Down" if stated == "down" else "Across"
                for cont_num in nums[1:]:
                    _register_link(links, clue.number, "Across", cont_num, cont_dir,
                                    across_set, down_set)

    for clue in clues_down:
        m = _SEE_RE.match(clue.text)
        if m:
            primary_num = int(m.group(1))
            stated = (m.group(2) or "").lower()
            primary_dir = "Across" if stated in ("", "across") else "Down"
            _register_link(links, primary_num, primary_dir, clue.number, "Down",
                            across_set, down_set)
            continue
        m = _LEADING_NUMS_RE.match(clue.text)
        if m:
            nums = [int(t) for t in re.split(r"[,\s]+(?:and\s+)?|\s+and\s+", m.group(1)) if t.strip().isdigit()]
            if len(nums) > 1 and nums[0] == clue.number:
                stated = (m.group(2) or "").lower()
                cont_dir = "Across" if stated == "across" else "Down"
                for cont_num in nums[1:]:
                    _register_link(links, clue.number, "Down", cont_num, cont_dir,
                                    across_set, down_set)

    crossword = Crossword(
        width=width,
        height=height,
        cells=cells,
        clues_across=clues_across,
        clues_down=clues_down,
        solution=solution,
        saved=saved,
        title=title,
        author=author,
        links=links,
    )
    crossword.validate()
    return crossword


def _register_link(
    links: dict,
    primary_num: int,
    primary_dir: str,
    cont_num: int,
    cont_dir: str,
    across_set: set,
    down_set: set,
) -> None:
    """Add a linked-clue entry for a continuation detected via "See N" text."""
    # Verify the primary actually exists in the puzzle.
    exists = primary_num in (across_set if primary_dir == "Across" else down_set)
    if not exists:
        return
    chain = [[primary_num, primary_dir], [cont_num, cont_dir]]
    # Both the primary and the continuation get a key so getChain() works
    # from either end (mirrors the convention in xw_parser and ipuz format).
    links.setdefault(primary_dir, {})[str(primary_num)] = chain
    links.setdefault(cont_dir, {})[str(cont_num)] = chain
