"""The shared speaker: make ONE line with exactly the voice it is given, any voice in the voice bank, or nothing.

    speak_any.py <text file> <out file> <voice> [speed]

Peter 2026-11-18 ("yes" to the full voice list for every agent; brief 2026-11-18-acd-full-voice-list.md, Phase B):
an agent's own speaking script (Gemini's hook, Jarvis's and Esther's Kokoro scripts, Simba's simba_tts.py) hands a
voice it does not make itself to this file, so every agent can speak with any voice in the bank (voicebank.py):

    an English Kokoro voice (af_ / am_ American, bf_ / bm_ British)   made by the shared Kokoro script, local
                                                                       (agents.KOKORO_COMMAND: kokoro_say.py)
    a Microsoft voice (en-US-AvaNeural, zh-TW-HsiaoYuNeural, ...)     asked of Microsoft for exactly that name

No fallback, ever (Peter 2026-11-11, brief decision 3): if that one voice cannot be made the out file is not written
and the exit code says why. Nothing here tries a second voice, Piper, Google or the Mac's own voice, and nothing is
ever played: the caller plays (or sends) the file.

The out file's ending picks the format: .mp3, .wav or .ogg (Ogg/Opus, a Telegram voice bubble); anything else is
written as .mp3 data. speed (0.5 - 2.0, default 1.0) is applied after the voice is made (ffmpeg atempo), the same way
Jarvis's script applies his 1.2x.

Exit 0 = the out file is written and not empty; 2 = nothing to make (empty text, or a name that is not a bank voice
this speaker can make, or a speed out of range); 1 = the voice engine failed (no internet, Kokoro broke ...).

Light on purpose: standard library plus agents.py (for the Kokoro script's place). It does not import settings.py,
so running it never touches ~/.voice_desk. Run it with Voice Desk's own Python (.venv/bin/python, 3.11+).
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agents  # noqa: E402  (tomllib only)

EDGE_NAME = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural$")
KOKORO_NAME = re.compile(r"^[ab][fm]_[a-z]+$")
NEWHOST = Path(os.environ.get("VOICE_DESK_EDGE_NEWHOST", Path.home() / ".local/bin/edge_tts_newhost.py"))
EDGE_PYTHONS = ("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3", "/usr/local/bin/python3")
FFMPEG_PLACES = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
# Same encodings as Esther's and Simba's scripts: a little silence in front so a waking speaker keeps the first word.
ENCODE = {"wav": ["-af", "adelay=200:all=1", "-c:a", "pcm_s16le", "-f", "wav"],
          "mp3": ["-af", "adelay=200:all=1", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"],
          "ogg": ["-af", "adelay=400:all=1", "-c:a", "libopus", "-b:a", "48k", "-application", "voip", "-f", "ogg"]}
SPEED_RANGE = (0.5, 2.0)

Runner = Callable[..., subprocess.CompletedProcess]


def engine_of(voice: str) -> str:
    """"kokoro", "edge", or "" (not a voice this speaker makes)."""
    v = (voice or "").strip()
    if KOKORO_NAME.match(v) and agents.is_kokoro(v):
        return "kokoro"
    if EDGE_NAME.match(v):
        return "edge"
    return ""


def can_make(voice: str) -> str:
    """"" when this speaker makes that voice, else a plain sentence saying why not."""
    if engine_of(voice):
        return ""
    return (f"{voice!r} is not a voice the shared speaker makes: it takes an English Kokoro voice that is installed "
            "(af_heart, bm_george ...) or a Microsoft voice name (en-US-AvaNeural ...)")


def ffmpeg() -> Optional[str]:
    return next((p for p in FFMPEG_PLACES if os.access(p, os.X_OK)), None) or shutil.which("ffmpeg")


_edge_py: Optional[str] = None


def edge_python(run: Runner = subprocess.run) -> Optional[str]:
    """A Python with edge-tts (the same search Voice Desk's tts.py and Hermes' voice script do), found once."""
    global _edge_py
    if _edge_py is None:
        found = ""
        cands = [os.environ.get("VOICE_DESK_EDGE_PY", ""), *EDGE_PYTHONS,
                 *sorted(glob.glob(str(Path.home() / ".hermes/installs/*/environments/*/venv/bin/python")))]
        for c in cands:
            if c and Path(c).exists():
                try:
                    if run([c, "-c", "import edge_tts"], capture_output=True, timeout=30).returncode == 0:
                        found = c
                        break
                except (OSError, subprocess.TimeoutExpired):
                    continue
        _edge_py = found
    return _edge_py or None


def _good(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


def _edge(txt: Path, out_mp3: Path, voice: str, run: Runner) -> bool:
    """Exactly this Microsoft voice, two tries (one hiccup is retried with the SAME voice, never another)."""
    py = edge_python(run)
    if not py or not NEWHOST.exists():
        return False
    for _ in range(2):
        try:
            r = run([py, str(NEWHOST), "--voice", voice, "--file", str(txt), "--write-media", str(out_mp3)],
                    capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and _good(out_mp3):
            return True
    return False


def _kokoro(txt: Path, out_wav: Path, voice: str, run: Runner) -> bool:
    """Exactly this Kokoro voice, made by the shared Kokoro script (local, no internet)."""
    cmd = agents.KOKORO_COMMAND
    if not all(Path(c).exists() for c in cmd):
        return False
    try:
        r = run([*cmd, str(txt), str(out_wav), voice], capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and _good(out_wav)


def _finish(raw: Path, out: Path, speed: float, run: Runner) -> bool:
    """raw -> out in the format out's ending asks for, at the speed asked for."""
    fmt = out.suffix.lower().lstrip(".")
    fmt = fmt if fmt in ENCODE else "mp3"
    if speed == 1.0 and raw.suffix.lower().lstrip(".") == fmt:
        shutil.copyfile(raw, out)
        return _good(out)
    ff = ffmpeg()
    if not ff:
        return False
    enc = list(ENCODE[fmt])
    if speed != 1.0:
        enc[1] = f"atempo={speed:g}," + enc[1]
    made = out.with_name(out.name + ".part")
    try:
        r = run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-ac", "1", *enc, str(made)],
                capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        made.unlink(missing_ok=True)
        return False
    if r.returncode != 0 or not _good(made):
        made.unlink(missing_ok=True)
        return False
    os.replace(made, out)
    return True


