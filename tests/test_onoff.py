"""Offline test of the grid's live on/off switches (onoff.py), against the fake world from gridfix.
Nothing here touches Peter's real settings, windows or speakers; `jq` and `bash` only read files in a temp folder.

    .venv/bin/python -m unittest tests.test_onoff -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import gridstate as G  # noqa: E402
import onoff  # noqa: E402
import voicecfg  # noqa: E402
import voiceedit as VE  # noqa: E402

TEAM = ["orchestrator", "assistant", "tax_angel_esther", "wanna-buffet"]


def agent_of(w, key):
    return next(a for a in agents.agents(agents.load(w.toml)) if a.key == key)


def mirror(w):
    return json.loads(w.paths.mirror_config.read_text())


def jq_says(path):
    return subprocess.run(["jq", "-r", "(.voice.enabled // .voiceEnabled) == true", str(path)],
                          capture_output=True, text=True).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.w = F.make_world()
        # the real files' layouts: mirror_config.json is indent=1 + sorted keys, settings.json is jq's 2-space layout
        self.write_mirror({"chat_id": "-1", "enabled": True, "voice_notes": {"agents": list(TEAM), "on": True}})
        self.settings = self.w.paths.claude_dir / "settings.json"
        self.settings.write_text(json.dumps({"permissions": {"defaultMode": "bypassPermissions"}, "model": "sonnet",
                                             "voice": {"enabled": True, "mode": "tap"}, "voiceEnabled": True}, indent=2) + "\n")
        self.conf = self.w.edit.claude_conf

    def write_mirror(self, data):
        self.w.paths.mirror_config.write_text(json.dumps(data, indent=1, sort_keys=True))

    def switch(self, key, place, token):
        return onoff.set_cell(key, place, token, self.w.paths, self.w.edit)

    def cell(self, key, place):
        return G.CELL_FUNCS[place](agent_of(self.w, key), self.w.paths)


class TheMenus(Base):
    def test_each_place_offers_only_the_values_it_really_has(self):
        p = self.w.paths
        m = onoff.menu(agent_of(self.w, "kylie"), "tg_group", self.cell("kylie", "tg_group"), p)
        self.assertEqual([o.token for o in m.options], ["off", "always"], "a group line has no voice-back")
        self.assertEqual([o.token for o in m.options if o.current], ["always"])
        m = onoff.menu(agent_of(self.w, "claude"), "terminal", self.cell("claude", "terminal"), p)
        self.assertEqual([o.token for o in m.options], ["off", "always"], "terminals: Off / Always only")
        m = onoff.menu(agent_of(self.w, "jarvis"), "mic", self.cell("jarvis", "mic"), p)
        self.assertEqual([o.token for o in m.options], ["queue:off"], "the shared-voice switch is gone")

    def test_the_read_only_cells_say_why(self):
        p = self.w.paths
        for key, place, word in (("kylie", "iphone", "Siri"), ("kylie", "terminal", "voice.auto_enable"),
                                 ("codex", "terminal", "nothing in"), ("codex", "tg_group", "nothing in")):
            m = onoff.menu(agent_of(self.w, key), place, self.cell(key, place), p)
            self.assertEqual(m.options, [], (key, place))
            self.assertIn(word, m.why_not, (key, place))
            self.assertFalse(onoff.can_switch(agent_of(self.w, key), place), (key, place))

    def test_telegram_direct_and_gemini_are_switchable_since_step_3(self):
        p = self.w.paths
        for key, place in (("kylie", "tg_dm"), ("jarvis", "tg_dm"), ("gemini", "terminal")):
            self.assertTrue(onoff.can_switch(agent_of(self.w, key), place), (key, place))
            m = onoff.menu(agent_of(self.w, key), place, self.cell(key, place), p)
            self.assertTrue(m.options, (key, place))
        m = onoff.menu(agent_of(self.w, "kylie"), "tg_dm", self.cell("kylie", "tg_dm"), p)
        self.assertEqual([o.token for o in m.options], ["off", "voice_back", "always"])
        m = onoff.menu(agent_of(self.w, "gemini"), "terminal", self.cell("gemini", "terminal"), p)
        self.assertEqual([o.token for o in m.options], ["off", "always"])

    def test_set_cell_refuses_a_read_only_cell_and_changes_nothing(self):
        before = self.w.toml.read_text()
        res = self.switch("kylie", "iphone", "off")
        self.assertFalse(res.ok)
        self.assertIn("Siri", res.message)
        res = self.switch("codex", "terminal", "off")
        self.assertFalse(res.ok)
        self.assertEqual(self.w.toml.read_text(), before)
        self.assertEqual(VE.read_history(self.w.edit), [])


class TheTeamGroup(Base):
    def test_off_takes_only_that_agent_off_and_keeps_the_file_layout(self):
        before = self.w.paths.mirror_config.read_text()
        res = self.switch("kylie", "tg_group", "off")
        self.assertTrue(res.ok, res.message)
        data = mirror(self.w)
        self.assertEqual(data["voice_notes"]["agents"], ["orchestrator", "tax_angel_esther", "wanna-buffet"])
        self.assertIs(data["voice_notes"]["on"], True, "the others still speak")
        self.assertEqual(self.w.paths.mirror_config.read_text(),
                         json.dumps(data, indent=1, sort_keys=True), "same layout as the team room writes")
        self.assertEqual(self.cell("kylie", "tg_group").value, G.OFF)
        self.assertEqual(self.cell("jarvis", "tg_group").value, G.ALWAYS)
        backup = Path(res.entry["files"][0]["backup"])
        self.assertEqual(backup.read_text(), before, "the backup is the file as it was")
        self.assertEqual(VE.recent(self.w.edit, 1)[0].split("  ", 1)[1],
                         "Kylie Telegram group voice notes: Always → Off")

    def test_undo_puts_the_name_back_in_the_same_place_in_the_list(self):
        before = self.w.paths.mirror_config.read_text()
        self.assertTrue(self.switch("kylie", "tg_group", "off").ok)
        self.assertNotEqual(self.w.paths.mirror_config.read_text(), before)
        self.assertTrue(VE.undo(self.w.edit, self.w.paths).ok)
        self.assertEqual(self.w.paths.mirror_config.read_text(), before, "byte for byte, Kylie second in the list again")

    def test_always_adds_the_agent_and_is_a_no_op_when_already_so(self):
        self.switch("kylie", "tg_group", "off")
        res = self.switch("kylie", "tg_group", "always")
        self.assertTrue(res.ok, res.message)
        self.assertIn("kylie", [a.key for a in agents.agents(agents.load(self.w.toml))])
        self.assertIn("assistant", mirror(self.w)["voice_notes"]["agents"])
        n = len(VE.read_history(self.w.edit))
        again = self.switch("kylie", "tg_group", "always")
        self.assertTrue(again.ok)
        self.assertIn("nothing to change", again.message)
        self.assertEqual(len(VE.read_history(self.w.edit)), n, "no history line for a no-op")

    def test_always_when_the_whole_group_was_off_turns_it_on_and_says_who_else_comes_on(self):
        self.write_mirror({"chat_id": "-1", "enabled": True, "voice_notes": {"agents": ["orchestrator"], "on": False}})
        before = self.w.paths.mirror_config.read_text()
        res = self.switch("kylie", "tg_group", "always")
        self.assertTrue(res.ok, res.message)
        data = mirror(self.w)
        self.assertIs(data["voice_notes"]["on"], True)
        self.assertEqual(data["voice_notes"]["agents"], ["orchestrator", "assistant"])
        self.assertIn("orchestrator", res.message, "Jarvis comes on too and Peter is told")
        self.assertEqual(self.cell("jarvis", "tg_group").value, G.ALWAYS)
        undo = VE.undo(self.w.edit, self.w.paths)
        self.assertTrue(undo.ok, undo.message)
        self.assertEqual(self.w.paths.mirror_config.read_text(), before, "undo puts both the flag and the list back")

    def test_a_missing_voice_notes_block_is_created_and_undo_removes_what_it_made(self):
        self.write_mirror({"chat_id": "-1", "enabled": True})
        res = self.switch("kylie", "tg_group", "always")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(mirror(self.w)["voice_notes"], {"agents": ["assistant"], "on": True})
        self.assertEqual(self.cell("kylie", "tg_group").value, G.ALWAYS)
        self.assertTrue(VE.undo(self.w.edit, self.w.paths).ok)
        self.assertEqual(self.cell("kylie", "tg_group").value, G.OFF)

    def test_voice_back_is_refused_for_the_group(self):
        res = self.switch("kylie", "tg_group", "voice_back")
        self.assertFalse(res.ok)
        self.assertIn("Off and Always only", res.message)

    def test_undo_keeps_a_hand_edit_made_to_the_same_file_meanwhile(self):
        self.switch("kylie", "tg_group", "off")
        data = mirror(self.w)
        data["chat_only_accounts"] = {"1": "iPad"}                 # Peter edits the file by hand
        self.write_mirror(data)
        res = VE.undo(self.w.edit, self.w.paths)
        self.assertTrue(res.ok, res.message)
        back = mirror(self.w)
        self.assertIn("assistant", back["voice_notes"]["agents"])
        self.assertEqual(back["chat_only_accounts"], {"1": "iPad"}, "the hand edit survives the undo")

    def test_an_unreadable_mirror_file_changes_nothing(self):
        self.w.paths.mirror_config.write_text("{ not json")
        res = self.switch("kylie", "tg_group", "off")
        self.assertFalse(res.ok)
        self.assertEqual(self.w.paths.mirror_config.read_text(), "{ not json")
        self.assertEqual(VE.read_history(self.w.edit), [])


class TheClaudeTerminal(Base):
    def files(self):
        return {n: p.read_text() for n, p in (("settings", self.settings), ("conf", self.conf), ("toml", self.w.toml))}

    def test_off_switches_every_place_a_hook_would_switch_it_back_on_from(self):
        before = self.files()
        hook = self.w.paths.claude_hook.read_text()
        self.assertEqual(jq_says(self.settings), "true")
        res = self.switch("claude", "terminal", "off")
        self.assertTrue(res.ok, res.message)
        data = json.loads(self.settings.read_text())
        self.assertIs(data["voice"]["enabled"], False)
        self.assertIs(data["voiceEnabled"], False, "jq's `//` skips a false, so a left-over voiceEnabled=true would keep it on")
        self.assertEqual(data["voice"]["mode"], "tap")
        self.assertEqual(data["model"], "sonnet", "nothing else in settings.json moves")
        self.assertEqual(jq_says(self.settings), "false", "the Stop hook's own test now says off")
        self.assertIn("VOICE_ALWAYS_ON=0", self.conf.read_text())
        self.assertIn("always_on = false", self.w.toml.read_text())
        self.assertEqual(self.cell("claude", "terminal").value, G.OFF)
        self.assertEqual(self.w.paths.claude_hook.read_text(), hook, "~/.claude/hooks is not touched")
        self.assertEqual([i for i in voicecfg.drift(voicecfg.collect(agents.load(self.w.toml), self.w.paths.profiles, self.conf))
                          if i.where == "claude"], [], "voicecfg check sees no drift for Claude")
        for entry in res.entry["files"]:
            self.assertTrue(Path(entry["backup"]).exists(), entry)
        self.assertEqual(Path([f for f in res.entry["files"] if f["path"].endswith("settings.json")][0]["backup"]).read_text(),
                         before["settings"])
        self.assertEqual(VE.recent(self.w.edit, 1)[0].split("  ", 1)[1], "Claude terminal voice: Always → Off")
        undo = VE.undo(self.w.edit, self.w.paths)
        self.assertTrue(undo.ok, undo.message)
        self.assertEqual(self.files(), before, "undo puts all three files back byte for byte")
        self.assertEqual(jq_says(self.settings), "true")
        self.assertEqual(self.cell("claude", "terminal").value, G.ALWAYS)

    def test_the_grid_reads_claude_the_way_the_hook_does(self):
        data = json.loads(self.settings.read_text())
        data["voice"]["enabled"] = False                           # the legacy key still says true: the hook speaks
        self.settings.write_text(json.dumps(data, indent=2) + "\n")
        self.assertEqual(jq_says(self.settings), "true")
        self.assertEqual(self.cell("claude", "terminal").value, G.ALWAYS)

    def test_always_brings_it_back(self):
        self.assertTrue(self.switch("claude", "terminal", "off").ok)
        res = self.switch("claude", "terminal", "always")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(jq_says(self.settings), "true")
        self.assertIn("VOICE_ALWAYS_ON=1", self.conf.read_text())
        self.assertIn("always_on = true", self.w.toml.read_text())
        self.assertTrue(self.switch("claude", "terminal", "always").message.endswith("nothing to change"))

    def test_without_the_defaults_file_nothing_is_changed(self):
        self.conf.unlink()
        before = (self.settings.read_text(), self.w.toml.read_text())
        res = self.switch("claude", "terminal", "off")
        self.assertFalse(res.ok)
        self.assertIn("voice-defaults.conf", res.message)
        self.assertEqual((self.settings.read_text(), self.w.toml.read_text()), before)

    def test_a_missing_voiceenabled_key_is_not_invented(self):
        self.settings.write_text(json.dumps({"voice": {"enabled": True, "mode": "tap"}}, indent=2) + "\n")
        res = self.switch("claude", "terminal", "off")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(json.loads(self.settings.read_text()), {"voice": {"enabled": False, "mode": "tap"}})

    def test_a_failed_read_back_puts_every_file_back_and_logs_the_failure(self):
        before = self.files()
        real = onoff._verify_file

        def verify(path, changes):
            return ["bash reads VOICE_ALWAYS_ON as '1', wanted '0'"] if path.name == "voice-defaults.conf" else real(path, changes)

        with mock.patch.object(onoff, "_verify_file", verify):
            res = self.switch("claude", "terminal", "off")
        self.assertFalse(res.ok)
        self.assertIn("put back", res.message)
        self.assertEqual(self.files(), before, "settings, conf and agents.toml are all as they were")
        hist = VE.read_history(self.w.edit)
        self.assertEqual(len(hist), 1)
        self.assertFalse(hist[0]["ok"])
        self.assertIn("failed", VE.describe(hist[0]))
        self.assertIsNone(VE.last_undoable(self.w.edit), "a failed switch is not undoable")

    def test_if_the_write_itself_blows_up_the_files_already_written_are_put_back(self):
        before = self.files()
        real = VE._atomic_write
        calls = []

        def flaky(path, text):
            calls.append(path.name)
            if path.name == "settings.json" and len(calls) == 3:   # the third file; the first two are already written
                raise OSError("disk full")
            return real(path, text)

        with mock.patch.object(VE, "_atomic_write", flaky):
            res = self.switch("claude", "terminal", "off")
        self.assertFalse(res.ok)
        self.assertEqual(self.files(), before)


class TheMicWindow(Base):
    def test_the_shared_voice_switch_is_gone_and_an_old_history_line_for_it_is_not_undone(self):
        # Peter 2026-11-11: no shared voice, so the "own voices / shared voice" switch (V) is gone everywhere
        self.assertFalse(self.switch("jarvis", "mic", "own_voices:off").ok)
        self.assertFalse((self.w.paths.state / "voices_per_profile").exists())
        self.assertNotIn("shared voice", self.cell("kylie", "mic").note)
        VE._append_history(self.w.edit, {"id": "old-1", "ts": 1.0, "kind": "switch", "agent": "jarvis",
                                         "display": "Voice Desk (all agents)", "field": "switch:mic",
                                         "what": "Voice Desk switch", "old": "own_voices:off", "new": "own_voices:on",
                                         "old_label": "x", "new_label": "y", "ok": True, "changes": []})
        self.assertIsNone(VE.last_undoable(self.w.edit), "U never acts on the retired switch")
        import watcher
        self.assertEqual(watcher.intents(VE.read_history(self.w.edit)), {}, "the watcher does not try to keep it")

    def test_the_speech_queue_off_file(self):
        off = self.w.paths.speech_dir / "off"
        res = self.switch("claude", "mic", "queue:off")
        self.assertTrue(res.ok, res.message)
        self.assertTrue(off.exists())
        self.assertIn("speech queue is off", self.cell("kylie", "mic").note)
        again = self.switch("claude", "mic", "queue:off")
        self.assertIn("nothing to change", again.message)
        self.assertTrue(self.switch("kylie", "mic", "queue:on").ok)
        self.assertFalse(off.exists())

    def test_an_unknown_switch_is_refused(self):
        res = self.switch("kylie", "mic", "queue:maybe")
        self.assertFalse(res.ok)


class TheSharedHistory(Base):
    def test_u_undoes_whichever_change_was_newest_a_voice_or_a_switch(self):
        toml0 = self.w.toml.read_text()
        self.assertTrue(VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit).ok)
        self.assertTrue(self.switch("kylie", "tg_group", "off").ok)
        lines = VE.recent(self.w.edit, 2)
        self.assertIn("Telegram group voice notes", lines[0])
        self.assertIn("English voice", lines[1])
        self.assertTrue(VE.undo(self.w.edit, self.w.paths).ok)
        self.assertEqual(self.cell("kylie", "tg_group").value, G.ALWAYS, "the switch came back first")
        self.assertEqual(agent_of(self.w, "kylie").voice, "en-US-AriaNeural", "the voice is still the changed one")
        self.assertTrue(VE.undo(self.w.edit, self.w.paths).ok)
        self.assertEqual(self.w.toml.read_text(), toml0)
        self.assertEqual(VE.undo(self.w.edit, self.w.paths).message, "nothing to undo")


if __name__ == "__main__":
    unittest.main()
