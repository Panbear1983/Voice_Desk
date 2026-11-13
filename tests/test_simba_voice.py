"""Step 6 (2026-11-10): Simba's voice. Kokoro (an American voice) plus a scratchy "witch" effect, made by
simba_tts.py; voice and effect are agents.toml settings the grid changes (E / F) with backup, history and undo.
The effect is a recipe ("16", "23 but slower and breathier", "16 pitch=1.1"), so Peter's pick is a setting.

Everything runs against fakes: a fake kokoro_onnx package (the real model is never loaded), throw-away copies of
agents.toml, and real ffmpeg writing into a temp folder. Nothing is played; nothing reaches a speaker.
JarvisSamples also re-makes Jarvis's 30 samples from his own Kokoro recordings when those are on disk
(~/.hermes/profiles/orchestrator/cache/scratch/simba_witch, or SIMBA_SAMPLES_DIR) and is skipped otherwise.

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
JARVIS = Path(os.environ.get("SIMBA_SAMPLES_DIR",
                             Path.home() / ".hermes/profiles/orchestrator/cache/scratch/simba_witch"))

SIMBA_BLOCK = '''
[agents.simba]
target = "simba"
display = "Simba"
names = ["simba"]
voice = "am_fenrir"
effect = "fenrir16"
english_only = true
command = ["/nonexistent/python", "/nonexistent/simba_tts.py"]
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

# Jarvis's filter chains, written out the way he typed them (his session, 2026-11-10), numbers as ffmpeg gets them.
K_SMOKY = ("[0]asetrate={sr}*0.85,aresample=48000,atempo=1.12,highpass=f=110,lowpass=f=4800,"
           "acrusher=bits=10:mix=0.35:mode=log:aa=1,asoftclip=type=tanh:threshold=0.3:output=1.4,"
           "aexciter=amount=2:freq=2200,acompressor=threshold=-26dB:ratio=5[v];"
           "[v][1]amix=inputs=2:duration=first:weights=1 0.4,volume=1.1")
K_RASPY = ("[0]asetrate={sr}*0.78,aresample=48000,atempo=1.08,highpass=f=150,lowpass=f=5200,"
           "tremolo=f=34:d=0.35,acrusher=bits=7:samples=2:mix=0.5:mode=lin,"
           "asoftclip=type=hard:threshold=0.2:output=1.6,acompressor=threshold=-24dB:ratio=8[v];"
           "[1]highpass=f=1500,volume=0.6[nn];[v][nn]amix=inputs=2:duration=first:weights=1 0.5,volume=1.2")
J_FX = {"smoky": ("highpass=f=100,lowpass=f=4600,acrusher=bits=10:mix=0.35:mode=log:aa=1,asoftclip=type=tanh:"
                  "threshold=0.3:output=1.4,aexciter=amount=2:freq=2200,acompressor=threshold=-26dB:ratio=5",
                  "anoisesrc=color=pink:amplitude=0.01:sample_rate=48000", "1 0.4"),
        "raspy": ("highpass=f=140,lowpass=f=5000,tremolo=f=34:d=0.35,acrusher=bits=7:samples=2:mix=0.5:mode=lin,"
                  "asoftclip=type=hard:threshold=0.2:output=1.6,acompressor=threshold=-24dB:ratio=8",
                  "anoisesrc=color=white:amplitude=0.02:sample_rate=48000", "1 0.5"),
        "goofy": ("highpass=f=250,equalizer=f=1200:t=q:w=1:g=6,equalizer=f=2600:t=q:w=1:g=4,"
                  "acompressor=threshold=-22dB:ratio=4", "anoisesrc=color=pink:amplitude=0.0005:sample_rate=48000",
                  "1 0.1")}
# Jarvis's mk calls for 11-20: number -> (voice, pitch, chain)
J_MK = {11: ("af_nicole", 0.72, "smoky"), 12: ("af_kore", 0.74, "raspy"), 13: ("af_river", 0.70, "raspy"),
        14: ("af_jessica", 0.76, "smoky"), 15: ("am_onyx", 1.12, "raspy"), 16: ("am_fenrir", 1.08, "smoky"),
        17: ("am_michael", 1.10, "raspy"), 18: ("af_river", 0.80, "smoky"), 19: ("af_nova", 1.22, "goofy"),
        20: ("af_bella", 1.30, "goofy")}
# Jarvis's v16 calls for 21-30: number -> (pitch, bits, mix, noise, tempo multiplier, tremolo depth, echo)
J_V16 = {21: (1.00, 10, 0.35, 0.01, 1, 0, False), 22: (1.04, 10, 0.35, 0.01, 1, 0, False),
         23: (1.12, 10, 0.35, 0.01, 1, 0, False), 24: (1.16, 10, 0.35, 0.01, 1, 0, False),
         25: (1.08, 8, 0.6, 0.01, 1, 0, False), 26: (1.08, 10, 0.35, 0.03, 1, 0, False),
         27: (1.08, 10, 0.35, 0.01, 0.9, 0, False), 28: (1.08, 10, 0.35, 0.01, 1.1, 0, False),
         29: (1.08, 9, 0.45, 0.015, 1, 0.2, False), 30: (1.08, 10, 0.35, 0.01, 1, 0, True)}


def g(x):
    return simba_tts._n(x)


def jarvis_mk(n, sr=24000):
    """(noise, graph) exactly as Jarvis's mk() shell function built them for samples 11-20."""
    _, p, chain = J_MK[n]
    fx, noise, w = J_FX[chain]
    return noise, (f"[0]asetrate={sr}*{g(p)},aresample=48000,atempo={g(round(1 / p, 4))},{fx}[v];"
                   f"[v][1]amix=inputs=2:duration=first:weights={w},volume=1.15")


