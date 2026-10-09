"""Terminal input/output helpers for the CLI."""

from __future__ import annotations

import getpass
import sys
from typing import Callable, Sequence

_USE_COLOR = sys.stdout.isatty()

_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}


def _c(text: str, color: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def line(text: str = "") -> None:
    print(text)


def title(text: str) -> None:
    print()
    print(_c(text, "bold"))
    print(_c("─" * len(text), "dim"))


def info(text: str) -> None:
    print(f"{_c('•', 'blue')} {text}")


def success(text: str) -> None:
    print(f"{_c('✔', 'green')} {text}")


def warn(text: str) -> None:
    print(f"{_c('!', 'yellow')} {text}")


def error(text: str) -> None:
    print(f"{_c('✘', 'red')} {text}", file=sys.stderr)


def ask(
    prompt: str,
    *,
    default: str | None = None,
    validator: Callable[[str], str] | None = None,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{_c('?', 'cyan')} {prompt}{suffix}: ").strip()
        except EOFError:
            raise KeyboardInterrupt
        if not raw and default is not None:
            raw = default
        if not raw:
            error("A value is required.")
            continue
        if validator is not None:
            try:
                return validator(raw)
            except Exception as exc:  # ValidationError
                error(str(exc))
                continue
        return raw


def secret(prompt: str, *, validator: Callable[[str], str] | None = None) -> str:
    # Only use getpass when attached to a real terminal. When input is piped
    # (scripts, CI, tests), getpass would open /dev/tty and block forever;
    # fall back to reading a normal line from stdin instead.
    interactive = sys.stdin.isatty()
    while True:
        try:
            if interactive:
                raw = getpass.getpass(f"{_c('?', 'cyan')} {prompt}: ")
            else:
                raw = input(f"{_c('?', 'cyan')} {prompt}: ")
        except EOFError:
            raise KeyboardInterrupt
        if not raw:
            error("A value is required.")
            continue
        if validator is not None:
            try:
                return validator(raw)
            except Exception as exc:  # ValidationError
                error(str(exc))
                continue
        return raw


def confirm(prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        raw = input(f"{_c('?', 'cyan')} {prompt}{suffix}: ").strip().lower()
    except EOFError:
        raise KeyboardInterrupt
    if not raw:
        return default
    return raw in ("y", "yes", "j", "ja")


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    if not rows:
        info("(no records)")
        return
    string_rows = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in string_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    header = "  ".join(_c(h.ljust(widths[i]), "bold") for i, h in enumerate(headers))
    print(header)
    print(_c("  ".join("─" * w for w in widths), "dim"))
    for row in string_rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
