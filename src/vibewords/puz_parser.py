"""
Parser for Across Lite .puz crossword files.

Produces a Crossword object via parse_puz(source).

Format reference: https://code.google.com/archive/p/puz/wikis/FileFormat.wiki
"""

from __future__ import annotations

import struct
from typing import Optional

from vibewords.crossword_model import Cell, Clue, Crossword

_MAGIC = b"ACROSS&DOWN\x00"


def parse_puz(source: bytes) -> Crossword:
    """Parse a .puz file and return a validated Crossword."""
    if source[2:14] != _MAGIC:
        raise ValueError("Not a valid .puz file (magic bytes not found)")

    width  = source[0x2C]
    height = source[0x2D]
    num_clues = struct.unpack_from("<H", source, 0x2E)[0]

    # Solution and player grids follow the fixed header at 0x34.
    grid_size  = width * height
    sol_start  = 0x34
    play_start = sol_start + grid_size
    str_start  = play_start + grid_size

    sol_bytes  = source[sol_start:sol_start + grid_size]
    play_bytes = source[play_start:play_start + grid_size]

    def cell_ch(data: bytes, r: int, c: int) -> str:
        return chr(data[r * width + c])

    def is_black(r: int, c: int) -> bool:
        return cell_ch(sol_bytes, r, c) == "."

    # Read null-terminated latin-1 strings: title, author, copyright, clues…, notes
    def _read_strings(start: int, count: int) -> list[str]:
        result = []
        pos = start
        while len(result) < count and pos < len(source):
            end = source.index(b"\x00", pos)
            result.append(source[pos:end].decode("latin-1"))
            pos = end + 1
        return result

    strings   = _read_strings(str_start, 3 + num_clues)
    title     = strings[0].strip() if len(strings) > 0 else ""
    author    = strings[1].strip() if len(strings) > 1 else ""
    raw_clues = strings[3:3 + num_clues]

    # Assign cell numbers in reading order.
    numbers: list[list[Optional[int]]] = [[None] * width for _ in range(height)]
    across_nums: list[int] = []
    down_nums:   list[int] = []
    idx = 0
    for r in range(height):
        for c in range(width):
            if is_black(r, c):
                continue
            starts_a = (c == 0 or is_black(r, c - 1)) and c + 1 < width  and not is_black(r, c + 1)
            starts_d = (r == 0 or is_black(r - 1, c)) and r + 1 < height and not is_black(r + 1, c)
            if starts_a or starts_d:
                idx += 1
                numbers[r][c] = idx
            if numbers[r][c] is not None:
                if starts_a:
                    across_nums.append(numbers[r][c])
                if starts_d:
                    down_nums.append(numbers[r][c])

    cells = [
        [Cell(row=r, col=c, black=is_black(r, c), number=numbers[r][c])
         for c in range(width)]
        for r in range(height)
    ]

    solution = [
        ["" if is_black(r, c) else cell_ch(sol_bytes, r, c)
         for c in range(width)]
        for r in range(height)
    ]

    # Player grid: '-' = empty, '.' = black; only include if non-empty.
    saved_raw = [
        ["" if cell_ch(play_bytes, r, c) in (".", "-") else cell_ch(play_bytes, r, c)
         for c in range(width)]
        for r in range(height)
    ]
    saved = saved_raw if any(v for row in saved_raw for v in row) else None

    # Distribute the flat clue list: all across in number order, then all down.
    clue_idx = 0
    clues_across = []
    for num in across_nums:
        text = raw_clues[clue_idx] if clue_idx < len(raw_clues) else ""
        clues_across.append(Clue(number=num, text=text, label=str(num)))
        clue_idx += 1

    clues_down = []
    for num in down_nums:
        text = raw_clues[clue_idx] if clue_idx < len(raw_clues) else ""
        clues_down.append(Clue(number=num, text=text, label=str(num)))
        clue_idx += 1

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
    )
    crossword.validate()
    return crossword
