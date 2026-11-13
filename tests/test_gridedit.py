"""Adding and removing agents (Part 3) and the here / not-here toggle and activation phrases (Part 4).

Only agents.toml is written, a copy is kept first, the result is read back, U undoes exactly.

    .venv/bin/python -m unittest tests.test_gridedit -v
"""
import shutil
import sys
import tomllib
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


def footer_keys(app) -> list[str]:
    """The keys the footer shows right now (Textual's own list: hidden and switched-off keys left out)."""
    return [k for k, ab in app.screen.active_bindings.items() if ab.binding.show]


class TheFooter(unittest.IsolatedAsyncioTestCase):
    """Part 5: the footer shows only the keys that apply to the selected row or cell."""

    async def test_the_footer_follows_the_row_and_the_cell(self):
        from tests.test_dashboard_grid import load, make_app
        w = F.make_world()
        w.toml.write_text(F.AGENTS_TOML + '\n[agents.simba]\ntarget = "simba"\ndisplay = "Simba"\nnames = ["simba"]\n'
                          'voice = "am_fenrir"\neffect = "fenrir16"\nenglish_only = true\n'
                          'command = ["/nonexistent/python", "/nonexistent/simba_tts.py"]\n')
        app = make_app(w)
        async with app.run_test(size=(200, 50)) as pilot:
            await load(app, pilot)
            keys = footer_keys(app)                                              # Jarvis, Agent column
            for k in ("t", "T", "c", "e", "z", "a", "d", "u", "w", "s", "question_mark", "m", "h", "q"):
                self.assertIn(k, keys)
            for k in ("v", "l", "o", "f", "x", "p"):
                self.assertNotIn(k, keys, f"{k} does not apply here")
            await pilot.press("right", "right")                                  # Jarvis, Terminal window
            await pilot.pause()
            self.assertIn("x", footer_keys(app))
            self.assertIn("p", footer_keys(app))
            await pilot.press(*["down"] * 7)                                     # Simba (8th row)
            await pilot.pause()
            self.assertEqual(app.selected().agent.key, "simba")
            keys = footer_keys(app)
            self.assertIn("f", keys, "F only on Simba's row")
            self.assertNotIn("z", keys, "Simba speaks English only")
            self.assertNotIn("p", keys, "Simba is not here at the terminal")
            self.assertIn("x", keys)

    async def test_question_mark_explains_the_colours(self):
        import dashboard as D
        from textual.widgets import Static
        from tests.test_dashboard_grid import load, make_app
        w = F.make_world()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.HelpScreen)
            text = str(app.screen.query_one("#help", Static).render())
            for word in ("green", "grey", "yellow", "red", "orange", "blank cell", "never green", "Shift-T"):
                self.assertIn(word, text)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.GridScreen)

    async def test_l_and_o_are_hidden_but_still_work(self):
        import dashboard as D
        from tests.test_dashboard_grid import load, make_app
        w = F.make_world()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "o")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.AskScreen, "O still opens its box")


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

    def test_d_then_u_is_byte_identical_for_every_block_of_the_real_agents_toml(self):
        # Review 2026-11-13: the block just before the last one (Esther) did not come back, because the last
        # block's span takes the blank line above it and the undo put her on the wrong side of that blank line.
        real = (REPO / "agents.toml").read_text()
        self.assertGreaterEqual(len(tomllib.loads(real)["agents"]), 8)
        for name, texts in (("real", real), ("fixture", F.AGENTS_TOML)):
            for key in tomllib.loads(texts)["agents"]:
                with self.subTest(key=key, file=name):
                    w = F.make_world()
                    w.toml.write_text(texts)
                    res = GE.remove_agent(w.edit, key)
                    self.assertTrue(res.ok, res.message)
                    self.assertNotIn(f"[agents.{key}]", w.toml.read_text())
                    u = VE.undo(w.edit)
                    self.assertTrue(u.ok, u.message)
                    self.assertEqual(w.toml.read_bytes(), texts.encode())

    def test_two_removals_in_a_row_come_back_with_u_u(self):
        real = (REPO / "agents.toml").read_text()
        w = F.make_world()
        w.toml.write_text(real)
        self.assertTrue(GE.remove_agent(w.edit, "esther").ok)
        self.assertTrue(GE.remove_agent(w.edit, "wanna_buffet").ok)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), real)

    def test_an_undo_whose_read_back_refuses_names_the_backup(self):
        from unittest import mock
        w = F.make_world()
        res = GE.remove_agent(w.edit, "kylie")
        self.assertTrue(res.ok, res.message)
        assert res.entry
        removed = w.toml.read_text()
        real_put = GE.put_block
        with mock.patch.object(GE, "put_block", lambda t, k, b, a=None: real_put(t, k, b and b.replace(
                "\n", "\n# hand-made\n", 1), a)):
            u = VE.undo(w.edit)
        self.assertFalse(u.ok)
        self.assertIn("did not come back as it was", u.message)
        self.assertIn(res.entry["toml_backup"], u.message)
        self.assertEqual(w.toml.read_text(), removed, "nothing written")

    def test_an_undo_refuses_when_the_block_was_edited_by_hand_since(self):
        w = F.make_world()
        self.assertTrue(GE.add_agent(w.edit, "Aider", "", None).ok)
        w.toml.write_text(w.toml.read_text().replace('display = "Aider"', 'display = "Aider!"'))
        u = VE.undo(w.edit)
        self.assertFalse(u.ok)
        self.assertIn("changed since", u.message)
        self.assertIn('display = "Aider!"', w.toml.read_text(), "nothing overwritten")


