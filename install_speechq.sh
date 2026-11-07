#!/bin/bash
# Puts the speech line (speechq.py, "the train station") in front of every Hermes window's voice:
# a small stand-in named afplay in ~/bin, which comes before /usr/bin on those windows' PATH, so
# each spoken sentence waits its turn instead of talking over another agent. Re-run any time.
#   ./install_speechq.sh            install (or refresh) the stand-in
#   ./install_speechq.sh --remove   take it away again (every afplay plays straight through)
# Quicker off switch without removing anything: touch ~/.voice_desk/speech/off
# Already-open Hermes windows pick it up at once (they look afplay up on PATH for every sentence).
set -u
cd "$(dirname "$0")" || exit 1
stand="$HOME/bin/afplay"
marker="# voice-desk speech line (install_speechq.sh)"
ours() { [ -f "$stand" ] && grep -qF "$marker" "$stand"; }

if [ "${1:-}" = "--remove" ]; then
  if [ ! -e "$stand" ]; then echo "speech line not installed"; exit 0; fi
  if ! ours; then echo "$stand is not Voice Desk's; left alone" >&2; exit 1; fi
  rm -f "$stand" && echo "speech line removed (afplay plays straight through again)"
  exit 0
fi

if [ -e "$stand" ] && ! ours; then
  echo "$stand already exists and is not Voice Desk's; left alone" >&2
  exit 1
fi
py="${VOICE_DESK_PYTHON:-$PWD/.venv/bin/python}"
if [ ! -x "$py" ]; then echo "no python at $py (make the .venv first)" >&2; exit 1; fi
mkdir -p "$HOME/bin" "$HOME/.voice_desk/speech"
tmp="$stand.$$.tmp"
cat > "$tmp" <<STAND
#!/bin/sh
$marker
# Every sentence a Hermes window speaks comes through here: speechq.py waits its turn, then plays
# it with the real /usr/bin/afplay. Voice Desk moved or broken -> plain afplay, never silence.
if [ -x "$py" ] && [ -f "$PWD/speechq.py" ]; then
  exec "$py" "$PWD/speechq.py" afplay "\$@"
fi
exec "\${VOICE_DESK_REAL_AFPLAY:-/usr/bin/afplay}" "\$@"
STAND
chmod 755 "$tmp" && mv -f "$tmp" "$stand" || { rm -f "$tmp"; echo "could not write $stand" >&2; exit 1; }
# "Kylie here." in each agent's own voice, made now so the first one is instant
[ "${VOICE_DESK_SPEECHQ_NO_WARM:-}" = "1" ] || "$py" speechq.py warm >/dev/null 2>&1 &
echo "speech line installed ($stand -> $PWD/speechq.py)"
