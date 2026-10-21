"""Offline backtest for the always-on listener: no microphone, no speakers, no tokens.

  .venv/bin/python tests/test_listener.py

1 name -> thread routing for Jarvis, Kylie, Esther, Wanna Buffet, Claude and Gemini
  (including how whisper actually spells them), and that a name mid-sentence is not a wake
2 wake-word stripping: the question that follows the name keeps the speaker's own words
3 the listener's decisions: asleep ignores, a name wakes + switches + runs one turn, a bare
  name opens a short follow-up window, mute / unmute, the window lock
4 mute-during-speech (the echo path): blocks arriving while it speaks, during the 1 s tail
  after, and while another program on the Mac speaks never reach listen(); an utterance
  cut by a deaf gap is thrown away whole
5 status file round trip: atomic write, read back, states in order, staleness, mute flag,
  dashboard orders; and the dashboard mounts and shows it without touching the mic
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VOICE_DESK_STATE"] = tempfile.mkdtemp(prefix="vd-test-")
# a fake Hermes profiles dir with the four real profile names, so routing does not depend on this Mac
_profiles = Path(tempfile.mkdtemp(prefix="vd-profiles-"))
# each with the voice its real config carries (the shape Hermes writes: tts.providers.<p>.voice)
_VOICE_CFG = {
    "assistant": "tts:\n  provider: natural\n  providers:\n    natural:\n      type: command\n      voice: en-US-AvaNeural\n  edge:\n    voice: en-US-JennyNeural\n",
    "wanna-buffet": "tts:\n  provider: natural\n  providers:\n    natural:\n      type: command\n      voice: en-US-AndrewNeural\n  edge:\n    voice: en-US-AndrewNeural\n",
    "orchestrator": "tts:\n  provider: natural\n  providers:\n    natural:\n      voice: en-US-JennyNeural\n",
    "tax_angel_esther": "tts:\n  provider: natural\n  providers:\n    natural:\n      type: command\n      voice: alloy\n",
}
for _p in ("assistant", "builder", "orchestrator", "tax_angel_esther", "wanna-buffet"):
    (_profiles / _p).mkdir()
    (_profiles / _p / "config.yaml").write_text("model: x\n" + _VOICE_CFG.get(_p, ""))
os.environ["HERMES_PROFILES_DIR"] = str(_profiles)
_agy = Path(tempfile.mkdtemp(prefix="vd-agy-")) / "agy"
_agy.write_text("#!/bin/sh\n")
os.environ["VOICE_DESK_GEMINI_BIN"] = str(_agy)

import settings as S  # noqa: E402
import service  # noqa: E402
import stt  # noqa: E402
from audio import Microphone  # noqa: E402
from brain import Brain, addressed  # noqa: E402
import listener as L  # noqa: E402

ok = True


def report(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f"  {detail}" if detail and not passed else ""))


# 1 ---------------------------------------------------------------------------------------------
def test_routing() -> None:
    b = Brain()
    want = {
        "Jarvis": "hermes:orchestrator", "Kylie": "hermes:assistant", "Esther": "hermes:tax_angel_esther",
        "Wanna Buffet": "hermes:wanna-buffet", "Claude": "claude", "Gemini": "gemini",
    }
    for name, target in want.items():
        report(f"routing: '{name}' resolves to {target}", b.resolve(name) == target, str(b.resolve(name)))
        report(f"routing: spoken name of {target} is {name}", b.name(target) == name, b.name(target))
        for said in (f"{name}, what time is it?", f"Hey {name}.", f"Talk to {name}.", f"hey, {name}! what time is it",
                     f"OK {name}. What time is it", f"switch to {name}", f"let me talk to {name} please"):
            hit = addressed(said)
            report(f"routing: {said!r} -> {target}", hit is not None and hit[0] == target, str(hit))
        said = b.command(f"talk to {name}")
        report(f"routing: 'talk to {name}' switches and says {name}",
               b.target() == target and said is not None and name in said, f"{b.target()} {said!r}")
    # what whisper (small, with and without the vocabulary hint) really wrote, 2026-10-21
    heard = {"Javis, what time is it?": "hermes:orchestrator", "Jaarvis, what time is it?": "hermes:orchestrator",
             "Talk to Javas": "hermes:orchestrator", "Hey Claud, what's new?": "claude",
             "Hey wanna buffet?": "hermes:wanna-buffet", "Wanna buff it? How is the market today?": "hermes:wanna-buffet",
             "Ester, how much tax do I owe?": "hermes:tax_angel_esther", "Hey Jarvis,現在幾點?": "hermes:orchestrator",
             "嘿 克勞德，現在幾點": "claude", "talk to the orchestrator": "hermes:orchestrator",
             "builder, are you there": "hermes:builder", "Talk to tax angel Esther": "hermes:tax_angel_esther"}
    for said, target in heard.items():
        hit = addressed(said)
        report(f"routing (as whisper wrote it): {said!r} -> {target}", hit is not None and hit[0] == target, str(hit))
    for said in ("I told Jarvis yesterday", "what time is it", "the weather in Taipei", "Jarvisville is a town",
                 "Tell Kylie I said hi", "Claudette called", ""):
        report(f"routing: no name at the start -> no wake: {said!r}", addressed(said) is None, str(addressed(said)))
    # asleep, the listener wants a hail (Peter 2026-10-21: a bare "Jarvis" fires too easily)
    for said, target in {"Hey Jarvis, what time is it?": "hermes:orchestrator", "OK Kylie, remind me at five.": "hermes:assistant",
                         "Talk to Esther.": "hermes:tax_angel_esther", "hey, wanna buffet! how is the market": "hermes:wanna-buffet",
                         "um, hey Jarvis": "hermes:orchestrator", "嘿 克勞德，現在幾點": "claude",
                         "let me talk to Jarvis please": "hermes:orchestrator"}.items():
        hit = addressed(said, hailed=True)
        report(f"hail: {said!r} wakes {target}", hit is not None and hit[0] == target, str(hit))
    for said in ("Jarvis, what time is it?", "Jarvis.", "Kylie said no", "um Jarvis", "so Esther, what do I file",
                 "Wanna Buffet, how is the market today?", "Javis, what time is it?"):
        report(f"hail: bare name does not wake: {said!r}", addressed(said, hailed=True) is None, str(addressed(said, hailed=True)))
    report("hail: 'Hey Jarvis, what time is it?' strips to the question",
           (addressed("Hey Jarvis, what time is it?", hailed=True) or ("", ""))[1] == "what time is it?")
    # the old commands still work
    report("routing: 'switch to Opus' is still a model change", b.command("switch to Opus") is None)
    report("routing: 'talk to me about the budget' is a sentence", b.command("talk to me about the budget") is None)
    report("routing: the phone's to='wanna-buffet' still resolves", b.resolve("wanna-buffet") == "hermes:wanna-buffet")
    report("routing: the phone's to='hermes:orchestrator' still resolves", b.resolve("hermes:orchestrator") == "hermes:orchestrator")
    report("routing: 'who can I talk to' lists the new names",
           all(n in (b.command("who can I talk to") or "") for n in ("Jarvis", "Kylie", "Esther", "Wanna Buffet")),
           str(b.command("who can I talk to")))
    b.set_target("claude")


# 2 ---------------------------------------------------------------------------------------------
def test_stripping() -> None:
    cases = {
        "Jarvis, what time is it?": "what time is it?",
        "Jarvis. What time is it?": "What time is it?",
        "Hey, Jarvis, what's on today?": "what's on today?",
        "OK Kylie, remind me at five.": "remind me at five.",
        "Wanna Buffet, how is the market today?": "how is the market today?",
        "Hey Wanna Buffet!": "",
        "Talk to Esther.": "",
        "let me talk to Esther please": "",
        "Esther, now that the year is over, what do I file?": "now that the year is over, what do I file?",
        "Hey Jarvis,現在幾點?": "現在幾點?",
        "嘿 克勞德，現在幾點": "現在幾點",
        "Hey Claude, what's new?": "what's new?",
        "Jarvis mute": "mute",
    }
    for said, rest in cases.items():
        hit = addressed(said)
        report(f"strip: {said!r} -> {rest!r}", hit is not None and hit[1] == rest, str(hit))
    # the window's old wake-word stripper is untouched
    from engine import Engine
    report("strip: window's 'hey claude' stripper still works",
           Engine._strip_wake_word(stt.normalize("Hey Claude, what's new?"), "Hey Claude, what's new?") == "what's new?")
    report("strip: whisper's echo of the vocabulary hint is dropped",
           stt.echoes_prompt("Jarvis, Kylie, Esther, Wanna Buffet, Claude, Gemini.", S.WHISPER_PROMPT)
           and not stt.echoes_prompt("Jarvis, ask Kylie what Esther said", "Jarvis, Kylie. x") is True
           and not stt.echoes_prompt("Jarvis, what time is it?", S.WHISPER_PROMPT))


# --- fakes ----------------------------------------------------------------------------------------
class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakePlayer:
    """Plays for `secs` of real time; records what the mic gate said while playing."""

    def __init__(self, secs: float = 0.0, during=None) -> None:
        self.secs, self.during, self.played = secs, during, []
        self._playing = False

    def play(self, path) -> None:
        self.played.append(Path(path).read_text())
        self._playing = True

    def wait(self) -> None:
        if self.during:
            self.during()
        time.sleep(self.secs)
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        self._playing = False


class FakeBrain(Brain):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, str]] = []

    def ask(self, text, on_status=lambda s: None, on_text=lambda s: None, to=None):  # type: ignore[override]
        handled = self.command(text)
        if handled:
            return handled
        self.asked.append((text, self.resolve(to) if to else self.target()))
        return f"**It is** ten past nine, says {self.name(to)}."


class FakeForeign:
    def __init__(self) -> None:
        self.on = False

    def playing(self) -> bool:
        return self.on

    def start(self):
        return self

    def stop(self) -> None:
        pass


def fake_synth(text: str) -> Path:
    p = Path(tempfile.mkdtemp(prefix="vd-say-")) / "say.wav"
    p.write_text(text)
    return p


CHIMES: list = []      # turn-taking tones the listener asked for (never played in tests)


def make_listener(clock=None, player=None, foreign=None) -> L.Listener:
    clock = clock or FakeClock()
    foreign = foreign or FakeForeign()
    lst = L.Listener(mic=None, whisper=object(), brain=FakeBrain(), player=player or FakePlayer(),
                     synth=fake_synth, foreign=foreign, clock=clock, chime=CHIMES.append)
    S.ECHO_TAIL_SEC = 0.0 if isinstance(clock, FakeClock) else S.ECHO_TAIL_SEC
    return lst


# 3 ---------------------------------------------------------------------------------------------
def test_listener_decisions() -> None:
    service.set_muted(False)
    S.BRAIN_FILE.write_text("claude")
    clock = FakeClock()
    lst = make_listener(clock=clock)
    lst.settle()
    report("listener: starts asleep", lst.status.state == "asleep", lst.status.state)
    report("listener: asleep, no name -> ignored, nothing sent", lst.handle("what time is it") == "ignored" and not lst.brain.asked)
    report("listener: asleep, a bare 'Jarvis, ...' is NOT enough (needs a hail)",
           lst.handle("Jarvis, what time is it?") == "ignored" and not lst.brain.asked)
    what = lst.handle("Hey Jarvis, what time is it?")
    report("listener: 'Hey Jarvis, what time is it' runs one turn", what == "turn", what)
    report("listener: ... with the orchestrator", lst.brain.asked[-1] == ("what time is it?", "hermes:orchestrator"),
           str(lst.brain.asked))
    report("listener: ... the sticky line is now Jarvis", lst.brain.target() == "hermes:orchestrator"
           and lst.status.data["name"] == "Jarvis")
    report("listener: ... the reply is spoken without markdown",
           lst.player.played[-1] == "It is ten past nine, says Jarvis.", str(lst.player.played))
    hist = [s for _, s in lst.status.data["history"]]
    report("listener: states in order asleep -> thinking -> speaking -> listening (conversation open)",
           hist[-4:] == ["asleep", "thinking", "speaking", "listening"], str(hist))
    report("listener: after the reply a 'your turn' pop plays", CHIMES[-1:] == [S.SOUND_YOUR_TURN], str(CHIMES))
    what = lst.handle("and tomorrow?")
    report("listener: within the open line a follow-up needs no name",
           what == "turn" and lst.brain.asked[-1] == ("and tomorrow?", "hermes:orchestrator"), str(lst.brain.asked))
    CHIMES.clear()
    clock.t += S.CONVERSATION_SEC + 1
    lst._maybe_end_conversation()
    lst.settle()
    report("listener: silence for the whole open line ends it with the falling tone",
           CHIMES == [S.SOUND_ASLEEP] and lst.status.state == "asleep", f"{CHIMES} {lst.status.state}")
    lst._maybe_end_conversation()
    report("listener: ... the falling tone plays only once", CHIMES == [S.SOUND_ASLEEP], str(CHIMES))
    report("listener: after the line lapses, the next turn needs a name again",
           lst.handle("and the day after?") == "ignored", str(lst.brain.asked[-1]))
    lst.handle("Hey Jarvis, one more thing")
    CHIMES.clear()
    report("listener: 'goodbye' ends the open line at once",
           lst.handle("goodbye") == "stopped" and lst.status.state == "asleep")
    clock.t += S.CONVERSATION_SEC + 1
    lst._maybe_end_conversation()
    report("listener: ... and then no falling tone (Okay. already said it)", CHIMES == [], str(CHIMES))

    # -- the safe phrase (Peter 2026-10-21: "I have no further questions, something like that") ------
    lst.handle("Hey Jarvis, what time is it?")
    CHIMES.clear()
    n_asked = len(lst.brain.asked)
    what = lst.handle("Thanks Jarvis, I have no further questions.")
    report("safe phrase: 'thanks Jarvis, I have no further questions' ends the conversation",
           what == "ended" and lst.status.state == "asleep" and not lst.awake(), f"{what} {lst.status.state}")
    report("safe phrase: ... with a spoken sign-off and the falling pair, nothing sent to the agent",
           lst.player.played[-1] == S.SIGN_OFF and CHIMES[-1:] == [S.SOUND_ASLEEP] and len(lst.brain.asked) == n_asked,
           f"{lst.player.played[-1]} {CHIMES}")
    report("safe phrase: afterwards a follow-up needs a name again", lst.handle("and tomorrow?") == "ignored")
    lst.handle("Hey Jarvis, what time is it?")
    CHIMES.clear()
    what = lst.handle("What's the weather in Taipei tomorrow, no further questions.")
    report("safe phrase: a question that comes with it is answered first, then the line closes",
           what == "ended" and lst.brain.asked[-1][0] == "What's the weather in Taipei tomorrow"
           and CHIMES == [S.SOUND_ASLEEP] and not lst.awake(), f"{lst.brain.asked[-1]} {CHIMES}")
    report("safe phrase: ignored while asleep (nothing to end, no hail)",
           lst.handle("I have no further questions") == "ignored")
    report("safe phrase: Mandarin works too", (lst.handle("Hey Jarvis") and lst.handle("沒有問題了")) == "ended")
    import tts as _T
    wav = Path(_T._beep_file("beep:880x1"))
    report("beeps: generated with Hermes' recipe (880 Hz, 120 ms, mono 16 kHz)",
           wav.exists() and 3_000 < wav.stat().st_size < 5_000, f"{wav} {wav.stat().st_size if wav.exists() else 0}")
    report("beeps: a plain file path plays as-is", _T._beep_file("/System/Library/Sounds/Tink.aiff") == "/System/Library/Sounds/Tink.aiff")
    what = lst.handle("Hey Kylie.")
    report("listener: bare 'hey Kylie' -> 'Kylie here.' and listening",
           what == "awake" and lst.player.played[-1] == "Kylie here." and lst.status.state == "listening", str(lst.player.played[-1]))
    lst.handle("what's on my calendar")
    report("listener: the next sentence goes to Kylie without her name",
           lst.brain.asked[-1] == ("what's on my calendar", "hermes:assistant"), str(lst.brain.asked[-1]))
    lst.handle("Esther")
    clock.t += S.FOLLOW_UP_SEC + 1
    report("listener: the follow-up window closes", lst.handle("how much do I owe") == "ignored")
    lst.handle("Talk to Wanna Buffet.")
    report("listener: 'talk to Wanna Buffet' switches + confirms with the new name",
           lst.brain.target() == "hermes:wanna-buffet" and lst.player.played[-1] == "Wanna Buffet here.", lst.player.played[-1])
    lst.handle("Gemini, hello")
    report("listener: Gemini by name", lst.brain.asked[-1] == ("hello", "gemini"), str(lst.brain.asked[-1]))
    lst.handle("Hey Claude, what's new?")
    report("listener: 'hey Claude' still works", lst.brain.asked[-1] == ("what's new?", "claude"), str(lst.brain.asked[-1]))
    n = len(lst.brain.asked)
    report("listener: 'Jarvis, stop' does not run a turn", lst.handle("Jarvis, stop") == "stopped" and len(lst.brain.asked) == n)
    # mute / unmute by voice
    report("listener: 'mute' mutes", lst.handle("Mute.") == "muted" and service.is_muted() and lst.status.state == "muted")
    report("listener: muted -> even a name is ignored", lst.handle("Jarvis, what time is it") == "still-muted"
           and len(lst.brain.asked) == n)
    report("listener: 'unmute' unmutes", lst.handle("Unmute.") == "unmuted" and not service.is_muted()
           and lst.status.state == "asleep")
    lst.handle("Jarvis, mute")
    report("listener: 'Jarvis, mute' mutes too", service.is_muted())
    report("listener: whisper's 'on MUDE' for unmute works", lst.handle("on MUDE.") == "unmuted" and not service.is_muted())
    lst.handle("Mute.")
    report("listener: 'unmute' glued to another sentence still unmutes (seen live)",
           lst.handle("Jarvis, what time is it? Unmute, please.") == "unmuted" and not service.is_muted())
    report("listener: unmuted, 'unmute' without a name is just ignored", lst.handle("Un-mute, please.") == "ignored")
    service.set_muted(True)
    lst.settle()
    report("listener: M in the dashboard (mute file) turns the light purple", lst.status.state == "muted")
    service.set_muted(False)
    lst.settle()
    report("listener: and back", lst.status.state == "asleep")
    # the window lock: the listener takes the same lock the window and the Stop hook use
    import fcntl
    report("listener: takes window.lock", lst._take_window_lock())
    other = open(S.STATE_DIR / "window.lock", "a")
    try:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = False
    except OSError:
        held = True
    report("listener: while it runs, the Stop hook's lock probe fails (so the hook stays silent)", held)
    other.close()
    lst._lock_fh.close()


# 4 ---------------------------------------------------------------------------------------------
def test_mute_during_speech() -> None:
    """The echo path. Speech-loud blocks are fed while the listener speaks, during the echo tail,
    and while another program speaks; none may come out of listen()."""
    rng = np.random.default_rng(1)
    loud = lambda: (rng.normal(0, 3000, S.BLOCK)).astype(np.int16).reshape(-1, 1)
    quiet = lambda: (rng.normal(0, 50, S.BLOCK)).astype(np.int16).reshape(-1, 1)
    S.ECHO_TAIL_SEC = 1.0
    foreign = FakeForeign()
    feed_log: dict[str, int] = {}

    lst_holder: dict = {}

    def during() -> None:                           # what the mic hears while the reply plays: the reply
        mic = lst_holder["l"].mic
        before = len(mic._blocks)
        for _ in range(60):                           # 1.8 s of loud audio
            mic._on_audio(loud(), S.BLOCK, None, None)
        feed_log["queued_while_speaking"] = len(mic._blocks) - before

    player = FakePlayer(secs=0.0, during=during)
    lst = L.Listener(mic=None, whisper=object(), brain=FakeBrain(), player=player, synth=fake_synth,
                     foreign=foreign, clock=time.monotonic)
    lst_holder["l"] = lst
    mic: Microphone = lst.mic
    report("echo: the listener's mic is gated by its deaf() check", mic.deaf == lst.deaf)

    t0 = time.monotonic()
    lst.speak("It is ten past nine.")
    took = time.monotonic() - t0
    report("echo: nothing heard while speaking is queued", feed_log.get("queued_while_speaking") == 0, str(feed_log))
    report("echo: speak() holds the mic deaf ~1 s after playback ends", 0.95 <= took <= 1.6, f"{took:.2f}s")

    # the tail: right after a foreign player stops, still deaf for ECHO_TAIL_SEC
    foreign.on = True
    report("echo: deaf while another program (Stop hook / Hermes voice) speaks", lst.deaf())
    for _ in range(30):
        mic._on_audio(loud(), S.BLOCK, None, None)
    report("echo: ... and its audio is not queued", len(mic._blocks) == 0)
    foreign.on = False
    report("echo: still deaf just after it stops (tail)", lst.deaf())
    time.sleep(S.ECHO_TAIL_SEC + 0.05)
    report("echo: hears again after the tail", not lst.deaf())

    # end to end through listen(): loud audio while deaf, then real speech after -> only the speech comes out
    S.ECHO_TAIL_SEC = 0.3
    marker = 7777

    def feed() -> None:
        time.sleep(0.2)
        foreign.on = True                                       # e.g. the Claude Stop hook speaking
        for _ in range(50):
            mic._on_audio(loud(), S.BLOCK, None, None); time.sleep(0.002)
        foreign.on = False
        for _ in range(20):
            mic._on_audio(quiet(), S.BLOCK, None, None); time.sleep(0.02)   # 0.4 s: through the 0.3 s tail
        for _ in range(40):                                     # Peter says something (marked samples)
            b = loud(); b[0, 0] = marker
            mic._on_audio(b, S.BLOCK, None, None); time.sleep(0.002)
        for _ in range(70):
            mic._on_audio(quiet(), S.BLOCK, None, None); time.sleep(0.002)

    threading.Thread(target=feed, daemon=True).start()
    audio = mic.listen(timeout_sec=6)
    n_marked = int(np.sum(audio == marker)) if audio is not None else 0
    loud_blocks = 0
    if audio is not None:
        for i in range(0, audio.size - S.BLOCK + 1, S.BLOCK):
            blk = audio[i:i + S.BLOCK]
            if np.sqrt(np.mean(blk.astype(float) ** 2)) > 1000 and blk[0] != marker:
                loud_blocks += 1
    report("echo: listen() returns only the speech that came after the echo",
           audio is not None and n_marked >= 30 and loud_blocks == 0, f"marked={n_marked} unmarked-loud={loud_blocks}")

    # an utterance cut in half by a deaf gap is dropped, not glued to what follows
    S.ECHO_TAIL_SEC = 0.0
    def feed2() -> None:
        time.sleep(0.2)
        for _ in range(30):
            mic._on_audio(loud(), S.BLOCK, None, None); time.sleep(0.002)   # speech starts...
        foreign.on = True
        mic._on_audio(loud(), S.BLOCK, None, None)                          # ...gap...
        foreign.on = False
        for _ in range(70):
            mic._on_audio(quiet(), S.BLOCK, None, None); time.sleep(0.002)
    threading.Thread(target=feed2, daemon=True).start()
    report("echo: a sentence cut by a deaf gap is thrown away whole", mic.listen(timeout_sec=1.2) is None)
    S.ECHO_TAIL_SEC = 1.0


# 5 ---------------------------------------------------------------------------------------------
def test_status_file() -> None:
    path = S.STATE_DIR / "status-test.json"
    st = service.Status(path)
    for s in ("asleep", "hearing", "thinking", "speaking", "asleep"):
        st.set_state(s)
    st.update(target="hermes:orchestrator", name="Jarvis", heard="what time is it", reply="Ten past nine.")
    back = service.read_status(path)
    report("status: round trip keeps every field", back["name"] == "Jarvis" and back["heard"] == "what time is it"
           and back["reply"] == "Ten past nine." and back["state"] == "asleep", str(back)[:200])
    report("status: history has the states in order",
           [s for _, s in back["history"]] == ["starting", "asleep", "hearing", "thinking", "speaking", "asleep"],
           str(back["history"]))
    report("status: no temp files left behind", not list(S.STATE_DIR.glob("status-test.json.*.tmp")))
    report("status: fresh while written", service.is_fresh(back))
    report("status: stale after 10 s of silence (listener dead)", not service.is_fresh(back, now=time.time() + 11))
    report("status: unreadable file -> empty, not a crash", service.read_status(S.STATE_DIR / "nope.json") == {})
    (S.STATE_DIR / "bad.json").write_text("{half")
    report("status: half-written file -> empty, not a crash", service.read_status(S.STATE_DIR / "bad.json") == {})
    for _ in range(service.HISTORY + 10):
        st.set_state("thinking"); st.set_state("asleep")
    report("status: history stays bounded", len(service.read_status(path)["history"]) == service.HISTORY)
    service.set_muted(True)
    report("status: mute flag on", service.is_muted())
    service.set_muted(False)
    report("status: mute flag off", not service.is_muted())
    service.send("hush"); service.send("new_thread"); service.send("rm -rf")
    report("status: dashboard orders delivered once, unknown ones dropped",
           service.take_orders() == ["hush", "new_thread"] and service.take_orders() == [])

    # the dashboard reads it and never opens the microphone
    import sounddevice
    opened = []
    real = sounddevice.InputStream
    sounddevice.InputStream = lambda *a, **k: opened.append(1) or real(*a, **k)   # type: ignore[assignment]
    S.LISTENER_STATUS.unlink(missing_ok=True)
    live = service.Status()
    live.update(target="hermes:orchestrator", name="Jarvis", heard="what time is it", reply="Ten past nine.")
    live.set_state("thinking")
    from dashboard import Dashboard
    from textual.widgets import Static

    async def run() -> tuple[str, str, str, str]:
        app = Dashboard()
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app.refresh_status()
            light = str(app.query_one("#light", Static).render())
            body = str(app.query_one("#body", Static).render())
            await pilot.press("m")
            await pilot.pause()
            muted = service.is_muted()
            live.set_state("asleep")
            app.refresh_status()
            light2 = str(app.query_one("#light", Static).render())
            await pilot.press("m")
            await pilot.press("h")
            await pilot.pause()
            live.data["updated"] = 0
            service.write_status(live.data)
            app.refresh_status()
            light3 = str(app.query_one("#light", Static).render())
            return light, body + f" muted={muted}", light2, light3

    light, body, light2, light3 = asyncio.run(run())
    sounddevice.InputStream = real   # type: ignore[assignment]
    report("dashboard: shows the state light", "thinking" in light, light)
    report("dashboard: shows who is on the line, last heard, last reply",
           "Jarvis" in body and "what time is it" in body and "Ten past nine." in body, body[:200])
    report("dashboard: M mutes (the listener reads the same flag)", "muted=True" in body and "muted" in light2, light2)
    report("dashboard: M again unmutes, H leaves a hush order", not service.is_muted() and service.take_orders() == ["hush"])
    report("dashboard: a dead listener shows as off", "off" in light3, light3)

    # -- voices: each profile speaks with its own Hermes voice (Peter 2026-10-21) --------------------
    import voices
    voices.set_per_profile(True)
    tbl = dict(voices.table())
    report("voices: Kylie speaks as Ava (assistant config.yaml), Mandarin as HsiaoChen",
           tbl["Kylie"].en == "en-US-AvaNeural" and tbl["Kylie"].zh == "zh-TW-HsiaoChenNeural" and tbl["Kylie"].source == "profile", str(tbl["Kylie"]))
    report("voices: Wanna Buffet speaks as Andrew (male), Mandarin as YunJhe (male)",
           tbl["Wanna Buffet"].en == "en-US-AndrewNeural" and tbl["Wanna Buffet"].zh == "zh-TW-YunJheNeural", str(tbl["Wanna Buffet"]))
    report("voices: Esther's Kokoro voice is not an Edge voice -> a chosen Edge fallback, not Jenny",
           tbl["Esther"].source == "fallback" and tbl["Esther"].en != S.TTS_VOICE_EN, str(tbl["Esther"]))
    report("voices: Claude (not a Hermes profile) gets the shared voice", voices.voice_for("claude").source == "shared")
    voices.set_per_profile(False)
    report("voices: V off -> everyone shares one voice", voices.voice_for("hermes:wanna-buffet") == voices.shared_voice())
    voices.set_per_profile(True)
    report("voices: V on again -> per profile", voices.voice_for("hermes:wanna-buffet").en == "en-US-AndrewNeural")
    spoken: list = []
    lst2 = make_listener(clock=FakeClock())
    _snd = Path(tempfile.mkdtemp(prefix="vd-voice-")) / "say.wav"   # a real file: speak() removes its folder after playing
    lst2.synth = lambda text, voice=None: (spoken.append(voice), _snd.write_bytes(b""), _snd)[2]
    lst2.brain.set_target("hermes:wanna-buffet")
    lst2.speak("hello")
    report("listener: speaks with the voice of whoever is on the line", bool(spoken) and spoken[0].en == "en-US-AndrewNeural", str(spoken))
    report("listener: the status file says which voice spoke (dashboard shows it)",
           lst2.status.data.get("voice") == "Andrew · YunJhe", str(lst2.status.data.get("voice")))

    # -- speed: voice turns ask Hermes for low reasoning; Edge is retried before any fallback voice ----
    import brain as B
    import tts as T
    seen: list = []

    class _P:
        def __init__(self, cmd, **kw):
            seen.append(cmd)
            self.returncode = 0
        def communicate(self, timeout=None):
            return "ok", ""
    real_popen = B.subprocess.Popen
    B.subprocess.Popen = _P                     # type: ignore[assignment]
    try:
        hb = B.HermesBrain("wanna-buffet")
        hb.session_id = None
        hb._ask("how did yesterday go", lambda s: None, retry=False)
    finally:
        B.subprocess.Popen = real_popen         # type: ignore[assignment]
    report("speed: a Hermes voice turn passes --reasoning low", bool(seen) and seen[0][-2:] == ["--reasoning", "low"]
           or (bool(seen) and "--reasoning" in seen[0] and seen[0][seen[0].index("--reasoning") + 1] == "low"), str(seen[:1])[:200])
    calls: list = []

    class _R:
        def __init__(self, rc):
            self.returncode = rc
    def _run(cmd, **kw):
        calls.append(cmd[-1])
        if cmd[-1] == "edge" and len(calls) == 1:
            return _R(1)                        # Edge hiccups once ...
        Path(cmd[2]).write_bytes(b"RIFF")       # ... and works on the retry
        return _R(0)
    real_run = T.subprocess.run
    T.subprocess.run = _run                     # type: ignore[assignment]
    try:
        d = Path(tempfile.mkdtemp(prefix="vd-chain-"))
        (d / "t.txt").write_text("hi")
        got = T._chain(d / "t.txt", d / "o.wav", "en-US-AndrewNeural")
    finally:
        T.subprocess.run = real_run             # type: ignore[assignment]
    report("voices: one Edge hiccup is retried with Edge, never handed to the fallback voice",
           got is not None and calls == ["edge", "edge"], str(calls))
    report("dashboard: never opened the microphone", not opened)


if __name__ == "__main__":
    test_routing()
    test_stripping()
    test_listener_decisions()
    test_mute_during_speech()
    test_status_file()
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
