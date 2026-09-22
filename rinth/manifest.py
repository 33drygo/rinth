"""rinth.toml (hand-written) and rinth.lock.json (generated).

The manifest is edited surgically, line by line, rather than re-serialized whole:
that way your comments and your ordering survive. tomllib is only used to read it.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ManifestError

MANIFEST_NAME = "rinth.toml"
LOCK_NAME = "rinth.lock.json"
LOCK_VERSION = 1

BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
CHANNELS = ("release", "beta", "alpha")

DEFAULT_LOADER = "paper"
DEFAULT_DIR = "."


@dataclass
class Entry:
    """One line of [plugins]."""

    slug: str
    version: str = "*"
    channel: str = "release"

    @property
    def pinned(self):
        return self.version not in ("*", "", None)

    @property
    def override(self):
        return self.version if self.pinned else None


@dataclass
class Manifest:
    root: Path                      # directory holding rinth.toml
    loader: str = DEFAULT_LOADER
    minecraft: str | None = None
    target_dir: str = DEFAULT_DIR
    entries: dict = field(default_factory=dict)
    _text: str = ""

    # --- paths ---------------------------------------------------------

    @property
    def path(self):
        return self.root / MANIFEST_NAME

    @property
    def lock_path(self):
        return self.root / LOCK_NAME

    @property
    def install_dir(self):
        return (self.root / self.target_dir).resolve()

    # --- reading -------------------------------------------------------

    @classmethod
    def exists(cls, root):
        return (Path(root) / MANIFEST_NAME).is_file()

    @classmethod
    def load(cls, root):
        root = Path(root).resolve()
        path = root / MANIFEST_NAME
        try:
            text = path.read_text()
        except FileNotFoundError as exc:
            raise ManifestError(
                f"no {MANIFEST_NAME} in {root}",
                hint="create one with: rinth init -l paper -m 1.21.4",
            ) from exc
        except OSError as exc:
            raise ManifestError(f"could not read {path}: {exc}") from exc

        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ManifestError(f"{MANIFEST_NAME} has a syntax error: {exc}") from exc

        server = data.get("server") or {}
        if not isinstance(server, dict):
            raise ManifestError("[server] must be a table")

        manifest = cls(
            root=root,
            loader=str(server.get("loader") or DEFAULT_LOADER),
            minecraft=str(server["minecraft"]) if server.get("minecraft") else None,
            target_dir=str(server.get("dir") or DEFAULT_DIR),
            _text=text,
        )

        plugins = data.get("plugins") or {}
        if not isinstance(plugins, dict):
            raise ManifestError("[plugins] must be a table")
        for slug, value in plugins.items():
            manifest.entries[slug] = _parse_entry(slug, value)
        return manifest

    # --- surgical writing ----------------------------------------------

    def set_entry(self, entry: Entry):
        """Add or update an entry, preserving comments and ordering."""
        self.entries[entry.slug] = entry
        self._text = _upsert_key(self._text, "plugins", entry.slug, _render_value(entry))

    def drop_entry(self, slug):
        self.entries.pop(slug, None)
        self._text = _remove_key(self._text, "plugins", slug)

    def set_server(self, **values):
        attributes = {"minecraft": "minecraft", "loader": "loader", "dir": "target_dir"}
        for key, value in values.items():
            if value is None:
                continue
            setattr(self, attributes[key], value)
            self._text = _upsert_key(self._text, "server", key, _quote(str(value)))

    def save(self):
        _atomic_write(self.path, self._text)

    # --- creation ------------------------------------------------------

    @classmethod
    def create(cls, root, loader, minecraft, target_dir=DEFAULT_DIR):
        root = Path(root).resolve()
        lines = [
            "# rinth — Modrinth project manager",
            "",
            "[server]",
            f"loader = {_quote(loader)}",
        ]
        if minecraft:
            lines.append(f"minecraft = {_quote(minecraft)}")
        lines += [
            f"dir = {_quote(target_dir)}",
            "",
            "[plugins]",
            '# slug = "*"            # latest compatible version',
            '# slug = "1.2.3"        # pinned version',
            '# slug = { version = "*", channel = "beta" }',
            "",
        ]
        return cls(
            root=root,
            loader=loader,
            minecraft=minecraft,
            target_dir=target_dir,
            _text="\n".join(lines),
        )


def _parse_entry(slug, value):
    if isinstance(value, str):
        return Entry(slug=slug, version=value or "*")
    if isinstance(value, dict):
        channel = str(value.get("channel") or "release")
        if channel not in CHANNELS:
            raise ManifestError(
                f"{slug}: channel '{channel}' is not valid",
                hint=f"use one of: {', '.join(CHANNELS)}",
            )
        return Entry(slug=slug, version=str(value.get("version") or "*"), channel=channel)
    raise ManifestError(f"{slug}: the value must be a string or an inline table")


# --- minimal TOML serialization ----------------------------------------
# Covers only what the schema needs: strings and inline tables of strings.


def _quote(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _key(name):
    return name if BARE_KEY.match(name) else _quote(name)


def _render_value(entry: Entry):
    if entry.channel != "release":
        return f"{{ version = {_quote(entry.version)}, channel = {_quote(entry.channel)} }}"
    return _quote(entry.version)


def _section_bounds(lines, section):
    """(start, end) of the lines belonging to [section]; None if absent."""
    header = re.compile(rf"^\s*\[\s*{re.escape(section)}\s*\]\s*(#.*)?$")
    other = re.compile(r"^\s*\[")
    start = None
    for index, line in enumerate(lines):
        if header.match(line):
            start = index + 1
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        if other.match(lines[index]):
            end = index
            break
    return start, end


def _key_line(lines, start, end, name):
    pattern = re.compile(rf"^\s*(?:{re.escape(name)}|\"{re.escape(name)}\")\s*=")
    for index in range(start, end):
        if pattern.match(lines[index]):
            return index
    return None


def _upsert_key(text, section, name, rendered):
    lines = text.splitlines()
    bounds = _section_bounds(lines, section)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", f"{_key(name)} = {rendered}", ""]
        return "\n".join(lines) + "\n"

    start, end = bounds
    found = _key_line(lines, start, end, name)
    if found is not None:
        indent = re.match(r"^\s*", lines[found]).group(0)
        comment = ""
        # Keep a trailing comment if the line had one.
        tail = lines[found].split("#", 1)
        if len(tail) == 2 and tail[0].count('"') % 2 == 0:
            comment = "  #" + tail[1]
        lines[found] = f"{indent}{_key(name)} = {rendered}{comment}"
        return "\n".join(lines) + "\n"

    insert_at = end
    while insert_at > start and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f"{_key(name)} = {rendered}")
    return "\n".join(lines) + "\n"


def _remove_key(text, section, name):
    lines = text.splitlines()
    bounds = _section_bounds(lines, section)
    if bounds is None:
        return text
    start, end = bounds
    found = _key_line(lines, start, end, name)
    if found is None:
        return text
    del lines[found]
    return "\n".join(lines) + "\n"


def _atomic_write(path, text):
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except OSError as exc:
        raise ManifestError(f"could not write {path}: {exc}") from exc


# --- lockfile ----------------------------------------------------------


class Lock:
    """Exact state of what is installed. Generated; never hand-edited."""

    def __init__(self, path, data=None):
        self.path = Path(path)
        data = data or {}
        self.server = data.get("server") or {}
        self.entries = data.get("entries") or {}

    @classmethod
    def load(cls, path):
        path = Path(path)
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return cls(path)
        except (OSError, ValueError):
            # A corrupt lock must not block anything: it is rebuilt on install.
            return cls(path)
        if data.get("version") != LOCK_VERSION:
            return cls(path)
        return cls(path, data)

    def get(self, slug):
        return self.entries.get(slug)

    def put(self, resolution, installed_as=None):
        self.entries[resolution.slug] = {
            "project_id": resolution.project_id,
            "title": resolution.title,
            "version_id": resolution.version_id,
            "version_number": resolution.version_number,
            "filename": installed_as or resolution.filename,
            "sha512": resolution.sha512,
            "size": resolution.file.get("size"),
            "url": resolution.url,
            "date_published": resolution.version.get("date_published"),
            "loader": resolution.loader_used,
            "pinned": resolution.pinned,
            "reason": resolution.reason,
        }

    def drop(self, slug):
        return self.entries.pop(slug, None)

    def dependencies_of(self, slug):
        """Entries that are here only because `slug` requires them."""
        needle = f"dependency of {slug}"
        return [key for key, value in self.entries.items() if value.get("reason") == needle]

    def save(self, loader, minecraft):
        payload = {
            "version": LOCK_VERSION,
            "server": {"loader": loader, "minecraft": minecraft},
            "entries": self.entries,
        }
        tmp = Path(str(self.path) + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except OSError as exc:
            raise ManifestError(f"could not write {self.path}: {exc}") from exc
