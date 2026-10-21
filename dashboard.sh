#!/bin/bash
# Voice Desk dashboard: shows and controls the always-on listener (it never opens the mic).
# M mute · H hush · N new conversation · R restart listener · K kill listener · Q close panel
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "missing .venv — run: uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python -r requirements.txt"; exit 1; }
exec .venv/bin/python dashboard.py
