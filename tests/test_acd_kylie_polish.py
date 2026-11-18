"""Offline tests for the five things Kylie found when she tried the live Agent Control Desk on 2026-11-18:

  1. the yes / no line of the confirm box was cut off at the bottom of the pane: it now fits a 30-row pane;
  2. after a "Saved" box her first Enter on a cell only closed the box: the box now passes every key but Esc on
     to the table, so ONE Enter opens the chosen cell's choices;
  3. the WHERE IT SHOWS UP row "Terminal chat" was taken for the voice switch beside it: it is now "Has a terminal
     chat window", and its status line says first that it is not the voice switch and names the row that is;
  4. a Hermes agent's terminal voice read "Always" both with nothing set and with the desk's switch on: it now says
     "(default)" or "(set by the desk)";
  5. a Codex window that herdr reports without a session code is said plainly in the mount menu and the strip.

Fakes and throw-away folders only: no live settings file, no window, no herdr, no sound.

    .venv/bin/python -m unittest tests.test_acd_kylie_polish -v
"""
import dataclasses
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests import test_acd_mount as TM  # noqa: E402
from tests import test_acd_table as T  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_mount as M  # noqa: E402
from textual.containers import VerticalScroll  # noqa: E402
from textual.widgets import OptionList, Static  # noqa: E402

SMALL = (170, 30)                  # the 30-row pane the confirm box must fit


def busy_herdr(w) -> None:
    """Six open Claude windows, like Peter's herdr on 2026-11-18, plus Gemini and a Codex window with no code."""
    more = [TM.pane(f"wV:p{i}", "claude", f"{i}{i}{i}{i}{i}{i}{i}{i}-1111-4111-8111-11111111111{i}",
                    label=f"Window number {i}") for i in range(6, 10)]
    TM.open_windows(w, TM.pane("wV:p1", "claude", TM.OPEN_A, label="Agent Control Desk"),
                    TM.pane("wV:pF", "claude", TM.OPEN_B, status="working", label="Team Room Config",
                            cwd=TM.HOME + "/GitHub/Team_Room"), *more,
                    TM.pane("wV:p5", "agy", TM.GEM, label="Hello Gemini"),
                    TM.pane("wV:p4", "codex", "", label="Codex (Dolly)"))


async def calm(pilot, n: int = 5) -> None:
    """A few screen refreshes: the box is measured and placed again after its yes / no line came."""
    for _ in range(n):
        await pilot.pause()


def keys_on_screen(test, app) -> None:
    """The menu's key lines are inside the box (above its bottom border) and the box is inside the screen."""
    box = app.screen.query_one("#menu").region
    keys = app.screen.query_one("#menu-keys", Static).region
    test.assertGreater(keys.height, 0)
    test.assertLessEqual(box.bottom, app.size.height, f"the box runs off the screen: {box}")
    test.assertLessEqual(keys.bottom, box.bottom - 1, f"the key lines are under the border: keys {keys}, box {box}")
    test.assertGreaterEqual(keys.y, box.y + 1)


