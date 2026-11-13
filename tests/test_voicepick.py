"""The voice pick-list behind E / Z / F (Part 2 of the 2026-11-13 dashboard iteration; since the command-center brief,
decisions 2 and 14: the lines come from the voice bank, everyone sees the whole bank, a line the agent's own speaker
cannot make yet is shown ◌ and cannot be picked). Pure lists, nothing written. The bank is the fake one in
tests/__init__.py.

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
profile = "simba"
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


def pickable(options) -> list[str]:
    return [o.token for o in options if not o.disabled and not o.token.startswith("::")]


def not_yet(options) -> list[str]:
    return [o.token[len(P.NOT_YET):] for o in options if o.disabled]


class TheLists(unittest.TestCase):
    def setUp(self):
        self.cfg = world_cfg(SIMBA)

    def test_every_list_ends_with_other_type_it_and_marks_exactly_one_current_line(self):
        for key in ("kylie", "gemini", "esther", "simba", "codex", "jarvis", "claude"):
            for field in ("voice", "voice_zh"):
                title, opts, why = P.menu(agent(self.cfg, key), field, self.cfg)
                self.assertEqual(why, "", (key, field))
                self.assertEqual(opts[-1].token, P.OTHER, (key, field))
                self.assertIn("other, type it", opts[-1].label)
                self.assertEqual(sum(o.current for o in opts), 1, (key, field, [o for o in opts if o.current]))
                self.assertEqual(len(ids(opts)), len(set(ids(opts))), "no line twice")
                self.assertFalse([o for o in opts if o.current and o.disabled], "today's voice is never greyed out")
        title, opts, why = P.menu(agent(self.cfg, "simba"), "effect", self.cfg)
        self.assertEqual((why, opts[-1].token), ("", P.OTHER))

    def test_everyone_sees_the_whole_english_bank_local_first_then_microsoft(self):
        english_kokoro = [n for n in tests.FAKE_KOKORO if n[0] in "ab"]
        english_edge = {n for n, loc, _ in tests.FAKE_EDGE if loc.startswith("en-")}
        for key in ("kylie", "gemini", "esther", "simba", "codex", "jarvis", "claude", "wanna_buffet"):
            opts = P.english(agent(self.cfg, key), self.cfg)
            names = [t[len(P.NOT_YET):] if t.startswith(P.NOT_YET) else t for t in ids(opts)
                     if t not in (P.SILENT, P.OTHER)
                     and "not in the voice bank" not in next(o.label for o in opts if o.token == t)]
            self.assertEqual(names[:len(english_kokoro)], english_kokoro, key)
            self.assertEqual(set(names[len(english_kokoro):]), english_edge, key)
            self.assertNotIn("jf_alpha", names, "a Japanese Kokoro voice is not an English voice")
            self.assertNotIn("zf_xiaobei", names)
            self.assertEqual(ids(opts)[-2], P.SILENT)

    def test_microsoft_order_american_then_british_then_other_accents(self):
        edge = [t for t in pickable(P.english(agent(self.cfg, "kylie"), self.cfg)) if t.endswith("Neural")]
        self.assertEqual([t[:5] for t in edge], ["en-US"] * 5 + ["en-GB", "en-AU"])

    def test_jenny_is_on_the_list_now_but_nobody_gets_her_by_default(self):
        """Decision 14 (Peter 2026-11-13): everyone gets the full list. Jenny is a line, not anyone's voice."""
        opts = P.english(agent(self.cfg, "kylie"), self.cfg)
        self.assertIn("en-US-JennyNeural", pickable(opts))
        self.assertEqual([o.token for o in opts if o.current], ["en-US-AvaNeural"])

    def test_kylie_can_pick_anything_in_the_bank(self):
        self.assertFalse(not_yet(P.english(agent(self.cfg, "kylie"), self.cfg)))
        self.assertEqual(pickable(P.mandarin(agent(self.cfg, "kylie"))),
                         ["zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural"])

    def test_gemini_sees_kokoro_but_can_pick_microsoft_only_until_stage_2(self):
        opts = P.english(agent(self.cfg, "gemini"), self.cfg)
        self.assertTrue(pickable(opts) and all(t.endswith("Neural") for t in pickable(opts)))
        self.assertIn("af_heart", not_yet(opts))
        why = next(o.label for o in opts if o.token == P.NOT_YET + "af_heart")
        self.assertIn("Microsoft voices only", why)
        self.assertIn("stage 2", why)

    def test_esther_sees_microsoft_but_can_pick_kokoro_only_and_her_short_name_counts_as_current(self):
        opts = P.english(agent(self.cfg, "esther"), self.cfg)
        self.assertEqual([o.token for o in opts if o.current], ["af_alloy"], "'alloy' is af_alloy")
        self.assertFalse([t for t in pickable(opts) if t.endswith("Neural")])
        self.assertIn("en-US-AvaNeural", not_yet(opts))

    def test_mandarin_is_taiwan_only(self):
        tokens = [t for t in ids(P.mandarin(agent(self.cfg, "kylie"))) if not t.startswith("::")]
        self.assertEqual(tokens, ["zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural"])

    def test_simba_sees_the_whole_list_and_can_pick_what_her_script_makes(self):
        opts = P.english(agent(self.cfg, "simba"), self.cfg)
        self.assertTrue(all(t[:3] in ("af_", "am_") for t in pickable(opts)), pickable(opts))
        self.assertIn("af_alloy", pickable(opts), "Esther's voice is no longer hidden from her (decision 14)")
        self.assertIn("am_puck", pickable(opts), "nor Jarvis's")
        self.assertIn("bf_emma", not_yet(opts), "her script refuses British voices today")
        self.assertIn("en-US-AvaNeural", not_yet(opts))
        self.assertEqual([o.token for o in opts if o.current], ["am_fenrir"])

    def test_simba_microsoft_lines_warn_that_her_effect_would_not_apply(self):
        opts = P.english(agent(self.cfg, "simba"), self.cfg)
        self.assertTrue([o for o in opts if o.disabled and o.token.endswith("Neural")])
        for o in opts:
            if o.disabled and o.token.endswith("Neural"):
                self.assertIn("effect would not apply", o.label, o.token)
            elif o.disabled:
                self.assertNotIn("effect", o.label, o.token)

    def test_english_only_agents_see_the_mandarin_list_greyed_out(self):
        title, opts, why = P.menu(agent(self.cfg, "simba"), "voice_zh", self.cfg)
        self.assertEqual(why, "")
        self.assertIn("English only", title)
        self.assertEqual(pickable(opts), [])
        self.assertEqual(len(not_yet(opts)), 3)
        self.assertEqual([o.token for o in opts if o.current], [P.SILENT])

    def test_a_stored_mandarin_voice_of_an_english_only_agent_says_it_is_silent(self):
        cfg = world_cfg(SIMBA.replace('english_only = true', 'english_only = true\nvoice_zh = "zh-TW-HsiaoYuNeural"'))
        cur = next(o for o in P.mandarin(agent(cfg, "simba")) if o.current)
        self.assertEqual(cur.token, "zh-TW-HsiaoYuNeural")
        self.assertIn("silent today", cur.label)

    def test_only_simba_has_an_effect_list_and_it_shows_her_current_recipe_on_top(self):
        self.assertIn("no voice effect", P.menu(agent(self.cfg, "kylie"), "effect", self.cfg)[2])
        opts = P.effect(agent(self.cfg, "simba"))
        self.assertEqual((opts[0].token, opts[0].current), ("16 pitch=1.4", True))
        self.assertIn("23", ids(opts))
        self.assertIn("smoky", ids(opts))

    def test_a_voiceless_agent_has_silent_marked(self):
        opts = P.english(agent(self.cfg, "codex"), self.cfg)
        self.assertEqual([o.token for o in opts if o.current], [P.SILENT])

    def test_a_voice_not_in_the_bank_is_shown_on_top_as_now(self):
        cfg = world_cfg('\n[agents.zed]\ntarget = "zed"\ndisplay = "Zed"\nnames = ["zed"]\nvoice = "en-US-MadeUpNeural"\n')
        opts = P.english(agent(cfg, "zed"), cfg)
        self.assertEqual((opts[0].token, opts[0].current), ("en-US-MadeUpNeural", True))
        self.assertIn("not in the voice bank", opts[0].label)


class NothingChangesByItself(unittest.TestCase):
    """Decision 14: only the list widens. Building every list for every agent leaves agents.toml as it was."""

    def test_building_every_list_writes_nothing(self):
        w = F.make_world()
        w.toml.write_text(F.AGENTS_TOML + SIMBA)
        before = w.toml.read_bytes()
        cfg = agents.load(w.toml)
        for a in agents.agents(cfg):
            for field in ("voice", "voice_zh", "effect"):
                P.menu(a, field, cfg)
        self.assertEqual(w.toml.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