def jarvis_v16(n, sr=24000):
    """(noise, graph) exactly as Jarvis's v16() shell function built them for samples 21-30."""
    p, bits, mix, noise, mul, trem, echo = J_V16[n]
    tr = f"tremolo=f=34:d={g(trem)}," if trem else ""
    ec = ",aecho=0.8:0.6:60|110:0.3|0.2" if echo else ""
    return (f"anoisesrc=color=pink:amplitude={g(noise)}:sample_rate=48000",
            f"[0]asetrate={sr}*{g(p)},aresample=48000,atempo={g(round(1 / p * mul, 4))},highpass=f=100,"
            f"lowpass=f=4600,{tr}acrusher=bits={bits}:mix={g(mix)}:mode=log:aa=1,asoftclip=type=tanh:threshold=0.3:"
            f"output=1.4,aexciter=amount=2:freq=2200,acompressor=threshold=-26dB:ratio=5[v];"
            f"[v][1]amix=inputs=2:duration=first:weights=1 0.4,volume=1.15{ec}")


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


# ---- the recipes ------------------------------------------------------------------------------------------
def graph_of(effect, sr=24000, fmt="wav"):
    a = simba_tts.ffmpeg_args(effect, sr, Path("/in.wav"), Path("/out." + fmt), fmt)
    lead = simba_tts.ENCODE[fmt][1]
    graph = a[a.index("-filter_complex") + 1]
    assert graph.endswith("," + lead), graph
    return a[a.index("lavfi") + 2], graph[: -len(lead) - 1]


