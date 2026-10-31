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


def _log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class Microphone:
    """Keeps one input stream open and hands out 30 ms int16 blocks through a queue.

    `deaf` is an optional gate: while it returns True every block is thrown away before
    anyone sees it (the always-on listener uses it so its own voice, and any other program
    speaking on this Mac, never reaches whisper)."""

    def __init__(self, deaf: Optional[Callable[[], bool]] = None,
                 default_input: Optional[Callable[[], Optional[str]]] = None,
                 built_in: Optional[Callable[[], list]] = None,
                 sd_module=None, clock: Callable[[], float] = time.monotonic,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self._blocks: collections.deque[np.ndarray] = collections.deque(maxlen=400)   # ~12 s
        self._cv = threading.Condition()
        self._stream: Optional[sd.InputStream] = None
        self.noise_floor = S.NOISE_FLOOR_START
        self.level = 0.0            # latest RMS, for the UI meter
        self.deaf = deaf
        self.dropped = 0            # blocks thrown away while deaf (the echo test reads this)
        self.gaps = 0               # bumps on every deaf block; listen() restarts an utterance cut by one
        # following the Mac's default mic (headsets); the fakes in tests/test_mic_follow.py replace all four
        if default_input is None or built_in is None:
            import coreaudio
            default_input = default_input or coreaudio.default_input_name
            built_in = built_in or coreaudio.built_in_input_names
        self._default_input = default_input
        self._built_in = built_in
        self._sd = sd_module or sd
        self.clock = clock
        self.log = log or _log
        self.device_name: Optional[str] = None   # the mic the stream is open on now
        self.last_block_at = 0.0                 # clock() of the newest block (stall watchdog)
        self._next_check = 0.0
        self._due = False
        self._failed: Optional[str] = None       # the default that would not open...
        self._failed_tries = 0                   # ...how often it has been tried...
        self._retry_at = 0.0                     # ...and when to try it again
        self._hold = S.MIC_STALL_SEC             # silence this long = the mic is gone (doubles per dead reopen)
        self._hold_until = 0.0                   # no reopen for a dead mic before this

    def start(self) -> None:
        if self._stream:
            return
        if not self._open_best(self._default_name()):
            raise RuntimeError("no microphone would open")

    def stop(self) -> None:
        if self._stream:
            stream, self._stream = self._stream, None
            try:
                stream.stop()
            finally:
                stream.close()

    # -- following the Mac's default microphone ---------------------------------------------------
    def _default_name(self) -> Optional[str]:
        try:
            return self._default_input()
        except Exception:                       # CoreAudio could not say: keep what we have
            return None

    def _index_of(self, name: Optional[str]) -> Optional[int]:
        """PortAudio's number for the input called `name` (its list is only current after a re-init)."""
        if not name:
            return None
        for i, dev in enumerate(self._sd.query_devices()):
            if dev["name"] == name and dev["max_input_channels"] > 0:
                return i
        return None

    def _open(self, name: Optional[str]) -> bool:
        """Open the stream on the input called `name` (None = PortAudio's default). False if it would not."""
        try:
            device = self._index_of(name)
            if name and device is None:
                raise RuntimeError("not in PortAudio's device list")
            stream = self._sd.InputStream(
                samplerate=S.SAMPLE_RATE, channels=1, dtype="int16", device=device,
                blocksize=S.BLOCK, callback=self._on_audio,
            )
            try:
                stream.start()
            except Exception:
                stream.close()
                raise
        except Exception as exc:
            self.log(f"microphone: could not open {name or 'the default input'}: {exc}")
            return False
        self._stream = stream
        try:
            self.device_name = self._sd.query_devices(stream.device)["name"]
        except Exception:
            self.device_name = name
        self.last_block_at = self.clock()
        return True

    def _open_best(self, want: Optional[str]) -> bool:
        """The wanted mic, else the Mac's own microphone, else whatever PortAudio calls the default."""
        if want and self._open(want):
            if want == self._failed:
                self._failed, self._failed_tries = None, 0
            return True
        if want:
            self._failed_tries = self._failed_tries + 1 if want == self._failed else 1
            self._failed, self._retry_at = want, self.clock() + S.MIC_RETRY_SEC
        try:
            fallbacks = [n for n in self._built_in() if n != want]
        except Exception:
            fallbacks = []
        for name in fallbacks:
            if self._open(name):
                self.log(f"microphone: fell back to {name}")
                return True
        return self._open(None)

    def switch_due(self) -> bool:
        """Cheap enough to ask often (one CoreAudio read every MIC_CHECK_SEC): True when the stream
        should move: the Mac's default mic is another one, the stream is gone, or it went silent."""
        now = self.clock()
        if now < self._next_check:
            return self._due
        self._next_check = now + S.MIC_CHECK_SEC
        if self._stream is not None and now - self.last_block_at < S.MIC_STALL_SEC:
            self._hold = S.MIC_STALL_SEC            # audio is flowing: the back-off starts over
        if self._stream is None or now - self.last_block_at > self._hold:
            # deaf (nothing would open) or silent (unplugged mid-stream): reopen, backing off to a
            # minute between tries so a dead device does not fill the log
            self._due = now >= self._hold_until
            return self._due
        want = self._default_name()
        if want and want != self._failed:       # the default moved on: a mic that failed earlier gets fresh tries
            self._failed, self._failed_tries = None, 0
        if not want or want == self.device_name:
            self._due = False
        elif want == self._failed:              # would not open: try again a few times, then wait for a change
            self._due = self._failed_tries < S.MIC_RETRY_TRIES and now >= self._retry_at
        else:
            self._due = True
        return self._due

    def switch(self) -> Optional[str]:
        """Move to the Mac's default mic. Call ONLY between sentences: the stream is closed for a
        moment. PortAudio reads its device list once, so it is restarted to see a new headset.
        Returns the name of the mic now open (None if none would open; switch_due() stays True)."""
        old = self.device_name
        want = self._default_name() or old
        stalled = self._stream is None or self.clock() - self.last_block_at > self._hold
        self.stop()
        try:
            self._sd._terminate()
            self._sd._initialize()
        except Exception as exc:
            self.log(f"microphone: could not restart PortAudio: {exc}")
        self.gaps += 1                          # nothing from before the switch is part of the next sentence
        self.noise_floor = S.NOISE_FLOOR_START  # a new mic has its own room noise
        self.drain()
        self._next_check, self._due = 0.0, False
        if stalled:                             # the next reopen for the same reason waits longer
            self._hold_until = self.clock() + self._hold
            self._hold = min(self._hold * 2, 60.0)
        if not self._open_best(want):
            self.device_name = None
            self.log("microphone: NO microphone would open; trying again shortly")
            return None
        if self.device_name != old:
            self.log(f"microphone: now listening on {self.device_name} (was {old or 'none'})")
        else:
            self.log(f"microphone: reopened {self.device_name} (no audio was arriving)" if stalled
                     else f"microphone: reopened {self.device_name}")
        return self.device_name

    def _on_audio(self, indata, frames, time_info, status) -> None:  # sounddevice callback thread
        self.last_block_at = self.clock()
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
                if speaking and self.clock() - self.last_block_at > S.MIC_STALL_SEC:
                    # the mic vanished mid-sentence (headset unplugged): keep what was heard, and give the
                    # loop its turn to move to the next microphone instead of waiting here forever
                    heard = (span - quiet_run) * S.BLOCK_MS / 1000
                    if heard < S.MIN_SPEECH_SEC:        # only a blip before it vanished: drop it like any other
                        self.log(f"microphone: audio stopped mid-sentence; dropped before whisper: too short ({heard:.2f}s of sound)")
                        return None
                    self.log("microphone: audio stopped mid-sentence; sending what was heard")
                    return np.concatenate(voiced)
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
                    # Peter 2026-10-22: log every drop, so a missed "Hey Jarvis" is never invisible.
                    print(time.strftime("%Y-%m-%d %H:%M:%S"),
                          f"dropped before whisper: too short ({(span - quiet_run) * S.BLOCK_MS / 1000:.2f}s of sound)",
                          flush=True)
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
