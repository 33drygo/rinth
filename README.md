# rinth

A CLI package manager for [Modrinth](https://modrinth.com) projects, aimed at the
`plugins/` folder of a Minecraft server but equally happy with mods, shaders and
resource packs. Declarative manifest, reproducible lockfile, dependency
resolution. Python stdlib only: it pulls in nothing.

It exists because the tools in the ecosystem target *mods*: `ferium` only accepts
fabric/forge/quilt/neoforge, `mrpack-install` is for server modpacks and `packwiz`
is for authoring them.

## Usage

```fish
rinth init -l paper -m 1.21.4 --into plugins
rinth search economy -t plugin
rinth install luckperms vault
rinth versions luckperms -l paper
rinth list
rinth update
```

| Command | Alias | What it does |
|---|---|---|
| `init` | | creates `rinth.toml` |
| `search <text>` | `s` | search projects (`-t plugin\|mod\|datapack\|shader\|resourcepack`) |
| `versions <project>` | `v` | version history |
| `info <project>` | | project summary |
| `install <project...>` | `i`, `add` | install, along with `required` dependencies |
| `update [project...]` | `u` | move up to the latest compatible; pins are respected |
| `remove <project...>` | `rm` | uninstall and prune orphaned dependencies |
| `list` | `ls` | what is installed and in what state |
| `sync` | | rebuild the folder from the manifest |
| `adopt` | | identify jars you already had, by hash |

Global flags, accepted before or after the subcommand: `-d/--dir`, `--dry-run`,
`-q`, `--json`, `--no-cache`, `--color`.

## Files

`rinth.toml` is yours to edit. rinth modifies it line by line, so your comments
and your ordering survive:

```toml
[server]
loader = "paper"
minecraft = "1.21.4"
dir = "plugins"

[plugins]
luckperms = "*"                                   # latest compatible
vault = "1.7.3"                                   # pinned
miniplaceholders = { version = "*", channel = "beta" }
```

`rinth.lock.json` is generated: exact version, sha512, filename, and why each
entry is there. Do not edit it.

## Details that matter

- **Loader fallback.** Plenty of plugins only publish for `spigot` and run fine on
  Paper. When there is no build for your loader, rinth walks the chain
  (`paper → spigot → bukkit`, `waterfall → bungeecord`, `quilt → fabric`) and
  tells you which one it used. Velocity does not inherit from BungeeCord: they
  are different APIs.
- **One version per platform.** LuckPerms publishes `v5.5.71-bukkit` and
  `-velocity` separately, so `-V` by number can be ambiguous; rinth says so and
  hands you the exact ids.
- **The API order is not reliable**, so versions are always sorted by publication
  date.
- **A jar that is not ours is never deleted**: only the file the lock recorded.
- **Everything is sha512-verified** before being moved into place, and the
  download is atomic.

## Installing

```fish
cd ~/src/rinth
makepkg -fi
```

Or without packaging: `python3 -m rinth --help` from this directory.

## Tests

```fish
python3 -m unittest discover -s tests -t .
```

They never touch the network: real responses are recorded in `tests/fixtures/`.
