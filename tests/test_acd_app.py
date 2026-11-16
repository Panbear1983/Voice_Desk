"""Offline test of the Agent Control Desk's screen: it is drawn headless (no window on the screen) against the
fake world from acdfix, with the background reading switched off so the test decides when to read.

    .venv/bin/python -m unittest tests.test_acd_app -v
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_snapshot  # noqa: E402
import voiceedit  # noqa: E402
from textual.widgets import DataTable, OptionList, Static  # noqa: E402

AGY = F.AGY


def world():
    w = F.make_world()
    F.add_agent(w, "orchestrator", model="claude-sonnet-5-5",
                backups=[("claude-sonnet-5-5", AGY), ("gemini-3.8-flash", AGY)])
    F.add_agent(w, "assistant", backups=[("gemini-3.8-flash", AGY), ("nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia")])
    F.add_agent(w, "builder", effort="high", addons=())
    F.add_agent(w, "simba", model="x-ai/grok-4.20", service="openrouter", effort="none")
    F.log(w, "orchestrator", F.turn(w.now - 90, "j1", "claude-sonnet-5-5", "anthropic", "cli"),
          F.failed(w.now - 80, "j1", "claude-sonnet-5-5", "anthropic", "Individual quota reached. Resets in 82h1m."),
          F.fallback(w.now - 80, "j1", "claude-sonnet-5-5", "gemini-3.8-flash", AGY))
    F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", "anthropic", "telegram"))
    F.log(w, "simba", F.turn(w.now - 60, "s1", "x-ai/grok-4.20", "openrouter", "bot_room"))
    return w


def edit_paths(w, launcher: bool = True) -> E.EditPaths:
    """Where a change would go for the read-only (JSON) world: only into its own throw-away folder."""
    root = w.root
    voice = voiceedit.EditPaths(toml=w.paths.agents_toml, history=root / "vh.jsonl", toml_backups=root / "vh-backups",
                                hermes_backups=root / "hermes-backups", profiles=w.paths.profiles,
                                claude_conf=root / "no-claude.conf", gemini_conf=None)
    return E.EditPaths(state=root / "acd-state", profiles=w.paths.profiles, rules=w.paths.rules,
                       restart_request=root / "helper" / "gateway_restart.request",
                       config_backups=root / "config-backups", voice=voice, launcher_armed=launcher,
                       members=w.paths.members, team_room_cmd=root / "no-such-team-room-command")


def make_app(w):
    return acd.Desk(collector=lambda: F.collect(w), autostart=False, edit=edit_paths(w), loader=F.loader, tester=None)


def text_of(app, selector: str) -> str:
    if selector == "#tree":
        return app.screen.tree_plain()
    return str(app.screen.query_one(selector, Static).render())


def grid_text(app) -> str:
    table = app.screen.query_one("#grid", DataTable)
    return "\n".join(str(table.get_cell_at((r, c))) for r in range(table.row_count) for c in range(len(table.columns)))


class DeskScreen(unittest.IsolatedAsyncioTestCase):
    async def test_grid_then_tree_then_back(self):
        w = world()
        app = make_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.GridScreen)
            self.assertIn("reading every agent", text_of(app, "#warnings"))     # nothing read yet, and it says so
            app.apply_snapshot(await asyncio.to_thread(app.collector))
            await pilot.pause()

            self.assertIn(acd.DRY_LABEL, text_of(app, "#title"))
            legend = text_of(app, "#legend")
            for words in ("live everywhere", "waiting on a restart or a fresh chat", "temporary override",
                          "changed behind the desk's back"):
                self.assertIn(words, legend)
            self.assertIn("1 agent had the main model fail", text_of(app, "#warnings"))

            table = app.screen.query_one("#grid", DataTable)
            self.assertEqual(table.row_count, 4)                                 # one row per agent
            self.assertEqual([str(c.label) for c in table.columns.values()], [label for label, _ in acd.COLUMNS])
            grid = grid_text(app)
            for words in ("Jarvis", "Kylie", "Builder", "Simba", "Claude Sonnet 5.5", "Gemini 3.8 Flash",
                          "the main model failed", "Grok 4.20 (locked)", "no voice", "NVIDIA Nemotron 3.5 Lightning",
                          "Puck, local", "job cards only"):
                self.assertIn(words, grid)
            self.assertIn("Claude", text_of(app, "#others"))                     # the call-by-name section
            self.assertIn("clod", text_of(app, "#others"))

            await pilot.press("down")                                            # Jarvis -> Kylie
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TreeScreen)
            tree = text_of(app, "#tree")
            for words in ("KYLIE", "Language model", "Set to", "Claude Opus 5.5", "Backups, in order", "Voice",
                          "Where it shows up", "Telegram private chat", "Who it may talk to", "Takes work from",
                          "answered by the model that is set"):
                self.assertIn(words, tree)
            self.assertIn(acd.DRY_LABEL, text_of(app, "#title"))
            self.assertIn("> Kylie", text_of(app, "#agents"))

            await pilot.press("left")                                            # the tree moves to Jarvis
            await pilot.pause()
            tree = text_of(app, "#tree")
            self.assertIn("JARVIS", tree)
            self.assertIn("resets in about 82 hours", tree)
            self.assertIn("Gemini 3.8 Flash answered", tree)
            self.assertIn("Hands out work", tree)

            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.GridScreen)
            self.assertEqual(app.screen.query_one("#grid", DataTable).row_count, 4)

    async def test_a_broken_settings_file_shows_an_error_row_and_the_desk_stays_up(self):
        w = world()
        F.add_agent(w, "assistant", raw="{ broken")
        app = make_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            app.apply_snapshot(app.collector())
            await pilot.pause()
            self.assertIn("cannot be read", grid_text(app))
            self.assertIn("settings file could not be read for: Kylie", text_of(app, "#warnings"))
            app.open_tree(1)
            await pilot.pause()
            self.assertIn("could not be read", text_of(app, "#tree"))

    async def test_a_failed_read_keeps_the_last_good_picture(self):
        w = world()
        app = make_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            app.apply_snapshot(app.collector())
            await pilot.pause()

            def boom():
                raise RuntimeError("disk gone")
            app.collector = boom
            app.action_reread()
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIn("this read failed", text_of(app, "#warnings"))
            self.assertEqual(app.screen.query_one("#grid", DataTable).row_count, 4)

    async def test_the_keys(self):
        app = make_app(world())
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            keys = {b.key for b in app.BINDINGS} | {b.key for b in acd.GridScreen.BINDINGS} | \
                   {b.key for b in acd.TreeScreen.BINDINGS}
            # version four adds P (pause the watcher) and, on the grid, A (the assistants' voices)
            self.assertEqual(keys, {"q", "c", "escape", "left", "right", "space", "u", "r", "p", "a"})

    async def test_a_narrow_window_drops_columns_instead_of_scrolling_sideways(self):
        self.assertEqual(len(acd.columns_for(190)), 7)
        self.assertEqual([c[0] for c in acd.columns_for(130)],
                         ["Agent", "Model set", "Model that answered last", "Backups", "Voice"])
        self.assertEqual([c[0] for c in acd.columns_for(80)], ["Agent", "Model set", "Model that answered last"])
        for width in (150, 128, 100, 84):
            app = make_app(world())
            async with app.run_test(size=(width, 50)) as pilot:
                await pilot.pause()
                app.apply_snapshot(app.collector())
                await pilot.pause()
                table = app.screen.query_one("#grid", DataTable)
                self.assertLess(len(table.columns), 7)
                self.assertGreaterEqual(len(table.columns), 3)
                self.assertLessEqual(table.virtual_size.width, width, f"the grid scrolls sideways at {width} columns")
                self.assertIn("columns hidden", text_of(app, "#keys"))
        app = make_app(world())
        async with app.run_test(size=(200, 50)) as pilot:                      # wide enough: everything is shown
            await pilot.pause()
            app.apply_snapshot(app.collector())
            await pilot.pause()
            self.assertEqual(len(app.screen.query_one("#grid", DataTable).columns), 7)

    async def test_headless_snapshot_writes_two_pictures(self):
        w = world()
        out = Path(tempfile.mkdtemp(prefix="acd-shot-"))
        grid, tree = await acd_snapshot.render(out, collector=lambda: F.collect(w), agent=0, size=(180, 58))
        self.assertTrue(grid.read_text(encoding="utf-8").lstrip().startswith("<svg"))
        self.assertIn("Language", tree.read_text(encoding="utf-8"))


class NoWrites(unittest.TestCase):
    def test_the_desk_modules_never_open_a_file_for_writing_or_start_a_process(self):
        root = Path(acd.__file__).resolve().parent
        # the screen and the facts never write; every change lives in acd_edit.py, which never starts a process
        # or restarts anything itself (it only leaves a request file for the restart helper)
        for name in ("acd.py", "acd_data.py"):
            src = (root / name).read_text(encoding="utf-8")
            for forbidden in ("write_text", "write_bytes", "subprocess", "os.system", "os.remove", "unlink(",
                              "shutil", "launchctl", "requests", "urllib", "socket"):
                self.assertNotIn(forbidden, src, f"{name} must stay read-only: found {forbidden!r}")
        src = (root / "acd_edit.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "launchctl", "gateway restart", "requests", "urllib", "socket",
                          "kill("):
            self.assertNotIn(forbidden, src, f"acd_edit.py must never run or restart anything: found {forbidden!r}")


# ---- version two: the pop-ups, against a world whose settings files are real YAML text ----------------------
def edit_app(w, tester=None):
    return acd.Desk(collector=lambda: F.edit_collect(w), autostart=False, edit=w.edit, loader=F.lines_loader,
                    tester=tester)


def box(app, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).render())


def list_text(app) -> str:
    lst = app.screen.query_one("#box-list", OptionList)
    return "\n".join(str(lst.get_option_at_index(i).prompt) for i in range(lst.option_count))


async def open_setting(app, pilot, agent_index: int, setting: str):
    """Open one agent's tree, move the highlight to a setting and press Enter."""
    app.apply_snapshot(app.collector())
    await pilot.pause()
    app.open_tree(agent_index)
    await pilot.pause()
    screen = app.screen
    tree = screen.query_one("#tree", OptionList)
    tree.highlighted = screen.settings.index(setting)
    await pilot.press("enter")
    await pilot.pause()


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