# ---- 1. the confirm box fits a 30-row pane ------------------------------------------------------------------------
class TheYesLineFits(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        w = getattr(self, "w", None)
        if w is not None and hasattr(w, "pins"):
            self.assertFalse((w.root / "FORBIDDEN").exists(), "the desk typed into a window")

    async def test_the_mount_menu_with_many_windows_keeps_enter_yes_on_screen(self):
        self.w = w = TM.world(armed=False)
        busy_herdr(w)
        before = F.tree_hashes(w.root)
        app = T.make_app(w)
        async with app.run_test(size=SMALL) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            await T.open_cell(app, pilot, "mount", "Claude")
            self.assertIsInstance(app.screen, acd.CellMenu)
            T.pick(app, f"mount|{TM.OPEN_A}")
            await pilot.press("enter")
            await calm(pilot)
            self.assertIn("Enter = yes", T.menu(app, "keys"))
            keys_on_screen(self, app)
            confirm = " ".join(T.menu(app, "confirm").split())
            self.assertIn(f"Voice Claude is mounted on session {TM.OPEN_A}", confirm)   # it says what was chosen
            self.assertFalse(app.screen.query_one("#menu-list", OptionList).display)    # the choices step aside
            await pilot.press("escape")                                                 # back to the choices
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertTrue(app.screen.query_one("#menu-list", OptionList).display)
            self.assertTrue(app.screen.query_one("#menu-note", Static).display)
            self.assertFalse(app.screen.query_one("#menu-confirm-box", VerticalScroll).display)
            self.assertNotIn("Enter = yes", T.menu(app, "keys"))
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TableScreen)
        self.assertEqual(F.tree_hashes(w.root), before, "a dry run wrote something")

    async def test_every_cell_menu_of_kylie_keeps_its_yes_line_on_screen(self):
        self.w = w = F.with_grid(F.edit_world())
        app = T.make_app(w)
        async with app.run_test(size=SMALL) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            screen = app.screen
            tried = 0
            for row in [r for r in screen.rows if not r.section]:
                cell = screen.cells.get((row.key, "assistant"))
                if cell is None or not cell.setting:
                    continue
                spec = app.spec_for(next(c for c in screen.cols if acd.col_id(c) == "assistant"), cell.setting)
                if spec is None or spec.locked or not any(not o.now and not o.disabled and not o.goes_on
                                                          for o in spec.options):
                    continue
                await T.open_cell(app, pilot, row.key, "Kylie")
                self.assertIsInstance(app.screen, acd.CellMenu, row.key)
                choice = next(o for o in app.screen.spec.options if not o.now and not o.disabled and not o.goes_on)
                T.pick(app, choice.token)
                await pilot.press("enter")
                await calm(pilot)
                if app.screen.confirming is not None:
                    tried += 1
                    self.assertIn("Enter = yes", T.menu(app, "keys"), row.key)
                    keys_on_screen(self, app)
                    await pilot.press("escape")
                    await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, acd.TableScreen, row.key)
            self.assertGreater(tried, 8, "too few menus were tried")

    async def test_a_long_answer_scrolls_inside_the_box_and_the_yes_line_stays(self):
        self.w = w = F.with_grid(F.edit_world())
        app = T.make_app(w)
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            assert app.screen.go("voice", 0)                    # the row's name: the same choice for every agent
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)
            T.pick(app, next(o.token for o in app.screen.spec.options if not o.now and not o.disabled))
            await pilot.press("enter")
            await calm(pilot)
            self.assertIsNotNone(app.screen.confirming)
            keys_on_screen(self, app)
            self.assertTrue(app.screen.overflow, "this answer should be longer than a 24-row box")
            self.assertIn("up / down: read the rest", T.menu(app, "keys"))
            box = app.screen.query_one("#menu-confirm-box", VerticalScroll)
            self.assertEqual(box.scroll_y, 0)
            await pilot.press("down")
            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(box.scroll_y, 2)
            keys_on_screen(self, app)
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(box.scroll_y, 1)


# ---- 2. one Enter after a "Saved" box opens the cell ----------------------------------------------------------------
class TheChosenRowStaysInSight(unittest.IsolatedAsyncioTestCase):
    async def test_walking_down_a_30_row_pane_never_hides_the_chosen_row(self):
        """The strip under the table grows to three lines on some cells; the chosen row must stay on screen."""
        w = F.with_grid(F.edit_world())
        app = T.make_app(w)
        async with app.run_test(size=SMALL) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            screen = app.screen
            table = screen.query_one("#table")
            assert screen.go("model", "Kylie")
            await pilot.pause()
            for _ in range(len(screen.rows)):
                await pilot.press("down")
                await calm(pilot, 3)
                r = table.cursor_coordinate.row
                seen = table.scrollable_content_region.height - table.header_height
                at = r - int(table.scroll_y)
                self.assertTrue(0 <= at < seen, f"row {screen.rows[r].key} is out of sight ({at} of {seen})")


