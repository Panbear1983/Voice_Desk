"""Offline: "talk to Jarvis" said into a phone Shortcut puts Jarvis on the line, the way the Mac window does
(Peter 2026-11-03: the phone sent "Talk to Assistant" into the Kylie Shortcut and it went to Kylie as a
question). Same matcher (brain.addressed + the talk-to command), same names (agents.toml).

The receiver's own HTTP handler on 127.0.0.1 and the real Brain router; only Brain.ask is replaced, so
no model is called, no window is typed into, and the real receiver token is never read."""
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401  (the fake no-window herdr and throw-away state folder)

import brain as B  # noqa: E402
import settings as S  # noqa: E402
import receiver  # noqa: E402


class RecordingBrain(B.Brain):
    """The real router (resolve, names, target); ask() records who would have been asked."""

    def __init__(self, delay: float = 0.0):
        super().__init__()
        self.asked: list[tuple[str, str]] = []
        self.delay = delay

    def ask(self, text, on_status=None, on_text=None, to=None):
        handled = self.command(text, to=to)                # the real Brain.ask answers these itself
        if handled:
            return handled
        target = self.resolve(to) if to else self.target()
        self.asked.append((target, text))
        time.sleep(self.delay)
        return f"{self.name(target)} answers {text}"


class PhoneSwitchTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="vd-switch-", dir=tests._tmp))
        profiles = tmp / "profiles"
        for p in ("assistant", "builder", "orchestrator", "tax_angel_esther", "wanna-buffet"):
            (profiles / p).mkdir(parents=True)
            (profiles / p / "config.yaml").write_text("model: x\n")
        agy = tmp / "agy"
        agy.write_text("#!/bin/sh\n")
        for p in (patch.object(S, "HERMES_PROFILES_DIR", profiles), patch.object(S, "GEMINI_BIN", str(agy)),
                  patch.object(S, "CODEX_BIN", str(tmp / "no-codex")),
                  patch.object(S, "BRAIN_FILE", tmp / "brain"),             # the Mac's sticky target: Claude
                  patch.object(S, "HERMES_SESSION_DIR", tmp / "hermes_sessions"),
                  patch.object(receiver, "TOKEN", "test-token"), patch.object(receiver, "log", lambda m: None)):
            p.start()
            self.addCleanup(p.stop)
        self.brain = RecordingBrain()
        p = patch.object(receiver, "brain", self.brain)
        p.start()
        self.addCleanup(p.stop)
        for d in (receiver._jobs, receiver._client_target, receiver._client_switch):
            d.clear()
            self.addCleanup(d.clear)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), receiver.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    # what the Shortcut sends: POST /ask {text, to}, then GET /reply?to=<its pinned agent>
    def post(self, text: str, to: str = "hermes:assistant") -> dict:
        req = Request(self.base + "/ask", data=json.dumps({"text": text, "to": to}).encode(),
                      headers={"X-Token": "test-token", "Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def get(self, to: str = "hermes:assistant") -> dict:
        req = Request(f"{self.base}/reply?to={to}", headers={"X-Token": "test-token"})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    # -- the bug ----------------------------------------------------------------------------
    def test_talk_to_assistant_from_the_kylie_shortcut_is_not_a_question(self):
        out = self.post("Talk to Assistant")                     # the 2026-11-03 17:51 request, word for word
        self.assertEqual(out["reply"], "Okay, you are talking to Kylie now.")
        self.assertIs(out["pending"], False)
        self.assertEqual(self.brain.asked, [])                   # nobody was asked "Talk to Assistant"

    def test_talk_to_jarvis_then_the_next_question_goes_to_jarvis(self):
        out = self.post("Talk to Jarvis.")
        self.assertEqual(out["reply"], "Okay, you are talking to Jarvis now.")
        self.assertEqual(self.brain.asked, [])
        out = self.post("What time is it?")                      # still sent by the Kylie Shortcut
        self.assertEqual(self.brain.asked, [("hermes:orchestrator", "What time is it?")])
        self.assertEqual(out["reply"], "Jarvis answers What time is it?")
        self.assertNotIn("end", out)

    def test_every_switch_form_the_mac_window_takes(self):
        for said, target, reply in (
                ("switch to Esther", "hermes:tax_angel_esther", "Okay, you are talking to Esther now."),
                ("Okay, talk to Wanna Buffet please", "hermes:wanna-buffet", "Okay, you are talking to Wanna Buffet now."),
                ("let me talk to Claude please", "claude", "Okay, you are talking to Claude now."),
                ("Hello Gemini.", "gemini", "Gemini here."),
                ("Hello, Jarvis!", "hermes:orchestrator", "Jarvis here."),
                ("Jarvis.", "hermes:orchestrator", "Jarvis here."),            # a bare name: the Mac's "Jarvis here."
                ("Javis", "hermes:orchestrator", "Jarvis here."),              # how whisper and dictation spell it
                ("talk to builder", "hermes:builder", "Okay, you are talking to Builder now.")):
            receiver._client_switch.clear()
            out = self.post(said)
            self.assertEqual(out["reply"], reply, said)
            self.assertEqual(receiver._client_switch["127.0.0.1"]["target"], target, said)
        self.assertEqual(self.brain.asked, [])

    def test_name_with_a_question_switches_and_asks_and_says_who_answers(self):
        out = self.post("Jarvis, what time is it?")
        self.assertEqual(self.brain.asked, [("hermes:orchestrator", "what time is it?")])
        self.assertEqual(out["reply"], "Jarvis here. Jarvis answers what time is it?")
        self.post("and tomorrow?")
        self.assertEqual(self.brain.asked[-1], ("hermes:orchestrator", "and tomorrow?"))

    def test_a_name_then_talk_to_another_switches_the_phone_not_the_mac(self):
        """Review round 1: "Kylie, talk to Jarvis" reached Brain.command, which moved the Mac's sticky
        agent and told the phone Jarvis was listening while its next question still went to Kylie."""
        for said, target, who in (("Kylie, talk to Jarvis", "hermes:orchestrator", "Jarvis"),
                                  ("Hello Kylie, talk to Jarvis.", "hermes:orchestrator", "Jarvis"),
                                  ("Jarvis, switch to Esther", "hermes:tax_angel_esther", "Esther")):
            receiver._client_switch.clear()
            self.brain.asked.clear()
            out = self.post(said)                                 # from the Kylie Shortcut
            self.assertEqual(out["reply"], f"Okay, you are talking to {who} now.", said)
            self.assertEqual(self.brain.asked, [], said)          # nobody was asked the switch as a question
            self.post("what time is it")
            self.assertEqual(self.brain.asked, [(target, "what time is it")], said)
            self.assertFalse(S.BRAIN_FILE.exists(), said)        # the Mac window's agent did not move
            self.assertEqual(self.brain.target(), "claude", said)

    def test_a_name_then_talk_to_someone_unknown_stays_put(self):
        out = self.post("Jarvis, talk to Bob")
        self.assertTrue(out["reply"].startswith("I do not know anyone called bob."), out["reply"])
        self.assertEqual((self.brain.asked, receiver._client_switch), ([], {}))
        self.assertFalse(S.BRAIN_FILE.exists())

    def test_own_name_is_just_the_question(self):
        out = self.post("Kylie, remind me at five")
        self.assertEqual(self.brain.asked, [("hermes:assistant", "remind me at five")])
        self.assertEqual(out["reply"], "Kylie answers remind me at five")     # no "Kylie here." in front
        self.assertEqual(self.post("Talk to Kylie")["reply"], "Okay, you are talking to Kylie now.")
        self.assertEqual(receiver._client_switch, {})

    def test_switch_back_to_the_shortcuts_own_agent(self):
        self.post("talk to Jarvis")
        self.assertEqual(self.post("talk to Kylie")["reply"], "Okay, back to Kylie.")
        self.post("hello again")
        self.assertEqual(self.brain.asked, [("hermes:assistant", "hello again")])

    # -- check-backs follow the switch --------------------------------------------------------
    def test_slow_answer_from_the_new_agent_comes_back_on_the_check_back(self):
        self.brain.delay = 0.3
        self.post("talk to Jarvis")
        with patch.object(receiver, "ASK_WAIT_SEC", 0.05):
            out = self.post("Tell me a story")
        self.assertTrue(out["pending"])
        self.assertEqual(self.get(), {"reply": "Jarvis answers Tell me a story"})   # the Shortcut asks ?to=assistant

    # -- what it must not do ------------------------------------------------------------------
    def test_ordinary_questions_are_not_switches(self):
        for said in ("I told Jarvis yesterday", "Tell Kylie I said hi", "talk to me about the budget",
                     "switch to Opus", "What can Esther do?"):
            out = self.post(said)
            self.assertEqual(out["reply"], f"Kylie answers {said}", said)
        self.assertEqual(receiver._client_switch, {})

    def test_hey_is_still_not_a_greeting(self):
        """Peter 2026-11-02: only "hello" greets. "Hey Jarvis" stays an ordinary question for the Shortcut's agent."""
        out = self.post("Hey Jarvis")
        self.assertEqual(self.brain.asked, [("hermes:assistant", "Hey Jarvis")])
        self.assertEqual(out["reply"], "Kylie answers Hey Jarvis")
        self.assertEqual(receiver._client_switch, {})

    def test_who_am_i_talking_to_names_the_new_agent(self):
        self.post("talk to Jarvis")
        self.assertEqual(self.post("Who am I talking to?")["reply"], "You are talking to Jarvis.")

    def test_unknown_name_says_so_and_stays_put(self):
        out = self.post("talk to Bob")
        self.assertTrue(out["reply"].startswith("I do not know anyone called bob. You can talk to "), out["reply"])
        self.assertIn("Jarvis", out["reply"])
        self.assertEqual((self.brain.asked, receiver._client_switch), ([], {}))

    def test_the_mac_windows_sticky_target_is_not_touched(self):
        self.post("talk to Jarvis")
        self.assertFalse(S.BRAIN_FILE.exists())                 # the Mac window still talks to whoever it had
        self.assertEqual(self.brain.target(), "claude")

    def test_another_shortcut_or_device_is_not_switched(self):
        self.post("talk to Jarvis")                              # from the Kylie Shortcut
        self.post("what is new", to="hermes:tax_angel_esther")   # then "Siri, Talk to Esther" on the same phone
        self.assertEqual(self.brain.asked, [("hermes:tax_angel_esther", "what is new")])
        self.assertEqual(receiver._client_switch, {})

    def test_the_switch_lapses_after_a_quiet_spell(self):
        self.post("talk to Jarvis")
        receiver._client_switch["127.0.0.1"]["seen"] -= receiver.SWITCH_KEEP_SEC + 1
        self.post("hello again")
        self.assertEqual(self.brain.asked, [("hermes:assistant", "hello again")])

    def test_ending_the_shortcut_forgets_the_switch(self):
        self.post("talk to Jarvis")
        self.assertIs(self.post("Stop.").get("end"), True)
        self.post("hello again")
        self.assertEqual(self.brain.asked[-1], ("hermes:assistant", "hello again"))

    def test_goodbye_after_a_switch_is_the_new_agents_goodbye(self):
        import commands
        self.post("talk to Jarvis")
        out = self.post("That's it.")
        self.assertEqual((out["reply"], out.get("end")), (commands.GOODBYE_REPLY, True))
        self.assertEqual(self.brain.asked, [("hermes:orchestrator", commands.GOODBYE_PROMPT)])
        self.assertEqual(receiver._client_switch, {})

    def test_name_then_goodbye_is_that_agents_goodbye(self):
        import commands
        out = self.post("Jarvis, goodbye")
        self.assertEqual((out["reply"], out.get("end")), (commands.GOODBYE_REPLY, True))
        self.assertEqual(self.brain.asked, [("hermes:orchestrator", commands.GOODBYE_PROMPT)])


class MacTalkToUnchangedTest(unittest.TestCase):
    """Brain.command now reads its phrase through brain.switch_request, the one the phone uses."""

    def test_switch_request(self):
        self.assertEqual(B.switch_request("Talk to Assistant."), "assistant")
        self.assertEqual(B.switch_request("okay, switch to Wanna Buffet please"), "wanna buffet")
        for said in ("switch to Opus", "talk to me about the budget", "what time is it", ""):
            self.assertIsNone(B.switch_request(said), said)


if __name__ == "__main__":
    unittest.main()
