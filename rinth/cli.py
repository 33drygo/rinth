"""rinth command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import output as out
from .api import Client
from .errors import ManifestError, RinthError
from .install import already_present, download, file_hash, remove_file
from .manifest import MANIFEST_NAME, Entry, Lock, Manifest
from .resolve import (
    CHANNEL_RANK,
    LOADER_FALLBACK,
    derive_type,
    newest,
    resolve_all,
)

LOADERS = sorted(LOADER_FALLBACK)
TYPES = ["plugin", "mod", "datapack", "shader", "resourcepack"]
CHANNELS = list(CHANNEL_RANK)


# --- shared helpers ----------------------------------------------------


def _root(args):
    return Path(args.dir or ".").resolve()


def _manifest(args, required=True):
    root = _root(args)
    if Manifest.exists(root):
        return Manifest.load(root)
    if required:
        raise ManifestError(
            f"no {MANIFEST_NAME} in {root}",
            hint="start with: rinth init -l paper -m <version>",
        )
    return None


def _latest_release(client):
    for tag in client.game_versions():
        if tag.get("version_type") == "release":
            return tag.get("version")
    return None


def _mc_summary(versions):
    versions = list(versions or [])
    if not versions:
        return "—"
    if len(versions) <= 2:
        return ", ".join(versions)
    return f"{versions[-1]} (+{len(versions) - 1})"


def _dump(payload):
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _confirm(question, assume_yes):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise RinthError(
            "confirmation is required and there is no interactive terminal",
            hint="add -y",
        )
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _progress(name):
    def report(done, total):
        if out.QUIET or not out.color_enabled():
            return
        if total:
            percent = done * 100 // total
            print(f"\r  {name}  {percent:3d}%", end="", flush=True)
            if done >= total:
                print("\r" + " " * (len(name) + 12) + "\r", end="", flush=True)

    return report


# --- applying a set of resolutions -------------------------------------


def _plan(resolutions, lock, dest):
    """Classify each resolution as ok / install / update / repair."""
    rows = []
    for resolution in resolutions:
        previous = lock.get(resolution.slug)
        path = dest / resolution.filename
        same_version = previous and previous.get("version_id") == resolution.version_id
        if same_version and already_present(path, resolution.sha512):
            action = "ok"
        elif same_version:
            action = "repair"
        elif previous:
            action = "update"
        else:
            action = "install"
        rows.append((action, resolution, previous))
    return rows


def _show_plan(rows):
    labels = {
        "ok": out.dim("unchanged"),
        "repair": out.yellow("repair"),
        "update": out.cyan("update"),
        "install": out.green("install"),
    }
    table = []
    for action, resolution, previous in rows:
        detail = resolution.version_number
        if action == "update" and previous:
            detail = f"{previous.get('version_number')} → {resolution.version_number}"
        origin = "" if resolution.reason == "direct" else out.dim(
            resolution.reason.replace("dependency of ", "dep of ")
        )
        table.append([labels[action], resolution.slug, detail, origin])
    out.table(["ACTION", "PROJECT", "VERSION", ""], table)


def _apply(manifest, lock, resolutions, args, record_manifest=True):
    """Download whatever is needed and update the manifest and lock."""
    dest = manifest.install_dir
    rows = _plan(resolutions, lock, dest)
    pending = [row for row in rows if row[0] != "ok"]

    if not out.QUIET:
        _show_plan(rows)

    if args.dry_run:
        out.info(out.dim("\n--dry-run: nothing was written"))
        return 0

    if not pending:
        out.ok("everything is up to date")
        return 0

    for action, resolution, previous in pending:
        out.step(f"{resolution.slug} {resolution.version_number}")
        target = dest / resolution.filename
        download(
            resolution.url,
            target,
            sha512=resolution.sha512,
            expected_size=resolution.file.get("size"),
            on_progress=_progress(resolution.filename),
        )
        # Only the file this lock recorded is deleted, never somebody else's.
        if previous and previous.get("filename") and previous["filename"] != resolution.filename:
            remove_file(dest / previous["filename"])
        lock.put(resolution)

    if record_manifest:
        for _, resolution, _ in rows:
            if resolution.reason != "direct":
                continue
            entry = manifest.entries.get(resolution.slug)
            version = args.version if getattr(args, "version", None) else (
                entry.version if entry else "*"
            )
            channel = getattr(args, "channel", "release")
            manifest.set_entry(Entry(resolution.slug, version, channel))
        manifest.save()

    lock.save(manifest.loader, manifest.minecraft)
    out.ok(f"{len(pending)} file(s) in {dest}")
    return 0


def _report(optional, incompatible, warnings, client):
    for warning in warnings:
        out.warn(warning)
    if incompatible:
        for project_id, parent in incompatible:
            out.warn(f"{parent} declares an incompatibility with {project_id}")
    if optional and not out.QUIET:
        names = _names(client, [pid for pid, _ in optional])
        listed = ", ".join(sorted({names.get(pid, pid) for pid, _ in optional}))
        out.info(out.dim(f"\noptional, not installed: {listed}"))


def _names(client, project_ids):
    try:
        return {p["id"]: p.get("slug", p["id"]) for p in client.projects(set(project_ids))}
    except RinthError:
        return {}


# --- commands ----------------------------------------------------------


def cmd_init(args, client):
    root = _root(args)
    if Manifest.exists(root) and not args.force:
        raise ManifestError(
            f"{MANIFEST_NAME} already exists in {root}", hint="use --force to overwrite it"
        )
    minecraft = args.minecraft or _latest_release(client)
    manifest = Manifest.create(root, args.loader, minecraft, args.target)
    manifest.save()
    out.ok(f"created {manifest.path}")
    out.info(
        f"  loader: {out.bold(manifest.loader)}   "
        f"minecraft: {out.bold(manifest.minecraft or '?')}   "
        f"dir: {out.bold(manifest.target_dir)}"
    )
    return 0


def cmd_search(args, client):
    manifest = _manifest(args, required=False)
    loader = args.loader or (manifest.loader if manifest and not args.all else None)
    minecraft = args.minecraft or (manifest.minecraft if manifest and not args.all else None)

    data = client.search(
        args.query,
        project_type=args.type,
        loader=loader,
        mc=minecraft,
        limit=args.limit,
    )
    hits = data.get("hits") or []

    if args.json:
        _dump(data)
        return 0
    if not hits:
        out.info("no results")
        if loader or minecraft:
            out.info(out.dim(
                f"active filters: {loader or '—'} / MC {minecraft or '—'} (use -A to drop them)"
            ))
        return 1

    rows = [
        [
            hit.get("slug", "?"),
            derive_type(hit),
            out.downloads(hit.get("downloads")),
            hit.get("title", ""),
            hit.get("description", ""),
        ]
        for hit in hits
    ]
    out.table(["SLUG", "TYPE", "↓", "TITLE", "DESCRIPTION"], rows, ["<", "<", ">", "<", "<"])
    total = data.get("total_hits", len(hits))
    if total > len(hits):
        out.info(out.dim(f"\n{len(hits)} of {total} — use -n to see more"))
    return 0


def cmd_versions(args, client):
    manifest = _manifest(args, required=False)
    loader = args.loader or (manifest.loader if manifest and not args.all else None)
    minecraft = args.minecraft or (manifest.minecraft if manifest and not args.all else None)

    versions = client.versions(
        args.ident,
        loaders=[loader] if loader else None,
        game_versions=[minecraft] if minecraft else None,
    )
    if loader:
        versions = [v for v in versions if loader in (v.get("loaders") or [])]
    versions = newest(versions)[: args.limit]

    if args.json:
        _dump(versions)
        return 0
    if not versions:
        out.info("no versions match the filters")
        if loader or minecraft:
            out.info(out.dim("use -A to see the full history"))
        return 1

    rows = [
        [
            version.get("version_number", "?"),
            version.get("version_type", "?"),
            ",".join(version.get("loaders") or []),
            _mc_summary(version.get("game_versions")),
            out.ago(version.get("date_published")),
            out.downloads(version.get("downloads")),
            version.get("id", ""),
        ]
        for version in versions
    ]
    out.table(
        ["VERSION", "CHANNEL", "LOADERS", "MC", "AGE", "↓", "ID"],
        rows,
        ["<", "<", "<", "<", ">", ">", "<"],
    )
    return 0


def cmd_info(args, client):
    project = client.project(args.ident)
    if args.json:
        _dump(project)
        return 0

    license_name = (project.get("license") or {}).get("id", "?")
    print(f"{out.bold(project.get('title', '?'))}  {out.dim(project.get('slug', ''))}")
    description = project.get("description")
    if description:
        print(out.clean(description))
    print()
    fields = [
        ("type", derive_type(project)),
        ("id", project.get("id", "?")),
        ("downloads", out.downloads(project.get("downloads"))),
        ("followers", out.downloads(project.get("followers"))),
        ("license", license_name),
        ("loaders", ", ".join(project.get("loaders") or []) or "—"),
        ("minecraft", _mc_summary(project.get("game_versions"))),
        ("categories", ", ".join(project.get("categories") or []) or "—"),
        ("environment",
         f"client {project.get('client_side', '?')} / server {project.get('server_side', '?')}"),
        ("source", project.get("source_url") or "—"),
    ]
    label_width = max(len(name) for name, _ in fields)
    for name, value in fields:
        print(f"  {out.dim(name.ljust(label_width))}  {value}")
    return 0


def cmd_install(args, client):
    if args.version and len(args.idents) != 1:
        raise RinthError("-V only makes sense with a single project at a time")

    root = _root(args)
    if Manifest.exists(root):
        manifest = Manifest.load(root)
    elif not args.loader:
        raise ManifestError(
            f"no {MANIFEST_NAME} in {root}",
            hint="create one with 'rinth init -l paper -m <version>' or pass -l here",
        )
    else:
        manifest = Manifest.create(
            root, args.loader, args.minecraft or _latest_release(client), args.target
        )
        if not args.dry_run:
            # Written now, so the message does not promise something absent.
            manifest.save()
            out.info(out.dim(f"created {manifest.path}"))

    loader = args.loader or manifest.loader
    minecraft = args.minecraft or manifest.minecraft
    requests = [(ident, args.version) for ident in args.idents]
    resolutions, optional, incompatible, warnings = resolve_all(
        client, requests, loader, minecraft, channel=args.channel, with_deps=not args.no_deps
    )
    lock = Lock.load(manifest.lock_path)
    _report(optional, incompatible, warnings, client)
    return _apply(manifest, lock, resolutions, args)


def cmd_update(args, client):
    manifest = _manifest(args)
    lock = Lock.load(manifest.lock_path)
    targets = args.idents or list(manifest.entries)
    if not targets:
        out.info("the manifest has no entries")
        return 0

    unknown = [slug for slug in targets if slug not in manifest.entries]
    if unknown:
        raise ManifestError(f"not in {MANIFEST_NAME}: {', '.join(unknown)}")

    requests = []
    kept = []
    for slug in targets:
        entry = manifest.entries[slug]
        if entry.pinned and not args.force:
            kept.append(f"{slug} ({entry.version})")
            requests.append((slug, entry.override))
        else:
            requests.append((slug, None))

    channel = _dominant_channel(manifest, targets)
    resolutions, optional, incompatible, warnings = resolve_all(
        client, requests, manifest.loader, manifest.minecraft, channel=channel
    )
    _report(optional, incompatible, warnings, client)
    if kept:
        out.info(out.dim(f"pinned, left alone: {', '.join(kept)}"))

    args.version = None
    args.channel = channel
    status = _apply(manifest, lock, resolutions, args, record_manifest=args.force)

    if not args.idents and not args.dry_run:
        _prune_orphans(manifest, lock, resolutions)
    return status


def _dominant_channel(manifest, slugs):
    """The most permissive channel among the entries being updated."""
    channels = [manifest.entries[slug].channel for slug in slugs]
    return max(channels, key=lambda c: CHANNEL_RANK.get(c, 0)) if channels else "release"


def _prune_orphans(manifest, lock, resolutions):
    """Drop dependencies nothing requires any more from the lock."""
    alive = {resolution.slug for resolution in resolutions}
    dest = manifest.install_dir
    removed = []
    for slug, entry in list(lock.entries.items()):
        if slug in alive or slug in manifest.entries:
            continue
        if not str(entry.get("reason", "")).startswith("dependency of"):
            continue
        if entry.get("filename"):
            remove_file(dest / entry["filename"])
        lock.drop(slug)
        removed.append(slug)
    if removed:
        lock.save(manifest.loader, manifest.minecraft)
        out.info(out.dim(f"dependencies no longer needed: {', '.join(removed)}"))


def cmd_remove(args, client):
    manifest = _manifest(args)
    lock = Lock.load(manifest.lock_path)
    dest = manifest.install_dir

    unknown = [s for s in args.idents if s not in manifest.entries and s not in lock.entries]
    if unknown:
        raise ManifestError(f"not installed: {', '.join(unknown)}")

    doomed = list(args.idents)
    if not args.keep_deps:
        # Work queue: a dependency can drag its own along, so walk all the way
        # down the tree instead of stopping one level in.
        pending = list(args.idents)
        while pending:
            for dependency in lock.dependencies_of(pending.pop()):
                if dependency in manifest.entries or dependency in doomed:
                    continue
                doomed.append(dependency)
                pending.append(dependency)

    extra = [s for s in doomed if s not in args.idents]
    out.info("about to remove: " + ", ".join(doomed))
    if extra:
        tail = (
            "was only there as a dependency"
            if len(extra) == 1
            else "were only there as dependencies"
        )
        out.info(out.dim(f"  ({', '.join(extra)} {tail})"))
    if args.dry_run:
        out.info(out.dim("--dry-run: nothing was written"))
        return 0
    if not _confirm("continue?", args.yes):
        out.info("cancelled")
        return 1

    for slug in doomed:
        entry = lock.get(slug)
        if entry and entry.get("filename"):
            remove_file(dest / entry["filename"])
        lock.drop(slug)
        manifest.drop_entry(slug)

    manifest.save()
    lock.save(manifest.loader, manifest.minecraft)
    out.ok(f"{len(doomed)} project(s) removed")
    return 0


def cmd_list(args, client):
    manifest = _manifest(args)
    lock = Lock.load(manifest.lock_path)
    dest = manifest.install_dir

    slugs = sorted(set(manifest.entries) | set(lock.entries))
    if args.json:
        _dump(
            {
                "server": {
                    "loader": manifest.loader,
                    "minecraft": manifest.minecraft,
                    "dir": str(dest),
                },
                "entries": {
                    slug: {
                        "manifest": vars(manifest.entries[slug])
                        if slug in manifest.entries
                        else None,
                        "lock": lock.get(slug),
                        "state": _file_state(dest, lock.get(slug)),
                    }
                    for slug in slugs
                },
            }
        )
        return 0

    if not slugs:
        out.info("nothing installed yet")
        return 0

    rows = []
    for slug in slugs:
        entry = manifest.entries.get(slug)
        locked = lock.get(slug)
        origin = "—"
        if entry is None and locked:
            origin = out.dim(str(locked.get("reason", "")).replace("dependency of ", "dep of "))
        elif entry and entry.pinned:
            origin = out.yellow(f"pinned {entry.version}")
        elif entry and entry.channel != "release":
            origin = out.dim(entry.channel)
        rows.append(
            [
                slug,
                (locked or {}).get("version_number", "—"),
                (locked or {}).get("loader", "—"),
                _file_state(dest, locked),
                origin,
            ]
        )
    out.table(["PROJECT", "VERSION", "LOADER", "FILE", "ORIGIN"], rows)
    out.info(out.dim(f"\n{manifest.loader} · MC {manifest.minecraft or '?'} · {dest}"))
    return 0


def _file_state(dest, locked):
    if not locked or not locked.get("filename"):
        return out.dim("—")
    path = Path(dest) / locked["filename"]
    if not path.is_file():
        return out.red("missing")
    sha512 = locked.get("sha512")
    if sha512 and file_hash(path, "sha512") != sha512.lower():
        return out.yellow("modified")
    return out.green("ok")


def cmd_sync(args, client):
    manifest = _manifest(args)
    lock = Lock.load(manifest.lock_path)
    if not manifest.entries:
        out.info("the manifest has no entries")
        return 0

    requests = [(slug, entry.override) for slug, entry in manifest.entries.items()]
    channel = _dominant_channel(manifest, list(manifest.entries))
    resolutions, optional, incompatible, warnings = resolve_all(
        client, requests, manifest.loader, manifest.minecraft, channel=channel
    )
    _report(optional, incompatible, warnings, client)
    args.version = None
    args.channel = channel
    return _apply(manifest, lock, resolutions, args, record_manifest=False)


def cmd_adopt(args, client):
    manifest = _manifest(args)
    lock = Lock.load(manifest.lock_path)
    dest = manifest.install_dir

    jars = sorted(p for p in dest.glob("*.jar") if p.is_file())
    if not jars:
        out.info(f"no .jar files in {dest}")
        return 0

    known = {entry.get("filename") for entry in lock.entries.values()}
    adopted, unknown = [], []
    for jar in jars:
        if jar.name in known:
            continue
        out.step(f"identifying {jar.name}")
        version = client.version_from_hash(file_hash(jar, "sha1"), "sha1")
        if not version:
            unknown.append(jar.name)
            continue
        project = client.project(version["project_id"])
        adopted.append((jar, project, version))

    if not adopted:
        out.info("nothing new to adopt")
        _report_unknown(unknown)
        return 0

    rows = [
        [project.get("slug", "?"), version.get("version_number", "?"), jar.name]
        for jar, project, version in adopted
    ]
    out.table(["PROJECT", "VERSION", "FILE"], rows)
    if args.dry_run:
        out.info(out.dim("--dry-run: nothing was written"))
        _report_unknown(unknown)
        return 0

    for jar, project, version in adopted:
        slug = project.get("slug")
        file = next((f for f in version.get("files") or [] if f.get("primary")), None)
        file = file or {"filename": jar.name, "hashes": {"sha512": file_hash(jar, "sha512")}}
        lock.entries[slug] = {
            "project_id": project.get("id"),
            "title": project.get("title", slug),
            "version_id": version.get("id"),
            "version_number": version.get("version_number"),
            "filename": jar.name,
            "sha512": (file.get("hashes") or {}).get("sha512"),
            "size": file.get("size"),
            "url": file.get("url"),
            "date_published": version.get("date_published"),
            "loader": next(iter(version.get("loaders") or []), manifest.loader),
            "pinned": args.pin,
            "reason": "direct",
        }
        manifest.set_entry(Entry(slug, version.get("version_number") if args.pin else "*"))

    manifest.save()
    lock.save(manifest.loader, manifest.minecraft)
    out.ok(f"{len(adopted)} project(s) adopted")
    _report_unknown(unknown)
    return 0


def _report_unknown(unknown):
    if unknown:
        out.info(out.dim(f"\nnot identified on Modrinth: {', '.join(unknown)}"))


# --- parser ------------------------------------------------------------

# Global flags are accepted before and after the subcommand: they live in a
# parent parser with default=SUPPRESS so the level that does not mention them
# cannot clobber the one that does, and anything missing is filled in afterwards
# from GLOBAL_DEFAULTS.
GLOBAL_DEFAULTS = {
    "dir": None,
    "dry_run": False,
    "quiet": False,
    "json": False,
    "no_cache": False,
    "color": "auto",
}


def _add_global_flags(parser):
    parser.add_argument(
        "-d", "--dir", metavar="PATH", default=argparse.SUPPRESS,
        help=f"project directory, where {MANIFEST_NAME} lives (default: current)",
    )
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="show what would happen and touch nothing")
    parser.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="errors only")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="JSON output (search, versions, info, list)")
    parser.add_argument("--no-cache", action="store_true", default=argparse.SUPPRESS,
                        help="ignore the response cache")
    parser.add_argument("--color", choices=["auto", "always", "never"],
                        default=argparse.SUPPRESS)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    _add_global_flags(common)

    parser = argparse.ArgumentParser(
        prog="rinth",
        parents=[common],
        description="Install and update Modrinth projects (plugins, mods, shaders…).",
    )
    parser.add_argument("--version", action="version", version=f"rinth {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, help_text, *aliases):
        return subparsers.add_parser(
            name, aliases=list(aliases), help=help_text, parents=[common]
        )

    def add_target_flags(sub, with_all=False):
        sub.add_argument("-l", "--loader", choices=LOADERS, help="target loader")
        sub.add_argument("-m", "--minecraft", metavar="VER", help="Minecraft version")
        if with_all:
            sub.add_argument("-A", "--all", action="store_true",
                             help="do not filter by loader or version")

    init = add("init", f"create a {MANIFEST_NAME}")
    init.add_argument("-l", "--loader", choices=LOADERS, default="paper")
    init.add_argument("-m", "--minecraft", metavar="VER", help="defaults to the latest release")
    init.add_argument("--into", metavar="PATH", default=".", dest="target",
                      help="subdirectory the jars go into")
    init.add_argument("-f", "--force", action="store_true")
    init.set_defaults(func=cmd_init)

    search = add("search", "search for projects", "s")
    search.add_argument("query")
    search.add_argument("-t", "--type", choices=TYPES, help="project type")
    search.add_argument("-n", "--limit", type=int, default=20, dest="limit")
    add_target_flags(search, with_all=True)
    search.set_defaults(func=cmd_search)

    versions = add("versions", "version history of a project", "v")
    versions.add_argument("ident", metavar="PROJECT")
    versions.add_argument("-n", "--limit", type=int, default=15, dest="limit")
    add_target_flags(versions, with_all=True)
    versions.set_defaults(func=cmd_versions)

    info = add("info", "project summary")
    info.add_argument("ident", metavar="PROJECT")
    info.set_defaults(func=cmd_info)

    install = add("install", "install projects and their dependencies", "i", "add")
    install.add_argument("idents", metavar="PROJECT", nargs="+")
    install.add_argument("-V", "--version", metavar="VER", dest="version",
                         help="specific version (id or number); pins the entry")
    install.add_argument("-c", "--channel", choices=CHANNELS, default="release")
    install.add_argument("--no-deps", action="store_true", help="do not resolve dependencies")
    install.add_argument("--into", metavar="PATH", default=".", dest="target",
                         help=f"jar subdirectory if a {MANIFEST_NAME} has to be created")
    add_target_flags(install)
    install.set_defaults(func=cmd_install)

    update = add("update", "move up to the latest compatible version", "u", "upgrade")
    update.add_argument("idents", metavar="PROJECT", nargs="*")
    update.add_argument("-f", "--force", action="store_true",
                        help="ignore pinned versions and rewrite the pin")
    update.set_defaults(func=cmd_update)

    remove = add("remove", "uninstall projects", "rm", "uninstall")
    remove.add_argument("idents", metavar="PROJECT", nargs="+")
    remove.add_argument("-y", "--yes", action="store_true")
    remove.add_argument("--keep-deps", action="store_true",
                        help="leave their dependencies alone")
    remove.set_defaults(func=cmd_remove)

    listing = add("list", "what is installed", "ls")
    listing.set_defaults(func=cmd_list)

    sync = add("sync", f"rebuild the directory from {MANIFEST_NAME}")
    sync.set_defaults(func=cmd_sync)

    adopt = add("adopt", "take over jars already present by identifying their hash")
    adopt.add_argument("--pin", action="store_true", help="pin the version found")
    adopt.set_defaults(func=cmd_adopt)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    for name, value in GLOBAL_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2

    out.QUIET = args.quiet
    if args.color != "auto":
        out.set_color(args.color == "always")
    if args.json:
        out.set_color(False)

    client = Client(use_cache=not args.no_cache, verbose=not args.quiet)
    try:
        return args.func(args, client)
    except RinthError as exc:
        out.fail(str(exc), getattr(exc, "hint", None))
        return 1
    except KeyboardInterrupt:
        out.fail("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
