#!/bin/bash
# Agent Control Desk, version one. It shows every agent's language model, voice, where it shows up and who it
# talks to. It only reads files: it changes nothing, restarts nothing and calls no model. Closing it changes nothing.
#   up/down choose an agent   Enter open the agent's tree   Esc back   C read again   Q close
# The project's Python lives in the main Voice Desk folder (.venv); a worktree borrows it.
cd "$(dirname "$0")" || exit 1
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$HOME/GitHub/Voice_Desk/.venv/bin/python"
[ -x "$PY" ] || { echo "missing .venv in ~/GitHub/Voice_Desk"; exit 1; }
exec "$PY" acd.py
