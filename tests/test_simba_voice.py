"""Step 6 (2026-11-10): Simba's voice. Kokoro (an American female voice) plus a scratchy "witch" effect, made by
simba_tts.py; voice and effect are agents.toml settings the grid changes (E / F) with backup, history and undo.

Everything runs against fakes: a fake kokoro_onnx package (the real model is never loaded), throw-away copies of
agents.toml, and real ffmpeg writing into a temp folder. Nothing is played; nothing reaches a speaker.

    .venv/bin/python -m unittest tests.test_simba_voice -v
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import gridstate as G  # noqa: E402
import gridview as V  # noqa: E402
import simba_tts  # noqa: E402
import tts  # noqa: E402
import voicecfg  # noqa: E402
import voiceedit as VE  # noqa: E402
import voices  # noqa: E402

FFMPEG = simba_tts.FFMPEG
SCRIPT = ROOT / "simba_tts.py"

SIMBA_BLOCK = '''
[agents.simba]
target = "simba"
display = "Simba"
names = ["simba"]
voice = "af_nicole"                  # sample 1
effect = "smoky"
voice_zh = "zh-TW-HsiaoYuNeural"
command = ["/nonexistent/python", "/nonexistent/simba_tts.py"]
fallback = "en-US-MichelleNeural"
'''

# A fake kokoro_onnx: one second of a 220 Hz tone at Kokoro's 24 kHz, and a note of how it was called.
FAKE_KOKORO = '''
import json, os
import numpy as np

class Kokoro:
    def __init__(self, model, voices):
        self.paths = [model, voices]
        if os.environ.get("FAKE_KOKORO_FAIL") == "load":
            raise RuntimeError("model will not load")

    def create(self, text, voice, speed, lang):
        with open(os.environ["FAKE_KOKORO_LOG"], "a") as fh:
            fh.write(json.dumps({"paths": self.paths, "text": text, "voice": voice, "speed": speed, "lang": lang}) + "\\n")
        t = np.arange(24000) / 24000.0
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 24000
'''


def simba_world():
    w = F.make_world()
    w.toml.write_text(F.AGENTS_TOML + SIMBA_BLOCK, encoding="utf-8")
    return w


def agent_of(toml, key):
    return next(a for a in agents.agents(agents.load(toml)) if a.key == key)


def probe(path):
    """(codec, sample rate, channels, seconds) by ffprobe."""
    r = subprocess.run([str(Path(FFMPEG).with_name("ffprobe")), "-v", "error", "-show_entries",
                        "format=duration:stream=codec_name,sample_rate,channels", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=30)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    return s["codec_name"], int(s["sample_rate"]), int(s["channels"]), float(d["format"]["duration"])


def digest(paths):
    return {str(p): hashlib.sha1(p.read_bytes()).hexdigest() for p in paths if p.exists()}


# ---- the script -------------------------------------------------------------------------------------------
class TheRecipe(unittest.TestCase):
    """The effects are Jarvis's sample recipe (k_01..k_10), not something new."""

    def args(self, effect, sr=24000, fmt="wav"):
        return simba_tts.ffmpeg_args(effect, sr, Path("/in.wav"), Path("/out." + fmt), fmt)

    def graph(self, effect, sr=24000):
        a = self.args(effect, sr)
        return a[a.index("-filter_complex") + 1], a[a.index("lavfi") + 2]

    def test_smoky_is_pitch_085_tempo_112_light_crush_soft_clip_faint_pink_noise(self):
        graph, noise = self.graph("smoky")
        for part in ("asetrate=24000*0.85", "atempo=1.12", "acrusher=bits=10:mix=0.35", "asoftclip=type=tanh"):
            self.assertIn(part, graph)
        self.assertNotIn("tremolo", graph)
        self.assertIn("color=pink:amplitude=0.01", noise)

    def test_raspy_is_pitch_078_tremolo_34hz_harder_crush_and_clip_white_noise(self):
        graph, noise = self.graph("raspy")
        for part in ("asetrate=24000*0.78", "atempo=1.08", "tremolo=f=34", "acrusher=bits=7", "asoftclip=type=hard"):
            self.assertIn(part, graph)
        self.assertIn("color=white:amplitude=0.02", noise)

    def test_the_pitch_follows_the_rate_kokoro_really_gave(self):
        graph, _ = self.graph("smoky", sr=22050)
        self.assertIn("asetrate=22050*0.85", graph)

    def test_plain_is_kokoro_as_it_is_with_only_the_lead_silence(self):
        a = self.args("plain")
        self.assertNotIn("-filter_complex", a)
        self.assertNotIn("lavfi", a)
        self.assertIn("adelay=200:all=1", a)

    def test_every_preset_ends_with_the_lead_silence_and_a_mono_file(self):
        for effect in simba_tts.PRESETS:
            for fmt in simba_tts.ENCODE:
                a = self.args(effect, fmt=fmt)
                self.assertTrue(any("adelay=" in x for x in a), (effect, fmt))
                self.assertEqual(a[a.index("-ac") + 1], "1")

    def test_the_numbered_samples_are_settings_the_script_accepts(self):
        self.assertEqual(sorted(simba_tts.SAMPLES), list(range(1, 11)))
        for voice, effect in simba_tts.SAMPLES.values():
            self.assertIsNone(simba_tts.voice_problem(voice))
            self.assertIsNone(simba_tts.effect_problem(effect))


