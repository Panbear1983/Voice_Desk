"""agents.toml `no_fallback = true` (Peter 2026-11-11): an agent with its own local voice speaks that voice or stays
silent. It never drops to an Edge voice (Microsoft) and never to the plain Mac voice when its script fails.

Everything runs against fakes: the script runner and the Edge chain are mocked, and nothing is played.

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


def _voice(no_fallback: bool) -> voices.Voice:
    return voices.Voice("am_onyx", "zh-TW-HsiaoChenNeural", "agents",
                        ("/bin/bash", "/x/own_script.sh"), "en-US-AriaNeural", None, no_fallback)


class NoFallbackSpeech(unittest.TestCase):
    def _speak(self, v, script_result):
        """Run tts.make_audio-style synthesis with a fake script result; return (result, calls)."""
        calls = {"chain": 0, "say": 0}
        with mock.patch.object(tts, "_script", return_value=script_result), \
             mock.patch.object(tts, "_chain", side_effect=lambda *a, **k: calls.__setitem__("chain", calls["chain"] + 1)), \
             mock.patch("subprocess.run", side_effect=lambda *a, **k: (calls.__setitem__("say", calls["say"] + 1),
                                                                      mock.Mock(returncode=1))[1]):
            res = tts.synthesize("hello there, this is english", v)
        return res, calls

    def test_script_fails_and_no_fallback_means_silence_and_no_other_voice_is_tried(self):
        res, calls = self._speak(_voice(True), None)
        self.assertIsNone(res)
        self.assertEqual(calls["chain"], 0, "an Edge voice was tried")
        self.assertEqual(calls["say"], 0, "the Mac voice was tried")

    def test_without_the_switch_the_old_fallback_is_unchanged(self):
        _res, calls = self._speak(_voice(False), None)
        self.assertGreaterEqual(calls["chain"], 1, "default behaviour (Edge fallback) must stay as it was")

    def test_script_works_and_no_fallback_is_irrelevant(self):
        p = Path(tempfile.mkdtemp()) / "a.wav"
        p.write_bytes(b"RIFF")
        res, calls = self._speak(_voice(True), p)
        self.assertEqual(res, p)
        self.assertEqual(calls["chain"], 0)


class NoFallbackSetting(unittest.TestCase):
    def test_agents_toml_switch_reaches_the_voice(self):
        cfg = {"agents": {"x": {"target": "hermes:x", "display": "X", "names": ["x"], "voice": "am_onyx",
                                "command": ["/bin/bash", "/x/s.sh"], "no_fallback": True}}, "shared": {}}
        got = next(a for a in agents.agents(cfg) if a.target == "hermes:x")
        self.assertTrue(got.no_fallback)
        cfg["agents"]["x"].pop("no_fallback")
        got = next(a for a in agents.agents(cfg) if a.target == "hermes:x")
        self.assertFalse(got.no_fallback, "off unless the file says true")


if __name__ == "__main__":
    unittest.main()
