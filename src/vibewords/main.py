import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace as dc_replace
from datetime import date
from pathlib import Path
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vibewords.config import load_config
from vibewords.connectors import Connector
from vibewords.ipuz_parser import parse_ipuz, to_ipuz
from vibewords.crossword_model import Crossword
from vibewords.scrapers import Scraper
from vibewords.scrapers.guardian import GuardianScraper
from vibewords.scrapers.guardian import ScraperError as _GuardianScraperError
from vibewords.scrapers.independent import IndependentScraper
from vibewords.scrapers.independent import ScraperError as _IndependentScraperError
from vibewords.scrapers.local import LocalConnector

ScraperError = (_GuardianScraperError, _IndependentScraperError)

cfg = load_config()



_ALL_CONNECTORS_LIST: list[Connector] = [
    GuardianScraper("cryptic", weekly_rate=6, schedule="Mon–Sat"),
    GuardianScraper("quiptic", weekly_rate=1, schedule="Sundays"),
    GuardianScraper("quick", weekly_rate=6, schedule="Mon–Sat"),
    IndependentScraper(),
]

_CONNECTORS: dict[str, Connector] = {
    c.connector_id: c for c in _ALL_CONNECTORS_LIST
    if c.source in cfg.connectors.enabled
}

if "local" in cfg.connectors.enabled:
    _local_connector = LocalConnector(Path(cfg.connectors.content_dir))
    _CONNECTORS[_local_connector.connector_id] = _local_connector

_log_level_name = cfg.server.log_level
_log_level = getattr(logging, _log_level_name, logging.INFO)

logger = logging.getLogger("vibewords")
logger.propagate = False
logger.setLevel(_log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Borrow uvicorn's handler so our messages use the same format as uvicorn's own logs.
    for handler in logging.getLogger("uvicorn").handlers:
        logger.addHandler(handler)
    logger.info("VibeWords starting | log_level=%s", _log_level_name)
    logger.info("Config:\n%s", cfg)
    yield


app = FastAPI(lifespan=lifespan)

COLORS = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#e91e63"]
ROOM_TTL = cfg.room.ttl_hours * 3600


def _make_short_title(title: str) -> str:
    """Compact display title for narrow UIs: strip boilerplate words."""
    if not title:
        return title
    s = title
    s = re.sub(r'(?i)^the\s+', '', s)
    s = re.sub(r'(?i)\bnumbers?\b\.?\s*(?=\d)', '#', s)
    s = re.sub(r'(?i)\bno\.?\s*(?=\d)', '#', s)
    s = re.sub(r'(?i)\bcrossword\b\s*', '', s)
    s = re.sub(r'\s{2,}', ' ', s).strip()
    return s or title


def _build_clue_maps(puzzle: Crossword):
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
    def __init__(self, puzzle: Crossword):
        self.puzzle = puzzle
        self.name: Optional[str] = None  # custom room name; falls back to puzzle title
        self.grid: Dict[str, str] = {}
        self.pencil_grid: Dict[str, str] = {}
        self.revealed: set = set()
        self.verified_clues: set = set()
        self.revealed_clues: set = set()
        self.clue_fill: Dict[str, str] = {}  # primary clue key -> 'pencil' | 'firm' (absent = not filled)
        self.solutions_url: Optional[str] = None  # None=searching, ""=not found, url=found
        self._cell_to_clue: Dict[str, set] = {}
        self._clue_to_cells: Dict[str, list] = {}
        self.clients: Dict[WebSocket, dict] = {}
        self.created_at = time.time()
        self.last_activity = self.created_at
        self._player_count = 0
        self._cell_to_clue, self._clue_to_cells = _build_clue_maps(puzzle)

    def _clue_state(self, clue_key: str) -> Optional[str]:
        """Fill state of a clue: 'firm' (all confirmed), 'pencil' (filled, >=1 pencil),
        or None (not all cells filled)."""
        cells = self._clue_to_cells.get(clue_key, [])
        if not cells:
            return None
        has_pencil = False
        for ck in cells:
            if ck in self.grid:
                continue
            if ck in self.pencil_grid:
                has_pencil = True
            else:
                return None  # an empty cell -> not filled
        return 'pencil' if has_pencil else 'firm'

    def recompute_clue_fill(self, clue_keys) -> dict:
        """Update self.clue_fill for the given keys; return {key: state|'none'} delta."""
        delta = {}
        for key in clue_keys:
            state = self._clue_state(key)
            if state != self.clue_fill.get(key):
                if state is None:
                    self.clue_fill.pop(key, None)
                    delta[key] = 'none'
                else:
                    self.clue_fill[key] = state
                    delta[key] = state
        return delta

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
            "short_title": _make_short_title(self.puzzle.title or ''),
            "author": self.puzzle.author,
            "date": self.puzzle.date,
            "source_url": self.puzzle.source_url,
            "width": self.puzzle.width,
            "height": self.puzzle.height,
            "cells": [
                [{"black": cell.black, "number": cell.number} for cell in row]
                for row in self.puzzle.cells
            ],
            "clues": {
                "across": [{"number": c.number, "label": c.label or str(c.number), "text": c.text, "answer": c.answer} for c in self.puzzle.clues_across],
                "down": [{"number": c.number, "label": c.label or str(c.number), "text": c.text, "answer": c.answer} for c in self.puzzle.clues_down],
            },
        }
        if self.puzzle.solution:
            d["solution"] = self.puzzle.solution
        if self.puzzle.links:
            d["links"] = self.puzzle.links
        d["solutions_url"] = self.solutions_url
        d["solutions_eligible"] = _fifteensquared_eligible(self.puzzle)
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


