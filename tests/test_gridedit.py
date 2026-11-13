"""Adding and removing agents (Part 3) and the here / not-here toggle and activation phrases (Part 4).

Only agents.toml is written, a copy is kept first, the result is read back, U undoes exactly.

    .venv/bin/python -m unittest tests.test_gridedit -v
"""
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import gridedit as GE  # noqa: E402
import gridstate as G  # noqa: E402
import voiceedit as VE  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def world_with_builder():
    w = F.make_world()
    (w.edit.profiles / "builder").mkdir()
    (w.edit.profiles / "builder" / "config.yaml").write_text("model: x\n")
    (w.edit.profiles / "default").mkdir()                       # no config.yaml: not a profile that can talk
    return w


def files_under(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class AddingAnAgent(unittest.TestCase):
    def setUp(self):
        self.w = world_with_builder()

    def test_the_pick_list_is_profiles_on_disk_not_on_the_grid(self):
        self.assertEqual(GE.addable_profiles(agents.load(self.w.toml), self.w.edit.profiles), ["builder"])

    def test_simbas_block_counts_as_her_profile(self):
        (self.w.edit.profiles / "simba").mkdir()
        (self.w.edit.profiles / "simba" / "config.yaml").write_text("model: x\n")
        self.w.toml.write_text(F.AGENTS_TOML + '\n[agents.simba]\ntarget = "simba"\ndisplay = "Simba"\nnames = ["simba"]\n')
        self.assertEqual(GE.addable_profiles(agents.load(self.w.toml), self.w.edit.profiles), ["builder"])

    def test_add_a_hermes_profile_silent_then_u_takes_it_off_byte_for_byte(self):
        before_profiles = files_under(self.w.edit.profiles)
        res = GE.add_agent(self.w.edit, "Builder", "builder, the builder", None, profile="builder")
        self.assertTrue(res.ok, res.message)
        a = next(x for x in agents.agents(agents.load(self.w.toml)) if x.key == "builder")
        self.assertEqual((a.target, a.display, a.names, a.voice), ("hermes:builder", "Builder", ("builder", "the builder"), None))
        self.assertIn("builder", [x.key for x in G.grid_agents(agents.load(self.w.toml))], "a row by itself")
        self.assertIn("silent", res.message)
        self.assertEqual(files_under(self.w.edit.profiles), before_profiles, "no profile file was touched")
        self.assertEqual(Path(res.entry["toml_backup"]).read_text(), F.AGENTS_TOML, "a copy from before")
        self.assertIn("Builder on the grid: no → added", VE.recent(self.w.edit, 1)[0])
        u = VE.undo(self.w.edit)
        self.assertTrue(u.ok, u.message)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        self.assertIn("undo: Builder on the grid: added → no", VE.recent(self.w.edit, 1)[0])

    def test_add_with_a_voice_checks_the_voice_name(self):
        bad = GE.add_agent(self.w.edit, "Builder", "", "zh-TW-HsiaoChenNeural", profile="builder")
        self.assertFalse(bad.ok)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        res = GE.add_agent(self.w.edit, "Builder", "", "en-US-BrianNeural", profile="builder")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(agents.load(self.w.toml)["agents"]["builder"]["voice"], "en-US-BrianNeural")

    def test_other_cli_agent_gets_a_target_of_its_own_and_no_chat_id(self):
        res = GE.add_agent(self.w.edit, "Aider", "aider, ada", "af_heart")
        self.assertTrue(res.ok, res.message)
        raw = agents.load(self.w.toml)["agents"]["aider"]
        self.assertEqual(raw, {"target": "aider", "display": "Aider", "names": ["aider", "ada"], "voice": "af_heart"})
        self.assertNotIn("chat", self.w.toml.read_text().split("[agents.aider]")[1])
        self.assertIn("cannot talk to it yet", res.message)

    def test_refuses_a_duplicate_a_builtin_and_an_empty_name(self):
        self.assertFalse(GE.add_agent(self.w.edit, "Kylie", "", None, profile="assistant").ok)
        self.assertFalse(GE.add_agent(self.w.edit, "Codex", "", None).ok)
        self.assertFalse(GE.add_agent(self.w.edit, "  ", "", None).ok)
        self.assertFalse(GE.add_agent(self.w.edit, "Ghost", "", None, profile="ghost").ok, "not on this Mac")
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        self.assertEqual(VE.read_history(self.w.edit), [])


class RemovingAnAgent(unittest.TestCase):
    def test_remove_takes_only_the_block_and_u_puts_it_back_exactly(self):
        w = F.make_world()
        before_profiles = files_under(w.edit.profiles)
        res = GE.remove_agent(w.edit, "kylie")
        self.assertTrue(res.ok, res.message)
        text = w.toml.read_text()
        self.assertNotIn("[agents.kylie]", text)
        self.assertNotIn("Kylie's English voice", text)
        self.assertIn("[agents.esther]", text)
        self.assertEqual(files_under(w.edit.profiles), before_profiles, "her Hermes profile is untouched")
        self.assertTrue((w.edit.profiles / "assistant" / "config.yaml").exists())
        self.assertIn("files are untouched", res.message)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_the_real_agents_toml_simbas_comments_go_with_her_and_come_back(self):
        w = F.make_world()
        real = (REPO / "agents.toml").read_text()
        w.toml.write_text(real)
        res = GE.remove_agent(w.edit, "simba")
        self.assertTrue(res.ok, res.message)
        text = w.toml.read_text()
        self.assertNotIn("girl cat", text, "the comment lines above her header are hers")
        self.assertIn('[agents.codex]\ntarget = "codex"', text)
        self.assertIn('voice_zh = "zh-TW-YunJheNeural"\n\n[agents.jarvis]', text, "one blank line between blocks")
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), real)

    def test_the_last_block_in_the_file_leaves_no_stray_blank_line(self):
        w = F.make_world()
        self.assertTrue(GE.remove_agent(w.edit, "wanna_buffet").ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML.split("\n[agents.wanna_buffet]")[0])
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_an_undo_refuses_when_the_block_was_edited_by_hand_since(self):
        w = F.make_world()
        self.assertTrue(GE.add_agent(w.edit, "Aider", "", None).ok)
        w.toml.write_text(w.toml.read_text().replace('display = "Aider"', 'display = "Aider!"'))
        u = VE.undo(w.edit)
        self.assertFalse(u.ok)
        self.assertIn("changed since", u.message)
        self.assertIn('display = "Aider!"', w.toml.read_text(), "nothing overwritten")


