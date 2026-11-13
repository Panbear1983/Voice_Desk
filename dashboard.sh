#!/bin/bash
# Voice Desk dashboard. Main screen = the voice grid, a wiring matrix (every agent x every place: here or not,
# the voice switch, plus what the logs say really happened). The footer shows only the keys that apply to the
# selected row or cell: T test · Shift-T test all · C check · E/Z voice pick-list · F effect (Simba) · A add agent
# · D remove agent · X here / not here · P activation phrase · U undo · W watcher · ? colours · M mute · H hush ·
# S listener screen (N new conversation · X hand the mic back · R restart · K kill · Esc back) · Q close panel.
# It never opens the mic and never speaks; closing it changes nothing.
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "missing .venv — run: uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python -r requirements.txt"; exit 1; }
exec .venv/bin/python dashboard.py
