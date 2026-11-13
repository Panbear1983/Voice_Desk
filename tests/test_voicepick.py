"""The voice pick-list behind E / Z / F (Part 2 of the 2026-11-13 dashboard iteration). Pure lists, nothing written.

    .venv/bin/python -m unittest tests.test_voicepick -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import voicepick as P  # noqa: E402

SIMBA = '''
[agents.simba]
target = "simba"
display = "Simba"
names = ["simba"]
voice = "am_fenrir"
effect = "16 pitch=1.4"
english_only = true
command = ["/nonexistent/python", "/nonexistent/simba_tts.py"]
'''


def world_cfg(extra: str = "") -> dict:
    w = F.make_world()
    w.toml.write_text(F.AGENTS_TOML + extra)
    return agents.load(w.toml)


def agent(cfg: dict, key: str) -> agents.Agent:
    return next(a for a in agents.agents(cfg) if a.key == key)


def ids(options) -> list[str]:
    return [o.token for o in options]


class TheLists(unittest.TestCase):
    def setUp(self):
        self.cfg = world_cfg(SIMBA)

    def test_every_list_ends_with_other_type_it_and_marks_exactly_one_current_line(self):
        for key, field in (("kylie", "voice"), ("kylie", "voice_zh"), ("gemini", "voice"), ("esther", "voice"),
                           ("simba", "voice"), ("simba", "effect"), ("codex", "voice")):
            title, opts, why = P.menu(agent(self.cfg, key), field, self.cfg)
            self.assertEqual(why, "", (key, field))
            self.assertEqual(opts[-1].token, P.OTHER, (key, field))
            self.assertIn("other, type it", opts[-1].label)
            self.assertEqual(sum(o.current for o in opts), 1, (key, field, [o for o in opts if o.current]))
            self.assertEqual(len(ids(opts)), len(set(ids(opts))), "no line twice")

    def test_english_kokoro_first_then_edge_no_jenny_current_marked(self):
        opts = P.english(agent(self.cfg, "kylie"), self.cfg)
        tokens = ids(opts)
        self.assertLess(tokens.index("af_heart"), tokens.index("en-US-AvaNeural"), "Kokoro (local) before Edge")
        self.assertNotIn("en-US-JennyNeural", tokens)
        self.assertEqual([o.token for o in opts if o.current], ["en-US-AvaNeural"])
        self.assertEqual(tokens[-2], P.SILENT)

    def test_gemini_gets_edge_voices_only_because_its_hook_speaks_edge_only(self):
        tokens = [t for t in ids(P.english(agent(self.cfg, "gemini"), self.cfg)) if not t.startswith("::")]
        self.assertTrue(tokens and all(t.endswith("Neural") for t in tokens))

    def test_esther_gets_kokoro_only_and_her_short_name_counts_as_current(self):
        opts = P.english(agent(self.cfg, "esther"), self.cfg)
        self.assertEqual([o.token for o in opts if o.current], ["af_alloy"], "'alloy' is af_alloy")
        self.assertFalse([t for t in ids(opts) if t.endswith("Neural")])

    def test_mandarin_is_taiwan_only(self):
        tokens = [t for t in ids(P.mandarin(agent(self.cfg, "kylie"))) if not t.startswith("::")]
        self.assertEqual(tokens, ["zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural"])

    def test_simba_american_kokoro_only_and_never_someone_elses_voice(self):
        tokens = [t for t in ids(P.english(agent(self.cfg, "simba"), self.cfg)) if not t.startswith("::")]
        self.assertTrue(all(t[:3] in ("af_", "am_") for t in tokens), tokens)
        self.assertNotIn("af_alloy", tokens, "Esther's")
        self.assertIn("am_fenrir", tokens, "her own stays")

    def test_simba_has_no_mandarin_list_and_only_simba_has_an_effect_list(self):
        self.assertIn("English only", P.menu(agent(self.cfg, "simba"), "voice_zh", self.cfg)[2])
        self.assertIn("no voice effect", P.menu(agent(self.cfg, "kylie"), "effect", self.cfg)[2])

    def test_simba_effect_list_shows_her_current_recipe_on_top(self):
        opts = P.effect(agent(self.cfg, "simba"))
        self.assertEqual((opts[0].token, opts[0].current), ("16 pitch=1.4", True))
        self.assertIn("23", ids(opts))
        self.assertIn("smoky", ids(opts))

    def test_a_voiceless_agent_has_silent_marked(self):
        opts = P.english(agent(self.cfg, "codex"), self.cfg)
        self.assertEqual([o.token for o in opts if o.current], [P.SILENT])

    def test_a_voice_not_on_the_list_is_shown_on_top_as_now(self):
        opts = P.english(agent(self.cfg, "jarvis"), self.cfg)            # the fixture's Jarvis has Jenny
        self.assertEqual((opts[0].token, opts[0].current), ("en-US-JennyNeural", True))
        self.assertIn("not on this list", opts[0].label)


if __name__ == "__main__":
    unittest.main()
