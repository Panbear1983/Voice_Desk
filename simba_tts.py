"""Simba's voice for Voice Desk: Kokoro (an American female voice) plus a scratchy "witch" effect.

Peter, Team room 2026-11-10: Simba is a girl cat with a scratchy, smoky witch voice, AMERICAN accent only,
made with Kokoro (the local engine Esther already uses). Which Kokoro voice and which effect are settings in
agents.toml ([agents.simba] voice / effect), changed from the panel (E and F keys); this file only knows how
to make them. Called by Voice Desk exactly like Esther's script:

    simba_tts.py <text file> <out file> <voice> [effect]

The out file's ending picks the format: .wav (the Mac's player), .mp3, or .ogg (Telegram voice bubble).
Exit 0 = the file is written; 2 = a voice or effect this script does not make (nothing written); 1 = Kokoro
or ffmpeg failed (Voice Desk then speaks the line with the agent's Edge backup voice).

Recipe (Jarvis's samples k_01..k_10, 2026-11-10): Kokoro at speed 0.92 with American English rules, then
ffmpeg. "smoky" = pitch down to 0.85 with tempo 1.12, light bit-crush, soft clip, faint pink noise. "raspy" =
pitch down to 0.78, 34 Hz tremolo, harder crush and clip, white-noise grit. "plain" = Kokoro as it is.

Only the two model files are read from the Kokoro folder (it sits in Peter's tax folder: never anything
else there). The model is loaded per call (about 2-4 s); Simba speaks rarely, so there is no warm service.
Importing this module loads nothing heavy: voiceedit uses VOICES / PRESETS to check what Peter types.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional

MODEL_DIR = Path(os.environ.get("SIMBA_KOKORO_DIR", "~/Desktop/Peter_Tax/tax_engine/data/voice")).expanduser()
MODEL, VOICES_BIN = "kokoro-v1.0.onnx", "voices-v1.0.bin"
FFMPEG = os.environ.get("SIMBA_FFMPEG", "/opt/homebrew/bin/ffmpeg")
SPEED = 0.92                       # the samples' Kokoro speed
LANG = "en-us"                     # American pronunciation, whatever the voice
DEFAULT_EFFECT = "smoky"

# The American female voices in voices-v1.0.bin (read from the file 2026-11-10). Peter: a girl cat, American
# accent only, so British (bf_), male (am_) and other languages are refused.
VOICES = ("af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
          "af_river", "af_sarah", "af_sky")

# The effects, as ffmpeg filter graphs. Input 0 is Kokoro's voice at {sr} Hz, input 1 the noise source.
PRESETS: dict[str, Optional[dict]] = {
    "plain": None,
    "smoky": {
        "noise": "anoisesrc=color=pink:amplitude=0.01:sample_rate=48000",
        "graph": ("[0]asetrate={sr}*0.85,aresample=48000,atempo=1.12,highpass=f=110,lowpass=f=4800,"
                  "acrusher=bits=10:mix=0.35:mode=log:aa=1,asoftclip=type=tanh:threshold=0.3:output=1.4,"
                  "aexciter=amount=2:freq=2200,acompressor=threshold=-26dB:ratio=5[v];"
                  "[v][1]amix=inputs=2:duration=first:weights=1 0.4,volume=1.1"),
    },
    "raspy": {
        "noise": "anoisesrc=color=white:amplitude=0.02:sample_rate=48000",
        "graph": ("[0]asetrate={sr}*0.78,aresample=48000,atempo=1.08,highpass=f=150,lowpass=f=5200,"
                  "tremolo=f=34:d=0.35,acrusher=bits=7:samples=2:mix=0.5:mode=lin,"
                  "asoftclip=type=hard:threshold=0.2:output=1.6,acompressor=threshold=-24dB:ratio=8[v];"
                  "[1]highpass=f=1500,volume=0.6[nn];[v][nn]amix=inputs=2:duration=first:weights=1 0.5,volume=1.2"),
    },
}

# Jarvis's numbered samples (sent to Peter on Telegram 2026-11-10): "sample 7" = these two settings.
SAMPLES = {1: ("af_nicole", "smoky"), 2: ("af_nicole", "raspy"), 3: ("af_kore", "smoky"), 4: ("af_kore", "raspy"),
           5: ("af_river", "smoky"), 6: ("af_river", "raspy"), 7: ("af_jessica", "smoky"), 8: ("af_jessica", "raspy"),
           9: ("af_nova", "smoky"), 10: ("af_nova", "raspy")}

# A little silence in front so a waking speaker does not swallow the first word (as Esther's script does).
ENCODE = {"wav": ["-af", "adelay=200:all=1", "-c:a", "pcm_s16le", "-f", "wav"],
          "mp3": ["-af", "adelay=200:all=1", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"],
          "ogg": ["-af", "adelay=400:all=1", "-c:a", "libopus", "-b:a", "40k", "-application", "voip", "-f", "ogg"]}


def clean_voice(name: str) -> str:
    """'Nicole', 'nicole' or 'af_nicole' -> 'af_nicole' (unchecked)."""
    v = (name or "").strip().lower()
    return v if "_" in v else f"af_{v}" if re.fullmatch(r"[a-z]+", v) else v


def voice_problem(name: str) -> Optional[str]:
    """None if this script speaks with that voice, else a sentence saying why not."""
    if clean_voice(name) in VOICES:
        return None
    return (f"Simba's voice must be an American female Kokoro voice ({', '.join(VOICES)}); got {name!r}.")


def effect_problem(name: str) -> Optional[str]:
    if (name or "").strip().lower() in PRESETS:
        return None
    return f"The voice effect must be one of {', '.join(PRESETS)}; got {name!r}."


def ffmpeg_args(effect: str, sr: int, src: Path, dst: Path, fmt: str) -> list[str]:
    """The whole ffmpeg command that turns Kokoro's plain voice (src, sr Hz) into the effect, as fmt."""
    preset = PRESETS[effect]
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    enc = list(ENCODE[fmt])
    if preset is not None:
        args += ["-f", "lavfi", "-i", preset["noise"]]
        graph = preset["graph"].format(sr=sr) + "," + enc[1]       # the lead silence goes on the end of the graph
        args += ["-filter_complex", graph]
        enc = enc[2:]
    return args + ["-ac", "1", *enc, str(dst)]


