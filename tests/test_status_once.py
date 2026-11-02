"""Offline: while an agent works, Voice Desk says ONE "still working" line per question, then
stays quiet until the answer (Peter 2026-11-02: "Why couldn't you just say it once and then just
let it finish?"). settings.STATUS_REPEAT = True brings the old repeating lines back.

Covers every door that speaks a status line: the background brains' ticker (Claude, Gemini,
Hermes), Codex, the live Claude/Gemini/Codex window follower, the live Hermes window follower
(all feed the Mac listener's _status_line) and the phone receiver's check-backs.
Fakes only: no herdr window, no model, no microphone, no network."""
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401  (the fake no-window herdr, also when this file is run directly)

import settings as S  # noqa: E402
import brain as B  # noqa: E402
import live_session  # noqa: E402
import receiver  # noqa: E402
from codex_brain import CodexBrain  # noqa: E402

FAST = 0.05          # seconds between status opportunities in these tests


class TickerTest(unittest.TestCase):
    """The background brains (Claude, Gemini, Hermes) share one ticker."""

    def run_ticker(self, seconds: float, until: int = 0) -> list[str]:
        """Run the ticker for `seconds`, or until it has said `until` lines (max 5 s)."""
        said: list[str] = []
        stop = threading.Event()
        t = threading.Thread(target=B._status_ticker, args=(stop, said.append, lambda: "Bash"), daemon=True)
        t.start()
        deadline = time.monotonic() + (5 if until else seconds)
        while time.monotonic() < deadline and not (until and len(said) >= until):
            time.sleep(0.01)
        stop.set()
        t.join(timeout=1)
        return said

    def test_one_line_per_turn(self):
        with patch.multiple(S, STATUS_EVERY_SEC=FAST, STATUS_REPEAT=False):
            said = self.run_ticker(FAST * 10)
        self.assertEqual(said, [S.STATUS_ONCE_LINE])

    def test_quick_answer_says_nothing(self):
        with patch.multiple(S, STATUS_EVERY_SEC=1, STATUS_REPEAT=False):
            self.assertEqual(self.run_ticker(0.1), [])

    def test_repeat_switch_brings_back_the_old_lines(self):
        with patch.multiple(S, STATUS_EVERY_SEC=FAST, STATUS_REPEAT=True):
            said = self.run_ticker(0, until=3)
        self.assertGreaterEqual(len(said), 3)
        self.assertEqual(said[0], "still running a command")


class CodexStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vd-status-codex-"))
        fake = self.tmp / "codex"
        fake.write_text(f"""#!{sys.executable}
import json, sys, time
sys.stdin.read()
print(json.dumps({{"type": "thread.started", "thread_id": "0199a213-81c0-7800-8aa1-bbab2a035a99"}}), flush=True)
time.sleep(0.6)
print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "Done."}}}}))
print(json.dumps({{"type": "turn.completed", "usage": {{}}}}))
""")
        fake.chmod(0o700)
        self.settings = patch.multiple(S, CODEX_BIN=str(fake), CODEX_CWD=self.tmp,
                                       CODEX_SESSION_FILE=self.tmp / "codex_session",
                                       TRANSCRIPT_LOG=self.tmp / "transcript.log",
                                       CODEX_MODEL="", CODEX_EFFORT="", STATUS_EVERY_SEC=FAST)
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_one_line_per_turn(self):
        said: list[str] = []
        with patch.object(S, "STATUS_REPEAT", False):
            self.assertEqual(CodexBrain().ask("question", on_status=said.append), "Done.")
        self.assertEqual(said, [S.STATUS_ONCE_LINE])

    def test_repeat_switch(self):
        said: list[str] = []
        with patch.object(S, "STATUS_REPEAT", True):
            CodexBrain().ask("question", on_status=said.append)
        self.assertGreater(len(said), 2)


