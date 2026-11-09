"""Offline tests for Claude and Gemini joining the speech queue (step 4).

The two Stop hooks live outside this repo (~/.claude/hooks/speak-natural.py and speak-response.sh,
~/.gemini/config/hooks/speak-response.py). These tests run those real files, read-only, in a throw-away HOME with fake
players on PATH: no speaker, no mic, no network (edge-tts is a fake), no real Voice Desk queue. They skip themselves
if the hooks are not on this Mac.

    .venv/bin/python -m unittest tests.test_speak_hooks -v
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401

import speechq as Q  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_HOOKS = Path.home() / ".claude/hooks"
GEMINI_HOOK = Path.home() / ".gemini/config/hooks/speak-response.py"
GEMINI_PY = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def wait_for(pred, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


class Fakes(unittest.TestCase):
    """A fake HOME with: a stand-in afplay (the queue), a plain afplay, killall and say, all of which only write to a log."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="vd-hooks-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.state = self.home / ".voice_desk"
        (self.state / "speech").mkdir(parents=True)
        self.bin = self.home / "fakebin"
        self.bin.mkdir()
        self.log = self.home / "calls.log"
        self.log.touch()
        self.stand_in = script(self.home / "bin_afplay", f'echo "queue:$VOICE_DESK_SPEAKER:$(basename "$1")" >> "{self.log}"\n')
        self.real = script(self.home / "real_afplay", f'echo "straight:$(basename "$1")" >> "{self.log}"\n')
        script(self.bin / "afplay", f'echo "path-afplay:$(basename "$1")" >> "{self.log}"\n')
        script(self.bin / "killall", f'echo "killall:$*" >> "{self.log}"\n')
        script(self.bin / "say", 'o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && o="$2"; shift; done\n'
                                 f'echo "say" >> "{self.log}"; [ -n "$o" ] && printf aiff > "$o"\nexit 0\n')
        self.edge = script(self.home / "fake-edge-tts",
                           'while [ $# -gt 0 ]; do [ "$1" = "--write-media" ] && out="$2"; shift; done\nprintf mp3 > "$out"\n')

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("VOICE_DESK") and k not in ("HERMES_HOME",)}
        env.update(HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}", VOICE_DESK_STATE=str(self.state),
                   VOICE_DESK_AFPLAY_STANDIN=str(self.stand_in), VOICE_DESK_REAL_AFPLAY=str(self.real),
                   EDGE_TTS=str(self.edge), SPEAK_STATE_DIR=str(self.home / "hookstate"))
        env.update(extra)
        return env

    def calls(self):
        return self.log.read_text().splitlines()


