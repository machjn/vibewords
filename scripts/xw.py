#!/usr/bin/env python3
"""Thin wrapper so scripts/xw.py still works during development."""
from vibewords.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
