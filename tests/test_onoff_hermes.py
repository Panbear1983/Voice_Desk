"""Offline test of the step 3 grid switches (onoff_hermes.py): Telegram direct chat modes and Gemini's voice,
plus the top-strip "Hermes fixes" light that reads hermes_patches.py. Everything runs in a temp folder:
no real gateway, Telegram, Gemini hook or speaker.

    .venv/bin/python -m unittest tests.test_onoff_hermes -v
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import gridstate as G  # noqa: E402
import hermes_patches as HP  # noqa: E402
import onoff  # noqa: E402
import syshealth  # noqa: E402
import voiceedit as VE  # noqa: E402

KEY = "assistant:telegram:1000000001"          # Kylie
JARVIS = "orchestrator:telegram:1000000001"


def agent_of(w, key):
    return next(a for a in agents.agents(agents.load(w.toml)) if a.key == key)


class Base(unittest.TestCase):
    def setUp(self):
        self.w = F.make_world()
        self.mode_file = self.w.paths.voice_mode_file
        self.assertEqual(self.mode_file, self.w.root / "hermes/gateway_voice_mode.json")

    def switch(self, key, place, token):
        return onoff.set_cell(key, place, token, self.w.paths, self.w.edit)

    def cell(self, key, place):
        return G.CELL_FUNCS[place](agent_of(self.w, key), self.w.paths)

    def modes(self):
        return json.loads(self.mode_file.read_text())

    def undo(self):
        return VE.undo(self.w.edit, self.w.paths)

    def install_live_patch(self, *, gateway_started=None):
        """A fake Hermes folder with the live-reload patch 'in' (marker text) and, optionally, a gateway pid."""
        home = self.w.paths.hermes_home
        (home / "hermes-agent/gateway").mkdir(parents=True)
        (home / "hermes-agent/gateway/run_voice.py").write_text("def _refresh_voice_modes_if_changed(): pass\n")
        (home / "backups/local-patches").mkdir(parents=True)
        (home / "backups/local-patches/manifest.json").write_text(json.dumps({"patches": [
            {"id": "gateway-live-voice-mode", "diff": "x.diff", "runs_in": "gateway", "summary": "live modes",
             "markers": [["gateway/run_voice.py", "_refresh_voice_modes_if_changed"]]}]}))
        if gateway_started is not None:
            (home / "gateway.pid").write_text(json.dumps({"pid": os.getpid(), "start_time": int(gateway_started * 100)}))


class TelegramDirect(Base):
    def test_first_ever_mode_creates_the_gateways_own_file_and_reads_back(self):
        self.assertFalse(self.mode_file.exists())
        res = self.switch("kylie", "tg_dm", "off")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.modes(), {KEY: "off"})
        self.assertEqual(self.cell("kylie", "tg_dm").value, G.OFF)
        self.assertIn("did not exist", json.dumps(res.entry["files"]))
        self.assertEqual(res.entry["field"], "switch:tg_dm")
        self.assertEqual(VE.read_history(self.w.edit)[-1]["new"], "off")

    def test_the_three_values_map_to_the_gateways_own_mode_names(self):
        for token, mode in (("voice_back", "voice_only"), ("always", "all"), ("off", "off")):
            res = self.switch("jarvis", "tg_dm", token)
            self.assertTrue(res.ok, res.message)
            self.assertEqual(self.modes()[JARVIS], mode)
            self.assertEqual(self.cell("jarvis", "tg_dm").label, G.LABEL[token])

    def test_other_chats_and_odd_keys_in_the_file_are_kept_and_the_old_file_is_backed_up(self):
        original = json.dumps({"wanna-buffet:telegram:1000000001": "all", "telegram:99": "off", "bad": "nonsense"}, indent=2)
        self.mode_file.write_text(original)
        res = self.switch("kylie", "tg_dm", "voice_back")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.modes(), {"wanna-buffet:telegram:1000000001": "all", "telegram:99": "off", "bad": "nonsense",
                                        KEY: "voice_only"})
        backups = list(self.w.edit.hermes_backups.glob("*-voice-panel/*gateway_voice_mode.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)

    def test_undo_of_the_change_that_created_the_mode_file_removes_the_file_again(self):
        self.assertFalse(self.mode_file.exists())
        self.switch("kylie", "tg_dm", "off")
        self.assertTrue(self.mode_file.exists())
        res = self.undo()
        self.assertTrue(res.ok, res.message)
        self.assertFalse(self.mode_file.exists(), "exact prior state: the file was absent, not an empty file")
        self.assertEqual(self.cell("kylie", "tg_dm").value, G.ALWAYS)

    def test_undoing_that_undo_makes_the_file_again(self):
        self.switch("kylie", "tg_dm", "off")
        self.assertTrue(self.undo().ok)
        self.assertFalse(self.mode_file.exists())
        again = onoff.undo(VE.read_history(self.w.edit)[-1], self.w.edit, self.w.paths)
        self.assertTrue(again.ok, again.message)
        self.assertEqual(self.modes(), {KEY: "off"})

    def test_a_file_that_was_there_before_is_never_removed_by_undo(self):
        self.mode_file.write_text(json.dumps({}))                      # an existing, empty file: it was there before
        self.switch("kylie", "tg_dm", "off")
        self.assertTrue(self.undo().ok)
        self.assertTrue(self.mode_file.exists())
        self.assertEqual(self.modes(), {})

    def test_undo_of_the_first_write_takes_the_key_off_again_and_keeps_a_hand_edit(self):
        self.switch("kylie", "tg_dm", "off")
        data = self.modes()
        data["telegram:99"] = "all"                                   # Peter edits another line by hand meanwhile
        self.mode_file.write_text(json.dumps(data))
        res = self.undo()
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.modes(), {"telegram:99": "all"})
        self.assertEqual(self.cell("kylie", "tg_dm").value, G.ALWAYS, "no saved mode: voice.auto_tts decides again")

    def test_undo_restores_the_previous_mode(self):
        self.switch("kylie", "tg_dm", "voice_back")
        self.switch("kylie", "tg_dm", "off")
        self.assertTrue(self.undo().ok)
        self.assertEqual(self.modes()[KEY], "voice_only")

    def test_a_broken_file_is_never_overwritten(self):
        self.mode_file.write_text('{"assistant:telegram:75129')
        res = self.switch("kylie", "tg_dm", "off")
        self.assertFalse(res.ok)
        self.assertIn("nothing changed", res.message)
        self.assertEqual(self.mode_file.read_text(), '{"assistant:telegram:75129')

    def test_same_mode_again_changes_nothing(self):
        self.switch("kylie", "tg_dm", "off")
        history = len(VE.read_history(self.w.edit))
        res = self.switch("kylie", "tg_dm", "off")
        self.assertTrue(res.ok)
        self.assertIn("nothing to change", res.message)
        self.assertEqual(len(VE.read_history(self.w.edit)), history)

    def test_stray_copies_in_profile_folders_are_never_read_or_written(self):
        strays = {}
        for prof, body in (("orchestrator", {"assistant:telegram:1000000001": "off"}), ("wanna-buffet", {"telegram:1000000001": "all"})):
            f = self.w.paths.profiles / prof / "gateway_voice_mode.json"
            f.write_text(json.dumps(body))
            strays[f] = f.read_bytes()
        self.assertEqual(self.cell("kylie", "tg_dm").value, G.ALWAYS, "the stray 'off' is not in force")
        self.assertTrue(self.switch("kylie", "tg_dm", "voice_back").ok)
        self.assertTrue(self.undo().ok)
        for f, before in strays.items():
            self.assertEqual(f.read_bytes(), before)

    def test_claude_gemini_codex_have_no_telegram_cell_to_switch(self):
        for key in ("claude", "gemini", "codex"):
            self.assertFalse(onoff.can_switch(agent_of(self.w, key), "tg_dm"))
            self.assertFalse(self.switch(key, "tg_dm", "off").ok)

    def test_the_message_says_when_it_goes_live(self):
        res = self.switch("kylie", "tg_dm", "off")
        self.assertIn("only when it starts", res.message, "no live-reload fix in Hermes' code: read at start")
        self.install_live_patch(gateway_started=time.time() - 3600)
        res = self.switch("kylie", "tg_dm", "always")
        self.assertIn("restart the Hermes gateway once", res.message)
        self.assertIn("not restarted", self.cell("kylie", "tg_dm").note)
        (self.w.paths.hermes_home / "gateway.pid").write_text(json.dumps({"pid": os.getpid(), "start_time": int((time.time() + 60) * 100)}))
        res = self.switch("kylie", "tg_dm", "voice_back")
        self.assertIn("next message", res.message)
        self.assertIn("re-reads it when it changes", self.cell("kylie", "tg_dm").note)

    def test_the_menu_names_the_current_value_and_when_it_applies(self):
        self.install_live_patch(gateway_started=time.time() + 60)
        self.switch("kylie", "tg_dm", "voice_back")
        m = onoff.menu(agent_of(self.w, "kylie"), "tg_dm", self.cell("kylie", "tg_dm"), self.w.paths)
        self.assertEqual([o.token for o in m.options if o.current], ["voice_back"])
        self.assertIn("switches live", m.title)


class GeminiSwitch(Base):
    def hook(self):
        return self.w.paths.gemini_hook

    def test_off_creates_the_marker_and_the_grid_admits_the_hook_does_not_read_it_yet(self):
        hook_before = self.hook().read_bytes()
        res = self.switch("gemini", "terminal", "off")
        self.assertTrue(res.ok, res.message)
        self.assertTrue(self.w.paths.gemini_switch.exists())
        self.assertEqual(self.w.paths.gemini_switch, self.w.paths.state / "gemini_voice_off")
        self.assertIn("still speaks", res.message)
        cell = self.cell("gemini", "terminal")
        self.assertEqual(cell.value, G.ALWAYS, "it really still speaks")
        self.assertIn("does not read it yet", cell.problem)
        self.assertEqual(self.hook().read_bytes(), hook_before, "Gemini's own hook is not edited in step 3")

    def test_once_the_hook_honours_the_file_off_means_off(self):
        self.hook().write_text("import os\nif os.path.exists(os.path.expanduser('~/.voice_desk/gemini_voice_off')): raise SystemExit\n")
        self.assertTrue(self.switch("gemini", "terminal", "off").ok)
        cell = self.cell("gemini", "terminal")
        self.assertEqual((cell.value, cell.problem), (G.OFF, ""))
        res = self.switch("gemini", "terminal", "always")
        self.assertTrue(res.ok)
        self.assertFalse(self.w.paths.gemini_switch.exists())
        self.assertEqual(self.cell("gemini", "terminal").value, G.ALWAYS)

    def test_undo_removes_the_marker(self):
        self.switch("gemini", "terminal", "off")
        self.assertTrue(self.undo().ok)
        self.assertFalse(self.w.paths.gemini_switch.exists())
        self.assertEqual(VE.read_history(self.w.edit)[-1]["kind"], "undo")

    def test_always_when_already_always_changes_nothing(self):
        res = self.switch("gemini", "terminal", "always")
        self.assertTrue(res.ok)
        self.assertIn("nothing to change", res.message)

    def test_the_default_without_a_marker_is_todays_behaviour(self):
        cell = self.cell("gemini", "terminal")
        self.assertEqual((cell.value, cell.problem), (G.ALWAYS, ""))
        self.assertIn("prepared", cell.note)


class HermesTerminalSwitch(Base):
    """Phase C (2026-11-18): the desk switches a Hermes agent's terminal windows through the profile's own
    terminal_voice.json; Peter's local Hermes patch makes every window re-read it before each reply."""

    def sw(self, profile="assistant"):
        return self.w.paths.profiles / profile / "terminal_voice.json"

    def install_terminal_patch(self):
        home = self.w.paths.hermes_home
        (home / "hermes-agent/hermes_cli").mkdir(parents=True)
        (home / "hermes-agent/hermes_cli/terminal_voice_switch.py").write_text('SWITCH_NAME = "terminal_voice.json"\n')
        (home / "backups/local-patches").mkdir(parents=True)
        (home / "backups/local-patches/manifest.json").write_text(json.dumps({"patches": [
            {"id": "terminal-voice-switch", "diff": "x.diff", "runs_in": "cli", "summary": "terminal switch",
             "markers": [["hermes_cli/terminal_voice_switch.py", 'SWITCH_NAME = "terminal_voice.json"']]}]}))

    def test_every_hermes_agents_terminal_is_switchable_codex_is_not(self):
        for key in ("kylie", "jarvis"):
            self.assertTrue(onoff.can_switch(agent_of(self.w, key), "terminal"), key)
            m = onoff.menu(agent_of(self.w, key), "terminal", self.cell(key, "terminal"), self.w.paths)
            self.assertEqual([o.token for o in m.options], ["off", "always"], key)
        self.assertFalse(onoff.can_switch(agent_of(self.w, "codex"), "terminal"))

    def test_the_default_without_a_file_is_todays_reading_and_reading_writes_nothing(self):
        from tests.acdfix import tree_hashes
        before = tree_hashes(self.w.root)
        cell = self.cell("kylie", "terminal")
        self.assertEqual(cell.value, G.ALWAYS)                      # fixture: voice.auto_enable true
        self.assertIn("the desk's switch is not set", cell.note)
        self.assertFalse(self.sw().exists())
        self.assertEqual(tree_hashes(self.w.root), before)

    def test_off_writes_the_profiles_own_file_and_nothing_else(self):
        self.install_terminal_patch()
        cfg = (self.w.paths.profiles / "assistant/config.yaml").read_bytes()
        toml = self.w.toml.read_bytes()
        res = self.switch("kylie", "terminal", "off")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(json.loads(self.sw().read_text()), {"voice": "off"})
        self.assertIn("next reply", res.message)
        self.assertIn("No gateway restart", res.message)
        self.assertEqual((self.w.paths.profiles / "assistant/config.yaml").read_bytes(), cfg)
        self.assertEqual(self.w.toml.read_bytes(), toml)
        self.assertFalse(self.sw("orchestrator").exists(), "one agent's switch only")
        cell = self.cell("kylie", "terminal")
        self.assertEqual((cell.value, cell.problem), (G.OFF, ""))
        self.assertIn("re-reads it before its next reply", cell.note)
        self.assertIn("follows it only after it is reopened", cell.note)
        self.assertNotIn("gateway restart needed)", cell.problem)

    def test_always_then_undo_twice(self):
        self.install_terminal_patch()
        self.assertTrue(self.switch("kylie", "terminal", "off").ok)
        self.assertTrue(self.switch("kylie", "terminal", "always").ok)
        self.assertEqual(json.loads(self.sw().read_text()), {"voice": "on"})
        self.assertEqual(self.cell("kylie", "terminal").value, G.ALWAYS)
        self.assertTrue(self.undo().ok)                             # back to off
        self.assertEqual(json.loads(self.sw().read_text()), {"voice": "off"})

    def test_undo_of_the_first_switch_removes_the_file_again(self):
        self.install_terminal_patch()
        self.assertTrue(self.switch("kylie", "terminal", "off").ok)
        self.assertTrue(self.undo().ok)
        self.assertFalse(self.sw().exists(), "no file = the windows go back to what they started with")
        self.assertIn("the desk's switch is not set", self.cell("kylie", "terminal").note)

    def test_the_same_value_again_changes_nothing(self):
        self.install_terminal_patch()
        self.assertTrue(self.switch("kylie", "terminal", "off").ok)
        res = self.switch("kylie", "terminal", "off")
        self.assertTrue(res.ok)
        self.assertIn("nothing to change", res.message)

    def test_a_broken_file_is_never_overwritten_and_shows_red(self):
        self.sw().write_text("{not json")
        res = self.switch("kylie", "terminal", "always")
        self.assertFalse(res.ok)
        self.assertEqual(self.sw().read_text(), "{not json")
        cell = self.cell("kylie", "terminal")
        self.assertEqual(cell.value, G.UNKNOWN)
        self.assertIn("windows ignore it", cell.problem)
        self.sw().write_text('{"voice": "loud"}')
        self.assertEqual(self.cell("kylie", "terminal").value, G.UNKNOWN)

    def test_without_the_hermes_fix_the_switch_says_windows_ignore_it(self):
        res = self.switch("kylie", "terminal", "off")             # no manifest in this world
        self.assertTrue(res.ok, res.message)
        self.assertIn("not in Hermes' code now", res.message)
        cell = self.cell("kylie", "terminal")
        self.assertEqual(cell.value, G.OFF)
        self.assertIn("hermes_patches.py apply", cell.problem)

    def test_hermes_patches_reports_the_terminal_fix(self):
        self.assertEqual(HP.terminal_voice_switch(self.w.paths.hermes_locations), (HP.MISSING_STATE, None))
        self.install_terminal_patch()
        state, since = HP.terminal_voice_switch(self.w.paths.hermes_locations)
        self.assertEqual(state, HP.LIVE)
        self.assertAlmostEqual(since or 0.0, time.time(), delta=60)
        (self.w.paths.hermes_home / "hermes-agent/hermes_cli/terminal_voice_switch.py").write_text("# updated away\n")
        self.assertEqual(HP.terminal_voice_switch(self.w.paths.hermes_locations)[0], HP.MISSING_STATE)

    def test_the_watcher_plans_the_same_file(self):
        import watcher
        self.install_terminal_patch()
        self.sw().write_text('{"voice": "on"}\n')
        planned = watcher._plan(agents.load(self.w.toml), "kylie", "terminal", "off", self.w.paths, self.w.edit)
        self.assertIsNotNone(planned)
        changes = planned[1] if planned else []
        self.assertEqual([(c["path"], c["key"], c["after"]) for c in changes], [(str(self.sw()), "voice", "off")])


