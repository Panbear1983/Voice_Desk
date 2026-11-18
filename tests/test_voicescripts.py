"""The outside speaking scripts (voicescripts.py) and the shared speaker (speak_any.py), Phase B of the 2026-11-18
full-voice-list brief. Offline and silent: every run uses fakes (a fake shared speaker, fake Kokoro, fake `say`, fake
player) and throw-away copies; nothing is played and no live file is written.

The tests that switch COPIES of the real outside scripts run only on a Mac that has them (Peter's); everywhere else
they are skipped and the rules are still tested on small stand-in files.

    .venv/bin/python -m unittest tests.test_voicescripts -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tests  # noqa: E402,F401

import agents  # noqa: E402
import speak_any  # noqa: E402
import voicescripts as V  # noqa: E402

REAL_HOME = Path(os.path.expanduser("~"))


def original(s: V.Script):
    """The real outside script as it was BEFORE the switch: the live file if it is not switched yet, else the backup
    the install made of it (the newest install whose result is exactly today's file). None = neither."""
    live = REAL_HOME / s.rel
    if not live.is_file():
        return None
    if V.state(s, REAL_HOME) != V.SWITCHED:
        return live
    now = V._sha(live.read_bytes())
    for manifest in sorted(REAL_HOME.glob(".hermes/profile-backups/*-voice-scripts/manifest.json"), reverse=True):
        for e in json.loads(manifest.read_text()).get("files", ()):
            if e.get("key") == s.key and e.get("after") == now and Path(e["backup"]).is_file():
                return Path(e["backup"])
    return None


def have_real(s: V.Script) -> bool:
    return original(s) is not None


def copy_home(keys) -> Path:
    """A throw-away home holding copies of the real outside scripts (as they were before the switch) at their usual
    places."""
    base = Path(tempfile.mkdtemp(prefix="vd-scripts-home-"))
    for s in V.by_key(keys):
        dst = base / s.rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original(s), dst)
    if "claude-natural" in keys and not (base / ".claude/hooks/kokoro_speak.py").exists():
        shutil.copy2(REAL_HOME / ".claude/hooks/kokoro_speak.py", base / ".claude/hooks/kokoro_speak.py")
    return base


class TheRealScriptsOnCopies(unittest.TestCase):
    """Install on copies, smoke-tested with fakes, then restore: byte for byte what was there before."""

    def setUp(self):
        missing = [s.key for s in V.SCRIPTS if not have_real(s)]
        if missing:
            self.skipTest(f"the real outside scripts are not on this Mac ({', '.join(missing)})")
        self.keys = [s.key for s in V.SCRIPTS]
        self.base = copy_home(self.keys)
        self.backups = Path(tempfile.mkdtemp(prefix="vd-scripts-bak-"))
        self.before = {s.key: (self.base / s.rel).read_bytes() for s in V.SCRIPTS}
        self.modes = {s.key: os.stat(original(s)).st_mode for s in V.SCRIPTS}

    def test_every_edit_fits_today(self):
        for s in V.SCRIPTS:
            self.assertEqual(V.state(s, self.base), V.READY, s.key)

    def test_the_smoke_tests_catch_the_old_scripts(self):
        """Run on the UNEDITED copies every smoke test must complain: that is what makes it a test."""
        for s in V.SCRIPTS:
            with tempfile.TemporaryDirectory() as tmp:
                problems = s.smoke(s, self.base / s.rel, Path(tmp), None)
            self.assertTrue(problems, f"{s.key}: the old script passed the smoke test")

    def test_install_switches_every_copy_and_restore_puts_every_byte_back(self):
        lines = []
        results = V.install(self.keys, base=self.base, backups=self.backups, out=lines.append)
        self.assertTrue(all(r.ok for r in results), "\n".join(lines))
        for s in V.SCRIPTS:
            self.assertEqual(V.state(s, self.base), V.SWITCHED, s.key)
            self.assertEqual(os.stat(self.base / s.rel).st_mode, self.modes[s.key], "mode kept")
        [folder] = list(self.backups.iterdir())
        note = (folder / "RESTORE.md").read_text()
        for s in V.SCRIPTS:
            self.assertIn(str(self.base / s.rel), note)
        again = V.install(self.keys, base=self.base, backups=self.backups, out=lines.append)
        self.assertTrue(all("nothing written" in r.message for r in again), "a second install changes nothing")
        self.assertTrue(V.restore(folder, out=lines.append))
        for s in V.SCRIPTS:
            self.assertEqual((self.base / s.rel).read_bytes(), self.before[s.key], s.key)

    def test_a_dry_run_writes_nothing(self):
        lines = []
        V.install(self.keys, base=self.base, backups=self.backups, dry_run=True, out=lines.append)
        self.assertEqual(list(self.backups.iterdir()), [])
        for s in V.SCRIPTS:
            self.assertEqual((self.base / s.rel).read_bytes(), self.before[s.key])
        self.assertTrue(any("+native=1" in x for x in lines), "the diff is shown")


