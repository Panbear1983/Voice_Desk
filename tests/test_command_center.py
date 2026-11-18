"""The command-center build, stage 1 (brief 2026-11-13, ~/.hermes/profiles/orchestrator/jarvis/briefs/
2026-11-13-voice-desk-command-center.md): the voice bank, Simba's row as a Hermes agent, and what each place can really
do for each agent. Fakes only: a throw-away world (tests/gridfix.py) and the fake bank in tests/__init__.py. Nothing
is played, sent or started.

    .venv/bin/python -m unittest tests.test_command_center -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import gridcan  # noqa: E402
import gridevidence as E  # noqa: E402
import gridstate as G  # noqa: E402
import onoff  # noqa: E402
import voicebank as B  # noqa: E402
import voicecfg  # noqa: E402
import voices  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

SIMBA_BLOCK = '''
[agents.simba]
profile = "simba"
display = "Simba"
names = ["simba"]
voice = "am_fenrir"
no_fallback = true
effect = "16 pitch=1.4"
english_only = true
command = ["/nonexistent/python", "/nonexistent/simba_tts.py"]
'''
# her real profile has no `voice:` block and speaks through her own command provider (checked 2026-11-13)
SIMBA_YAML = ("model:\n  provider: openrouter\ntelegram:\n  require_mention: true\ntts:\n  provider: simba_local\n"
              "  providers:\n    simba_local:\n      type: command\n      command: /x/tts_simba.sh {input_path} "
              "{output_path} {voice}\n      voice: am_fenrir\n      timeout: 60\n")


def simba_world():
    w = F.make_world()
    w.toml.write_text(F.AGENTS_TOML + SIMBA_BLOCK)
    (w.paths.profiles / "simba/logs").mkdir(parents=True)
    (w.paths.profiles / "simba/config.yaml").write_text(SIMBA_YAML)
    return w


def agent(w, key):
    return next(a for a in agents.agents(agents.load(w.toml)) if a.key == key)


def snapshot(root: Path) -> dict:
    return {str(p): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


# ---- the voice bank ---------------------------------------------------------------------------------------------
class TheVoiceBank(unittest.TestCase):
    def test_kokoro_voices_are_read_from_the_installed_voice_file(self):
        names = [v.name for v in B.kokoro()]
        self.assertEqual(sorted(names), sorted(tests.FAKE_KOKORO))
        self.assertEqual(names[:8], [n for n in tests.FAKE_KOKORO if n[0] == "a"], "American first")
        heart = next(v for v in B.kokoro() if v.name == "af_heart")
        self.assertEqual((heart.engine, heart.lang, heart.locale, heart.gender), ("kokoro", "en", "en-US", "female"))
        self.assertEqual(heart.label, "Heart, American female · Kokoro, local")

    def test_a_new_voice_pack_shows_up_by_itself(self):
        d = Path(tempfile.mkdtemp(prefix="vd-bank-"))
        with zipfile.ZipFile(d / "voices-v1.0.bin", "w") as z:
            for n in ("af_heart", "af_brandnew"):
                z.writestr(f"{n}.npy", b"\0")
        self.assertEqual([v.name for v in B.kokoro([d])], ["af_brandnew", "af_heart"])
        self.assertEqual(B.kokoro([d / "nothing-here"]), [])
        (d / "voices-v1.0.bin").write_text("not a zip")
        self.assertEqual(B.kokoro([d]), [], "a broken file means no voices from it, not a crash")

    def test_edge_voices_come_from_microsofts_saved_list(self):
        en = [v.name for v in B.edge() if v.lang == "en"]
        self.assertIn("en-US-JennyNeural", en)
        self.assertEqual([v.name for v in B.mandarin_taiwan()],
                         ["zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural"])
        andrew = next(v for v in B.edge() if v.name == "en-US-AndrewMultilingualNeural")
        self.assertEqual(andrew.label, "Andrew (multilingual), American male · Microsoft Edge, online")
        self.assertEqual(B.edge(Path("/nonexistent/edge_voices.json")), [])

    def test_english_is_local_first_then_microsoft(self):
        engines = [v.engine for v in B.english()]
        self.assertEqual(engines, sorted(engines, key=lambda e: e != "kokoro"))
        self.assertNotIn("jf_alpha", [v.name for v in B.english()])

    def test_the_saved_microsoft_list_in_the_repo_is_real_and_readable(self):
        head, voices_ = B.edge_list(REPO / "edge_voices.json")
        self.assertIn("fetched_at", head)
        names = [v.name for v in voices_]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreater(len([v for v in voices_ if v.lang == "en"]), 30)
        for v in ("en-US-AvaNeural", "en-US-AriaNeural", "en-GB-RyanNeural", "zh-TW-HsiaoChenNeural",
                  "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural"):
            self.assertIn(v, names, "every voice an agent has today is in the bank")

    def test_refresh_saves_microsofts_list_and_keeps_the_old_one_when_it_fails(self):
        d = Path(tempfile.mkdtemp(prefix="vd-refresh-"))
        target = d / "edge_voices.json"
        rows = [{"ShortName": "en-US-AvaNeural", "Locale": "en-US", "Gender": "Female"}]

        def ok_run(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")

        done, msg = B.refresh(target, python="/fake/python", run=ok_run)
        self.assertTrue(done, msg)
        self.assertEqual(json.loads(target.read_text())["voices"], rows)
        before = target.read_bytes()
        for bad in (lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "403 Forbidden"),
                    lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "[]", ""),
                    lambda argv, **kw: (_ for _ in ()).throw(OSError("no network"))):
            done, msg = B.refresh(target, python="/fake/python", run=bad)
            self.assertFalse(done)
            self.assertIn("stays", msg)
            self.assertEqual(target.read_bytes(), before)
        self.assertFalse(B.refresh(target, python="", run=ok_run)[0], "no Python with edge-tts: nothing fetched")


# ---- Simba's row (decision 8) -------------------------------------------------------------------------------------
class SimbasRowReadsLikeAHermesAgent(unittest.TestCase):
    def setUp(self):
        self.w = simba_world()
        self.simba = agent(self.w, "simba")

    def test_her_cells(self):
        cells = G.cells_for(self.simba, self.w.paths)
        self.assertEqual(G.kind_of(self.simba), G.HERMES)
        self.assertEqual(cells["terminal"].value, G.OFF, "no voice.auto_enable: a window starts with voice off")
        self.assertEqual(cells["tg_dm"].value, G.OFF, "no chat mode, no auto_tts: Hermes' default, text only")
        self.assertIn("Hermes' default", cells["tg_dm"].note)
        self.assertEqual(cells["tg_group"].value, G.VOICE_BACK, "simba.yaml voice_notes.enabled: true")
        self.assertEqual(cells["mic"].value, G.VOICE_BACK)
        for place in ("tg_dm", "tg_group"):
            self.assertIn("does not move or switch it", cells[place].note, place)
        self.assertNotIn(G.NA, [c.value for c in cells.values()])

    def test_she_is_here_wherever_she_is_wired(self):
        row = next(r for r in E.build(agents.load(self.w.toml), self.w.paths, E.LogReader(self.w.paths.profiles))
                   if r.agent.key == "simba")
        for place in ("terminal", "tg_dm", "tg_group", "mic"):
            self.assertTrue(row.is_here(place), place)
        self.assertEqual(row.evidence["tg_group"].headline, "her listener")

    def test_her_telegram_cells_are_never_switched(self):
        before = snapshot(self.w.root)
        for place in ("tg_dm", "tg_group"):
            self.assertFalse(onoff.can_switch(self.simba, place), place)
            cell = G.CELL_FUNCS[place](self.simba, self.w.paths)
            self.assertIn("does not move or switch it", onoff.menu(self.simba, place, cell, self.w.paths).why_not)
            for token in (G.OFF, G.ALWAYS, G.VOICE_BACK):
                res = onoff.set_cell("simba", place, token, self.w.paths, self.w.edit)
                self.assertFalse(res.ok, (place, token))
        self.assertEqual(snapshot(self.w.root), before, "nothing written anywhere")
        self.assertFalse(self.w.paths.voice_mode_file.exists())

    def test_others_keep_their_switches(self):
        kylie = agent(self.w, "kylie")
        self.assertTrue(onoff.can_switch(kylie, "tg_dm"))
        self.assertTrue(onoff.can_switch(kylie, "tg_group"))

    def test_voice_desk_speaks_her_lines_in_her_own_voice_and_effect(self):
        cfg = agents.load(self.w.toml)
        from unittest import mock
        with mock.patch.object(voices, "config", return_value=cfg):
            v = voices.voice_for("hermes:simba")
        self.assertEqual((v.en, v.zh, v.effect), ("am_fenrir", "", "16 pitch=1.4"))
        self.assertEqual(v.command, ("/nonexistent/python", "/nonexistent/simba_tts.py"))

    def test_voicecfg_manages_only_her_voice_line_and_invents_no_voice_block(self):
        items = voicecfg.collect(agents.load(self.w.toml), self.w.edit.profiles, self.w.edit.claude_conf,
                                 self.w.edit.gemini_conf)
        hers = [i for i in items if i.where == "simba"]
        self.assertEqual([(i.key, i.have, i.want) for i in hers], [("tts.providers.simba_local.voice", "am_fenrir",
                                                                    "am_fenrir")])
        self.assertFalse(voicecfg.drift(hers), "nothing to write into her profile")
        kylie = [i.key for i in items if i.where == "kylie"]
        self.assertIn("voice.stop_phrases", kylie, "a profile with a voice block still gets its end phrases")

    def test_knobs_without_a_voice_block_still_say_so(self):
        self.w.toml.write_text(self.w.toml.read_text().replace('english_only = true',
                                                               'english_only = true\nauto_tts = true'))
        items = voicecfg.collect(agents.load(self.w.toml), self.w.edit.profiles, self.w.edit.claude_conf)
        bad = [i for i in voicecfg.drift(items) if i.where == "simba"]
        self.assertTrue(bad and all("no `voice:` block" in i.problem for i in bad))


# ---- what each place can really do ------------------------------------------------------------------------------
class WhatEachPlaceCanDo(unittest.TestCase):
    def setUp(self):
        self.w = simba_world()

    def can(self, key, place):
        a = agent(self.w, key)
        return gridcan.can(a, place, G.CELL_FUNCS[place](a, self.w.paths), self.w.paths)

    def test_a_hermes_terminal_and_telegram_speak_with_the_profiles_own_voice(self):
        c = self.can("kylie", "terminal")
        self.assertIn("en-US-AvaNeural (provider natural)", c.speaks)
        self.assertIn("a Microsoft pick with E is written here too", c.voice)
        self.assertIn("Enter: Off / Always", c.change)                  # Phase C: the desk's terminal switch
        self.assertIn("Enter: Off / Voice-back / Always", self.can("kylie", "tg_dm").change)
        self.assertIn("Enter: Off / Always", self.can("kylie", "tg_group").change)

    def test_a_profile_voice_that_differs_from_agents_toml_is_said(self):
        c = self.can("jarvis", "terminal")                  # fixture: Jenny in both, so it reaches it
        self.assertIn("a Microsoft pick with E is written here too", c.voice)
        cfgp = self.w.paths.profiles / "orchestrator/config.yaml"
        cfgp.write_text(cfgp.read_text().replace("voice: en-US-JennyNeural", "voice: en-US-GuyNeural", 1))
        self.assertIn("profile says en-US-GuyNeural", self.can("jarvis", "terminal").voice)

    def test_simba(self):
        self.assertIn("simba_local", self.can("simba", "terminal").speaks)
        self.assertIn("view only", self.can("simba", "tg_dm").change)
        self.assertIn("does not move or switch it", self.can("simba", "tg_dm").change)
        g = self.can("simba", "tg_group")
        self.assertIn("her own listener", g.speaks)
        self.assertIn("am_fenrir", g.speaks)
        self.assertIn("Voice Desk speaks am_fenrir (its own script)", self.can("simba", "mic").speaks)

    def test_claude_and_gemini_say_whether_their_hook_still_has_the_mac_voice(self):
        self.assertIn("no backup voice", self.can("claude", "terminal").speaks)
        (self.w.paths.claude_dir / "hooks/speak-natural.py").write_text('argv = ["say"] + ["-v", "x"]\n')
        self.assertIn("still switches to the Mac voice", self.can("claude", "terminal").speaks)
        self.w.paths.gemini_hook.write_text('subprocess.run(["killall", "say"])\n')
        self.assertIn("no backup voice", self.can("gemini", "terminal").speaks, "killing say is not speaking with it")
        self.w.paths.gemini_hook.write_text('subprocess.run(["say", "-v", mac_voice, text])\n')
        g = self.can("gemini", "terminal")
        self.assertIn("Microsoft voices only", g.speaks)
        self.assertIn("still switches to the Mac voice", g.speaks)
        self.assertIn("Enter: Off / Always", g.change)

    def test_codex_iphone_and_mic(self):
        self.assertEqual(self.can("codex", "terminal").speaks, "")
        self.assertIn("Siri's own voice", self.can("kylie", "iphone").speaks)
        self.assertIn("speech queue", self.can("kylie", "mic").change)
        self.assertIn("nothing: no voice set", self.can("codex", "mic").speaks)

    def test_reading_it_writes_nothing(self):
        before = snapshot(self.w.root)
        for a in agents.agents(agents.load(self.w.toml)):
            for place in G.PLACE_KEYS:
                gridcan.line(gridcan.can(a, place, G.CELL_FUNCS[place](a, self.w.paths), self.w.paths))
        self.assertEqual(snapshot(self.w.root), before)

    def test_the_detail_panel_shows_the_place_under_the_cursor(self):
        import gridview as V
        rows = {r.agent.key: r for r in E.build(agents.load(self.w.toml), self.w.paths,
                                                E.LogReader(self.w.paths.profiles))}
        text = V.detail_text(rows["kylie"], None, place="tg_dm", paths=self.w.paths).plain
        self.assertIn("What Telegram direct can do for Kylie", text)
        self.assertIn("Enter: Off / Voice-back / Always", text)
        self.assertNotIn("What ", V.detail_text(rows["kylie"], None).plain, "Agent / Voice column: no place line")


# ---- decision 14: nothing changes by itself -----------------------------------------------------------------------
VOICE_FIELDS = ("voice", "voice_zh", "effect", "speed", "english_only", "no_fallback", "command", "always_on", "mode",
                *agents.KNOBS)
BASELINE = "2b5352e"      # main just before stage 1 (2026-11-13 21:41)


class NoVoiceChangesAsASideEffect(unittest.TestCase):
    def test_the_real_agents_toml_kept_every_voice_value_of_the_baseline(self):
        """Decision 14: after the build every agent speaks with exactly the voice, effect, speed and on/off state it had.
        Compares the real agents.toml with the one before stage 1. Skipped once Peter has changed a voice on purpose
        (a line in the panel's change history after the baseline), because then a difference is his choice."""
        try:
            base_text = subprocess.run(["git", "-C", str(REPO), "show", f"{BASELINE}:agents.toml"], capture_output=True,
                                       text=True, timeout=20, check=True).stdout
            base_time = float(subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%ct", BASELINE],
                                             capture_output=True, text=True, timeout=20, check=True).stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            self.skipTest("no git history here")
        history = Path(os.path.expanduser("~/.voice_desk/voice_history.jsonl"))
        try:
            lines = history.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for ln in lines:
            try:
                if float(json.loads(ln).get("ts", 0)) > base_time:
                    self.skipTest("Peter changed a voice after the baseline (panel history)")
            except ValueError:
                continue
        base = tomllib.loads(base_text)["agents"]
        now = agents.load(REPO / "agents.toml")["agents"]
        self.assertEqual(set(base), set(now), "no agent added or removed")
        for key in base:
            for f in VOICE_FIELDS:
                self.assertEqual(base[key].get(f), now[key].get(f), f"{key}.{f}")
        self.assertEqual(now["simba"].get("profile"), "simba", "the only change: her row points at her profile")

    def test_the_whole_grid_on_a_copy_of_the_real_agents_toml_changes_nothing(self):
        w = F.make_world()
        shutil.copy(REPO / "agents.toml", w.toml)
        before = w.toml.read_bytes()
        cfg = agents.load(w.toml)
        rows = E.build(cfg, w.paths, E.LogReader(w.paths.profiles))
        import voicepick
        for r in rows:
            for f in ("voice", "voice_zh", "effect"):
                voicepick.menu(r.agent, f, cfg)
            for place in G.PLACE_KEYS:
                gridcan.can(r.agent, place, r.cells[place], w.paths)
        self.assertEqual(w.toml.read_bytes(), before)
        resolved = {a.key: voices.resolve(a) for a in agents.agents(agents.load(w.toml))}
        self.assertEqual(resolved["simba"].en, "am_fenrir")
        self.assertEqual(resolved["jarvis"].en, "am_puck")


# ---- the stage 1 review fixes (2026-11-14, card t_1d723c38) --------------------------------------------------------
JARVIS_ENGLISH_ONLY = F.AGENTS_TOML.replace('names = ["jarvis"]\n', 'names = ["jarvis"]\nenglish_only = true\n', 1)


def outside(w) -> dict:
    """Every file a voice change may write, except Voice Desk's own history and backups."""
    return {k: v for k, v in snapshot(w.root).items()
            if not k.startswith((str(w.root / "state"), str(w.root / "profile-backups")))}


class StageOneReviewFixes(unittest.TestCase):
    def setUp(self):
        self.w = F.make_world()

    # (1) silent, then U, on an English-only agent's stored Mandarin voice
    def test_silent_then_u_puts_jarvis_mandarin_voice_back_exactly(self):
        import voiceedit as VE
        import voicepick
        self.w.toml.write_text(JARVIS_ENGLISH_ONLY)
        before_toml, before = self.w.toml.read_bytes(), outside(self.w)
        jarvis = agent(self.w, "jarvis")
        self.assertTrue(jarvis.english_only)
        self.assertEqual(jarvis.voice_zh, "zh-TW-HsiaoChenNeural")
        _, opts, why = voicepick.menu(jarvis, "voice_zh", agents.load(self.w.toml))
        silent = next(o for o in opts if o.token == voicepick.SILENT)
        self.assertFalse(silent.disabled, "the Z list offers 'silent' on his row, so it can be picked")
        res = VE.change("jarvis", "voice_zh", None, self.w.edit)          # what picking 'silent' does
        self.assertTrue(res.ok, res.message)
        self.assertIsNone(agent(self.w, "jarvis").voice_zh)
        res = VE.undo(self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.w.toml.read_bytes(), before_toml, "the exact line is back, comment and place")
        self.assertEqual(outside(self.w), before, "no other file changed")
        self.assertEqual(agent(self.w, "jarvis").voice_zh, "zh-TW-HsiaoChenNeural")
        self.assertFalse(VE.undo(self.w.edit).ok, "nothing left to undo")

    def test_undo_puts_back_only_the_old_value_a_new_mandarin_voice_is_still_refused(self):
        import voiceedit as VE
        self.w.toml.write_text(JARVIS_ENGLISH_ONLY)
        res = VE.change("jarvis", "voice_zh", "zh-TW-HsiaoYuNeural", self.w.edit)       # the typed route
        self.assertFalse(res.ok)
        self.assertIn("English only", res.message)
        self.assertEqual(self.w.toml.read_text(), JARVIS_ENGLISH_ONLY)
        res = VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit)              # an ordinary change + U
        self.assertTrue(res.ok, res.message)
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.w.toml.read_text(), JARVIS_ENGLISH_ONLY)

    # (2) the "what this place can do" line is true for each kind of pick
    def can(self, key, place="terminal"):
        a = agent(self.w, key)
        return gridcan.can(a, place, G.CELL_FUNCS[place](a, self.w.paths), self.w.paths)

    def test_kylie_microsoft_pick_reaches_her_profile_a_kokoro_pick_does_not_and_the_line_says_so(self):
        import voiceedit as VE
        line = self.can("kylie").voice
        self.assertIn("a Microsoft pick with E is written here too", line)
        self.assertIn("a Kokoro pick changes Voice Desk's own speech only", line)
        self.assertIn("keeps en-US-AvaNeural", line)
        prof = self.w.paths.profiles / "assistant/config.yaml"
        before = prof.read_bytes()
        self.assertTrue(VE.change("kylie", "voice", "af_heart", self.w.edit).ok)         # Kokoro: profile untouched
        self.assertEqual(prof.read_bytes(), before)
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertTrue(VE.change("kylie", "voice", "en-US-AriaNeural", self.w.edit).ok)  # Microsoft: written there
        self.assertIn("voice: en-US-AriaNeural", prof.read_text())

    def test_wanna_buffet_on_kokoro_the_line_no_longer_claims_e_writes_his_profile(self):
        import voiceedit as VE
        self.w.toml.write_text(F.AGENTS_TOML.replace('voice = "en-US-AndrewNeural"', 'voice = "am_santa"', 1))
        prof = self.w.paths.profiles / "wanna-buffet/config.yaml"
        prof.write_text(F.profile_yaml("am_santa", threshold=500, speed=0.92))          # his profile speaks Santa too
        line = self.can("wanna_buffet").voice
        self.assertNotIn("E changes it here too", line, "the old, untrue line")
        self.assertIn("E does not write here today", line)
        self.assertIn("keeps am_santa", line)
        self.assertIn("a Microsoft pick would be written into its profile too", line)
        before = prof.read_bytes()
        self.assertTrue(VE.change("wanna_buffet", "voice", "am_onyx", self.w.edit).ok)   # Kokoro: profile untouched
        self.assertEqual(prof.read_bytes(), before)
        self.assertIn("keeps am_santa", self.can("wanna_buffet").voice)
        self.assertTrue(VE.change("wanna_buffet", "voice", "en-US-AvaNeural", self.w.edit).ok)
        self.assertIn("voice: en-US-AvaNeural", prof.read_text(), "a Microsoft pick is written there, as the line says")

    def test_an_agent_with_its_own_script_gets_every_pick_written_into_its_profile(self):
        import voiceedit as VE
        self.assertIn("E changes it here too (written into its profile", self.can("esther").voice)
        self.assertTrue(VE.change("esther", "voice", "af_heart", self.w.edit).ok)
        self.assertIn("voice: af_heart", (self.w.paths.profiles / "tax_angel_esther/config.yaml").read_text())
        self.assertIn("E changes it here too (written into its profile", self.can("esther").voice)

    # (3) the pick check reads the installed voice bank
    def test_a_newly_installed_kokoro_voice_is_listed_and_accepted_when_picked(self):
        import voiceedit as VE
        import voicepick
        from unittest import mock
        d = Path(tempfile.mkdtemp(prefix="vd-newbank-"))
        with zipfile.ZipFile(d / "voices-v1.0.bin", "w") as z:
            for n in (*tests.FAKE_KOKORO, "af_brandnew", "bm_newcomer", "jf_newone"):
                z.writestr(f"{n}.npy", b"\0")
        kylie = agent(self.w, "kylie")
        self.assertFalse(agents.is_kokoro("af_brandnew"), "not installed: not a voice")
        with self.assertRaises(VE.EditError):
            VE.check_voice("voice", "af_brandnew", kylie)
        prof = self.w.paths.profiles / "assistant/config.yaml"
        before = prof.read_bytes()
        with mock.patch.object(B, "KOKORO_HOMES", (d,)):
            for name in ("af_brandnew", "bm_newcomer"):
                self.assertTrue(agents.is_kokoro(name), name)
                line = next(o for o in voicepick.english(kylie, {}) if name in o.token)
                self.assertEqual((line.token, line.disabled), (name, False), "listed AND pickable")
                self.assertEqual(VE.check_voice("voice", name, kylie), name)
            self.assertFalse(agents.is_kokoro("jf_newone"), "a Japanese voice is not an English voice")
            with self.assertRaises(VE.EditError):
                VE.check_voice("voice", "jf_newone", kylie)
            res = VE.change("kylie", "voice", "af_brandnew", self.w.edit)
            self.assertTrue(res.ok, res.message)
            v = voices.resolve(agent(self.w, "kylie"))
            self.assertEqual((v.en, v.command), ("af_brandnew", agents.KOKORO_COMMAND),
                             "spoken by the shared Kokoro script, like any Kokoro voice")
            self.assertEqual(prof.read_bytes(), before, "a Kokoro name is never written into Hermes' own provider")
            self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)

    def test_the_known_names_and_edge_names_still_read_as_before(self):
        self.assertTrue(agents.is_kokoro("am_adam"), "a known name stays valid even when no voice file lists it")
        for name in ("en-US-AvaNeural", "alloy", "", None, "zf_xiaobei"):
            self.assertFalse(agents.is_kokoro(name), name)


if __name__ == "__main__":
    unittest.main()
