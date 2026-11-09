"""Offline tests for the silent voice test: it renders into a temp folder, checks the file with the real
ffprobe, plays nothing, and reports a broken voice as broken instead of quietly using another one.

    .venv/bin/python -m unittest tests.test_voicetest -v
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import settings as S  # noqa: E402
import voicetest as VT  # noqa: E402


def make_wav(path: Path, seconds: float = 1.2) -> Path:
    subprocess.run([S.FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(path)],
                   check=True)
    return path


def agent_and_cfg(key):
    w = F.make_world()
    cfg = agents.load(w.toml)
    return next(a for a in agents.agents(cfg) if a.key == key), cfg


class TheProbe(unittest.TestCase):
    def test_real_audio_passes(self):
        ok, dur, what = VT.probe(make_wav(Path(tempfile.mkdtemp()) / "a.wav"))
        self.assertTrue(ok, what)
        self.assertAlmostEqual(dur, 1.2, delta=0.2)

    def test_text_pretending_to_be_audio_fails(self):
        p = Path(tempfile.mkdtemp()) / "a.mp3"
        p.write_text("this is not audio at all")
        self.assertFalse(VT.probe(p)[0])

    def test_an_empty_file_and_a_blip_fail(self):
        d = Path(tempfile.mkdtemp())
        (d / "e.wav").write_bytes(b"")
        self.assertFalse(VT.probe(d / "e.wav")[0])
        ok, _, what = VT.probe(make_wav(d / "blip.wav", 0.1))
        self.assertFalse(ok)
        self.assertIn("only", what)


class TheRun(unittest.TestCase):
    def test_each_language_is_rendered_with_its_own_configured_voice_and_nothing_is_played(self):
        a, cfg = agent_and_cfg("kylie")
        seen = []

        def synth(txt, out, voice, agent, lang):
            seen.append((lang, voice, txt.read_text(encoding="utf-8")))
            make_wav(out)
            return "voice"

        real, started = subprocess.Popen, []

        def spy(cmd, *args, **kw):                                         # every process started during the test
            started.append(str(cmd))
            return real(cmd, *args, **kw)

        with mock.patch("subprocess.Popen", spy):
            res = VT.run(a, cfg, synth=synth)
        self.assertFalse([c for c in started if "afplay" in c], "nothing is ever played")
        self.assertEqual([(lang, v) for lang, v, _ in seen], [("en", "en-US-AvaNeural"), ("zh", "zh-TW-HsiaoChenNeural")])
        self.assertIn("Kylie", seen[0][2])
        self.assertIn("Kylie", seen[1][2])
        self.assertTrue(any("\u4e00" <= c <= "\u9fff" for c in seen[1][2]), "the Mandarin test sentence is Mandarin")
        self.assertTrue(all(r.ok for r in res), res)
        self.assertEqual({r.voice for r in res}, {"en-US-AvaNeural", "zh-TW-HsiaoChenNeural"})

    def test_extra_languages_are_tested_too(self):
        w = F.make_world()
        w.toml.write_text(F.AGENTS_TOML.replace('names = ["jarvis"]', 'names = ["jarvis"]\nvoices_extra = { ja = "ja-JP-NanamiNeural" }'))
        cfg = agents.load(w.toml)
        a = next(x for x in agents.agents(cfg) if x.key == "jarvis")
        langs = []

        def synth(txt, out, voice, agent, lang):
            langs.append((lang, voice))
            make_wav(out)
            return "voice"

        VT.run(a, cfg, synth=synth)
        self.assertEqual(langs, [("en", "en-US-JennyNeural"), ("zh", "zh-TW-HsiaoChenNeural"), ("ja", "ja-JP-NanamiNeural")])

    def test_a_voice_that_cannot_be_made_is_reported_as_failed_not_skipped(self):
        a, cfg = agent_and_cfg("kylie")
        res = VT.run(a, cfg, synth=lambda *x: None)
        self.assertFalse(any(r.ok for r in res))
        self.assertIn("could not be made", res[0].detail)

    def test_a_synth_that_writes_garbage_fails_the_ffprobe_check(self):
        a, cfg = agent_and_cfg("kylie")

        def synth(txt, out, voice, agent, lang):
            out.write_text("garbage")
            return "voice"

        self.assertFalse(any(r.ok for r in VT.run(a, cfg, synth=synth)))

    def test_a_crashing_synth_does_not_crash_the_panel(self):
        a, cfg = agent_and_cfg("kylie")

        def boom(*x):
            raise RuntimeError("script exploded")

        res = VT.run(a, cfg, synth=boom)
        self.assertFalse(res[0].ok)
        self.assertIn("script exploded", res[0].detail)

    def test_esther_is_rendered_by_her_own_script_and_the_backup_is_labelled(self):
        a, cfg = agent_and_cfg("esther")
        self.assertTrue(a.command)

        def edge(txt, out, voice):
            make_wav(out)
            return True

        with mock.patch.object(VT.tts, "_script", return_value=None) as script, \
                mock.patch.object(VT, "_edge_direct", side_effect=edge) as direct:
            res = VT.run(a, cfg)
        script.assert_called_once()
        self.assertEqual(script.call_args.args[3], "alloy")
        self.assertEqual(direct.call_args_list[0].args[2], "en-US-AriaNeural", "the backup voice is asked for, by name")
        self.assertTrue(res[0].ok)
        self.assertEqual(res[0].via, "backup")
        self.assertIn("backup voice", res[0].detail)

    def test_gemini_and_codex_are_tested_with_the_shared_voice_they_really_use(self):
        for key in ("gemini", "codex"):
            a, cfg = agent_and_cfg(key)
            got = []

            def synth(txt, out, voice, agent, lang):
                got.append(voice)
                make_wav(out)
                return "voice"

            VT.run(a, cfg, synth=synth)
            self.assertEqual(got, ["en-US-JennyNeural", "zh-TW-HsiaoChenNeural"])

    def test_the_temp_files_are_cleaned_up(self):
        a, cfg = agent_and_cfg("kylie")
        made = []

        def synth(txt, out, voice, agent, lang):
            made.append(out.parent)
            make_wav(out)
            return "voice"

        VT.run(a, cfg, synth=synth)
        self.assertFalse(made[0].exists())

    def test_results_survive_a_restart(self):
        a, cfg = agent_and_cfg("kylie")

        def synth(txt, out, voice, agent, lang):
            make_wav(out)
            return "voice"

        res = VT.run(a, cfg, synth=synth)
        p = Path(tempfile.mkdtemp()) / "check.json"
        VT.save({"kylie": res}, p)
        self.assertEqual([(r.lang, r.ok) for r in VT.load(p)["kylie"]], [("en", True), ("zh", True)])
        self.assertEqual(VT.load(Path(tempfile.mkdtemp()) / "none.json"), {})


class TheRealEdgePath(unittest.TestCase):
    def test_edge_direct_refuses_when_there_is_no_edge_python(self):
        with mock.patch.object(VT, "edge_python", return_value=None):
            self.assertFalse(VT._edge_direct(Path("a.txt"), Path("a.mp3"), "en-US-AvaNeural"))

    def test_edge_direct_asks_for_exactly_the_named_voice_and_nothing_else(self):
        d = Path(tempfile.mkdtemp())
        txt, out, fake_host = d / "a.txt", d / "a.mp3", d / "edge_tts_newhost.py"
        txt.write_text("hello")
        fake_host.write_text("")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            out.write_bytes(b"x" * 10)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with mock.patch.object(VT, "edge_python", return_value="/usr/bin/python3"), \
                mock.patch.object(VT, "NEWHOST", fake_host), mock.patch("subprocess.run", fake_run):
            self.assertTrue(VT._edge_direct(txt, out, "zh-TW-HsiaoYuNeural"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index("--voice") + 1], "zh-TW-HsiaoYuNeural")


if __name__ == "__main__":
    unittest.main()
