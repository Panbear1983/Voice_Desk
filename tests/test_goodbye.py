"""Offline: the phone door ends on the same phrases as the Mac window (Peter 2026-11-02: "I want all
the phrases to be exactly the same as the Mac phrase to deactivate").

One phrase list (settings.py), one matcher (commands.classify), used by the Mac window (engine.py)
and the phone receiver (receiver.py); the Shortcut stops after each Speak when the receiver says
"end". Fakes only: no model, no window, no microphone, no real receiver, no network."""
import json
import os
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401  (the fake no-window herdr and throw-away state folder)

import commands  # noqa: E402
import settings as S  # noqa: E402
import receiver  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_every_phrase_in_the_one_list(self):
        for kind, words in (("goodbye", S.GOODBYE_WORDS), ("sleep", S.SLEEP_WORDS), ("stop", S.STOP_WORDS)):
            for w in words:
                self.assertEqual(commands.classify(w), kind, w)

    def test_peters_own_words_are_goodbyes(self):
        for w in ("No further question.", "No further questions.", "That's it.", "That is it",
                  "I'm done.", "We're done!"):
            self.assertEqual(commands.classify(w), "goodbye", w)

    def test_punctuation_case_and_dictation_apostrophes(self):
        for w in ("Goodbye.", "GOODBYE!", "  bye bye ", "Bye, Claude.", "That’s it.", "I’m done", "That's all?"):
            self.assertEqual(commands.classify(w), "goodbye", w)
        self.assertEqual(commands.classify("Go to sleep."), "sleep")
        self.assertEqual(commands.classify("Stop listening!"), "sleep")
        self.assertEqual(commands.classify("Hold on."), "stop")

    def test_mandarin(self):
        self.assertEqual(commands.classify("再見"), "goodbye")
        self.assertEqual(commands.classify("拜拜。"), "goodbye")
        self.assertEqual(commands.classify("休息"), "sleep")
        self.assertEqual(commands.classify("等一下"), "stop")

    def test_ordinary_sentences_are_not_commands(self):
        for w in ("", "   ", "What's the weather?", "Say goodbye to the old build", "Is that it?",
                  "I'm done with the report, what next", "that's it for the first part, now the second",
                  "Stop the build server please"):
            self.assertIsNone(commands.classify(w), w)

    def test_one_list_only(self):
        """No second copy of the phrases: the engine and receiver go through commands.classify."""
        root = Path(__file__).resolve().parents[1]
        for name in ("engine.py", "receiver.py"):
            src = (root / name).read_text()
            self.assertIn("commands.classify(", src, name)
            self.assertNotIn("GOODBYE_WORDS", src.split('"""', 2)[-1], name)   # code, not the docstring
        self.assertNotIn('"bye bye"', (root / "commands.py").read_text())


