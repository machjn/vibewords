# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (Python 3.11+ required)
pip install -e .

# Run dev server with live reload
uvicorn vibewords.main:app --reload

# Run with debug logging
LOG_LEVEL=DEBUG uvicorn vibewords.main:app --reload
```

There is no frontend build step — `static/` is served directly.

There are no tests currently.

## Architecture

**VibeWords** is a real-time collaborative crossword solver. Players share a room, see each other's cursors, and solve together.

### Backend (`src/vibewords/`)

- **`main.py`** — The entire server: FastAPI app, REST API, WebSocket handler, and all room logic. `Room` objects hold all shared state (grid letters, pencil grid, revealed cells, verified clues, connected WebSocket clients). Rooms are in-process dicts (`rooms: Dict[str, Room]`) with a TTL — **state is not persisted**. Single-instance deployment is required so all clients share the same room objects.

- **`ipuz_parser.py`** — Parses `.ipuz` JSON into `Puzzle`, `Cell`, and `Clue` dataclasses. Also handles the `saved` field (previously entered letters restored into the grid on room creation).

- **`config.py`** — Config is loaded from `config.yaml` (or the path in `VIBEWORDS_CONFIG`), then overridden by env vars of the form `VIBEWORDS_<SECTION>_<KEY>` (e.g. `VIBEWORDS_UI_HOLD_DELAY_MS`). Sections: `server`, `room`, `ui`.

- **`scrapers/`** — `guardian.py` and `independent.py` fetch puzzles by date or URL and return raw ipuz bytes. `fifteensquared.py` parses blog posts to extract clue/length data for grid reconstruction. `grid_reconstructor.py` infers a grid layout from `WordSpec` objects (index, direction, length).

### WebSocket protocol

On join, the server sends a `sync` message with the full puzzle, current grid state, and user list. After that, clients send typed messages and the server broadcasts updates:

| Client → Server | Server → Clients |
|---|---|
| `cell_update` | `cell_update` |
| `cursor_move` | `cursor_move` |
| `pointer_move` / `pointer_clear` | same |
| `rename` | `renamed` |
| `rename_room` | `room_renamed` (broadcast to all) |
| `word_correct` | `clue_verified` (if valid) |

Clue verification uses `_build_clue_maps()` in `main.py`, which pre-computes two lookup tables: `cell_to_clue` (for fast invalidation on cell edits) and `clue_to_cells` (for validating a `word_correct` claim against the solution). Composite/linked clues (e.g. "25 and 11 down") are collapsed to a single primary key (`a-25`) covering all cells in the chain.

### Frontend (`static/`)

Plain JS with no framework or build tooling. `app.js` runs the room page; `IS_COARSE` (`window.matchMedia('(pointer: coarse)')`) gates mobile-specific behaviour. On coarse-pointer devices, a radial letter-picker wheel is used instead of the native keyboard. Identity (user ID and name) is persisted in `localStorage`.

### Deployment

GitHub Actions builds a Docker image on push to `main` and deploys to GCP Cloud Run (`europe-west2`). The Cloud Run service runs with `--min-instances=1` (room state must not be lost between requests) and `--timeout 3600` (WebSocket connections are long-lived HTTP requests). If multiple instances are ever needed, `--session-affinity` must be added.

## Development

All commands should be run inside a python virtualenv
