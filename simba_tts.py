"""Simba's voice for Voice Desk: Kokoro (an American voice) plus a scratchy "witch" effect.

Peter, Team room 2026-11-10: Simba is a girl cat with a scratchy, smoky witch voice, AMERICAN accent only,
made with Kokoro (the local engine Esther already uses). Which Kokoro voice and which effect are settings in
agents.toml ([agents.simba] voice / effect), changed from the panel (E and F keys); this file only knows how
to make them. Called by Voice Desk exactly like Esther's script:

    simba_tts.py <text file> <out file> <voice> [effect]

The out file's ending picks the format: .wav (the Mac's player), .mp3, or .ogg (Telegram voice bubble).
Exit 0 = the file is written; 2 = a voice or effect this script does not make (nothing written); 1 = Kokoro
or ffmpeg failed (Voice Desk then speaks the line with the agent's Edge backup voice).

The effect is a short recipe, written the way Peter picks samples:

    16                              Jarvis's sample 16 as it was sent (also "fenrir16", the default)
    23 but slower and breathier     sample 23, then slower (tempo x0.9) and breathier (noise 0.03)
    23 26                           sample 23 plus what made sample 26 different (a variation of 16)
    16 pitch=1.1 tremolo=0.15       sample 16 with two knobs set exactly
    smoky / raspy / plain           the first two effects (samples 1-10 without the voice)

It starts with a sample number (1-30) or name (PRESETS), then changes applied left to right: words (WORDS),
numbers 21-30 (each adds what made that variation of 16 different, VARIATIONS) and knob=value (KNOBS).
The number fixes the effect only; the Kokoro voice is the separate `voice` setting (SAMPLES says which
voice each sample was made with).

The samples (Jarvis, 2026-11-10, ~/.hermes/profiles/orchestrator/cache/scratch/simba_witch/): 1-10 Kokoro at
speed 0.92 with "smoky" or "raspy"; 11-20 Kokoro at speed 0.95 with a pitch per sample (tempo kept) and a
smoky, raspy or goofy chain; 21-30 variations of 16 (am_fenrir, pitch 1.08). Every number is rebuilt here
from its recipe; tests/test_simba_voice.py checks them against Jarvis's files when those are on disk.

Only the two model files are read from the Kokoro folder (it sits in Peter's tax folder: never anything
else there). The model is loaded per call (about 2-4 s); Simba speaks rarely, so there is no warm service.
Importing this module loads nothing heavy: voiceedit uses it to check what Peter types.
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
LANG = "en-us"                     # American pronunciation, whatever the voice
DEFAULT_EFFECT = "fenrir16"
ECHO = "aecho=0.8:0.6:60|110:0.3|0.2"      # sample 30's cave echo

# The American voices in voices-v1.0.bin (read from the file 2026-11-10): af_ female, am_ male. Peter:
# American accent only, so British (bf_/bm_) and other languages are refused. Sample 16 is am_fenrir.
VOICES = ("af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
          "af_river", "af_sarah", "af_sky",
          "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa")


class RecipeError(ValueError):
    """An effect recipe this script cannot make; the message is a sentence Peter can read."""


# ---- the recipes ------------------------------------------------------------------------------------------
# Every effect is one set of these values. None switches a stage off. Only KNOBS are Peter's to set.
_SMOKY_J = dict(plain=False, speed=0.95, pitch=1.0, tempo=1.0, hp=100, lp=4600, eq=(), tremolo=0.0, flutter=34,
                bits=10, crush_samples=None, mix=0.35, crush_mode="log", aa=1, clip="tanh", clip_threshold=0.3,
                clip_output=1.4, exciter=True, comp=-26, ratio=5, noise=0.01, noise_color="pink", noise_hp=None,
                noise_level=None, noise_weight=0.4, volume=1.15, echo=False)
# "tempo" is the overall speed against Kokoro's own: the pitch change speeds the voice up by `pitch`, so
# ffmpeg's atempo is tempo/pitch. Samples 11-30 kept Kokoro's tempo (1.0); samples 1-10 did not.
_RASPY_J = dict(_SMOKY_J, hp=140, lp=5000, tremolo=0.35, bits=7, crush_samples=2, mix=0.5, crush_mode="lin",
                aa=None, clip="hard", clip_threshold=0.2, clip_output=1.6, exciter=False, comp=-24, ratio=8,
                noise=0.02, noise_color="white", noise_weight=0.5)
_GOOFY_J = dict(_SMOKY_J, hp=250, lp=None, eq=((1200, 6), (2600, 4)), bits=None, clip=None, exciter=False,
                comp=-22, ratio=4, noise=0.0005, noise_weight=0.1)
_SMOKY_K = dict(_SMOKY_J, speed=0.92, pitch=0.85, tempo=0.952, hp=110, lp=4800, volume=1.1)     # atempo 1.12
_RASPY_K = dict(_RASPY_J, speed=0.92, pitch=0.78, tempo=0.8424, hp=150, lp=5200, noise_hp=1500,  # atempo 1.08
                noise_level=0.6, volume=1.2)
_PLAIN = dict(_SMOKY_J, plain=True, speed=0.92)

# What made each of 21-30 different from 16 (Jarvis's v16 function): set exactly, in this order.
VARIATIONS: dict[int, dict] = {
    21: {"pitch": 1.00}, 22: {"pitch": 1.04}, 23: {"pitch": 1.12}, 24: {"pitch": 1.16},
    25: {"bits": 8, "mix": 0.6}, 26: {"noise": 0.03}, 27: {"tempo": 0.9}, 28: {"tempo": 1.1},
    29: {"bits": 9, "mix": 0.45, "noise": 0.015, "tremolo": 0.2}, 30: {"echo": True},
}

# Jarvis's numbered samples: the voice each was made with, and its effect.
SAMPLES: dict[int, tuple[str, dict]] = {
    1: ("af_nicole", _SMOKY_K), 2: ("af_nicole", _RASPY_K), 3: ("af_kore", _SMOKY_K), 4: ("af_kore", _RASPY_K),
    5: ("af_river", _SMOKY_K), 6: ("af_river", _RASPY_K), 7: ("af_jessica", _SMOKY_K), 8: ("af_jessica", _RASPY_K),
    9: ("af_nova", _SMOKY_K), 10: ("af_nova", _RASPY_K),
    11: ("af_nicole", dict(_SMOKY_J, pitch=0.72)), 12: ("af_kore", dict(_RASPY_J, pitch=0.74)),
    13: ("af_river", dict(_RASPY_J, pitch=0.70)), 14: ("af_jessica", dict(_SMOKY_J, pitch=0.76)),
    15: ("am_onyx", dict(_RASPY_J, pitch=1.12)), 16: ("am_fenrir", dict(_SMOKY_J, pitch=1.08)),
    17: ("am_michael", dict(_RASPY_J, pitch=1.10)), 18: ("af_river", dict(_SMOKY_J, pitch=0.80)),
    19: ("af_nova", dict(_GOOFY_J, pitch=1.22)), 20: ("af_bella", dict(_GOOFY_J, pitch=1.30)),
}
for _num, _change in VARIATIONS.items():
    SAMPLES[_num] = ("am_fenrir", dict(SAMPLES[16][1], **_change))

# Named starting points. A number 1-30 is one too.
PRESETS: dict[str, dict] = {"fenrir16": SAMPLES[16][1], "smoky": _SMOKY_K, "raspy": _RASPY_K, "plain": _PLAIN}

# Peter's knobs: name -> (lowest, highest, what it does). echo is on/off.
KNOBS: dict[str, tuple[float, float, str]] = {
    "speed": (0.5, 1.5, "Kokoro's own speaking speed"),
    "pitch": (0.5, 2.0, "pitch factor, above 1 is higher"),
    "tempo": (0.5, 2.0, "overall tempo, below 1 is slower"),
    "bits": (2, 16, "bit-crush depth, fewer is crunchier"),
    "mix": (0.0, 1.0, "how much of the crush is heard"),
    "noise": (0.0, 0.2, "breath noise"),
    "tremolo": (0.0, 1.0, "flutter depth, 0 is off"),
    "echo": (0, 1, "cave echo, on or off"),
}

# Words, applied in order. A number is added to (pitch) or multiplies (tempo); a dict sets knobs exactly.
WORDS: dict[str, tuple[str, float] | dict] = {
    "lowest": ("pitch", -0.08), "lower": ("pitch", -0.04), "higher": ("pitch", +0.04), "highest": ("pitch", +0.08),
    "slower": ("tempo", 0.9), "faster": ("tempo", 1.1),
    "crunchier": VARIATIONS[25], "breathier": VARIATIONS[26],
    "rougher": VARIATIONS[29], "flutter": VARIATIONS[29], "rougher-flutter": VARIATIONS[29],
    "echo": VARIATIONS[30], "cave": VARIATIONS[30], "cave-echo": VARIATIONS[30],
}
_FILLER = {"but", "and", "with", "plus", "then", "sample", "number", "#"}
_ON, _OFF = {"on", "yes", "true", "1"}, {"off", "no", "false", "0"}

# A little silence in front so a waking speaker does not swallow the first word (as Esther's script does).
# mp3 is written from 16-bit samples: lame refuses the float frames the cave echo (sample 30) leaves behind.
ENCODE = {"wav": ["-af", "adelay=200:all=1", "-c:a", "pcm_s16le", "-f", "wav"],
          "mp3": ["-af", "adelay=200:all=1", "-c:a", "libmp3lame", "-sample_fmt", "s16p", "-b:a", "64k", "-f", "mp3"],
          "ogg": ["-af", "adelay=400:all=1", "-c:a", "libopus", "-b:a", "40k", "-application", "voip", "-f", "ogg"]}


def clean_effect(text: Optional[str]) -> str:
    """How a recipe is stored: lower case, single spaces ("23 But  Slower" -> "23 but slower")."""
    return " ".join((text or "").lower().split())


def _tokens(text: str) -> list[str]:
    out = []
    for t in re.split(r"[\s,;+]+", clean_effect(text)):
        t = t.lstrip("#")
        if t and t not in _FILLER:
            out.append(t)
    return out


def _start(token: str) -> Optional[dict]:
    if token.isdigit() and int(token) in SAMPLES:
        return SAMPLES[int(token)][1]
    return PRESETS.get(token)


def _number(name: str, text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise RecipeError(f"{name} needs a number, like {name}={KNOBS[name][1]:g}; got {text!r}.") from None


def recipe(text: Optional[str]) -> dict:
    """The full set of effect values a recipe stands for. Raises RecipeError with a plain sentence."""
    tokens = _tokens(text or "")
    if not tokens:
        raise RecipeError(f"The voice effect is empty; write a sample number such as 16, or {DEFAULT_EFFECT}.")
    p = _start(tokens[0])
    if p is None:
        raise RecipeError(f"The voice effect starts with a sample number (1-30) or one of {', '.join(PRESETS)}; "
                          f"got {text!r}.")
    p = dict(p)
    for t in tokens[1:]:
        if "=" in t:
            name, value = t.split("=", 1)
            if name not in KNOBS:
                raise RecipeError(f"{name!r} is not a knob; the knobs are {', '.join(KNOBS)}.")
            if name == "echo":
                if value not in _ON | _OFF:
                    raise RecipeError(f"echo is on or off; got {value!r}.")
                p["echo"] = value in _ON
                continue
            p[name] = _number(name, value)
        elif t.isdigit() and int(t) in VARIATIONS:
            p.update(VARIATIONS[int(t)])
        elif t in WORDS:
            w = WORDS[t]
            if isinstance(w, dict):
                p.update(w)
            elif w[0] == "pitch":
                p["pitch"] = p["pitch"] + w[1]
            else:
                p["tempo"] = p["tempo"] * w[1]
        elif t.isdigit():
            raise RecipeError(f"Only one sample can be the start; after it, 21-30 add what made that variation of "
                              f"16 different. {t} is not one of them.")
        else:
            raise RecipeError(f"{t!r} is not a change I know. Words: {', '.join(WORDS)}; or knob=value with "
                              f"{', '.join(KNOBS)}.")
    for name, (low, high, what) in KNOBS.items():
        if name == "echo" or p[name] is None:          # echo is on/off; bits None = no crusher (goofy)
            continue
        p[name] = round(float(p[name]), 4)
        if name == "bits":
            p[name] = int(round(p[name]))
        if not low <= p[name] <= high:
            raise RecipeError(f"{name} ({what}) must be between {low:g} and {high:g}; this recipe makes it "
                              f"{p[name]:g}.")
    if p["plain"]:
        changed = [k for k in KNOBS if k != "speed" and p[k] != _PLAIN[k]]
        if changed:
            raise RecipeError(f"plain is Kokoro without an effect: only speed can be set, not {', '.join(changed)}.")
    elif not 0.5 <= p["tempo"] / p["pitch"] <= 100:
        raise RecipeError(f"pitch {p['pitch']:g} with tempo {p['tempo']:g} needs a slow-down ffmpeg cannot make "
                          "(tempo divided by pitch must be at least 0.5).")
    return p


def effect_problem(text: Optional[str]) -> Optional[str]:
    """None if this script makes that effect, else a sentence saying why not."""
    try:
        recipe(text)
    except RecipeError as exc:
        return str(exc)
    return None


def sample_voice(text: Optional[str]) -> Optional[str]:
    """The voice the recipe's starting sample was made with ("23 slower" -> am_fenrir); None for smoky etc."""
    tokens = _tokens(text or "")
    if not tokens:
        return None
    if tokens[0].isdigit() and int(tokens[0]) in SAMPLES:
        return SAMPLES[int(tokens[0])][0]
    return "am_fenrir" if tokens[0] == "fenrir16" else None