class FakeBrain:
    """Stands in for receiver.brain. Records what it was asked."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.asked: list[str] = []

    def resolve(self, to):
        return to

    def target(self):
        return "claude"

    def name(self, t=None):
        return str(t)

    def targets(self):
        return ["claude"]

    def last_tool_for(self, t=None):
        return ""

    def ask(self, text, to=None, **kw):
        self.asked.append(text)
        time.sleep(self.delay)
        return f"answer to {text}"


class ReceiverEndTest(unittest.TestCase):
    """The receiver's own HTTP handler on a throw-away port on 127.0.0.1, with a fake brain."""

    def setUp(self):
        receiver._jobs.clear()
        receiver._client_target.clear()
        self.addCleanup(receiver._jobs.clear)
        self.brain = FakeBrain()
        for p in (patch.object(receiver, "brain", self.brain), patch.object(receiver, "TOKEN", "test-token"),
                  patch.object(receiver, "log", lambda msg: None)):
            p.start()
            self.addCleanup(p.stop)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), receiver.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def post(self, text: str) -> dict:
        req = Request(self.base + "/ask", data=json.dumps({"text": text, "to": "claude"}).encode(),
                      headers={"X-Token": "test-token", "Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def get(self, job: str) -> dict:
        req = Request(f"{self.base}/reply?to=claude&job={job}", headers={"X-Token": "test-token"})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def test_goodbye_files_the_note_then_says_goodbye_and_ends(self):
        out = self.post("That's it.")
        self.assertEqual(out["reply"], commands.GOODBYE_REPLY)
        self.assertIs(out.get("end"), True)
        self.assertEqual(self.brain.asked, [commands.GOODBYE_PROMPT])    # the memory note, not "That's it."

    def test_mandarin_goodbye_ends(self):
        self.assertIs(self.post("拜拜").get("end"), True)

    def test_sleep_and_stop_end_the_phone_without_asking_the_brain(self):
        for w in ("Go to sleep.", "Stop."):
            out = self.post(w)
            self.assertIs(out.get("end"), True, w)
            self.assertEqual(out["reply"], "Okay.")
        self.assertEqual(self.brain.asked, [])

    def test_normal_text_never_ends(self):
        for w in ("What's on today?", "I'm done with the report, what next"):
            out = self.post(w)
            self.assertNotIn("end", out, w)
            self.assertEqual(out["reply"], f"answer to {w}")

    def test_slow_goodbye_carries_end_on_the_check_back(self):
        self.brain.delay = 0.3
        with patch.object(receiver, "ASK_WAIT_SEC", 0.05):
            out = self.post("Goodbye.")
        self.assertTrue(out["pending"])
        self.assertNotIn("end", out)                                     # not yet: the Shortcut keeps polling
        back = self.get(out["job"])
        self.assertEqual(back, {"reply": commands.GOODBYE_REPLY, "end": True})

    def test_slow_normal_answer_check_back_never_ends(self):
        self.brain.delay = 0.3
        with patch.object(receiver, "ASK_WAIT_SEC", 0.05):
            out = self.post("Tell me a story")
        self.assertEqual(self.get(out["job"]), {"reply": "answer to Tell me a story"})
        self.assertEqual(self.get(out["job"]), {"reply": receiver.SILENT})


class MacWindowTest(unittest.TestCase):
    """The Mac window's loop (engine.Engine) with a fake mic/whisper/brain and the speaker recorded:
    the same phrases end it there, through the same matcher."""

    class Brain:
        def __init__(self):
            self.asked: list[str] = []

        def ask(self, text, **kw):
            self.asked.append(text)
            return "fine"

        def cancel(self):
            pass

    def turn(self, said: str):
        import numpy as np
        import engine
        brain = self.Brain()
        e = engine.Engine(mic=object(), whisper=object(), brain=brain)
        spoken: list[str] = []
        e._capture = lambda: np.zeros(1)
        e._transcribe = lambda audio: said
        e._speak = lambda text, allow_barge_in=False: spoken.append(text)
        with patch.object(engine, "addressed", lambda text: None), patch.object(engine.tts, "tick", lambda: None):
            e._one_turn()
        return e, brain, spoken

    def test_peters_new_words_end_the_mac_window_too(self):
        for w in ("That's it.", "No further questions.", "We're done.", "再見"):
            e, brain, spoken = self.turn(w)
            self.assertEqual(brain.asked, [commands.GOODBYE_PROMPT], w)
            self.assertEqual(spoken, [commands.GOODBYE_REPLY], w)
            self.assertEqual(e.state, "off", w)

    def test_sleep_and_stop_and_normal_text(self):
        e, brain, _ = self.turn("Go to sleep.")
        self.assertTrue(e.asleep)
        self.assertEqual(brain.asked, [])
        e, brain, _ = self.turn("Hold on.")
        self.assertEqual((e.state, brain.asked), ("listening", []))
        e, brain, spoken = self.turn("Is that it?")
        self.assertEqual((brain.asked, spoken), (["Is that it?"], ["fine"]))


class ShortcutTest(unittest.TestCase):
    """The Shortcut plist: one Stop This Shortcut behind an If after every Speak step."""

    @classmethod
    def setUpClass(cls):
        token = Path(tests._tmp) / "shortcut-test-token"                  # never the real receiver token
        token.write_text("test-token")
        with patch.dict(os.environ, {"VOICE_DESK_MAC_IP": "192.0.2.1"}), \
                patch.object(S, "RECEIVER_TOKEN_FILE", token):             # documentation address, not a real Mac
            import make_shortcut
        cls.M = make_shortcut

    def ids(self, plist: dict) -> list[str]:
        return [a["WFWorkflowActionIdentifier"] for a in plist["WFWorkflowActions"]]

    def test_exit_once_per_speak_step(self):
        ids = self.ids(self.M.build("claude"))
        speaks = ids.count("is.workflow.actions.speaktext")
        self.assertEqual(speaks, 1 + self.M.POLLS)
        self.assertEqual(ids.count("is.workflow.actions.exit"), speaks)
        for i, a in enumerate(ids):
            if a == "is.workflow.actions.speaktext":
                self.assertEqual(ids[i + 1:i + 6], ["is.workflow.actions.getvalueforkey", "is.workflow.actions.conditional",
                                                    "is.workflow.actions.exit", "is.workflow.actions.conditional",
                                                    "is.workflow.actions.conditional"])

    def test_if_reads_end_from_the_same_url_result_and_stays_inside_the_repeat(self):
        acts = self.M.build("hermes:builder")["WFWorkflowActions"]
        self.assertEqual(acts[0]["WFWorkflowActionIdentifier"], "is.workflow.actions.repeat.count")
        self.assertEqual(acts[-1]["WFWorkflowActionIdentifier"], "is.workflow.actions.repeat.count")
        self.assertEqual(acts[0]["WFWorkflowActionParameters"]["WFRepeatCount"], self.M.REPEATS)
        for i, a in enumerate(acts):
            if a["WFWorkflowActionIdentifier"] != "is.workflow.actions.exit":
                continue
            get_reply, get_end, cond = acts[i - 4], acts[i - 2], acts[i - 1]
            self.assertEqual(get_end["WFWorkflowActionParameters"]["WFDictionaryKey"], "end")
            self.assertEqual(get_end["WFWorkflowActionParameters"]["WFInput"],
                             get_reply["WFWorkflowActionParameters"]["WFInput"])
            p = cond["WFWorkflowActionParameters"]
            self.assertEqual((p["WFControlFlowMode"], p["WFCondition"]), (0, self.M.IF_HAS_ANY_VALUE))
            self.assertEqual(p["WFInput"]["Variable"]["Value"]["OutputUUID"],
                             get_end["WFWorkflowActionParameters"]["UUID"])
            groups = [x["WFWorkflowActionParameters"]["GroupingIdentifier"] for x in (acts[i - 1], acts[i + 1], acts[i + 2])]
            self.assertEqual(len(set(groups)), 1)
            self.assertEqual([acts[i + 1]["WFWorkflowActionParameters"]["WFControlFlowMode"],
                              acts[i + 2]["WFWorkflowActionParameters"]["WFControlFlowMode"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