class LiveWindowStatusTest(unittest.TestCase):
    """A live window that keeps working past the turn limit: one status line, then the
    'answer will be on the screen' fallback (which must stay)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vd-status-live-"))
        self.settings = patch.multiple(S, STATUS_EVERY_SEC=FAST, CLAUDE_TURN_TIMEOUT_SEC=0.6)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.poll = patch.object(live_session, "POLL_SEC", 0.01)
        self.poll.start()
        self.addCleanup(self.poll.stop)

    def follow_claude(self) -> tuple[str, list[str]]:
        message = live_session.PREFIX + "is it done yet"
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"content": message}}) + "\n"
                              + json.dumps({"type": "assistant", "message": {"content": [
                                  {"type": "tool_use", "name": "Bash"}]}}) + "\n")
        brain = live_session.LiveBrain("claude")
        brain.agent = live_session.Agent("claude", "Claude", "claude", lambda sid: transcript,
                                         live_session._claude_events, False)
        said: list[str] = []
        reply = brain._follow("wZ:p1", "sid", 0, message, said.append, lambda s: None)
        return reply, said

    def follow_hermes(self) -> tuple[str, list[str]]:
        message = live_session.PREFIX + "is it done yet"
        db = self.tmp / "state.db"
        con = sqlite3.connect(db)
        con.execute("create table messages (id integer primary key autoincrement, session_id text, role text, "
                    "content text, tool_calls text, tool_name text, finish_reason text)")
        con.execute("insert into messages (session_id, role, content) values ('s1', 'user', ?)", (message,))
        con.commit()
        con.close()
        brain = live_session.HermesLiveBrain("builder", "Builder")
        brain.db = db
        said: list[str] = []
        reply = brain._follow("wH:p1", 0, message, said.append, lambda s: None)
        return reply, said

    def test_claude_gemini_codex_window_one_line(self):
        with patch.object(S, "STATUS_REPEAT", False):
            reply, said = self.follow_claude()
        self.assertEqual(said, [S.STATUS_ONCE_LINE])
        self.assertIn("the answer will be on the screen", reply)

    def test_claude_gemini_codex_window_repeat_switch(self):
        with patch.object(S, "STATUS_REPEAT", True):
            _, said = self.follow_claude()
        self.assertGreater(len(said), 2)
        self.assertEqual(said[0], "still running a command")

    def test_hermes_window_one_line(self):
        with patch.object(S, "STATUS_REPEAT", False):
            reply, said = self.follow_hermes()
        self.assertEqual(said, [S.STATUS_ONCE_LINE])
        self.assertIn("the answer will be on the screen", reply)

    def test_hermes_window_repeat_switch(self):
        with patch.object(S, "STATUS_REPEAT", True):
            _, said = self.follow_hermes()
        self.assertGreater(len(said), 2)


class FakeMic:
    def drain(self) -> None:
        pass


class FakePlayer:
    def __init__(self) -> None:
        self.played: list[str] = []

    def play(self, path) -> None:
        self.played.append(Path(path).read_text())

    def wait(self) -> None:
        pass

    def stop(self) -> None:
        pass


class MacListenerTest(unittest.TestCase):
    """The whole Mac turn: the real listener, the real brain router and the real ClaudeBrain,
    with a fake `claude` program that takes 0.8 s. What the speaker says is recorded, not played."""

    def setUp(self):
        import listener as L
        import tts
        self.L = L
        self.tmp = Path(tempfile.mkdtemp(prefix="vd-status-mac-"))
        fake = self.tmp / "claude"
        fake.write_text(f"""#!{sys.executable}