def _fetch_fifteensquared_url(source_url: str) -> str:
    """Search FifteenSquared for a solutions post matching this Guardian puzzle.

    FifteenSquared post URLs cannot be constructed directly: the slug includes the
    setter name (e.g. "guardian-30005-pavo") which is not in the Guardian puzzle
    URL, and title formatting varies across posts (commas in numbers, type prefix
    present or absent, separator characters differ).  This is therefore best-effort:
    we search the WordPress REST API and confirm by puzzle number in the slug.
    Returns the post URL, or '' if not found or on any error.
    """
    m = re.search(r"theguardian\.com/crosswords/([a-z-]+)/(\d+)", source_url)
    if not m:
        return ""
    crossword_type, puzzle_number = m.group(1), m.group(2)

    # FifteenSquared titles use thousands-separator commas ("Guardian 30,005 / Pavo"),
    # but the slug always uses the plain number ("guardian-30005-pavo"). Search with
    # the formatted number so WordPress matches the title; confirm via the slug.
    try:
        formatted_number = f"{int(puzzle_number):,}"
    except ValueError:
        formatted_number = puzzle_number

    def _search(query: str) -> str:
        # At most 2 attempts: retry once on transient network errors only.
        api_url = (
            f"https://www.fifteensquared.net/wp-json/wp/v2/posts"
            f"?search={quote(query)}&per_page=5&_fields=link,title,slug"
        )
        req = Request(api_url, headers={"User-Agent": "vibewords/0.1 (+personal use)"})
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                with urlopen(req, timeout=5) as resp:
                    posts = json.loads(resp.read())
                for post in posts:
                    if puzzle_number in post.get("slug", ""):
                        return post["link"]
                return ""
            except HTTPError:
                raise
            except (URLError, OSError, TimeoutError) as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(1)
        raise last_exc  # type: ignore[misc]

    try:
        url = _search(f"guardian {crossword_type} {formatted_number}")
        if not url:
            url = _search(f"guardian {formatted_number}")
        if not url:
            url = _search(f"guardian {puzzle_number}")
        if url:
            logger.debug("FifteenSquared match for %s/%s: %s", crossword_type, puzzle_number, url)
        return url
    except Exception as exc:
        logger.debug("FifteenSquared lookup failed for %s: %s", source_url, exc)
    return ""


def prune_rooms():
    now = time.time()
    stale = [rid for rid, room in rooms.items() if now - room.last_activity > ROOM_TTL]
    for rid in stale:
        del rooms[rid]
    if stale:
        logger.info("Pruned %d stale room(s) | active=%d", len(stale), len(rooms))


def _fifteensquared_eligible(puzzle: Crossword) -> bool:
    publisher_ok = any(p in puzzle.publisher.lower() for p in ('guardian', 'independent'))
    title_ok = 'cryptic' in puzzle.title.lower()
    return publisher_ok and title_ok


