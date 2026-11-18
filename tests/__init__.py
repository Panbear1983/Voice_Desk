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

# Which agents' own speaking scripts can play any bank voice (voicescripts.py) depends on files outside the project
# (Gemini's hook, the shared speaker at ~/GitHub/Voice_Desk/speak_any.py). The tests never read Peter's: Gemini's
# hook and the shared speaker point at places that do not exist, so every list is built as "not switched yet"
# unless a test sets them up itself.
os.environ["VOICE_DESK_GEMINI_HOOK"] = str(_tmp / "no-gemini-hook.py")
os.environ["VOICE_DESK_SPEAK_ANY"] = str(_tmp / "no-speak_any.py")

import contextlib  # noqa: E402
import sys  # noqa: E402

SWITCH_MARK = "voice-desk: any bank voice (shared speaker)"      # voicescripts.MARK


@contextlib.contextmanager
def scripts_switched(*scripts):
    """Inside: Gemini's hook (a stand-in) and every script file given carry the switch mark, and the shared speaker
    is in place, so every agent's own speaker counts as able to play any bank voice. Only throw-away files are
    written (the script files given must be the test's own)."""
    d = Path(tempfile.mkdtemp(prefix="vd-switched-"))
    hook = d / "speak-response.py"
    hook.write_text(f"# {SWITCH_MARK}\n")
    speaker = d / "speak_any.py"
    speaker.write_text("# a stand-in for the shared speaker\n")
    keys = ("VOICE_DESK_GEMINI_HOOK", "VOICE_DESK_SPEAK_ANY", "VOICE_DESK_SPEAK_PY")
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update(VOICE_DESK_GEMINI_HOOK=str(hook), VOICE_DESK_SPEAK_ANY=str(speaker),
                      VOICE_DESK_SPEAK_PY=sys.executable)
    for p in scripts:
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((p.read_text() if p.exists() else "") + f"\n# {SWITCH_MARK}\n")
    try:
        yield d
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
