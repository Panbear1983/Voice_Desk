#!/bin/bash
# Installs the permission pop-up watcher as a launchd job (com.peter.permission-watch): one pass every 30 s.
# Touches no other launchd job.
#
#   ./install.sh --print    show the filled-in job file; writes and loads nothing
#   ./install.sh            write ~/Library/LaunchAgents/com.peter.permission-watch.plist and load it
#   ./install.sh --remove   unload it and delete that file
set -u
cd "$(dirname "$0")" || exit 1
label=com.peter.permission-watch
repo=$(cd ../.. && pwd)
dst="$HOME/Library/LaunchAgents/$label.plist"
py="$repo/.venv/bin/python"

body() { sed -e "s#@REPO@#$repo#g" -e "s#@HOME@#$HOME#g" "$label.plist"; }

case "${1:-}" in
  --print) body; exit 0 ;;
  --remove)
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null
    rm -f "$dst" && echo "permission watcher removed ($label)"
    exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--print | --remove]" >&2; exit 2 ;;
esac

[ -x "$py" ] || { echo "no python at $py (make the repo .venv first)" >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/state/permission_watch"
body > "$dst" || exit 1
plutil -lint "$dst" >/dev/null || { echo "the job file is not valid: $dst" >&2; exit 1; }
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$dst" || exit 1
echo "permission watcher installed and running ($label), checks every 30 seconds"
echo "for screenshots, give Screen Recording to: $(realpath "$py" 2>/dev/null || echo "$py")"
