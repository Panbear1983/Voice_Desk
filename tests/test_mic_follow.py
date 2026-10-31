"""Offline tests for following the Mac's default microphone (headsets, AirPods).

No real audio device, no herdr, no Telegram, no model: a fake PortAudio (sounddevice) and a fake
CoreAudio "which mic is the default" answer. The fake PortAudio keeps the device list it saw at its
last start, exactly like the real one, so a headset is only visible after the switch restarts it.

  .venv/bin/python -m unittest tests.test_mic_follow
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VOICE_DESK_STATE", tempfile.mkdtemp(prefix="vd-mic-"))

import settings as S  # noqa: E402
import audio  # noqa: E402
import listener as L  # noqa: E402
import service  # noqa: E402

BUILT_IN, HEADSET, AIRPODS = "MacBook Pro Microphone", "USB Headset", "AirPods Pro"
MARK = {BUILT_IN: 1111, HEADSET: 2222, AIRPODS: 3333}      # first sample of every loud block says which mic heard it


class World:
    """The Mac: which mics are plugged in, which one is the default, and what Peter is saying."""

    def __init__(self) -> None:
        self.plugged = [BUILT_IN]
        self.default = BUILT_IN
        self.broken: set[str] = set()       # in the list, but will not open (AirPods still negotiating)
        self.dead: set[str] = set()         # opened, then stopped delivering audio (unplugged)
        self.speech: list[tuple[int, object]] = []   # (loud blocks, action after each block) queued
        self.lock = threading.Lock()
        self.rng = np.random.default_rng(3)

    def say(self, blocks: int, at=None) -> None:
        """Peter says one sentence `blocks` long; at=(k, fn) runs fn after the k-th loud block."""
        with self.lock:
            self.speech.append((blocks, at))

    def block(self, device: str, loud_state: dict) -> np.ndarray:
        if loud_state.get("left", 0) <= 0:
            with self.lock:
                if self.speech:
                    n, at = self.speech.pop(0)
                    loud_state.update(left=n, total=n, at=at)
        if loud_state.get("left", 0) > 0:
            b = self.rng.normal(0, 3000, S.BLOCK).astype(np.int16)
            b[0] = MARK[device]
            loud_state["left"] -= 1
            done = loud_state["total"] - loud_state["left"]
            at = loud_state.get("at")
            if at and done == at[0]:
                at[1]()
            return b.reshape(-1, 1)
        return self.rng.normal(0, 40, S.BLOCK).astype(np.int16).reshape(-1, 1)


class FakeSD:
    """sounddevice as the Microphone uses it: a cached device list, _terminate/_initialize, InputStream."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.events: list[str] = []
        self.speech_state: dict = {}          # one Peter: the sentence continues on whichever mic is open
        self._initialize()

    def _initialize(self) -> None:
        self.devices = [{"name": n, "max_input_channels": 1} for n in self.world.plugged]
        self.events.append("init")

    def _terminate(self) -> None:
        self.events.append("terminate")

    def query_devices(self, device=None):
        return self.devices if device is None else self.devices[device]

    def InputStream(self, samplerate, channels, dtype, device, blocksize, callback):  # noqa: N802
        return FakeStream(self, device, callback)


class FakeStream:
    def __init__(self, sd: FakeSD, device, callback) -> None:
        self.sd, self.callback = sd, callback
        self.device = 0 if device is None else device
        self.name = sd.devices[self.device]["name"]
        self._run = threading.Event()

    def start(self) -> None:
        if self.name in self.sd.world.broken:
            raise RuntimeError("Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")
        self.sd.events.append(f"open {self.name}")
        self._run.set()
        threading.Thread(target=self._feed, daemon=True).start()

    def _feed(self) -> None:
        while self._run.is_set():
            if self.name in self.sd.world.dead:
                time.sleep(0.01)
                continue
            self.callback(self.sd.world.block(self.name, self.sd.speech_state), S.BLOCK, None, None)
            time.sleep(0.001)

    def stop(self) -> None:
        self._run.clear()
        self.sd.events.append(f"close {self.name}")

    def close(self) -> None:
        self._run.clear()


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_mic(world: World, clock=time.monotonic, deaf=None):
    sd = FakeSD(world)
    logs: list[str] = []
    mic = audio.Microphone(deaf=deaf, default_input=lambda: world.default,
                           built_in=lambda: [n for n in world.plugged if n == BUILT_IN],
                           sd_module=sd, clock=clock, log=logs.append)
    return mic, sd, logs


