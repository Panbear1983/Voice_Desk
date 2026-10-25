"""Offline tests for the live-window doors (live_session.py): a fake herdr and fake session
transcripts; no microphone, no model, no real terminal is touched."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_tmp = tempfile.TemporaryDirectory(prefix="vd-live-")
TMP = Path(_tmp.name)
os.environ["VOICE_DESK_STATE"] = str(TMP / "state")
os.environ["VOICE_DESK_CLAUDE_PROJECTS"] = str(TMP / "projects")
os.environ["VOICE_DESK_HERDR_BIN"] = str(TMP / "herdr")

import settings as S
import live_session
from brain import Brain, ClaudeBrain
from codex_brain import CodexBrain

SID = "11111111-2222-3333-4444-555555555555"
TRANSCRIPT = TMP / "projects" / "-Users-peter" / f"{SID}.jsonl"
FAKE_HERDR = r'''#!{python}
import json, os, sys
from pathlib import Path
tmp = Path({tmp!r})
args = sys.argv[1:]
status = (tmp / "status").read_text().strip() if (tmp / "status").exists() else "idle"
if args[:2] == ["pane", "list"]:
    panes = [] if (tmp / "no_pane").exists() else [{{"pane_id": "wZ:p1", "agent": "claude", "agent_status": status,
              "agent_session": {{"value": {sid!r}}}}}]
    print(json.dumps({{"result": {{"panes": panes}}}}))
elif args[:2] == ["pane", "send-text"]:
    (tmp / "typed").write_text(args[3])
elif args[:2] == ["pane", "send-keys"]:
    typed = (tmp / "typed").read_text()
    lines = [{{"type": "user", "message": {{"content": typed}}}},
             {{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Let me check."}},
                                                           {{"type": "tool_use", "name": "Bash"}}]}}}},
             {{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "The study is running fine."}}]}}}},
             {{"type": "user", "message": {{"content": "Stop hook feedback: AUTO-SAVE checkpoint"}}}},
             {{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Memory is up to date."}}]}}}}]
    if not (tmp / "lost").exists():
        with open({transcript!r}, "a") as f:
            f.write("".join(json.dumps(l) + "\n" for l in lines))
    (tmp / "status").write_text("idle")
'''


class LiveClaudeTest(unittest.TestCase):
    def setUp(self):
        for name in ("status", "no_pane", "lost", "typed"):
            (TMP / name).unlink(missing_ok=True)
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text(json.dumps({"type": "user", "message": {"content": "earlier"}}) + "\n")
        herdr = TMP / "herdr"
        herdr.write_text(FAKE_HERDR.format(python=sys.executable, tmp=str(TMP), sid=SID, transcript=str(TRANSCRIPT)))
        herdr.chmod(0o755)
        (S.STATE_DIR / "claude_live_session").write_text(SID)
        live_session.QUIET_SEC = 0.6
        live_session.DELIVERY_TIMEOUT_SEC = 3
        live_session.ENTER_DELAY_SEC = 0
        live_session.RETRY_ENTER_SEC = 1

    def test_turn_types_into_the_pane_and_returns_the_answer_before_the_hook_note(self):
        brain = live_session.LiveBrain("claude")
        progress = []
        reply = brain.ask("how is the  study going", on_text=progress.append)
        self.assertEqual(reply, "The study is running fine.")
        self.assertEqual((TMP / "typed").read_text(), "[voice] how is the study going")
        self.assertEqual(progress, ["Let me check."])
        self.assertEqual(brain.last_tool, "Bash")
        self.assertFalse(S.LIVE_TURN_FLAG.exists())

    def test_router_uses_the_live_session_only_when_its_pane_is_open(self):
        self.assertIsInstance(Brain().backend("claude"), live_session.LiveBrain)
        (TMP / "no_pane").write_text("")
        self.assertIsInstance(Brain().backend("claude"), ClaudeBrain)
        (TMP / "no_pane").unlink()
        self.assertIsInstance(Brain().backend("claude"), live_session.LiveBrain)   # the only open window, pin or not
        self.assertIsInstance(Brain().backend("codex"), CodexBrain)                 # no Codex window open

    def test_words_that_never_arrive_are_reported(self):
        (TMP / "lost").write_text("")
        reply = live_session.LiveBrain("claude").ask("hello")
        self.assertIn("never arrived", reply)
        self.assertFalse(S.LIVE_TURN_FLAG.exists())


class TranscriptReadersTest(unittest.TestCase):
    """The Gemini and Codex windows write different transcript shapes; check each reader."""

    def test_gemini_reader(self):
        ev = list(live_session._gemini_events({"type": "USER_INPUT", "content": "<USER_REQUEST>\n[voice] hi\n</USER_REQUEST>\n<X>"}))
        self.assertEqual(ev, [("user", "[voice] hi")])
        self.assertEqual(list(live_session._gemini_events({"source": "MODEL", "type": "PLANNER_RESPONSE", "content": "ready"})),
                         [("say", "ready")])
        self.assertEqual(list(live_session._gemini_events({"source": "MODEL", "type": "PLANNER_RESPONSE", "content": ""})),
                         [("tool", "")])

    def test_codex_reader(self):
        user = {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                     "content": [{"type": "input_text", "text": "[voice] hi"}]}}
        done = {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "ready"}}
        self.assertEqual(list(live_session._codex_events(user)), [("user", "[voice] hi")])
        self.assertEqual(list(live_session._codex_events(done)), [("done", "ready")])


if __name__ == "__main__":
    unittest.main()
