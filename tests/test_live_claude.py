"""Offline tests for the live-session Claude door (live_claude.py): a fake herdr and a fake
session transcript; no microphone, no model, no real terminal is touched."""
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
import live_claude
from brain import Brain, ClaudeBrain

SID = "11111111-2222-3333-4444-555555555555"
TRANSCRIPT = TMP / "projects" / "-Users-peter" / f"{SID}.jsonl"
FAKE_HERDR = r'''#!{python}
import json, os, sys
from pathlib import Path
tmp = Path({tmp!r})
args = sys.argv[1:]
status = (tmp / "status").read_text().strip() if (tmp / "status").exists() else "idle"
if args[:2] == ["pane", "list"]:
    panes = [] if (tmp / "no_pane").exists() else [{{"pane_id": "wZ:p1", "agent_status": status,
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
        S.CLAUDE_LIVE_SESSION_FILE.write_text(SID)
        live_claude.QUIET_SEC = 0.6
        live_claude.DELIVERY_TIMEOUT_SEC = 3

    def test_turn_types_into_the_pane_and_returns_the_answer_before_the_hook_note(self):
        brain = live_claude.LiveClaudeBrain()
        progress = []
        reply = brain.ask("how is the  study going", on_text=progress.append)
        self.assertEqual(reply, "The study is running fine.")
        self.assertEqual((TMP / "typed").read_text(), "[voice] how is the study going")
        self.assertEqual(progress, ["Let me check."])
        self.assertEqual(brain.last_tool, "Bash")
        self.assertFalse(S.LIVE_TURN_FLAG.exists())

    def test_router_uses_the_live_session_only_when_its_pane_is_open(self):
        self.assertIsInstance(Brain().backend("claude"), live_claude.LiveClaudeBrain)
        (TMP / "no_pane").write_text("")
        self.assertIsInstance(Brain().backend("claude"), ClaudeBrain)
        S.CLAUDE_LIVE_SESSION_FILE.unlink()
        (TMP / "no_pane").unlink()
        self.assertIsInstance(Brain().backend("claude"), ClaudeBrain)

    def test_words_that_never_arrive_are_reported(self):
        (TMP / "lost").write_text("")
        reply = live_claude.LiveClaudeBrain().ask("hello")
        self.assertIn("never arrived", reply)
        self.assertFalse(S.LIVE_TURN_FLAG.exists())


if __name__ == "__main__":
    unittest.main()
