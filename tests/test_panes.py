"""Offline: how the switchboard reads a Hermes window, on text captured from Peter's real windows.

  .venv/bin/python tests/test_panes.py        (or python -m unittest tests.test_panes)

Kylie 2026-11-17: after "Hello, Kylie" Voice Desk said "Kylie is listening" although her microphone
never opened, then twice decided an end phrase had been said when none was. Her window is narrow
(31 columns): the voice bar is only " 🎤 Ctrl+B", and while a turn runs the input box's top rule
carries a spinner ("── ⠹ Working… ───").

tests/fixtures/panes/*.txt are real `herdr pane read` captures of the four Hermes windows taken
2026-11-17 (narrow bar idle, busy spinner, recording, transcribing). Frames that could not be
captured on demand (voice off, a stuck recorder, a half-drawn frame) are built below from those
real frames and Hermes' own strings (locales/en.yaml), and say so.

No real window is touched: start() runs against FakeWindow, which stands in for herdr.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401  (the fake no-window herdr and throw-away state folder)

import panes as P  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "panes"
_real_sleep = time.sleep


def fx(name: str) -> str:
    return (FIX / name).read_text()


NARROW_IDLE = fx("narrow_idle_kylie.txt")                 # Kylie: " 🎤 Ctrl+B", voice on, nobody talking
TITLED_IDLE = fx("narrow_idle_titled_rule_esther.txt")    # Esther: the top rule is "─ Esther online status reply ─"
AFTER_END = fx("narrow_idle_after_end_phrase_jarvis.txt")  # Jarvis: end phrase, then voice back on (rearm)
BUSY = fx("busy_working_jarvis.txt")                      # "── ⠹ Working… · 0s ───"
BUSY_TOOL = fx("busy_tool_jarvis.txt")                    # "── ⠼ (⌐■_■) mulling... · 11s..."
BUSY_PREP = fx("busy_preparing_jarvis.txt")               # "── ⠴ ⚡ Preparing mcp__termi..."
REC_TAIL = fx("rec_narrow_jarvis.txt")                    # " ● REC" (6 rows: the row with "● Recording" is above it)
TRANSCRIBING = fx("transcribing_narrow_jarvis.txt")       # " ◉ STT"
BUSY_LONG = fx("busy_long_turn_jarvis.txt")               # "── ⠧ Working… · 1m · ↓ 3.9k..."
# Real reads with no input box: Hermes was redrawing (3 of ~1,100 reads in 7 minutes of a busy turn).
# The old reader called all three "off", and one "off" read is enough to end a hand-off as an end phrase.
HALF_TOOL = fx("half_drawn_tool_row_jarvis.txt")          # ends: separator pair, a tool row
HALF_SEP = fx("half_drawn_separator_jarvis.txt")          # ends: a separator pair
HALF_REARM = fx("half_drawn_after_rearm_jarvis.txt")      # ends: "/voice off  to disable voice" help text

RULE = "───────────────────────────────"
STATS = " ☤ agy-opus · 1s"
# Built: the real recording frame with the row above restored. Hermes prints
# "● Recording... (auto-stops on silence | Ctrl+B to stop & exit continuous)" once the microphone
# is open; at 31 columns it wraps into exactly the two rows the capture shows plus this one.
REC_CONFIRMED = "● Recording... (auto-stops on s\n" + REC_TAIL
# Built: ● REC on the bar but no "● Recording..." line. Hermes sets the REC flag before it opens the
# stream (cli_voice_mixin._voice_start_recording), so a recorder that hangs while opening looks like this.
REC_STUCK = "\n".join(NARROW_IDLE.splitlines()[:-4]) + f"\n{RULE}\n● recording... Ctrl+B to stop,\n ● REC\n{STATS}\n"
# Built: Kylie's frame with voice mode off (Hermes hides the voice bar; the placeholder is the session's own).
OFF = "\n".join(NARROW_IDLE.splitlines()[:-4]) + f"\nVoice mode disabled.\n{RULE}\n☤ Ask me anything\n{STATS}\n"
# Built: a turn running in a window whose voice is off (no voice bar at all).
BUSY_NO_BAR = "\n".join(l for l in BUSY.splitlines() if "🎤" not in l) + "\n"
# Built: half-drawn / scrolled-away frames - scrollback with no input box under it.
HALF_DRAWN = "│ my mic is actually          │\n│ recording                   │\n" + RULE + "\n─────────\n"
SCROLLED = "\n".join(NARROW_IDLE.splitlines()[:8]) + "\n"


class StateOfTest(unittest.TestCase):
    def test_narrow_bar_is_voice_on_not_recording(self):
        self.assertEqual(P.state_of(NARROW_IDLE), "voice-idle")
        self.assertEqual(P.state_of(TITLED_IDLE), "voice-idle")
        self.assertEqual(P.state_of(AFTER_END), "voice-idle")

    def test_busy_spinner_is_busy_never_off(self):
        for frame in (BUSY, BUSY_TOOL, BUSY_PREP, BUSY_LONG, BUSY_NO_BAR):
            self.assertEqual(P.state_of(frame), "busy", frame)

    def test_real_half_drawn_frames_are_unclear_not_off(self):
        for frame in (HALF_TOOL, HALF_SEP, HALF_REARM):
            self.assertEqual(P.state_of(frame), "unclear", frame)

    def test_recording_and_transcribing(self):
        self.assertEqual(P.state_of(REC_TAIL), "recording")
        self.assertEqual(P.state_of(REC_STUCK), "recording")
        self.assertEqual(P.state_of(TRANSCRIBING), "transcribing")

    def test_off_needs_a_drawn_input_box(self):
        self.assertEqual(P.state_of(OFF), "off")
        self.assertEqual(P.state_of(HALF_DRAWN), "unclear")
        self.assertEqual(P.state_of(SCROLLED), "unclear")
        self.assertEqual(P.state_of(""), "gone")
        self.assertEqual(P.state_of(None), "gone")

    def test_recording_confirmed_only_with_hermes_own_line(self):
        self.assertTrue(P.recording_confirmed(REC_CONFIRMED))
        self.assertFalse(P.recording_confirmed(REC_STUCK))
        self.assertFalse(P.recording_confirmed(NARROW_IDLE))
        # an old recording that already ended does not confirm a new REC
        old = "● Recording... (auto-stops on s\nilence)\n\nSilence detected, auto-stopping\n...\nTranscribing...\n"
        self.assertFalse(P.recording_confirmed(old + f"{RULE}\n ● REC\n{STATS}\n"))

    def test_screen_reads_the_bottom_not_the_scrolled_view(self):
        calls = []
        real = P._herdr
        P._herdr = lambda *a, **k: calls.append(a) or type("R", (), {"returncode": 0, "stdout": NARROW_IDLE})()
        try:
            P.state("wH:pT")
        finally:
            P._herdr = real
        args = list(calls[0])
        self.assertEqual(args[args.index("--source") + 1], "recent")

    def test_off_is_believed_only_when_read_twice(self):
        # Built: a wide window caught mid-redraw - an unwrapped 40-cell scrollback rule, then a tool row.
        # Statically it looks like a drawn box with no voice bar; the next read shows the turn.
        blip = "x" * 70 + "\n" + "─" * 40 + "\n💻 $ ls + 2 commands  0.3s\n"
        self.assertEqual(P.state_of(blip), "off")
        for reads, want in (([blip, BUSY], "busy"), ([blip, NARROW_IDLE], "voice-idle"), ([OFF, OFF], "off"),
                            ([HALF_TOOL, HALF_SEP, BUSY], "busy")):
            with Reads(reads):
                self.assertEqual(P.state("wH:pR"), want, reads)

    def test_rearm_types_nothing_on_a_blip(self):
        by_phrase = "Stop phrase detected — ending voice chat.\n\nVoice mode disabled.\n"
        blip_after_phrase = by_phrase + "x" * 70 + "\n" + "─" * 40 + "\n💻 $ ls + 2 commands  0.3s\n"
        with Reads([blip_after_phrase, blip_after_phrase, BUSY, BUSY]) as r:
            self.assertFalse(P.rearm("wH:pR"))
        self.assertEqual(r.sent, [])

    def test_rearm_still_undoes_a_real_end_phrase(self):
        ended = (f"Stop phrase detected — ending voice chat.\n\nVoice mode disabled.\n{RULE}\n"
                 f"☤ Ask me anything\n{STATS}\n")
        with Reads([ended]) as r:
            self.assertTrue(P.rearm("wH:pR"))
        self.assertIn(("pane", "send-text", "wH:pR", "/voice on"), r.sent)


class Reads:
    """screen_tail returns these frames one per read (the last one repeats); records anything sent."""

    def __init__(self, frames):
        self.frames, self.sent = list(frames), []

    def __enter__(self):
        self._real = (P.screen_tail, P._herdr, P.time.sleep)
        P.screen_tail = lambda pane, lines=6, ansi=False: self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        P._herdr = lambda *a, **k: self.sent.append(a) or type("R", (), {"returncode": 0, "stdout": ""})()
        P.time.sleep = lambda s: None
        return self

    def __exit__(self, *exc):
        P.screen_tail, P._herdr, P.time.sleep = self._real


class FakeWindow:
    """herdr for one Hermes window: shows `frame`; each Ctrl+B shows the next entry of `on_press`."""

    def __init__(self, frame: str, on_press=()):
        self.frame, self.on_press, self.keys, self.typed = frame, list(on_press), [], []

    def __enter__(self):
        self._real = (P.screen_tail, P._herdr, P.time.sleep)
        P.screen_tail = lambda pane, lines=6, ansi=False: self.frame
        P._herdr = self._herdr
        P.time.sleep = lambda s: _real_sleep(0.005)       # P.time is the time module itself: restored on exit
        return self

    def __exit__(self, *exc):
        P.screen_tail, P._herdr, P.time.sleep = self._real

    def _herdr(self, *args, **kw):
        if args[:2] == ("pane", "send-keys"):
            self.keys.append(args[3])
            if args[3] == "ctrl+b" and self.on_press:
                self.frame = self.on_press.pop(0)
        elif args[:2] == ("pane", "send-text"):
            self.typed.append(args[3])
        return type("R", (), {"returncode": 0, "stdout": ""})()


class StartTest(unittest.TestCase):
    WAIT = 0.15

    def test_started_only_when_the_window_really_records(self):
        with FakeWindow(NARROW_IDLE, [REC_CONFIRMED]) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "started")
        self.assertEqual(w.keys, ["ctrl+b"])

    def test_ignored_ctrl_b_is_pressed_once_more(self):
        with FakeWindow(NARROW_IDLE, [NARROW_IDLE, REC_CONFIRMED]) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "started")
        self.assertEqual(w.keys, ["ctrl+b", "ctrl+b"])

    def test_ignored_twice_is_an_honest_failure(self):
        with FakeWindow(NARROW_IDLE, [NARROW_IDLE, NARROW_IDLE]) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "no-rec")
        self.assertEqual(w.keys, ["ctrl+b", "ctrl+b"])

    def test_rec_without_a_microphone_is_stuck_and_not_pressed_again(self):
        with FakeWindow(NARROW_IDLE, [REC_STUCK]) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "mic-stuck")
        self.assertEqual(w.keys, ["ctrl+b"])           # a second Ctrl+B on REC would mean "stop"

    def test_already_stuck_before_we_press(self):
        with FakeWindow(REC_STUCK) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "mic-stuck")
        self.assertEqual(w.keys, [])

    def test_already_recording(self):
        with FakeWindow(REC_CONFIRMED) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "started")
        self.assertEqual(w.keys, [])

    def test_busy_turn_waits_without_pressing(self):
        for frame in (BUSY, BUSY_TOOL, TRANSCRIBING, HALF_DRAWN):
            with FakeWindow(frame) as w:
                self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "busy")
            self.assertEqual(w.keys, [], frame)

    def test_turn_starting_after_the_press_is_busy(self):
        with FakeWindow(NARROW_IDLE, [BUSY]) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "busy")
        self.assertEqual(w.keys, ["ctrl+b"])

    def test_stale_continuous_bar_is_not_taken_for_recording(self):
        wide = f"{RULE}\n🎤 ❯ \n{RULE}\n 🎤 Voice mode | TTS on | Continuous  —  Ctrl+B to record \n{STATS}\n"
        with FakeWindow(wide, [wide, wide]) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "no-rec")
        self.assertEqual(w.keys, ["ctrl+b", "ctrl+b"])

    def test_voice_off_is_turned_on_then_recorded(self):
        class W(FakeWindow):
            def _herdr(self, *args, **kw):
                if args[:2] == ("pane", "send-keys") and args[3] == "enter" and self.typed == ["/voice on"]:
                    self.frame = NARROW_IDLE              # /voice on took
                return super()._herdr(*args, **kw)
        with W(OFF, [REC_CONFIRMED]) as w:
            self.assertEqual(P.start("wH:pT", wait_sec=self.WAIT), "started")
        self.assertEqual(w.typed, ["/voice on"])
        self.assertEqual(w.keys, ["enter", "ctrl+b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
