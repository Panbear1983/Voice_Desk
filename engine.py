"""The loop: listen -> pause sends -> think -> speak -> listen again.

UI-agnostic. The window (app.py) subscribes to events; a headless test can drive it
with a wav file instead of the microphone (see tests/).
"""
from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import settings as S
import stt
import tts
from audio import Microphone, write_wav
from brain import Brain, addressed

Event = Callable[[str, str], None]   # (kind, text) kind: state|you|claude|status|note


class Engine:
    MODES = ("open", "tap")

    def __init__(self, on_event: Event = lambda k, t: None, mic: Optional[Microphone] = None,
                 whisper: Optional[stt.Whisper] = None, brain: Optional[Brain] = None) -> None:
        self.on_event = on_event
        self.mic = mic or Microphone()
        self.whisper = whisper or stt.Whisper()
        self.brain = brain or Brain()
        self.player = tts.Player()
        self.mode = "open"             # open mic (default) or tap-to-talk
        self.asleep = False
        self.state = "starting"
        self._tap = threading.Event()  # set by the UI when Peter taps Space in tap mode
        self._quit = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_speech = time.monotonic()

    # -- lifecycle --------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="voice-loop")
        self._thread.start()

    def quit(self) -> None:
        self._quit.set()
        self.player.stop()
        self.brain.cancel()

    def _set(self, state: str) -> None:
        self.state = state
        self.on_event("state", state)

    # -- controls the window can call ----------------------------------------------------------
    def tap(self) -> None:
        """Space in tap mode: first tap starts listening, second tap sends."""
        self._tap.set()

    def toggle_mode(self) -> str:
        self.mode = "tap" if self.mode == "open" else "open"
        self.on_event("note", "tap to talk: press Space, speak, press Space again" if self.mode == "tap"
                      else "open mic: just talk, a pause sends")
        return self.mode

    def sleep(self) -> None:
        self.asleep = True
        self._set("asleep")

    def wake(self) -> None:
        self.asleep = False
        self._last_speech = time.monotonic()
        self._set("listening")

    def interrupt(self) -> None:
        self.player.stop()

    # -- the loop ---------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._set("starting")
            self.whisper.start()
            self.mic.start()
        except Exception as exc:  # pragma: no cover - surfaced in the window
            self._set("error")
            self.on_event("note", f"could not start: {exc}")
            return
        self._set("listening")
        self._speak("I'm listening.")
        while not self._quit.is_set():
            try:
                self._one_turn()
            except Exception as exc:  # keep the loop alive on any single failure
                self.on_event("note", f"turn failed: {exc}")
                self._set("listening")
        self.mic.stop()
        self.whisper.stop()

    def _one_turn(self) -> None:
        audio = self._capture()
        if audio is None:
            return
        self._set("thinking")
        text = self._transcribe(audio)
        if not text:
            self._set("asleep" if self.asleep else "listening")
            return
        self._last_speech = time.monotonic()
        norm = stt.normalize(text)

        hit = addressed(text)                       # "Jarvis, ..." / "hey Kylie" / "talk to Esther": wake + switch
        if hit:
            target, rest = hit
            self.brain.set_target(target)
            if self.asleep:
                self.wake()
            self.on_event("note", f"on the line: {self.brain.name(target)}")
            if not rest:
                self._speak(f"{self.brain.name(target)} here.")
                self._set("listening")
                return
            text, norm = rest, stt.normalize(rest)
        elif self.asleep:
            if not any(w in norm for w in (stt.normalize(x) for x in S.WAKE_WORDS)):
                self._set("asleep")
                return
            self.wake()
            rest = self._strip_wake_word(norm, text)
            if not rest:
                self._speak("Yes?")
                return
            text, norm = rest, stt.normalize(rest)

        if norm in {stt.normalize(w) for w in S.GOODBYE_WORDS}:
            self._goodbye()
            return
        if norm in {stt.normalize(w) for w in S.SLEEP_WORDS}:
            self._speak("Okay, sleeping. Say a name, like hey Claude or Jarvis, to wake me.")
            self.sleep()
            return
        if norm in {stt.normalize(w) for w in S.OPEN_MIC_WORDS}:
            self.mode = "open"; self.on_event("note", "open mic"); self._speak("Open mic."); self._set("listening"); return
        if norm in {stt.normalize(w) for w in S.TAP_MODE_WORDS}:
            self.mode = "tap"; self.on_event("note", "tap to talk"); self._speak("Tap to talk."); self._set("listening"); return
        if norm in {stt.normalize(w) for w in S.STOP_WORDS}:
            self._set("listening")
            return
        for w in S.TRAILING_SEND_WORDS:
            if text.lower().endswith(" " + w):
                text = text[: -len(w) - 1].rstrip()

        self.on_event("you", text)
        tts.tick()
        reply = self.brain.ask(text, on_status=self._status, on_text=lambda t: self.on_event("progress", t))
        self.on_event("claude", reply)
        self._speak(tts.speakable(reply), allow_barge_in=True)
        self._set("asleep" if self.asleep else "listening")

    # -- pieces -----------------------------------------------------------------------------
    def _capture(self) -> Optional[np.ndarray]:
        if self.mode == "tap":
            self._set("asleep" if self.asleep else "listening")
            self._tap.clear()
            while not self._tap.wait(0.2):
                if self._quit.is_set():
                    return None
            self._tap.clear()
            self._set("hearing")
            audio = self.mic.listen(timeout_sec=S.MAX_UTTERANCE_SEC,
                                    should_abort=lambda: self._tap.is_set() or self._quit.is_set())
            return audio
        timeout = S.SLEEP_AFTER_SEC if not self.asleep else 3600
        audio = self.mic.listen(timeout_sec=timeout, should_abort=self._quit.is_set,
                                on_state=lambda s: self._set("hearing") if s == "hearing" else None)
        if audio is None and not self.asleep and time.monotonic() - self._last_speech > S.SLEEP_AFTER_SEC:
            self.sleep()
        return audio

    def _transcribe(self, audio: np.ndarray) -> str:
        wav = Path(tempfile.mkdtemp(prefix="cv-")) / "in.wav"
        write_wav(audio, wav)
        return self.whisper.transcribe(wav)

    def _status(self, phrase: str) -> None:
        self.on_event("status", phrase)
        tts.say_now(phrase + ".", self.player)

    def _speak(self, text: str, allow_barge_in: bool = False) -> None:
        if not text:
            return
        self._set("speaking")
        path = tts.synthesize(text)
        if not path:
            self.on_event("note", "no voice available, see the window")
            return
        self.player.play(path)
        if allow_barge_in and self.mode == "open":
            if self.mic.wait_for_barge_in(self.player.is_playing):
                self.player.stop()
                self.on_event("note", "interrupted")
                # whatever Peter said while interrupting is picked up by the next listen()
        else:
            self.player.wait()

    def _goodbye(self) -> None:
        self._set("thinking")
        self.on_event("you", "goodbye")
        try:
            self.brain.ask(
                "We are ending this voice session. File ONE short memory note of what we discussed and decided "
                "(use mempalace_add_drawer under the right wing per ~/.mempalace/MEMORY_PROTOCOL.md; "
                "wing 'peter' if it was general). Then reply with one short goodbye sentence.",
                on_status=self._status)
        except Exception:
            pass
        self._speak("Goodbye.")
        self._quit.set()
        self._set("off")

    @staticmethod
    def _strip_wake_word(norm: str, original: str) -> str:
        for w in sorted((stt.normalize(x) for x in S.WAKE_WORDS), key=len, reverse=True):
            if norm.startswith(w):
                return original[len(w):].lstrip(" ,.") if len(original) >= len(w) else ""
        return original
