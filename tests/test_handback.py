"""Offline: the hand-back on the window's first beep (Peter 2026-11-17 via Kylie, card t_69c8023c).

  .venv/bin/python -m unittest tests.test_handback

Before: Peter says an end phrase in a handed Hermes window -> the window beeps (recording stopped)
-> transcribes -> "Stop phrase detected" -> Voice Desk noticed only on later status-bar polls (and,
with the window's voice switched straight back on, sometimes only after the 60 s idle grace), then
played its own falling pair. "hello Jarvis" worked only after that second beep.

Now the first beep is the only signal. From the moment the handed window stops recording (its bar
says ◉ Transcribing) the room mic listens and holds what it hears. An end phrase on that window's
screen hands back at once, silently, and the held words count ("hello Jarvis"). A normal turn, a new
recording, a closed window, "No speech detected" or PANE_BUFFER_SEC without a verdict throws them away.
Echo deafness (our own speech, any other program playing, ECHO_TAIL_SEC) is unchanged.

Nothing real is touched: no microphone, no herdr window, no whisper, no speakers.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401  (the fake no-window herdr and throw-away state folder)

import settings as S  # noqa: E402
import service  # noqa: E402
import panes as P  # noqa: E402
import listener as L  # noqa: E402
from brain import Brain  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "panes"
AFTER_END = (FIX / "narrow_idle_after_end_phrase_jarvis.txt").read_text()   # real: end phrase, voice back on
TRANSCRIBING = (FIX / "transcribing_narrow_jarvis.txt").read_text()          # real: " ◉ STT"
REC_TAIL = (FIX / "rec_narrow_jarvis.txt").read_text()                       # real: " ● REC"
BUSY = (FIX / "busy_working_jarvis.txt").read_text()                         # real: a turn is running
RULE = "───────────────────────────────"
STATS = " ☤ agy-claude · 1.1d"
IDLE_FOOTER = f"{RULE}\n🎤 type or Ctrl+B to record\n 🎤 Ctrl+B\n{STATS}\n"
REC_LINE = "● Recording... (auto-stops on s\n"          # Hermes prints it once the mic is really open


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


class FakeForeign:
    def __init__(self) -> None:
        self.on = False

    def playing(self) -> bool:
        return self.on

    def start(self):
        return self

    def stop(self) -> None:
        pass


class FakePlayer:
    def __init__(self) -> None:
        self.played: list = []

    def play(self, path) -> None:
        self.played.append(Path(path).read_text())

    def wait(self) -> None:
        pass

    def is_playing(self) -> bool:
        return False

    def stop(self) -> None:
        pass


class FakeBrain(Brain):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list = []

    def ask(self, text, on_status=lambda s: None, on_text=lambda s: None, to=None):  # type: ignore[override]
        self.asked.append(text)
        return "ok"


class FakeWhisper:
    """What whisper makes of the next utterance."""

    def __init__(self) -> None:
        self.text = ""

    def transcribe(self, path) -> str:
        return self.text


def fake_synth(text: str) -> Path:
    p = Path(tempfile.mkdtemp(prefix="vd-say-")) / "say.wav"
    p.write_text(text)
    return p


class FakeWindows:
    """herdr stand-in with screen text: the bar state AND the rows Hermes printed (what look() reads)."""
    ACTIVE = P.ACTIVE

    def __init__(self) -> None:
        self.windows = {"assistant": "wH:pR", "orchestrator": "wH:pN"}
        self.screen: dict = {"wH:pR": IDLE_FOOTER, "wH:pN": IDLE_FOOTER}   # None = herdr cannot read it
        self.pressed: list = []

    def find(self, profile):
        return self.windows.get(profile)

    def look(self, pane):
        tail = self.screen.get(pane)
        return P.state_of(tail), tail

    def state(self, pane):
        return self.look(pane)[0]

    def start(self, pane, wait_sec=0):
        self.pressed.append(pane)
        self.screen[pane] = "old reply text\n" + REC_LINE + REC_TAIL
        return "started"

    def rearm(self, pane):
        return False

    # what Hermes draws, step by step (real captured frames, joined the way the window scrolls)
    def transcribing(self, pane):
        self.screen[pane] = REC_LINE + TRANSCRIBING

    def ended(self, pane):
        self.screen[pane] = REC_LINE + AFTER_END

    def busy(self, pane):
        self.screen[pane] = REC_LINE + "Silence detected, auto-stopping\n...\nTranscribing...\n" + BUSY

    def no_speech(self, pane):
        self.screen[pane] = REC_LINE + "Silence detected, auto-stopping\n...\nNo speech detected.\n" + IDLE_FOOTER


def loud(rng, marker=None):
    b = rng.normal(0, 3000, S.BLOCK).astype(np.int16).reshape(-1, 1)
    if marker is not None:
        b[0, 0] = marker
    return b


def quiet(rng):
    return rng.normal(0, 50, S.BLOCK).astype(np.int16).reshape(-1, 1)


class HandBackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (S.PANE_MODE, S.ECHO_TAIL_SEC, S.HAND_BACK_CHIME, S.PANE_BUFFER_SEC)
        S.PANE_MODE = True
        S.ECHO_TAIL_SEC = 0.0
        service.set_muted(False)
        self.chimes: list = []
        self.clock = FakeClock()
        self.foreign = FakeForeign()
        self.whisper = FakeWhisper()
        self.lst = L.Listener(mic=None, whisper=self.whisper, brain=FakeBrain(), player=FakePlayer(),
                              synth=fake_synth, foreign=self.foreign, clock=self.clock, chime=self.chimes.append)
        self.lst.transcribe = lambda audio: self.whisper.text     # no wav files, no whisper-server
        self.win = FakeWindows()
        self.lst.panes = self.win

    def tearDown(self) -> None:
        S.PANE_MODE, S.ECHO_TAIL_SEC, S.HAND_BACK_CHIME, S.PANE_BUFFER_SEC = self._saved

    def hand_to_kylie(self) -> None:
        self.assertEqual(self.lst.handle("Hello Kylie"), "handed")
        self.lst._watch_hand()                          # she records Peter's words: the room mic is deaf
        self.assertTrue(self.lst.deaf())
        self.assertIsNone(self.lst._held)

    def heard_later(self, text: str) -> dict:
        """Peter's utterance comes out of the mic while the window still decides: _heard() in its own
        thread, exactly as the main loop calls it."""
        self.whisper.text = text
        out: dict = {}
        th = threading.Thread(target=lambda: out.setdefault("what", self.lst._heard(np.zeros(16000, np.int16))),
                              daemon=True)
        th.start()
        out["thread"] = th
        return out

    # -- the screen verdict -------------------------------------------------------------------------
    def test_verdict_reads_the_real_end_phrase_frame(self):
        self.assertEqual(P.heard_verdict(REC_LINE + AFTER_END), "end")
        self.assertEqual(P.heard_verdict(AFTER_END), "end")

    def test_verdict_waits_while_transcribing_recording_or_busy(self):
        for tail in (REC_LINE + TRANSCRIBING, REC_LINE + REC_TAIL, REC_LINE + BUSY, IDLE_FOOTER, None, ""):
            self.assertEqual(P.heard_verdict(tail), "pending", repr((tail or "")[-60:]))

    def test_an_older_end_phrase_above_a_new_recording_is_not_a_verdict(self):
        self.assertEqual(P.heard_verdict(AFTER_END.replace(IDLE_FOOTER.strip(), "") + REC_LINE + REC_TAIL), "pending")
        self.assertEqual(P.heard_verdict("\n".join(AFTER_END.splitlines()[:-4]) + "\n" + REC_LINE + IDLE_FOOTER), "pending")

    def test_agent_text_quoting_the_stop_line_is_not_an_end_phrase(self):
        quoted = REC_LINE + "Transcribing...\nJarvis: the window prints\n\n" + "\n".join(
            ["line"] * 7) + "\nStop phrase detected — ending\nand then voice goes off.\n" + IDLE_FOOTER
        self.assertEqual(P.heard_verdict(quoted), "pending")

    def test_local_stt_model_line_counts_like_transcribing(self):
        local = (REC_LINE + "Preparing local STT model 'bas\ne' (first use may download it\nfrom Hugging Face)...\n"
                 "Stop phrase detected — ending v\noice chat.\n\nVoice mode disabled.\n" + IDLE_FOOTER)
        self.assertEqual(P.heard_verdict(local), "end")

    def test_no_speech_is_a_verdict_of_nothing(self):
        self.assertEqual(P.heard_verdict(REC_LINE + "No speech detected.\n" + IDLE_FOOTER), "nothing")

    # -- 1 end phrase, then "hello Jarvis" right after the first beep --------------------------------
    def test_end_phrase_then_immediate_wake(self):
        self.hand_to_kylie()
        self.win.transcribing("wH:pR")                  # her first beep: recording stopped, transcribing
        self.lst._watch_hand()
        self.assertIsNotNone(self.lst._held, "listening starts at the first beep")
        self.assertFalse(self.lst.deaf(), "the room mic hears from the first beep (no echo playing)")
        got = self.heard_later("Hello Jarvis")          # said right after the beep, before her verdict
        time.sleep(0.2)
        self.assertEqual(self.win.pressed, ["wH:pR"], "nothing is acted on before her verdict")
        self.assertTrue(got["thread"].is_alive(), "the words wait for her verdict")
        self.win.ended("wH:pR")                         # "Stop phrase detected"; her voice is back on, idle
        t0 = time.monotonic()
        self.lst._watch_hand()                          # ONE look at her screen is enough
        got["thread"].join(3)
        self.assertLess(time.monotonic() - t0, 1.0, "acted on at once, no second wait")
        self.assertEqual(got.get("what"), "handed")
        self.assertEqual(self.win.pressed, ["wH:pR", "wH:pN"], "Ctrl+B in Jarvis's window")
        self.assertEqual(self.lst.handed, "wH:pN")
        self.assertNotIn(S.SOUND_ASLEEP, self.chimes, "no second beep after an end phrase")

    def test_verdict_before_the_words_finish_keeps_them_whole(self):
        """The real microphone: Peter starts "hello Jarvis" after the beep, her verdict lands while he is
        still speaking. The hand-back must not drain the mic or cut his words with a deaf gap."""
        rng = np.random.default_rng(7)
        lst = L.Listener(mic=None, whisper=self.whisper, brain=FakeBrain(), player=FakePlayer(), synth=fake_synth,
                         foreign=self.foreign, clock=time.monotonic, chime=self.chimes.append)
        lst.panes = self.win
        self.assertEqual(lst.handle("Hello Kylie"), "handed")
        lst._watch_hand()
        self.win.transcribing("wH:pR")
        lst._watch_hand()
        mic = lst.mic
        marker = 7777

        def peter() -> None:
            time.sleep(0.15)
            for i in range(40):                         # "hello Jar-" ...
                mic._on_audio(loud(rng, marker), S.BLOCK, None, None); time.sleep(0.002)
                if i == 20:
                    self.win.ended("wH:pR")
                    lst._watch_hand()                   # ... her verdict lands mid-word ...
            for _ in range(int(S.SILENCE_SEND_SEC * 1000 / S.BLOCK_MS) + 10):   # ... "-vis", then his pause
                mic._on_audio(quiet(rng), S.BLOCK, None, None); time.sleep(0.002)

        threading.Thread(target=peter, daemon=True).start()
        audio = mic.listen(timeout_sec=3)
        self.assertIsNone(lst.handed)
        self.assertIsNotNone(audio, "his words survive the hand-back")
        self.assertGreaterEqual(int(np.sum(audio == marker)), 38, "all of them, not just the part after it")

    def test_second_beep_is_a_setting(self):
        S.HAND_BACK_CHIME = True
        self.hand_to_kylie()
        self.win.transcribing("wH:pR")
        self.lst._watch_hand()
        self.win.ended("wH:pR")
        self.lst._watch_hand()
        self.assertIsNone(self.lst.handed)
        self.assertEqual(self.chimes, [S.SOUND_ASLEEP])

    def test_end_phrase_with_nothing_said_meanwhile_just_hands_back(self):
        self.hand_to_kylie()
        self.win.transcribing("wH:pR")
        self.lst._watch_hand()
        self.win.ended("wH:pR")
        self.lst._watch_hand()
        self.assertIsNone(self.lst.handed)
        self.assertEqual(self.lst.status.state, "asleep")
        self.assertEqual(self.chimes, [])
        self.assertFalse(self.lst.deaf(), "listening for a name")

    def test_end_phrase_seen_without_the_transcribing_frame_still_hands_back_at_once(self):
        """The 0.5 s watch can miss a short ◉ Transcribing; her screen still says what happened."""
        self.hand_to_kylie()
        self.win.ended("wH:pR")
        self.lst._watch_hand()
        self.assertIsNone(self.lst.handed, "not after PANE_END_POLLS reads, not after the 60 s grace")
        self.assertEqual(self.chimes, [])

    # -- 2 a normal turn: the held words are thrown away, the window keeps the conversation ------------
    def test_normal_turn_discards(self):
        self.hand_to_kylie()
        asked = len(self.lst.brain.asked)
        self.win.transcribing("wH:pR")
        self.lst._watch_hand()
        got = self.heard_later("Hello Jarvis, what is on my calendar")   # e.g. Peter talking to someone in the room
        time.sleep(0.1)
        self.win.busy("wH:pR")                          # her verdict: a normal turn, she is answering
        self.lst._watch_hand()
        got["thread"].join(3)
        self.assertEqual(got.get("what"), "dropped")
        self.assertEqual(self.win.pressed, ["wH:pR"], "no window was touched")
        self.assertEqual(len(self.lst.brain.asked), asked, "no turn of our own")
        self.assertEqual(self.lst.handed, "wH:pR", "she keeps the conversation")
        self.assertIsNone(self.lst._held)
        self.assertTrue(self.lst.deaf(), "the room mic is deaf again")
        self.assertEqual(self.chimes, [], "silently")
        self.assertEqual(self.lst.player.played, [], "and nothing said")

    def test_each_recording_gets_its_own_listening_spell(self):
        self.hand_to_kylie()
        for _ in range(3):                               # three normal turns in a row
            self.win.transcribing("wH:pR")
            self.lst._watch_hand()
            self.assertIsNotNone(self.lst._held)
            self.win.busy("wH:pR")
            self.lst._watch_hand()
            self.assertIsNone(self.lst._held)
            self.win.transcribing("wH:pR")              # still the same turn's frame on a late read: no new spell
            self.lst._watch_hand()
            self.assertIsNone(self.lst._held)
            self.win.screen["wH:pR"] = REC_LINE + REC_TAIL   # her reply was spoken; she records again
            self.lst._watch_hand()
        self.assertEqual(self.lst.handed, "wH:pR")

    def test_discards_on_new_recording_no_speech_timeout_and_closed_window(self):
        for end in ("recording", "no-speech", "timeout", "gone"):
            with self.subTest(end=end):
                self.lst._take_back("test")
                self.win.screen["wH:pR"] = IDLE_FOOTER
                self.hand_to_kylie()
                self.win.transcribing("wH:pR")
                self.lst._watch_hand()
                got = self.heard_later("Hello Jarvis")
                time.sleep(0.1)
                if end == "recording":
                    self.win.screen["wH:pR"] = REC_LINE + REC_TAIL
                elif end == "no-speech":
                    self.win.no_speech("wH:pR")
                elif end == "timeout":
                    self.clock.advance(S.PANE_BUFFER_SEC + 0.1)
                else:
                    self.win.screen["wH:pR"] = None
                self.lst._watch_hand()
                got["thread"].join(3)
                self.assertEqual(got.get("what"), "dropped")
                self.assertEqual(self.win.pressed[-1], "wH:pR", "Jarvis's window never touched")
                self.assertIsNone(self.lst._held)

    def test_words_never_count_while_she_has_the_conversation(self):
        """Even with no listening spell open (a stray utterance at the very moment one closed)."""
        self.hand_to_kylie()
        self.whisper.text = "Hello Jarvis"
        self.assertEqual(self.lst._heard(np.zeros(16000, np.int16)), "dropped")
        self.assertEqual(self.win.pressed, ["wH:pR"])

    # -- 3 echo: her spoken reply and any other playback are never heard ------------------------------
    def test_echo_not_acted_on(self):
        self.hand_to_kylie()
        self.win.transcribing("wH:pR")
        self.lst._watch_hand()
        S.ECHO_TAIL_SEC = 1.0
        self.foreign.on = True                          # anything playing (her reply, her beep, a hook)
        self.assertTrue(self.lst.deaf(), "deaf while another program plays, listening spell or not")
        self.foreign.on = False
        self.assertTrue(self.lst.deaf(), "... and for ECHO_TAIL_SEC after")
        self.clock.advance(1.1)
        self.assertFalse(self.lst.deaf())
        # her verdict: a normal turn; her spoken reply follows. Whatever the mic made of the time before
        # her verdict ("Hello Jarvis" in her reply, say) is dropped, and her reply itself is never heard.
        got = self.heard_later("Hello Jarvis")
        time.sleep(0.1)
        self.win.busy("wH:pR")
        self.lst._watch_hand()
        got["thread"].join(3)
        self.assertEqual(got.get("what"), "dropped")
        self.foreign.on = True
        self.win.screen["wH:pR"] = REC_LINE + "Transcribing...\n" + IDLE_FOOTER
        self.lst._watch_hand()
        self.assertTrue(self.lst.deaf())
        self.assertEqual(self.win.pressed, ["wH:pR"])

    def test_echo_blocks_never_reach_the_mic_during_the_spell(self):
        """The real Microphone gate: loud audio while something plays is thrown away, spell open or not."""
        rng = np.random.default_rng(3)
        lst = L.Listener(mic=None, whisper=self.whisper, brain=FakeBrain(), player=FakePlayer(), synth=fake_synth,
                         foreign=self.foreign, clock=time.monotonic, chime=self.chimes.append)
        lst.panes = self.win
        lst.handle("Hello Kylie")
        lst._watch_hand()
        self.win.transcribing("wH:pR")
        lst._watch_hand()
        self.assertIsNotNone(lst._held)
        S.ECHO_TAIL_SEC = 0.3
        self.foreign.on = True
        for _ in range(30):
            lst.mic._on_audio(loud(rng), S.BLOCK, None, None)
        self.assertEqual(len(lst.mic._blocks), 0)
        self.foreign.on = False
        for _ in range(5):
            lst.mic._on_audio(loud(rng), S.BLOCK, None, None)
        self.assertEqual(len(lst.mic._blocks), 0, "the echo tail too")

    # -- what must not change -------------------------------------------------------------------------
    def test_other_hand_backs_keep_their_falling_pair(self):
        self.hand_to_kylie()
        self.win.screen["wH:pR"] = None                 # her window was closed
        for _ in range(S.PANE_END_POLLS):
            self.lst._watch_hand()
        self.assertIsNone(self.lst.handed)
        self.assertEqual(self.chimes, [S.SOUND_ASLEEP])

    def test_recording_and_her_turns_stay_deaf(self):
        self.hand_to_kylie()
        for frame in (REC_LINE + REC_TAIL, REC_LINE + BUSY):
            self.win.screen["wH:pR"] = frame
            self.lst._watch_hand()
            self.assertTrue(self.lst.deaf())
            self.assertIsNone(self.lst._held)


if __name__ == "__main__":
    unittest.main()
