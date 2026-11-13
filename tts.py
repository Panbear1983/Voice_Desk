"""Text to speech + playback. Each agent speaks with exactly ONE voice per language, the one agents.toml names
(voices.py): its own script for a Kokoro voice, Edge for exactly the named Microsoft voice. If that voice fails the
line stays silent; no second voice is ever tried (Peter 2026-11-11). Playback is afplay so it can be killed
mid-sentence when Peter talks over it."""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

import settings as S
import voices

_CJK = re.compile(r"[一-鿿　-〿＀-￯]")


def speakable(text: str) -> str:
    """Strip anything that cannot be read aloud (same rules as the Stop hook)."""
    text = re.sub(r"```.*?```", " the code is in the window. ", text, flags=re.S)
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.M)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " a link ", text)
    text = re.sub(r"[*_#>|~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_mandarin(text: str) -> bool:
    return len(_CJK.findall(text)) > len(re.findall(r"[A-Za-z]", text))


_EDGE_PYTHONS = ("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3", "/usr/local/bin/python3")
_edge_python: Optional[str] = None
NEWHOST = Path.home() / ".local/bin/edge_tts_newhost.py"      # Edge through Microsoft's current address


def edge_python() -> Optional[str]:
    """A Python that has edge-tts (the same search Hermes' own voice script does), found once."""
    global _edge_python
    if _edge_python is None:
        import glob
        found = ""
        for c in [*_EDGE_PYTHONS, *sorted(glob.glob(str(Path.home() / ".hermes/installs/*/environments/*/venv/bin/python")),
                                          key=lambda f: -Path(f).stat().st_mtime)]:
            if Path(c).exists() and subprocess.run([c, "-c", "import edge_tts"], capture_output=True).returncode == 0:
                found = c
                break
        _edge_python = found
    return _edge_python or None


def edge_exact(txt: Path, out: Path, voice: str) -> bool:
    """Ask Edge for exactly this voice and nothing else: no Google, no Mac voice. Two tries (one Edge hiccup is
    retried with Edge, never handed to another voice)."""
    py = edge_python()
    if not voice or not py or not NEWHOST.exists():
        return False
    for _ in range(2):
        try:
            r = subprocess.run([py, str(NEWHOST), "--voice", voice, "--file", str(txt), "--write-media", str(out)],
                               capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return True
    return False


def _chain(txt: Path, out: Path, voice: str) -> Optional[Path]:
    """The one named Edge voice, or None. Peter 2026-11-11: no second voice, ever. Hermes' shared chain
    (tts_natural.sh) is NOT used here any more: after Edge it tries Google and the Mac voice, and for mostly-Chinese
    text it picks its own Mandarin voice, both of which would put a different voice on the line."""
    return out if edge_exact(txt, out, voice) else None


def _script(cmd: tuple[str, ...], txt: Path, out: Path, voice: str, timeout: float = 120,
            effect: Optional[str] = None) -> Optional[Path]:
    """A non-Edge voice made by the agent's own script (Esther's Kokoro, Simba's Kokoro + effect):
    <cmd...> <text file> <out file> <voice> [effect]. None if it fails or is too slow."""
    try:
        r = subprocess.run([*cmd, str(txt), str(out), voice, *([effect] if effect else [])],
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out if r.returncode == 0 and out.exists() and out.stat().st_size > 0 else None


def synthesize(text: str, voice: Optional[voices.Voice] = None) -> Optional[Path]:
    """Return a playable audio file for text, or None: the line stays silent.

    ONE voice per language, no fallback (Peter 2026-11-11): the English line is made with `voice.en` only (through
    the agent's own script when it has one, else Edge for exactly that name), the Mandarin line with `voice.zh`
    only. If that one voice fails, or the agent has no voice for the language, the result is None and nothing else
    is tried: no second Edge voice, no Piper, no Mac voice. `voice` is the speaker's (voices.voice_for(target));
    None means nobody's voice, so silence."""
    v = voice or voices.SILENT
    if is_mandarin(text) or (_CJK.search(text) and not v.zh):
        # a mostly-Chinese line needs the Mandarin voice; any Chinese in a line for an agent without one (Simba,
        # Jarvis: English only) stays silent too: an English voice would mangle it
        want, command = v.zh, ()
    else:
        want, command = v.en, v.command
    if not want:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="cv-tts-"))
    txt = tmp / "say.txt"
    txt.write_text(text, encoding="utf-8")
    if command:                      # Esther / Jarvis / Simba / any Kokoro voice: that script, and only that script
        return _script(command, txt, tmp / "say.wav", want, effect=v.effect)
    return _chain(txt, tmp / "say.mp3", want)


class Player:
    """One afplay at a time; stop() cuts it off."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def play(self, path: Path) -> None:
        with self._lock:
            self._proc = subprocess.Popen([S.AFPLAY, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def is_playing(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def wait(self) -> None:
        p = self._proc
        if p:
            p.wait()

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            self._proc = None


def say_now(text: str, player: Player, voice: Optional[voices.Voice] = None) -> None:
    """Synthesize and play, blocking until done (used for short status lines). No voice = silent."""
    path = synthesize(text, voice)
    if path:
        player.play(path)
        player.wait()


def tick() -> None:
    """'I have your words, thinking.' (the Hermes CLI 'stopped recording' cue)"""
    chime(S.SOUND_GOT_IT)


# Turn-taking beeps, generated the way Hermes CLI voice mode makes its own (tools/voice_mode.py
# play_beep: sine, 120 ms per beep, 60 ms gap, 10 ms fades, volume 0.3) so the two feel the same.
# A setting that names a Mac system sound file is played as-is; "beep:<Hz>x<count>" is generated.
def _beep_file(spec: str) -> str:
    m = re.match(r"^beep:(\d+)(?:-(\d+))?x(\d+)$", spec)
    if not m:
        return spec
    f1, f2, count = int(m.group(1)), int(m.group(2) or m.group(1)), int(m.group(3))
    path = S.STATE_DIR / "sounds" / f"{spec.replace(':', '_')}.wav"
    if path.exists():
        return str(path)
    import wave
    import numpy as np
    rate, dur, vol = 16_000, 0.12, S.BEEP_VOLUME
    n = int(rate * dur)
    fade = int(rate * 0.01)
    parts = []
    for i in range(count):
        f = f1 + (f2 - f1) * (i / max(1, count - 1))      # "660-440x2": a falling pair
        t = np.linspace(0, dur, n, endpoint=False)
        tone = np.sin(2 * np.pi * f * t)
        tone[:fade] *= np.linspace(0, 1, fade)
        tone[-fade:] *= np.linspace(1, 0, fade)
        parts.append((tone * vol * 32767).astype(np.int16))
        if i < count - 1:
            parts.append(np.zeros(int(rate * 0.06), dtype=np.int16))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.concatenate(parts).tobytes())
    return str(path)


def chime(sound: str) -> None:
    """A short cue, not waited for."""
    subprocess.Popen([S.AFPLAY, _beep_file(sound)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
