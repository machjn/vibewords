import json
import logging
from pathlib import Path
from typing import Any

from vibewords.connectors import Connector

logger = logging.getLogger("vibewords")


class LocalConnector(Connector):
    """Connector that serves IPUZ files from a local content directory."""

    def __init__(self, content_dir: Path):
        self._content_dir = content_dir

    @property
    def connector_id(self) -> str:
        return "local"

    @property
    def source(self) -> str:
        return "local"

    @property
    def source_name(self) -> str:
        return "Local"

    @property
    def name(self) -> str:
        return "Local Library"

    def list_puzzles(self) -> list[dict[str, Any]]:
        if not self._content_dir.exists():
            logger.info("Local content directory %s does not exist", self._content_dir)
            return []

        puzzles = []
        for ipuz_file in sorted(self._content_dir.rglob("*.ipuz")):
            try:
                data = json.loads(ipuz_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Skipping unreadable puzzle %s: %s", ipuz_file, e)
                continue

            rel = ipuz_file.relative_to(self._content_dir)
            parts = rel.parts
            category = parts[0] if len(parts) > 1 else None
            puzzles.append({
                "id": rel.as_posix(),
                "title": data.get("title") or ipuz_file.stem,
                "category": category,
                "date": data.get("date"),
                "author": data.get("author"),
                "publisher": data.get("publisher"),
            })

        return puzzles

    def fetch_by_path(self, relative_path: str) -> dict[str, Any]:
        clean = relative_path.lstrip("/")
        resolved = (self._content_dir / clean).resolve()

        if not resolved.is_relative_to(self._content_dir.resolve()):
            raise ValueError(f"Path outside content directory: {relative_path!r}")

        if resolved.suffix.lower() != ".ipuz":
            raise ValueError(f"Not an IPUZ file: {relative_path!r}")

        if not resolved.is_file():
            raise FileNotFoundError(f"Puzzle not found: {relative_path!r}")

        return json.loads(resolved.read_text(encoding="utf-8"))