async def _fifteensquared_background(room_id: str):
    """Fetch a FifteenSquared solutions URL in the background and broadcast the result."""
    room = rooms.get(room_id)
    if not room:
        return
    if not room.puzzle.source_url or not _fifteensquared_eligible(room.puzzle):
        return
    url = await asyncio.get_event_loop().run_in_executor(
        None, _fetch_fifteensquared_url, room.puzzle.source_url
    )
    if room_id not in rooms:
        return  # room was pruned while we were waiting
    room.solutions_url = url  # "" if not found, url string if found
    await room.broadcast({"type": "solutions_url", "url": url})


def _make_room(puzzle: Crossword, source: str = "upload") -> str:
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
    room.recompute_clue_fill(room._clue_to_cells.keys())  # seed fill state from saved letters
    rooms[room_id] = room
    logger.info(
        "Room %s created | source=%s | title=%r | size=%dx%d | saved_cells=%d",
        room_id, source, puzzle.title or "Untitled",
        puzzle.width, puzzle.height, saved_count,
    )
    return room_id


# ── Room creation: file upload ─────────────────────────────────────────────

@app.post("/api/rooms")
async def create_room(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    if "ipuz" not in cfg.connectors.enabled:
        raise HTTPException(status_code=404, detail="IPUZ upload is not enabled")
    prune_rooms()
    content = await file.read()
    try:
        puzzle = parse_ipuz(content)
    except Exception as e:
        logger.warning("Failed to parse uploaded file %r: %s", file.filename, e)
        raise HTTPException(status_code=400, detail=f"Could not parse ipuz file: {e}")
    room_id = _make_room(puzzle, source=f"file:{file.filename}")
    background_tasks.add_task(_fifteensquared_background, room_id)
    return {"room_id": room_id}


# ── Client config ─────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    return {
        "hold_delay_ms": cfg.ui.hold_delay_ms,
        "hold_drift_px": cfg.ui.hold_drift_px,
        "ipuz_enabled": "ipuz" in cfg.connectors.enabled,
    }


# ── Connectors metadata ────────────────────────────────────────────────────

@app.get("/api/scrapers")
def list_scrapers():
    return [
        {
            "id": c.connector_id,
            "source": c.source,
            "source_name": c.source_name,
            "name": c.name,
            "schedule": c.schedule,
            "supports_url": getattr(c, "supports_url", False),
        }
        for c in _CONNECTORS.values()
    ]


# ── Room creation: by date ─────────────────────────────────────────────────

class RoomFromDate(BaseModel):
    scraper: str
    date: str  # ISO format YYYY-MM-DD


@app.post("/api/rooms/date")
def create_room_from_date(
    body: RoomFromDate,
    background_tasks: BackgroundTasks,
):
    prune_rooms()
    entry = _CONNECTORS.get(body.scraper)
    if entry is None or not isinstance(entry, Scraper):
        raise HTTPException(status_code=400, detail=f"Unknown scraper: {body.scraper!r}")

    try:
        puzzle_date = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {body.date!r}")

    logger.info("Fetching %s %s crossword for %s", entry.source_name, entry.name, puzzle_date)
    try:
        ipuz_data = entry.fetch_for_date(puzzle_date)
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

    room_id = _make_room(puzzle, source=body.scraper)
    background_tasks.add_task(_fifteensquared_background, room_id)
    return {"room_id": room_id}


# ── Room creation: by URL ──────────────────────────────────────────────────

class RoomFromUrl(BaseModel):
    scraper: str
    url: str


@app.post("/api/rooms/url")
def create_room_from_url(
    body: RoomFromUrl,
    background_tasks: BackgroundTasks,
):
    prune_rooms()
    entry = _CONNECTORS.get(body.scraper)
    if entry is None or not isinstance(entry, Scraper):
        raise HTTPException(status_code=400, detail=f"Unknown scraper: {body.scraper!r}")
    if not entry.supports_url:
        raise HTTPException(status_code=400, detail=f"{entry.name} does not support URL-based fetching")

    logger.info("Fetching crossword from %s via %s %s", body.url, entry.source_name, entry.name)
    try:
        ipuz_data = entry.fetch_by_url(body.url)
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

    room_id = _make_room(puzzle, source=body.scraper)
    background_tasks.add_task(_fifteensquared_background, room_id)
    return {"room_id": room_id}


