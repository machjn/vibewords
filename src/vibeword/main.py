import time
import uuid
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vibeword.ipuz_parser import Puzzle, parse_ipuz
from vibeword.scrapers.guardian import ScraperError, fetch_crossword_data, convert, normalise_url

app = FastAPI()

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


def _make_room(puzzle: Puzzle) -> str:
    if not puzzle.cells:
        raise HTTPException(status_code=400, detail="Puzzle has no grid data")
    room_id = uuid.uuid4().hex[:8]
    rooms[room_id] = Room(puzzle)
    return room_id


# ── Room creation: file upload ─────────────────────────────────────────────

@app.post("/api/rooms")
async def create_room(file: UploadFile = File(...)):
    prune_rooms()
    content = await file.read()
    try:
        puzzle = parse_ipuz(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse ipuz file: {e}")
    return {"room_id": _make_room(puzzle)}


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

    try:
        data = fetch_crossword_data(url)
        ipuz_data = convert(data, origin=url, include_solutions=True)
    except ScraperError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch crossword: {e}")

    try:
        puzzle = parse_ipuz(ipuz_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse fetched puzzle: {e}")

    return {"room_id": _make_room(puzzle)}


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

    user_id = uuid.uuid4().hex[:6]
    color = room.next_color()
    name = room.next_player_name()
    room.clients[websocket] = {"user_id": user_id, "color": color, "name": name, "cursor": None}

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
                    else:
                        room.grid[key] = value
                        room.pencil_grid.pop(key, None)
                        if revealed:
                            room.revealed.add(key)
                        else:
                            room.revealed.discard(key)
                else:
                    room.grid.pop(key, None)
                    room.pencil_grid.pop(key, None)
                    room.revealed.discard(key)
                await room.broadcast(
                    {"type": "cell_update", "row": row, "col": col, "value": value,
                     "pencil": pencil, "revealed": revealed, "user_id": user_id},
                    exclude=websocket,
                )

            elif msg_type == "cursor_move":
                cursor = {"row": data.get("row"), "col": data.get("col"),
                          "direction": data.get("direction", "across")}
                room.clients[websocket]["cursor"] = cursor
                await room.broadcast(
                    {"type": "cursor_move", "user_id": user_id, "color": color,
                     "name": room.clients[websocket]["name"], **cursor},
                    exclude=websocket,
                )

            elif msg_type == "rename":
                new_name = str(data.get("name", "")).strip()[:20]
                if new_name:
                    room.clients[websocket]["name"] = new_name
                    await room.broadcast(
                        {"type": "renamed", "user_id": user_id, "name": new_name},
                        exclude=websocket,
                    )

    except WebSocketDisconnect:
        pass
    finally:
        room.clients.pop(websocket, None)
        await room.broadcast({"type": "user_left", "user_id": user_id})


app.mount("/", StaticFiles(directory="static", html=True), name="static")