class TheKeys(unittest.IsolatedAsyncioTestCase):
    """A, D and Shift-T in the headless dashboard against the fake world (nothing played, nothing sent)."""

    async def test_a_adds_builder_silent_from_the_profile_list_and_u_takes_him_off(self):
        import dashboard as D
        import voicepick
        from textual.widgets import Input, OptionList
        from tests.test_dashboard_grid import load, make_app, pick, text_of, wait_for
        w = world_with_builder()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            ids = [o.id for o in app.screen.query_one(OptionList)._options]
            self.assertEqual(ids, ["builder", D.Dashboard.CLI], "profiles not on the grid, then other CLI agent")
            await pick(app, pilot, "builder")
            self.assertIsInstance(app.screen, D.AskScreen)
            self.assertEqual(app.screen.query_one(Input).value, "Builder", "the display name starts filled in")
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.screen.query_one(Input).value, "builder", "spoken names start with the name")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            await pick(app, pilot, voicepick.SILENT)
            self.assertTrue(await wait_for(lambda: VE.read_history(w.edit)))
            await pilot.pause()
            self.assertIn("Builder added to the grid (silent", text_of(app, "#msg"))
            await load(app, pilot)
            self.assertIn("builder", [r.agent.key for r in app.rows], "a row of his own")
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 2))
            self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    async def test_d_asks_first_then_removes_only_the_block_and_u_puts_it_back(self):
        import dashboard as D
        from tests.test_dashboard_grid import load, make_app, pick, text_of, wait_for
        w = F.make_world()
        before_profiles = files_under(w.edit.profiles)
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "d")                                      # Kylie
            self.assertTrue(await wait_for(lambda: isinstance(app.screen, D.ChoiceScreen)))
            await pilot.press("enter")                                          # the marked line is "keep"
            self.assertTrue(await wait_for(lambda: isinstance(app.screen, D.GridScreen)))
            self.assertEqual(w.toml.read_text(), F.AGENTS_TOML, "keep changes nothing")
            await pilot.press("d")
            self.assertTrue(await wait_for(lambda: isinstance(app.screen, D.ChoiceScreen)))
            await pick(app, pilot, "remove")
            self.assertTrue(await wait_for(lambda: VE.read_history(w.edit)))
            await pilot.pause()
            self.assertNotIn("[agents.kylie]", w.toml.read_text())
            self.assertEqual(files_under(w.edit.profiles), before_profiles)
            self.assertIn("Kylie taken off the grid", text_of(app, "#msg"))
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 2))
            self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    async def test_shift_t_tests_everyone_and_t_one_row(self):
        from unittest import mock
        from tests.test_dashboard_grid import load, make_app
        w = F.make_world()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            with mock.patch.object(app, "_run_tests") as run:
                await pilot.press("T")
                await pilot.pause()
                self.assertEqual(len(run.call_args[0][0]), len(app.rows), "Shift-T: everyone")
                await pilot.press("t")
                await pilot.pause()
                self.assertEqual(len(run.call_args[0][0]), 1, "t: the selected row")


if __name__ == "__main__":
    unittest.main()
