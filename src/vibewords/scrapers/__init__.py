"""Crossword scraper interface.

Adding a new scraper
--------------------
1. Create a module in this package (e.g. `telegraph.py`).
2. Implement module-level helpers (fetch, parse, convert) as needed.
3. Define a class inheriting from `Scraper` and implement:
     - `name` property       →  human-readable publication name
     - `fetch_for_date(d)`   →  fetch a specific date's puzzle as an IPUZ dict
   Optionally override `default_output_name` for custom filenames.
   `fetch_today()` is provided for free as `fetch_for_date(date.today())`.
4. Re-export the class from this `__init__.py` if you want it auto-discoverable.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date
from typing import Any


class Scraper(ABC):
    """Common interface for all crossword scrapers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable publication name, e.g. 'Guardian Cryptic'."""

    @abstractmethod
    def fetch_for_date(self, puzzle_date: date) -> dict[str, Any]:
        """Fetch the crossword for a specific date and return an IPUZ dict."""

    def fetch_today(self) -> dict[str, Any]:
        """Fetch today's crossword. Delegates to fetch_for_date."""
        return self.fetch_for_date(date.today())

    def default_output_name(self, ipuz: dict[str, Any]) -> str:
        """Suggest a filename for the puzzle (can be overridden)."""
        slug = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
        return f"{slug}_{ipuz.get('date', 'unknown')}.ipuz"
