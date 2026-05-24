import logging
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vibeword.ipuz_parser import Puzzle, parse_ipuz
from vibeword.scrapers.guardian import GuardianScraper
from vibeword.scrapers.guardian import ScraperError as _GuardianScraperError
from vibeword.scrapers.independent import IndependentScraper
from vibeword.scrapers.independent import ScraperError as _IndependentScraperError

ScraperError = (_GuardianScraperError, _IndependentScraperError)

_SCRAPERS = {
    "guardian_cryptic": {
        "instance": GuardianScraper("cryptic", weekly_rate=6),
        "source": "guardian", "source_name": "Guardian",
        "name": "Cryptic", "supports_url": True, "schedule": "Mon–Sat",
    },
    "guardian_quiptic": {
        "instance": GuardianScraper("quiptic", weekly_rate=1),
        "source": "guardian", "source_name": "Guardian",
        "name": "Quiptic", "supports_url": True, "schedule": "Sundays",
    },
    "guardian_quick": {
        "instance": GuardianScraper("quick", weekly_rate=6),
        "source": "guardian", "source_name": "Guardian",
        "name": "Quick", "supports_url": True, "schedule": "Mon–Sat",
    },
    "independent_cryptic": {
        "instance": IndependentScraper(),
        "source": "independent", "source_name": "Independent",
        "name": "Cryptic", "supports_url": False, "schedule": "Daily",
    },
}

_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)

logger = logging.getLogger("vibeword")
logger.propagate = False
logger.setLevel(_log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Borrow uvicorn's handler so our messages use the same format as uvicorn's own logs.
    for handler in logging.getLogger("uvicorn").handlers:
        logger.addHandler(handler)
    logger.info("VibeWord starting | log_level=%s", _log_level_name)
    yield


app = FastAPI(lifespan=lifespan)

COLORS = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#e91e63"]
ROOM_TTL = 60 * 60 * 6  # 6 hours


def _build_clue_maps(puzzle: Puzzle):
    """Build two lookup tables used to keep verified_clues consistent.

    cell_to_clue_keys  ("r,c" -> set of clue keys)
        Used on every cell_update to instantly find which verified clues must
        be invalidated.  Without this we'd have to scan all clues on every
        keystroke.

    clue_to_cells  (clue_key -> ordered list of "r,c" keys)
        Used when a client sends word_correct to validate the claim: every
        cell listed here must match the solution before we accept it.

    Clue keys use the format "a-<n>" (across) or "d-<n>" (down), matching the
    DOM element IDs on the frontend (e.g. clue-a-5).

    Composite / linked clues (e.g. "25 and 11") are handled by registering all
    cell runs in the chain under the PRIMARY key only ("a-25").  This means
    editing any cell in a continuation run (clue 11 down) correctly invalidates
    the primary answer, and the frontend's chain-walking logic can restore
    highlighting for all segments from that single key.
    """
    cells = puzzle.cells
    height, width = puzzle.height, puzzle.width
    links = puzzle.links or {}

    num_to_pos: Dict[int, tuple] = {}
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            if cell.number:
                num_to_pos[cell.number] = (r, c)

    def run_cells_from(r: int, c: int, direction: str):
        result = []
        if direction == 'across':
            cc = c
            while cc < width and not cells[r][cc].black:
                result.append((r, cc)); cc += 1
        else:
            rr = r
            while rr < height and not cells[rr][c].black:
                result.append((rr, c)); rr += 1
        return result

    def chain_entries(chain, fallback_dir: str):
        # Normalise both chain formats to (clue_num, direction) pairs.
        # Legacy format: [25, 11]            — all segments share fallback_dir.
        # Tagged format: [[25,"Across"],[11,"Down"]] — each segment names its dir.
        out = []
        for entry in chain:
            if isinstance(entry, list) and len(entry) >= 2:
                out.append((int(entry[0]), 'across' if entry[1] == 'Across' else 'down'))
            else:
                out.append((int(entry), fallback_dir))
        return out

    # Identify continuation clues (non-head segments of any chain) so they are
    # not registered as independent heads.  Their cells will be captured when
    # the chain head is processed.
    continuation_clues: set = set()
    for dir_key, dir_chains in links.items():
        if not isinstance(dir_chains, dict):
            continue
        fallback = 'across' if dir_key == 'Across' else 'down'
        for head_str, chain in dir_chains.items():
            if not isinstance(chain, list):
                continue
            for seg_num, seg_dir in chain_entries(chain, fallback)[1:]:
                continuation_clues.add((seg_num, seg_dir))

    cell_to_clue: Dict[str, set] = {}
    clue_to_cells: Dict[str, list] = {}

    def register_head(clue_num: int, direction: str):
        # Walk every segment in this clue's chain (just [clue_num] for simple
        # clues) and register all cells under a single primary key.
        dir_key = 'Across' if direction == 'across' else 'Down'
        chain_raw = (links.get(dir_key) or {}).get(str(clue_num), [clue_num])
        primary_key = f"{'a' if direction == 'across' else 'd'}-{clue_num}"
        cell_list = []
        for seg_num, seg_dir in chain_entries(chain_raw, direction):
            pos = num_to_pos.get(seg_num)
            if pos is None:
                continue
            for r, c in run_cells_from(pos[0], pos[1], seg_dir):
                ckey = f"{r},{c}"
                cell_to_clue.setdefault(ckey, set()).add(primary_key)
                cell_list.append(ckey)
        if cell_list:
            clue_to_cells[primary_key] = cell_list

    for clue in puzzle.clues_across:
        if (clue.number, 'across') not in continuation_clues:
            register_head(clue.number, 'across')
    for clue in puzzle.clues_down:
        if (clue.number, 'down') not in continuation_clues:
            register_head(clue.number, 'down')

    return cell_to_clue, clue_to_cells


class Room:
    def __init__(self, puzzle: Puzzle):
        self.puzzle = puzzle
        self.grid: Dict[str, str] = {}
        self.pencil_grid: Dict[str, str] = {}
        self.revealed: set = set()
        self.verified_clues: set = set()
        self._cell_to_clue: Dict[str, set] = {}
        self._clue_to_cells: Dict[str, list] = {}
        self.clients: Dict[WebSocket, dict] = {}
        self.last_activity = time.time()
        self._player_count = 0
        self._cell_to_clue, self._clue_to_cells = _build_clue_maps(puzzle)

    def next_color(self) -> str:
        used = {info["color"] for info in self.clients.values()}
        available = [c for c in COLORS if c not in used]
        if available:
            return random.choice(available)
        # All 8 colours taken — pick randomly anyway (>8 players)
        return random.choice(COLORS)

    def next_player_name(self) -> str:
        self._player_count += 1
        return f"Player {self._player_count}"

    def puzzle_dict(self) -> dict:
        d: dict = {
            "title": self.puzzle.title,
            "author": self.puzzle.author,
            "width": self.puzzle.width,
            "height": self.puzzle.height,
            "cells": [
                [{"black": cell.black, "number": cell.number} for cell in row]
                for row in self.puzzle.cells
            ],
            "clues": {
                "across": [{"number": c.number, "label": c.label or str(c.number), "text": c.text} for c in self.puzzle.clues_across],
                "down": [{"number": c.number, "label": c.label or str(c.number), "text": c.text} for c in self.puzzle.clues_down],
            },
        }
        if self.puzzle.solution:
            d["solution"] = self.puzzle.solution
        if self.puzzle.links:
            d["links"] = self.puzzle.links
        return d

    def users_list(self) -> list:
        return [
            {"user_id": info["user_id"], "color": info["color"],
             "name": info["name"], "cursor": info.get("cursor")}
            for info in self.clients.values()
        ]

    async def broadcast(self, message: dict, exclude: Optional[WebSocket] = None):
        dead = []
        for ws, info in list(self.clients.items()):
            if ws is exclude:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)