class TheLiveScripts(unittest.TestCase):
    """The switched scripts on this Mac, run read-only with fakes (the same smoke test the install ran): each still
    makes its voice through the shared speaker, stays silent when that fails, and never calls a second voice."""

    def test_every_switched_live_script_passes_its_smoke_test(self):
        live = [s for s in V.SCRIPTS if V.state(s, REAL_HOME) == V.SWITCHED]
        if not live:
            self.skipTest("no outside script is switched on this Mac")
        for s in live:
            before = (REAL_HOME / s.rel).read_bytes()
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(s.smoke(s, REAL_HOME / s.rel, Path(tmp), None), [], s.key)
            self.assertEqual((REAL_HOME / s.rel).read_bytes(), before, f"{s.key}: the smoke test never writes it")


def stand_in(base: Path, text: str) -> Path:
    p = base / "scripts/demo.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(0o755)
    return p


class TheInstallRules(unittest.TestCase):
    """The same install on a small stand-in script, so the rules are tested on every machine."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="vd-scripts-rules-"))
        self.backups = self.base / "backups"
        self.path = stand_in(self.base, "#!/bin/sh\necho old\nexit 0\n")

    def script(self, smoke=lambda s, p, t, b: [], edits=(("echo old\n", f"# {V.MARK}\necho new\n"),)):
        return V.Script("demo", "Demo", "scripts/demo.sh", "bash", V.MARK, edits, "demo edit", smoke)

    def run_install(self, scripts):
        old = V.SCRIPTS
        V.SCRIPTS = tuple(scripts)
        try:
            return V.install([s.key for s in scripts], base=self.base, backups=self.backups, out=lambda _: None)
        finally:
            V.SCRIPTS = old

    def test_a_failed_smoke_test_puts_the_file_back_and_stops(self):
        bad = self.script(smoke=lambda s, p, t, b: ["it spoke with a second voice"])
        later = V.Script("later", "Later", "scripts/later.sh", "bash", V.MARK, (("x", "y"),), "never reached")
        stand_in(self.base, "#!/bin/sh\necho old\nexit 0\n")
        results = self.run_install([bad, later])
        self.assertEqual(len(results), 1, "the install stops at the first failure")
        self.assertFalse(results[0].ok)
        self.assertIn("put back as it was", results[0].message)
        self.assertEqual(self.path.read_text(), "#!/bin/sh\necho old\nexit 0\n")
        [folder] = list(self.backups.iterdir())
        self.assertTrue(json.loads((folder / "manifest.json").read_text())["files"][0]["restored"])

    def test_a_crashing_smoke_test_counts_as_a_failure(self):
        def boom(*_):
            raise RuntimeError("fake crash")
        results = self.run_install([self.script(smoke=boom)])
        self.assertFalse(results[0].ok)
        self.assertEqual(self.path.read_text(), "#!/bin/sh\necho old\nexit 0\n")

    def test_text_that_does_not_fit_is_never_edited(self):
        s = self.script(edits=(("echo something else\n", "x"),))
        self.assertEqual(V.state(s, self.base), V.NOFIT)
        results = self.run_install([s])
        self.assertIn("does not fit", results[0].message)
        self.assertFalse(self.backups.exists(), "not even a backup folder")

    def test_restore_leaves_a_file_changed_by_hand_alone(self):
        self.run_install([self.script()])
        self.assertIn("echo new", self.path.read_text())
        self.path.write_text(self.path.read_text() + "# Peter's own line\n")
        [folder] = list(self.backups.iterdir())
        self.assertFalse(V.restore(folder, out=lambda _: None))
        self.assertIn("Peter's own line", self.path.read_text())

    def test_the_executable_bit_is_kept(self):
        self.run_install([self.script()])
        self.assertTrue(os.access(self.path, os.X_OK))


class WhoCanPlayWhat(unittest.TestCase):
    """The pick-lists ask `switched`: the agent's own file carries the mark AND the shared speaker is in place."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vd-switched-"))
        self.script = self.tmp / "own_tts.sh"
        self.script.write_text("#!/bin/sh\nexit 0\n")
        self.speaker = self.tmp / "speak_any.py"
        self.env = {k: os.environ.get(k) for k in ("VOICE_DESK_SPEAK_ANY", "VOICE_DESK_SPEAK_PY", "VOICE_DESK_GEMINI_HOOK")}
        os.environ["VOICE_DESK_SPEAK_ANY"] = str(self.speaker)
        os.environ["VOICE_DESK_SPEAK_PY"] = sys.executable
        os.environ["VOICE_DESK_GEMINI_HOOK"] = str(self.tmp / "gemini-hook.py")

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def agent(self, **kw):
        raw = {"display": "Demo", "names": ["demo"], "voice": "am_puck", **kw}
        cfg = {"agents": {"demo": raw}}
        return next(a for a in agents.agents(cfg))

    def test_a_script_needs_the_mark_and_the_shared_speaker(self):
        a = self.agent(profile="demo", command=["/bin/sh", str(self.script)])
        self.assertFalse(V.switched(a))
        self.assertIn("Kokoro voices only", V.speaker_problem(a, "voice", "en-US-AvaNeural"))
        self.script.write_text(f"#!/bin/sh\n# {V.MARK}\nexit 0\n")
        self.assertFalse(V.switched(a), "marked, but the shared speaker is not in place")
        self.speaker.write_text("# the shared speaker\n")
        self.assertTrue(V.switched(a))
        self.assertEqual(V.speaker_problem(a, "voice", "en-US-AvaNeural"), "")

    def test_gemini_reads_its_hook_and_agents_without_a_script_play_everything(self):
        g = self.agent(target="gemini", voice="en-US-AriaNeural")
        self.assertIn("Microsoft voices only", V.speaker_problem(g, "voice", "af_heart"))
        self.assertEqual(V.speaker_problem(g, "voice", "en-US-AvaNeural"), "")
        (self.tmp / "gemini-hook.py").write_text(f"# {V.MARK}\n")
        self.speaker.write_text("# the shared speaker\n")
        self.assertEqual(V.speaker_problem(g, "voice", "af_heart"), "")
        k = self.agent(profile="kylie", voice="en-US-AvaNeural")
        self.assertTrue(V.switched(k))
        self.assertEqual(V.speaker_problem(k, "voice", "af_heart"), "")

    def test_mandarin_never_depends_on_the_script(self):
        a = self.agent(profile="demo", command=["/bin/sh", str(self.script)])
        self.assertEqual(V.speaker_problem(a, "voice_zh", "zh-TW-HsiaoYuNeural"), "")

    def test_a_script_s_own_voices_are_never_not_playable(self):
        """Found by looking at the desk (2026-11-18): Esther's "alloy" is a name only her own script knows. Before
        any switch, every voice an agent has today must count as playable: nothing changes by default."""
        esther = self.agent(profile="tax_angel_esther", voice="alloy", command=["/x/python", str(self.script)])
        for name in ("alloy", "deepalloy", "af_heart", "bm_george"):
            self.assertEqual(V.speaker_problem(esther, "voice", name), "", name)
        simba = self.agent(profile="simba", voice="am_fenrir", command=["/x/python", str(self.tmp / "simba_tts.py")])
        for name in ("am_fenrir", "Fenrir", "af_nicole"):
            self.assertEqual(V.speaker_problem(simba, "voice", name), "", name)
        self.assertIn("American Kokoro", V.speaker_problem(simba, "voice", "bf_emma"))
        g = self.agent(target="gemini", voice="en-US-AriaNeural")
        self.assertEqual(V.speaker_problem(g, "voice", "en-US-AriaNeural"), "")