class TheVoiceRule(unittest.TestCase):
    """Peter: a girl cat, American accent only."""

    def test_american_female_kokoro_voices_only(self):
        for ok in ("af_nicole", "af_kore", "af_river", "af_jessica", "af_nova", "Nicole", "kore"):
            self.assertIsNone(simba_tts.voice_problem(ok), ok)
        for bad in ("bf_emma", "am_adam", "en-US-AvaNeural", "zf_xiaoxiao", "", "af_nobody", "nicole smoky"):
            self.assertIsNotNone(simba_tts.voice_problem(bad), bad)

    def test_a_short_name_means_the_american_female_voice(self):
        self.assertEqual(simba_tts.clean_voice(" Nicole "), "af_nicole")
        self.assertEqual(simba_tts.clean_voice("af_kore"), "af_kore")

    def test_importing_the_script_loads_no_model(self):
        r = subprocess.run([sys.executable, "-c", "import sys, simba_tts; print('kokoro_onnx' in sys.modules, "
                            "'numpy' in sys.modules)"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "False False", r.stderr)


class TheScriptAsVoiceDeskRunsIt(unittest.TestCase):
    """simba_tts.py run as a program, with the arguments Voice Desk gives it, a fake Kokoro and the real ffmpeg."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vd-simba-"))
        pkg = self.tmp / "fakes/kokoro_onnx"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(FAKE_KOKORO)
        self.log = self.tmp / "kokoro.log"
        self.models = self.tmp / "models"            # never Peter's tax folder
        self.txt = self.tmp / "say.txt"
        self.txt.write_text("Show up on Thursday, or I start reading your excuses out loud.", encoding="utf-8")

    def run_script(self, out, *args, fail=None):
        env = dict(os.environ, PYTHONPATH=str(self.tmp / "fakes"), FAKE_KOKORO_LOG=str(self.log),
                   SIMBA_KOKORO_DIR=str(self.models))
        if fail:
            env["FAKE_KOKORO_FAIL"] = fail
        return subprocess.run([sys.executable, str(SCRIPT), str(self.txt), str(out), *args],
                              capture_output=True, text=True, timeout=120, env=env)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_raspy_wav_is_made_from_kokoro_at_speed_092_with_american_rules(self):
        out = self.tmp / "say.wav"
        r = self.run_script(out, "af_kore", "raspy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_bytes()[:4], b"RIFF")
        codec, rate, channels, secs = probe(out)
        self.assertEqual((codec, rate, channels), ("pcm_s16le", 48000, 1))
        # 1 s of Kokoro, slowed by the pitch drop (1/0.78) and sped up by the tempo (1.08), plus 0.2 s lead
        self.assertAlmostEqual(secs, 1 / (0.78 * 1.08) + 0.2, delta=0.06)
        [call] = self.calls()
        self.assertEqual((call["voice"], call["speed"], call["lang"]), ("af_kore", 0.92, "en-us"))
        self.assertEqual(call["text"], self.txt.read_text())
        self.assertEqual(call["paths"], [str(self.models / "kokoro-v1.0.onnx"), str(self.models / "voices-v1.0.bin")])

    def test_smoky_is_the_default_effect_and_is_longer_than_plain(self):
        smoky, plain = self.tmp / "s.wav", self.tmp / "p.wav"
        self.assertEqual(self.run_script(smoky, "nicole").returncode, 0)
        self.assertEqual(self.run_script(plain, "af_nicole", "plain").returncode, 0)
        self.assertAlmostEqual(probe(smoky)[3], 1 / (0.85 * 1.12) + 0.2, delta=0.06)
        self.assertAlmostEqual(probe(plain)[3], 1.2, delta=0.06)
        self.assertEqual(probe(plain)[1], 24000, "plain keeps Kokoro's own rate")
        self.assertEqual([c["voice"] for c in self.calls()], ["af_nicole", "af_nicole"])

    def test_the_file_ending_picks_the_format(self):
        for name, magic in (("v.ogg", b"OggS"), ("v.mp3", None)):
            out = self.tmp / name
            self.assertEqual(self.run_script(out, "af_nova", "smoky").returncode, 0, name)
            self.assertGreater(out.stat().st_size, 1000)
            if magic:
                self.assertEqual(out.read_bytes()[:4], magic)
            else:
                self.assertEqual(probe(out)[0], "mp3")

    def test_a_voice_or_effect_it_does_not_make_writes_nothing_and_never_loads_kokoro(self):
        for args in (("bf_emma", "smoky"), ("am_adam",), ("af_nicole", "spooky")):
            out = self.tmp / "no.wav"
            r = self.run_script(out, *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertFalse(out.exists(), args)
        self.assertEqual(self.calls(), [])

    def test_if_kokoro_fails_it_says_so_and_writes_nothing(self):
        out = self.tmp / "no.wav"
        r = self.run_script(out, "af_nicole", "smoky", fail="load")
        self.assertEqual(r.returncode, 1)
        self.assertIn("model will not load", r.stderr)
        self.assertFalse(out.exists())


# ---- Voice Desk speaking with it ---------------------------------------------------------------------------
class VoiceDeskSpeaksWithIt(unittest.TestCase):
    def setUp(self):
        self.w = simba_world()
        self.cfg = agents.load(self.w.toml)

    def voice(self):
        with mock.patch.object(voices, "config", return_value=self.cfg), \
                mock.patch.object(voices, "per_profile_enabled", return_value=True):
            return voices.voice_for("simba")

    def test_voice_for_simba_is_her_kokoro_voice_effect_and_taiwan_mandarin(self):
        v = self.voice()
        self.assertEqual((v.en, v.zh, v.effect, v.fallback), ("af_nicole", "zh-TW-HsiaoYuNeural", "smoky",
                                                              "en-US-MichelleNeural"))
        self.assertEqual(v.command, ("/nonexistent/python", "/nonexistent/simba_tts.py"))

    def test_with_own_voices_off_simba_has_the_shared_voice_like_everyone(self):
        with mock.patch.object(voices, "config", return_value=self.cfg), \
                mock.patch.object(voices, "per_profile_enabled", return_value=False):
            self.assertEqual(voices.voice_for("simba").source, "shared")

    def test_the_script_gets_the_effect_after_the_voice_and_esther_gets_exactly_what_she_got_before(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append(list(argv))
            Path(next(a for a in argv if a.endswith(".wav"))).write_bytes(b"RIFF....")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        tmp = Path(tempfile.mkdtemp(prefix="vd-simba-run-"))
        with mock.patch.object(tts.subprocess, "run", side_effect=fake_run):
            self.assertTrue(tts._script(("py", "simba_tts.py"), tmp / "t.txt", tmp / "a.wav", "af_nicole", effect="raspy"))
            self.assertTrue(tts._script(("py", "esther_tts.py"), tmp / "t.txt", tmp / "b.wav", "alloy"))
        self.assertEqual(seen[0], ["py", "simba_tts.py", str(tmp / "t.txt"), str(tmp / "a.wav"), "af_nicole", "raspy"])
        self.assertEqual(seen[1], ["py", "esther_tts.py", str(tmp / "t.txt"), str(tmp / "b.wav"), "alloy"])

    def test_english_goes_through_her_script_mandarin_through_hsiaoyu_and_a_failure_to_the_backup(self):
        v = self.voice()
        made = []

        def script(cmd, txt, out, voice, timeout=120, effect=None):
            made.append(("script", voice, effect))
            return None if "fail" in txt.read_text() else out

        def chain(txt, out, voice):
            made.append(("edge", voice))
            return out

        with mock.patch.object(tts, "_script", side_effect=script), mock.patch.object(tts, "_chain", side_effect=chain):
            tts.synthesize("Go away, human.", v)
            tts.synthesize("走開啦，人類。", v)
            tts.synthesize("this one will fail", v)
        self.assertEqual(made, [("script", "af_nicole", "smoky"), ("edge", "zh-TW-HsiaoYuNeural"),
                                ("script", "af_nicole", "smoky"), ("edge", "en-US-MichelleNeural")])

    def test_the_silent_voice_test_runs_her_script_with_the_effect(self):
        import voicetest
        simba = agent_of(self.w.toml, "simba")
        with mock.patch.object(tts, "_script", return_value=Path("/x")) as script:
            via = voicetest._real_synth(Path("/t.txt"), Path("/o.mp3"), "af_nicole", simba, "en")
        self.assertEqual(via, "voice")
        self.assertEqual(script.call_args.kwargs.get("effect"), "smoky")


class SimbaIsAVoiceNotABrain(unittest.TestCase):
    """This card adds her voice, not a trigger: nothing wakes her and no other file is written for her."""

    def test_no_hermes_claude_or_gemini_setting_is_collected_for_her(self):
        w = simba_world()
        items = voicecfg.collect(agents.load(w.toml), w.edit.profiles, w.edit.claude_conf, w.edit.gemini_conf)
        self.assertFalse([i for i in items if i.where == "simba"])
        self.assertTrue(items, "the others still are")

    def test_hello_simba_wakes_nothing(self):
        import brain
        self.assertNotIn("simba", {t for _, t in brain.name_aliases()})

    def test_the_real_agents_toml_entry(self):
        cfg = agents.load()
        simba = next(a for a in agents.agents(cfg) if a.key == "simba")
        self.assertIsNone(simba_tts.voice_problem(simba.voice))
        self.assertIsNone(simba_tts.effect_problem(simba.effect))
        self.assertTrue(simba.voice_zh.startswith("zh-TW-"))
        self.assertEqual(Path(simba.command[-1]).resolve(), SCRIPT.resolve(), "agents.toml runs this repo's script")
        self.assertTrue(VE.uses_simba_script(simba))
        others = {VE._kokoro_name(a.voice) for a in agents.agents(cfg) if a.key != "simba"}
        self.assertNotIn(VE._kokoro_name(simba.voice), others, "a voice of her own")
        self.assertNotIn(VE._kokoro_name(simba.fallback), others, "her backup voice is not someone else's either")
        self.assertNotIn("simba", agents.whisper_prompt(cfg).lower())
        self.assertEqual(G.kind_of(simba), G.OTHER)


# ---- the panel ------------------------------------------------------------------------------------------------
class ThePanelChangesHerVoice(unittest.TestCase):
    def setUp(self):
        self.w = simba_world()
        self.start = self.w.toml.read_text()
        self.others = [*self.w.edit.profiles.glob("*/config.yaml"), self.w.edit.claude_conf, self.w.edit.gemini_conf]
        self.before = digest(self.others)

    def simba(self):
        return agent_of(self.w.toml, "simba")

    def test_she_is_editable(self):
        self.assertTrue(VE.editable(self.simba()))

    def test_e_changes_her_voice_in_agents_toml_only_with_backup_and_history(self):
        res = VE.change("simba", "voice", "Kore", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.simba().voice, "af_kore", "the short name is stored as the full Kokoro name")
        self.assertIn("simba_tts.py", res.message)
        [entry] = VE.read_history(self.w.edit)
        self.assertEqual((entry["old"], entry["new"], entry["ok"]), ("af_nicole", "af_kore", True))
        self.assertEqual(Path(entry["toml_backup"]).read_text(), self.start)
        self.assertEqual(digest(self.others), self.before, "no Hermes, Claude or Gemini file is touched")
        self.assertIn("Simba English voice: af_nicole → af_kore", VE.recent(self.w.edit)[0])

    def test_f_changes_her_effect(self):
        res = VE.change("simba", "effect", " Raspy ", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.simba().effect, "raspy")
        self.assertIn("Voice Desk uses it from the next line", res.message)
        self.assertIn("Simba voice effect: smoky → raspy", VE.recent(self.w.edit)[0])
        self.assertEqual(digest(self.others), self.before)

    def test_what_she_may_not_have_is_refused_and_nothing_is_written(self):
        for field, value, words in (("voice", "bf_emma", "American female"), ("voice", "am_adam", "American female"),
                                    ("voice", "en-US-AvaNeural", "American female"),
                                    ("voice", "alloy", "Esther's voice"), ("voice", "af_alloy", "Esther's voice"),
                                    ("effect", "spooky", "smoky"),
                                    ("voice_zh", "zh-CN-XiaoxiaoNeural", "Taiwan Mandarin")):
            res = VE.change("simba", field, value, self.w.edit)
            self.assertFalse(res.ok, (field, value))
            self.assertIn(words, res.message, (field, value))
        self.assertEqual(self.w.toml.read_text(), self.start)
        self.assertEqual(VE.read_history(self.w.edit), [])

    def test_only_simba_has_an_effect(self):
        res = VE.change("kylie", "effect", "smoky", self.w.edit)
        self.assertFalse(res.ok)
        self.assertIn("no voice effect", res.message)
        res = VE.change("esther", "effect", "smoky", self.w.edit)
        self.assertFalse(res.ok, "Esther's Kokoro script has no effect")
        self.assertEqual(self.w.toml.read_text(), self.start)

    def test_others_keep_their_rules(self):
        self.assertFalse(VE.change("kylie", "voice", "af_kore", self.w.edit).ok, "Kylie still needs an Edge voice")
        self.assertTrue(VE.change("esther", "voice", "bella", self.w.edit).ok, "Esther's Kokoro names are hers to pick")

    def test_peters_pick_is_two_changes_and_two_undos_put_everything_back_byte_for_byte(self):
        voice, effect = simba_tts.SAMPLES[8]                                   # "sample 8" = jessica raspy
        self.assertTrue(VE.change("simba", "voice", voice, self.w.edit).ok)
        self.assertTrue(VE.change("simba", "effect", effect, self.w.edit).ok)
        self.assertEqual((self.simba().voice, self.simba().effect), ("af_jessica", "raspy"))
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.simba().effect, "smoky")
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.w.toml.read_text(), self.start)
        self.assertEqual(digest(self.others), self.before)


class TheGridShowsHer(unittest.TestCase):
    def setUp(self):
        self.w = simba_world()
        self.cfg = agents.load(self.w.toml)
        self.simba = agent_of(self.w.toml, "simba")

    def test_she_is_the_last_row_and_every_place_says_voice_only(self):
        self.assertEqual(G.grid_agents(self.cfg)[-1].key, "simba")
        cells = G.cells_for(self.simba, self.w.paths)
        self.assertEqual({c.value for c in cells.values()}, {G.NA})
        self.assertTrue(all("voice only" in c.note for c in cells.values()))

    def test_the_voice_cell_and_the_detail_name_her_voice_and_effect(self):
        info = G.voice_info(self.simba, self.cfg)
        self.assertEqual((info.en, info.zh, info.effect, info.backup),
                         ("af_nicole", "zh-TW-HsiaoYuNeural", "smoky", "en-US-MichelleNeural"))
        row = mock.Mock(voice=info, agent=self.simba, kind=G.OTHER, cells={}, evidence={})
        cell = V.voice_text(row).plain
        self.assertIn("af_nicole · HsiaoYu", cell)
        self.assertIn("smoky", cell)
        self.assertNotIn("smoky", V.voice_text(mock.Mock(voice=G.voice_info(agent_of(self.w.toml, "kylie"), self.cfg))).plain)


class TheGridKeys(unittest.IsolatedAsyncioTestCase):
    """E and F on Simba's row, then U U, in the headless dashboard against the fake world."""

    async def test_e_and_f_on_simbas_row_then_u_u(self):
        import dashboard as D
        from textual.widgets import Input
        from tests.test_dashboard_grid import load, make_app, text_of, wait_for
        w = simba_world()
        start = w.toml.read_text()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press(*["down"] * 7, "f")                               # Simba is the 8th row
            await pilot.pause()
            self.assertIsInstance(app.screen, D.AskScreen)
            field = app.screen.query_one(Input)
            self.assertEqual(field.value, "smoky", "the box starts with today's effect")
            field.value = "raspy"
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: VE.read_history(w.edit)))
            await pilot.pause()
            self.assertIn("Simba voice effect: smoky → raspy", text_of(app, "#history"))
            await pilot.press("e")
            await pilot.pause()
            field = app.screen.query_one(Input)
            self.assertEqual(field.value, "af_nicole")
            field.value = "jessica"
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 2))
            await pilot.pause()
            self.assertEqual(agent_of(w.toml, "simba").voice, "af_jessica")
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 3))
            await pilot.press("u")
            self.assertTrue(await wait_for(lambda: len(VE.read_history(w.edit)) == 4))
            await pilot.pause()
            self.assertEqual(w.toml.read_text(), start)

    async def test_f_on_another_row_says_why_not(self):
        from tests.test_dashboard_grid import load, make_app, text_of
        w = simba_world()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press("down", "f")                                       # Kylie
            await pilot.pause()
            self.assertIn("no voice effect", text_of(app, "#msg"))
            self.assertEqual(VE.read_history(w.edit), [])


if __name__ == "__main__":
    unittest.main()
