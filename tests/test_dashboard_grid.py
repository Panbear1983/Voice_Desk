"""Offline test of the dashboard's grid: it renders headless (no window on the screen), against the fake
world from gridfix, with the background reading switched off so the test decides when to read.

    .venv/bin/python -m unittest tests.test_dashboard_grid -v
"""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import dashboard as D  # noqa: E402
import gridevidence as E  # noqa: E402
import service  # noqa: E402
import settings as S  # noqa: E402
import syshealth  # noqa: E402
import voicecfg  # noqa: E402
import voiceedit  # noqa: E402
import voicetest  # noqa: E402
from textual.widgets import DataTable, Input, OptionList, Static  # noqa: E402

FAKE_LIGHTS = [syshealth.Light(True, "voice engine OK"), syshealth.Light(True, "Hermes gateway up"),
               syshealth.Light(False, "speech queue off"), syshealth.Light(True, "Hermes fixes present (2/2)"),
               syshealth.Light(True, "Mac output: volume 50%, not muted, Test Speakers")]


def make_app(world):
    return D.Dashboard(paths=world.paths, edit_paths=world.edit, reader=E.LogReader(world.paths.profiles), autostart=False,
                       cfg_loader=lambda: agents.load(world.toml))


async def load(app, pilot):
    await pilot.pause()
    with mock.patch.object(syshealth, "collect", return_value=FAKE_LIGHTS):
        rows, lights = await asyncio.to_thread(app.collect)
    app.apply_snapshot(rows, lights)
    await pilot.pause()


def text_of(app, selector):
    return str(app.screen.query_one(selector, Static).render())


async def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


