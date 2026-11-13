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

    def test_mandarin_voice_must_be_taiwan_mandarin(self):
        # Peter 2026-11-09: Taiwan Mandarin accent for Chinese, no Mainland accent
        for bad in ("zh-CN-XiaoxiaoNeural", "zh-HK-HiuGaaiNeural"):
            with self.assertRaises(VE.EditError, msg=bad) as cm:
                VE.check_voice("voice_zh", bad, self.kylie)
            self.assertIn("Taiwan Mandarin", str(cm.exception))

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

    def test_gemini_and_codex_can_be_changed_now(self):
        # step 5: both get their own voice; before, the panel refused and they spoke with the shared Jenny
        for key in ("gemini", "codex"):
            self.assertTrue(VE.editable(agent_of(self.w, key)), key)

    def test_a_gemini_voice_goes_into_agents_toml_and_gemini_hooks_file_with_backup_and_history(self):
        conf = self.w.edit.gemini_conf
        before = conf.read_text()
        res = VE.change("gemini", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(agent_of(self.w, "gemini").voice, "en-US-AriaNeural")
        self.assertIn('[agents.gemini]\ntarget = "gemini"\ndisplay = "Gemini"\nnames = ["gemini"]\nvoice = "en-US-AriaNeural"\n',
                      self.w.toml.read_text(), "one line added at the end of Gemini's block")
        text = conf.read_text()
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', text)
        self.assertIn('NEURAL_VOICE_ZH="zh-TW-HsiaoChenNeural"', text,
                      "no Mandarin voice in agents.toml: that line is not managed, so it is left exactly as it was")
        self.assertEqual(text.replace("en-US-AriaNeural", "en-US-EmmaNeural"), before, "only the English line changed")
        self.assertEqual(res.changed_files, [str(conf)])
        self.assertEqual(Path(res.entry["files"][0]["backup"]).read_text(), before)
        self.assertEqual(VE.recent(self.w.edit, 1)[0].split("  ", 1)[1], "Gemini English voice: not set → Aria")
        res = VE.change("gemini", "voice_zh", "zh-TW-HsiaoChenNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertIn('NEURAL_VOICE_ZH="zh-TW-HsiaoChenNeural"', conf.read_text())
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', conf.read_text())

    def test_gemini_hooks_file_is_read_back_with_bash_and_put_back_if_it_does_not_read_right(self):
        before_conf, before_toml = self.w.edit.gemini_conf.read_text(), self.w.toml.read_text()
        with mock.patch.object(voicecfg, "_verify", return_value=["bash reads NEURAL_VOICE_EN as 'x'"]):
            res = VE.change("gemini", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertFalse(res.ok)
        self.assertEqual(self.w.edit.gemini_conf.read_text(), before_conf)
        self.assertEqual(self.w.toml.read_text(), before_toml)

    def test_a_missing_gemini_file_is_made_on_the_first_change(self):
        self.w.edit.gemini_conf.unlink()
        res = VE.change("gemini", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        text = self.w.edit.gemini_conf.read_text()
        self.assertTrue(text.startswith("# Gemini's spoken-reply voices."), text)
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', text)
        self.assertNotIn("NEURAL_VOICE_ZH", text, "no Mandarin voice in agents.toml: no line is invented for it")
        self.assertEqual(res.entry["files"][0]["backup"], voicecfg.NEW_FILE)

    def test_a_codex_voice_lives_in_agents_toml_only_and_voice_desk_speaks_with_it(self):
        import voices
        res = VE.change("codex", "voice", "en-GB-RyanNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.changed_files, [])
        self.assertIn("Voice Desk reads it", res.message)
        self.assertTrue(VE.change("codex", "voice_zh", "zh-TW-YunJheNeural", self.w.edit).ok)
        with mock.patch.object(agents, "PATH", self.w.toml):
            v = voices.voice_for("codex")
        self.assertEqual((v.en, v.zh, v.source), ("en-GB-RyanNeural", "zh-TW-YunJheNeural", "agents"))

    def test_a_voice_set_to_silent_is_removed_and_undone(self):
        import voices
        res = VE.change("kylie", "voice_zh", None, self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertIsNone(agent_of(self.w, "kylie").voice_zh)
        self.assertIn("silent", res.message)
        with mock.patch.object(agents, "PATH", self.w.toml):
            self.assertEqual(voices.voice_for("hermes:assistant").zh, "", "no Mandarin voice: Chinese stays silent")
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    def test_gemini_and_codex_refuse_what_is_not_an_edge_voice_of_the_right_language(self):
        for key, field, bad in (("gemini", "voice", "alloy"), ("codex", "voice", "zh-TW-YunJheNeural"),
                                ("codex", "voice_zh", "en-GB-RyanNeural")):
            res = VE.change(key, field, bad, self.w.edit)
            self.assertFalse(res.ok, (key, field, bad))
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        self.assertEqual(VE.read_history(self.w.edit), [])

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

    def test_undo_of_gemini_and_codex_voices_puts_everything_back_exactly(self):
        import datetime as dt
        t = dt.datetime(2026, 11, 9, 12, 0, 0)
        conf_before = self.w.edit.gemini_conf.read_text()
        VE.change("gemini", "voice", "en-US-AriaNeural", self.w.edit, now=t)
        VE.change("gemini", "voice_zh", "zh-TW-HsiaoYuNeural", self.w.edit, now=t + dt.timedelta(seconds=1))
        VE.change("codex", "voice", "en-GB-RyanNeural", self.w.edit, now=t + dt.timedelta(seconds=2))
        for _ in range(3):
            res = VE.undo(self.w.edit)
            self.assertTrue(res.ok, res.message)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML, "the added voice lines are gone again")
        self.assertEqual(self.w.edit.gemini_conf.read_text(), conf_before,
                         "the lines the change wrote into Gemini's hook file are put back from its backup")
        self.assertIn("undo: Gemini English voice: Aria → not set", VE.recent(self.w.edit, 1)[0])

    def test_history_survives_a_garbage_line(self):
        VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)
        with open(self.w.edit.history, "a") as fh:
            fh.write("not json\n")
        self.assertEqual(len(VE.read_history(self.w.edit)), 1)


if __name__ == "__main__":
    unittest.main()


class KokoroForAnyAgent(unittest.TestCase):
    """Peter 2026-11-11: Kokoro (the local voice, no internet) is Claude's default and a choice for any agent.
    Picking a Kokoro name switches the agent to exactly that voice. No Edge voice is kept as a fallback any more
    (one voice, no fallback): the agent's Hermes profile line is simply not managed while its voice is Kokoro."""

    def setUp(self):
        self.w = F.make_world()

    def voice_desk_voice(self, target):
        import voices
        with mock.patch.object(agents, "PATH", self.w.toml):
            return voices.voice_for(target)

    def test_a_kokoro_name_is_a_valid_english_voice_for_any_agent(self):
        for key in ("kylie", "claude", "gemini", "codex", "jarvis"):
            self.assertEqual(VE.check_voice("voice", "af_heart", agent_of(self.w, key)), "af_heart", key)
        with self.assertRaises(VE.EditError):
            VE.check_voice("voice", "af_nobody", agent_of(self.w, "kylie"))

    def test_kylie_switched_to_kokoro_gets_no_fallback_and_her_hermes_profile_is_left_alone(self):
        yaml = self.w.paths.profiles / "assistant/config.yaml"
        before = yaml.read_text()
        res = VE.change("kylie", "voice", "bf_emma", self.w.edit)
        self.assertTrue(res.ok, res.message)
        k = agent_of(self.w, "kylie")
        self.assertEqual(k.voice, "bf_emma")
        self.assertNotIn("fallback", k.raw, "no fallback line is written any more")
        self.assertEqual(yaml.read_text(), before, "Hermes' own config never gets a Kokoro name")
        v = self.voice_desk_voice("hermes:assistant")
        self.assertEqual((v.en, v.command), ("bf_emma", agents.KOKORO_COMMAND))

    def test_claude_switched_to_kokoro_and_back_sets_the_engine_in_the_voice_defaults_file(self):
        conf = self.w.edit.claude_conf
        res = VE.change("claude", "voice", "af_heart", self.w.edit)
        self.assertTrue(res.ok, res.message)
        text = conf.read_text()
        for line in ('ENGLISH_ENGINE="kokoro"', 'KOKORO_VOICE_EN="af_heart"'):
            self.assertIn(line, text)
        self.assertIn(conf, [Path(p) for p in res.changed_files])
        res = VE.change("claude", "voice", "en-US-AriaNeural", self.w.edit)
        self.assertTrue(res.ok, res.message)
        text = conf.read_text()
        self.assertIn('ENGLISH_ENGINE="edge"', text)
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', text)

    def test_undo_of_a_kokoro_switch_puts_the_edge_voice_back(self):
        self.assertTrue(VE.change("claude", "voice", "af_heart", self.w.edit).ok)
        res = VE.undo(self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(agent_of(self.w, "claude").voice, "en-US-JennyNeural")
        self.assertIn('ENGLISH_ENGINE="edge"', self.w.edit.claude_conf.read_text())

    def test_gemini_on_kokoro_writes_nothing_to_its_edge_only_hook_file(self):
        self.assertTrue(VE.change("gemini", "voice", "en-US-AriaNeural", self.w.edit).ok)
        res = VE.change("gemini", "voice", "am_adam", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertNotIn("am_adam", self.w.edit.gemini_conf.read_text(), "its hook speaks Edge only")
        self.assertEqual(self.voice_desk_voice("gemini").en, "am_adam")
