"""Offline tests for the last two things the old dashboard had and the table desk lacked (2026-11-17):

    here / not here     the old X key: the Voice Desk list's own mark for an agent at a place
    activation phrase   the old P key: stored and shown only, it renames nothing

Both go through the old dashboard's own writers (gridedit.set_here / set_phrase) behind the desk's armed gate,
with the desk's history and u for undo. Throw-away folders and fakes only: no live settings file, no window,
no listener, no sound.

    .venv/bin/python -m unittest tests.test_acd_two_keys -v
"""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests.test_acd_table import make_app, menu, menu_list, notice, open_cell, pick, settle, show, shown  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_parity as P  # noqa: E402
import agents as A  # noqa: E402
from textual.widgets import Input  # noqa: E402

WHEN = dt.datetime(2026, 11, 17, 11, 0, 0)


def world(armed: bool = False):
    return F.with_grid(F.edit_world(armed=armed))


def block(w, key: str) -> dict:
    return A.load(w.edit.voice.toml)["agents"][key]


def toml_text(w) -> str:
    return Path(w.edit.voice.toml).read_text(encoding="utf-8")


class TheWriters(unittest.TestCase):
    def test_a_dry_run_shows_the_line_and_writes_nothing(self):
        w = world()
        before = F.tree_hashes(w.root)
        r = P.set_here(w.edit, "kylie", "iphone", False, now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertIn("Kylie is marked not here at iPhone (Siri)", r.message)
        self.assertIn("+ here = ", "\n".join(r.lines))
        r = P.set_phrase(w.edit, "kylie", "mic", "hey Kylie", now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertIn("hey Kylie", "\n".join(r.lines))
        self.assertIn("does not rename a Siri shortcut", r.needs)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_armed_not_here_is_written_with_a_copy_and_u_puts_it_back_exactly(self):
        w = world(armed=True)
        original = toml_text(w)
        r = P.set_here(w.edit, "kylie", "iphone", False, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertNotIn("iphone", block(w, "kylie")["here"])
        self.assertIn("mic", block(w, "kylie")["here"])
        self.assertTrue(list(Path(w.edit.voice.toml_backups).glob("*-agents.toml")), "a copy was kept first")
        last = E.last_undoable(w.edit)
        self.assertEqual((last["kind"], last["did"]), ("agent", "nothere"))
        again = P.set_here(w.edit, "kylie", "iphone", False, now=WHEN)
        self.assertTrue(again.ok and "already" in again.message, again.message)
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=5), loader=F.lines_loader)
        self.assertTrue(u.ok and not u.dry, u.message)
        self.assertEqual(toml_text(w), original)
        self.assertIn("switches nothing on or off", u.needs)

    def test_armed_phrase_is_stored_and_empty_goes_back_to_todays(self):
        w = world(armed=True)
        original = toml_text(w)
        self.assertEqual(P.phrase_now(w.edit, "kylie", "mic"), ("hello Kylie", False, "hello Kylie"))
        r = P.set_phrase(w.edit, "kylie", "mic", "  hey   Kylie ", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(block(w, "kylie")["phrases"], {"mic": "hey Kylie"})
        self.assertEqual(P.phrase_now(w.edit, "kylie", "mic"), ("hey Kylie", True, "hello Kylie"))
        self.assertIn("does not rename a Siri shortcut or change the wake words", r.needs)
        r = P.set_phrase(w.edit, "kylie", "mic", "", now=WHEN + dt.timedelta(seconds=2))
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertNotIn("phrases", block(w, "kylie"))
        self.assertIn("today's (hello Kylie)", r.message)
        E.undo(w.edit, now=WHEN + dt.timedelta(seconds=4), loader=F.lines_loader)
        E.undo(w.edit, now=WHEN + dt.timedelta(seconds=6), loader=F.lines_loader)
        self.assertEqual(toml_text(w), original)

    def test_refusals_are_plain_and_write_nothing(self):
        w = world(armed=True)
        before = toml_text(w)
        for r in (P.set_here(w.edit, "nobody", "mic", False, now=WHEN),
                  P.set_here(w.edit, "kylie", "the moon", False, now=WHEN),
                  P.set_phrase(w.edit, "kylie", "mic", "x" * 81, now=WHEN),
                  P.set_phrase(w.edit, "claude", "tg_dm", "hello", now=WHEN)):       # Claude is not here there
            self.assertFalse(r.ok, r.message)
            self.assertTrue(r.message.startswith("Not changed."), r.message)
        self.assertIn("mark it here first", P.set_phrase(w.edit, "claude", "tg_dm", "hello", now=WHEN).message)
        self.assertEqual(toml_text(w), before)
        self.assertIsNone(E.last_undoable(w.edit))

    def test_without_the_voice_desk_files_nothing_is_tried(self):
        w = F.edit_world(armed=True)                       # no grid handed to the desk
        r = P.set_here(w.edit, "kylie", "mic", False, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("not known to the desk", r.message)


class TheCells(unittest.TestCase):
    def test_where_it_shows_up_cells_open_the_marks_and_the_team_room_keeps_its_own_switch(self):
        snap = F.edit_collect(world())
        kylie = F.agent(snap, "assistant")
        for row, place in (("terminal", "terminal"), ("tg_private", "tg_dm"), ("mac_voice", "mic"),
                           ("iphone_voice", "iphone")):
            self.assertEqual(acd.cell_at(f"presence:{row}", kylie).setting, f"wire:{place}", row)
        self.assertEqual(acd.cell_at("presence:team_room", kylie).setting, "team_room")
        self.assertEqual(acd.cell_at("presence:tg_group", kylie).setting, "team_room")
        self.assertIn("Activation phrase: hello Kylie (today's, none stored).",
                      acd.cell_at("presence:mac_voice", kylie).detail)

    def test_without_the_grid_the_cells_stay_facts(self):
        snap = F.edit_collect(F.edit_world())
        kylie = F.agent(snap, "assistant")
        cell = acd.cell_at("presence:mac_voice", kylie)
        self.assertEqual(cell.setting, "")
        self.assertTrue(cell.fixed)

    def test_a_stored_not_here_shows_in_the_cell_and_a_stored_phrase_in_the_strip(self):
        w = world(armed=True)
        P.set_here(w.edit, "kylie", "iphone", False, now=WHEN)
        P.set_phrase(w.edit, "kylie", "mic", "hey Kylie", now=WHEN + dt.timedelta(seconds=1))
        kylie = F.agent(F.edit_collect(w), "assistant")
        gone = acd.cell_at("presence:iphone_voice", kylie)
        self.assertEqual((gone.status, gone.words, gone.setting), (D.GREY, "not here", "wire:iphone"))
        self.assertIn("a mark the desk shows", gone.detail[0])
        self.assertIn("Activation phrase: hey Kylie (stored).", acd.cell_at("presence:mac_voice", kylie).detail)

    def test_the_assistants_call_names_cell_says_what_is_stored(self):
        w = world(armed=True)
        P.set_phrase(w.edit, "claude", "mic", "hey Claude", now=WHEN)
        P.set_here(w.edit, "claude", "iphone", False, now=WHEN + dt.timedelta(seconds=1))
        claude = next(o for o in F.edit_collect(w).others if o.key == "claude")
        cell = acd.cell_at("names", claude)
        self.assertEqual(cell.setting, "wire:*")
        story = " ".join(cell.detail)
        self.assertIn("Stored activation phrases: Mic window / phone: hey Claude.", story)
        # once one mark is stored the list names every place: the two Telegram places were never wired for Claude
        self.assertIn("Marked not here at: Telegram direct, Telegram group, iPhone (Siri).", story)
        self.assertIn("call names themselves are not changed", story)


class OnTheScreen(unittest.IsolatedAsyncioTestCase):
    async def test_a_dry_run_not_here_from_the_menu_writes_nothing(self):
        w = world()
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "presence:iphone_voice", "Kylie")
            self.assertEqual(menu(app, "title"), "Kylie: iPhone (Siri), here or not here")
            rows = menu_list(app).split("\n")
            self.assertEqual(rows[:2], ["(o) here   now", "( ) not here"])
            self.assertIn("change the phrase... (now: Talk to Kylie (today's, none stored))", rows[2])
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("DRY RUN", confirm)
            self.assertIn("Kylie is marked not here at iPhone (Siri) in the Voice Desk list.", confirm)
            self.assertIn("switches nothing on or off", confirm)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].dry)
            self.assertIn("This would happen: Kylie is marked not here at iPhone (Siri)", notice(app))
        self.assertEqual(F.tree_hashes(w.root), before, "a dry run from the screen wrote something")

    async def test_armed_not_here_shows_in_the_table_and_u_undoes(self):
        w = world(armed=True)
        original = toml_text(w)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "presence:iphone_voice", "Kylie")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("WRITTEN", menu(app, "confirm"))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved: Kylie is marked not here at iPhone (Siri)", notice(app))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(shown(app, "presence:iphone_voice", "Kylie"), f"{acd.MARK} not here")
            await open_cell(app, pilot, "presence:iphone_voice", "Kylie")
            self.assertEqual(menu_list(app).split("\n"), ["( ) here", "(o) not here   now"])   # no phrase when not here
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
        self.assertEqual(toml_text(w), original)

    async def test_change_the_phrase_asks_for_a_line_then_one_yes_and_says_it_renames_nothing(self):
        w = world(armed=True)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "presence:mac_voice", "Kylie")
            pick(app, "phrase|mic")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.AskPopup)        # straight to the line: no "written now" yet
            note = " ".join(str(app.screen.query_one("#box-note").render()).split())
            self.assertIn("Leave it empty to go back to today's: hello Kylie.", note)
            self.assertIn("does not rename a Siri shortcut or change the wake words yet", note)
            app.screen.query_one("#box-input", Input).value = "hey Kylie"
            await pilot.press("enter")
            await pilot.pause()
            words = " ".join(notice(app).split())
            self.assertIn("Store this phrase for Kylie at Mic window / phone?", words)
            self.assertIn("now hello Kylie (today's, none stored)", words)
            self.assertIn("new hey Kylie", words)
            self.assertNotIn("phrases", block(w, "kylie"))          # nothing is saved before the yes
            await pilot.press("y")
            await settle(app, pilot)
            self.assertIn("Saved: Kylie at Mic window / phone: the activation phrase changes", notice(app))
        self.assertEqual(block(w, "kylie")["phrases"], {"mic": "hey Kylie"})

    async def test_escape_at_the_line_or_at_the_question_saves_nothing(self):
        w = world(armed=True)
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "presence:mac_voice", "Kylie")
            pick(app, "phrase|mic")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await open_cell(app, pilot, "presence:mac_voice", "Kylie")
            pick(app, "phrase|mic")
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#box-input", Input).value = "hey Kylie"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await settle(app, pilot)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_claudes_call_names_cell_lists_every_place(self):
        w = world()
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "names", "Claude")
            self.assertIsInstance(app.screen, acd.CellMenu)
            rows = menu_list(app)
            self.assertIn("Terminal window: mark it not here", rows)
            self.assertIn("Telegram direct: mark it here", rows)
            self.assertIn("Mic window / phone: change the phrase... (now: hello Claude (today's, none stored))", rows)
            self.assertNotIn("Telegram direct: change the phrase", rows)      # not here there: no phrase to change
            pick(app, "here|tg_dm")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("Nothing makes it speak there yet", confirm)
            await pilot.press("escape")
            await pilot.press("escape")
            await pilot.pause()
        self.assertEqual(F.tree_hashes(w.root), before)


if __name__ == "__main__":
    unittest.main()
