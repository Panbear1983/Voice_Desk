"""Text to speech + playback. Reuses Hermes' voice chain (Jenny -> Piper -> Mac voice) for
English and the Mac's Meijia voice for Mandarin. Playback is afplay so it can be killed
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


def _chain(txt: Path, out: Path, voice: str) -> Optional[Path]:
    """Hermes' voice chain (Edge -> Gemini -> Mac) with one named Edge voice."""
    if not S.TTS_CHAIN.exists():
        return None
    r = subprocess.run([str(S.TTS_CHAIN), str(txt), str(out), voice, S.TTS_ORDER],
                       capture_output=True, timeout=120)
    return out if r.returncode == 0 and out.exists() and out.stat().st_size > 0 else None


def synthesize(text: str, voice: Optional[voices.Voice] = None) -> Optional[Path]:
    """Return a playable audio file for text, or None if every voice failed.
    `voice` is the speaker's (voices.voice_for(target)); None = the shared Voice Desk voice."""
    v = voice or voices.shared_voice()
    tmp = Path(tempfile.mkdtemp(prefix="cv-tts-"))
    txt = tmp / "say.txt"
    txt.write_text(text, encoding="utf-8")
    out = tmp / "say.wav"
    if is_mandarin(text):
        got = _chain(txt, out, v.zh)
        if got:
            return got
        aiff = tmp / "say.aiff"
        r = subprocess.run(["/usr/bin/say", "-v", S.TTS_VOICE_ZH_MAC, "-f", str(txt), "-o", str(aiff)],
                           capture_output=True)
        if r.returncode == 0 and aiff.exists():
            subprocess.run([S.FFMPEG, "-loglevel", "error", "-y", "-i", str(aiff), str(out)], capture_output=True)
            return out if out.exists() else None
        return None
    got = _chain(txt, out, v.en)
    if got:
        return got
    # last resort: the plain Mac voice, so the loop never goes silent
    aiff = tmp / "say.aiff"
    r = subprocess.run(["/usr/bin/say", "-r", "185", "-f", str(txt), "-o", str(aiff)], capture_output=True)
    if r.returncode == 0 and aiff.exists():
        subprocess.run([S.FFMPEG, "-loglevel", "error", "-y", "-i", str(aiff), str(out)], capture_output=True)
        return out if out.exists() else None
    return None


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


def say_now(text: str, player: Player) -> None:
    """Synthesize and play, blocking until done (used for short status lines)."""
    path = synthesize(text)
    if path:
        player.play(path)
        player.wait()


def tick() -> None:
    """A soft click: 'I have your words, thinking.'"""
    subprocess.Popen([S.AFPLAY, "/System/Library/Sounds/Tink.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