rooms: Dict[str, Room] = {}


def prune_rooms():
    now = time.time()
    stale = [rid for rid, room in rooms.items() if now - room.last_activity > ROOM_TTL]
    for rid in stale:
        del rooms[rid]
    if stale:
        logger.info("Pruned %d stale room(s) | active=%d", len(stale), len(rooms))


def _make_room(puzzle: Puzzle, source: str = "upload") -> str:
    if not puzzle.cells:
        raise HTTPException(status_code=400, detail="Puzzle has no grid data")
    room_id = uuid.uuid4().hex[:8]
    room = Room(puzzle)
    saved_count = 0
    if puzzle.saved:
        for r, row in enumerate(puzzle.saved):
            for c, letter in enumerate(row):
                if letter:
                    room.grid[f"{r},{c}"] = letter
                    saved_count += 1
    rooms[room_id] = room
    logger.info(
        "Room %s created | source=%s | title=%r | size=%dx%d | saved_cells=%d",
        room_id, source, puzzle.title or "Untitled",
        puzzle.width, puzzle.height, saved_count,
    )
    return room_id


# ── Room creation: file upload ─────────────────────────────────────────────

@app.post("/api/rooms")
async def create_room(file: UploadFile = File(...)):
    prune_rooms()
    content = await file.read()
    try:
        puzzle = parse_ipuz(content)
    except Exception as e:
        logger.warning("Failed to parse uploaded file %r: %s", file.filename, e)
        raise HTTPException(status_code=400, detail=f"Could not parse ipuz file: {e}")
    return {"room_id": _make_room(puzzle, source=f"file:{file.filename}")}


# ── Scrapers metadata ──────────────────────────────────────────────────────