class Popups(unittest.IsolatedAsyncioTestCase):
    async def test_model_popup_choose_save_confirm_in_dry_run_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = edit_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 1, "model")                          # Kylie
            self.assertIsInstance(app.screen, acd.EditPopup)
            self.assertIn("Kylie: language model", box(app, "#box-title"))
            listed = list_text(app)
            for words in ("Model, on the Claude subscription", "Claude Opus 5.5   now", "Claude Sonnet 5.5",
                          "Thinking effort", "medium effort   now", "high effort"):
                self.assertIn(words, listed)
            await pilot.press("s")                                              # nothing chosen yet
            self.assertIn("nothing to save", box(app, "#box-status"))
            await pilot.press("down")
            await pilot.press("enter")                                          # choose Sonnet
            await pilot.pause()
            self.assertIn("Claude Sonnet 5.5   <- chosen", list_text(app))
            await pilot.press("s")
            await pilot.pause()
            confirm = box(app, "#box-confirm")
            self.assertIn("DRY RUN", confirm)
            self.assertIn("Kylie's model changes from Claude Opus 5.5 to Claude Sonnet 5.5.", confirm)
            self.assertIn("Enter = yes", box(app, "#box-keys"))
            await pilot.press("escape")                                         # back to the choices, not closed
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.EditPopup)
            self.assertEqual(box(app, "#box-confirm"), "")
            await pilot.press("s")
            await pilot.press("enter")                                          # yes
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.NoticePopup)
            self.assertIn("Dry run: nothing was written", box(app, "#box-title"))
            body = box(app, "#box-text")
            self.assertIn("This would happen: Kylie's model changes", body)
            self.assertIn("+ default: claude-sonnet-5-5", body)
            self.assertIn("open terminal chats keep the old model until a fresh chat is started", body.replace("\n    ", " "))
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TreeScreen)
        self.assertEqual(F.tree_hashes(w.root), before, "a dry run from the screen wrote something")

    async def test_cancel_changes_nothing(self):
        w = F.edit_world(armed=True)
        before = F.tree_hashes(w.root)
        app = edit_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 1, "model")
            await pilot.press("down")
            await pilot.press("space")
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.TreeScreen)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_armed_save_from_the_screen_writes_and_u_undoes(self):
        w = F.edit_world(armed=True)
        app = edit_app(w)
        cfg = w.profiles / "assistant" / "config.yaml"
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            self.assertIn(acd.ARMED_LABEL, str(app.screen.query_one("#title", Static).render()))
            await open_setting(app, pilot, 1, "model")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("WRITTEN now", box(app, "#box-confirm"))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved", box(app, "#box-title"))
            self.assertEqual(E.managed(cfg.read_text())["model"]["default"], "claude-sonnet-5-5")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("saved, not live everywhere", str(app.screen_stack[1].query_one("#grid", DataTable).get_cell_at((1, 1))))
            self.assertIn("Not live yet", app.screen.tree_plain())
            await pilot.press("u")
            await pilot.pause()
            self.assertIn("Undo the newest change?", box(app, "#box-title"))
            self.assertIn("Kylie's model changes from Claude Opus 5.5 to Claude Sonnet 5.5", box(app, "#box-text"))
            await pilot.press("y")
            await settle(app, pilot)
            self.assertIn("Undone:", box(app, "#box-text"))
            self.assertEqual(E.managed(cfg.read_text())["model"]["default"], "claude-opus-5-5")

    async def test_backups_popup_space_toggles_and_shows_the_off_ones(self):
        w = F.edit_world()
        cfg = w.profiles / "orchestrator" / "config.yaml"
        cfg.write_text(E.remove_backup(cfg.read_text(), "nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "2026-11-13")[0])
        before = F.tree_hashes(w.root)
        app = edit_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 0, "backups")                        # Jarvis
            self.assertIn("Jarvis: backups", box(app, "#box-title"))
            listed = list_text(app)
            self.assertIn("[x] 1  Claude Opus 5.5 through Antigravity   on now", listed)
            self.assertIn("[ ] -  NVIDIA Nemotron 3.5 Lightning   off now   (switched off 13 Nov)", listed)
            await pilot.press("space")                                          # the first backup: off
            await pilot.press("down", "down")
            await pilot.press("space")                                          # NVIDIA: on
            await pilot.pause()
            listed = list_text(app)
            self.assertIn("on now   -> off", listed)
            self.assertIn("off now   -> on", listed)
            await pilot.press("s")
            await pilot.pause()
            confirm = box(app, "#box-confirm")
            self.assertIn("Jarvis's backup Claude Opus 5.5 through Antigravity is switched off", confirm)
            self.assertIn("Jarvis's backup NVIDIA Nemotron 3.5 Lightning is switched on.", confirm)
            await pilot.press("enter")
            await settle(app, pilot)
            body = box(app, "#box-text")
            self.assertIn("- - provider: antigravity-subscription-directsdk", body)
            self.assertIn("+ - provider: nvidia", body)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_voice_popup_tests_a_voice_without_playing_it_and_saves_in_dry_run(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        asked = []

        def fake_tester(agent, lang, voice):
            asked.append((agent.key, lang, voice))
            return True, "1.4 seconds of real sound was made (mp3); nothing was played"
        app = edit_app(w, tester=fake_tester)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 1, "voice")                          # Kylie's English voice
            self.assertIn("Kylie: voice", box(app, "#box-title"))
            self.assertIn("English voice", list_text(app))
            self.assertIn("T test the highlighted voice", box(app, "#box-keys"))
            lst = app.screen.query_one("#box-list", OptionList)
            rows = app.screen.rows
            aria = next(i for i, (_, o) in enumerate(rows) if o and o.token == "en-US-AriaNeural")
            lst.highlighted = aria
            await pilot.press("t")
            await settle(app, pilot)
            self.assertEqual(asked, [("kylie", "en", "en-US-AriaNeural")])
            self.assertIn("Test passed", box(app, "#box-status"))
            await pilot.press("enter")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("Kylie's English voice changes from Ava to Aria.", box(app, "#box-confirm"))
            await pilot.press("enter")
            await settle(app, pilot)
            body = box(app, "#box-text")
            self.assertIn("Dry run", box(app, "#box-title"))
            self.assertIn("in the Voice Desk list:", body)
            self.assertIn("en-US-AriaNeural", body)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_speed_popup_offers_presets_only(self):
        w = F.edit_world()
        app = edit_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 1, "speed")
            listed = list_text(app)
            for words in ("slower (0.9)", "normal (1)   now", "a bit faster (1.1)", "fast (1.2)"):
                self.assertIn(words, listed)
            self.assertEqual(len(app.screen.query("Input")), 0, "there is no box to type a number into")
            await pilot.press("down", "down")
            await pilot.press("space")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("Kylie's speaking speed changes from normal to fast.", box(app, "#box-confirm"))

    async def test_simba_is_locked_and_esther_carries_her_note(self):
        w = F.edit_world()
        app = edit_app(w)
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 4, "model")                          # Simba
            self.assertIn("Simba's model is locked", box(app, "#box-note"))
            self.assertFalse(app.screen.query_one("#box-list", OptionList).display)
            await pilot.press("s")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.TreeScreen)
            await pilot.press("escape")
            await pilot.pause()
            await open_setting(app, pilot, 2, "model")                          # Esther
            self.assertIn("meant to stay fixed on Claude Opus 5.5", box(app, "#box-note"))
            self.assertIn("Claude Sonnet 5.5", list_text(app))

    async def test_restart_review_only_leaves_a_request_for_the_helper(self):
        for armed in (False, True):
            w = F.edit_world(armed=armed)
            E._append_history(w.edit, {"id": "a", "ts": w.now - 100, "kind": "addon", "ok": True, "needs_restart": True,
                                       "what": "the DLS add-on was switched off inside Jarvis"}) if armed else None
            before = F.tree_hashes(w.root)
            app = edit_app(w)
            async with app.run_test(size=(180, 58)) as pilot:
                await pilot.pause()
                app.apply_snapshot(app.collector())
                await pilot.pause()
                await pilot.press("r")
                await pilot.pause()
                self.assertIn("Restart review", box(app, "#box-title"))
                body = box(app, "#box-text")
                self.assertIn("waits for a quiet moment", body.replace("\n", " "))
                if armed:
                    self.assertIn("1 saved change is waiting for a restart", body)
                    self.assertIn("the DLS add-on was switched off inside Jarvis", body)
                else:
                    self.assertIn("No saved change is waiting for a restart", body)
                    self.assertIn("dry run", body)
                await pilot.press("y")
                await settle(app, pilot)
                if armed:
                    self.assertTrue(w.edit.restart_request.exists())
                    self.assertIn("Restart requested", box(app, "#box-text"))
                else:
                    self.assertIn("DRY RUN, nothing written", box(app, "#box-text"))
            if not armed:
                self.assertEqual(F.tree_hashes(w.root), before)
            else:
                written = set(F.tree_hashes(w.root)) - set(before)
                self.assertEqual(written, {"helper/gateway_restart.request"}, "only the helper's request file is new")

    async def test_undo_with_nothing_to_undo_says_so(self):
        app = edit_app(F.edit_world())
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            self.assertIn("no change from the desk to undo", box(app, "#box-text"))


if __name__ == "__main__":
    unittest.main()
