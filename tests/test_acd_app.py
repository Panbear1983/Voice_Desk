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
import acd_snapshot  # noqa: E402
from textual.widgets import DataTable, Static  # noqa: E402

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


def make_app(w):
    return acd.Desk(collector=lambda: F.collect(w), autostart=False)


def text_of(app, selector: str) -> str:
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

            self.assertIn(acd.READ_ONLY_LABEL, text_of(app, "#title"))
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
            self.assertIn(acd.READ_ONLY_LABEL, text_of(app, "#title"))
            self.assertIn("> Kylie", text_of(app, "#agents"))

            await pilot.press("up")                                              # the tree moves to Jarvis
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

    async def test_version_one_has_no_way_to_change_or_restart_anything(self):
        app = make_app(world())
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            keys = {b.key for b in app.BINDINGS} | {b.key for b in acd.GridScreen.BINDINGS} | \
                   {b.key for b in acd.TreeScreen.BINDINGS}
            self.assertEqual(keys, {"q", "c", "escape", "up", "down"})

    async def test_headless_snapshot_writes_two_pictures(self):
        w = world()
        out = Path(tempfile.mkdtemp(prefix="acd-shot-"))
        grid, tree = await acd_snapshot.render(out, collector=lambda: F.collect(w), agent=0, size=(180, 58))
        self.assertTrue(grid.read_text(encoding="utf-8").lstrip().startswith("<svg"))
        self.assertIn("Language", tree.read_text(encoding="utf-8"))


class NoWrites(unittest.TestCase):
    def test_the_desk_modules_never_open_a_file_for_writing_or_start_a_process(self):
        root = Path(acd.__file__).resolve().parent
        for name in ("acd.py", "acd_data.py"):
            src = (root / name).read_text(encoding="utf-8")
            for forbidden in ("write_text", "write_bytes", "subprocess", "os.system", "os.remove", "unlink(",
                              "shutil", "launchctl", "requests", "urllib", "socket"):
                self.assertNotIn(forbidden, src, f"{name} must stay read-only: found {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
