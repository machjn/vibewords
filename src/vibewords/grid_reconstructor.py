"""
Grid reconstructor for British cryptic crosswords.

Given a complete word list as (index, direction, length) triples — derivable from
clue numbering and parenthetical lengths in the clue text — this module does a
backtracking search to find all NxN grids (odd N) satisfying:

  • Word ordering  : word start-cells appear in reading order matching given indices
  • British grid   : no 2×2 block of white squares
  • Rotational sym : enforced during search via symmetric cell propagation
  • Connectivity   : all white cells form one connected region
  • Checking rules : ≥ half of each word's letters are checked; no run of >2
                     consecutive unchecked; first and last letters always checked
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Iterator, Optional


WHITE: bool = True
BLACK: bool = False
UNKNOWN = None

CellState = Optional[bool]


# ---------------------------------------------------------------------------
# Public data types

@dataclass(frozen=True)
class WordSpec:
    """A word identified by its crossword number, direction, and length."""
    index: int      # reading-order rank of the word's starting cell (1-based)
    direction: str  # 'A' (across) or 'D' (down)
    length: int


@dataclass(frozen=True)
class PlacedWord:
    index: int
    direction: str
    length: int
    row: int
    col: int


# ---------------------------------------------------------------------------
# Grid

class Grid:
    """NxN grid with three-valued cells: WHITE, BLACK, or UNKNOWN."""

    __slots__ = ("n", "cells")

    def __init__(self, n: int, cells: Optional[list[CellState]] = None) -> None:
        self.n = n
        self.cells: list[CellState] = list(cells) if cells is not None else [UNKNOWN] * (n * n)

    def copy(self) -> Grid:
        return Grid(self.n, self.cells)

    def _i(self, row: int, col: int) -> int:
        return row * self.n + col

    def get(self, row: int, col: int) -> CellState:
        if 0 <= row < self.n and 0 <= col < self.n:
            return self.cells[self._i(row, col)]
        return BLACK  # out-of-bounds counts as black

    def _set(self, row: int, col: int, value: bool) -> bool:
        """Set one cell; return False on conflict with an already-known value."""
        if not (0 <= row < self.n and 0 <= col < self.n):
            return value is BLACK
        i = self._i(row, col)
        cur = self.cells[i]
        if cur is UNKNOWN:
            self.cells[i] = value
            return True
        return cur == value

    def _set_sym(self, row: int, col: int, value: bool) -> bool:
        """Set a cell and its 180° symmetric partner; return False on conflict."""
        if not self._set(row, col, value):
            return False
        return self._set(self.n - 1 - row, self.n - 1 - col, value)

    # ------------------------------------------------------------------
    # Word placement

    def place_word(self, spec: WordSpec, row: int, col: int) -> Optional[Grid]:
        """
        Clone the grid, place *spec* at (row, col), and apply rotational symmetry.
        Returns the new Grid, or None if any cell conflict arises.

        Sets word cells to WHITE, the two boundary cells to BLACK, and mirrors
        all of those decisions to their symmetric counterparts.
        """
        g = self.copy()
        dr, dc = (0, 1) if spec.direction == 'A' else (1, 0)

        for i in range(spec.length):
            if not g._set_sym(row + dr * i, col + dc * i, WHITE):
                return None

        for br, bc in [(row - dr, col - dc),
                       (row + dr * spec.length, col + dc * spec.length)]:
            if 0 <= br < g.n and 0 <= bc < g.n:
                if not g._set_sym(br, bc, BLACK):
                    return None

        return g

    # ------------------------------------------------------------------
    # Constraint checks (cheap ones used during search)

    def has_2x2_white(self) -> bool:
        """True if any 2×2 block of cells is entirely white (British grid violation)."""
        n = self.n
        cells = self.cells
        for r in range(n - 1):
            base = r * n
            for c in range(n - 1):
                if (cells[base + c] is WHITE
                        and cells[base + c + 1] is WHITE
                        and cells[base + n + c] is WHITE
                        and cells[base + n + c + 1] is WHITE):
                    return True
        return False

    # ------------------------------------------------------------------
    # Finalisation and word extraction (used after search completes)

    def finalize(self) -> Grid:
        """Return a copy with all UNKNOWN cells set to BLACK."""
        g = self.copy()
        g.cells = [BLACK if c is UNKNOWN else c for c in g.cells]
        return g

    def extract_words(self) -> list[PlacedWord]:
        """
        Scan the finalised grid and return all maximal white runs of length ≥ 3,
        sorted by reading order, with crossword-style word indices assigned.
        Words at the same starting cell share an index (one across, one down).
        """
        n = self.n
        cells = self.cells
        # (cell_index, direction, row, col, length)
        raw: list[tuple[int, str, int, int, int]] = []

        for r in range(n):
            c = 0
            while c < n:
                if cells[r * n + c] is WHITE:
                    s = c
                    while c < n and cells[r * n + c] is WHITE:
                        c += 1
                    if c - s >= 3:
                        raw.append((r * n + s, 'A', r, s, c - s))
                else:
                    c += 1

        for col in range(n):
            r = 0
            while r < n:
                if cells[r * n + col] is WHITE:
                    s = r
                    while r < n and cells[r * n + col] is WHITE:
                        r += 1
                    if r - s >= 3:
                        raw.append((s * n + col, 'D', s, col, r - s))
                else:
                    r += 1

        # Primary: cell index (reading order).  Secondary: 'A' < 'D'.
        raw.sort(key=lambda e: (e[0], e[1]))

        result: list[PlacedWord] = []
        word_idx = 0
        prev_cell = -1
        for cell_i, direction, row, col, length in raw:
            if cell_i != prev_cell:
                word_idx += 1
                prev_cell = cell_i
            result.append(PlacedWord(
                index=word_idx, direction=direction,
                length=length, row=row, col=col,
            ))
        return result

    def is_connected(self) -> bool:
        """True if all white cells form a single connected component."""
        whites = [
            (r, c)
            for r in range(self.n)
            for c in range(self.n)
            if self.cells[self._i(r, c)] is WHITE
        ]
        if not whites:
            return True
        visited: set[tuple[int, int]] = {whites[0]}
        stack = [whites[0]]
        while stack:
            r, c = stack.pop()
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nb = (r + dr, c + dc)
                if nb not in visited and self.get(*nb) is WHITE:
                    visited.add(nb)
                    stack.append(nb)
        return len(visited) == len(whites)

    # ------------------------------------------------------------------

    def __str__(self) -> str:
        sym = {WHITE: '■', BLACK: '□', UNKNOWN: '·'}
        return '\n'.join(
            ' '.join(sym[self.cells[self._i(r, c)]] for c in range(self.n))
            for r in range(self.n)
        )


# ---------------------------------------------------------------------------
# Post-search validation

def _validate_checking(placed: list[PlacedWord]) -> bool:
    """
    Validate British checking rules:
      - first and last letters of every word are checked (part of a crossing word)
      - no run of >2 consecutive unchecked letters
      - at least half of each word's letters are checked
    """
    across_cells: set[tuple[int, int]] = set()
    down_cells: set[tuple[int, int]] = set()
    for pw in placed:
        for i in range(pw.length):
            if pw.direction == 'A':
                across_cells.add((pw.row, pw.col + i))
            else:
                down_cells.add((pw.row + i, pw.col))
    checked = across_cells & down_cells

    for pw in placed:
        L = pw.length
        cells = (
            [(pw.row, pw.col + i) for i in range(L)] if pw.direction == 'A'
            else [(pw.row + i, pw.col) for i in range(L)]
        )
        chk = [c in checked for c in cells]

        # No double-unch at word start or end (a single unch is permitted).
        if L >= 2 and not chk[0] and not chk[1]:
            return False
        if L >= 2 and not chk[-2] and not chk[-1]:
            return False

        unch_run = 0
        n_checked = 0
        for c in chk:
            if c:
                n_checked += 1
                unch_run = 0
            else:
                unch_run += 1
                if unch_run > 2:
                    return False

        if n_checked * 2 < L:
            return False  # fewer than half checked

    return True


def _specs_match(placed: list[PlacedWord], specs: list[WordSpec]) -> bool:
    if len(placed) != len(specs):
        return False
    return all(
        pw.index == s.index and pw.direction == s.direction and pw.length == s.length
        for pw, s in zip(placed, specs)
    )


# ---------------------------------------------------------------------------
# Backtracking search helpers

def _candidate_positions(
    spec: WordSpec, min_cell: int, max_cell: int, n: int, grid: Grid,
) -> Iterator[tuple[int, int]]:
    """Yield (row, col) starting positions in reading order within [min_cell, max_cell],
    skipping cells already known to be BLACK."""
    L = spec.length
    cells = grid.cells
    for cell in range(min_cell, max_cell + 1):
        row, col = divmod(cell, n)
        if cells[row * n + col] is BLACK:
            continue
        if spec.direction == 'A':
            if col + L <= n:
                yield row, col
        else:
            if row + L <= n:
                yield row, col


def _find_forced_position(
    spec: WordSpec, min_cell: int, max_cell: int, grid: Grid,
) -> Optional[tuple[int, int]]:
    """
    If symmetry has already determined exactly one valid starting position for *spec*
    in [min_cell, max_cell] (all word cells WHITE, both boundaries BLACK/edge), return it.
    Returns None if zero or more than one such position exists.
    """
    n = grid.n
    L = spec.length
    dr, dc = (0, 1) if spec.direction == 'A' else (1, 0)
    found: Optional[tuple[int, int]] = None

    for cell in range(min_cell, max_cell + 1):
        row, col = divmod(cell, n)
        if spec.direction == 'A' and col + L > n:
            continue
        if spec.direction == 'D' and row + L > n:
            continue
        if not all(grid.get(row + dr * i, col + dc * i) is WHITE for i in range(L)):
            continue
        br, bc = row - dr, col - dc
        if 0 <= br < n and 0 <= bc < n and grid.get(br, bc) is not BLACK:
            continue
        er, ec = row + dr * L, col + dc * L
        if 0 <= er < n and 0 <= ec < n and grid.get(er, ec) is not BLACK:
            continue
        if found is not None:
            return None  # ambiguous
        found = (row, col)

    return found


def _has_phantom_run(grid: Grid, spec_counts: Counter) -> bool:
    """
    Return True if any fully-bounded white run of length ≥ 3 appears more often
    than the spec allows.

    Runs of length 1–2 are ignored: they are unchecked cells of crossing words and
    are perfectly valid in a British cryptic grid.  Only runs that form actual words
    (length ≥ 3) need to match the spec.
    """
    n = grid.n
    cells = grid.cells
    found: Counter = Counter()

    for r in range(n):
        c = 0
        while c < n:
            if cells[r * n + c] is WHITE:
                s = c
                while c < n and cells[r * n + c] is WHITE:
                    c += 1
                left_ok = s == 0 or cells[r * n + s - 1] is BLACK
                right_ok = c == n or cells[r * n + c] is BLACK
                if left_ok and right_ok and c - s >= 3:
                    found[('A', c - s)] += 1
            else:
                c += 1

    for col in range(n):
        r = 0
        while r < n:
            if cells[r * n + col] is WHITE:
                s = r
                while r < n and cells[r * n + col] is WHITE:
                    r += 1
                top_ok = s == 0 or cells[(s - 1) * n + col] is BLACK
                bot_ok = r == n or cells[r * n + col] is BLACK
                if top_ok and bot_ok and r - s >= 3:
                    found[('D', r - s)] += 1
            else:
                r += 1

    return any(count > spec_counts.get(key, 0) for key, count in found.items())


def _has_spurious_run(grid: Grid, placed_cells: set[int], max_placed: int) -> bool:
    """
    Return True if any complete word-length run (≥3) starts at a cell index that:
      - is strictly less than max_placed (below the last word we placed), AND
      - is not in placed_cells (not one of the words we placed explicitly).

    Such a run is a "phantom word" inserted between already-placed words, which is
    impossible in a correctly numbered grid.  This catches, for example, a reflected
    word landing between two already-placed words when the wrong position was chosen.
    """
    if not placed_cells:
        return False
    n = grid.n
    cells = grid.cells

    for r in range(n):
        c = 0
        while c < n:
            if cells[r * n + c] is WHITE:
                s = c
                while c < n and cells[r * n + c] is WHITE:
                    c += 1
                if c - s >= 3:
                    left_ok = s == 0 or cells[r * n + s - 1] is BLACK
                    right_ok = c == n or cells[r * n + c] is BLACK
                    if left_ok and right_ok:
                        ci = r * n + s
                        if ci < max_placed and ci not in placed_cells:
                            return True
            else:
                c += 1

    for col in range(n):
        r = 0
        while r < n:
            if cells[r * n + col] is WHITE:
                s = r
                while r < n and cells[r * n + col] is WHITE:
                    r += 1
                if r - s >= 3:
                    top_ok = s == 0 or cells[(s - 1) * n + col] is BLACK
                    bot_ok = r == n or cells[r * n + col] is BLACK
                    if top_ok and bot_ok:
                        ci = s * n + col
                        if ci < max_placed and ci not in placed_cells:
                            return True
            else:
                r += 1

    return False


def _precompute_distinct_after(specs: list[WordSpec]) -> list[int]:
    """
    For each position i in specs, count how many distinct word indices appear
    strictly after specs[i].index.  Gives an upper-bound on the latest cell
    specs[i] can occupy (n² - 1 - distinct_after[i]).
    """
    indices = sorted({s.index for s in specs})
    distinct_for = {v: sum(1 for u in indices if u > v) for v in indices}
    return [distinct_for[s.index] for s in specs]


def _backtrack(
    specs: list[WordSpec],
    i: int,
    grid: Grid,
    last_cell: int,
    placed_cells: set[int],
    distinct_after: list[int],
    spec_counts: Counter,
    n: int,
    results: list[Grid],
    limit: int,
) -> None:
    if len(results) >= limit:
        return

    if i == len(specs):
        final = grid.finalize()
        placed = final.extract_words()
        if (_specs_match(placed, specs)
                and final.is_connected()
                and _validate_checking(placed)):
            results.append(final)
        return

    spec = specs[i]
    # Words sharing an index (one A, one D) start at the same cell.
    shared = i > 0 and specs[i - 1].index == spec.index

    if shared:
        row, col = divmod(last_cell, n)
        g2 = grid.place_word(spec, row, col)
        if g2 is not None and not g2.has_2x2_white() and not _has_phantom_run(g2, spec_counts):
            _backtrack(specs, i + 1, g2, last_cell, placed_cells, distinct_after, spec_counts, n, results, limit)
        return

    min_cell = last_cell + 1
    # Upper bound: leave at least one cell per remaining distinct word index.
    max_cell = n * n - 1 - distinct_after[i]

    dr, dc = (0, 1) if spec.direction == 'A' else (1, 0)
    for row, col in _candidate_positions(spec, min_cell, max_cell, n, grid):
        cell = row * n + col
        # Quick check: word cells must not already be BLACK and boundary must not be WHITE.
        if any(grid.get(row + dr * k, col + dc * k) is BLACK for k in range(spec.length)):
            continue
        br, bc = row - dr, col - dc
        if grid.get(br, bc) is WHITE:
            continue
        er, ec = row + dr * spec.length, col + dc * spec.length
        if grid.get(er, ec) is WHITE:
            continue
        g2 = grid.place_word(spec, row, col)
        if g2 is None:
            continue
        if g2.has_2x2_white():
            continue
        if _has_phantom_run(g2, spec_counts):
            continue
        if _has_spurious_run(g2, placed_cells, cell):
            continue
        placed_cells.add(cell)
        _backtrack(specs, i + 1, g2, cell, placed_cells, distinct_after, spec_counts, n, results, limit)
        placed_cells.discard(cell)


# ---------------------------------------------------------------------------
# Public API

def reconstruct(
    word_specs: list[WordSpec],
    n_values: Optional[list[int]] = None,
    limit: int = 4,
) -> list[Grid]:
    """
    Find crossword grids matching *word_specs*.

    Tries grid sizes in *n_values* (must be odd) from smallest to largest,
    stopping after the first size that yields any solution.

    Parameters
    ----------
    word_specs : list[WordSpec]
        Complete word list.  Direction must be 'A' or 'D'.
    n_values : list[int], optional
        Grid sizes to attempt.  Defaults to a range starting at the minimum
        size implied by the longest word, stepping by 2 up to +8.
    limit : int
        Maximum number of grids to return.

    Returns
    -------
    list[Grid]
        All valid grids found (empty if the specs are inconsistent).
    """
    specs = sorted(word_specs, key=lambda w: (w.index, w.direction))

    max_a = max((w.length for w in specs if w.direction == 'A'), default=3)
    max_d = max((w.length for w in specs if w.direction == 'D'), default=3)
    min_n = max(max_a, max_d)
    if min_n % 2 == 0:
        min_n += 1

    if n_values is None:
        n_values = list(range(min_n, min_n + 10, 2))

    distinct_after = _precompute_distinct_after(specs)
    spec_counts: Counter = Counter((s.direction, s.length) for s in specs)

    results: list[Grid] = []
    for n in n_values:
        if n < min_n:
            continue
        _backtrack(specs, 0, Grid(n), -1, set(), distinct_after, spec_counts, n, results, limit)
        if results:
            break  # return smallest-N solutions first

    return results


# ---------------------------------------------------------------------------
# Helpers

def parse_clue_length(clue_text: str) -> Optional[int]:
    """
    Extract total letter count from the parenthetical at the end of a clue.
    '(7)' → 7,  '(3,4)' → 7,  '(3-4)' → 7.  Returns None if not found.
    """
    m = re.search(r'\(([0-9][0-9,\-]*)\)\s*$', clue_text.strip())
    if not m:
        return None
    try:
        return sum(int(p) for p in re.split(r'[,\-]', m.group(1)))
    except ValueError:
        return None


def specs_from_puzzle(puzzle) -> list[WordSpec]:
    """
    Extract WordSpecs from a parsed Crossword (vibewords.crossword_model.Crossword).

    Word lengths are measured directly from the grid, so this works even when
    the clue text doesn't contain a length indicator.  Use this to test the
    reconstructor against a known puzzle (extract specs, reconstruct, compare).
    """
    n_rows = puzzle.height
    n_cols = puzzle.width
    specs: list[WordSpec] = []

    def _find_cell(number: int) -> Optional[tuple[int, int]]:
        for r, row in enumerate(puzzle.cells):
            for c, cell in enumerate(row):
                if cell.number == number:
                    return r, c
        return None

    for clue in puzzle.clues_across:
        rc = _find_cell(clue.number)
        if rc is None:
            continue
        r, c = rc
        length = 0
        while c + length < n_cols and not puzzle.cells[r][c + length].black:
            length += 1
        if length >= 3:
            specs.append(WordSpec(index=clue.number, direction='A', length=length))

    for clue in puzzle.clues_down:
        rc = _find_cell(clue.number)
        if rc is None:
            continue
        r, c = rc
        length = 0
        while r + length < n_rows and not puzzle.cells[r + length][c].black:
            length += 1
        if length >= 3:
            specs.append(WordSpec(index=clue.number, direction='D', length=length))

    return specs


# ---------------------------------------------------------------------------
# CLI

def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python -m vibewords.grid_reconstructor <puzzle.ipuz> [N]\n"
            "  python -m vibewords.grid_reconstructor <fifteensquared-url> [N]",
            file=sys.stderr,
        )
        sys.exit(1)

    arg = sys.argv[1]

    if arg.startswith("http://") or arg.startswith("https://"):
        from vibewords.scrapers.fifteensquared import specs_from_fifteensquared
        print(f"Fetching clues from {arg} …")
        specs = specs_from_fifteensquared(arg)
        title = arg
        n_values = [int(sys.argv[2])] if len(sys.argv) > 2 else [15]
    else:
        with open(arg) as f:
            raw = json.load(f)
        from vibewords.ipuz_parser import parse_ipuz
        puzzle = parse_ipuz(raw)
        specs = specs_from_puzzle(puzzle)
        title = puzzle.title
        n_values = [int(sys.argv[2])] if len(sys.argv) > 2 else [puzzle.height]

    print(f"Extracted {len(specs)} word specs from '{title}'")
    for s in sorted(specs, key=lambda w: (w.index, w.direction)):
        print(f"  {s.index}{s.direction}  length={s.length}")

    print(f"\nSearching for grids with N ∈ {n_values} …")

    import time
    t0 = time.perf_counter()
    grids = reconstruct(specs, n_values=n_values)
    elapsed = time.perf_counter() - t0

    if not grids:
        print(f"No valid grid found ({elapsed:.2f}s).")
    else:
        print(f"Found {len(grids)} grid(s) in {elapsed:.2f}s.\n")
        for i, g in enumerate(grids, 1):
            print(f"─── Grid {i} ───")
            print(g)
            print()


if __name__ == "__main__":
    main()
