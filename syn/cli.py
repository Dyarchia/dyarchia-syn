"""Entry point of ``syn``.

Each command family lives in its own module and exposes
``register(subparsers)``, which adds its subcommands and binds each one with
``set_defaults(run=function)``. A run function receives the parsed arguments,
prints a compact plain-text answer to stdout and returns the exit code.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

MODULES = ("tables", "flow", "diff", "find", "org")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syn", description="Salesforce metadata, cheap to read and to change.")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name in MODULES:
        importlib.import_module(f"syn.{name}").register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command. Exit codes: 0 done, 1 some file failed, 2 nothing done (usage or input error).

    A reader that stops early (``syn rows ... | head``) closes the pipe; that
    ends the command quietly instead of with a traceback.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")
    args = build_parser().parse_args(argv)
    try:
        code = int(args.run(args) or 0)
        sys.stdout.flush()
        return code
    except SynError as error:
        print(f"syn: {error}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


class SynError(Exception):
    """A problem the user can fix: a missing file, an unknown key, an ambiguous block."""