class TheGridScreen(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        service.set_muted(False)
        service.take_orders()
        self.w = F.make_world()
        now = time.time()
        for i in range(3):                                    # Kylie: Telegram replies with no voice at all (Peter's real case)
            F.dm_reply(self.w, "assistant", now - (3 - i) * 3600)
        F.dm_reply(self.w, "orchestrator", now - 1800)       # Jarvis: replied, and a voice followed
        F.dm_voice(self.w, "orchestrator", now - 1800 + 1)
        F.team_note(self.w, "assistant", now - 600)

    async def test_the_grid_is_the_main_screen_with_every_agent_and_every_place(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            self.assertIsInstance(app.screen, D.GridScreen)
            table = app.screen.query_one(DataTable)
            self.assertEqual(table.row_count, 7)
            self.assertEqual([c.label.plain for c in table.columns.values()],
                             ["Agent", "Voice (EN · ZH)", "Terminal window", "Telegram direct", "Telegram group",
                              "Mic window / phone", "iPhone (Siri)", "Voice check"])
            first = [str(table.get_row_at(i)[0]) for i in range(7)]
            self.assertEqual(first, ["Jarvis", "Kylie", "Esther", "Wanna Buffet", "Claude", "Gemini", "Codex"])
            health = text_of(app, "#health")
            for word in ("voice engine OK", "Hermes gateway up", "speech queue off", "Hermes fixes present", "Mac output"):
                self.assertIn(word, health)

    async def test_kylies_text_only_telegram_cell_is_red_and_the_team_group_cell_is_green(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            by = {r.agent.key: r for r in app.rows}
            self.assertEqual(by["kylie"].evidence["tg_dm"].status, "bad")
            self.assertEqual(by["jarvis"].evidence["tg_dm"].status, "ok")
            self.assertEqual(by["kylie"].evidence["tg_group"].status, "ok")
            table = app.screen.query_one(DataTable)
            kylie_dm = table.get_row_at(1)[3]
            self.assertIn("text only", kylie_dm.plain)
            self.assertIn("#e74c3c", {str(s.style) for s in kylie_dm.spans})
            kylie_group = table.get_row_at(1)[4]
            self.assertIn("#2ecc71", {str(s.style) for s in kylie_group.spans})

    async def test_the_detail_pane_follows_the_cursor(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down")
            await pilot.pause()
            detail = text_of(app, "#detail")
            self.assertIn("Kylie", detail)
            self.assertIn("went out as text with no voice", detail)
            self.assertIn("quiet hours 22:00-07:00", detail)

    async def test_m_and_h_work_on_the_grid_and_the_listener_screen_and_the_other_keys_live_on_the_second_screen(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("m")
            await pilot.pause()
            self.assertTrue(service.is_muted(), "M on the grid mutes")
            await pilot.press("m", "h")
            await pilot.pause()
            self.assertFalse(service.is_muted())
            self.assertEqual(service.take_orders(), ["hush"], "H on the grid hushes")
            await pilot.press("n")                           # N is not a grid key
            await pilot.pause()
            self.assertEqual(service.take_orders(), [])
            with mock.patch.object(voicecfg, "PROFILES", self.w.paths.profiles), \
                    mock.patch.object(voicecfg, "CLAUDE_CONF", self.w.edit.claude_conf), \
                    mock.patch.object(voicecfg, "GEMINI_CONF", self.w.edit.gemini_conf):
                await pilot.press("s")
                await pilot.pause()
                self.assertIsInstance(app.screen, D.ListenerScreen)
                await pilot.press("n")
                await pilot.press("m")
                await pilot.press("h")
                await pilot.pause()
                self.assertEqual(service.take_orders(), ["new_thread", "hush"], "N and H on the listener screen")
                self.assertTrue(service.is_muted(), "M on the listener screen")
                await pilot.press("m")
                self.assertIn("Heard", text_of(app, "#body"))
                self.assertIn("Voices", text_of(app, "#voices"))
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, D.GridScreen)
                self.assertEqual(app.screen.query_one(DataTable).row_count, 7, "the grid is back, with its rows")

    async def test_t_runs_the_silent_test_for_the_selected_row_and_shows_the_result(self):
        app = make_app(self.w)
        asked = []

        def fake_run(agent, cfg):
            asked.append(agent.key)
            return [voicetest.TestResult("en", "en-US-AvaNeural", True, 2.5, 0.5, "mp3, 2.5s", time.time(), "voice"),
                    voicetest.TestResult("zh", "zh-TW-HsiaoChenNeural", False, 0, 0.5, "the voice could not be made", time.time())]

        with mock.patch.object(voicetest, "run", fake_run), mock.patch.object(voicetest, "save"), \
                mock.patch.object(voicetest, "load", return_value={}):
            async with app.run_test(size=(180, 50)) as pilot:
                await load(app, pilot)
                await pilot.press("down", "t")
                self.assertTrue(await wait_for(lambda: "kylie" in app.tests))
                await pilot.pause()
                self.assertEqual(asked, ["kylie"])
                cell = app.screen.query_one(DataTable).get_row_at(1)[7].plain
                self.assertIn("en ✓ 2.5s", cell)
                self.assertIn("zh ✗ FAILED", cell)
                self.assertIn("FAILED", text_of(app, "#detail"))

    async def test_editing_a_voice_with_e_then_undo_with_u(self):
        app = make_app(self.w)
        profile = self.w.paths.profiles / "assistant/config.yaml"
        before = profile.read_text()
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "e")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.AskScreen)
            field = app.screen.query_one(Input)
            self.assertEqual(field.value, "en-US-AvaNeural", "the box starts with the current voice")
            field.value = "en-US-AriaNeural"
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: voiceedit.read_history(self.w.edit)))
            await pilot.pause()
            self.assertEqual(next(a for a in agents.agents(agents.load(self.w.toml)) if a.key == "kylie").voice, "en-US-AriaNeural")
            self.assertIn("en-US-AriaNeural", profile.read_text())
            await pilot.pause()
            self.assertIn("Kylie English voice: Ava → Aria", text_of(app, "#history"))
            self.assertIn("✓", text_of(app, "#msg"))
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(voiceedit.read_history(self.w.edit)) == 2))
            await pilot.pause()
            self.assertEqual(profile.read_text(), before)
            self.assertIn("undo", text_of(app, "#history"))

    async def test_a_bad_voice_name_is_refused_with_a_sentence_and_changes_nothing(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "z")
            await pilot.pause()
            app.screen.query_one(Input).value = "en-US-AvaNeural"            # an English voice in the Mandarin box
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: "✗" in text_of(app, "#msg")))
            self.assertIn("Mandarin voice looks like", text_of(app, "#msg"))
            self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    async def test_gemini_and_codex_voices_are_edited_on_the_grid_too_and_undone_with_u(self):
        # step 5: E on Gemini's row opens the box; Gemini has no voice in this fixture, so it starts empty (silent)
        app = make_app(self.w)
        conf = self.w.edit.gemini_conf
        before = conf.read_text()
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "down", "down", "down", "down", "e")     # Gemini is the 6th row
            await pilot.pause()
            self.assertIsInstance(app.screen, D.AskScreen)
            field = app.screen.query_one(Input)
            self.assertEqual(field.value, "", "no shared Jenny any more: no voice is no voice")
            field.value = "en-US-AriaNeural"
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: voiceedit.read_history(self.w.edit)))
            await pilot.pause()
            self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', conf.read_text())
            self.assertIn("Gemini English voice: not set → Aria", text_of(app, "#history"))
            await pilot.press("down", "e")                                      # Codex, the 7th row
            await pilot.pause()
            app.screen.query_one(Input).value = "en-GB-RyanNeural"
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: len(voiceedit.read_history(self.w.edit)) == 2))
            await pilot.pause()
            self.assertEqual(next(a for a in agents.agents(agents.load(self.w.toml)) if a.key == "codex").voice, "en-GB-RyanNeural")
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(voiceedit.read_history(self.w.edit)) == 3))
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(voiceedit.read_history(self.w.edit)) == 4))
            await pilot.pause()
            self.assertEqual(conf.read_text(), before)
            self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    async def test_the_grid_never_reads_more_than_once_per_refresh_and_not_at_all_when_nothing_changed(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            reads = app.reader.reads
            with mock.patch.object(syshealth, "collect", return_value=FAKE_LIGHTS):
                for _ in range(3):
                    await asyncio.to_thread(app.collect)
            self.assertEqual(app.reader.reads, reads, "unchanged logs are not read again")
            app._last_collect = time.monotonic()
            app.autostart = True
            with mock.patch.object(app, "_collect_worker") as worker:
                app.collect_in_background()                   # inside the refresh window: nothing starts
                self.assertFalse(worker.called)
                app.collect_in_background(force=True)         # C key
                self.assertTrue(worker.called)

    async def test_the_old_listener_light_still_shows_on_the_grid(self):
        S.LISTENER_STATUS.unlink(missing_ok=True)
        live = service.Status()
        live.update(target="hermes:orchestrator", name="Jarvis")
        live.set_state("thinking")
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            app.refresh_status()
            self.assertIn("thinking", text_of(app, "#light"))
            self.assertIn("Jarvis", text_of(app, "#who"))
        S.LISTENER_STATUS.unlink(missing_ok=True)


class TheOnOffCells(unittest.IsolatedAsyncioTestCase):
    """Enter on a cell switches it (step 2). Fake world only: no real file, window or speaker is touched."""

    def setUp(self):
        service.set_muted(False)
        service.take_orders()
        self.w = F.make_world()
        self.w.paths.mirror_config.write_text(json.dumps({"chat_id": "-1", "enabled": True, "voice_notes": {
            "on": True, "agents": ["orchestrator", "assistant", "tax_angel_esther", "wanna-buffet"]}}, indent=1, sort_keys=True))

    def group_agents(self):
        return json.loads(self.w.paths.mirror_config.read_text())["voice_notes"]["agents"]

    async def test_enter_on_kylies_group_cell_opens_a_chooser_and_off_switches_her_off_then_u_undoes_it(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "right", "right", "right", "right")      # Kylie, Telegram group column
            await pilot.pause()
            self.assertIn("Enter changes it", text_of(app, "#detail"))
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            options = app.screen.query_one(OptionList)
            self.assertEqual([o.id for o in options._options], ["off", "always"])
            self.assertEqual(options.highlighted, 1, "the chooser starts on the current value (Always)")
            await pilot.press("up", "enter")
            self.assertTrue(await wait_for(lambda: voiceedit.read_history(self.w.edit)))
            await pilot.pause()
            self.assertNotIn("assistant", self.group_agents())
            self.assertIn("orchestrator", self.group_agents())
            self.assertIn("✓", text_of(app, "#msg"))
            self.assertIn("Kylie Telegram group voice notes: Always → Off", text_of(app, "#history"))
            self.assertIsInstance(app.screen, D.GridScreen)
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(voiceedit.read_history(self.w.edit)) == 2))
            await pilot.pause()
            self.assertIn("assistant", self.group_agents())
            self.assertIn("undo", text_of(app, "#history"))

    async def test_escape_in_the_chooser_changes_nothing(self):
        app = make_app(self.w)
        before = self.w.paths.mirror_config.read_text()
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("right", "right", "right", "right", "enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.GridScreen)
        self.assertEqual(self.w.paths.mirror_config.read_text(), before)
        self.assertEqual(voiceedit.read_history(self.w.edit), [])

    async def test_a_read_only_cell_says_why_and_opens_nothing(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "right", "right", "right", "right", "right", "right", "enter")      # Kylie, iPhone
            await pilot.pause()
            self.assertIsInstance(app.screen, D.GridScreen)
            self.assertIn("Siri", text_of(app, "#msg"))
            await pilot.press("down", "down", "down", "down", "down", "left", "left", "left", "left", "enter")   # Codex, terminal window
            await pilot.pause()
            self.assertIsInstance(app.screen, D.GridScreen)
            self.assertIn("Codex", text_of(app, "#msg"))

    async def test_claude_terminal_cell_switches_voice_off_and_the_grid_shows_it(self):
        self.w.paths.claude_dir.joinpath("settings.json").write_text(
            json.dumps({"voice": {"enabled": True, "mode": "tap"}, "voiceEnabled": True}, indent=2) + "\n")
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "down", "down", "down", "right", "right", "enter")   # Claude, terminal window
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            await pilot.press("up", "enter")
            self.assertTrue(await wait_for(lambda: voiceedit.read_history(self.w.edit)))
            await pilot.pause()
            self.assertIs(json.loads((self.w.paths.claude_dir / "settings.json").read_text())["voice"]["enabled"], False)
            self.assertTrue(await wait_for(lambda: True))
            rows, lights = await asyncio.to_thread(app.collect)
            self.assertEqual({r.agent.key: r.cells["terminal"].value for r in rows}["claude"], "off")

    async def test_kylies_telegram_direct_cell_switches_through_the_chooser(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "right", "right", "right", "enter")      # Kylie, Telegram direct chat
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            await pilot.press("up", "up", "enter")                              # Always -> Off
            self.assertTrue(await wait_for(lambda: voiceedit.read_history(self.w.edit)))
            await pilot.pause()
            self.assertEqual(json.loads(self.w.paths.voice_mode_file.read_text()), {"assistant:telegram:7512954760": "off"})
            rows, lights = await asyncio.to_thread(app.collect)
            self.assertEqual({r.agent.key: r.cells["tg_dm"].value for r in rows}["kylie"], "off")

    async def test_gemini_terminal_cell_writes_the_switch_file_and_says_the_hook_is_not_listening_yet(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "down", "down", "down", "down", "right", "right", "enter")   # Gemini, terminal window
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            await pilot.press("up", "enter")
            self.assertTrue(await wait_for(lambda: voiceedit.read_history(self.w.edit)))
            await pilot.pause()
            self.assertTrue(self.w.paths.gemini_switch.exists())
            self.assertIn("still speaks", text_of(app, "#msg"))
            rows, lights = await asyncio.to_thread(app.collect)
            gemini = {r.agent.key: r for r in rows}["gemini"]
            self.assertIn("does not read it yet", gemini.cells["terminal"].problem)

    async def test_the_mic_cell_offers_the_voice_desk_switch_and_no_shared_voice(self):
        app = make_app(self.w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("right", "right", "right", "right", "right", "enter")   # Jarvis, mic window / phone
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            options = app.screen.query_one(OptionList)
            self.assertEqual([o.id for o in options._options], ["queue:off"])
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: voiceedit.read_history(self.w.edit)))
            self.assertTrue((self.w.paths.speech_dir / "off").exists())

    async def test_enter_on_the_voice_column_edits_the_voice_and_on_the_voice_check_column_tests(self):
        app = make_app(self.w)
        asked = []
        with mock.patch.object(voicetest, "run", lambda agent, cfg: asked.append(agent.key) or []), \
                mock.patch.object(voicetest, "save"), mock.patch.object(voicetest, "load", return_value={}):
            async with app.run_test(size=(180, 50)) as pilot:
                await load(app, pilot)
                await pilot.press("right", "enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, D.AskScreen)
                await pilot.press("escape")
                await pilot.press("right", "right", "right", "right", "right", "right", "enter")
                self.assertTrue(await wait_for(lambda: asked))
                self.assertEqual(asked, ["jarvis"])


if __name__ == "__main__":
    unittest.main()