class HermesFixesLight(Base):
    def test_no_manifest_falls_back_to_the_old_marker_check(self):
        light = syshealth.hermes_fixes(self.w.paths, hermes_agent=self.w.root / "nowhere")
        self.assertIs(light.ok, False)
        self.assertIn("missing", light.label)

    def test_patch_gone_is_red_and_says_how_to_put_it_back(self):
        self.install_live_patch()
        (self.w.paths.hermes_home / "hermes-agent/gateway/run_voice.py").write_text("# an update replaced the file\n")
        light = syshealth.hermes_fixes(self.w.paths)
        self.assertIs(light.ok, False)
        self.assertIn("0/1", light.label)
        self.assertIn("hermes_patches.py apply", light.detail)

    def test_on_disk_but_gateway_not_restarted_is_grey_not_green(self):
        self.install_live_patch(gateway_started=time.time() - 3600)
        light = syshealth.hermes_fixes(self.w.paths)
        self.assertIsNone(light.ok)
        self.assertIn("gateway not restarted", light.label)

    def test_in_and_live_is_green(self):
        self.install_live_patch(gateway_started=time.time() + 60)
        light = syshealth.hermes_fixes(self.w.paths)
        self.assertIs(light.ok, True)
        self.assertIn("1/1", light.label)

    def test_collect_passes_the_grids_own_hermes_home(self):
        self.install_live_patch(gateway_started=time.time() + 60)
        labels = [l.label for l in syshealth.collect(self.w.paths)]
        self.assertIn("Hermes fixes present (1/1)", labels)


if __name__ == "__main__":
    unittest.main()