class OneEnterAfterSaved(unittest.IsolatedAsyncioTestCase):
    async def save_kylie_model(self, app, pilot, down: str = "down") -> None:
        await T.open_cell(app, pilot, "model", "Kylie")
        await pilot.press(down)
        await pilot.press("enter")
        await pilot.press("enter")
        await T.settle(app, pilot)
        self.assertNotIsInstance(app.screen, (acd.TableScreen, acd.CellMenu))
        self.assertIn("Saved", T.notice(app))

    async def test_enter_on_a_saved_box_opens_the_chosen_cell_at_once(self):
        w = F.with_grid(F.edit_world(armed=True))
        app = T.make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            await self.save_kylie_model(app, pilot)
            title = "Kylie: language model"
            await pilot.press("enter")                       # ONE Enter: the box goes and the cell's menu opens
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertTrue(app.screen.spec.title.startswith(title), app.screen.spec.title)
            self.assertFalse(any(isinstance(s, acd.ResultPopup) for s in app.screen_stack), "a box was left open")

    async def test_an_arrow_closes_the_box_and_moves_and_esc_only_closes(self):
        w = F.with_grid(F.edit_world(armed=True))
        app = T.make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            await self.save_kylie_model(app, pilot)
            self.assertIn("Esc closes", T.text_of(app, "#box-keys"))
            self.assertIn("any other key closes this and goes on to the table (Enter opens the chosen cell)",
                          T.text_of(app, "#box-keys"))
            screen = next(s for s in app.screen_stack if isinstance(s, acd.TableScreen))
            r, c = screen.query_one("#table").cursor_coordinate
            await pilot.press("down")
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TableScreen)
            self.assertEqual(tuple(screen.query_one("#table").cursor_coordinate), (r + 1, c))
            assert screen.go("model", "Kylie")
            await pilot.pause()
            await self.save_kylie_model(app, pilot, down="up")
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TableScreen)
            self.assertEqual(tuple(screen.query_one("#table").cursor_coordinate), (r, c))


# ---- 3. the terminal row of WHERE IT SHOWS UP is not taken for the voice switch -------------------------------------
class NotTheVoiceSwitch(unittest.TestCase):
    def test_the_row_is_named_for_what_it_is_and_fits(self):
        snap = F.edit_collect(F.with_grid(F.edit_world()))
        labels = [r.label for r in acd.rows_of(snap)]
        self.assertIn("Has a terminal chat window", labels)
        self.assertNotIn("Terminal chat", labels)
        self.assertIn("Speaks in terminal window", labels)
        self.assertLessEqual(len("Has a terminal chat window") + 2, acd.LABEL_WIDTH)

    def test_its_status_line_says_first_it_is_not_the_voice_switch_and_names_the_real_row(self):
        w = F.with_grid(F.edit_world())
        snap = F.edit_collect(w)
        kylie = F.agent(snap, "assistant")
        row = next(r for r in acd.rows_of(snap) if r.key == "presence:terminal")
        cell = acd.cell_of(row, kylie, None, snap.room_note, snap.read_at)
        self.assertTrue(cell.detail[0].startswith(acd.NOT_SWITCH_HEAD), cell.detail)
        strip = " ".join(acd.strip_text(row, kylie, cell, 168).plain.split())
        self.assertLessEqual(len(acd.strip_text(row, kylie, cell, 168).plain.split("\n")), acd.STRIP_LINES)
        self.assertIn("This row is not the voice switch", strip)
        self.assertIn('Whether Kylie speaks there is the row "Speaks in terminal window"', strip)
        self.assertIn("Enter opens the choices", strip)

    def test_marked_not_here_it_still_says_so_first(self):
        wire = D.Wire("terminal", False, True, "", False)
        cell = acd.wired_cell("Kylie", D.GREEN, "on", [acd.NOT_THE_VOICE_SWITCH["terminal"].format(name="Kylie"),
                                                       "Terminal chat window: on: x."], wire)
        self.assertEqual(cell.words, "not here")
        self.assertTrue(cell.detail[0].startswith(acd.NOT_SWITCH_HEAD))
        self.assertIn("marked not here", cell.detail[1])

    def test_the_mark_menu_points_at_the_real_switch(self):
        w = F.with_grid(F.edit_world())
        snap = F.edit_collect(w)
        app = T.make_app(w)
        app.snapshot = snap
        kylie = F.agent(snap, "assistant")
        spec = app.spec_wire(kylie, "terminal")
        self.assertIn('Not the voice switch: whether Kylie speaks in its terminal window is the row '
                      '"Speaks in terminal window"', spec.note)
        self.assertIn('the row "Speaks in terminal window"', " ".join(spec.describe("nothere|terminal")))
        other = app.spec_wire(kylie, "tg_dm")
        self.assertNotIn("Not the voice switch", other.note)


