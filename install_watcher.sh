#!/bin/bash
# Installs the Voice Desk watcher as a launchd job: one cheap check every 5 minutes (watcher.py once), no
# process stays alive in between. The watcher puts back anything that drifted from what the panel set
# (switches, agents.toml voices, Hermes local fixes) and messages Peter only when it cannot repair.
#
#   ./install_watcher.sh --print    show the job file; writes and loads nothing
#   ./install_watcher.sh            write ~/Library/LaunchAgents/com.peter.voice-desk-watcher.plist and load it
#   ./install_watcher.sh --remove   unload it and delete that file
# Pause without removing: W in ./dashboard.sh, or `python3 watcher.py pause`.
set -u
cd "$(dirname "$0")" || exit 1
label=com.peter.voice-desk-watcher
plist="$HOME/Library/LaunchAgents/$label.plist"
py="${VOICE_DESK_PYTHON:-$PWD/.venv/bin/python}"

body() {
cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$py</string><string>$PWD/watcher.py</string><string>once</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>StandardOutPath</key><string>$HOME/.voice_desk/watcher.log</string>
  <key>StandardErrorPath</key><string>$HOME/.voice_desk/watcher.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
</dict></plist>
PLIST
}

case "${1:-}" in
  --print) body; exit 0 ;;
  --remove)
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null
    rm -f "$plist" && echo "watcher removed ($label)"
    exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--print | --remove]" >&2; exit 2 ;;
esac

[ -x "$py" ] || { echo "no python at $py (make the .venv first)" >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.voice_desk"
body > "$plist" || exit 1
plutil -lint "$plist" >/dev/null || { echo "the job file is not valid: $plist" >&2; exit 1; }
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$plist" && echo "watcher installed and running ($label), checks every 5 minutes"
