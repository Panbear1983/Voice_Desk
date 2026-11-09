"""Offline test of hermes_patches.py: a throw-away git checkout stands in for ~/.hermes/hermes-agent.
Nothing here touches Peter's real Hermes folder, gateway or Telegram.

    .venv/bin/python -m unittest tests.test_hermes_patches -v
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401

import hermes_patches as HP  # noqa: E402

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null"}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=GIT_ENV, check=True).stdout


BASE_ONE = "def speak():\n    play()\n\n\ndef other():\n    pass\n"
BASE_TWO = "def load():\n    return {}\n\n\ndef tail():\n    pass\n"


class World:
    """root = fake hermes-agent checkout with two patches; patches = the manifest + diffs; home = gateway.pid."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root, self.patches, self.home = t / "agent", t / "patches", t / "home"
        for d in (self.root, self.patches, self.home):
            d.mkdir()
        git(self.root, "init", "-q")
        (self.root / "one.py").write_text(BASE_ONE)
        (self.root / "two.py").write_text(BASE_TWO)
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "base")
        # patch 1 (cli) edits one.py; patch 2 (gateway) edits two.py and adds a test file
        (self.root / "one.py").write_text(BASE_ONE.replace("    play()\n", "    play()  # LOCAL-ONE\n"))
        (self.patches / "one.diff").write_text(git(self.root, "diff", "one.py"))
        git(self.root, "checkout", "one.py")
        (self.root / "two.py").write_text(BASE_TWO.replace("return {}", "return {}  # LOCAL-TWO"))
        (self.root / "test_two.py").write_text("def test_two():\n    assert True  # LOCAL-TEST\n")
        git(self.root, "add", "-N", "test_two.py")
        (self.patches / "two.diff").write_text(git(self.root, "diff", "two.py", "test_two.py"))
        git(self.root, "rm", "-q", "--cached", "test_two.py")
        git(self.root, "checkout", "two.py")
        (self.root / "test_two.py").unlink()
        (self.patches / "manifest.json").write_text(json.dumps({"version": 1, "patches": [
            {"id": "one", "diff": "one.diff", "runs_in": "cli", "summary": "first fix",
             "markers": [["one.py", "LOCAL-ONE"]]},
            {"id": "gateway-live-voice-mode", "diff": "two.diff", "runs_in": "gateway", "summary": "second fix",
             "markers": [["two.py", "LOCAL-TWO"], ["test_two.py", "LOCAL-TEST"]]}]}))
        self.loc = HP.Locations(self.root, self.patches, self.home)

    def apply_all_by_hand(self):
        for d in ("one.diff", "two.diff"):
            git(self.root, "apply", str(self.patches / d))

    def gateway_running_since(self, epoch):
        (self.home / "gateway.pid").write_text(json.dumps({"pid": os.getpid(), "start_time": int(epoch * 100)}))

    def close(self):
        self.tmp.cleanup()


class Status(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)

    def states(self):
        return {s.patch.id: s for s in HP.status(self.w.loc)}

    def test_missing_patches_that_fit_are_reported_as_such(self):
        st = self.states()
        self.assertEqual(st["one"].state, HP.MISSING)
        self.assertEqual(st["gateway-live-voice-mode"].state, HP.MISSING)
        light, label, _ = HP.summary(list(st.values()))
        self.assertIs(light, False)
        self.assertIn("0/2", label)

    def test_present_patches_are_recognised_by_their_markers(self):
        self.w.apply_all_by_hand()
        st = self.states()
        self.assertTrue(all(s.state == HP.PRESENT for s in st.values()))
        light, label, _ = HP.summary(list(st.values()))
        self.assertIs(light, True)
        self.assertIn("2/2", label)

    def test_a_harmless_edit_around_a_patch_does_not_make_it_look_lost(self):
        self.w.apply_all_by_hand()
        one = self.w.root / "one.py"
        one.write_text("# new header from an update\n" + one.read_text())
        self.assertEqual(self.states()["one"].state, HP.PRESENT)

    def test_update_that_rewrote_the_code_is_a_conflict_not_a_silent_loss(self):
        (self.w.root / "one.py").write_text("def speak():\n    play_v2(volume=3)\n")   # upstream changed the very lines
        s = self.states()["one"]
        self.assertEqual(s.state, HP.CONFLICT)
        light, label, detail = HP.summary(list(self.states().values()))
        self.assertIs(light, False)
        self.assertIn("conflicts", detail)

    def test_half_there_is_partial(self):
        self.w.apply_all_by_hand()
        (self.w.root / "test_two.py").unlink()
        self.assertEqual(self.states()["gateway-live-voice-mode"].state, HP.PARTIAL)

    def test_missing_saved_diff_is_named(self):
        (self.w.patches / "one.diff").unlink()
        self.assertEqual(self.states()["one"].state, HP.NODIFF)

    def test_unreadable_manifest_is_an_error_not_a_green_light(self):
        (self.w.patches / "manifest.json").write_text("{nope")
        with self.assertRaises(HP.ManifestError):
            HP.status(self.w.loc)


