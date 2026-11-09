#!/bin/bash
# Voice Desk dashboard. Main screen = the voice grid (every agent x every place: the setting, plus what the
# logs say really happened). T silent voice test · E/Z change a voice · U undo · M mute · H hush · S listener
# screen (N new conversation · V voices · X hand the mic back · R restart · K kill) · Q close panel.
# It never opens the mic and never speaks; closing it changes nothing.
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "missing .venv — run: uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python -r requirements.txt"; exit 1; }
exec .venv/bin/python dashboard.py