# ---- 4. "Always" says whether it is the default or set by the desk -------------------------------------------------
class DefaultOrSetByTheDesk(unittest.TestCase):
    def terminal(self, w):
        snap = F.edit_collect(w)
        return acd.cell_at("vplace:terminal", F.agent(snap, "assistant")), F.agent(snap, "assistant")

    def test_nothing_set_reads_default_and_the_desks_switch_reads_set_by_the_desk(self):
        w = F.with_grid(F.edit_world())
        cell, _ = self.terminal(w)
        value = cell.words.split(" (")[0]
        self.assertEqual(cell.words, f"{value} (default)")
        self.assertIn("the default, since the desk's terminal voice switch is not set", cell.detail[0])
        switch = Path(w.grid.profiles) / "assistant/terminal_voice.json"
        switch.write_text(json.dumps({"voice": "on"}))
        cell, _ = self.terminal(w)
        self.assertEqual(cell.words, "Always (set by the desk)")
        self.assertIn("Always, set by the desk", cell.detail[0])
        self.assertEqual(acd.cell_text(cell, 40).plain, f"{acd.MARK} Always (set by the desk)")
        self.assertEqual(acd.cell_text(cell, 21).plain, f"{acd.MARK} Always (by desk)")      # a narrow column
        self.assertNotIn("…", acd.cell_text(cell, 21).plain)
        switch.write_text(json.dumps({"voice": "off"}))
        cell, _ = self.terminal(w)
        self.assertEqual(cell.words, "Off (set by the desk)")

    def test_the_menu_says_which_it_is_and_other_places_and_assistants_are_unchanged(self):
        w = F.with_grid(F.edit_world())
        (Path(w.grid.profiles) / "assistant/terminal_voice.json").write_text(json.dumps({"voice": "on"}))
        snap = F.edit_collect(w)
        app = T.make_app(w)
        app.snapshot = snap
        kylie = F.agent(snap, "assistant")
        spec = app.spec_place(kylie, "vplace:terminal")
        self.assertTrue(spec.note.startswith("Now: Always, set by the desk"), spec.note)
        tg = acd.cell_at("vplace:tg_dm", kylie)
        self.assertNotIn("(", tg.words)
        claude = next(o for o in snap.others if o.name == "Claude")
        self.assertNotIn("(", acd.cell_at("vplace:terminal", claude).words)
        self.assertEqual({p.set_by for p in claude.voice_places}, {""})


# ---- 5. a Codex window with no session code is said plainly --------------------------------------------------------
class AWindowWithNoCode(unittest.TestCase):
    def tearDown(self):
        self.assertFalse((self.w.root / "FORBIDDEN").exists(), "the desk typed into a window")

    def test_the_menu_and_the_strip_say_it_cannot_be_mounted(self):
        self.w = w = TM.world(pin=None)
        snap = D.collect(w.paths, yaml_loader=F.lines_loader, now=w.now, desk=E.record(w.edit))
        app = T.make_app(w)
        app.snapshot = snap
        codex = next(o for o in snap.others if o.name == "Codex")
        spec = app.spec_mount(codex)
        lines = [o.label for o in spec.options]
        self.assertIn('"Codex (Dolly)" (wV:p4): this Codex window shows herdr no session code, so voice cannot '
                      'mount it yet', lines)
        self.assertTrue(next(o for o in spec.options if o.token == "nocode|wV:p4").disabled)
        self.assertFalse(any("no Codex window" in x for x in lines))
        cell = acd.cell_at("mount", codex)
        story = " ".join(cell.detail)
        self.assertIn('herdr shows no session code for "Codex (Dolly)" (wV:p4), so voice cannot use that window: '
                      'voice Codex goes to its separate background session', story)

    def test_no_window_at_all_says_none_is_open(self):
        self.w = w = TM.world(pin=None)
        TM.open_windows(w, TM.pane("wV:p1", "claude", TM.OPEN_A, label="Agent Control Desk"))
        snap = D.collect(w.paths, yaml_loader=F.lines_loader, now=w.now, desk=E.record(w.edit))
        app = T.make_app(w)
        app.snapshot = snap
        codex = next(o for o in snap.others if o.name == "Codex")
        lines = [o.label for o in app.spec_mount(codex).options]
        self.assertIn("no Codex window is open in herdr", lines)
        self.assertNotIn("herdr shows no session code", " ".join(acd.cell_at("mount", codex).detail))
        self.assertEqual(M.no_code_words(M.Session("", "wV:p9", "idle", "", ""), "Codex"),
                         "wV:p9: this Codex window shows herdr no session code, so voice cannot mount it yet")


if __name__ == "__main__":
    unittest.main()
