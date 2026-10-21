#!/bin/bash
# Voice Desk always-on listener — normally started at login by launchd (see install_listener.sh).
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python listener.py