# ── Room creation: local connector ────────────────────────────────────────

class RoomFromLocal(BaseModel):
    path: str


@app.get("/api/local-puzzles")
def list_local_puzzles():
    conn = _CONNECTORS.get("local")
    if not isinstance(conn, LocalConnector):
        raise HTTPException(status_code=404, detail="Local connector is not enabled")
    return conn.list_puzzles()


@app.post("/api/rooms/local")
def create_room_from_local(
    body: RoomFromLocal,
    background_tasks: BackgroundTasks,
):
    conn = _CONNECTORS.get("local")
    if not isinstance(conn, LocalConnector):
        raise HTTPException(status_code=404, detail="Local connector is not enabled")
    prune_rooms()
    try:
        ipuz_data = conn.fetch_by_path(body.path)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to load local puzzle %r: %s", body.path, e)
        raise HTTPException(status_code=500, detail=f"Could not load puzzle: {e}")
    try:
        puzzle = parse_ipuz(ipuz_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse puzzle: {e}")
    room_id = _make_room(puzzle, source=f"local:{body.path}")
    background_tasks.add_task(_fifteensquared_background, room_id)
    return {"room_id": room_id}


# ── Room list ──────────────────────────────────────────────────────────────

@app.get("/api/rooms")
async def list_rooms():
    prune_rooms()
    return [
        {
            "room_id": room_id,
            "title": room.name or room.puzzle.title or "Untitled",
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


# ── Export ─────────────────────────────────────────────────────────────────

@app.get("/api/rooms/{room_id}/export")
async def export_room(room_id: str):
    if room_id not in rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    room = rooms[room_id]
    saved = [
        [
            room.grid.get(f"{r},{c}") or room.pencil_grid.get(f"{r},{c}") or ""
            for c in range(room.puzzle.width)
        ]
        for r in range(room.puzzle.height)
    ]
    content = to_ipuz(dc_replace(room.puzzle, saved=saved))
    filename = re.sub(r"[^\w\-.]", "_", room.puzzle.title or "crossword") + ".ipuz"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        "room_name": room.name,
        "puzzle": room.puzzle_dict(),
        "grid": room.grid,
        "pencil_grid": room.pencil_grid,
        "revealed": list(room.revealed),
        "verified_clues": list(room.verified_clues),
        "revealed_clues": list(room.revealed_clues),
        "clue_fill": room.clue_fill,
        "users": room.users_list(),
        "room_created_at": room.created_at,
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
                affected = room._cell_to_clue.get(key, set())
                for clue_key in affected:
                    room.verified_clues.discard(clue_key)
                fill_delta = room.recompute_clue_fill(affected)
                await room.broadcast(
                    {"type": "cell_update", "row": row, "col": col, "value": value,
                     "pencil": pencil, "revealed": revealed, "user_id": user_id},
                    exclude=websocket,
                )
                if fill_delta:
                    await room.broadcast({"type": "clue_fill", "states": fill_delta})

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

            elif msg_type == "rename_room":
                old_name = room.name
                new_name = str(data.get("name", "")).strip()[:60]
                room.name = new_name or None
                logger.info("[%s] %s renamed room: %r → %r", room_id, user_id, old_name, room.name)
                await room.broadcast({"type": "room_renamed", "name": room.name or ""})

            elif msg_type == "word_revealed":
                key = str(data.get("key", ""))
                if re.fullmatch(r'[ad]-\d+', key):
                    room.revealed_clues.add(key)
                    logger.debug("[%s] %s clue %s revealed", room_id, user_id, key)
                    await room.broadcast(
                        {"type": "clue_revealed", "key": key},
                        exclude=websocket,
                    )

            elif msg_type == "word_unrevealed":
                key = str(data.get("key", ""))
                if re.fullmatch(r'[ad]-\d+', key):
                    room.revealed_clues.discard(key)
                    logger.debug("[%s] %s clue %s unrevealed", room_id, user_id, key)
                    await room.broadcast(
                        {"type": "clue_unrevealed", "key": key},
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