import json, time
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Bash"}}]}}}}), flush=True)
time.sleep(0.8)
print(json.dumps({{"type": "result", "result": "The build passed."}}), flush=True)
""")
        fake.chmod(0o700)
        for p in (patch.multiple(S, CLAUDE_BIN=str(fake), STATUS_EVERY_SEC=FAST, ECHO_TAIL_SEC=0.0,
                                 SESSION_FILE=self.tmp / "session_id", TRANSCRIPT_LOG=self.tmp / "transcript.log",
                                 BRAIN_FILE=self.tmp / "brain",
                                 HERDR_BIN=str(tests._herdr)),    # no open windows: the background brain answers
                  patch.object(tts, "tick", lambda: None)):       # no turn-taking beep on the real speaker
            p.start()
            self.addCleanup(p.stop)

    def run_turn(self) -> list[str]:
        player = FakePlayer()

        def synth(text, voice=None):                 # the listener deletes each clip's folder after playing it
            p = Path(tempfile.mkdtemp(prefix="say-", dir=self.tmp)) / "say.txt"
            p.write_text(text)
            return p

        lst = self.L.Listener(mic=FakeMic(), whisper=object(), brain=B.Brain(), player=player,
                              synth=synth, foreign=type("F", (), {"playing": lambda s: False})(),
                              chime=lambda name: None)
        lst.turn("did the build pass", "claude", last=True)
        return player.played

    def test_one_status_line_then_the_answer(self):
        with patch.object(S, "STATUS_REPEAT", False):
            played = self.run_turn()
        self.assertEqual(played, [S.STATUS_ONCE_LINE + ".", "The build passed."])

    def test_repeat_switch(self):
        with patch.object(S, "STATUS_REPEAT", True):
            played = self.run_turn()
        self.assertGreater(played.count("still running a command."), 1)
        self.assertEqual(played[-1], "The build passed.")


class SlowBrain:
    """Stands in for receiver.brain: answers after `delay` seconds."""

    def __init__(self, delay: float):
        self.delay = delay

    def resolve(self, to):
        return to

    def target(self):
        return "claude"

    def last_tool_for(self, t=None):
        return "Bash"

    def ask(self, text, to=None, **kw):
        time.sleep(self.delay)
        return f"answer to {text}"


class PhoneStatusTest(unittest.TestCase):
    """The Siri Shortcut keeps checking back (that keeps Siri's request alive); only the first
    status check-back says a line, later ones answer '.', then the answer comes exactly once.
    The job is held pending by the test itself, so the count of check-backs is exact."""

    def setUp(self):
        receiver._jobs.clear()
        receiver._client_target.clear()
        self.addCleanup(receiver._jobs.clear)
        self.brain = patch.object(receiver, "brain", SlowBrain(0))
        self.brain.start()
        self.addCleanup(self.brain.stop)

    def check_backs(self, waiting: int = 12) -> list[str]:
        job = {"id": "j1", "text": "slow question", "to": "claude", "target": "claude", "client_ip": "iphone",
               "reply": "", "done": False, "consumed": False, "started": time.monotonic(), "polls": 0}
        receiver._jobs[job["id"]] = job
        replies = [receiver.take_reply(0, job_id="j1")[0] for _ in range(waiting)]   # 0 s stands in for 8 s
        with receiver._cv:
            job["reply"], job["done"] = "answer to slow question", True
        replies += [receiver.take_reply(0, job_id="j1")[0] for _ in range(3)]       # the answer, then nothing
        return replies

    def test_one_line_per_question_then_quiet_then_answer_once(self):
        with patch.object(S, "STATUS_REPEAT", False):
            replies = self.check_backs()
        spoken = [r for r in replies if r != receiver.SILENT]
        self.assertEqual(spoken, [S.STATUS_ONCE_LINE + ".", "answer to slow question"], replies)
        # the first status comes on the same check-back as before (every STATUS_EVERY_POLLS-th one)
        self.assertEqual(replies.index(S.STATUS_ONCE_LINE + "."), receiver.STATUS_EVERY_POLLS - 1)
        self.assertEqual(replies[12], "answer to slow question")

    def test_each_question_gets_its_own_line(self):
        with patch.object(S, "STATUS_REPEAT", False):
            first = self.check_backs()
            receiver._jobs.clear()
            second = self.check_backs()
        self.assertEqual(first.count(S.STATUS_ONCE_LINE + "."), 1)
        self.assertEqual(second.count(S.STATUS_ONCE_LINE + "."), 1)

    def test_repeat_switch_brings_back_the_old_lines(self):
        with patch.object(S, "STATUS_REPEAT", True):
            replies = self.check_backs()
        statuses = [r for r in replies if r not in (receiver.SILENT, "answer to slow question")]
        self.assertEqual(statuses, ["still running a command."] * (12 // receiver.STATUS_EVERY_POLLS), replies)
        self.assertEqual(replies.count("answer to slow question"), 1)


class PhoneEndToEndTest(unittest.TestCase):
    """Same through a real job thread (start_job + a slow stand-in brain): one line, one answer."""

    def setUp(self):
        receiver._jobs.clear()
        receiver._client_target.clear()
        self.addCleanup(receiver._jobs.clear)
        self.brain = patch.object(receiver, "brain", SlowBrain(1.0))
        self.brain.start()
        self.addCleanup(self.brain.stop)

    def test_one_line_then_the_answer(self):
        with patch.object(S, "STATUS_REPEAT", False):
            job = receiver.start_job("slow question", "claude", client_ip="iphone")
            replies: list[str] = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                text, is_answer = receiver.take_reply(0.02, job_id=job["id"])
                replies.append(text)
                if is_answer:
                    break
        spoken = [r for r in replies if r != receiver.SILENT]
        self.assertEqual(spoken, [S.STATUS_ONCE_LINE + ".", "answer to slow question"], replies)


if __name__ == "__main__":
    unittest.main()