@app.get("/api/scrapers")
def list_scrapers():
    return [
        {
            "id": k,
            "source": v["source"], "source_name": v["source_name"],
            "name": v["name"], "supports_url": v["supports_url"],
            "schedule": v["schedule"],
        }
        for k, v in _SCRAPERS.items()
    ]


# ── Room creation: by date ─────────────────────────────────────────────────

class RoomFromDate(BaseModel):
    scraper: str
    date: str  # ISO format YYYY-MM-DD


@app.post("/api/rooms/date")
def create_room_from_date(body: RoomFromDate):
    prune_rooms()
    entry = _SCRAPERS.get(body.scraper)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"Unknown scraper: {body.scraper!r}")

    try:
        puzzle_date = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {body.date!r}")

    scraper = entry["instance"]
    logger.info("Fetching %s crossword for %s", entry["name"], puzzle_date)
    try:
        ipuz_data = scraper.fetch_for_date(puzzle_date)
    except ScraperError as e:
        logger.warning("Scrape failed (%s, %s): %s", body.scraper, puzzle_date, e)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("Unexpected error (%s, %s): %s", body.scraper, puzzle_date, e)
        raise HTTPException(status_code=502, detail=f"Failed to fetch crossword: {e}")

    try:
        puzzle = parse_ipuz(ipuz_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse fetched puzzle: {e}")

    return {"room_id": _make_room(puzzle, source=body.scraper)}


# ── Room creation: by URL ──────────────────────────────────────────────────

class RoomFromUrl(BaseModel):
    scraper: str
    url: str


@app.post("/api/rooms/url")
def create_room_from_url(body: RoomFromUrl):
    prune_rooms()
    entry = _SCRAPERS.get(body.scraper)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"Unknown scraper: {body.scraper!r}")
    if not entry["supports_url"]:
        raise HTTPException(status_code=400, detail=f"{entry['name']} does not support URL-based fetching")

    scraper = entry["instance"]
    logger.info("Fetching crossword from %s via %s", body.url, entry["name"])
    try:
        ipuz_data = scraper.fetch_by_url(body.url)
    except ScraperError as e:
        logger.warning("Scrape failed for %s: %s", body.url, e)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("Unexpected error fetching %s: %s", body.url, e)
        raise HTTPException(status_code=502, detail=f"Failed to fetch crossword: {e}")

    try:
        puzzle = parse_ipuz(ipuz_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse fetched puzzle: {e}")

    return {"room_id": _make_room(puzzle, source=body.scraper)}


# ── Room list ──────────────────────────────────────────────────────────────

@app.get("/api/rooms")
async def list_rooms():
    prune_rooms()
    return [
        {
            "room_id": room_id,
            "title": room.puzzle.title or "Untitled",
            "author": room.puzzle.author or "",
            "width": room.puzzle.width,
            "height": room.puzzle.height,
            "players": [
                {"name": info["name"], "color": info["color"]}
                for info in room.clients.values()
            ],
            "last_activity": room.last_activity,
        }
        for room_id, room in rooms.items()
    ]


@app.get("/rooms")
async def rooms_page():
    return FileResponse("static/rooms.html")


# ── Room page ──────────────────────────────────────────────────────────────

@app.get("/room/{room_id}")
async def room_page(room_id: str):
    if room_id not in rooms:
        return FileResponse("static/404.html", status_code=404)
    return FileResponse("static/room.html")


# ── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    if room_id not in rooms:
        await websocket.close(code=4004)
        return

    room = rooms[room_id]
    await websocket.accept()

    params = websocket.query_params
    stored_id    = params.get("user_id", "")
    stored_color = params.get("color", "")
    stored_name  = params.get("name", "").strip()[:20]

    user_id = stored_id    if re.fullmatch(r"[0-9a-f]{6}", stored_id)           else uuid.uuid4().hex[:6]
    used_colors = {info["color"] for info in room.clients.values()}
    color   = stored_color if re.fullmatch(r"#[0-9a-fA-F]{6}", stored_color) and stored_color not in used_colors else room.next_color()
    name    = stored_name  if stored_name                                         else room.next_player_name()

    room.clients[websocket] = {"user_id": user_id, "color": color, "name": name, "cursor": None}

    logger.info("[%s] %s (%s) joined | players=%d", room_id, name, user_id, len(room.clients))

    await websocket.send_json({
        "type": "sync",
        "user_id": user_id,
        "color": color,
        "name": name,
        "puzzle": room.puzzle_dict(),
        "grid": room.grid,
        "pencil_grid": room.pencil_grid,
        "revealed": list(room.revealed),
        "verified_clues": list(room.verified_clues),
        "users": room.users_list(),
    })
    await room.broadcast(
        {"type": "user_joined", "user_id": user_id, "color": color, "name": name},
        exclude=websocket,
    )

    try:
        while True:
            data = await websocket.receive_json()
            room.last_activity = time.time()
            msg_type = data.get("type")

            if msg_type == "cell_update":
                row, col = int(data["row"]), int(data["col"])
                value = data.get("value", "").upper()[:1]
                pencil = bool(data.get("pencil", False))
                revealed = bool(data.get("revealed", False))
                key = f"{row},{col}"
                if value:
                    if pencil:
                        room.pencil_grid[key] = value
                        room.grid.pop(key, None)
                        room.revealed.discard(key)
                        logger.debug("[%s] %s cell (%d,%d) = %r (pencil)", room_id, user_id, row, col, value)
                    else:
                        room.grid[key] = value
                        room.pencil_grid.pop(key, None)
                        if revealed:
                            room.revealed.add(key)
                            logger.debug("[%s] %s cell (%d,%d) = %r (revealed)", room_id, user_id, row, col, value)
                        else:
                            room.revealed.discard(key)
                            logger.debug("[%s] %s cell (%d,%d) = %r", room_id, user_id, row, col, value)
                else:
                    room.grid.pop(key, None)
                    room.pencil_grid.pop(key, None)
                    room.revealed.discard(key)
                    logger.debug("[%s] %s cell (%d,%d) cleared", room_id, user_id, row, col)
                # Invalidate any verified clues that include this cell.
                for clue_key in room._cell_to_clue.get(key, set()):
                    room.verified_clues.discard(clue_key)
                await room.broadcast(
                    {"type": "cell_update", "row": row, "col": col, "value": value,
                     "pencil": pencil, "revealed": revealed, "user_id": user_id},
                    exclude=websocket,
                )

            elif msg_type == "cursor_move":
                cursor = {"row": data.get("row"), "col": data.get("col"),
                          "direction": data.get("direction", "across")}
                room.clients[websocket]["cursor"] = cursor
                logger.debug(
                    "[%s] %s cursor (%s,%s) %s",
                    room_id, user_id, cursor["row"], cursor["col"], cursor["direction"],
                )
                await room.broadcast(
                    {"type": "cursor_move", "user_id": user_id, "color": color,
                     "name": room.clients[websocket]["name"], **cursor},
                    exclude=websocket,
                )

            elif msg_type == "pointer_move":
                x = float(data.get("x", 0))
                y = float(data.get("y", 0))
                logger.debug("[%s] %s pointer (%.3f, %.3f)", room_id, user_id, x, y)
                await room.broadcast(
                    {"type": "pointer_move", "user_id": user_id, "color": color,
                     "name": room.clients[websocket]["name"], "x": x, "y": y},
                    exclude=websocket,
                )

            elif msg_type == "pointer_clear":
                logger.debug("[%s] %s pointer cleared", room_id, user_id)
                await room.broadcast(
                    {"type": "pointer_clear", "user_id": user_id},
                    exclude=websocket,
                )

            elif msg_type == "rename":
                old_name = room.clients[websocket]["name"]
                new_name = str(data.get("name", "")).strip()[:20]
                if new_name:
                    room.clients[websocket]["name"] = new_name
                    logger.info("[%s] %s renamed: %r → %r", room_id, user_id, old_name, new_name)
                    await room.broadcast(
                        {"type": "renamed", "user_id": user_id, "name": new_name},
                        exclude=websocket,
                    )

            elif msg_type == "word_correct":
                key = str(data.get("key", ""))
                if not re.fullmatch(r'[ad]-\d+', key):
                    pass
                elif key not in room._clue_to_cells:
                    pass
                else:
                    # Validate against solution if available.
                    valid = True
                    if room.puzzle.solution:
                        for ckey in room._clue_to_cells[key]:
                            r2, c2 = map(int, ckey.split(','))
                            sol_row = room.puzzle.solution[r2] if r2 < len(room.puzzle.solution) else []
                            sol_letter = (sol_row[c2] if c2 < len(sol_row) else '').upper()
                            if not sol_letter or sol_letter == '#':
                                continue
                            if room.grid.get(ckey, '') != sol_letter:
                                valid = False
                                break
                    if valid:
                        room.verified_clues.add(key)
                        logger.debug("[%s] %s clue %s verified", room_id, user_id, key)
                        await room.broadcast(
                            {"type": "clue_verified", "key": key},
                            exclude=websocket,
                        )

    except WebSocketDisconnect:
        pass
    finally:
        name = room.clients.get(websocket, {}).get("name", user_id)
        room.clients.pop(websocket, None)
        logger.info("[%s] %s (%s) disconnected | players=%d", room_id, name, user_id, len(room.clients))
        await room.broadcast({"type": "user_left", "user_id": user_id})


app.mount("/", StaticFiles(directory="static", html=True), name="static")