def loud_marks(samples: np.ndarray) -> dict:
    """How many loud blocks of each mic are in the audio."""
    out: dict = {}
    for i in range(0, samples.size - S.BLOCK + 1, S.BLOCK):
        blk = samples[i:i + S.BLOCK]
        if np.sqrt(np.mean(blk.astype(float) ** 2)) > 1000:
            out[int(blk[0])] = out.get(int(blk[0]), 0) + 1
    return out


class Saved:
    """Put module settings back after each test."""
    names = ("MIC_CHECK_SEC", "MIC_RETRY_SEC", "MIC_RETRY_TRIES", "MIC_STALL_SEC", "ECHO_TAIL_SEC")

    def setUp(self) -> None:
        self._saved = {n: getattr(S, n) for n in self.names}

    def tearDown(self) -> None:
        for n, v in self._saved.items():
            setattr(S, n, v)


class MicrophoneFollows(Saved, unittest.TestCase):
    def test_opens_the_default_by_name(self):
        world = World()
        world.plugged = [HEADSET, BUILT_IN]
        world.default = BUILT_IN
        mic, sd, _ = make_mic(world)
        mic.start()
        try:
            self.assertEqual(mic.device_name, BUILT_IN)
            self.assertIn(f"open {BUILT_IN}", sd.events)
        finally:
            mic.stop()

    def test_switch_waits_for_the_check_and_restarts_portaudio(self):
        S.MIC_CHECK_SEC = 2.0
        clock, world = FakeClock(), World()
        mic, sd, logs = make_mic(world, clock=clock)
        mic.start()
        try:
            self.assertFalse(mic.switch_due())
            world.plugged.append(HEADSET)             # headset plugged in and made the default
            world.default = HEADSET
            clock.t += 1.0
            self.assertFalse(mic.switch_due(), "CoreAudio is asked only every MIC_CHECK_SEC")
            clock.t += 1.5
            mic.last_block_at = clock.t
            self.assertTrue(mic.switch_due())
            self.assertNotIn(HEADSET, [d["name"] for d in sd.devices], "PortAudio's list is stale until restarted")
            gaps = mic.gaps
            self.assertEqual(mic.switch(), HEADSET)
            self.assertEqual(sd.events[-4:], [f"close {BUILT_IN}", "terminate", "init", f"open {HEADSET}"])
            self.assertGreater(mic.gaps, gaps, "nothing from before the switch joins the next sentence")
            self.assertTrue(any(f"now listening on {HEADSET}" in m for m in logs), logs)
            clock.t += 2.5
            mic.last_block_at = clock.t
            self.assertFalse(mic.switch_due())
            world.plugged.remove(HEADSET)             # unplugged: the Mac goes back to its own mic
            world.default = BUILT_IN
            clock.t += 2.5
            mic.last_block_at = clock.t
            self.assertTrue(mic.switch_due())
            self.assertEqual(mic.switch(), BUILT_IN)
        finally:
            mic.stop()

    def test_a_mic_that_will_not_open_falls_back_and_is_retried(self):
        S.MIC_CHECK_SEC, S.MIC_RETRY_SEC, S.MIC_RETRY_TRIES = 1.0, 5.0, 3
        clock, world = FakeClock(), World()
        mic, sd, logs = make_mic(world, clock=clock)
        mic.start()
        try:
            world.plugged.append(AIRPODS)
            world.broken.add(AIRPODS)                 # still negotiating
            world.default = AIRPODS
            clock.t += 1.1
            mic.last_block_at = clock.t
            self.assertTrue(mic.switch_due())
            self.assertEqual(mic.switch(), BUILT_IN, "never deaf: back on the Mac's own mic")
            self.assertIsNotNone(mic._stream)
            self.assertTrue(any("could not open AirPods Pro" in m for m in logs), logs)
            self.assertTrue(any(f"fell back to {BUILT_IN}" in m for m in logs), logs)
            clock.t += 1.1
            mic.last_block_at = clock.t
            self.assertFalse(mic.switch_due(), "not retried before MIC_RETRY_SEC")
            clock.t += 5.0
            mic.last_block_at = clock.t
            self.assertTrue(mic.switch_due(), "retried after MIC_RETRY_SEC")
            world.broken.discard(AIRPODS)             # ready now
            self.assertEqual(mic.switch(), AIRPODS)
        finally:
            mic.stop()

    def test_retries_stop_after_a_few_and_start_again_on_the_next_change(self):
        S.MIC_CHECK_SEC, S.MIC_RETRY_SEC, S.MIC_RETRY_TRIES = 1.0, 1.0, 2
        clock, world = FakeClock(), World()
        mic, sd, logs = make_mic(world, clock=clock)
        mic.start()

        def tick() -> bool:
            clock.t += 1.1
            mic.last_block_at = clock.t
            return mic.switch_due()
        try:
            world.plugged.append(AIRPODS)
            world.broken.add(AIRPODS)
            world.default = AIRPODS
            tries = 0
            for _ in range(10):
                if tick():
                    tries += 1
                    self.assertEqual(mic.switch(), BUILT_IN)
            self.assertEqual(tries, 2, "a broken mic is not reopened forever")
            world.default = BUILT_IN                  # Peter picks the Mac mic, then AirPods again
            self.assertFalse(tick())
            world.default = AIRPODS
            world.broken.discard(AIRPODS)
            self.assertTrue(tick(), "a new change gets fresh tries")
            self.assertEqual(mic.switch(), AIRPODS)
        finally:
            mic.stop()

    def test_nothing_opens_then_it_comes_back(self):
        S.MIC_CHECK_SEC, S.MIC_STALL_SEC = 1.0, 3.0
        clock, world = FakeClock(), World()
        mic, sd, logs = make_mic(world, clock=clock)
        mic.start()
        try:
            world.broken.add(BUILT_IN)
            world.plugged.append(HEADSET)
            world.broken.add(HEADSET)
            world.default = HEADSET
            clock.t += 1.1
            mic.last_block_at = clock.t
            self.assertTrue(mic.switch_due())
            self.assertIsNone(mic.switch())
            self.assertTrue(any("NO microphone would open" in m for m in logs), logs)
            clock.t += 1.1
            self.assertTrue(mic.switch_due(), "with no stream it keeps trying")
            self.assertIsNone(mic.switch())
            clock.t += 1.1
            self.assertFalse(mic.switch_due(), "... backing off, so the log does not fill up")
            world.broken.clear()
            clock.t += 60
            self.assertTrue(mic.switch_due())
            self.assertEqual(mic.switch(), HEADSET)
        finally:
            mic.stop()

    def test_a_mic_that_goes_silent_is_reopened(self):
        S.MIC_CHECK_SEC, S.MIC_STALL_SEC = 0.05, 0.3
        world = World()
        world.plugged.append(HEADSET)
        world.default = HEADSET
        mic, sd, logs = make_mic(world)
        mic.start()
        try:
            time.sleep(0.1)
            self.assertFalse(mic.switch_due())
            world.dead.add(HEADSET)                   # unplugged; CoreAudio still names it for a moment
            time.sleep(0.5)
            self.assertTrue(mic.switch_due())
            world.plugged.remove(HEADSET)
            world.default = BUILT_IN
            self.assertEqual(mic.switch(), BUILT_IN)
        finally:
            mic.stop()

    def test_unplugged_mid_sentence_keeps_what_was_heard(self):
        S.MIC_STALL_SEC = 0.3
        world = World()
        world.plugged.append(HEADSET)
        world.default = HEADSET
        mic, sd, logs = make_mic(world)
        mic.start()
        try:
            world.say(40, at=(20, lambda: world.dead.add(HEADSET)))
            got = mic.listen(timeout_sec=3)
            self.assertIsNotNone(got, "listen() must not hang on a vanished mic")
            self.assertEqual(loud_marks(got).get(MARK[HEADSET], 0), 20)
        finally:
            mic.stop()