@unittest.skipUnless((CLAUDE_HOOKS / "speak-natural.py").exists(), "Claude's speak hook is not on this Mac")
class ClaudeJoinsTheQueue(Fakes):
    def speak(self, **extra):
        text = self.home / "text.txt"
        text.write_text(" ".join(f"Sentence number {i} is here to make the text long enough to need two chunks. " * 2 for i in range(4)))
        r = subprocess.run([sys.executable, str(CLAUDE_HOOKS / "speak-natural.py"), str(text), "en-US-JennyNeural", ""],
                           env=self.env(**extra), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.calls()

    def test_every_chunk_goes_through_the_queue_as_claude(self):
        calls = self.speak()
        self.assertGreaterEqual(len(calls), 2)                              # more than one chunk
        self.assertTrue(all(c.startswith("queue:claude:") for c in calls), calls)

    def test_the_off_switch_file_plays_straight_like_before(self):
        (self.state / "speech/off").write_text("off\n")
        calls = self.speak()
        self.assertTrue(calls and all(c.startswith("straight:") for c in calls), calls)

    def test_without_the_stand_in_it_plays_straight_like_before(self):
        calls = self.speak(VOICE_DESK_AFPLAY_STANDIN=str(self.home / "no-such-stand-in"))
        self.assertTrue(calls and all(c.startswith("straight:") for c in calls), calls)

    def test_the_whole_hook_speaks_through_the_queue(self):
        """speak-response.sh (the real Stop hook) -> speak-natural.py -> the queue's stand-in."""
        hooks = self.home / ".claude/hooks"
        hooks.mkdir(parents=True)
        for name in ("speak-response.sh", "speak-natural.py"):
            (hooks / name).symlink_to(CLAUDE_HOOKS / name)
        (self.home / ".claude/settings.json").write_text(json.dumps({"voice": {"enabled": True}}))
        transcript = self.home / "t.jsonl"
        transcript.write_text("")
        payload = json.dumps({"transcript_path": str(transcript), "session_id": "s1", "prompt_id": "p1",
                              "last_assistant_message": "The build is finished. Everything passed."})
        r = subprocess.run(["/bin/bash", str(hooks / "speak-response.sh")], input=payload, env=self.env(),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(wait_for(lambda: self.calls()), "nothing was played")
        self.assertTrue(self.calls()[0].startswith("queue:claude:"), self.calls())

    def test_voice_off_in_claude_settings_still_means_silence(self):
        hooks = self.home / ".claude/hooks"
        hooks.mkdir(parents=True)
        for name in ("speak-response.sh", "speak-natural.py"):
            (hooks / name).symlink_to(CLAUDE_HOOKS / name)
        (self.home / ".claude/settings.json").write_text(json.dumps({"voice": {"enabled": False}}))
        transcript = self.home / "t.jsonl"
        transcript.write_text("")
        subprocess.run(["/bin/bash", str(hooks / "speak-response.sh")], env=self.env(), capture_output=True, text=True, timeout=30,
                       input=json.dumps({"transcript_path": str(transcript), "last_assistant_message": "Hello there."}))
        time.sleep(1.5)
        self.assertEqual(self.calls(), [])


@unittest.skipUnless(GEMINI_HOOK.exists() and Path(GEMINI_PY).exists(), "Gemini's speak hook is not on this Mac")
class GeminiJoinsTheQueue(Fakes):
    def setUp(self):
        super().setUp()
        hooks = self.home / ".gemini/config/hooks"
        hooks.mkdir(parents=True)
        (hooks / "speak-response.py").symlink_to(GEMINI_HOOK)
        self.hook = hooks / "speak-response.py"
        self.fake_mods = self.home / "fakemods"
        self.fake_mods.mkdir()
        self.set_edge(works=True)
        self.transcript = self.home / "transcript.jsonl"
        self.transcript.write_text(json.dumps({"source": "MODEL", "type": "PLANNER_RESPONSE",
                                               "content": "Hello from Gemini. All good."}) + "\n")

    def set_edge(self, works: bool):
        (self.fake_mods / "edge_tts.py").write_text(
            "class Communicate:\n    def __init__(self, text, voice):\n        self.t = text\n"
            "    async def save(self, path):\n"
            + ("        open(path, 'wb').write(b'mp3')\n" if works else "        raise RuntimeError('no network')\n"))

    def fire(self, **extra):
        r = subprocess.run([GEMINI_PY, str(self.hook)], input=json.dumps({"transcriptPath": str(self.transcript)}),
                           env=self.env(PYTHONPATH=str(self.fake_mods), **extra), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"decision": "stop"', r.stdout)
        return r

    def hook_log(self):
        f = self.home / ".gemini/config/hooks/voice.log"
        return f.read_text() if f.exists() else ""

    def test_it_waits_its_turn_in_the_queue_and_does_not_cut_the_others_off(self):
        self.fire()
        self.assertTrue(wait_for(lambda: any(c.startswith("queue:gemini:") for c in self.calls())), self.calls())
        time.sleep(0.5)
        self.assertFalse([c for c in self.calls() if c.startswith("killall")], self.calls())   # never killall afplay now
        self.assertFalse([c for c in self.calls() if c.startswith(("straight", "path-afplay"))])

    def test_with_the_queue_off_it_behaves_exactly_as_before(self):
        (self.state / "speech/off").write_text("off\n")
        self.fire()
        self.assertTrue(wait_for(lambda: any(c.startswith("path-afplay:") for c in self.calls())), self.calls())
        self.assertTrue([c for c in self.calls() if c.startswith("killall:afplay")], self.calls())   # the old behaviour

    def test_the_switch_file_keeps_gemini_silent(self):
        (self.state / "gemini_voice_off").write_text("off\n")
        self.fire()
        time.sleep(2.0)
        self.assertEqual(self.calls(), [])
        self.assertIn("gemini_voice_off", self.hook_log())

    def test_the_hook_source_mentions_the_switch_so_the_grid_knows_it_listens(self):
        self.assertIn("gemini_voice_off", GEMINI_HOOK.read_text())

    def test_when_the_neural_voice_fails_the_mac_voice_is_also_played_in_turn(self):
        self.set_edge(works=False)
        self.fire()
        self.assertTrue(wait_for(lambda: any(c.startswith("queue:gemini:") for c in self.calls())), self.calls())
        self.assertIn("say", self.calls())
        self.assertTrue([c for c in self.calls() if c.endswith(".aiff")], self.calls())       # a file, played by the stand-in


class TheQueueKnowsClaudeAndGemini(unittest.TestCase):
    def test_the_speaker_name_is_taken_from_the_environment_but_only_for_those_two(self):
        self.assertEqual(Q.owner_from_env({"VOICE_DESK_SPEAKER": "claude"}, ppid_cmd=""), "claude")
        self.assertEqual(Q.owner_from_env({"VOICE_DESK_SPEAKER": "Gemini"}, ppid_cmd=""), "gemini")
        self.assertEqual(Q.owner_from_env({"VOICE_DESK_SPEAKER": "mallory"}, ppid_cmd=""), Q.OTHER)
        self.assertEqual(Q.owner_from_env({"HERMES_HOME": "/x/profiles/assistant", "VOICE_DESK_SPEAKER": "claude"}, ppid_cmd=""), "claude")
        self.assertEqual(Q.owner_from_env({"HERMES_HOME": "/x/profiles/assistant"}, ppid_cmd=""), "assistant")

    def test_claude_holds_the_floor_between_chunks_and_waits_for_another_agents_floor(self):
        clock = {"t": 1000.0}
        root = Path(tempfile.mkdtemp(prefix="vd-q-"))
        self.addCleanup(shutil.rmtree, root, True)
        st = Q.Station(root, clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
        st._write("floor.json", {"owner": "assistant", "until": 1001.0})          # Kylie is mid-reply
        waited = {}
        with st.turn("claude", known=set()) as turn:
            waited["s"] = turn.waited
            self.assertFalse(turn.name)                                          # no "<name> here." for Claude
        self.assertGreaterEqual(waited["s"], 0.9)                                 # it did wait for Kylie's floor
        self.assertEqual(st.floor()["owner"], "claude")                           # and now it holds the floor itself
        self.assertEqual(st.last()["owner"], "claude")

    def test_the_stand_in_plays_a_claude_file_with_the_real_afplay_and_leaves_no_ticket(self):
        root = Path(tempfile.mkdtemp(prefix="vd-q-"))
        self.addCleanup(shutil.rmtree, root, True)
        log = root / "played.txt"
        real = script(root / "real", f'echo "$1" >> "{log}"\n')
        audio = root / "a.mp3"
        audio.write_bytes(b"x")
        env = {**os.environ, "VOICE_DESK_STATE": str(root), "VOICE_DESK_REAL_AFPLAY": str(real), "VOICE_DESK_SPEAKER": "claude"}
        r = subprocess.run([sys.executable, str(ROOT / "speechq.py"), "afplay", str(audio)], env=env, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(log.read_text().strip(), str(audio))
        self.assertEqual(Q.Station(root=root / "speech").line(), [])
        self.assertEqual(Q.Station(root=root / "speech").floor()["owner"], "claude")


if __name__ == "__main__":
    unittest.main()
