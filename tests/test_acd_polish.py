"""Offline tests of the polish after Kylie's fourth look at the table (2026-11-17, 09:37): the two Antigravity
rungs are 2a and 2b; cells fit their column without an ellipsis at the width of Peter's pane; whole columns only
beside the pinned row names; one obvious cursor, the same on a blank cell; the strip says each fact once in at most
three lines; menus stop at their ends; the standard-ladder box fits, names who is left out, and has the keep / drop
choice inside it; the legend says what red means; and the confirm line says what Escape does.

Fakes and throw-away folders only: no live settings file, no Team Room, no Telegram, no window, no sound.

    .venv/bin/python -m unittest tests.test_acd_polish -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests import test_acd_table as T  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
from textual.containers import VerticalScroll  # noqa: E402
from textual.widgets import DataTable, OptionList, Static  # noqa: E402

AGY = F.AGY
PANE = (170, 53)                 # the size of the pane the desk lives in (Monitors > Ops)
SONNET = ("anthropic", "claude-sonnet-5-5")


def busy_world():
    """A world with something in every kind of cell: replies in the log, a used-up rung, an extra backup."""
    w = F.with_grid(F.edit_world())
    T.set_list(w, "assistant", [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), SONNET, ("nvidia", T.NV)])
    T.set_list(w, "wanna-buffet", [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), ("nvidia", T.ULTRA), T.CODEX])
    F.log(w, "orchestrator", F.turn(w.now - 300, "j1", "claude-sonnet-5-5", "anthropic"))
    F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", AGY),
          F.quota(w.now - 59, "k1", "claude-opus-5-5", resets="86h46m59s"),
          F.fallback(w.now - 58, "k1", "claude-opus-5-5", "gemini-3.8-flash", AGY))
    F.log(w, "wanna-buffet", F.turn(w.now - 3 * 86400, "w1", "claude-opus-5-5", "anthropic", "cron"))
    return w


class Words(unittest.TestCase):
    def test_the_two_antigravity_rungs_are_2a_and_2b(self):
        self.assertEqual(D.RUNG_LABEL["ag_claude"], "2a Antigravity: Claude")
        self.assertEqual(D.RUNG_LABEL["ag_gemini"], "2b Antigravity: Gemini")
        labels = [r.label for r in acd.rows_of(F.edit_collect(F.edit_world())) if r.key.startswith("rung:")]
        self.assertEqual([x.split()[0] for x in labels], ["1", "2a", "2b", "3", "4"])     # no number said twice
        self.assertEqual(len(set(x.split()[0] for x in labels)), len(labels))
        self.assertIn("2a", E.RUNG_NAME["ag_claude"])
        self.assertIn("2b", E.RUNG_NAME["ag_gemini"])
        self.assertEqual((D.RUNG_WORDS["ag_claude"], D.RUNG_WORDS["ag_gemini"]), ("rung 2a", "rung 2b"))

    def test_models_have_a_short_name_for_a_cell_and_the_full_name_stays_for_the_strip(self):
        for model, short, family in (("claude-opus-5-5", "Opus 5.5", "Opus"), ("claude-sonnet-5-5", "Sonnet 5.5", "Sonnet"),
                                     ("gemini-3.8-flash", "Gemini Flash", "Gemini"), ("gemini-4-argon", "Gemini 4 Argon", "Gemini"),
                                     (T.NV, "Nemotron 3.5", "Nemotron"), (T.ULTRA, "Nemotron 3 Ultra", "Nemotron"),
                                     ("gpt-5.4-mini", "GPT 5.4 mini", "GPT"), ("x-ai/grok-4.20", "Grok 4.20", "Grok")):
            self.assertEqual((D.short_model(model), D.model_family(model)), (short, family), model)
        self.assertEqual(D.model_name("claude-opus-5-5"), "Claude Opus 5.5")              # the full name is untouched

    def test_no_cell_is_cut_at_the_width_of_the_pane(self):
        snap = F.edit_collect(busy_world())
        width = acd.column_width(snap, PANE[0])
        cut = []
        for row in acd.rows_of(snap):
            for col in snap.agents:
                cell = acd.cell_of(row, col, None, snap.room_note, snap.read_at)
                if "…" in acd.cell_text(cell, width).plain:
                    cut.append((row.label, col.name, cell.words))
        self.assertEqual(cut, [], f"cells cut with an ellipsis at {PANE[0]} columns (column width {width})")
        for r in acd.rows_of(snap):                                              # no row name is cut either
            self.assertLessEqual(len(r.label) + (0 if r.section else 2), acd.LABEL_WIDTH, r.label)

    def test_the_short_words_of_the_cells(self):
        w = busy_world()
        snap = F.edit_collect(w)
        kylie, jarvis, buffet = (F.agent(snap, p) for p in ("assistant", "orchestrator", "wanna-buffet"))
        at = lambda key, v: acd.cell_of(acd.Row(key, key), v, None, "", snap.read_at)     # noqa: E731
        self.assertEqual(at("signin", jarvis).words, "Claude sub.")          # a green light beside it IS "signed in"
        self.assertRegex(at("last", jarvis).words, r"^Sonnet 5\.5, \d\d:\d\d$")           # today: the clock time
        self.assertRegex(at("last", buffet).words, r"^Opus 5\.5, \d{1,2} [A-Z][a-z]{2}$")  # older: the day
        self.assertRegex(at("rung:ag_claude", kylie).words, r"^Opus, back \d{1,2} [A-Z][a-z]{2}$")
        self.assertEqual(at("rung:ag_claude", kylie).status, D.RED)
        self.assertEqual(at("rung:ag_gemini", kylie).words, "Gemini Flash")
        self.assertEqual(at("rung:nvidia", kylie).words, "Nemotron 3.5")
        self.assertEqual(at("extras", kylie).words, "Sonnet 5.5 (Claude)")
        self.assertEqual(at("vplace:terminal", kylie).words.split(",")[0], at("vplace:terminal", kylie).words)
        story = " ".join(str(x) for x in at("rung:ag_claude", kylie).detail)             # the strip keeps it in full
        self.assertIn("Claude Opus 5.5 through Antigravity", story)
        self.assertIn("used up until", story)

    def test_a_cell_that_must_be_cut_is_cut_between_two_words(self):
        self.assertEqual(acd.cut_words("Nemotron 3 Ultra, sign-in dead", 20), "Nemotron 3 Ultra…")
        self.assertEqual(acd.cut_words("fits", 20), "fits")
        self.assertEqual(acd.cut_words("oneverylongwordwithnospaces", 10), "oneverylo…")  # nothing to cut at
        for room in range(6, 30):
            got = acd.cut_words("a very long sentence that cannot fit", room)
            self.assertLessEqual(len(got), room)
            self.assertIn(got.rstrip("…"), ("a very long sentence that cannot fit", "a", "a very", "a very long",
                                            "a very long sentence", "a very long sentence that",
                                            "a very long sentence that cannot"), (room, got))

    def test_the_legend_says_what_red_means_and_fits_one_line_at_the_width_of_the_pane(self):
        legend = acd.legend_text(PANE[0] - 2).plain
        self.assertIn("failed, used up, or changed behind the desk's back", legend)
        self.assertEqual(len(legend.splitlines()), 1)


class TheStrip(unittest.TestCase):
    def test_each_fact_is_said_once_and_the_word_allowance_once(self):
        h = SimpleNamespace(words="The Claude subscription: working", allowance=["Current session: 94% left", "Current week: 59% left."],
                            allowance_note="")
        lines = acd._health_lines(h)
        self.assertEqual(lines, ["The Claude subscription: working", "Allowance: Current session: 94% left; Current week: 59% left"])
        cell = acd.Cell(D.GREEN, "x", ["One fact.", "One fact.", "Another fact", *lines], setting="model")
        plain = acd.strip_text(acd.Row("model", "Model"), SimpleNamespace(name="Kylie"), cell, 400).plain
        self.assertEqual(plain.count("One fact."), 1)
        self.assertEqual(plain.lower().count("allowance:"), 1)
        self.assertTrue(plain.endswith("Enter opens the choices."))

    def test_never_more_than_three_lines_the_first_facts_stay_and_what_enter_does_stays(self):
        facts = [f"Fact number {i} is a sentence of some length that takes room on the line." for i in range(1, 12)]
        cell = acd.Cell(D.GREEN, "x", facts, setting="model")
        for width in (60, 100, 168):
            t = acd.strip_text(acd.Row("model", "Model"), SimpleNamespace(name="Kylie"), cell, width).plain
            lines = t.splitlines()
            self.assertLessEqual(len(lines), acd.STRIP_LINES, width)
            self.assertTrue(all(len(ln) <= width for ln in lines), width)
            flat = " ".join(lines)
            self.assertTrue(flat.startswith("Kylie, Model: Fact number 1 is"), width)     # the most important first
            self.assertTrue(flat.endswith("Enter opens the choices."), width)
            self.assertNotIn("Fact number 11", flat)                                      # the last ones give way

    def test_a_fact_cell_says_nothing_to_change_once_and_enter_only_lights_it_up(self):
        cell = acd.Cell(D.GREEN, "Opus 5.5, 03:58", ["It was answered by Claude Opus 5.5."], fixed=acd.FACT)
        row, col = acd.Row("last", "Who answered last"), SimpleNamespace(name="Kylie")
        calm, nudged = acd.strip_text(row, col, cell, 168), acd.strip_text(row, col, cell, 168, nudge=True)
        self.assertEqual(calm.plain, nudged.plain)                                        # the same words, not a second copy
        self.assertEqual(calm.plain.count("Nothing to change here"), 1)
        self.assertNotIn("No menu here", calm.plain)
        yellow = acd.COLOUR[D.YELLOW]
        self.assertFalse(any(yellow in str(s.style) for s in calm.spans))
        self.assertTrue(any(yellow in str(s.style) for s in nudged.spans))


class OnTheScreen(unittest.IsolatedAsyncioTestCase):
    async def test_the_header_is_short_and_the_strip_is_at_most_three_lines_in_the_pane(self):
        w = busy_world()
        app = T.make_app(w)
        async with app.run_test(size=PANE) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            rows = len(T.text_of(app, "#legend").splitlines()) + len(T.text_of(app, "#systems").splitlines())
            self.assertLessEqual(rows, 3)
            for key in ("model", "signin", "last", "rung:ag_claude", "extras", "voice", "presence:team_room"):
                self.assertTrue(app.screen.go(key, "Kylie"))
                await pilot.pause()
                self.assertLessEqual(len(T.text_of(app, "#strip").splitlines()), acd.STRIP_LINES, key)
            self.assertTrue(app.screen.go("last", "Kylie"))
            await pilot.pause()
            await pilot.press("enter")                                          # no menu: the strip's own sentence lights up
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TableScreen)
            strip = " ".join(T.text_of(app, "#strip").split())
            self.assertEqual(strip.count("Nothing to change here"), 1)
            self.assertEqual(strip.count("a fact read from the logs"), 1)

    async def test_only_whole_columns_stand_beside_the_pinned_row_names(self):
        w = busy_world()
        app = T.make_app(w)
        async with app.run_test(size=(110, 50)) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            t = app.screen.query_one("#table", acd.DeskTable)
            widths = [c.get_render_width(t) for c in t.ordered_columns]
            starts = {sum(widths[1:i]) for i in range(1, len(widths))}
            seen = t.scrollable_content_region.width - widths[0]
            moved = False
            for _ in range(len(widths) - 2):
                await pilot.press("right")
                await pilot.pause()
                await pilot.pause()
                self.assertIn(t.scroll_x, starts, "half a column is sticking out from under the row names")
                c = t.cursor_coordinate.column
                left = sum(widths[1:c]) - t.scroll_x
                self.assertGreaterEqual(left, 0)                                # the chosen column is whole ...
                self.assertLessEqual(left + widths[c], seen)                    # ... on both sides
                moved = moved or t.scroll_x > 0
            self.assertTrue(moved, "the window was wide enough: nothing scrolled, so nothing was tested")
            self.assertEqual(t.fixed_columns, 1)
            for _ in range(len(widths)):
                await pilot.press("left")
                await pilot.pause()
                await pilot.pause()
                self.assertIn(t.scroll_x, starts)
            self.assertEqual((t.cursor_coordinate.column, t.scroll_x), (0, 0))

    async def test_the_chosen_cell_is_one_clear_block_the_same_on_a_blank_cell(self):
        def luminance(colour: str) -> float:
            r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
            lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
        contrast = (luminance(acd.CURSOR_BG) + 0.05) / (luminance(acd.PAGE_BG) + 0.05)
        self.assertGreater(contrast, 1.8, "the chosen cell is too close to the page's own colour to be seen")
        w = busy_world()
        app = T.make_app(w)
        async with app.run_test(size=(260, 70)) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            t = app.screen.query_one("#table", DataTable)
            for part in ("datatable--cursor", "datatable--fixed-cursor"):
                bg = t.get_component_rich_style(part).bgcolor
                self.assertEqual(bg.get_truecolor().hex.lower(), acd.CURSOR_BG, part)
            self.assertTrue(app.screen.go("model", "Claude"))                   # a blank cell: the row does not apply
            await pilot.pause()
            self.assertEqual(T.shown(app, "model", "Claude"), "")
            r, c = t.cursor_coordinate
            from textual.coordinate import Coordinate
            fixed, scrolling = t._render_line_in_row(t._row_locations.get_key(r), 0, t.rich_style, Coordinate(r, c),
                                                     Coordinate(-1, -1))
            cursor_bg = {seg.style.bgcolor.get_truecolor().hex.lower() for cell in scrolling for seg in cell
                         if seg.style and seg.style.bgcolor}
            self.assertIn(acd.CURSOR_BG, cursor_bg, "a blank cell under the cursor is not drawn as the cursor block")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Nothing to change here: this row does not apply to Claude.", T.text_of(app, "#strip"))

    async def test_a_menu_stops_at_its_first_and_last_choice_and_says_so(self):
        w = busy_world()
        app = T.make_app(w)
        async with app.run_test(size=PANE) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            await T.open_cell(app, pilot, "model", "Kylie")
            self.assertIsInstance(app.screen, acd.CellMenu)
            lst = app.screen.query_one("#menu-list", OptionList)
            self.assertIsInstance(lst, acd.EndsList)
            last = lst.option_count - 1
            lst.highlighted = 0
            await pilot.pause()
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(lst.highlighted, 0)                                # it did not jump round to the bottom
            self.assertIn("that is the first choice", T.menu(app, "status"))
            for _ in range(last + 3):
                await pilot.press("down")
                await pilot.pause()
            self.assertEqual(lst.highlighted, last)                             # ... nor back to the top
            self.assertIn("that is the last choice", T.menu(app, "status"))
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(lst.highlighted, last - 1)
            self.assertEqual(T.menu(app, "status").strip(), "")                 # moving again clears the note

    async def test_the_confirm_line_says_escape_goes_back_to_the_choices_and_a_second_one_closes(self):
        w = busy_world()
        before = F.tree_hashes(w.root)
        app = T.make_app(w)
        async with app.run_test(size=PANE) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            await T.open_cell(app, pilot, "model", "Kylie")
            T.pick(app, "claude-sonnet-5-5")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Esc back to choices", T.menu(app, "keys"))
            self.assertIn("Esc again closes", T.menu(app, "keys"))
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)                     # back at the choices
            self.assertNotIn("Esc back to choices", T.menu(app, "keys"))
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TableScreen)
        self.assertEqual(F.tree_hashes(w.root), before)


class TheStandardLadderBox(unittest.IsolatedAsyncioTestCase):
    async def open_box(self, app, pilot):
        await pilot.pause()
        await T.show(app, pilot)
        await pilot.press("s")
        await T.settle(app, pilot)
        self.assertIsInstance(app.screen, acd.LadderPopup)

    def body(self, app) -> str:
        return " ".join(T.text_of(app, "#box-text").split())

    async def test_every_agent_fits_in_the_pane_and_the_box_says_who_is_left_out(self):
        w = busy_world()
        before = F.tree_hashes(w.root)
        app = T.make_app(w)
        async with app.run_test(size=PANE) as pilot:
            await self.open_box(app, pilot)
            words = self.body(app)
            for name in ("Jarvis", "Kylie", "Esther", "Wanna Buffet"):
                self.assertIn(name, words)
            self.assertIn("Simba is left out: no backups by design", words)
            self.assertIn("2a Antigravity: Claude 2b Antigravity: Gemini", words)
            scroll = app.screen.query_one("#box-body", VerticalScroll)
            self.assertEqual(scroll.max_scroll_y, 0, "the box does not fit: agents are hidden below its edge")
            self.assertFalse(app.screen.query_one("#box-more", Static).display)
            keys = T.text_of(app, "#box-keys")
            self.assertIn("y = yes, set it for 4 agents", keys)                 # the number is the number that is shown
        self.assertEqual(F.tree_hashes(w.root), before, "showing the box wrote something")

    async def test_in_a_short_window_a_line_says_there_is_more_below_and_the_arrows_scroll(self):
        w = busy_world()
        app = T.make_app(w)
        async with app.run_test(size=(170, 22)) as pilot:
            await self.open_box(app, pilot)
            scroll, more = app.screen.query_one("#box-body", VerticalScroll), app.screen.query_one("#box-more", Static)
            self.assertGreater(scroll.max_scroll_y, 0)
            self.assertTrue(more.display)
            self.assertIn("more below", T.text_of(app, "#box-more"))
            for _ in range(int(scroll.max_scroll_y) + 2):
                await pilot.press("down")
                await pilot.pause()
            await pilot.pause()
            self.assertEqual(scroll.scroll_y, scroll.max_scroll_y)
            self.assertFalse(more.display)                                      # at the bottom: nothing more to say

    async def test_k_and_x_choose_what_happens_to_the_extras_and_the_before_and_after_follow(self):
        w = busy_world()
        before = F.tree_hashes(w.root)
        app = T.make_app(w)
        async with app.run_test(size=PANE) as pilot:
            await self.open_box(app, pilot)
            words = self.body(app)
            self.assertIn("[k] keep extras after rung 4 (selected)", words)
            self.assertNotIn("[x] drop extras (selected)", words)
            self.assertIn("- before: 2a Opus 5.5 · 2b Gemini Flash · extra Sonnet 5.5 (Claude) · 4 Nemotron 3.5", words)
            self.assertIn("+ after: 2a Opus 5.5 · 2b Gemini Flash · 3 GPT 5.4 mini · 4 Nemotron 3.5 · extra Sonnet 5.5 (Claude)",
                          words)
            await pilot.press("x")
            await pilot.pause()
            words = self.body(app)
            self.assertIn("[x] drop extras (selected)", words)
            self.assertNotIn("[k] keep extras after rung 4 (selected)", words)
            kylie = next(p for p in app.screen.plans["drop"] if p.name == "Kylie")
            self.assertEqual(acd.ladder_words(kylie.after), "2a Opus 5.5 · 2b Gemini Flash · 3 GPT 5.4 mini · 4 Nemotron 3.5")
            self.assertIn("+ after: " + acd.ladder_words(kylie.after), words)
            self.assertEqual(words.count("extra Sonnet 5.5 (Claude)"), 1)       # only in her "before" now
            self.assertIn("takes Claude Sonnet 5.5 via Claude sub. off the list", words)
            await pilot.press("k")
            await pilot.pause()
            self.assertIn("[k] keep extras after rung 4 (selected)", self.body(app))
            await pilot.press("x")
            await pilot.press("y")                                              # a desk that is not armed: a dry run
            await T.settle(app, pilot)
            self.assertTrue(app.last_results and all(r.ok and r.dry for r in app.last_results))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_armed_drop_takes_the_extras_off_and_keep_leaves_them_after_rung_four(self):
        for mode, key in (("drop", "x"), ("keep", "k")):
            w = busy_world()
            E.set_armed(w.edit, True)
            app = T.make_app(w)
            async with app.run_test(size=PANE) as pilot:
                await self.open_box(app, pilot)
                await pilot.press(key)
                await pilot.press("y")
                await T.settle(app, pilot)
                self.assertTrue(all(r.ok and not r.dry for r in app.last_results), [r.message for r in app.last_results])
            got = T.listed(w, "assistant")
            self.assertEqual([D.rung_of(*p) for p in got[:4]], list(D.LADDER_KEYS), mode)
            if mode == "drop":
                self.assertNotIn(SONNET, got)
                self.assertEqual(len(got), 4)
            else:
                self.assertEqual(got[4:], [SONNET])
            self.assertTrue(E.undo(w.edit, loader=T.L).ok)                      # each agent can be put back

    async def test_escape_changes_nothing_on_an_armed_desk(self):
        w = busy_world()
        E.set_armed(w.edit, True)
        before = F.tree_hashes(w.root)
        app = T.make_app(w)
        async with app.run_test(size=PANE) as pilot:
            await self.open_box(app, pilot)
            await pilot.press("x")
            await pilot.press("escape")
            await T.settle(app, pilot)
            self.assertIsInstance(app.screen, acd.TableScreen)
        self.assertEqual(F.tree_hashes(w.root), before)


if __name__ == "__main__":
    unittest.main()
