#!/bin/bash
# Voice Desk always-on listener — normally started at login by launchd (see install_listener.sh).
cd "$(dirname "$0")" || exit 1
# Peter 2026-10-22: log every ignored sentence while we chase missed "Hey Jarvis" wake-ups. Remove when done.
export VOICE_DESK_LISTENER_DEBUG=1
exec .venv/bin/python listener.py
