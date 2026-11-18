"""Silent proof that each agent's voice really comes out of its own script (brief 2026-11-18-acd-full-voice-list.md,
"Done means": render each voice to a temp file, ffprobe + whisper transcript; nothing plays).

    .venv/bin/python tools/silent_voice_render.py --out <folder> [--speaker <path to speak_any.py>]

Two groups:
  today   every agent's CURRENT voice, made the way it is made today (its own script, its hook's own Microsoft call,
          the shared Kokoro script, or Microsoft by name): the voice nobody changed must still come out;
  new     voices the agents could not speak before Phase B, made through their own (switched) scripts and the shared
          speaker given with --speaker (by default this checkout's speak_any.py, so it can be checked before the
          merge puts it at ~/GitHub/Voice_Desk/speak_any.py);
  silent  a voice that does not exist: the script must fail and write nothing (no other voice).

Each file is checked with ffprobe and transcribed by the local whisper-server (127.0.0.1:8178, the one Voice Desk's
listener keeps loaded; only read, never started or stopped). Nothing is ever played. Scripts that would write to a
live log run with a throw-away HOME whose voice folders point at the real ones. Esther's own voice (alloy) is not
rendered here: her script would start her warm voice service in the background, which is a live process.
Results: <out>/render.json and one line per voice on the screen.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
SENTENCE = "Hello Peter, this is a quiet voice check for the full voice list."
WHISPER = os.environ.get("VOICE_DESK_WHISPER_URL", "http://127.0.0.1:8178")
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
TAX_PY = HOME / "Desktop/Peter_Tax/tax_engine/venv/bin/python"
KOKORO = (HOME / ".local/share/claude-voice/venv/bin/python", HOME / ".claude/hooks/kokoro_say.py")
JARVIS = HOME / ".hermes/profiles/orchestrator/scripts/tts_jarvis_local.sh"
ESTHER = HOME / ".hermes/profiles/tax_angel_esther/scripts/esther_tts.py"
GEMINI = HOME / ".gemini/config/hooks/speak-response.py"
GEMINI_PY = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"

GEMINI_PROBE = r'''import asyncio, importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("gemini_render", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
text, voice, out = sys.argv[2], sys.argv[3], Path(sys.argv[4])
if m._EDGE_VOICE.match(voice):
    ok = asyncio.run(m.synthesize_edge_tts(text, voice, out))
else:
    ok = m.shared_speaker(text, voice, out)
sys.exit(0 if ok else 1)
'''


def fake_home(root: Path) -> Path:
    """A HOME whose voice folders are the real ones (read), so a script's own log lines land here, not live."""
    h = root / "home"
    for rel in (".local/share/claude-voice", ".claude/hooks", ".local/bin/edge_tts_newhost.py",
                "Library/Python"):                  # edge-tts lives in the user's own site-packages
        (h / rel).parent.mkdir(parents=True, exist_ok=True)
        if not (h / rel).exists():
            (h / rel).symlink_to(HOME / rel)
    return h


