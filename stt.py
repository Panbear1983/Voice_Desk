"""Speech to text: whisper-server kept loaded in the background, so a turn costs ~1 s
instead of the ~9 s whisper-cli pays to reload the model every time."""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional

import settings as S


class Whisper:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self.url = f"http://127.0.0.1:{S.WHISPER_PORT}"

    def _alive(self) -> bool:
        try:
            urllib.request.urlopen(self.url + "/", timeout=0.5)
            return True
        except Exception:
            return False

    def start(self, timeout: float = 40.0) -> None:
        if self._alive():        # another window already runs it; share it
            return
        self._proc = subprocess.Popen(
            [S.WHISPER_SERVER, "-m", str(S.WHISPER_MODEL), "--host", "127.0.0.1",
             "--port", str(S.WHISPER_PORT), "-l", S.WHISPER_LANGUAGE, "-nt", "-t", str(S.WHISPER_THREADS)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self._alive():
                return
            if self._proc.poll() is not None:
                raise RuntimeError("whisper-server exited during start")
            time.sleep(0.25)
        raise RuntimeError("whisper-server did not come up")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def transcribe(self, wav: Path, prompt: str = S.WHISPER_PROMPT) -> str:
        boundary = "----claudevoice"
        data = wav.read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + data + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson"
            + (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\n{prompt}" if prompt else "")
            + f"\r\n--{boundary}--\r\n").encode()
        req = urllib.request.Request(
            self.url + "/inference", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = json.loads(resp.read().decode()).get("text", "")
        text = clean(text)
        return "" if prompt and echoes_prompt(text, prompt) else text


def echoes_prompt(text: str, prompt: str) -> bool:
    """On noise whisper can hand the vocabulary hint back as if it were heard
    ("Jarvis, Kylie, Esther, Wanna Buffet..."). Three or more of the hint's names in one
    sentence is that echo, not a person talking."""
    names = [normalize(n) for n in prompt.split(".")[0].split(",") if normalize(n)]
    said = normalize(text)
    return sum(1 for n in names if n in said) >= 3


def clean(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)       # [BLANK_AUDIO], (laughs)
    text = re.sub(r"\s+", " ", text).strip()
    if text.lower() in S.WHISPER_HALLUCINATIONS:
        return ""
    return text


def normalize(text: str) -> str:
    """lowercase, no punctuation — for matching spoken commands."""
    return re.sub(r"[^\w\s']", "", text.lower()).strip()
