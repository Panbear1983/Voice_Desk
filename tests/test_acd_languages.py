"""Offline tests for the last three things the old dashboard had and the table desk lacked (2026-11-18):

    extra languages     the old L key: voices for languages beyond English and Chinese (stored and shown only)
    language choice     the old O key: follow the reply's language, or always one language (stored and shown only)
    quiet hours         shown per place, as the old dashboard's grid showed them (set by hand, nothing enforces it)

L and O go through the old dashboard's own writer (voiceedit.change) behind the desk's armed gate, with the
desk's history and u for undo. Throw-away folders and fakes only: no live settings file, no window, no listener,
no sound.

    .venv/bin/python -m unittest tests.test_acd_languages -v
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
import acd_edit as E  # noqa: E402
import acd_parity as P  # noqa: E402
import agents as A  # noqa: E402
from textual.widgets import Input  # noqa: E402

WHEN = dt.datetime(2026, 11, 18, 16, 0, 0)
NANAMI = "ja-JP-NanamiNeural"


def world(armed: bool = False):
    return F.with_grid(F.edit_world(armed=armed))


def block(w, key: str) -> dict:
    return A.load(w.edit.voice.toml)["agents"][key]


def toml_text(w) -> str:
    return Path(w.edit.voice.toml).read_text(encoding="utf-8")


def english_only(w, key: str = "jarvis") -> None:
    """Mark one agent English only, by hand, in the throw-away list (the way a person editing the file would)."""
    text = toml_text(w).replace(f"[agents.{key}]\n", f"[agents.{key}]\nenglish_only = true\n", 1)
    Path(w.edit.voice.toml).write_text(text, encoding="utf-8")


# ---- 1. the writers -----------------------------------------------------------------------------------------------
class TheWriters(unittest.TestCase):
    def test_a_dry_run_shows_the_line_and_writes_nothing(self):
        w = world()
        before = F.tree_hashes(w.root)
        r = P.set_extra(w.edit, "kylie", f"ja={NANAMI}", now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertIn("Kylie's extra languages change from none to Japanese (Nanami)", r.message)
        self.assertIn(f'+ voices_extra = {{ ja = "{NANAMI}" }}', "\n".join(r.lines))
        self.assertIn("Stored and shown only", r.needs)
        r = P.set_override(w.edit, "kylie", "zh", now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertIn("from follow the reply's language to always Chinese", r.message)
        self.assertIn('+ voice_override = "zh"', "\n".join(r.lines))
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_armed_extra_languages_are_written_with_a_copy_and_u_puts_them_back_exactly(self):
        w = world(armed=True)
        original = toml_text(w)
        r = P.set_extra(w.edit, "kylie", f"ja={NANAMI}, fr=fr-FR-DeniseNeural", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(block(w, "kylie")["voices_extra"], {"ja": NANAMI, "fr": "fr-FR-DeniseNeural"})
        self.assertTrue(list(Path(w.edit.voice.toml_backups).glob("*-agents.toml")), "a copy was kept first")
        last = E.last_undoable(w.edit)
        self.assertEqual((last["kind"], last["field"]), ("language", "voices_extra"))
        again = P.set_extra(w.edit, "kylie", f"ja={NANAMI}, fr=fr-FR-DeniseNeural", now=WHEN)
        self.assertTrue(again.ok and "nothing to change" in again.message, again.message)
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=5), loader=F.lines_loader)
        self.assertTrue(u.ok and not u.dry, u.message)
        self.assertTrue(u.message.startswith("Undone: Kylie's extra languages"), u.message)
        self.assertEqual(toml_text(w), original)

    def test_the_language_choice_is_written_auto_takes_the_line_away_and_u_puts_it_back_verbatim(self):
        w = world(armed=True)
        original = toml_text(w)
        self.assertTrue(P.set_extra(w.edit, "kylie", f"ja={NANAMI}", now=WHEN).ok)
        r = P.set_override(w.edit, "kylie", "ja", now=WHEN + dt.timedelta(seconds=1))
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(block(w, "kylie")["voice_override"], "ja")
        with_choice = toml_text(w)
        r = P.set_override(w.edit, "kylie", "auto", now=WHEN + dt.timedelta(seconds=2))
        self.assertTrue(r.ok, r.message)
        self.assertNotIn("voice_override", block(w, "kylie"))          # auto is the default: the line goes
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=3), loader=F.lines_loader)
        self.assertTrue(u.ok, u.message)
        self.assertEqual(toml_text(w), with_choice)                     # the removed line is back, verbatim
        self.assertTrue(E.undo(w.edit, now=WHEN + dt.timedelta(seconds=4), loader=F.lines_loader).ok)
        self.assertTrue(E.undo(w.edit, now=WHEN + dt.timedelta(seconds=5), loader=F.lines_loader).ok)
        self.assertEqual(toml_text(w), original)

    def test_refusals_say_why_and_write_nothing(self):
        w = world(armed=True)
        before = F.tree_hashes(w.root)
        for text in ("ja", "en=en-US-AvaNeural", "ja=fr-FR-DeniseNeural"):
            r = P.set_extra(w.edit, "kylie", text, now=WHEN)
            self.assertFalse(r.ok, text)
            self.assertTrue(r.message.startswith("Not changed."), r.message)
        r = P.set_override(w.edit, "kylie", "ja", now=WHEN)              # not one of her languages
        self.assertFalse(r.ok)
        self.assertIn("follow the reply's language, always English, always Chinese", r.message)
        self.assertTrue(P.set_override(w.edit, "kylie", "auto", now=WHEN).ok)      # already: nothing to change
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_the_language_it_is_pinned_to_cannot_be_taken_away_first(self):
        w = world(armed=True)
        self.assertTrue(P.set_extra(w.edit, "kylie", f"ja={NANAMI}", now=WHEN).ok)
        self.assertTrue(P.set_override(w.edit, "kylie", "ja", now=WHEN).ok)
        kept = toml_text(w)
        r = P.set_extra(w.edit, "kylie", "", now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("set it back to follow the reply's language first", r.message)
        self.assertEqual(toml_text(w), kept)

    def test_an_english_only_agent_takes_no_other_language(self):
        w = world(armed=True)
        english_only(w)
        r = P.set_extra(w.edit, "jarvis", f"ja={NANAMI}", now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("speaks English only", r.message)
        self.assertFalse(P.set_override(w.edit, "jarvis", "zh", now=WHEN).ok)
        self.assertEqual(P.choice_codes(P.languages_now(w.edit, "jarvis")), ["auto", "en"])
        self.assertTrue(P.set_override(w.edit, "jarvis", "en", now=WHEN).ok)
        self.assertEqual(block(w, "jarvis")["voice_override"], "en")


# ---- 2. what the table shows ---------------------------------------------------------------------------------------
class TheTable(unittest.TestCase):
    def test_quiet_hours_show_on_the_place_they_belong_to_and_nowhere_else(self):
        w = world()
        snap = F.edit_collect(w)
        kylie = F.agent(snap, "assistant")
        self.assertEqual({p.key: p.quiet for p in kylie.voice_places}.get("tg_dm"), "22:00-07:00")
        tg = acd.cell_at("vplace:tg_dm", kylie)
        self.assertTrue(tg.words.endswith(", quiet 22:00-07:00"), tg.words)
        story = " ".join(tg.detail)
        self.assertIn("Quiet hours: 22:00-07:00 (stored and shown only: nothing keeps it quiet then yet", story)
        self.assertNotIn("quiet", acd.cell_text(tg, len(tg.short) + 3).plain)   # a narrow column keeps the setting
        for place in ("terminal", "mic"):
            self.assertNotIn("Quiet hours", " ".join(acd.cell_at(f"vplace:{place}", kylie).detail))
        jarvis = F.agent(snap, "orchestrator")
        self.assertNotIn("Quiet hours", " ".join(acd.cell_at("vplace:tg_dm", jarvis).detail))

    def test_the_other_languages_row_appears_only_when_something_is_stored(self):
        w = world(armed=True)
        self.assertNotIn("languages", [r.key for r in acd.rows_of(F.edit_collect(w))])
        self.assertTrue(P.set_extra(w.edit, "kylie", f"ja={NANAMI}", now=WHEN).ok)
        self.assertTrue(P.set_override(w.edit, "claude", "zh", now=WHEN).ok)
        snap = F.edit_collect(w)
        rows = [r.key for r in acd.rows_of(snap)]
        self.assertIn("languages", rows)
        self.assertLess(rows.index("speed"), rows.index("languages"))
        kylie = acd.cell_at("languages", F.agent(snap, "assistant"))
        self.assertEqual((kylie.words, kylie.setting), ("Japanese", "languages"))
        self.assertIn("Extra languages: Japanese (Nanami).", kylie.detail)
        claude = acd.cell_at("languages", next(o for o in snap.others if o.name == "Claude"))
        self.assertEqual(claude.words, "always Chinese")
        self.assertIn("Stored and shown only", " ".join(claude.detail))
        self.assertEqual(acd.cell_at("languages", F.agent(snap, "orchestrator")).words, "none")


# ---- 3. the menus ----------------------------------------------------------------------------------------------------
class TheMenus(unittest.IsolatedAsyncioTestCase):
    async def test_the_chinese_voice_menu_leads_to_the_language_choice_and_it_is_saved_once_confirmed(self):
        w = world(armed=True)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "voice_zh", "Kylie")
            self.assertIn("other languages... (stored only; now: none)", menu_list(app))
            pick(app, acd.LANG_MENU)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertEqual(menu_list(app).split("\n")[:4],
                             ["(o) follow the reply's language (the default)   now", "( ) always English",
                              "( ) always Chinese", "( ) extra languages: change... (now: none)"])
            pick(app, "override|zh")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("WRITTEN", confirm)
            self.assertIn("Nothing speaks by this choice yet", confirm)
            self.assertNotIn("voice_override", block(w, "kylie"))       # nothing before the second Enter
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved: Kylie's language choice changes from follow the reply's language to always "
                          "Chinese", notice(app))
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertEqual(shown(app, "languages", "Kylie"), f"{acd.MARK} always Chinese")
        self.assertEqual(block(w, "kylie")["voice_override"], "zh")

    async def test_extra_languages_are_typed_then_one_yes_and_esc_saves_nothing(self):
        w = world(armed=True)
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "voice_zh", "Kylie")
            pick(app, acd.LANG_MENU)
            await pilot.press("enter")
            await pilot.pause()
            pick(app, "extra")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.AskPopup)
            note = " ".join(str(app.screen.query_one("#box-note").render()).split())
            self.assertIn("Type code=voice", note)
            app.screen.query_one("#box-input", Input).value = f"ja={NANAMI}"
            await pilot.press("enter")
            await pilot.pause()
            words = " ".join(notice(app).split())
            self.assertIn("Change Kylie's extra languages?", words)
            self.assertIn("now none", words)
            self.assertIn("new Japanese (Nanami)", words)
            await pilot.press("escape")                                 # no at the question: nothing is saved
            await settle(app, pilot)
            self.assertEqual(F.tree_hashes(w.root), before)
            await open_cell(app, pilot, "voice_zh", "Kylie")
            pick(app, acd.LANG_MENU)
            await pilot.press("enter")
            await pilot.pause()
            pick(app, "extra")
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#box-input", Input).value = "ja=nonsense"
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Nothing was changed.", notice(app))
            await pilot.press("escape")
            await pilot.pause()
            await open_cell(app, pilot, "voice_zh", "Kylie")
            pick(app, acd.LANG_MENU)
            await pilot.press("enter")
            await pilot.pause()
            pick(app, "extra")
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#box-input", Input).value = f"ja={NANAMI}"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
            self.assertIn("Saved: Kylie's extra languages change from none to Japanese (Nanami)", notice(app))
        self.assertEqual(block(w, "kylie")["voices_extra"], {"ja": NANAMI})

    async def test_a_dry_desk_shows_the_change_and_writes_nothing(self):
        w = world()
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "voice_zh", "Claude")
            pick(app, acd.LANG_MENU)
            await pilot.press("enter")
            await pilot.pause()
            pick(app, "override|en")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("DRY RUN, nothing written", notice(app))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_t_on_a_line_that_opens_a_next_step_tests_nothing(self):
        w = world()
        tried = []
        app = make_app(w, tester=lambda agent, lang, voice: tried.append(voice) or (True, "ok"))
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "voice_zh", "Kylie")
            pick(app, acd.LANG_MENU)
            await pilot.press("t")
            await pilot.pause()
            self.assertIn("no voice to test", menu(app, "status"))
            await pilot.press("escape")
            await pilot.pause()
        self.assertEqual(tried, [])

    async def test_on_the_rows_name_one_choice_goes_to_every_agent_it_is_a_choice_for(self):
        """Enter on "Other languages" itself (the row's name): one language choice for every agent at once. The
        confirmation lists each agent's before and after; an English-only agent and an agent without the chosen
        extra language stay as they are; nothing is written before the second Enter."""
        w = world(armed=True)
        english_only(w)                                                 # Jarvis: English only
        self.assertTrue(P.set_extra(w.edit, "kylie", f"ja={NANAMI}", now=WHEN).ok)   # the row shows from now on
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "languages", 0)
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertEqual(menu(app, "title"), "Other languages: every agent")
            self.assertEqual(menu_list(app).split("\n"),
                             ["(o) follow the reply's language (the default)   now", "( ) always English",
                              "( ) always Chinese", "( ) always Japanese"])     # no "extra languages..." step here
            pick(app, "override|zh")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("WRITTEN", confirm)
            for name in ("Kylie", "Esther", "Wanna Buffet", "Claude", "Gemini", "Codex"):
                self.assertIn(f"{name}: follow the reply's language (the default) -> always Chinese", confirm)
            self.assertIn("Jarvis: stays follow the reply's language (the default) (not a choice for this agent)",
                          confirm)
            self.assertNotIn("voice_override", toml_text(w))           # nothing before the second Enter
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(len(app.last_results), 6)                  # one change per agent, none for Jarvis
            self.assertTrue(all(r.ok and not r.dry for r in app.last_results), [r.message for r in app.last_results])
        for key in ("kylie", "esther", "wanna_buffet", "claude", "gemini", "codex"):
            self.assertEqual(block(w, key).get("voice_override"), "zh", key)
        self.assertNotIn("voice_override", block(w, "jarvis"))

    async def test_on_the_rows_name_an_extra_language_only_one_agent_has_changes_only_that_agent(self):
        w = world(armed=True)
        self.assertTrue(P.set_extra(w.edit, "kylie", f"ja={NANAMI}", now=WHEN).ok)   # only Kylie has Japanese
        E.set_armed(w.edit, False)                                      # then a dry desk: shown, nothing written
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "languages", 0)
            pick(app, "override|ja")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("Kylie: follow the reply's language (the default) -> always Japanese", confirm)
            for name in ("Jarvis", "Esther", "Wanna Buffet", "Claude", "Gemini", "Codex"):
                self.assertIn(f"{name}: stays follow the reply's language (the default) (not a choice for this agent)",
                              confirm)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(len(app.last_results), 1)
            self.assertTrue(app.last_results[0].ok and app.last_results[0].dry, app.last_results[0].message)
        self.assertEqual(F.tree_hashes(w.root), before)
        self.assertNotIn("voice_override", block(w, "kylie"))


if __name__ == "__main__":
    unittest.main()
