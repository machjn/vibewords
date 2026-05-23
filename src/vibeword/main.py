import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vibeword.ipuz_parser import Puzzle, parse_ipuz
from vibeword.scrapers.guardian import ScraperError, fetch_crossword_data, convert, normalise_url

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


class Room:
    def __init__(self, puzzle: Puzzle):
        self.puzzle = puzzle
        self.grid: Dict[str, str] = {}
        self.pencil_grid: Dict[str, str] = {}
        self.revealed: set = set()
        self.clients: Dict[WebSocket, dict] = {}
        self.last_activity = time.time()
        self._color_index = 0
        self._player_count = 0

    def next_color(self) -> str:
        color = COLORS[self._color_index % len(COLORS)]
        self._color_index += 1
        return color

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
                "across": [{"number": c.number, "text": c.text} for c in self.puzzle.clues_across],
                "down": [{"number": c.number, "text": c.text} for c in self.puzzle.clues_down],
            },
        }
        if self.puzzle.solution:
            d["solution"] = self.puzzle.solution
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


# ── Room creation: URL ─────────────────────────────────────────────────────

class RoomFromUrl(BaseModel):
    url: str


@app.post("/api/rooms/url")
def create_room_from_url(body: RoomFromUrl):
    prune_rooms()
    try:
        url = normalise_url(body.url)
    except ScraperError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("Fetching crossword from %s", url)
    try:
        data = fetch_crossword_data(url)
        ipuz_data = convert(data, origin=url, include_solutions=True)
    except ScraperError as e:
        logger.warning("Scrape failed for %s: %s", url, e)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("Unexpected error fetching %s: %s", url, e)
        raise HTTPException(status_code=502, detail=f"Failed to fetch crossword: {e}")

    try:
        puzzle = parse_ipuz(ipuz_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse fetched puzzle: {e}")

    return {"room_id": _make_room(puzzle, source="guardian")}


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
    color   = stored_color if re.fullmatch(r"#[0-9a-fA-F]{6}", stored_color)    else room.next_color()
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

    except WebSocketDisconnect:
        pass
    finally:
        name = room.clients.get(websocket, {}).get("name", user_id)
        room.clients.pop(websocket, None)
        logger.info("[%s] %s (%s) disconnected | players=%d", room_id, name, user_id, len(room.clients))
        await room.broadcast({"type": "user_left", "user_id": user_id})


app.mount("/", StaticFiles(directory="static", html=True), name="static")
