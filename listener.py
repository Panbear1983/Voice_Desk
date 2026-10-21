"""Voice Desk, always on: the Mac listens for a name and answers.

Run by launchd (install_listener.sh, label com.peter.voice-desk-listener). It sits ASLEEP with
the microphone open and only reacts to a sentence that starts with a name:

    "Jarvis, what time is it"   -> one turn with the orchestrator, spoken back, then asleep
    "hey Kylie" / "Kylie?"      -> "Kylie here." and ten seconds to say the question
    "talk to Esther"            -> switches the line to Esther and says so
    "mute" / "unmute"           -> ignores everything except "unmute" (or M in the dashboard)

After the reply it is asleep again: every turn starts with a name. Everything is transcribed
locally by whisper-server; only a sentence that starts with a name ever leaves the Mac.

Echo: the mic is DEAF while it speaks and for a second after, and also while any other
program on this Mac is speaking (afplay / say: the Claude Stop hook, Hermes voice mode).
It holds ~/.voice_desk/window.lock like the window does, so the Claude Stop hook stays
silent while it runs, and the window and the listener never both own the mic.

State for the dashboard goes to ~/.voice_desk/listener.json (service.py).
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import settings as S
import service
import stt
import tts
import voices
from brain import Brain, addressed

# VOICE_DESK_LISTENER_DEBUG=1 also logs what it heard and ignored. Off by default: the mic is
# always open, and room talk that was not meant for it should not end up in a file.
DEBUG = os.environ.get("VOICE_DESK_LISTENER_DEBUG") == "1"


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class ForeignSpeech:
    """Is any other program on this Mac speaking right now? Polled in the background (pgrep
    is a process spawn; the audio callback must stay cheap) and read from the callback."""

    def __init__(self, names=S.FOREIGN_PLAYERS, every: float = 0.25) -> None:
        self.pattern = "|".join(names)
        self.every = every
        self._playing = False
        self._stop = threading.Event()

    def start(self) -> "ForeignSpeech":
        threading.Thread(target=self._poll, daemon=True, name="foreign-speech").start()
        return self

    def _poll(self) -> None:
        while not self._stop.wait(self.every):
            try:
                r = subprocess.run(["/usr/bin/pgrep", "-x", self.pattern], capture_output=True, timeout=5)
                self._playing = r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                self._playing = False

    def playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        self._stop.set()


class Listener:
    def __init__(self, mic=None, whisper=None, brain: Optional[Brain] = None, player=None,
                 synth: Callable[..., Optional[Path]] = tts.synthesize,
                 foreign=None, status: Optional[service.Status] = None,
                 clock: Callable[[], float] = time.monotonic,
                 chime: Callable[[str], None] = tts.chime) -> None:
        self.clock = clock
        self.chime = chime              # turn-taking tones (tests pass a recorder)
        self._in_conversation = False   # a reply was spoken and the open line has not lapsed yet
        self.speaking = False
        self.quiet_until = 0.0          # deaf until then (echo tail after any speech)
        self.follow_until = 0.0         # awake (no name needed) until then
        self.foreign = foreign or ForeignSpeech()
        if mic is None:
            from audio import Microphone
            mic = Microphone(deaf=self.deaf)
        self.mic = mic
        self.whisper = whisper or stt.Whisper()
        self.brain = brain or Brain()
        self.player = player or tts.Player()
        self.synth = synth
        self.status = status or service.Status()
        self._quit = threading.Event()
        self._lock_fh = None
        self._speak_lock = threading.Lock()   # the reply and a "still working" line never overlap
        self._thinking = False                # a "still working" line is only spoken while a turn runs

    # -- the echo guard -------------------------------------------------------------------------
    def deaf(self) -> bool:
        """True while the mic must not be heard: we are speaking, something else on this Mac
        is speaking, or less than ECHO_TAIL_SEC has passed since either stopped."""
        now = self.clock()
        if self.speaking:
            return True
        if self.foreign.playing():
            self.quiet_until = max(self.quiet_until, now + S.ECHO_TAIL_SEC)
            return True
        return now < self.quiet_until

    # -- state ------------------------------------------------------------------------------------
    def awake(self) -> bool:
        return self.clock() < self.follow_until

    def _rest_state(self) -> str:
        if service.is_muted():
            return "muted"
        return "listening" if self.awake() else "asleep"

    def settle(self) -> None:
        self.status.set_state(self._rest_state())

    def _on_the_line(self, target: str) -> None:
        self.status.update(target=target, name=self.brain.name(target))

    # -- speaking ---------------------------------------------------------------------------------
    def speak(self, text: str, after: str = "", only_if: Callable[[], bool] = lambda: True) -> None:
        """Say text with the mic deaf, then keep it deaf for ECHO_TAIL_SEC. One voice at a time:
        a second caller waits; `only_if` is checked again once it is its turn."""
        if not text:
            return
        # Speak as whoever is on the line: each Hermes profile's own voice (voices.py), unless
        # the dashboard switched per-profile voices off. Test stubs take one argument.
        try:
            voice = voices.voice_for(self.brain.target())
            path = self.synth(text, voice)
        except TypeError:
            voice = None
            path = self.synth(text)
        if voice is not None:
            self.status.update(voice=voices.short(voice), voice_source=voice.source)
        if not path:
            self.status.update(note="no voice available")
            return
        try:
            with self._speak_lock:
                if not only_if():
                    return
                self.speaking = True
                self.mic.drain()
                self.status.set_state("speaking")
                try:
                    self.player.play(path)
                    self.player.wait()
                    tail_end = self.clock() + S.ECHO_TAIL_SEC
                    self.quiet_until = max(self.quiet_until, tail_end)
                    while self.clock() < tail_end and not self._quit.is_set():   # the light stays "speaking" while deaf
                        time.sleep(0.05)
                finally:
                    self.speaking = False
                    self.mic.drain()
                    if after:
                        self.status.set_state(after)
        finally:
            shutil.rmtree(Path(path).parent, ignore_errors=True)

    def _status_line(self, phrase: str) -> None:
        """'still working on it' while a brain thinks (called from the brain's ticker thread).
        Dropped if the answer arrived in the meantime."""
        if not self._thinking:
            return
        self.status.update(note=phrase)
        self.speak(phrase + ".", after="thinking", only_if=lambda: self._thinking)

    # -- one heard sentence -----------------------------------------------------------------------
    def handle(self, text: str) -> str:
        """Act on one transcribed sentence. Returns what happened (for logs and tests):
        ignored | muted | unmuted | still-muted | awake | stopped | turn"""
        text = (text or "").strip()
        if not text:
            return "ignored"
        norm = stt.normalize(text)
        # Asleep, the listener needs a hail ("hey Jarvis", "ok Kylie", "talk to Esther"); a bare name in
        # passing ("Jarvis said...") stays ignored. Once awake, the name alone still switches the line.
        hit = addressed(text, hailed=not self.awake())
        if hit is None and not self.awake():
            hit_bare = addressed(text)                           # "Jarvis, mute" still counts as "mute" below
        else:
            hit_bare = hit
        bare = stt.normalize(hit_bare[1]) if hit_bare else norm
        mute_words = {stt.normalize(w) for w in S.MUTE_WORDS}
        unmute_words = {stt.normalize(w) for w in S.UNMUTE_WORDS}

        if service.is_muted():
            # whisper sometimes hears "unmute" as "mute"; nobody says "mute" to a muted mic, so both
            # unmute. A long pause-less stretch can glue "unmute" onto other words, so it counts anywhere.
            if bare in unmute_words or bare in mute_words or re.search(r"\bun\s?mute\b", norm):
                service.set_muted(False)
                self.follow_until = 0.0
                self.speak("Listening again. Say a name to start.", after="asleep")
                log("unmuted by voice")
                return "unmuted"
            self.status.update(ignored="(muted)")
            return "still-muted"
        if bare in mute_words:
            service.set_muted(True)
            self.follow_until = 0.0
            self.speak("Muted. Say unmute when you want me back.", after="muted")
            log("muted by voice")
            return "muted"

        if hit:
            target, rest = hit
            self.brain.set_target(target)
            self._on_the_line(target)
            if not rest:
                self.speak(f"{self.brain.name(target)} here.", after="listening")
                self.follow_until = self.clock() + S.FOLLOW_UP_SEC
                self.settle()
                return "awake"
            text = rest
        elif not self.awake():
            # a bare name + a control word ("Jarvis, stop") is harmless and still obeyed while asleep
            if hit_bare and bare in {stt.normalize(w) for w in S.STOP_WORDS | S.SLEEP_WORDS | S.GOODBYE_WORDS}:
                self.follow_until = 0.0
                self.settle()
                return "stopped"
            self.status.update(ignored=text[:80])
            return "ignored"
        else:
            target = self.brain.target()
            self._on_the_line(target)

        norm = stt.normalize(text)
        if norm in {stt.normalize(w) for w in S.STOP_WORDS | S.SLEEP_WORDS | S.GOODBYE_WORDS}:
            self.follow_until = 0.0
            self._in_conversation = False             # ended on purpose: "Okay." says so, no falling tone
            if norm not in {stt.normalize(w) for w in S.STOP_WORDS}:
                self.speak("Okay.", after="asleep")
            self.settle()
            return "stopped"
        for w in S.TRAILING_SEND_WORDS:
            if text.lower().endswith(" " + w):
                text = text[: -len(w) - 1].rstrip()
        self.turn(text, target)
        return "turn"

    def turn(self, text: str, target: str) -> None:
        self.status.update(heard=text, reply="", note="", turns=self.status.data.get("turns", 0) + 1)
        self.status.set_state("thinking")
        log(f"turn -> {self.brain.name(target)}: {text[:80]!r}")
        t0 = time.monotonic()
        try:
            tts.tick()
        except Exception:
            pass
        self._thinking = True
        try:
            reply = self.brain.ask(text, on_status=self._status_line, to=target)
        except Exception as exc:                      # keep listening whatever one turn does
            reply = f"I hit an error: {exc}"
        finally:
            self._thinking = False
        self.status.update(reply=reply, note="", took=round(time.monotonic() - t0, 1))
        log(f"reply from {self.brain.name(target)} in {time.monotonic()-t0:.1f}s: {reply[:80]!r}")
        self.speak(tts.speakable(reply) or "I did not get an answer back.")
        # Peter 2026-10-21: keep the conversation open after every reply, so the back-and-forth needs
        # no name; the pop says "your turn", silence for CONVERSATION_SEC ends it (falling tone).
        self.follow_until = self.clock() + S.CONVERSATION_SEC
        self._in_conversation = True
        self._chime(S.SOUND_YOUR_TURN)
        self.settle()

    def _chime(self, sound: str) -> None:
        try:
            self.chime(sound)
        except Exception:
            pass

    def _maybe_end_conversation(self) -> None:
        """The open line ran out with no reply from Peter: say so with a falling tone."""
        if self._in_conversation and not self.awake() and not self._thinking and not self.speaking:
            self._in_conversation = False
            log("conversation over (silence)")
            if not service.is_muted():
                self._chime(S.SOUND_ASLEEP)

    # -- the dashboard's orders and the heartbeat ---------------------------------------------------
    def _ticker(self) -> None:
        while not self._quit.wait(0.5):
            for order in service.take_orders():
                log(f"order from the dashboard: {order}")
                if order == "hush":
                    self.player.stop()
                elif order == "new_thread":
                    self.brain.new_thread()
                    self.status.update(note=f"new conversation with {self.brain.name()}")
                elif order == "sleep":
                    self.follow_until = 0.0
            if self.status.state in ("asleep", "listening", "muted"):
                self._maybe_end_conversation()
                self.settle()                     # the follow-up window ran out, or M was pressed
            reason = ("speaking" if self.speaking else "other speech" if self.foreign.playing()
                      else "echo tail" if self.clock() < self.quiet_until else "")
            self.status.update(level=int(getattr(self.mic, "level", 0)), muted=service.is_muted(), deaf=reason)

    # -- lifecycle ------------------------------------------------------------------------------------
    def _take_window_lock(self) -> bool:
        fh = open(S.STATE_DIR / "window.lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._lock_fh = fh
        return True

    def quit(self, *_a) -> None:
        self._quit.set()
        self.player.stop()
        try:
            self.brain.cancel()
        except Exception:
            pass

    def run(self) -> None:
        threading.Thread(target=self._ticker, daemon=True, name="ticker").start()
        while not self._take_window_lock():           # the Voice Desk window has the mic; wait for it
            self.status.update(note="the Voice Desk window is open; waiting for it to close")
            self.status.set_state("standby")
            if self._quit.wait(5):
                return self._off()
        self.status.update(note="")
        try:
            self.status.set_state("starting")
            self.foreign.start()
            self.whisper.start()
            self.mic.start()
        except Exception as exc:
            self.status.update(note=f"could not start: {exc}")
            self.status.set_state("error")
            log(f"could not start: {exc}")
            time.sleep(10)                             # launchd restarts us; do not spin
            return self._off()
        self._on_the_line(self.brain.target())
        log(f"listening for a name (on the line: {self.status.data['name']})")
        self.settle()
        while not self._quit.is_set():
            try:
                self.settle()
                limit = S.MAX_UTTERANCE_SEC if self.awake() else S.ASLEEP_MAX_UTTERANCE_SEC
                audio = self.mic.listen(timeout_sec=2, should_abort=self._quit.is_set, max_sec=limit,
                                        on_state=lambda s: self.status.set_state("hearing") if s == "hearing"
                                        else self.settle())
                if audio is None:
                    continue
                text = self.transcribe(audio)
                what = self.handle(text)
                if what == "ignored":
                    if DEBUG and text:
                        log(f"ignored ({len(audio) / S.SAMPLE_RATE:.1f}s): {text[:120]!r}")
                    self.settle()
                else:
                    log(f"heard ({what}): {text[:80]!r}")
            except Exception as exc:                   # one bad turn never stops the listener
                log(f"turn failed: {exc}")
                self.status.update(note=f"turn failed: {exc}")
                self.speaking = False
                time.sleep(1)
        self._off()

    def transcribe(self, audio) -> str:
        from audio import write_wav
        tmp = Path(tempfile.mkdtemp(prefix="vd-listen-"))
        try:
            return self.whisper.transcribe(write_wav(audio, tmp / "in.wav"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _off(self) -> None:
        for f in (self.mic.stop, self.whisper.stop, self.foreign.stop):
            try:
                f()
            except Exception:
                pass
        self.status.set_state("off")
        log("listener off")


def main() -> None:
    listener = Listener()
    signal.signal(signal.SIGTERM, listener.quit)
    signal.signal(signal.SIGINT, listener.quit)
    log(f"Voice Desk listener starting (pid {listener.status.data['pid']})")
    listener.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