class TheRecipesAreJarvissSamples(unittest.TestCase):
    """Every sample number makes Jarvis's filter chain, character for character (as his session typed it)."""

    def test_1_to_10_are_smoky_and_raspy_with_the_voice_each_was_made_with(self):
        voices_ = ["af_nicole", "af_kore", "af_river", "af_jessica", "af_nova"]
        for n in range(1, 11):
            voice, _ = simba_tts.SAMPLES[n]
            self.assertEqual(voice, voices_[(n - 1) // 2], n)
            want = K_SMOKY if n % 2 else K_RASPY
            self.assertEqual(graph_of(str(n))[1], want.format(sr=24000), n)
            self.assertEqual(simba_tts.recipe(str(n))["speed"], 0.92, n)
        self.assertEqual(graph_of("smoky")[1], K_SMOKY.format(sr=24000))
        self.assertEqual(graph_of("raspy")[1], K_RASPY.format(sr=24000))
        self.assertEqual(graph_of("raspy")[0], "anoisesrc=color=white:amplitude=0.02:sample_rate=48000")

    def test_11_to_20_are_jarviss_mk_chains(self):
        for n, (voice, _, _) in J_MK.items():
            self.assertEqual(simba_tts.SAMPLES[n][0], voice, n)
            self.assertEqual(graph_of(str(n)), jarvis_mk(n), n)
            self.assertEqual(simba_tts.recipe(str(n))["speed"], 0.95, n)

    def test_16_is_fenrir16_and_the_default(self):
        self.assertEqual(graph_of("16"), graph_of("fenrir16"))
        self.assertEqual(simba_tts.DEFAULT_EFFECT, "fenrir16")
        self.assertEqual(simba_tts.sample_voice("fenrir16"), "am_fenrir")
        noise, graph = graph_of("16")
        self.assertIn("asetrate=24000*1.08,aresample=48000,atempo=0.9259,highpass=f=100,lowpass=f=4600", graph)

    def test_21_to_30_are_jarviss_variations_of_16(self):
        for n in J_V16:
            self.assertEqual(simba_tts.SAMPLES[n][0], "am_fenrir", n)
            self.assertEqual(graph_of(str(n)), jarvis_v16(n), n)
            self.assertEqual(simba_tts.recipe(str(n))["speed"], 0.95, n)

    def test_the_pitch_follows_the_rate_kokoro_really_gave(self):
        self.assertIn("asetrate=22050*0.85", graph_of("smoky", sr=22050)[1])
        self.assertIn("asetrate=22050*1.08", graph_of("16", sr=22050)[1])

    def test_plain_is_kokoro_as_it_is_with_only_the_lead_silence(self):
        a = simba_tts.ffmpeg_args("plain", 24000, Path("/in.wav"), Path("/out.wav"), "wav")
        self.assertNotIn("-filter_complex", a)
        self.assertNotIn("lavfi", a)
        self.assertIn("adelay=200:all=1", a)
        self.assertEqual(simba_tts.recipe("plain speed=0.95")["speed"], 0.95)

    def test_every_sample_and_name_ends_with_the_lead_silence_and_a_mono_file(self):
        for effect in [*map(str, simba_tts.SAMPLES), *simba_tts.PRESETS]:
            for fmt in simba_tts.ENCODE:
                a = simba_tts.ffmpeg_args(effect, 24000, Path("/in.wav"), Path("/out." + fmt), fmt)
                self.assertTrue(any("adelay=" in x for x in a), (effect, fmt))
                self.assertEqual(a[a.index("-ac") + 1], "1")

    def test_every_sample_number_1_to_30_is_a_setting_the_script_accepts(self):
        self.assertEqual(sorted(simba_tts.SAMPLES), list(range(1, 31)))
        for n, (voice, _) in simba_tts.SAMPLES.items():
            self.assertIsNone(simba_tts.voice_problem(voice), n)
            self.assertIsNone(simba_tts.effect_problem(str(n)), n)
            self.assertEqual(simba_tts.sample_voice(f"#{n} slower"), voice, n)


class PetersWordsAreTheRecipe(unittest.TestCase):
    """'Say a number, or combine them, like 23 but slower and breathier' (Jarvis to Peter, 2026-11-10)."""

    def knobs(self, text):
        p = simba_tts.recipe(text)
        return {k: p[k] for k in simba_tts.KNOBS}

    def test_23_but_slower_and_breathier(self):
        want = dict(self.knobs("16"), pitch=1.12, tempo=0.9, noise=0.03)
        self.assertEqual(self.knobs("23 but slower and breathier"), want)
        self.assertEqual(self.knobs("23, slower + breathier"), want)
        self.assertEqual(self.knobs("Sample #23 but Slower and BREATHIER"), want)

    def test_a_variation_number_after_the_first_adds_what_made_it_different(self):
        self.assertEqual(self.knobs("23 26"), self.knobs("23 breathier"))
        self.assertEqual(self.knobs("16 27"), self.knobs("27"))
        self.assertEqual(self.knobs("23 29")["pitch"], 1.12)
        self.assertEqual(self.knobs("23 29")["tremolo"], 0.2)

    def test_each_word_does_what_jarvis_did_for_that_sample(self):
        base = self.knobs("16")
        for word, n in (("lowest", 21), ("lower", 22), ("higher", 23), ("highest", 24), ("crunchier", 25),
                        ("breathier", 26), ("slower", 27), ("faster", 28), ("rougher", 29), ("echo", 30)):
            self.assertEqual(self.knobs(f"16 {word}"), self.knobs(str(n)), word)
            self.assertNotEqual(self.knobs(f"16 {word}"), base, word)

    def test_pitch_words_step_from_where_you_are_and_tempo_words_multiply(self):
        self.assertEqual(self.knobs("23 higher")["pitch"], 1.16)
        self.assertEqual(self.knobs("16 slower slower")["tempo"], 0.81)
        self.assertEqual(self.knobs("smoky slower")["tempo"], round(0.952 * 0.9, 4))

    def test_every_knob_can_be_set_and_reaches_the_sound(self):
        cases = {"speed": ("1.1", None), "pitch": ("1.2", "asetrate=24000*1.2,"),
                 "tempo": ("0.8", "atempo=0.7407,"), "bits": ("6", "acrusher=bits=6:"),
                 "mix": ("0.7", ":mix=0.7:"), "noise": ("0.05", "amplitude=0.05:"),
                 "tremolo": ("0.15", "tremolo=f=34:d=0.15,"), "echo": ("on", ",aecho=0.8:0.6:60|110:0.3|0.2")}
        self.assertEqual(set(cases), set(simba_tts.KNOBS))
        for knob, (value, in_sound) in cases.items():
            p = simba_tts.recipe(f"16 {knob}={value}")
            if knob == "echo":
                self.assertIs(p["echo"], True)
            else:
                self.assertEqual(p[knob], float(value), knob)
            if in_sound:
                noise, graph = graph_of(f"16 {knob}={value}")
                self.assertIn(in_sound, noise + "|" + graph, knob)
                self.assertNotIn(in_sound, "|".join(graph_of("16")), knob)
        self.assertIs(simba_tts.recipe("30 echo=off")["echo"], False)

    def test_what_it_cannot_make_is_refused_with_a_sentence(self):
        for bad, words in (("spooky", "sample number"), ("", "empty"), ("16 louder", "not a change"),
                           ("16 7", "Only one sample"), ("16 31", "Only one sample"), ("31", "sample number"),
                           ("16 pitch=abc", "needs a number"), ("16 pitch=3", "between 0.5 and 2"),
                           ("16 speed=2", "between 0.5 and 1.5"), ("16 noise=-0.1", "between 0 and 0.2"),
                           ("16 bits=1", "between 2 and 16"), ("16 mix=1.5", "between 0 and 1"),
                           ("16 tremolo=2", "between 0 and 1"), ("16 volume=2", "not a knob"),
                           ("16 echo=maybe", "on or off"), ("plain pitch=1.2", "only speed"),
                           ("16 pitch=2 tempo=0.6", "at least 0.5")):
            problem = simba_tts.effect_problem(bad)
            self.assertIsNotNone(problem, bad)
            self.assertIn(words, problem, bad)

    def test_the_panel_can_say_what_a_recipe_is(self):
        self.assertEqual(simba_tts.describe("23 but slower and breathier"),
                         "speed 0.95 · pitch 1.12 · tempo 0.9 · crush 10 bits mix 0.35 · noise 0.03")
        self.assertIn("tremolo 0.2", simba_tts.describe("29"))
        self.assertIn("echo", simba_tts.describe("30"))
        self.assertIn("without an effect", simba_tts.describe("plain"))

    def test_a_stored_recipe_is_lower_case_with_single_spaces(self):
        self.assertEqual(simba_tts.clean_effect("  23 But   Slower "), "23 but slower")


class TheVoiceRule(unittest.TestCase):
    """Peter: American accent only. Sample 16 is a male Kokoro voice (am_fenrir) pitched up."""

    def test_american_kokoro_voices_female_and_male(self):
        for ok in ("af_nicole", "af_kore", "am_fenrir", "am_onyx", "am_michael", "Nicole", "fenrir", " Fenrir "):
            self.assertIsNone(simba_tts.voice_problem(ok), ok)
        for bad in ("bf_emma", "bm_george", "en-US-AvaNeural", "zf_xiaoxiao", "jf_alpha", "", "af_nobody",
                    "am_nobody", "nicole smoky"):
            self.assertIsNotNone(simba_tts.voice_problem(bad), bad)

    def test_a_short_name_means_the_american_voice_of_that_name(self):
        self.assertEqual(simba_tts.clean_voice(" Nicole "), "af_nicole")
        self.assertEqual(simba_tts.clean_voice("fenrir"), "am_fenrir")
        self.assertEqual(simba_tts.clean_voice("af_kore"), "af_kore")

    def test_the_list_is_every_american_voice_in_the_model_file(self):
        self.assertEqual({v[:3] for v in simba_tts.VOICES}, {"af_", "am_"})
        self.assertEqual(len(simba_tts.VOICES), 20)

    def test_importing_the_script_loads_no_model(self):
        r = subprocess.run([sys.executable, "-c", "import sys, simba_tts; print('kokoro_onnx' in sys.modules, "
                            "'numpy' in sys.modules)"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "False False", r.stderr)


# ---- the script -------------------------------------------------------------------------------------------
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

    def test_sample_16_is_am_fenrir_at_speed_095_with_american_rules_pitched_up_tempo_kept(self):
        out = self.tmp / "say.wav"
        r = self.run_script(out, "am_fenrir", "16")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_bytes()[:4], b"RIFF")
        codec, rate, channels, secs = probe(out)
        self.assertEqual((codec, rate, channels), ("pcm_s16le", 48000, 1))
        self.assertAlmostEqual(secs, 1 + 0.2, delta=0.06, msg="tempo kept: 1 s of Kokoro + 0.2 s lead")
        [call] = self.calls()
        self.assertEqual((call["voice"], call["speed"], call["lang"]), ("am_fenrir", 0.95, "en-us"))
        self.assertEqual(call["text"], self.txt.read_text())
        self.assertEqual(call["paths"], [str(self.models / "kokoro-v1.0.onnx"), str(self.models / "voices-v1.0.bin")])

    def test_fenrir16_is_the_default_effect(self):
        out = self.tmp / "d.wav"
        self.assertEqual(self.run_script(out, "fenrir").returncode, 0)
        self.assertEqual([(c["voice"], c["speed"]) for c in self.calls()], [("am_fenrir", 0.95)])

    def test_peters_words_reach_kokoro_and_ffmpeg(self):
        slow, knob = self.tmp / "slow.wav", self.tmp / "knob.wav"
        r = self.run_script(slow, "am_fenrir", "23 but slower and breathier")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertAlmostEqual(probe(slow)[3], 1 / 0.9 + 0.2, delta=0.06)
        self.assertEqual(self.run_script(knob, "af_kore", "16 speed=1.1 tempo=1.25").returncode, 0)
        self.assertAlmostEqual(probe(knob)[3], 1 / 1.25 + 0.2, delta=0.06)
        self.assertEqual([(c["voice"], c["speed"]) for c in self.calls()], [("am_fenrir", 0.95), ("af_kore", 1.1)])

    def test_raspy_and_plain_still_work(self):
        raspy, plain = self.tmp / "r.wav", self.tmp / "p.wav"
        self.assertEqual(self.run_script(raspy, "af_kore", "raspy").returncode, 0)
        self.assertEqual(self.run_script(plain, "af_nicole", "plain").returncode, 0)
        self.assertAlmostEqual(probe(raspy)[3], 1 / (0.78 * 1.08) + 0.2, delta=0.06)
        self.assertAlmostEqual(probe(plain)[3], 1.2, delta=0.06)
        self.assertEqual(probe(plain)[1], 24000, "plain keeps Kokoro's own rate")
        self.assertEqual([c["speed"] for c in self.calls()], [0.92, 0.92])

    def test_the_file_ending_picks_the_format(self):
        for name, magic in (("v.ogg", b"OggS"), ("v.mp3", None)):
            out = self.tmp / name
            self.assertEqual(self.run_script(out, "af_nova", "30").returncode, 0, name)
            self.assertGreater(out.stat().st_size, 1000)
            if magic:
                self.assertEqual(out.read_bytes()[:4], magic)
            else:
                self.assertEqual(probe(out)[0], "mp3")

    def test_a_voice_or_effect_it_does_not_make_writes_nothing_and_never_loads_kokoro(self):
        for args in (("bf_emma", "16"), ("bm_george",), ("am_fenrir", "spooky"), ("am_fenrir", "16 pitch=9")):
            out = self.tmp / "no.wav"
            r = self.run_script(out, *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertFalse(out.exists(), args)
        self.assertEqual(self.calls(), [])

    def test_a_line_that_is_not_english_writes_nothing_and_never_loads_kokoro(self):
        for text in ("走開啦，人類。", "Fine, 好啦.", "うるさい", "ｗｈａｔ"):            # Peter 2026-11-10: English ONLY
            self.txt.write_text(text, encoding="utf-8")
            out = self.tmp / "zh.ogg"
            r = self.run_script(out, "am_fenrir", "16")
            self.assertEqual(r.returncode, 2, text)
            self.assertIn("English only", r.stderr)
            self.assertFalse(out.exists(), text)
        self.assertEqual(self.calls(), [])
        self.assertIsNone(simba_tts.NOT_ENGLISH.search("Ugh — fine, café, naïve… “quotes” ’n stuff!"))

    def test_if_kokoro_fails_it_says_so_and_writes_nothing(self):
        out = self.tmp / "no.wav"
        r = self.run_script(out, "am_fenrir", "16", fail="load")
        self.assertEqual(r.returncode, 1)
        self.assertIn("model will not load", r.stderr)
        self.assertFalse(out.exists())


@unittest.skipUnless((JARVIS / "k2_am_fenrir.wav").exists() and (JARVIS / "j_16_am_fenrir_smoky.ogg").exists(),
                     "Jarvis's sample files are not on this Mac")
class JarvisSamples(unittest.TestCase):
    """Re-make every numbered sample from the very Kokoro recording Jarvis used (k_<voice>.wav at speed 0.92 for
    1-10, k2_<voice>.wav at 0.95 for 11-30) with the real ffmpeg, and lay it next to the file he sent Peter.
    Ours carries 0.4 s of lead silence; his noise is random, so the waveforms match closely, not exactly."""

    @staticmethod
    def pcm(path, skip=0.0):
        import numpy as np
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-af",
                              f"atrim=start={skip}", "-f", "s16le", "-"], capture_output=True, timeout=60).stdout
        return np.frombuffer(raw, dtype="<i2").astype("float64")

    @staticmethod
    def corr(a, b, lag=480):
        import numpy as np
        n = min(len(a), len(b)) - 2 * lag
        return max(float(np.corrcoef(a[lag + d: lag + d + n], b[lag: lag + n])[0, 1]) for d in range(-lag, lag + 1, 16))

    def test_every_number_1_to_30_comes_out_as_long_as_jarviss_and_sounds_like_it(self):
        tmp = Path(tempfile.mkdtemp(prefix="vd-simba-jarvis-"))
        for n in range(1, 31):
            voice, _ = simba_tts.SAMPLES[n]
            base = JARVIS / (f"k_{voice}.wav" if n <= 10 else f"k2_{voice}.wav")
            his = next(JARVIS.glob(f"k_{n:02d}_*.ogg" if n <= 10 else f"j_{n}_*.ogg" if n <= 20 else f"v_{n}_*.ogg"))
            self.assertIn(voice, his.name if n <= 20 else "am_fenrir", n)
            out = tmp / f"{n}.ogg"
            r = subprocess.run(simba_tts.ffmpeg_args(str(n), 24000, base, out, "ogg"), capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, (n, r.stderr))
            self.assertAlmostEqual(probe(out)[3], probe(his)[3] + 0.4, delta=0.03, msg=n)
            if n in (16, 21, 23, 27):                    # the waveform tells pitch and tempo apart (16 vs 23 ~0.08)
                self.assertGreater(self.corr(self.pcm(out, 0.4), self.pcm(his)), 0.8, n)


# ---- Voice Desk speaking with it ---------------------------------------------------------------------------
class VoiceDeskSpeaksWithIt(unittest.TestCase):
    def setUp(self):
        self.w = simba_world()
        self.cfg = agents.load(self.w.toml)

    def voice(self):
        with mock.patch.object(voices, "config", return_value=self.cfg):
            return voices.voice_for("simba")

    def test_voice_for_simba_is_her_kokoro_voice_effect_and_english_only(self):
        v = self.voice()
        self.assertEqual((v.en, v.zh, v.effect), ("am_fenrir", "", "fenrir16"))
        self.assertFalse(hasattr(v, "fallback"), "one voice, no backup (Peter 2026-11-11)")
        self.assertEqual(v.command, ("/nonexistent/python", "/nonexistent/simba_tts.py"))
        self.assertEqual(voices.short(v), "Kokoro am_fenrir · English only")

    def test_english_only_wins_even_over_a_mandarin_voice_left_in_agents_toml(self):
        self.w.toml.write_text(self.w.toml.read_text().replace("english_only = true",
                                                               "english_only = true\nvoice_zh = \"zh-TW-HsiaoYuNeural\""))
        self.cfg = agents.load(self.w.toml)
        self.assertEqual(self.voice().zh, "")
        self.assertEqual(G.voice_info(agent_of(self.w.toml, "simba"), self.cfg).zh, "")

    def test_there_is_no_shared_voice_to_switch_her_to(self):
        self.assertFalse(hasattr(voices, "per_profile_enabled"))
        self.assertEqual(self.voice().source, "agents")

    def test_the_script_gets_the_effect_after_the_voice_and_esther_gets_exactly_what_she_got_before(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append(list(argv))
            Path(next(a for a in argv if a.endswith(".wav"))).write_bytes(b"RIFF....")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        tmp = Path(tempfile.mkdtemp(prefix="vd-simba-run-"))
        with mock.patch.object(tts.subprocess, "run", side_effect=fake_run):
            self.assertTrue(tts._script(("py", "simba_tts.py"), tmp / "t.txt", tmp / "a.wav", "am_fenrir",
                                        effect="23 but slower and breathier"))
            self.assertTrue(tts._script(("py", "esther_tts.py"), tmp / "t.txt", tmp / "b.wav", "alloy"))
        self.assertEqual(seen[0], ["py", "simba_tts.py", str(tmp / "t.txt"), str(tmp / "a.wav"), "am_fenrir",
                                   "23 but slower and breathier"], "the whole recipe is one argument")
        self.assertEqual(seen[1], ["py", "esther_tts.py", str(tmp / "t.txt"), str(tmp / "b.wav"), "alloy"])

    def test_english_goes_through_her_script_chinese_stays_silent_and_a_failure_stays_silent(self):
        v = self.voice()
        made = []

        def script(cmd, txt, out, voice, timeout=120, effect=None):
            made.append(("script", voice, effect))
            return None if "fail" in txt.read_text() else out

        def chain(txt, out, voice):
            made.append(("edge", voice))
            return out

        with mock.patch.object(tts, "_script", side_effect=script), mock.patch.object(tts, "_chain", side_effect=chain), \
                mock.patch.object(tts.subprocess, "run", side_effect=AssertionError("no Mac voice for her")):
            self.assertIsNotNone(tts.synthesize("Go away, human.", v))
            self.assertIsNone(tts.synthesize("走開啦，人類。", v), "Peter 2026-11-10: English only")
            self.assertIsNone(tts.synthesize("Fine, 好啦, whatever.", v), "a Chinese word in an English line too")
            self.assertIsNone(tts.synthesize("this one will fail", v), "Peter 2026-11-11: a failed voice is silent")
        self.assertEqual(made, [("script", "am_fenrir", "fenrir16"), ("script", "am_fenrir", "fenrir16")],
                         "no Edge voice ever takes over")

    def test_esther_keeps_her_mandarin_voice(self):
        esther = agent_of(self.w.toml, "esther")
        self.assertFalse(esther.english_only)
        with mock.patch.object(voices, "config", return_value=self.cfg):
            v = voices.voice_for(esther.target)
        self.assertEqual(v.zh, "zh-TW-HsiaoYuNeural")
        with mock.patch.object(tts, "_chain", return_value=Path("/zh.wav")) as chain:
            self.assertEqual(tts.synthesize("走開啦。", v), Path("/zh.wav"))
        self.assertEqual(chain.call_args.args[2], "zh-TW-HsiaoYuNeural")

    def test_the_voice_check_tests_her_english_only(self):
        import voicetest
        simba = agent_of(self.w.toml, "simba")
        self.assertEqual(voicetest.voices_to_test(simba, self.cfg), [("en", "am_fenrir")])
        self.assertEqual([lang for lang, _ in voicetest.voices_to_test(agent_of(self.w.toml, "kylie"), self.cfg)],
                         ["en", "zh"])

    def test_the_silent_voice_test_runs_her_script_with_the_effect(self):
        import voicetest
        simba = agent_of(self.w.toml, "simba")
        with mock.patch.object(tts, "_script", return_value=Path("/x")) as script:
            via = voicetest._real_synth(Path("/t.txt"), Path("/o.mp3"), "am_fenrir", simba, "en")
        self.assertEqual(via, "voice")
        self.assertEqual(script.call_args.kwargs.get("effect"), "fenrir16")


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
        self.assertTrue(simba.english_only, "Peter 2026-11-10: English ONLY")
        self.assertIsNone(simba.voice_zh, "no Mandarin voice in her entry")
        self.assertEqual(Path(simba.command[-1]).resolve(), SCRIPT.resolve(), "agents.toml runs this repo's script")
        self.assertTrue(VE.uses_simba_script(simba))
        others = {VE._kokoro_name(a.voice) for a in agents.agents(cfg) if a.key != "simba"}
        self.assertNotIn(VE._kokoro_name(simba.voice), others, "a voice of her own")
        self.assertNotIn("fallback", simba.raw, "one voice, no backup (Peter 2026-11-11)")
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
        self.assertEqual((entry["old"], entry["new"], entry["ok"]), ("am_fenrir", "af_kore", True))
        self.assertEqual(Path(entry["toml_backup"]).read_text(), self.start)
        self.assertEqual(digest(self.others), self.before, "no Hermes, Claude or Gemini file is touched")
        self.assertIn("Simba English voice: am_fenrir → af_kore", VE.recent(self.w.edit)[0])

    def test_e_takes_american_male_voices_too(self):
        res = VE.change("simba", "voice", "onyx", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.simba().voice, "am_onyx")

    def test_f_takes_peters_words_as_one_setting(self):
        res = VE.change("simba", "effect", "  23 But Slower and  Breathier ", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.simba().effect, "23 but slower and breathier")
        self.assertIn("Voice Desk uses it from the next line", res.message)
        self.assertNotIn("was made with", res.message, "23 is a fenrir sample and she already speaks with fenrir")
        self.assertIn("Simba voice effect: fenrir16 → 23 but slower and breathier", VE.recent(self.w.edit)[0])
        self.assertEqual(tomllib.loads(self.w.toml.read_text())["agents"]["simba"]["effect"],
                         "23 but slower and breathier")
        self.assertEqual(digest(self.others), self.before)

    def test_f_says_which_voice_a_sample_was_made_with_when_hers_differs(self):
        res = VE.change("simba", "effect", "8", self.w.edit)
        self.assertTrue(res.ok, res.message)
        self.assertIn("That sample was made with af_jessica; Simba speaks with am_fenrir (E changes it).", res.message)
        self.assertEqual(self.simba().voice, "am_fenrir", "a number sets the effect only")

    def test_knob_settings_go_through_the_panel_and_undo(self):
        self.assertTrue(VE.change("simba", "effect", "16 pitch=1.1 tremolo=0.15 echo=on", self.w.edit).ok)
        self.assertEqual(self.simba().effect, "16 pitch=1.1 tremolo=0.15 echo=on")
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.w.toml.read_text(), self.start)

    def test_what_she_may_not_have_is_refused_and_nothing_is_written(self):
        for field, value, words in ((("voice", "bf_emma", "American Kokoro")), ("voice", "bm_george", "American"),
                                    ("voice", "en-US-AvaNeural", "American Kokoro"),
                                    ("voice", "alloy", "Esther's voice"), ("voice", "af_alloy", "Esther's voice"),
                                    ("effect", "spooky", "sample number"), ("effect", "16 pitch=3", "between"),
                                    ("effect", "16 louder", "not a change"), ("effect", "", "empty"),
                                    ("voice_zh", "zh-CN-XiaoxiaoNeural", "English only"),
                                    ("voice_zh", "zh-TW-HsiaoYuNeural", "English only"),
                                    ("voice_override", "zh", "English only"),
                                    ("voices_extra", {"ja": "ja-JP-NanamiNeural"}, "English only")):
            res = VE.change("simba", field, value, self.w.edit)
            self.assertFalse(res.ok, (field, value))
            self.assertIn(words, res.message, (field, value))
        self.assertEqual(self.w.toml.read_text(), self.start)
        self.assertEqual(VE.read_history(self.w.edit), [])

    def test_only_simba_has_an_effect(self):
        res = VE.change("kylie", "effect", "16", self.w.edit)
        self.assertFalse(res.ok)
        self.assertIn("no voice effect", res.message)
        res = VE.change("esther", "effect", "smoky", self.w.edit)
        self.assertFalse(res.ok, "Esther's Kokoro script has no effect")
        self.assertEqual(self.w.toml.read_text(), self.start)

    def test_others_keep_their_rules(self):
        # Peter 2026-11-11: any agent may pick a Kokoro voice now (Kylie keeps Ava as her fallback); Simba's own
        # rules (American only, an effect, a voice no one else has) still apply only to Simba
        self.assertTrue(VE.change("kylie", "voice", "af_kore", self.w.edit).ok, "Kylie may pick a Kokoro voice")
        self.assertFalse(VE.change("kylie", "voice", "kore", self.w.edit).ok, "but only by its full Kokoro name")
        self.assertTrue(VE.change("esther", "voice", "bella", self.w.edit).ok, "Esther's Kokoro names are hers to pick")

    def test_peters_pick_is_two_changes_and_two_undos_put_everything_back_byte_for_byte(self):
        voice, _ = simba_tts.SAMPLES[8]                                       # "sample 8" = jessica raspy
        self.assertTrue(VE.change("simba", "voice", voice, self.w.edit).ok)
        self.assertTrue(VE.change("simba", "effect", "8", self.w.edit).ok)
        self.assertEqual((self.simba().voice, self.simba().effect), ("af_jessica", "8"))
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.simba().effect, "fenrir16")
        self.assertTrue(VE.undo(self.w.edit).ok)
        self.assertEqual(self.w.toml.read_text(), self.start)
        self.assertEqual(digest(self.others), self.before)