def _n(x: float) -> str:
    """A number the way ffmpeg is given it: 10, 0.35, -26, 0.0005."""
    s = f"{round(float(x), 6):f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def describe(text: Optional[str]) -> str:
    """The knobs a recipe ends up with, for the panel ("speed 0.95 · pitch 1.12 · tempo 0.9 · ...")."""
    try:
        p = recipe(text)
    except RecipeError:
        return "not a recipe this script makes"
    if p["plain"]:
        return f"Kokoro without an effect · speed {_n(p['speed'])}"
    parts = [f"speed {_n(p['speed'])}", f"pitch {_n(p['pitch'])}", f"tempo {_n(p['tempo'])}"]
    if p["bits"]:
        parts.append(f"crush {p['bits']} bits mix {_n(p['mix'])}")
    parts.append(f"noise {_n(p['noise'])}")
    if p["tremolo"]:
        parts.append(f"tremolo {_n(p['tremolo'])}")
    if p["echo"]:
        parts.append("echo")
    return " · ".join(parts)


def filters(p: dict, sr: int) -> tuple[str, str]:
    """(noise source, filter graph) for one set of effect values. Input 0 is Kokoro's voice at sr Hz, input 1
    the noise; the graph is written exactly the way Jarvis wrote his samples' chains."""
    chain = [f"asetrate={sr}*{_n(p['pitch'])}", "aresample=48000", f"atempo={_n(round(p['tempo'] / p['pitch'], 4))}",
             f"highpass=f={_n(p['hp'])}"]
    if p["lp"]:
        chain.append(f"lowpass=f={_n(p['lp'])}")
    chain += [f"equalizer=f={_n(f)}:t=q:w=1:g={_n(g)}" for f, g in p["eq"]]
    if p["tremolo"]:
        chain.append(f"tremolo=f={_n(p['flutter'])}:d={_n(p['tremolo'])}")
    if p["bits"]:
        crush = f"acrusher=bits={p['bits']}"
        if p["crush_samples"]:
            crush += f":samples={p['crush_samples']}"
        crush += f":mix={_n(p['mix'])}:mode={p['crush_mode']}"
        if p["aa"] is not None:
            crush += f":aa={_n(p['aa'])}"
        chain.append(crush)
    if p["clip"]:
        chain.append(f"asoftclip=type={p['clip']}:threshold={_n(p['clip_threshold'])}:output={_n(p['clip_output'])}")
    if p["exciter"]:
        chain.append("aexciter=amount=2:freq=2200")
    chain.append(f"acompressor=threshold={_n(p['comp'])}dB:ratio={_n(p['ratio'])}")
    graph = "[0]" + ",".join(chain) + "[v];"
    if p["noise_hp"]:
        graph += f"[1]highpass=f={_n(p['noise_hp'])},volume={_n(p['noise_level'])}[nn];[v][nn]"
    else:
        graph += "[v][1]"
    graph += f"amix=inputs=2:duration=first:weights=1 {_n(p['noise_weight'])},volume={_n(p['volume'])}"
    if p["echo"]:
        graph += "," + ECHO
    noise = f"anoisesrc=color={p['noise_color']}:amplitude={_n(p['noise'])}:sample_rate=48000"
    return noise, graph


