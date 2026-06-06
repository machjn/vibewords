#!/usr/bin/env python3
"""xw — command-line tools for crossword files.

Usage:
  python scripts/xw.py display puzzle.xw
  python scripts/xw.py display puzzle.ipuz
  python scripts/xw.py display a.xw b.ipuz
  python scripts/xw.py export puzzle.xw
  python scripts/xw.py export puzzle.xw -o out.ipuz
  python scripts/xw.py scrape guardian cryptic 30013        # → guardian_cryptic_30013.ipuz
  python scripts/xw.py scrape guardian cryptic 2026-06-06   # → guardian_cryptic_2026-06-06.ipuz
  python scripts/xw.py scrape guardian cryptic              # today
  python scripts/xw.py scrape independent cryptic 2026-06-06
  python scripts/xw.py scrape https://www.theguardian.com/crosswords/cryptic/30013
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from vibewords.crossword_model import Crossword
from vibewords.scrapers.guardian import GuardianScraper
from vibewords.scrapers.independent import IndependentScraper

_GUARDIAN_SCRAPERS: dict[str, GuardianScraper] = {
    t: GuardianScraper(t) for t in ("cryptic", "quiptic", "quick")
}


def _load(path: Path) -> Crossword:
    suffix = path.suffix.lower()
    if suffix == ".xw":
        from vibewords.xw_parser import parse_xw
        return parse_xw(path.read_text(encoding="utf-8"))
    elif suffix == ".ipuz":
        import json
        from vibewords.ipuz_parser import parse_ipuz
        return parse_ipuz(json.loads(path.read_text(encoding="utf-8")))
    elif suffix == ".puz":
        from vibewords.puz_parser import parse_puz
        return parse_puz(path.read_bytes())
    else:
        raise ValueError(f"Unrecognised file format: {path.suffix!r} (expected .xw, .ipuz, or .puz)")


def cmd_export(args: argparse.Namespace) -> int:
    from vibewords.ipuz_parser import to_ipuz
    path = Path(args.file)
    try:
        crossword = _load(path)
    except FileNotFoundError:
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {path}: {e}", file=sys.stderr)
        return 1

    out = Path(args.output) if args.output else path.with_suffix(".ipuz")
    out.write_bytes(to_ipuz(crossword))
    print(out)
    return 0


def cmd_display(args: argparse.Namespace) -> int:
    ok = True
    for path_str in args.files:
        path = Path(path_str)
        if len(args.files) > 1:
            print(f"=== {path} ===")
        try:
            print(_load(path))
        except FileNotFoundError:
            print(f"Error: file not found: {path}", file=sys.stderr)
            ok = False
        except Exception as e:
            print(f"Error: {path}: {e}", file=sys.stderr)
            ok = False
        if len(args.files) > 1:
            print()
    return 0 if ok else 1


def cmd_scrape(args: argparse.Namespace) -> int:
    target = args.target
    no_solutions = args.no_solutions

    try:
        if target.startswith("http"):
            # URL mode — Guardian is the only scraper that supports URLs
            scraper = GuardianScraper()
            ipuz = scraper.fetch_by_url(target, include_solutions=not no_solutions)
        else:
            name = target.lower()
            if name == "guardian":
                ctype = args.type
                if ctype not in _GUARDIAN_SCRAPERS:
                    known = ", ".join(_GUARDIAN_SCRAPERS)
                    print(f"Error: Guardian TYPE must be one of: {known}", file=sys.stderr)
                    return 1
                scraper = _GUARDIAN_SCRAPERS[ctype]
                ref = args.ref
                if ref is None:
                    ipuz = scraper.fetch_today()
                elif ref.isdigit():
                    ipuz = scraper.fetch_by_number(int(ref))
                else:
                    ipuz = scraper.fetch_for_date(date.fromisoformat(ref))
            elif name == "independent":
                if args.type != "cryptic":
                    print("Error: Independent TYPE must be: cryptic", file=sys.stderr)
                    return 1
                scraper = IndependentScraper()
                puzzle_date = date.fromisoformat(args.ref) if args.ref else date.today()
                ipuz = scraper.fetch_for_date(puzzle_date)
            else:
                known = "guardian, independent"
                print(f"Error: unknown scraper {name!r}. Known: {known}", file=sys.stderr)
                return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    out = Path(args.output or scraper.default_output_name(ipuz))
    out.write_text(json.dumps(ipuz, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xw", description="Crossword file tools.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_display = sub.add_parser("display", help="Display a crossword puzzle.")
    p_display.add_argument("files", nargs="+", metavar="FILE", help=".xw or .ipuz file(s)")
    p_display.set_defaults(func=cmd_display)

    p_export = sub.add_parser("export", help="Export a crossword to .ipuz.")
    p_export.add_argument("file", metavar="FILE", help="Input .xw or .ipuz file")
    p_export.add_argument("-o", "--output", metavar="OUT", help="Output .ipuz path (default: same name as input)")
    p_export.set_defaults(func=cmd_export)

    p_scrape = sub.add_parser("scrape", help="Fetch a puzzle from an online source and save as .ipuz.")
    p_scrape.add_argument(
        "target", metavar="TARGET",
        help="Scraper name (guardian, independent) or a full URL",
    )
    p_scrape.add_argument(
        "type", nargs="?", metavar="TYPE",
        help="Crossword type (e.g. cryptic, quiptic, quick). Required unless TARGET is a URL.",
    )
    p_scrape.add_argument(
        "ref", nargs="?", metavar="REF",
        help="ISO date (YYYY-MM-DD) or puzzle number; omit for today",
    )
    p_scrape.add_argument("-o", "--output", metavar="OUT", help="Output .ipuz path (default: auto-named)")
    p_scrape.add_argument("--no-solutions", action="store_true", help="Omit solution grid")
    p_scrape.set_defaults(func=cmd_scrape)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