def probe(path: Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_name,sample_rate",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        s = (d.get("streams") or [{}])[0]
        return {"codec": s.get("codec_name"), "rate": s.get("sample_rate"),
                "seconds": round(float(d["format"]["duration"]), 2)}
    except (ValueError, KeyError, IndexError):
        return {"error": r.stderr.strip()[-200:]}


def transcribe(path: Path) -> str:
    wav = path.with_suffix(".16k.wav")
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(path), "-ar", "16000", "-ac", "1", str(wav)],
                   capture_output=True)
    b = "----voicecheck"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + wav.read_bytes() + (
        f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n--{b}--\r\n").encode()
    req = urllib.request.Request(WHISPER + "/inference", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return " ".join(json.loads(r.read().decode()).get("text", "").split())


def words(t: str) -> set[str]:
    return set(re.findall(r"[a-z]+", t.lower()))


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--speaker", type=Path, default=REPO / "speak_any.py")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="silent-render-"))
    home = fake_home(root)
    txt = root / "say.txt"
    txt.write_text(SENTENCE, encoding="utf-8")
    py = str(REPO / ".venv/bin/python")
    shared = {"VOICE_DESK_SPEAK_ANY": str(a.speaker), "VOICE_DESK_SPEAK_PY": py, "PYTHONDONTWRITEBYTECODE": "1"}
    simba = [str(TAX_PY), str(REPO / "simba_tts.py")]
    cases = [
        # (group, who, voice, command, out suffix, extra env, use the throw-away HOME)
        ("today", "Jarvis", "am_puck", ["/bin/bash", str(JARVIS)], "mp3", {}, True),
        ("today", "Simba", "am_fenrir", simba + ["{in}", "{out}", "am_fenrir", "16 pitch=1.4"], "ogg", {}, False),
        ("today", "Gemini", "en-US-AriaNeural", [GEMINI_PY, "-c", GEMINI_PROBE, str(GEMINI), SENTENCE,
                                                 "en-US-AriaNeural", "{out}"], "mp3", {}, True),
        ("today", "Claude", "af_heart", [str(KOKORO[0]), str(KOKORO[1])], "wav", {}, False),
        ("today", "Kylie", "en-US-AvaNeural", [py, str(a.speaker)], "mp3", {}, False),
        ("today", "Wanna Buffet", "am_santa", [str(KOKORO[0]), str(KOKORO[1])], "wav", {}, False),
        ("today", "Codex", "en-GB-RyanNeural", [py, str(a.speaker)], "mp3", {}, False),
        ("new", "Jarvis", "en-US-AvaNeural", ["/bin/bash", str(JARVIS)], "mp3", shared, True),
        ("new", "Esther", "am_adam", [str(TAX_PY), str(ESTHER)], "ogg", shared, True),
        ("new", "Esther", "en-GB-RyanNeural", [str(TAX_PY), str(ESTHER)], "ogg", shared, True),
        ("new", "Gemini", "bm_george", [GEMINI_PY, "-c", GEMINI_PROBE, str(GEMINI), SENTENCE, "bm_george", "{out}"],
         "mp3", shared, True),
        ("new", "Simba", "en-US-AvaNeural", simba + ["{in}", "{out}", "en-US-AvaNeural", "16 pitch=1.4"], "ogg",
         shared, False),
        ("new", "Simba", "bf_emma", simba + ["{in}", "{out}", "bf_emma", "16 pitch=1.4"], "ogg", shared, False),
        ("silent", "Jarvis", "en-US-NoSuchVoiceNeural", ["/bin/bash", str(JARVIS)], "mp3", shared, True),
        ("silent", "Esther", "en-US-NoSuchVoiceNeural", [str(TAX_PY), str(ESTHER)], "ogg", shared, True),
    ]
    results = []
    for group, who, voice, cmd, suffix, extra, sandboxed in cases:
        out = a.out / f"{group}_{who.lower().replace(' ', '_')}_{voice}.{suffix}"
        out.unlink(missing_ok=True)
        if "{out}" in cmd:
            argv = [c.replace("{in}", str(txt)).replace("{out}", str(out)) for c in cmd]
        else:
            argv = [*cmd, str(txt), str(out), voice]
        env = {**os.environ, **extra}
        env.pop("VOICE_DESK_LOOP", None)
        if sandboxed:
            env["HOME"] = str(home)
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=env)
            rc, err = r.returncode, r.stderr.strip()[-300:]
        except subprocess.TimeoutExpired:
            rc, err = -1, "timed out"
        row = {"group": group, "who": who, "voice": voice, "exit": rc, "file": str(out),
               "written": out.exists() and out.stat().st_size > 0}
        if row["written"]:
            row.update(probe(out))
            try:
                row["heard"] = transcribe(out)
            except Exception as exc:                     # noqa: BLE001
                row["heard"] = f"(whisper failed: {exc})"
            row["words_matched"] = f"{len(words(row['heard']) & words(SENTENCE))}/{len(words(SENTENCE))}"
        elif err:
            row["stderr"] = err
        if group == "silent":
            row["pass"] = rc != 0 and not row["written"]
        else:
            row["pass"] = rc == 0 and row["written"] and len(words(row.get("heard", "")) & words(SENTENCE)) >= 8
        results.append(row)
        print(f"{'PASS' if row['pass'] else 'FAIL'}  {group:6} {who:12} {voice:24} exit {rc}  "
              + (f"{row.get('codec')} {row.get('seconds')} s  heard: {row.get('heard', '')!r}" if row["written"]
                 else f"nothing written {row.get('stderr', '')[-120:]!r}"))
    (a.out / "render.json").write_text(json.dumps({"sentence": SENTENCE, "results": results}, indent=1))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
