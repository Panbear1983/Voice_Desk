"""Every Voice Desk test runs against fakes, never Peter's real windows.

Loaded before any test module (python -m unittest tests.x), so settings.py — whichever module
imports it first — sees a throw-away state folder and a herdr that reports no open windows.
2026-10-25: without this, the run order decided which herdr the tests used, and a test run typed
"[voice] question" into Peter's real Codex window and "[voice] hello" into his Claude session."""
import os
import stat
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="vd-tests-"))
_herdr = _tmp / "no-windows-herdr"
_herdr.write_text('#!/bin/sh\ncase "$1 $2" in\n  "pane list") echo \'{"result": {"panes": []}}\' ;;\nesac\nexit 0\n')
_herdr.chmod(_herdr.stat().st_mode | stat.S_IXUSR)
os.environ.setdefault("VOICE_DESK_STATE", str(_tmp / "state"))
os.environ["VOICE_DESK_HERDR_BIN"] = str(_herdr)
os.environ["VOICE_DESK_CLAUDE_PROJECTS"] = str(_tmp / "projects")
os.environ["VOICE_DESK_GEMINI_BRAIN"] = str(_tmp / "gemini-brain")
os.environ["VOICE_DESK_CODEX_SESSIONS"] = str(_tmp / "codex-sessions")
