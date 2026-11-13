"""Headless self-test: proves every stage without a microphone or a screen.

  .venv/bin/python tests/selftest.py            # all stages
  .venv/bin/python tests/selftest.py --no-brain # skip the Claude turn (no tokens spent)

Stages: 1 silence detector on synthetic audio, 2 whisper-server transcription of a spoken
clip, 3 English + Mandarin voices, 4 two Claude turns on one thread (memory check),
5 the phone receiver's check-back polling with a stand-in brain, 6 the window mounts
headlessly with the loop stubbed out, 7 the brain router ("talk to Esther", one thread
per Hermes profile) without spending a token, 8 (--hermes) two live Hermes turns,
9 (--gemini) two live Gemini turns through Google's Antigravity tool.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VOICE_DESK_STATE", tempfile.mkdtemp(prefix="cv-state-"))
# Never Peter's real windows when started as a script (tests/__init__.py is skipped then): its fake
# herdr reports no open windows, so the router and the live turns use the background threads.
import tests  # noqa: E402,F401

import settings as S  # noqa: E402
import stt  # noqa: E402
import tts  # noqa: E402
from audio import Microphone, write_wav  # noqa: E402

ok = True


def report(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok &= passed
    print(f"[{'PASS' if passed else 'FAIL'}] {name} {detail}")


# 1 ---------------------------------------------------------------------------------------------
def stage_vad() -> None:
    mic = Microphone()
    rng = np.random.default_rng(0)
    quiet = lambda n: (rng.normal(0, 60, n * S.BLOCK)).astype(np.int16)        # noise floor ~60
    loud = lambda n: (rng.normal(0, 2500, n * S.BLOCK)).astype(np.int16)       # speech ~2500
    stream = np.concatenate([quiet(40), loud(60), quiet(70)])                  # 1.2 s quiet, 1.8 s talk, 2.1 s quiet
    blocks = [stream[i:i + S.BLOCK] for i in range(0, len(stream), S.BLOCK)]

    def feed() -> None:                       # arrive like a live mic (listen() drains on entry)
        time.sleep(0.2)
        for b in blocks:
            mic._on_audio(b.reshape(-1, 1), len(b), None, None)
            time.sleep(0.002)

    import threading
    threading.Thread(target=feed, daemon=True).start()
    t0 = time.monotonic()
    audio = mic.listen(timeout_sec=5)
    dur = audio.size / S.SAMPLE_RATE if audio is not None else 0
    report("silence detector", audio is not None and 2.2 <= dur <= 4.2,
           f"captured {dur:.1f}s (talk 1.8s + pre-roll + 1.5s pause) in {time.monotonic()-t0:.1f}s")
    # nothing said -> None after timeout
    mic2 = Microphone()
    for _ in range(40):
        mic2._on_audio(quiet(1).reshape(-1, 1), S.BLOCK, None, None)
    report("silence -> no utterance", mic2.listen(timeout_sec=0.5) is None)


# 2 ---------------------------------------------------------------------------------------------
def stage_stt(w: stt.Whisper) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="cv-stt-"))
    phrase = "What is the weather like in Taipei today, and should I bring an umbrella?"
    subprocess.run(["/usr/bin/say", "-v", "Samantha", "-o", str(tmp / "a.aiff"), phrase], check=True)
    subprocess.run([S.FFMPEG, "-loglevel", "error", "-y", "-i", str(tmp / "a.aiff"), "-ar", "16000", "-ac", "1",
                    str(tmp / "a.wav")], check=True)
    t0 = time.monotonic()
    text = w.transcribe(tmp / "a.wav")
    report("whisper transcription", "umbrella" in text.lower() and "taipei" in text.lower(),
           f"{time.monotonic()-t0:.1f}s -> {text!r}")
    report("hallucination filter", stt.clean("[BLANK_AUDIO]") == "" and stt.clean("Thank you.") == "")
    report("command normalize", stt.normalize("Go to sleep.") == "go to sleep")


# 3 ---------------------------------------------------------------------------------------------
def stage_tts() -> None:
    # Peter 2026-11-11: no shared voice and no Mac voice: an agent's line is made with that agent's own voice
    # (Kylie: Edge Ava / HsiaoChen here), and no voice at all means silence.
    import voices
    kylie = voices.voice_for("hermes:assistant")
    t0 = time.monotonic()
    p = tts.synthesize("Your daily trading report is working again.", kylie)
    report(f"english voice ({kylie.en})", p is not None and p.stat().st_size > 1000, f"{time.monotonic()-t0:.1f}s {p}")
    t0 = time.monotonic()
    p = tts.synthesize("早安，今天的報告已經送出了。", kylie)
    report(f"mandarin voice ({kylie.zh})", p is not None and p.stat().st_size > 1000, f"{time.monotonic()-t0:.1f}s")
    report("no voice = silent (nothing made, no stand-in voice)", tts.synthesize("hello", voices.SILENT) is None)
    report("speakable strips markdown",
           tts.speakable("**Done.** See `foo.py` and https://x.y/z\n```\ncode\n```") ==
           "Done. See foo.py and a link the code is in the window.")


# 4 ---------------------------------------------------------------------------------------------
def stage_brain() -> None:
    from brain import Brain
    b = Brain()
    b.new_thread()
    statuses: list[str] = []
    t0 = time.monotonic()
    r1 = b.ask("Remember the code word is pineapple. Reply with just: noted.", on_status=statuses.append)
    t1 = time.monotonic() - t0
    r2 = b.ask("What was the code word? One word.")
    report("claude turn 1", bool(r1) and not r1.startswith("I did not get an answer"), f"{t1:.1f}s -> {r1!r}")
    report("claude thread remembers", "pineapple" in r2.lower(), f"-> {r2!r}")
    report("session id saved", S.SESSION_FILE.exists() and len(S.SESSION_FILE.read_text()) > 10)


# 5 ---------------------------------------------------------------------------------------------
def stage_receiver() -> None:
    """The phone door, with a stand-in brain: a slow answer must come back through the
    check-back polls exactly once, and idle polls must answer at once."""
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import receiver
    receiver.ASK_WAIT_SEC = receiver.POLL_WAIT_SEC = 1.5
    receiver.log = lambda m: None
    receiver.brain.ask = lambda text, **kw: (time.sleep(3), f"**Answer** to {text}")[1]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), receiver.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(method: str, path: str, body: dict | None = None) -> tuple[str, float]:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     headers={"X-Token": receiver.TOKEN, "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body else None)
        t0 = time.monotonic()
        return json.loads(urllib.request.urlopen(req, timeout=20).read())["reply"], time.monotonic() - t0

    first, _ = call("POST", "/ask", {"text": "slow question"})
    polls = [call("GET", "/reply") for _ in range(4)]
    replies = [r for r, _ in polls]
    report("receiver: slow answer arrives via check-back, once",
           first == "." and replies.count("Answer to slow question") == 1, str(replies))
    after = polls[replies.index("Answer to slow question") + 1:] if "Answer to slow question" in replies else []
    report("receiver: idle check-backs answer at once", bool(after) and all(t < 0.5 for _, t in after))
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/reply"))
        report("receiver: rejects a missing token", False)
    except urllib.error.HTTPError as exc:
        report("receiver: rejects a missing token", exc.code == 403)
    # the phone can pin a request to one thread; an unknown name is answered, not queued
    seen = {}
    receiver.brain.ask = lambda text, to=None, **kw: (seen.setdefault("to", to), f"from {to}")[1]
    pinned, _ = call("POST", "/ask", {"text": "q", "to": "claude"})
    report("receiver: 'to' pins the thread", seen.get("to") == "claude" and pinned == "from claude", str((seen, pinned)))
    bogus, _ = call("POST", "/ask", {"text": "q", "to": "nobody-here"})
    report("receiver: unknown 'to' is answered at once", bogus.startswith("I do not know anyone"), bogus)
    srv.shutdown()


# 7 ---------------------------------------------------------------------------------------------
def stage_router() -> None:
    """The front door picks the thread by spoken name; Hermes profiles are discovered from disk."""
    import brain as B
    profiles = B.hermes_profiles()
    b = B.Brain()
    report("router: Claude is always a target", b.targets()[0] == "claude" and b.target() == "claude")
    report("router: Hermes profiles found", bool(profiles), str(profiles))
    if profiles:
        p = profiles[0]
        word = [w for w in p.replace("-", "_").split("_") if w][-1]
        report("router: any word of the profile name resolves", b.resolve(word) == f"hermes:{p}", f"{word!r} -> {b.resolve(word)}")
        said = b.command(f"talk to {word}")
        report("router: 'talk to X' switches the sticky thread", b.target() == f"hermes:{p}" and said.startswith("Okay"), str(said))
        report("router: Hermes backend runs in the profile's home", b.backend().home == S.HERMES_PROFILES_DIR / p)
    report("router: 'switch to Opus' is left to the model switch", b.command("switch to Opus") is None)
    report("router: a sentence is not a switch", b.command("talk to me about the budget") is None)
    report("router: 'talk to Claude' goes back", b.command("talk to Claude") == "Okay, back to Claude." and b.target() == "claude")
    report("router: who am I talking to", b.command("who am I talking to") == "You are talking to Claude.")


# 8 ---------------------------------------------------------------------------------------------
def stage_hermes() -> None:
    """Two live turns with the first Hermes profile: the second must remember the first."""
    import brain as B
    profiles = B.hermes_profiles()
    if not profiles:
        report("hermes: no profile to test", False)
        return
    b = B.Brain()
    to = f"hermes:{profiles[0]}"
    t0 = time.monotonic()
    first = b.ask("Remember that the code word is lantern. Reply in one short sentence.", to=to)
    sid = b.backend(to).session_id
    report("hermes: first turn answered + session id found", bool(first) and bool(sid), f"{time.monotonic()-t0:.0f}s {sid} {first[:60]!r}")
    second = b.ask("What is the code word? Answer in three words or fewer.", to=to)
    report("hermes: second turn remembers the first", "lantern" in second.lower(), second[:80])


# 9 ---------------------------------------------------------------------------------------------
def stage_gemini() -> None:
    """Two live Gemini turns: the second must remember the first, and the conversation id is kept."""
    import brain as B
    b = B.Brain()
    if "gemini" not in b.targets():
        report("gemini: agy not installed", False)
        return
    t0 = time.monotonic()
    first = b.ask("Remember that the code word is harbour. Reply in one short sentence.", to="gemini")
    sid = b.gemini.session_id
    report("gemini: first turn answered + conversation id kept", bool(first) and bool(sid), f"{time.monotonic()-t0:.0f}s {sid} {first[:60]!r}")
    second = b.ask("What is the code word? Three words or fewer.", to="gemini")
    report("gemini: second turn remembers the first", "harbour" in second.lower() or "harbor" in second.lower(), second[:80])


# 6 ---------------------------------------------------------------------------------------------
def stage_window() -> None:
    import engine
    engine.Engine.start = lambda self: None          # no mic, no whisper in the test
    from app import VoiceDesk

    async def run() -> tuple[str, bool]:
        app = VoiceDesk()
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app._apply("state", "listening")
            app._apply("you", "hello there")
            app._apply("claude", "Hi Peter.")
            await pilot.pause()
            from textual.widgets import Static
            light = str(app.query_one("#light", Static).render())
            await pilot.press("m")
            await pilot.pause()
            mode = str(app.query_one("#mode", Static).render())
            return light, "tap" in mode

    light, tapped = asyncio.run(run())
    report("window mounts + light", "listening" in light, light)
    report("M switches mode", tapped)


def stage_speechq() -> None:
    """The speech line with the REAL /usr/bin/afplay (a silent clip at volume 0), throwaway state:
    the stand-in plays, exits 0 like afplay, and leaves no ticket behind."""
    import wave
    state = Path(tempfile.mkdtemp(prefix="vd-self-sq-"))
    clip = state / "silent.wav"
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\0\0" * 11025)
    env = dict(os.environ, VOICE_DESK_STATE=str(state), HERMES_HOME="/x/profiles/assistant",
               VOICE_DESK_SPEECHQ_NAMES_ONLY_CACHED="1")
    env.pop("VOICE_DESK_REAL_AFPLAY", None)
    t0 = time.time()
    r = subprocess.run([sys.executable, str(ROOT / "speechq.py"), "afplay", "-v", "0", str(clip)],
                       env=env, capture_output=True, text=True, timeout=30)
    took = time.time() - t0
    left = list((state / "speech" / "queue").glob("*.json"))
    report("speech line plays through the real afplay", r.returncode == 0 and 0.4 < took < 10 and not left,
           f"rc={r.returncode} {took:.1f}s tickets={len(left)} {r.stderr[-200:]}")


if __name__ == "__main__":
    stage_vad()
    w = stt.Whisper()
    w.start()
    try:
        stage_stt(w)
    finally:
        w.stop()
    stage_tts()
    if "--no-brain" not in sys.argv:
        stage_brain()
    stage_receiver()
    stage_window()
    stage_router()
    stage_speechq()
    if "--hermes" in sys.argv:
        stage_hermes()
    if "--gemini" in sys.argv:
        stage_gemini()
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
