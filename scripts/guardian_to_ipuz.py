#!/usr/bin/env python3
"""Download a Guardian crossword page and convert it to IPUZ.

Usage:
  python scripts/guardian_to_ipuz.py https://www.theguardian.com/crosswords/cryptic/30013
  python scripts/guardian_to_ipuz.py cryptic/30013 -o brockwell.ipuz
  python scripts/guardian_to_ipuz.py 30013 --type cryptic --no-solutions
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vibeword.scrapers.guardian import (
    ScraperError,
    convert,
    default_output_name,
    fetch_crossword_data,
    normalise_url,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a Guardian crossword to IPUZ.")
    parser.add_argument("puzzle", help="Guardian URL, crossword path (e.g. cryptic/30013), or bare number")
    parser.add_argument("-o", "--output", help="Output .ipuz path")
    parser.add_argument("--type", default="cryptic", help="Crossword type when only a number is given (default: cryptic)")
    parser.add_argument("--no-solutions", action="store_true", help="Write an unsolved IPUZ without the solution grid")
    args = parser.parse_args(argv)

    include_solutions = not args.no_solutions
    try:
        url = normalise_url(args.puzzle, args.type)
        data = fetch_crossword_data(url)
        ipuz = convert(data, origin=url, include_solutions=include_solutions)
    except ScraperError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    output = Path(args.output or default_output_name(data, include_solutions))
    output.write_text(json.dumps(ipuz, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
