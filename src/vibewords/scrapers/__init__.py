"""Crossword scraper interface.

Adding a new scraper
--------------------
1. Create a module in this package (e.g. `telegraph.py`).
2. Implement module-level helpers (fetch, parse, convert) as needed.
3. Define a class inheriting from `Scraper` and implement:
     - `connector_id` property  →  unique key, e.g. 'guardian_cryptic'
     - `source` property        →  source group, e.g. 'guardian'
     - `source_name` property   →  display name for source, e.g. 'Guardian'
     - `name` property          →  display name for this type, e.g. 'Cryptic'
     - `fetch_for_date(d)`      →  fetch a specific date's puzzle as an IPUZ dict
   Optionally override `schedule`, `supports_url`, `fetch_by_url`, and
   `default_output_name`.  `fetch_today()` is provided for free.
4. Re-export the class from this `__init__.py` if you want it auto-discoverable.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from vibewords.connectors import Connector


class Scraper(Connector, ABC):
    """Connector that fetches puzzles from a remote source."""

    @abstractmethod
    def fetch_for_date(self, puzzle_date: date) -> dict[str, Any]:
        """Fetch the crossword for a specific date and return an IPUZ dict."""

    def fetch_today(self) -> dict[str, Any]:
        """Fetch today's crossword. Delegates to fetch_for_date."""
        return self.fetch_for_date(date.today())

    @property
    def supports_url(self) -> bool:
        """Override and return True in scrapers that implement fetch_by_url."""
        return False

    def fetch_by_url(self, url: str) -> dict[str, Any]:
        raise NotImplementedError(f"{self.__class__.__name__} does not support URL fetching")

    def default_output_name(self, ipuz: dict[str, Any]) -> str:
        """Suggest a filename for the puzzle (can be overridden)."""
        slug = re.sub(r"[^a-z0-9]+", "_", f"{self.source_name} {self.name}".lower()).strip("_")
        return f"{slug}_{ipuz.get('date', 'unknown')}.ipuz"
