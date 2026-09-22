"""Presentation: tables, color and messages.

Color is only enabled when stdout is a tty and NO_COLOR is unset. With --json
none of this is used: cli.py dumps JSON and exits.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import sys
import unicodedata

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

QUIET = False
_COLOR = None


def color_enabled(stream=None) -> bool:
    global _COLOR
    if _COLOR is None:
        stream = stream or sys.stdout
        _COLOR = (
            hasattr(stream, "isatty")
            and stream.isatty()
            and os.environ.get("NO_COLOR") is None
            and os.environ.get("TERM") != "dumb"
        )
    return _COLOR


def set_color(enabled):
    global _COLOR
    _COLOR = enabled


def paint(text, code):
    if not color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text):
    return paint(text, "1")


def dim(text):
    return paint(text, "2")


def green(text):
    return paint(text, "32")


def yellow(text):
    return paint(text, "33")


def red(text):
    return paint(text, "31")


def cyan(text):
    return paint(text, "36")


# --- messages ----------------------------------------------------------


def info(message):
    if not QUIET:
        print(message)


def step(message):
    if not QUIET:
        print(f"{cyan('::')} {message}")


def ok(message):
    if not QUIET:
        print(f"{green('✓')} {message}")


def warn(message):
    print(f"{yellow('warning:')} {message}", file=sys.stderr)


def fail(message, hint=None):
    print(f"{red('error:')} {message}", file=sys.stderr)
    if hint:
        print(f"       {dim(hint)}", file=sys.stderr)


# --- formatting --------------------------------------------------------


def width(text) -> int:
    """Approximate display width: wide characters count as two columns."""
    total = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def clean(text) -> str:
    return _CONTROL.sub(" ", str(text or "")).strip()


def truncate(text, limit) -> str:
    text = clean(text)
    if limit <= 0 or width(text) <= limit:
        return text
    out = []
    used = 0
    for char in text:
        char_width = width(char)
        if used + char_width > limit - 1:
            break
        out.append(char)
        used += char_width
    return "".join(out) + "…"


def downloads(count) -> str:
    try:
        count = int(count)
    except (TypeError, ValueError):
        return "?"
    for limit, suffix in ((1_000_000_000, "G"), (1_000_000, "M"), (1_000, "k")):
        if count >= limit:
            value = count / limit
            return f"{value:.1f}{suffix}" if value < 10 else f"{value:.0f}{suffix}"
    return str(count)


def size(num_bytes) -> str:
    try:
        num_bytes = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num_bytes < 1024 or unit == "GiB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GiB"


def parse_date(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def ago(value) -> str:
    """Short relative date: 3d, 5w, 2y."""
    moment = parse_date(value)
    if moment is None:
        return "?"
    delta = dt.datetime.now(dt.timezone.utc) - moment
    days = delta.days
    if days <= 0:
        return "today"
    if days < 7:
        return f"{days}d"
    if days < 60:
        return f"{days // 7}w"
    if days < 730:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def table(headers, rows, aligns=None, max_width=None):
    """Print an aligned table, shrinking the last column when needed."""
    if not rows:
        return
    rows = [[clean(cell) for cell in row] for row in rows]
    columns = len(headers)
    aligns = aligns or ["<"] * columns
    widths = [width(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], width(cell))

    limit = max_width or shutil.get_terminal_size((100, 24)).columns
    total = sum(widths) + 2 * (columns - 1)
    if total > limit:
        widths[-1] = max(12, widths[-1] - (total - limit))

    def render(cells, styler=None):
        parts = []
        for index, cell in enumerate(cells):
            cell = truncate(cell, widths[index])
            pad = widths[index] - width(cell)
            piece = cell + " " * pad if aligns[index] == "<" else " " * pad + cell
            parts.append(styler(piece) if styler else piece)
        return "  ".join(parts).rstrip()

    print(render(headers, bold))
    for row in rows:
        print(render(row))