# --- the listener's own loop, end to end -----------------------------------------------------------
class FakeWhisper:
    def __init__(self) -> None:
        self.heard: list[np.ndarray] = []
        self.order: list[str] = []
        self.sd: FakeSD | None = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def transcribe(self, wav) -> str:
        with wave.open(str(wav)) as w:
            self.heard.append(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16))
        if self.sd is not None:
            self.sd.events.append("transcribed")
        return "the weather is nice today"           # no name: the listener ignores it, nothing is sent


class FakeForeign:
    def __init__(self) -> None:
        self.on = False

    def playing(self) -> bool:
        return self.on

    def start(self):
        return self

    def stop(self) -> None:
        pass


class NoPlayer:
    def stop(self) -> None:
        pass


class NoBrain:
    def target(self) -> str:
        return "claude"

    def name(self, target=None) -> str:
        return "Claude"

    def cancel(self) -> None:
        pass

    def ask(self, *a, **k):
        raise AssertionError("no turn may run in this test")


class ListenerFollows(Saved, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        S.MIC_CHECK_SEC, S.MIC_STALL_SEC, S.ECHO_TAIL_SEC = 0.05, 2.0, 0.0
        service.set_muted(False)
        self.world = World()
        self.whisper = FakeWhisper()
        self.foreign = FakeForeign()
        self.lst = L.Listener(mic=object(), whisper=self.whisper, brain=NoBrain(), player=NoPlayer(),
                              synth=lambda *a: None, foreign=self.foreign, chime=lambda s: None)
        self.mic, self.sd, self.logs = make_mic(self.world, deaf=self.lst.deaf)
        self.lst.mic = self.mic
        self.whisper.sd = self.sd

    def run_listener(self) -> threading.Thread:
        t = threading.Thread(target=self.lst.run, daemon=True)
        t.start()
        self.wait(lambda: self.mic.device_name is not None and self.lst.status.state == "asleep")
        return t

    def stop_listener(self, t: threading.Thread) -> None:
        self.lst.quit()
        t.join(5)
        self.assertFalse(t.is_alive())
        if self.lst._lock_fh:                          # the next test's listener takes window.lock again
            self.lst._lock_fh.close()

    def wait(self, cond, secs: float = 8.0) -> None:
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.01)
        self.fail(f"timed out; events={self.sd.events[-8:]} logs={self.logs}")

    def test_change_while_peter_speaks_waits_for_the_end_of_the_sentence(self):
        t = self.run_listener()
        try:
            self.assertEqual(self.mic.device_name, BUILT_IN)

            def headset_connects() -> None:            # mid-sentence: plugged in and made the default
                self.world.plugged.append(HEADSET)
                self.world.default = HEADSET
                self.sd.events.append("headset connected")
            self.world.say(60, at=(20, headset_connects))   # 1.8 s sentence, headset arrives 0.6 s in
            self.wait(lambda: len(self.whisper.heard) == 1)
            first = loud_marks(self.whisper.heard[0])
            self.assertEqual(first, {MARK[BUILT_IN]: 60}, "the whole sentence, from the mic it started on")
            self.wait(lambda: self.mic.device_name == HEADSET)
            ev = self.sd.events
            self.assertLess(ev.index("headset connected"), ev.index("transcribed"))
            self.assertLess(ev.index("transcribed"), ev.index(f"open {HEADSET}"),
                            "the stream moved only after the sentence was finished")
            self.assertTrue(any(f"now listening on {HEADSET}" in m for m in self.logs), self.logs)
            self.assertEqual(self.lst.status.data.get("mic"), HEADSET)
            self.world.say(30)                         # Peter keeps talking: heard through the headset
            self.wait(lambda: len(self.whisper.heard) == 2)
            self.assertEqual(loud_marks(self.whisper.heard[1]), {MARK[HEADSET]: 30})
        finally:
            self.stop_listener(t)

    def test_no_switch_while_something_is_speaking(self):
        S.MIC_CHECK_SEC = 0.0
        self.mic.start()
        try:
            self.world.plugged.append(HEADSET)
            self.world.default = HEADSET
            self.mic.last_block_at = self.mic.clock()
            self.lst.speaking = True
            self.assertIsNone(self.lst._follow_mic(), "not while our answer plays")
            self.lst.speaking = False
            self.lst._thinking = True
            self.assertIsNone(self.lst._follow_mic(), "not during a turn")
            self.lst._thinking = False
            self.foreign.on = True
            self.assertIsNone(self.lst._follow_mic(), "not while another program speaks")
            self.foreign.on = False
            self.assertEqual(self.mic.device_name, BUILT_IN)
            self.assertEqual(self.lst._follow_mic(), HEADSET)
        finally:
            self.mic.stop()

    def test_deaf_gate_still_works_on_the_new_mic(self):
        t = self.run_listener()
        try:
            self.world.plugged.append(HEADSET)
            self.world.default = HEADSET
            self.wait(lambda: self.mic.device_name == HEADSET)
            self.foreign.on = True                     # e.g. the Claude Stop hook speaking
            dropped = self.mic.dropped
            self.world.say(40)
            self.wait(lambda: self.mic.dropped >= dropped + 40)
            self.foreign.on = False
            self.world.say(30)
            self.wait(lambda: len(self.whisper.heard) == 1)
            self.assertEqual(loud_marks(self.whisper.heard[0]), {MARK[HEADSET]: 30},
                             "what was said while deaf never reached whisper")
        finally:
            self.stop_listener(t)


if __name__ == "__main__":
    unittest.main()
