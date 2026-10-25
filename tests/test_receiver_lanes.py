"""Offline: two devices with OLD shortcuts (check-backs carry no job and no target) each get their
own answer, and an answer abandoned long ago is not read out to a new question. Fake brain only."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import receiver


class FakeBrain:
    def __init__(self):
        self.sticky = "claude"
    def resolve(self, to):
        return {"gemini": "gemini", "codex": "codex", "claude": "claude"}.get(to, to)
    def target(self):
        return self.sticky
    def name(self, t=None):
        return str(t)
    def last_tool_for(self, t=None):
        return ""
    def ask(self, text, to=None, **kw):
        time.sleep(0.2 if to == "codex" else 0.5)
        return f"{to} says {text}"


class LanesTest(unittest.TestCase):
    def setUp(self):
        receiver.brain = FakeBrain()
        receiver._jobs.clear()
        receiver._client_target.clear()

    def test_two_devices_same_assistant_get_their_own_answers(self):
        receiver.start_job("from iphone", "gemini", client_ip="iphone")
        receiver.start_job("from ipad", "gemini", client_ip="ipad")
        a, ok_a = receiver.take_reply(5, client_ip="ipad")
        b, ok_b = receiver.take_reply(5, client_ip="iphone")
        self.assertTrue(ok_a and ok_b)
        self.assertEqual(a, "gemini says from ipad")
        self.assertEqual(b, "gemini says from iphone")

    def test_abandoned_answer_is_not_read_out_later(self):
        job = receiver.start_job("old question", "codex", client_ip="iphone")
        time.sleep(0.5)
        job["started"] -= receiver.STALE_SEC + 60          # pretend it finished six minutes ago
        text, ok = receiver.take_reply(1, client_ip="iphone")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
