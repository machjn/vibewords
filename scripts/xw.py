#!/usr/bin/env python3
"""xw — command-line tools for crossword files.

Usage:
  python scripts/xw.py display puzzle.xw
  python scripts/xw.py display puzzle.ipuz
  python scripts/xw.py display a.xw b.ipuz
  python scripts/xw.py export puzzle.xw
  python scripts/xw.py export puzzle.xw -o out.ipuz
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vibewords.crossword_model import Crossword


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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
