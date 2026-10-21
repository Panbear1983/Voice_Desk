#!/bin/bash
# Installs the always-on listener as a login job (launchd): the Mac listens for a name
# ("Jarvis, ...", "hey Kylie", "Esther, ...", "Wanna Buffet, ...", "hey Claude") and answers.
# Re-run any time; it reloads.  --no-load writes the job without starting it.
set -u
cd "$(dirname "$0")" || exit 1
label=com.peter.voice-desk-listener
plist="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$HOME/Library/LaunchAgents"
# the app's folder used to be ~/.claude_voice; carry it over (threads, phone token, transcript) rather than start empty
[ -d "$HOME/.claude_voice" ] && [ ! -e "$HOME/.voice_desk" ] && mv "$HOME/.claude_voice" "$HOME/.voice_desk"
mkdir -p "$HOME/.voice_desk"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$PWD/start_listener.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>StandardOutPath</key><string>$HOME/.voice_desk/listener.log</string>
  <key>StandardErrorPath</key><string>$HOME/.voice_desk/listener.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string>
    <key>HOME</key><string>$HOME</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && sleep 3     # bootstrap right after bootout fails with an I/O error
if [ "${1:-}" = "--no-load" ]; then
  echo "listener installed but not started ($plist). Start it with: launchctl bootstrap gui/$(id -u) $plist"
  exit 0
fi
launchctl bootstrap "gui/$(id -u)" "$plist" && echo "listener installed and running ($label); ./dashboard.sh shows it"