class Loaded(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)
        self.w.apply_all_by_hand()

    def test_gateway_started_before_the_file_changed_means_not_live_yet(self):
        self.w.gateway_running_since(time.time() - 3600)
        st = {s.patch.id: s for s in HP.status(self.w.loc)}
        self.assertIs(st["gateway-live-voice-mode"].loaded, False)
        self.assertIsNone(st["one"].loaded)                       # a window patch: not about the gateway
        light, label, detail = HP.summary(list(st.values()))
        self.assertIsNone(light)
        self.assertIn("gateway not restarted", label)
        self.assertEqual(HP.live_voice_mode(self.w.loc), HP.PENDING)

    def test_gateway_started_after_the_file_changed_is_live(self):
        self.w.gateway_running_since(time.time() + 60)
        self.assertIs({s.patch.id: s for s in HP.status(self.w.loc)}["gateway-live-voice-mode"].loaded, True)
        self.assertEqual(HP.live_voice_mode(self.w.loc), HP.LIVE)
        self.assertIs(HP.summary(HP.status(self.w.loc))[0], True)

    def test_no_gateway_pid_means_unknown_and_the_next_start_will_have_it(self):
        self.assertIsNone(HP.gateway_started(self.w.loc))
        self.assertEqual(HP.live_voice_mode(self.w.loc), HP.LIVE)

    def test_missing_patch_means_no_live_switching(self):
        (self.w.root / "two.py").write_text(BASE_TWO)
        self.assertEqual(HP.live_voice_mode(self.w.loc), HP.MISSING_STATE)


class Apply(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)

    def test_apply_puts_back_what_fits_and_backs_the_files_up_first(self):
        res = HP.apply_missing(self.w.loc)
        self.assertTrue(all(r.ok for r in res), res)
        self.assertIn("LOCAL-ONE", (self.w.root / "one.py").read_text())
        self.assertTrue((self.w.root / "test_two.py").exists())
        backups = {p.name: p.read_text() for p in self.w.patches.glob("pre-apply-*/*.py")}
        self.assertEqual(backups, {"one.py": BASE_ONE, "two.py": BASE_TWO})
        self.assertTrue(all(s.state == HP.PRESENT for s in HP.status(self.w.loc)))

    def test_apply_twice_changes_nothing_the_second_time(self):
        HP.apply_missing(self.w.loc)
        before = (self.w.root / "one.py").read_text()
        res = HP.apply_missing(self.w.loc)
        self.assertEqual([r.message for r in res], ["already in", "already in"])
        self.assertEqual((self.w.root / "one.py").read_text(), before)

    def test_a_conflicting_patch_is_never_forced_and_the_others_still_go_in(self):
        rewritten = "def speak():\n    play_v2(volume=3)\n"
        (self.w.root / "one.py").write_text(rewritten)
        res = {r.id: r for r in HP.apply_missing(self.w.loc)}
        self.assertFalse(res["one"].ok)
        self.assertIn("nothing changed", res["one"].message)
        self.assertEqual((self.w.root / "one.py").read_text(), rewritten)
        self.assertTrue(res["gateway-live-voice-mode"].ok)

    def test_apply_that_does_not_end_with_the_markers_is_undone(self):
        manifest = json.loads((self.w.patches / "manifest.json").read_text())
        manifest["patches"][0]["markers"] = [["one.py", "THIS-TEXT-NEVER-APPEARS"]]
        (self.w.patches / "manifest.json").write_text(json.dumps(manifest))
        res = {r.id: r for r in HP.apply_missing(self.w.loc)}
        self.assertFalse(res["one"].ok)
        self.assertEqual((self.w.root / "one.py").read_text(), BASE_ONE)


class CommandLine(unittest.TestCase):
    def test_check_exit_code_is_nonzero_when_a_fix_is_gone(self):
        w = World()
        self.addCleanup(w.close)
        env = {**os.environ, "VOICE_DESK_HERMES_HOME": str(w.home), "VOICE_DESK_HERMES_AGENT": str(w.root),
               "VOICE_DESK_HERMES_PATCHES": str(w.patches)}
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run([sys.executable, str(root / "hermes_patches.py"), "check"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("missing", r.stdout)
        r = subprocess.run([sys.executable, str(root / "hermes_patches.py"), "apply"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, str(root / "hermes_patches.py"), "check"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