def make(text_file: Path, out: Path, voice: str, speed: float = 1.0, run: Runner = subprocess.run) -> int:
    """Write the line to out with exactly `voice`. Returns the exit code (see the top of the file)."""
    voice = (voice or "").strip()
    engine = engine_of(voice)
    if not engine:
        print(can_make(voice), file=sys.stderr)
        return 2
    if not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]:
        print(f"speed must be between {SPEED_RANGE[0]:g} and {SPEED_RANGE[1]:g}; got {speed:g}", file=sys.stderr)
        return 2
    try:
        text = Path(text_file).read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        print(f"cannot read the text: {exc}", file=sys.stderr)
        return 2
    if not text:
        print("nothing to say", file=sys.stderr)
        return 2
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="speak-any-") as tmp:
        txt = Path(tmp) / "say.txt"
        txt.write_text(text, encoding="utf-8")
        raw = Path(tmp) / ("voice.wav" if engine == "kokoro" else "voice.mp3")
        made = _kokoro(txt, raw, voice, run) if engine == "kokoro" else _edge(txt, raw, voice, run)
        if not made:
            print(f"{voice} could not be made ({engine}); the line stays silent, no other voice is tried",
                  file=sys.stderr)
            return 1
        if not _finish(raw, out, speed, run):
            print("the audio could not be converted; the line stays silent", file=sys.stderr)
            return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: speak_any.py <text file> <out file> <voice> [speed]", file=sys.stderr)
        return 2
    speed = 1.0
    if len(argv) > 3 and argv[3].strip():
        try:
            speed = float(argv[3])
        except ValueError:
            print(f"speed must be a number; got {argv[3]!r}", file=sys.stderr)
            return 2
    return make(Path(argv[0]), Path(argv[1]), argv[2], speed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
