"""Offline tests for what Kylie found when she clicked through the Agent Control Desk on 2026-11-17 03:19:
a banner that contradicted the rows, backups shown plainly "on" with no allowance left, a highlighted row that
lost its colours, a status line that broke mid-phrase, an agent clipped at the bottom edge, a bare "?", key
hints in capitals, two hard sentences, and whether the tree's last branch can be reached with the arrow keys.
Fakes and throw-away folders only: no live settings file, no window, no sound.

    .venv/bin/python -m unittest tests.test_acd_kylie -v
"""
import asyncio
import dataclasses
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests.test_acd_v4 import make_app, show  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_health as H  # noqa: E402
from rich.text import Text  # noqa: E402
from textual.widgets import DataTable, OptionList, Static  # noqa: E402

AGY = F.AGY
NVIDIA = "nvidia/nemotron-3.5-lightning-30b-a3b"


def dead_then(w, profile: str, *later: str) -> None:
    """Yesterday's story: the sign-in was dead and a backup answered. Then whatever happened later."""
    F.log(w, profile, F.dead_token(w.now - 3602), F.signin_failed(w.now - 3601, f"{AGY}/claude-opus-5-5"),
          F.turn(w.now - 3600, "old", "claude-opus-5-5", AGY), *later)


# ---- 1. the banner says what the rows say --------------------------------------------------------------------
class BannerFollowsTheRows(unittest.TestCase):
    def test_a_sign_in_that_works_again_takes_the_agent_out_of_the_banner(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("claude-opus-5-5", AGY)])
        dead_then(w, "assistant", F.turn(w.now - 60, "new", "claude-opus-5-5", "anthropic"))
        snap = F.collect(w)
        v = F.agent(snap, "assistant")
        self.assertIn("signed in, working", acd.cell_model_set(v).plain)
        self.assertEqual(v.signin_failed, "")
        texts = [x.text for x in snap.warnings]
        self.assertFalse(any("could not sign in" in x for x in texts), texts)

    def test_an_agent_still_locked_out_stays_in_the_banner_and_the_count_matches_the_rows(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("claude-opus-5-5", AGY)])
        F.add_agent(w, "builder", backups=[("claude-opus-5-5", AGY)])
        dead_then(w, "assistant", F.turn(w.now - 60, "new", "claude-opus-5-5", "anthropic"))    # fixed
        dead_then(w, "builder")                                                                 # still dead
        snap = F.collect(w)
        rows_dead = [v.name for v in snap.agents if "sign-in dead" in acd.cell_model_set(v).plain]
        self.assertEqual(rows_dead, ["Builder"])
        texts = [x.text for x in snap.warnings]
        self.assertTrue(any("could not sign in for 1 agent" in x for x in texts), texts)

    def test_a_failure_newer_than_the_last_good_reply_still_counts(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("claude-opus-5-5", AGY)])
        F.log(w, "assistant", F.turn(w.now - 5000, "a", "claude-opus-5-5", "anthropic"),
              F.dead_token(w.now - 62), F.signin_failed(w.now - 61, f"{AGY}/claude-opus-5-5"),
              F.turn(w.now - 60, "b", "claude-opus-5-5", AGY))
        snap = F.collect(w)
        self.assertTrue(any("could not sign in for 1 agent" in x.text for x in snap.warnings))

    def test_the_pure_rule(self):
        ms = D.ModelSet("claude-opus-5-5", "anthropic", "medium")
        old = D.Turn(F.NOW - 500, "s1", "cli", 0, "claude-opus-5-5", AGY)
        old.signin_failed = "no credentials"
        good = D.Turn(F.NOW - 100, "s2", "cli", 0, "claude-opus-5-5", "anthropic")
        self.assertEqual(D.recent_signin_failure([old, good], F.NOW, ms), "")
        self.assertEqual(D.recent_signin_failure([good, old], F.NOW, ms), "no credentials")
        self.assertEqual(D.recent_signin_failure([old], F.NOW, ms), "no credentials")


