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

# A small fake voice bank (voicebank.py), so the pick-list tests never depend on what this Mac has installed.
# FAKE_KOKORO / FAKE_EDGE name what is in it.
import json  # noqa: E402
import zipfile  # noqa: E402

FAKE_KOKORO = ("af_alloy", "af_heart", "af_kore", "af_nicole", "am_fenrir", "am_onyx", "am_puck", "am_santa",
               "bf_emma", "bm_george", "jf_alpha", "zf_xiaobei")
FAKE_EDGE = (("en-US-AvaNeural", "en-US", "Female"), ("en-US-AriaNeural", "en-US", "Female"),
             ("en-US-JennyNeural", "en-US", "Female"), ("en-US-AndrewMultilingualNeural", "en-US", "Male"),
             ("en-US-EmmaNeural", "en-US", "Female"), ("en-GB-RyanNeural", "en-GB", "Male"),
             ("en-AU-NatashaNeural", "en-AU", "Female"), ("zh-TW-HsiaoChenNeural", "zh-TW", "Female"),
             ("zh-TW-HsiaoYuNeural", "zh-TW", "Female"), ("zh-TW-YunJheNeural", "zh-TW", "Male"),
             ("zh-CN-XiaoxiaoNeural", "zh-CN", "Female"), ("ja-JP-NanamiNeural", "ja-JP", "Female"))
_bank = _tmp / "voice-bank"
_bank.mkdir()
with zipfile.ZipFile(_bank / "voices-v1.0.bin", "w") as _z:
    for _n in FAKE_KOKORO:
        _z.writestr(f"{_n}.npy", b"\x00")
(_bank / "edge_voices.json").write_text(json.dumps({"fetched_at": "test", "voices": [
    {"ShortName": n, "Locale": loc, "Gender": g} for n, loc, g in FAKE_EDGE]}))
os.environ.setdefault("VOICE_DESK_KOKORO_BANKS", str(_bank))
os.environ.setdefault("VOICE_DESK_EDGE_LIST", str(_bank / "edge_voices.json"))
