"""Render actual Textual views with recorded Codex test replies; no mic or model calls.

  .venv/bin/python docs/codex_screenshots.py
  python3 docs/render_images.py --only 08_codex_window 09_codex_dashboard --no-gif

These are headless UI replays, not photographs of a live microphone or iPhone session.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Always isolate replay state, even when invoked from a running Voice Desk environment.
os.environ["VOICE_DESK_STATE"] = tempfile.mkdtemp(prefix="vd-codex-shots-")
# Documentation exports need the real UI palette even under a colourless CI shell.
os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"

import engine
import service
import settings as S
from app import VoiceDesk
from dashboard import Dashboard
from textual.widgets import Static

OUT = ROOT / "docs/img"
DEMO = json.loads((ROOT / "docs/codex_demo.json").read_text())


async def shoot():
    OUT.mkdir(parents=True, exist_ok=True)
    with patch.object(engine.Engine, "start", lambda self: None), \
         patch.object(S, "CODEX_BIN", sys.executable):  # availability only; never executed
        window = VoiceDesk()
        window.engine.brain.set_target("codex")
        async with window.run_test(size=(100, 24)) as pilot:
            for turn in DEMO["turns"]:
                window._apply("you", turn["you"])
                window._apply("claude", turn["codex"])  # existing generic reply event
            window._apply("state", "speaking")
            await pilot.pause(0.3)
            assert "Codex" in str(window.query_one("#mode", Static).render())
            (OUT / "08_codex_window.svg").write_text(window.export_screenshot(), encoding="utf-8")

        last = DEMO["turns"][-1]
        snapshot = {"target": "codex", "name": "Codex", "state": "speaking", "level": 0,
                    "heard": last["you"], "reply": last["codex"], "turns": len(DEMO["turns"]),
                    "updated": time.time(), "since": time.time(),
                    "note": "Recorded conversation replay: the second question resumed the same Codex thread."}
        with patch.object(service, "read_status", return_value=snapshot), \
             patch.object(service, "is_muted", return_value=False):
            dashboard = Dashboard()
            async with dashboard.run_test(size=(110, 28)) as pilot:
                await pilot.pause(0.3)
                assert "Codex" in str(dashboard.query_one("#who", Static).render())
                assert last["codex"] in str(dashboard.query_one("#body", Static).render())
                assert "Codex:" in str(dashboard.query_one("#voices", Static).render())
                (OUT / "09_codex_dashboard.svg").write_text(dashboard.export_screenshot(), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(shoot())
    print("Rendered Codex window and dashboard from recorded test replies; no microphone or model used.")