# ---- 2. a backup that is on but cannot answer says so --------------------------------------------------------
class BackupsSayWhenTheyCannotAnswer(unittest.TestCase):
    def world(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("claude-opus-5-5", AGY), ("gemini-3.8-flash", AGY), (NVIDIA, "nvidia")])
        F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", AGY),
              F.quota(w.now - 59, "k1", "claude-opus-5-5", resets="86h46m59s"),
              F.fallback(w.now - 58, "k1", "claude-opus-5-5", "gemini-3.8-flash", AGY))
        return w

    def test_only_the_model_whose_allowance_is_gone_is_marked_and_it_names_the_day(self):
        w = self.world()
        v = F.agent(F.collect(w), "assistant")
        day = dt.datetime.fromtimestamp(w.now - 59 + 86 * 3600 + 46 * 60 + 59).strftime("%a %-d %b")
        first, second, third = v.backups
        self.assertEqual(D.backup_trouble(v.health, first, w.now), f"allowance used up until {day}")
        self.assertEqual(D.backup_trouble(v.health, second, w.now), "")      # same service, another model: fine
        self.assertEqual(D.backup_trouble(v.health, third, w.now), "")
        grid = acd.cell_backups(v).plain.split("\n")
        self.assertEqual(grid[0], "on  Claude Opus 5.5 via Antigravity")     # "on" stays: it is the setting
        self.assertEqual(grid[1].strip(), f"used up until {day}")
        self.assertEqual(len(grid), 4)                                       # one extra line, for that backup only
        tree = acd.tree_text(v).plain
        self.assertIn(f"(on, but allowance used up until {day}: it cannot answer until then)", tree)
        self.assertEqual(tree.count("on, but"), 1)

    def test_a_backup_on_a_service_with_a_dead_sign_in_is_marked_and_an_off_backup_never_is(self):
        dead = D.ServiceView("anthropic", H.FAILING, D.RED, "x", H.SIGNIN_DEAD, True)
        on = D.Backup(1, "claude-sonnet-5-5", "anthropic")
        self.assertEqual(D.backup_trouble((dead,), on), "sign-in dead")
        self.assertEqual(D.backup_trouble((dead,), dataclasses.replace(on, on=False)), "")
        self.assertEqual(D.backup_trouble((), on), "")


