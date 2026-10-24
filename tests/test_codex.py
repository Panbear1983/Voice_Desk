"""Offline process-level Codex adapter tests; no login, network, microphone or model calls."""
import json
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_state = tempfile.TemporaryDirectory(prefix="vd-codex-import-")
os.environ["VOICE_DESK_STATE"] = _state.name

import settings as S
from codex_brain import CodexBrain
from brain import Brain, addressed

SID = "0199a213-81c0-7800-8aa1-bbab2a035a53"
OTHER = "0199a213-81c0-7800-8aa1-bbab2a035a54"
FAKE = '''#!{python}
import json, os, sys, time
from pathlib import Path
prompt = sys.stdin.read()
with open(os.environ["FAKE_CODEX_CALLS"], "a") as f:
 f.write(json.dumps({{"args":sys.argv[1:], "prompt":prompt, "loop":os.environ.get("VOICE_DESK_LOOP")}})+"\\n")
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
sid = "{sid}" if mode != "wrong_session" else "{other}"
if mode == "stderr":
 print("private diagnostic should not be spoken",file=sys.stderr); sys.exit(1)
print(json.dumps({{"type":"thread.started","thread_id":sid}}),flush=True)
if mode == "sleep":time.sleep(30)
if mode == "quota":
 print(json.dumps({{"type":"turn.failed","error":{{"message":"Usage limit reached"}}}}));sys.exit(1)
print(json.dumps({{"type":"item.completed","item":{{"type":"command_execution","aggregated_output":"do not speak tool output"}}}}))
print(json.dumps({{"type":"item.completed","item":{{"type":"agent_message","text":"Earlier progress"}}}}))
print(json.dumps({{"type":"item.completed","item":{{"type":"agent_message","text":"The answer is ready."}}}}))
print(json.dumps({{"type":"turn.completed","usage":{{}}}}))
'''


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vd-codex-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "codex"
        self.bin.write_text(FAKE.format(python=sys.executable, sid=SID, other=OTHER))
        self.bin.chmod(0o700)
        self.calls_file = self.root / "calls.jsonl"
        self.settings = patch.multiple(S, CODEX_BIN=str(self.bin), CODEX_CWD=self.root,
                                      CODEX_SESSION_FILE=self.root / "codex_session",
                                      TRANSCRIPT_LOG=self.root / "transcript.log",
                                      BRAIN_FILE=self.root / "brain", CODEX_MODEL="", CODEX_EFFORT="")
        self.settings.start();self.addCleanup(self.settings.stop)
        self.env = patch.dict(os.environ, {"FAKE_CODEX_CALLS":str(self.calls_file),"FAKE_CODEX_MODE":"ok"})
        self.env.start();self.addCleanup(self.env.stop)

    def calls(self):
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()] if self.calls_file.exists() else []

    def test_new_conversation_then_exact_resume_and_safe_input(self):
        brain = CodexBrain()
        self.assertEqual(brain.ask('--model bad; $(touch never)'), "The answer is ready.")
        self.assertEqual(brain.session_id, SID)
        self.assertEqual(S.CODEX_SESSION_FILE.stat().st_mode & 0o777, 0o600)
        next_brain = CodexBrain()
        self.assertEqual(next_brain.ask("second question"), "The answer is ready.")
        first, second = self.calls()
        self.assertNotIn('resume',first['args'])
        self.assertEqual(second['args'][-3:], ['resume',SID,'-'])
        self.assertNotIn('--last',second['args'])
        self.assertNotIn('--model bad; $(touch never)',first['args'])
        self.assertIn('--model bad; $(touch never)',first['prompt'])
        self.assertIn('workspace-write',first['args'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',first['args'])
        self.assertEqual(first['loop'],'1')

    def test_quota_failure_saves_thread_without_automatic_resend(self):
        os.environ['FAKE_CODEX_MODE']='quota'
        brain=CodexBrain();reply=brain.ask('question')
        self.assertIn('usage limit',reply)
        self.assertEqual(brain.session_id,SID)
        self.assertEqual(len(self.calls()),1)

    def test_unexpected_new_session_does_not_replace_saved_id(self):
        S.CODEX_SESSION_FILE.write_text(SID)
        os.environ['FAKE_CODEX_MODE']='wrong_session'
        self.assertIn('did not resume',CodexBrain().ask('question'))
        self.assertEqual(S.CODEX_SESSION_FILE.read_text(),SID)
        self.assertEqual(len(self.calls()),1)

    def test_raw_diagnostics_not_spoken(self):
        os.environ['FAKE_CODEX_MODE']='stderr'
        reply=CodexBrain().ask('question')
        self.assertIn('did not complete',reply)
        self.assertNotIn('private diagnostic',reply)

    def test_timeout_stops_process(self):
        os.environ['FAKE_CODEX_MODE']='sleep'
        with patch.object(S,'CODEX_TURN_TIMEOUT_SEC',0.15):
            started=time.monotonic();reply=CodexBrain().ask('question')
        self.assertIn('took too long',reply)
        self.assertLess(time.monotonic()-started,4)

    def test_cancel_stops_process_and_returns(self):
        os.environ['FAKE_CODEX_MODE']='sleep'
        brain=CodexBrain();result=[]
        thread=threading.Thread(target=lambda:result.append(brain.ask('question')))
        thread.start()
        deadline=time.monotonic()+3
        while not self.calls_file.exists() and time.monotonic()<deadline:time.sleep(0.01)
        brain.cancel();thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result,['Okay, I stopped Codex.'])

    def test_blank_and_unavailable(self):
        self.assertIn('did not hear',CodexBrain().ask(' '))
        self.assertEqual(self.calls(),[])
        with patch.object(S,'CODEX_BIN','/missing/codex'):
            self.assertIn('could not start',CodexBrain().ask('question'))
            self.assertNotIn('codex',Brain().targets())
            self.assertIsNone(addressed('Hey Codex, hello',hailed=True))

    def test_routing_persistence_and_new_thread(self):
        brain=Brain()
        self.assertIn('codex',brain.targets())
        self.assertEqual(addressed('Hey Codex, hello',hailed=True),('codex','hello'))
        self.assertEqual(brain.resolve('openai codex'),'codex')
        self.assertIn('Codex',brain.command('talk to Codex'))
        self.assertEqual(brain.target(),'codex')
        self.assertEqual(brain.ask('question',to='codex'),'The answer is ready.')
        brain.new_thread('codex')
        self.assertFalse(S.CODEX_SESSION_FILE.exists())
        self.assertIsNone(brain.codex.session_id)

    def test_model_and_effort_overrides_are_arguments(self):
        with patch.object(S,'CODEX_MODEL','chosen-model'),patch.object(S,'CODEX_EFFORT','high'):
            CodexBrain().ask('question')
        args=self.calls()[0]['args']
        self.assertIn('chosen-model',args)
        self.assertIn('model_reasoning_effort="high"',args)

    def test_phone_shortcut_pins_codex_without_shell_action(self):
        token=self.root/'receiver_token';token.write_text('test-only-token')
        with patch.object(S,'RECEIVER_TOKEN_FILE',token),patch.dict(os.environ,{'VOICE_DESK_MAC_IP':'127.0.0.1'}):
            spec=importlib.util.spec_from_file_location('test_shortcut_generator',ROOT/'make_shortcut.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            workflow=module.build('codex')
        actions=workflow['WFWorkflowActions']
        self.assertFalse(any('shellscript' in action['WFWorkflowActionIdentifier'] for action in actions))
        post=next(a['WFWorkflowActionParameters'] for a in actions
                  if a['WFWorkflowActionParameters'].get('WFHTTPMethod')=='POST')
        fields=post['WFJSONValues']['Value']['WFDictionaryFieldValueItems']
        values={field['WFKey']['Value']['string']:field['WFValue']['Value']['string'] for field in fields}
        self.assertEqual(values['to'],'codex')


if __name__=='__main__':unittest.main()
