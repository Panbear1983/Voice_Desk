"""One voice, no fallback, empty = silent (Peter 2026-11-11).

First built as the agents.toml `no_fallback = true` switch for five agents; since the dashboard iteration of
2026-11-13 it is how EVERY agent speaks: its own voice for the language, or silence. A failed voice never drops to
a second Edge voice, Piper, Google or the plain Mac voice, and an agent with no voice is silent.

Everything runs against fakes: the script runner, Edge and `say` are mocked, and nothing is played.

    .venv/bin/python -m unittest tests.test_no_fallback -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tests  # noqa: E402,F401

import agents  # noqa: E402
import tts  # noqa: E402
import voices  # noqa: E402


def _script_voice() -> voices.Voice:
    return voices.Voice("am_onyx", "zh-TW-HsiaoChenNeural", "agents", ("/bin/bash", "/x/own_script.sh"))


class OneVoiceNoFallback(unittest.TestCase):
    def _speak(self, v, text="hello there, this is english", script_result=None, edge_ok=False):
        """synthesize() with fakes; returns (result, calls). Every other process (say, ffmpeg, Hermes' chain) is
        counted, so any second voice shows up."""
        calls = {"script": [], "edge": [], "other": []}

        def script(cmd, txt, out, voice, **k):
            calls["script"].append(voice)
            return script_result

        def edge(txt, out, voice):
            calls["edge"].append(voice)
            if edge_ok:
                out.write_bytes(b"ID3")
            return edge_ok

        with mock.patch.object(tts, "_script", side_effect=script), \
             mock.patch.object(tts, "edge_exact", side_effect=edge), \
             mock.patch("subprocess.run", side_effect=lambda *a, **k: (calls["other"].append(a), mock.Mock(returncode=1))[1]):
            res = tts.synthesize(text, v)
        return res, calls

    def test_a_failing_script_voice_means_silence_and_no_other_voice_is_tried(self):
        res, calls = self._speak(_script_voice(), script_result=None)
        self.assertIsNone(res)
        self.assertEqual(calls["script"], ["am_onyx"])
        self.assertEqual(calls["edge"], [], "an Edge voice was tried after the script failed")
        self.assertEqual(calls["other"], [], "another program (the Mac voice, Hermes' chain) was tried")

    def test_the_no_fallback_flag_no_longer_matters_every_agent_is_silent_on_failure(self):
        cfg = {"agents": {"x": {"target": "hermes:x", "display": "X", "names": ["x"], "voice": "am_onyx",
                                "command": ["/bin/bash", "/x/s.sh"]}}, "shared": {}}
        with mock.patch.object(voices, "config", return_value=cfg):
            v = voices.voice_for("hermes:x")
        res, calls = self._speak(v, script_result=None)
        self.assertIsNone(res)
        self.assertEqual((calls["edge"], calls["other"]), ([], []))

    def test_a_failing_edge_voice_is_tried_by_name_only_then_silence(self):
        v = voices.Voice("en-US-AvaNeural", "zh-TW-HsiaoChenNeural", "agents")
        res, calls = self._speak(v, edge_ok=False)
        self.assertIsNone(res)
        self.assertEqual(calls["edge"], ["en-US-AvaNeural"], "only the agent's own voice, by name")
        self.assertEqual(calls["other"], [])

    def test_a_failing_mandarin_voice_is_silent_and_never_the_mac_meijia_voice(self):
        v = voices.Voice("en-US-AvaNeural", "zh-TW-HsiaoChenNeural", "agents")
        res, calls = self._speak(v, text="這是一個中文句子", edge_ok=False)
        self.assertIsNone(res)
        self.assertEqual(calls["edge"], ["zh-TW-HsiaoChenNeural"])
        self.assertEqual(calls["other"], [], "the Mac's Meijia voice was tried")

    def test_a_working_voice_speaks(self):
        p = Path(tempfile.mkdtemp()) / "a.wav"
        p.write_bytes(b"RIFF")
        res, calls = self._speak(_script_voice(), script_result=p)
        self.assertEqual(res, p)
        self.assertEqual(calls["edge"], [])
        res, calls = self._speak(voices.Voice("en-US-AvaNeural", "", "agents"), edge_ok=True)
        self.assertIsNotNone(res)

    def test_a_voiceless_agent_and_no_voice_at_all_are_silent_and_nothing_runs(self):
        for v in (None, voices.SILENT, voices.Voice("", "", "silent")):
            res, calls = self._speak(v)
            self.assertIsNone(res)
            self.assertEqual(calls, {"script": [], "edge": [], "other": []})

    def test_voice_for_gives_silence_to_an_agent_without_a_voice_and_to_an_unknown_target(self):
        cfg = {"agents": {"builder": {"profile": "builder", "display": "Builder", "names": ["builder"]}},
               "shared": {}}
        with mock.patch.object(voices, "config", return_value=cfg):
            self.assertEqual(voices.voice_for("hermes:builder"), voices.SILENT)
            self.assertEqual(voices.voice_for("hermes:nobody"), voices.SILENT)
        self.assertFalse(hasattr(voices, "shared_voice"), "the shared Voice Desk voice is gone")
        self.assertFalse(hasattr(voices, "per_profile_enabled"), "the V key's switch is gone")

    def test_a_mandarin_line_for_an_english_only_voice_is_silent(self):
        v = voices.Voice("am_puck", "", "agents", ("/bin/bash", "/x/jarvis.sh"))
        res, calls = self._speak(v, text="你好 Peter")
        self.assertIsNone(res)
        self.assertEqual(calls, {"script": [], "edge": [], "other": []})


class TheSetting(unittest.TestCase):
    def test_agents_toml_switch_is_still_read(self):
        cfg = {"agents": {"x": {"target": "hermes:x", "display": "X", "names": ["x"], "voice": "am_onyx",
                                "command": ["/bin/bash", "/x/s.sh"], "no_fallback": True}}, "shared": {}}
        got = next(a for a in agents.agents(cfg) if a.target == "hermes:x")
        self.assertTrue(got.no_fallback)

    def test_the_real_agents_toml_has_no_shared_voice_and_no_fallback_field(self):
        cfg = agents.load()
        self.assertNotIn("voice", cfg["shared"])
        self.assertNotIn("voice_zh", cfg["shared"])
        for key, raw in cfg["agents"].items():
            self.assertNotIn("fallback", raw, key)
        self.assertNotIn("Jenny", agents.PATH.read_text(encoding="utf-8").split("[agents.")[0]
                         .replace("no Jenny", ""), "no Jenny in the shared part")


if __name__ == "__main__":
    unittest.main()