class TheGridShowsHer(unittest.TestCase):
    def setUp(self):
        self.w = simba_world()
        self.cfg = agents.load(self.w.toml)
        self.simba = agent_of(self.w.toml, "simba")

    def test_she_is_the_last_row_and_only_her_telegram_group_cell_has_a_value(self):
        self.assertEqual(G.grid_agents(self.cfg)[-1].key, "simba")
        cells = G.cells_for(self.simba, self.w.paths)
        group = cells.pop("tg_group")
        self.assertEqual({c.value for c in cells.values()}, {G.NA})
        self.assertTrue(all("voice only" in c.note for c in cells.values()))
        self.assertEqual(group.value, G.VOICE_BACK)
        self.assertIn("voice notes", group.note)
        self.assertIn("English only, at most 15 a day", group.note)
        import onoff
        self.assertFalse(onoff.can_switch(self.simba, "tg_group"), "switched in simba.yaml, never from the grid")

    def test_her_telegram_group_cell_follows_simba_yaml(self):
        y = self.w.paths.simba_yaml
        y.write_text(y.read_text().replace("enabled: true", "enabled: false"))
        self.assertEqual(G.tg_group_cell(self.simba, self.w.paths).value, G.OFF)
        import gridevidence as GE
        row = next(r for r in GE.build(self.cfg, self.w.paths, GE.LogReader(self.w.paths.profiles))
                   if r.agent.key == "simba")
        self.assertEqual((row.evidence["tg_group"].headline, row.evidence["tg_group"].detail),
                         ("off", "Her Team_Room replies go out as text only."))
        y.unlink()
        self.assertEqual(G.tg_group_cell(self.simba, self.w.paths).value, G.UNKNOWN)
        kylie = agent_of(self.w.toml, "kylie")
        self.assertNotIn("simba", G.tg_group_cell(kylie, self.w.paths).note, "nobody else reads her file")

    def test_the_grid_rows_carry_her_group_cell_with_plain_evidence(self):
        import gridevidence as GE
        rows = GE.build(self.cfg, self.w.paths, GE.LogReader(self.w.paths.profiles))
        row = next(r for r in rows if r.agent.key == "simba")
        self.assertEqual(row.evidence["tg_group"].status, "idle")
        self.assertIn("simba.py", row.evidence["tg_group"].detail)
        detail = V.detail_text(row, None).plain
        self.assertIn("Telegram group", detail)
        self.assertIn("English only (a Chinese line stays silent)", detail)

    def test_the_voice_cell_and_the_detail_name_her_voice_and_effect(self):
        info = G.voice_info(self.simba, self.cfg)
        self.assertEqual((info.en, info.zh, info.effect), ("am_fenrir", "", "fenrir16"))
        row = mock.Mock(voice=info, agent=self.simba, kind=G.OTHER, cells={}, evidence={})
        cell = V.voice_text(row).plain
        self.assertIn("am_fenrir · English only", cell)
        self.assertIn("fenrir16", cell)
        self.assertNotIn("fenrir16", V.voice_text(mock.Mock(voice=G.voice_info(agent_of(self.w.toml, "kylie"),
                                                                                 self.cfg))).plain)
        import gridevidence as GE
        cells = G.cells_for(self.simba, self.w.paths)
        detail = V.detail_text(mock.Mock(voice=info, agent=self.simba, kind=G.OTHER, cells=cells,
                                         evidence=dict.fromkeys(cells, GE.Evidence("idle", "", "")), quiet={}),
                               None).plain
        self.assertIn("effect: fenrir16 = speed 0.95 · pitch 1.08 · tempo 1", detail)