class TheWiringMatrix(unittest.TestCase):
    """Part 4: here / not here per cell (separate from the voice switch), unfinished never green, phrases."""

    def rows(self, w):
        import gridevidence as E
        return {r.agent.key: r for r in E.build(agents.load(w.toml), w.paths, E.LogReader(w.paths.profiles))}

    def test_not_here_is_a_blank_cell_and_here_but_unwired_is_orange_never_green(self):
        import gridevidence as E
        import gridview as V
        na = G.Cell(G.NA, "nothing speaks there")
        self.assertEqual(V.cell_text(na, E.NA_EVIDENCE, False, "tg_dm").plain, "")
        t = V.cell_text(na, E.NA_EVIDENCE, True, "tg_dm")
        self.assertEqual(t.plain, "here\n◌ not wired yet")
        self.assertNotIn(V.COLOUR["ok"], {str(s.style) for s in t.spans})
        ok = E.Evidence("ok", "spoke 2m ago")
        t = V.cell_text(G.Cell(G.UNKNOWN, "no Siri shortcut"), ok, True, "iphone")
        self.assertIn("not wired", t.plain, "an iPhone with no shortcut is unfinished even when the door is open")
        self.assertEqual(V.cell_text(G.Cell(G.ALWAYS), E.Evidence("ok", "spoke 2m ago"), False, "tg_group").plain, "",
                         "not here hides even a working cell")

    def test_by_default_an_agent_is_here_where_it_is_wired(self):
        w = F.make_world()
        r = self.rows(w)
        self.assertFalse(r["codex"].is_here("tg_dm"), "Codex has no Telegram bot")
        self.assertTrue(r["codex"].is_here("mic"))
        self.assertTrue(r["kylie"].is_here("tg_dm"))
        self.assertEqual(r["kylie"].phrase("mic"), ("hello Kylie", False))

    def test_x_puts_codex_here_on_telegram_unfinished_and_u_takes_it_back(self):
        w = F.make_world()
        cells = self.rows(w)["codex"].cells
        res = GE.set_here(w.edit, "codex", "tg_dm", True, cells)
        self.assertTrue(res.ok, res.message)
        self.assertIn("not wired there yet", res.message)
        r = self.rows(w)["codex"]
        self.assertTrue(r.is_here("tg_dm"))
        self.assertTrue(G.unwired(r.cells["tg_dm"], "tg_dm"))
        self.assertIn("Codex at Telegram direct: not here → here", VE.recent(w.edit, 1)[0])
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_x_takes_kylie_off_a_place_without_touching_her_voice_switch(self):
        w = F.make_world()
        before = files_under(w.root)
        r = self.rows(w)["kylie"]
        res = GE.set_here(w.edit, "kylie", "tg_dm", False, r.cells)
        self.assertTrue(res.ok, res.message)
        self.assertIn("voice switch there is unchanged", res.message)
        after = files_under(w.root)
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        self.assertTrue(all(k.endswith("agents.toml") or "voice_history" in k for k in changed), changed)
        raw = agents.load(w.toml)["agents"]["kylie"]["here"]
        self.assertNotIn("tg_dm", raw)
        self.assertIn("mic", raw)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_p_stores_phrases_per_place_empty_goes_back_to_default_and_u_undoes_each(self):
        w = F.make_world()
        self.assertTrue(GE.set_phrase(w.edit, "jarvis", "mic", "  hello   Jarvis  ").ok)
        self.assertTrue(GE.set_phrase(w.edit, "jarvis", "iphone", "Talk to Jarvis now").ok)
        self.assertEqual(agents.load(w.toml)["agents"]["jarvis"]["phrases"],
                         {"mic": "hello Jarvis", "iphone": "Talk to Jarvis now"})
        self.assertIn("Jarvis phrase at iPhone (Siri): “default (Talk to Jarvis)” → “Talk to Jarvis now”",
                      VE.recent(w.edit, 1)[0])
        self.assertTrue(GE.set_phrase(w.edit, "jarvis", "mic", "").ok)
        self.assertEqual(agents.load(w.toml)["agents"]["jarvis"]["phrases"], {"iphone": "Talk to Jarvis now"})
        for _ in range(3):
            self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_the_flags_survive_a_restart_because_they_live_in_agents_toml(self):
        w = F.make_world()
        GE.set_here(w.edit, "codex", "iphone", False, self.rows(w)["codex"].cells)
        GE.set_phrase(w.edit, "codex", "mic", "hello Codex")
        a = next(x for x in agents.agents(agents.load(w.toml)) if x.key == "codex")
        self.assertEqual(G.phrase_map(a), {"mic": "hello Codex"})
        self.assertFalse(G.here_map(a, self.rows(w)["codex"].cells)["iphone"])


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

    async def test_x_and_p_on_a_cell_then_u_u(self):
        import dashboard as D
        from textual.widgets import DataTable, Input
        from tests.test_dashboard_grid import load, make_app, text_of, wait_for
        w = F.make_world()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press(*["down"] * 6, "right", "right", "right")          # Codex, Telegram direct
            self.assertEqual((app.selected().agent.key, app.selected_place()), ("codex", "tg_dm"))
            table = app.screen.query_one(DataTable)
            self.assertEqual(table.get_cell_at(table.cursor_coordinate).plain, "", "not here: a blank cell")
            self.assertNotIn("p", footer_keys(app), "no phrase on a cell where the agent is not")
            await pilot.press("p")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, D.AskScreen)
            await pilot.press("x")
            self.assertTrue(await wait_for(lambda: VE.read_history(w.edit)))
            await load(app, pilot)
            table = app.screen.query_one(DataTable)
            self.assertEqual(table.get_cell_at(table.cursor_coordinate).plain, "here\n◌ not wired yet")
            await pilot.press("right", "right", "p")                             # Codex, mic
            await pilot.pause()
            self.assertIsInstance(app.screen, D.AskScreen)
            app.screen.query_one(Input).value = "hello Codex"
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 2))
            await load(app, pilot)
            self.assertIn("wakes with “hello Codex”", text_of(app, "#detail"))
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 3))
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 4))
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