def kokoro_wav(text: str, voice: str, dst: Path) -> int:
    """Kokoro's plain voice into a 16-bit mono wav; returns its sample rate."""
    import numpy as np
    from kokoro_onnx import Kokoro
    k = Kokoro(str(MODEL_DIR / MODEL), str(MODEL_DIR / VOICES_BIN))
    samples, rate = k.create(text, voice=voice, speed=SPEED, lang=LANG)
    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return int(rate)


def make(text: str, out: Path, voice: str, effect: str = DEFAULT_EFFECT) -> int:
    """Write Simba's line to out. Returns the exit code (see the top of the file)."""
    voice, effect = clean_voice(voice), (effect or DEFAULT_EFFECT).strip().lower()
    for problem in (voice_problem(voice), effect_problem(effect)):
        if problem:
            print(problem, file=sys.stderr)
            return 2
    if not text.strip():
        print("nothing to say", file=sys.stderr)
        return 2
    fmt = out.suffix.lower().lstrip(".")
    fmt = fmt if fmt in ENCODE else "wav"
    with tempfile.TemporaryDirectory(prefix="simba-tts-") as tmp:
        raw = Path(tmp) / "kokoro.wav"
        made = Path(tmp) / f"simba.{fmt}"
        try:
            sr = kokoro_wav(text, voice, raw)
            r = subprocess.run(ffmpeg_args(effect, sr, raw, made, fmt), capture_output=True, timeout=120)
        except Exception as exc:                       # Kokoro or ffmpeg missing/broken: Voice Desk uses the backup
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        if r.returncode != 0 or not made.exists() or made.stat().st_size == 0:
            print(r.stderr.decode(errors="replace")[-500:], file=sys.stderr)
            return 1
        shutil.move(str(made), str(out))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: simba_tts.py <text file> <out file> <voice> [effect]", file=sys.stderr)
        return 2
    src, out = Path(argv[0]), Path(argv[1])
    voice = argv[2]
    effect = argv[3] if len(argv) > 3 else DEFAULT_EFFECT
    return make(src.read_text(encoding="utf-8"), out, voice, effect)


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # os._exit, not sys.exit: onnxruntime sometimes aborts while Python tears down (seen 2026-11-10: the file was
    # written, then "recursive_mutex lock failed", exit 134), and Voice Desk would throw the good file away.
    os._exit(code)
