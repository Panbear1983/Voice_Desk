#!/bin/bash
# Agent Control Desk. It shows every agent's language model, voice, where it shows up and who it talks to, and
# (version two) lets you change the model, the backups and the voice from small pop-ups.
#   ./acd.sh            every change is a DRY RUN: shown in full, nothing is written
#   ./acd.sh --armed    changes are written, but only if the desk's own armed switch is also on:
#                         .venv/bin/python acd_edit.py arm | disarm | status
# It never restarts anything itself; R only leaves a request for the existing restart helper.
#   grid: up/down, Enter open the tree, A the voices of Claude, Gemini and Codex
#   tree: up/down, Enter change, left/right other agent, Esc back
#   U undo   R restart review   P pause / resume the watcher   C read again   Q close
# Version four: each service's sign-in and allowance, anything changed behind the desk's back (put it back or
# accept it), and the Voice Desk dashboard's own voice switches, place by place. The watcher alone:
#   .venv/bin/python acd_watch.py --once | status | pause | resume | auto on | auto off
# ACD_USAGE=off ./acd.sh   leaves out the allowance check (Hermes' own `hermes usage`, at most every 10 minutes).
# The project's Python lives in the main Voice Desk folder (.venv); a worktree borrows it.
cd "$(dirname "$0")" || exit 1
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$HOME/GitHub/Voice_Desk/.venv/bin/python"
[ -x "$PY" ] || { echo "missing .venv in ~/GitHub/Voice_Desk"; exit 1; }
exec "$PY" acd.py "$@"