# ---- 4. the status line breaks between items only -------------------------------------------------------------
class StatusLineWraps(unittest.TestCase):
    def snap(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        lights = (("voice engine OK", True), ("Hermes gateway up", True), ("speech queue on", True),
                  ("Hermes fixes present (9/9)", True), ("watcher not running", None),
                  ("Mac output: volume 94%, MUTED, MacBook Pro Speakers", False))
        return dataclasses.replace(F.collect(w), watch_mode="reports only (automatic put-back is off)",
                                   lights=tuple((ok, label, "") for label, ok in lights))

    def test_no_item_is_ever_split_across_two_lines(self):
        snap = self.snap()
        items = ["watcher: reports only (automatic put-back is off)", "● voice engine OK", "● Hermes gateway up",
                 "● speech queue on", "● Hermes fixes present (9/9)", "● Voice Desk watcher not running",
                 "● Mac output: volume 94%, MUTED, MacBook Pro Speakers"]
        for width in (198, 170, 120, 90, 60):
            rows = acd.systems_text(snap, width).plain.split("\n")
            self.assertTrue(all(len(r) <= width for r in rows), (width, rows))
            self.assertEqual([part for r in rows for part in r.split("   ")], items, width)
        self.assertGreater(len(acd.systems_text(snap, 120).plain.split("\n")), 1)

    def test_one_line_when_it_fits_and_colours_are_kept(self):
        t = acd.systems_text(self.snap(), 400)
        self.assertNotIn("\n", t.plain)
        self.assertIn(acd.COLOUR[D.RED], {str(s.style) for s in t.spans})

    def test_flow_puts_an_item_wider_than_the_window_on_its_own_line(self):
        rows = acd.flow([Text("aaaa"), Text("b" * 30), Text("cc")], 10).plain.split("\n")
        self.assertEqual(rows, ["aaaa", "b" * 30, "cc"])


# ---- 5 and 6 and 7: words --------------------------------------------------------------------------------------
class PlainWords(unittest.TestCase):
    def test_a_place_that_is_not_known_gets_a_grey_square_and_a_reason_never_a_bare_question_mark(self):
        w = F.make_world()
        F.add_agent(w, "simba", model="x-ai/grok-4.20", service="openrouter", effort="none")
        v = F.agent(F.collect(w), "simba")
        self.assertEqual(next(p for p in v.presence if p.key == "iphone_voice").state, D.UNKNOWN)
        cell = acd.cell_presence(v)
        lines = cell.plain.split("\n")
        self.assertIn(acd.MARK + " iPhone: no shortcut", lines)
        self.assertFalse(any(ln.startswith("?") for ln in lines), lines)
        self.assertIn("no Siri shortcut for this agent was found", acd.tree_text(v).plain)   # the tree has the long reason

    def test_the_key_hints_show_the_letters_the_way_they_are_typed(self):
        for hint in (acd.GRID_KEYS, acd.TREE_KEYS):
            for word in ("u undo", "r restart review", "p pause watcher", "c read again", "q close"):
                self.assertIn(word, hint)
            for word in ("U undo", "R restart", "P pause", "C read", "Q close", "A assistants"):
                self.assertNotIn(word, hint)
        self.assertIn("a assistants' voices", acd.GRID_KEYS)

    def test_the_two_hard_sentences_are_ordinary_english_with_no_setting_names(self):
        self.assertNotIn("shown apart", H.TWIN_NOTE)
        self.assertIn("two allowances with this name", H.TWIN_NOTE)
        self.assertIn("never added together", H.TWIN_NOTE)
        plain = D.plain_why("a Hermes window decides per window in memory (voice.auto_enable); not switchable from the panel")
        self.assertNotIn("auto_enable", plain)
        self.assertNotIn("in memory", plain)
        self.assertIn("each open Hermes window keeps its own choice until it is closed", plain)
        self.assertEqual(D.plain_why("no switch here (tts.provider)"), "no switch here")
        self.assertEqual(D.plain_why("the phone decides"), "the phone decides")

    def test_the_real_tree_of_a_voice_agent_no_longer_names_the_setting(self):
        w = F.with_grid(F.edit_world())
        v = F.agent(F.edit_collect(w), "assistant")
        tree = acd.tree_text(v).plain
        self.assertNotIn("auto_enable", tree)
        self.assertIn("keeps its own choice until it is closed", " ".join(tree.split()).replace("│ ", ""))


# ---- 3, 5 and 8: the screen ------------------------------------------------------------------------------------
class TheScreen(unittest.IsolatedAsyncioTestCase):
    async def test_the_highlighted_row_keeps_its_own_colours(self):
        w = F.with_grid(F.edit_world())
        app = make_app(w)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            table = app.screen.query_one("#grid", DataTable)
            self.assertEqual(table.cursor_foreground_priority, "renderable")
            self.assertEqual(table.cursor_row, 0)
            strip = table.render_line(1)                      # the first line of the highlighted row
            colours = {seg.style.color.name for seg in strip if seg.style and seg.style.color and seg.text.strip()}
            self.assertIn(acd.COLOUR[D.GREEN], colours, "a green square must stay green under the highlight")
            self.assertGreater(len(colours), 1)               # not everything painted one colour

    async def test_agents_below_the_bottom_edge_are_named_and_scrolling_to_them_clears_the_line(self):
        w = F.with_grid(F.edit_world())
        app = make_app(w)
        async with app.run_test(size=(200, 30)) as pilot:    # a short window: five agents cannot all fit
            await pilot.pause()
            await show(app, pilot)
            await pilot.pause()
            more = app.screen.query_one("#more", Static)
            self.assertTrue(more.display)
            text = str(more.render())
            self.assertIn("more below, press down:", text)
            self.assertIn("Simba", text)
            self.assertNotIn("Jarvis", text)
            table = app.screen.query_one("#grid", DataTable)
            for _ in range(table.row_count):
                await pilot.press("down")
                await pilot.pause()
            await pilot.pause()
            self.assertEqual(table.cursor_row, table.row_count - 1)
            self.assertFalse(more.display, str(more.render()))
        async with make_app(w).run_test(size=(200, 90)) as pilot:   # a tall window: everything fits, no line
            app2 = pilot.app
            await pilot.pause()
            await show(app2, pilot)
            await pilot.pause()
            self.assertFalse(app2.screen.query_one("#more", Static).display)

    async def test_the_arrow_keys_reach_the_last_branch_of_the_tree_and_come_back_to_the_top(self):
        w = F.with_grid(F.edit_world())
        app = make_app(w)
        async with app.run_test(size=(200, 40)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            app.open_tree(1)                                  # Kylie
            await pilot.pause()
            self.assertIn("Who it may talk to", app.screen.tree_plain())
            tree = app.screen.query_one("#tree", OptionList)
            self.assertGreater(tree.max_scroll_y, 0, "the tree must be taller than this window for the test to mean anything")
            self.assertEqual(tree.scroll_y, 0)
            settings = [i for i, s in enumerate(app.screen.settings) if s]
            for _ in range(len(settings) + 2):
                if tree.highlighted == settings[-1]:
                    break
                await pilot.press("down")
                await pilot.pause()
            await pilot.pause()
            self.assertEqual(tree.highlighted, settings[-1])
            self.assertEqual(app.screen.settings[settings[-1]], "talk")     # the last setting is a talk rule
            self.assertEqual(tree.scroll_y, tree.max_scroll_y, "the very bottom of the tree is on screen")
            for _ in range(len(settings) + 2):
                if tree.highlighted == settings[0]:
                    break
                await pilot.press("up")
                await pilot.pause()
            await pilot.pause()
            self.assertEqual(tree.scroll_y, 0, "and the headings above the first setting come back")


if __name__ == "__main__":
    unittest.main()
