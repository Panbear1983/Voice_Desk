"""Offline tests for changing a voice from the panel: the line editor for agents.toml, the whole
change (backup -> write -> voicecfg sync -> Hermes read-back), undo and the history. Works on copies
in a temp folder; Peter's real agents.toml and profiles are never touched.

    .venv/bin/python -m unittest tests.test_voiceedit -v
"""
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import voicecfg  # noqa: E402
import voiceedit as VE  # noqa: E402


def agent_of(world, key):
    return next(a for a in agents.agents(agents.load(world.toml)) if a.key == key)


class TheLineEditor(unittest.TestCase):
    def test_replaces_one_line_and_keeps_the_comment_and_everything_else(self):
        out = VE.set_field(F.AGENTS_TOML, "kylie", "voice", "en-US-AriaNeural")
        self.assertIn('voice = "en-US-AriaNeural"            # Kylie\'s English voice', out)
        before, after = F.AGENTS_TOML.splitlines(), out.splitlines()
        self.assertEqual(len(before), len(after))
        self.assertEqual([i for i, (a, b) in enumerate(zip(before, after)) if a != b], [after.index(
            'voice = "en-US-AriaNeural"            # Kylie\'s English voice')])

    def test_a_voice_in_one_agent_never_changes_another(self):
        out = VE.set_field(F.AGENTS_TOML, "jarvis", "voice", "en-US-GuyNeural")
        cfg = tomllib.loads(out)["agents"]
        self.assertEqual(cfg["jarvis"]["voice"], "en-US-GuyNeural")
        self.assertEqual(cfg["kylie"]["voice"], "en-US-AvaNeural")
        self.assertEqual(cfg["claude"]["voice"], "en-US-JennyNeural")

    def test_inserts_a_missing_key_after_the_last_statement_even_past_a_multi_line_array(self):
        out = VE.set_field(F.AGENTS_TOML, "esther", "voice_override", "zh")
        d = tomllib.loads(out)["agents"]["esther"]
        self.assertEqual(d["voice_override"], "zh")
        self.assertEqual(d["command"], ["/nonexistent/python", "/nonexistent/esther_tts.py"], "the array is intact")
        lines = out.splitlines()
        i = lines.index('voice_override = "zh"')
        self.assertEqual(lines[i - 1], "barge_in = false")

    def test_an_inline_table_for_extra_languages_and_removing_it_again(self):
        out = VE.set_field(F.AGENTS_TOML, "kylie", "voices_extra", {"ja": "ja-JP-NanamiNeural", "fr": "fr-FR-DeniseNeural"})
        self.assertEqual(tomllib.loads(out)["agents"]["kylie"]["voices_extra"], {"ja": "ja-JP-NanamiNeural", "fr": "fr-FR-DeniseNeural"})
        back = VE.set_field(out, "kylie", "voices_extra", None)
        self.assertEqual(back, F.AGENTS_TOML, "removing what was added gives the original text back, byte for byte")

    def test_a_block_that_does_not_exist_is_refused(self):
        with self.assertRaises(VE.EditError):
            VE.set_field(F.AGENTS_TOML, "nobody", "voice", "en-US-AvaNeural")

    def test_the_last_agent_in_a_file_without_a_final_newline(self):
        text = F.AGENTS_TOML.rstrip("\n")
        out = VE.set_field(text, "wanna_buffet", "voice_override", "en")
        self.assertEqual(tomllib.loads(out)["agents"]["wanna_buffet"]["voice_override"], "en")


class TheValidation(unittest.TestCase):
    def setUp(self):
        self.w = F.make_world()
        self.kylie, self.esther = agent_of(self.w, "kylie"), agent_of(self.w, "esther")

    def test_english_voice_must_be_an_english_edge_voice(self):
        self.assertEqual(VE.check_voice("voice", " en-US-AriaNeural ", self.kylie), "en-US-AriaNeural")
        for bad in ("zh-TW-HsiaoChenNeural", "Aria", "", "en-US-Aria Neural", 'en-US-"x"Neural'):
            with self.assertRaises(VE.EditError, msg=bad):
                VE.check_voice("voice", bad, self.kylie)

    def test_a_kokoro_name_is_allowed_only_for_the_agent_with_a_command(self):
        self.assertEqual(VE.check_voice("voice", "bella", self.esther), "bella")
        with self.assertRaises(VE.EditError):
            VE.check_voice("voice", "bella", self.kylie)

    def test_mandarin_voice_must_be_zh(self):
        self.assertEqual(VE.check_voice("voice_zh", "zh-TW-HsiaoYuNeural", self.kylie), "zh-TW-HsiaoYuNeural")
        with self.assertRaises(VE.EditError):
            VE.check_voice("voice_zh", "en-US-AvaNeural", self.kylie)

    def test_extra_language_code_and_voice_must_agree(self):
        self.assertEqual(VE.check_voice("voices_extra", VE.parse_extra("ja=ja-JP-NanamiNeural"), self.kylie), {"ja": "ja-JP-NanamiNeural"})
        for bad in ("ja=fr-FR-DeniseNeural", "en=en-US-AvaNeural", "japanese=ja-JP-NanamiNeural", "ja"):
            with self.assertRaises(VE.EditError, msg=bad):
                VE.check_voice("voices_extra", VE.parse_extra(bad), self.kylie)

    def test_override_must_name_a_language_the_agent_has(self):
        self.assertEqual(VE.check_voice("voice_override", "ZH", self.kylie), "zh")
        with self.assertRaises(VE.EditError):
            VE.check_voice("voice_override", "ja", self.kylie)


