"""Version selection and dependency resolution.

Two quirks of the Modrinth API govern this module:

1. A plugin publishes one version *per platform* (LuckPerms ships v5.5.71-bukkit
   and v5.5.71-velocity). Filtering with ?loaders= is not enough: results must be
   filtered again client-side and sorted by date_published, because the API does
   not guarantee an order.
2. Dependencies carry a project_id but almost always version_id: null, so
   resolving a dependency means repeating the whole selection, not following a
   pointer.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .errors import NotFound, ResolveError
from .output import parse_date

# Sentinel for date sorting when date_published is missing or invalid.
_EPOCH = dt.datetime.min.replace(tzinfo=dt.timezone.utc)

# Compatibility chains: if the requested loader has no versions, try the next
# one. Only in the direction where compatibility actually holds.
LOADER_FALLBACK = {
    "purpur": ["purpur", "paper", "spigot", "bukkit"],
    "folia": ["folia", "paper", "spigot", "bukkit"],
    "paper": ["paper", "spigot", "bukkit"],
    "spigot": ["spigot", "bukkit"],
    "bukkit": ["bukkit"],
    "waterfall": ["waterfall", "bungeecord"],
    "bungeecord": ["bungeecord"],
    # Velocity has its own API: a BungeeCord plugin does not run there.
    "velocity": ["velocity"],
    "quilt": ["quilt", "fabric"],
    "fabric": ["fabric"],
    "neoforge": ["neoforge"],
    "forge": ["forge"],
    "datapack": ["datapack"],
}

# A Paper plugin is not automatically safe on Folia: it must be folia-aware.
# Warn instead of staying quiet.
RISKY_FALLBACK = {("folia", "paper"), ("folia", "spigot"), ("folia", "bukkit")}

CHANNEL_RANK = {"release": 0, "beta": 1, "alpha": 2}

PLUGIN_LOADERS = {
    "paper", "spigot", "bukkit", "purpur", "folia",
    "velocity", "bungeecord", "waterfall",
}


def loader_chain(loader):
    return LOADER_FALLBACK.get(loader, [loader])


def channel_allows(version_type, channel):
    """release accepts release only; beta accepts release+beta; alpha, all."""
    return CHANNEL_RANK.get(version_type, 99) <= CHANNEL_RANK.get(channel, 0)


def primary_file(version):
    """The main jar of a version (some ship sources/javadoc alongside)."""
    files = version.get("files") or []
    if not files:
        return None
    return next((f for f in files if f.get("primary")), files[0])


def newest(versions):
    return sorted(
        versions, key=lambda v: (parse_date(v.get("date_published")) or _EPOCH), reverse=True
    )


@dataclass
class Resolution:
    """A concrete version, ready to download."""

    slug: str
    project_id: str
    title: str
    version: dict
    file: dict
    loader_used: str
    requested_loader: str
    reason: str = "direct"
    pinned: bool = False
    warnings: list = field(default_factory=list)

    @property
    def version_id(self):
        return self.version.get("id")

    @property
    def version_number(self):
        return self.version.get("version_number")

    @property
    def filename(self):
        return self.file.get("filename")

    @property
    def sha512(self):
        return (self.file.get("hashes") or {}).get("sha512")

    @property
    def url(self):
        return self.file.get("url")

    @property
    def fell_back(self):
        return self.loader_used != self.requested_loader


def _supported_game_versions(client, ident, loaders):
    """For a useful error message: which Minecraft versions are supported.

    Keeps the order the API publishes them in (chronological); sorting them as
    strings would put 1.21.10 before 1.21.8.
    """
    seen = {}
    for loader in loaders:
        try:
            for version in newest(client.versions(ident, loaders=[loader]))[::-1]:
                if loader in (version.get("loaders") or []):
                    seen.update(dict.fromkeys(version.get("game_versions") or []))
        except NotFound:
            break
    return list(seen)


def _pick(versions, loader, mc, channel):
    """Filter client-side and return the newest version that satisfies all."""
    candidates = [
        version
        for version in versions
        if loader in (version.get("loaders") or [])
        and (mc is None or mc in (version.get("game_versions") or []))
        and channel_allows(version.get("version_type"), channel)
        and primary_file(version) is not None
    ]
    return newest(candidates)[0] if candidates else None


def resolve_version(
    client, ident, loader, mc, override=None, channel="release", reason="direct"
):
    """Pick the version of `ident` for (loader, mc). Raises ResolveError if none."""
    project = client.project(ident)
    slug = project.get("slug") or ident
    project_id = project.get("id")
    title = project.get("title") or slug
    warnings = []

    if override:
        version = _find_override(client, ident, slug, override)
        if loader not in (version.get("loaders") or []):
            warnings.append(
                f"{slug}: pinned version {version.get('version_number')} does not "
                f"declare '{loader}' (it declares "
                f"{', '.join(version.get('loaders') or []) or 'nothing'})"
            )
        if mc and mc not in (version.get("game_versions") or []):
            warnings.append(
                f"{slug}: pinned version {version.get('version_number')} "
                f"does not support MC {mc}"
            )
        file = primary_file(version)
        if file is None:
            raise ResolveError(f"{slug}: version {override} has no files")
        return Resolution(
            slug=slug,
            project_id=project_id,
            title=title,
            version=version,
            file=file,
            loader_used=loader,
            requested_loader=loader,
            reason=reason,
            pinned=True,
            warnings=warnings,
        )

    chain = loader_chain(loader)
    for step_loader in chain:
        versions = client.versions(
            ident, loaders=[step_loader], game_versions=[mc] if mc else None
        )
        chosen = _pick(versions, step_loader, mc, channel)
        if chosen is None:
            continue
        if step_loader != loader:
            note = f"{slug}: nothing published for '{loader}', using the '{step_loader}' build"
            if (loader, step_loader) in RISKY_FALLBACK:
                note += " — check that the plugin is Folia-compatible"
            warnings.append(note)
        return Resolution(
            slug=slug,
            project_id=project_id,
            title=title,
            version=chosen,
            file=primary_file(chosen),
            loader_used=step_loader,
            requested_loader=loader,
            reason=reason,
            pinned=False,
            warnings=warnings,
        )

    supported = _supported_game_versions(client, ident, chain)
    if supported:
        tail = ", ".join(supported[-8:])
        raise ResolveError(
            f"{slug}: no version for {loader} + MC {mc}",
            hint=f"supported Minecraft versions: {tail}",
        )
    if channel == "release":
        raise ResolveError(
            f"{slug}: no version for {loader} + MC {mc}",
            hint="try --channel beta in case it only ships prereleases",
        )
    raise ResolveError(f"{slug}: no versions at all for {loader}")


def _find_override(client, ident, slug, override):
    """Look up by version_id, then by version_number across all versions."""
    versions = client.versions(ident)
    for version in versions:
        if version.get("id") == override:
            return version
    matches = [v for v in versions if v.get("version_number") == override]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Several platforms share a version_number; let the user disambiguate.
        options = "  ".join(
            f"{v['id']} ({'/'.join(v.get('loaders') or [])})" for v in matches[:6]
        )
        raise ResolveError(
            f"{slug}: '{override}' matches {len(matches)} different builds",
            hint=f"use the exact id: {options}",
        )
    known = ", ".join(v.get("version_number", "?") for v in newest(versions)[:6])
    raise ResolveError(
        f"{slug}: version '{override}' does not exist",
        hint=f"recent versions: {known}" if known else None,
    )


def resolve_all(client, requests, loader, mc, channel="release", with_deps=True):
    """Resolve several projects plus their required dependencies.

    `requests` is a list of (ident, override). Returns
    (resolutions, optional, incompatible, warnings): resolutions include the
    dependencies, tagged through `reason`.
    """
    resolved = {}
    optional = []
    warnings = []
    incompatible = []
    queue = [(ident, override, "direct") for ident, override in requests]
    seen_idents = set()

    while queue:
        ident, override, reason = queue.pop(0)
        if ident in seen_idents:
            continue
        seen_idents.add(ident)

        resolution = resolve_version(
            client, ident, loader, mc, override=override, channel=channel, reason=reason
        )
        warnings.extend(resolution.warnings)

        previous = resolved.get(resolution.project_id)
        if previous is not None:
            resolved[resolution.project_id] = _winner(previous, resolution, warnings)
            continue
        resolved[resolution.project_id] = resolution

        if not with_deps:
            continue

        for dependency in resolution.version.get("dependencies") or []:
            kind = dependency.get("dependency_type")
            target = dependency.get("project_id")
            if kind == "required":
                version_id = dependency.get("version_id")
                if not target and version_id:
                    # Rarely a dependency only carries the version; pull the
                    # project out of it so it resolves like any other.
                    target = (client.version(version_id) or {}).get("project_id")
                if not target:
                    continue
                # With a version_id the dependency is nailed to that build;
                # without one (the usual case) it resolves like any project.
                queue.append((target, version_id, f"dependency of {resolution.slug}"))
            elif kind == "optional" and target:
                optional.append((target, resolution.slug))
            elif kind == "incompatible" and target:
                incompatible.append((target, resolution.slug))

    return list(resolved.values()), optional, incompatible, warnings


def _winner(previous, candidate, warnings):
    """Two branches want the same project: a pin wins, otherwise the newest."""
    if previous.pinned and not candidate.pinned:
        return previous
    if candidate.pinned and not previous.pinned:
        warnings.append(
            f"{candidate.slug}: using pinned version {candidate.version_number} "
            f"instead of {previous.version_number}"
        )
        return candidate
    older, newer = sorted(
        (previous, candidate),
        key=lambda r: parse_date(r.version.get("date_published")) or _EPOCH,
    )
    if older.version_id != newer.version_id:
        warnings.append(
            f"{newer.slug}: requested twice, installing {newer.version_number} "
            f"(dropping {older.version_number})"
        )
    return newer


def derive_type(obj):
    """The type Modrinth shows on the website.

    /search returns all_project_types; /project does not, and there every plugin
    shows up as 'mod'. In that case infer it from the loaders, like the site does.
    """
    types = obj.get("all_project_types") or []
    if "plugin" in types:
        return "plugin"
    base = obj.get("project_type") or "?"
    if base == "mod":
        loaders = set(obj.get("loaders") or []) | set(obj.get("categories") or [])
        if loaders & PLUGIN_LOADERS:
            return "plugin"
    return types[0] if types and base == "?" else base