def tiny_wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(48000))                          # 1 s: 24000 16-bit frames
    return path


class TheSharedSpeaker(unittest.TestCase):
    """speak_any.py with a fake Kokoro script and a fake Microsoft helper: exactly the voice asked for, or nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vd-speak-any-"))
        self.log = self.tmp / "calls.log"
        self.wav = tiny_wav(self.tmp / "tiny.wav")
        self.txt = self.tmp / "say.txt"
        self.txt.write_text("Hello there.", encoding="utf-8")
        self.mode = "ok"

    def run_fake(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        with open(self.log, "a") as f:
            f.write(json.dumps(cmd) + "\n")
        if cmd[1:3] == ["-c", "import edge_tts"]:
            return subprocess.CompletedProcess(cmd, 0)
        if "--write-media" in cmd or cmd[:len(agents.KOKORO_COMMAND)] == list(agents.KOKORO_COMMAND):
            if self.mode != "ok":
                return subprocess.CompletedProcess(cmd, 1, b"", b"fake failure")
            out = Path(cmd[cmd.index("--write-media") + 1]) if "--write-media" in cmd else Path(cmd[-2])
            shutil.copyfile(self.wav, out)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.run(cmd, **kw)                       # ffmpeg: the real one, on files in tmp

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def make(self, voice, out, speed=1.0):
        old = (speak_any.NEWHOST, speak_any._edge_py, agents.KOKORO_COMMAND)
        newhost = self.tmp / "edge_tts_newhost.py"
        newhost.write_text("# fake\n")
        speak_any.NEWHOST, speak_any._edge_py = newhost, sys.executable
        kpy, ksay = self.tmp / "kpy", self.tmp / "kokoro_say.py"
        kpy.write_text("")
        ksay.write_text("")
        agents.KOKORO_COMMAND = (str(kpy), str(ksay))
        try:
            return speak_any.make(self.txt, out, voice, speed, run=self.run_fake)
        finally:
            speak_any.NEWHOST, speak_any._edge_py, agents.KOKORO_COMMAND = old

    def test_a_kokoro_voice_goes_to_the_kokoro_script_with_exactly_that_name(self):
        out = self.tmp / "k.wav"
        self.assertEqual(self.make("bm_george", out), 0)
        self.assertEqual(out.read_bytes()[:4], b"RIFF")
        made = [c for c in self.calls() if c[0].endswith("kpy")]
        self.assertEqual([c[-1] for c in made], ["bm_george"])
        self.assertFalse([c for c in self.calls() if "--write-media" in c], "Microsoft is never asked")

    def test_a_microsoft_voice_goes_to_microsoft_twice_at_most_then_silence(self):
        out = self.tmp / "e.ogg"
        self.assertEqual(self.make("en-US-AvaNeural", out), 0)
        self.assertEqual(out.read_bytes()[:4], b"OggS", "the ending picks the format")
        self.mode = "fail"
        self.log.unlink()
        out2 = self.tmp / "e2.mp3"
        self.assertEqual(self.make("en-GB-RyanNeural", out2), 1)
        self.assertFalse(out2.exists())
        asks = [c for c in self.calls() if "--write-media" in c]
        self.assertEqual([c[c.index("--voice") + 1] for c in asks], ["en-GB-RyanNeural"] * 2, "same voice, never another")
        self.assertFalse([c for c in self.calls() if c[0].endswith("kpy")], "never handed to Kokoro")

    def test_speed_is_applied_after_the_voice(self):
        out = self.tmp / "s.wav"
        self.assertEqual(self.make("af_heart", out, 2.0), 0)
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True)
        self.assertAlmostEqual(float(r.stdout), 0.5 + 0.2, delta=0.05, msg="1 s at 2x, plus the 0.2 s lead")

    def test_anything_else_is_refused_without_trying(self):
        for voice in ("", "Samantha", "jf_alpha", "alloy", "af_nobody"):
            out = self.tmp / "x.mp3"
            self.assertEqual(self.make(voice, out), 2, voice)
            self.assertFalse(out.exists())
        self.assertEqual(self.make("af_heart", self.tmp / "y.mp3", 3.0), 2, "speed out of range")
        self.assertEqual(self.calls(), [])

    def test_it_never_imports_settings(self):
        r = subprocess.run([sys.executable, "-c", "import sys, speak_any; print('settings' in sys.modules, "
                            "'tts' in sys.modules)"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "False False", r.stderr)


if __name__ == "__main__":
    unittest.main()