class TheGridKeys(unittest.IsolatedAsyncioTestCase):
    """E and F on Simba's row (pick-lists since 2026-11-13), then U U, in the headless dashboard against the fake world."""

    async def test_e_and_f_on_simbas_row_then_u_u(self):
        import dashboard as D
        import voicepick
        from textual.widgets import Input, OptionList
        from tests.test_dashboard_grid import load, make_app, pick, text_of, wait_for
        w = simba_world()
        start = w.toml.read_text()
        app = make_app(w)
        async with app.run_test(size=(180, 50)) as pilot:
            await load(app, pilot)
            await pilot.press(*["down"] * 7, "f")                               # Simba is the 8th row
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            ids = [o.id for o in app.screen.query_one(OptionList)._options]
            self.assertEqual(ids[-1], voicepick.OTHER, "the last line is 'other, type it'")
            self.assertIn("23", ids)
            await pick(app, pilot, voicepick.OTHER)                              # the old typing box
            self.assertIsInstance(app.screen, D.AskScreen)
            field = app.screen.query_one(Input)
            self.assertEqual(field.value, "fenrir16", "the box starts with today's effect")
            field.value = "23 but slower and breathier"
            await pilot.press("enter")
            self.assertTrue(await wait_for(lambda: VE.read_history(w.edit)))
            await pilot.pause()
            self.assertIn("Simba voice effect: fenrir16 → 23 but slower and breathier", text_of(app, "#history"))
            await pilot.press("e")
            await pilot.pause()
            self.assertIsInstance(app.screen, D.ChoiceScreen)
            lst = app.screen.query_one(OptionList)
            ids = [o.id for o in lst._options]
            voices_on_list = [i for i in ids if not i.startswith("::")]
            self.assertTrue(voices_on_list and all(v[:3] in ("af_", "am_") for v in voices_on_list),
                            "American Kokoro voices only")
            self.assertNotIn("af_alloy", ids, "Esther's voice is hers")
            self.assertEqual(ids[lst.highlighted], "am_fenrir", "the list starts on her current voice")
            await pick(app, pilot, "af_jessica")
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