class TheChange(unittest.TestCase):
    def setUp(self):
        self.w = F.make_world()
        self.cfg_yaml = self.w.paths.profiles / "assistant/config.yaml"

    def test_changing_kylies_voice_writes_toml_and_her_profile_with_backups_and_history(self):
        before_yaml = self.cfg_yaml.read_text()
        res = VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(agent_of(self.w, "kylie").voice, "en-US-AriaNeural")
        yaml = self.cfg_yaml.read_text()
        self.assertEqual(yaml.count("en-US-AriaNeural"), 2, "the active provider's voice and tts.edge.voice")
        self.assertEqual(yaml.replace("en-US-AriaNeural", "en-US-AvaNeural"), before_yaml.replace("silence_threshold: 300", "silence_threshold: 300"))
        self.assertEqual(len(res.entry["files"]), 1)
        backup = Path(res.entry["files"][0]["backup"])
        self.assertEqual(backup.read_text(), before_yaml, "the backup is the file as it was before")
        self.assertEqual(Path(res.entry["toml_backup"]).read_text(), F.AGENTS_TOML)
        self.assertEqual(VE.recent(self.w.edit, 1)[0].split("  ", 1)[1], "Kylie English voice: Ava → Aria")

    def test_the_profile_is_read_back_by_hermes_own_parser(self):
        if not voicecfg.HERMES_PY.exists():
            self.skipTest("no Hermes install here")
        VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        data = voicecfg.hermes_readback(self.cfg_yaml)
        self.assertEqual(voicecfg.dig(data, "tts.providers.natural.voice"), "en-US-AriaNeural")

    def test_only_the_voice_lines_change_other_drift_is_left_alone(self):
        # the fixture's Kylie profile already has silence_threshold 300 (agents.toml says 200): not this change's business
        VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertIn("silence_threshold: 300", self.cfg_yaml.read_text())
        for prof in ("orchestrator", "tax_angel_esther", "wanna-buffet"):
            self.assertIn("voice:", (self.w.paths.profiles / prof / "config.yaml").read_text())
        self.assertIn("en-US-JennyNeural", (self.w.paths.profiles / "orchestrator/config.yaml").read_text())

    def test_claude_english_and_mandarin_go_to_the_voice_defaults_file(self):
        conf = self.w.edit.claude_conf
        res = VE.change("claude", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"        # English', conf.read_text())
        res = VE.change("claude", "voice_zh", "zh-TW-HsiaoYuNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertIn('NEURAL_VOICE_ZH="zh-TW-HsiaoYuNeural"', conf.read_text())
        self.assertIn("VOICE_MODE=\"tap\"", conf.read_text())

    def test_a_mandarin_voice_for_a_hermes_agent_is_stored_where_voice_desk_reads_it(self):
        before = self.cfg_yaml.read_text()
        res = VE.change("kylie", "voice_zh", "zh-TW-HsiaoYuNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(agent_of(self.w, "kylie").voice_zh, "zh-TW-HsiaoYuNeural")
        self.assertEqual(self.cfg_yaml.read_text(), before, "no Hermes setting holds a Mandarin voice")
        self.assertEqual(res.changed_files, [])

    def test_extra_languages_and_override_are_stored_and_can_be_cleared(self):
        self.assertTrue(VE.change("kylie", "voices_extra", {"ja": "ja-JP-NanamiNeural"}, self.w.edit).ok)
        self.assertEqual(agent_of(self.w, "kylie").voices_extra, {"ja": "ja-JP-NanamiNeural"})
        self.assertTrue(VE.change("kylie", "voice_override", "ja", self.w.edit).ok)
        self.assertEqual(agent_of(self.w, "kylie").voice_override, "ja")
        self.assertTrue(VE.change("kylie", "voices_extra", {}, self.w.edit).ok)
        self.assertEqual(agent_of(self.w, "kylie").voices_extra, {})

    def test_the_same_value_changes_nothing_and_writes_no_history(self):
        res = VE.change("kylie", "voice", "en-US-AvaNeural", self.w.edit)
        self.assertTrue(res.ok)
        self.assertIn("nothing to change", res.message)
        self.assertEqual(VE.read_history(self.w.edit), [])
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    def test_a_refused_value_changes_nothing(self):
        res = VE.change("kylie", "voice", "zh-TW-HsiaoChenNeural", self.w.edit)
        self.assertFalse(res.ok)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        self.assertEqual(VE.read_history(self.w.edit), [])

    def test_gemini_and_codex_have_nothing_the_panel_can_change_yet(self):
        for key in ("gemini", "codex"):
            res = VE.change(key, "voice", "en-US-AriaNeural", self.w.edit)
            self.assertFalse(res.ok)
            self.assertIn("shared Voice Desk voice", res.message)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    def test_if_the_apply_fails_agents_toml_is_put_back_and_the_failure_is_logged(self):
        with mock.patch.object(voicecfg, "apply", return_value=([], ["profile: restored from backup, did not read back"])):
            res = VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertFalse(res.ok)
        self.assertIn("put back", res.message)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        hist = VE.read_history(self.w.edit)
        self.assertEqual(len(hist), 1)
        self.assertFalse(hist[0]["ok"])
        self.assertIsNone(VE.last_undoable(self.w.edit), "a failed change is not something to undo")

    def test_if_the_apply_blows_up_agents_toml_is_put_back_too(self):
        with mock.patch.object(voicecfg, "apply", side_effect=RuntimeError("disk full")):
            res = VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertFalse(res.ok)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    def test_if_agents_toml_changes_while_preparing_nothing_is_written(self):
        real = VE.set_field

        def sneaky(text, *a, **k):
            self.w.toml.write_text(text + "\n# someone else edited this\n")
            return real(text, *a, **k)

        with mock.patch.object(VE, "set_field", sneaky):
            res = VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertFalse(res.ok)
        self.assertIn("someone else edited this", self.w.toml.read_text())
        self.assertEqual(self.cfg_yaml.read_text().count("en-US-AriaNeural"), 0)


class TheUndo(unittest.TestCase):
    def setUp(self):
        self.w = F.make_world()
        self.cfg_yaml = self.w.paths.profiles / "assistant/config.yaml"

    def test_undo_puts_the_profile_and_agents_toml_back_and_keeps_a_new_backup(self):
        original_yaml, original_toml = self.cfg_yaml.read_text(), self.w.toml.read_text()
        VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        res = VE.undo(self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.cfg_yaml.read_text(), original_yaml)
        self.assertEqual(self.w.toml.read_text(), original_toml)
        hist = VE.read_history(self.w.edit)
        self.assertEqual([h["kind"] for h in hist], ["change", "undo"])
        self.assertEqual(hist[1]["undoes"], hist[0]["id"])
        self.assertIn("undo: Kylie English voice: Aria → Ava", VE.recent(self.w.edit, 1)[0])

    def test_undo_twice_walks_back_through_changes_and_then_says_nothing_to_undo(self):
        import datetime as dt
        t = dt.datetime(2026, 11, 9, 12, 0, 0)
        VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit, now=t)
        VE.change("kylie", "voice", "en-US-EmmaNeural", self.w.edit, now=t + dt.timedelta(minutes=1))
        self.assertEqual(agent_of(self.w, "kylie").voice, "en-US-EmmaNeural")
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(agent_of(self.w, "kylie").voice, "en-US-AriaNeural")
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(agent_of(self.w, "kylie").voice, "en-US-AvaNeural")
        res = VE.undo(self.w.edit)
        self.assertFalse(res.ok)
        self.assertEqual(res.message, "nothing to undo")

    def test_undo_does_not_throw_away_a_later_unrelated_change_in_the_same_file(self):
        VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        self.cfg_yaml.write_text(self.cfg_yaml.read_text().replace("silence_duration: 1.5", "silence_duration: 2.5"))
        self.assertTrue(VE.undo(self.w.edit).ok)
        text = self.cfg_yaml.read_text()
        self.assertIn("silence_duration: 2.5", text, "the hand edit made after the change survives the undo")
        self.assertIn("voice: en-US-AvaNeural", text)

    def test_undo_of_an_added_setting_removes_it(self):
        VE.change("kylie", "voice_override", "zh", self.w.edit)
        self.assertIn("voice_override", self.w.toml.read_text())
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    def test_history_survives_a_garbage_line(self):
        VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        with open(self.w.edit.history, "a") as fh:
            fh.write("not json\n")
        self.assertEqual(len(VE.read_history(self.w.edit)), 1)


if __name__ == "__main__":
    unittest.main()
