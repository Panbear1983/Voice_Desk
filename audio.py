"""Microphone side of the loop: open mic, decide when Peter starts and stops talking.

Plain RMS energy against an adaptive noise floor (the same approach Hermes' own voice
mode uses on this Mac). No extra VAD library, no model.
"""
from __future__ import annotations

import collections
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

import settings as S


def _rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))


class Microphone:
    """Keeps one input stream open and hands out 30 ms int16 blocks through a queue.

    `deaf` is an optional gate: while it returns True every block is thrown away before
    anyone sees it (the always-on listener uses it so its own voice, and any other program
    speaking on this Mac, never reaches whisper)."""

    def __init__(self, deaf: Optional[Callable[[], bool]] = None) -> None:
        self._blocks: collections.deque[np.ndarray] = collections.deque(maxlen=400)   # ~12 s
        self._cv = threading.Condition()
        self._stream: Optional[sd.InputStream] = None
        self.noise_floor = S.NOISE_FLOOR_START
        self.level = 0.0            # latest RMS, for the UI meter
        self.deaf = deaf
        self.dropped = 0            # blocks thrown away while deaf (the echo test reads this)
        self.gaps = 0               # bumps on every deaf block; listen() restarts an utterance cut by one

    def start(self) -> None:
        if self._stream:
            return
        self._stream = sd.InputStream(
            samplerate=S.SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=S.BLOCK, callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _on_audio(self, indata, frames, time_info, status) -> None:  # sounddevice callback thread
        block = indata[:, 0].copy()
        self.level = _rms(block)
        if self.deaf is not None and self.deaf():
            self.dropped += 1
            self.gaps += 1
            with self._cv:
                self._blocks.clear()            # nothing heard before or during the gap survives it
            return
        with self._cv:
            self._blocks.append(block)
            self._cv.notify_all()

    def next_block(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        with self._cv:
            if not self._blocks:
                self._cv.wait(timeout)
            return self._blocks.popleft() if self._blocks else None

    def drain(self) -> None:
        with self._cv:
            self._blocks.clear()

    # -- the two things the loop asks for ------------------------------------------------

    def listen(self, timeout_sec: float, should_abort: Callable[[], bool] = lambda: False,
               on_state: Callable[[str], None] = lambda s: None,
               max_sec: float = S.MAX_UTTERANCE_SEC) -> Optional[np.ndarray]:
        """Block until one utterance is complete. Returns int16 audio, or None if nothing
        was said within timeout_sec (or should_abort() turned true)."""
        self.drain()
        pre_roll: collections.deque[np.ndarray] = collections.deque(
            maxlen=int(S.PRE_ROLL_SEC * 1000 / S.BLOCK_MS))
        voiced: list[np.ndarray] = []
        loud_run = 0
        quiet_run = 0
        speaking = False
        span = 0                        # blocks from the first loud block on (the too-short test)
        start = time.monotonic()
        blocks_start = int(S.SPEECH_START_SEC * 1000 / S.BLOCK_MS)
        blocks_send = int(S.SILENCE_SEND_SEC * 1000 / S.BLOCK_MS)
        max_blocks = int(max_sec * 1000 / S.BLOCK_MS)
        gaps = self.gaps

        while True:
            if should_abort():
                return None
            block = self.next_block()
            if self.gaps != gaps:               # the mic went deaf mid-way: whatever came before is not one utterance
                gaps = self.gaps
                if speaking:
                    on_state("listening")
                pre_roll.clear()
                speaking, voiced, loud_run, quiet_run, span = False, [], 0, 0, 0
            if block is None:
                if not speaking and time.monotonic() - start > timeout_sec:
                    return None
                continue
            level = _rms(block)
            threshold = max(self.noise_floor * S.SPEECH_OVER_FLOOR, S.MIN_SPEECH_RMS)

            if not speaking:
                pre_roll.append(block)
                if level > threshold:
                    loud_run += 1
                    if loud_run >= blocks_start:
                        speaking = True
                        voiced = list(pre_roll)
                        quiet_run = 0
                        span = loud_run
                        on_state("hearing")
                else:
                    loud_run = 0
                    # adapt the floor only while nobody is talking (slow EMA)
                    self.noise_floor = 0.97 * self.noise_floor + 0.03 * max(level, 20.0)
                    if time.monotonic() - start > timeout_sec:
                        return None
                continue

            voiced.append(block)
            span += 1
            if level > threshold * 0.6:
                quiet_run = 0
            else:
                quiet_run += 1
            if quiet_run >= blocks_send or len(voiced) >= max_blocks:
                audio = np.concatenate(voiced)
                # Too short to be words? Measure from the first loud block to the last one. (Peter 2026-10-22: a
                # quick "Hi Jarvis" often needed several loud tries. The old test was total length minus the whole
                # 0.6 s pre-roll and the 1.5 s silence, which also subtracted the 0.25 s of speech that triggered
                # detection, so a short greeting needed ~0.65 s of sound and was dropped before whisper saw it.)
                if (span - quiet_run) * S.BLOCK_MS / 1000 < S.MIN_SPEECH_SEC:
                    speaking, voiced, loud_run, quiet_run, span = False, [], 0, 0, 0
                    start = time.monotonic()
                    on_state("listening")
                    continue
                return audio

    def wait_for_barge_in(self, is_playing: Callable[[], bool]) -> bool:
        """While Claude speaks: return True if Peter talks over it. The Mac's own speakers
        leak into the mic, so the floor is re-measured during the first 0.7 s of playback."""
        self.drain()
        calib: list[float] = []
        loud_run = 0
        need = int(S.BARGE_IN_SEC * 1000 / S.BLOCK_MS)
        t0 = time.monotonic()
        while is_playing():
            block = self.next_block(0.2)
            if block is None:
                continue
            level = _rms(block)
            if time.monotonic() - t0 < 0.7:
                calib.append(level)
                continue
            floor = max(np.median(calib) if calib else self.noise_floor, self.noise_floor)
            if level > max(floor * S.BARGE_IN_OVER_FLOOR, S.MIN_SPEECH_RMS * 1.5):
                loud_run += 1
                if loud_run >= need:
                    return True
            else:
                loud_run = 0
        return False


def write_wav(audio: np.ndarray, path: Path) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(S.SAMPLE_RATE)
        w.writeframes(audio.astype(np.int16).tobytes())
    return path
