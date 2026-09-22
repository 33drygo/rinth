# fish completions for rinth.

set -l rinth_cmds init search s versions v info install i add update u upgrade remove rm uninstall list ls sync adopt
set -l rinth_loaders bukkit bungeecord datapack fabric folia forge neoforge paper purpur quilt spigot velocity waterfall
set -l rinth_types plugin mod datapack shader resourcepack
set -l rinth_channels release beta alpha

# Projects already installed, read from the lock in the current directory.
function __rinth_installed
    set -l lock rinth.lock.json
    test -f $lock; or return
    python3 -c 'import json,sys
try:
    data = json.load(open("rinth.lock.json"))
except Exception:
    sys.exit()
for slug, entry in (data.get("entries") or {}).items():
    print(slug + "\t" + str(entry.get("version_number") or ""))' 2>/dev/null
end

# Minecraft versions, cached for a day so tab-completion does not hit the API.
function __rinth_mc_versions
    set -l cache "$HOME/.cache/rinth/game_versions.txt"
    if not test -f $cache; or test (find $cache -mtime +1 2>/dev/null | count) -gt 0
        mkdir -p (dirname $cache)
        curl -sf -A "rinth/completions" https://api.modrinth.com/v2/tag/game_version \
            | python3 -c 'import json,sys
try:
    tags = json.load(sys.stdin)
except Exception:
    sys.exit()
for tag in tags:
    if tag.get("version_type") == "release":
        print(tag["version"])' >$cache 2>/dev/null
    end
    test -f $cache; and cat $cache
end

complete -c rinth -f

# Subcommands
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a init -d "create a rinth.toml"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a "search s" -d "search for projects"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a "versions v" -d "version history"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a info -d "project summary"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a "install i add" -d "install projects"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a "update u upgrade" -d "update"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a "remove rm uninstall" -d "uninstall"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a "list ls" -d "what is installed"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a sync -d "rebuild the folder"
complete -c rinth -n "not __fish_seen_subcommand_from $rinth_cmds" -a adopt -d "adopt jars already present"

# Global flags
complete -c rinth -s h -l help -d "help"
complete -c rinth -s d -l dir -r -F -d "project directory"
complete -c rinth -l dry-run -d "write nothing"
complete -c rinth -s q -l quiet -d "errors only"
complete -c rinth -l json -d "JSON output"
complete -c rinth -l no-cache -d "ignore the cache"
complete -c rinth -l color -x -a "auto always never"
complete -c rinth -l version -d "rinth version"

# Loader and Minecraft version
complete -c rinth -n "__fish_seen_subcommand_from init search s versions v install i add" \
    -s l -l loader -x -a "$rinth_loaders" -d loader
complete -c rinth -n "__fish_seen_subcommand_from init search s versions v install i add" \
    -s m -l minecraft -x -a "(__rinth_mc_versions)" -d "Minecraft version"

# init
complete -c rinth -n "__fish_seen_subcommand_from init" -l into -r -F -d "jar subdirectory"
complete -c rinth -n "__fish_seen_subcommand_from init" -s f -l force -d "overwrite the existing one"

# search
complete -c rinth -n "__fish_seen_subcommand_from search s" -s t -l type -x -a "$rinth_types" -d type
complete -c rinth -n "__fish_seen_subcommand_from search s versions v" -s n -l limit -x -d "how many results"
complete -c rinth -n "__fish_seen_subcommand_from search s versions v" -s A -l all -d "no filters"

# install
complete -c rinth -n "__fish_seen_subcommand_from install i add" -s V -l version -x -d "specific version"
complete -c rinth -n "__fish_seen_subcommand_from install i add" -s c -l channel -x -a "$rinth_channels"
complete -c rinth -n "__fish_seen_subcommand_from install i add" -l no-deps -d "without dependencies"
complete -c rinth -n "__fish_seen_subcommand_from install i add" -l into -r -F -d "jar subdirectory"

# update / remove work on what is already installed
complete -c rinth -n "__fish_seen_subcommand_from update u upgrade remove rm uninstall" \
    -a "(__rinth_installed)"
complete -c rinth -n "__fish_seen_subcommand_from update u upgrade" -s f -l force -d "ignore pins"
complete -c rinth -n "__fish_seen_subcommand_from remove rm uninstall" -s y -l yes -d "do not ask"
complete -c rinth -n "__fish_seen_subcommand_from remove rm uninstall" -l keep-deps -d "leave dependencies"

# adopt
complete -c rinth -n "__fish_seen_subcommand_from adopt" -l pin -d "pin the version found"