def ffmpeg_args(effect: str, sr: int, src: Path, dst: Path, fmt: str) -> list[str]:
    """The whole ffmpeg command that turns Kokoro's plain voice (src, sr Hz) into the effect, as fmt."""
    p = recipe(effect)
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    enc = list(ENCODE[fmt])
    if not p["plain"]:
        noise, graph = filters(p, sr)
        args += ["-f", "lavfi", "-i", noise, "-filter_complex", graph + "," + enc[1]]   # lead silence goes last
        enc = enc[2:]
    return args + ["-ac", "1", *enc, str(dst)]


def clean_voice(name: str) -> str:
    """'Fenrir', 'nicole' or 'af_nicole' -> the full Kokoro name (unchecked)."""
    v = (name or "").strip().lower()
    if "_" in v or not re.fullmatch(r"[a-z]+", v):
        return v
    return next((full for full in VOICES if full.split("_", 1)[1] == v), f"af_{v}")


def voice_problem(name: str) -> Optional[str]:
    """None if this script speaks with that voice, else a sentence saying why not."""
    if clean_voice(name) in VOICES:
        return None
    return (f"Simba's voice must be an American Kokoro voice (af_… female or am_… male: "
            f"{', '.join(VOICES)}); got {name!r}.")


def kokoro_wav(text: str, voice: str, speed: float, dst: Path) -> int:
    """Kokoro's plain voice into a 16-bit mono wav; returns its sample rate."""
    import numpy as np
    from kokoro_onnx import Kokoro
    k = Kokoro(str(MODEL_DIR / MODEL), str(MODEL_DIR / VOICES_BIN))
    samples, rate = k.create(text, voice=voice, speed=speed, lang=LANG)
    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return int(rate)


def make(text: str, out: Path, voice: str, effect: str = DEFAULT_EFFECT) -> int:
    """Write Simba's line to out. Returns the exit code (see the top of the file)."""
    voice, effect = clean_voice(voice), effect or DEFAULT_EFFECT
    problem = voice_problem(voice) or effect_problem(effect)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if not text.strip():
        print("nothing to say", file=sys.stderr)
        return 2
    p = recipe(effect)
    fmt = out.suffix.lower().lstrip(".")
    fmt = fmt if fmt in ENCODE else "wav"
    with tempfile.TemporaryDirectory(prefix="simba-tts-") as tmp:
        raw = Path(tmp) / "kokoro.wav"
        made = Path(tmp) / f"simba.{fmt}"
        try:
            sr = kokoro_wav(text, voice, p["speed"], raw)
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
