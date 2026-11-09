"""Offline tests for the watcher (watcher.py): drift is put back through the panel's own code, the pause key
stops everything, nothing is fought, and Telegram is told only about what cannot be repaired. Everything runs in
temp folders against fakes: no real settings, gateway, windows, speakers or Telegram (the "send" is a list).

    .venv/bin/python -m unittest tests.test_watcher -v
"""
import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402
from tests.test_hermes_patches import World as PatchWorld, git  # noqa: E402

import agents  # noqa: E402
import dashboard as D  # noqa: E402
import gridevidence as E  # noqa: E402
import gridstate as G  # noqa: E402
import hermes_patches as HP  # noqa: E402
import onoff  # noqa: E402
import syshealth  # noqa: E402
import voicecfg  # noqa: E402
import voiceedit as VE  # noqa: E402
import watcher as W  # noqa: E402
from textual.widgets import Static  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
T0 = 1_800_000_000.0                      # a fixed "now"; every run is told its own time


class Sender:
    """The fake Telegram: records what would have been sent."""

    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def __call__(self, text):
        self.sent.append(text)
        return self.ok


class Base(unittest.TestCase):
    def setUp(self):
        self.w = F.make_world()
        self.p, self.e = self.w.paths, self.w.edit
        # the fake world starts with Kylie's threshold off from agents.toml (a drift of its own): settle it first
        done, errors = voicecfg.apply(voicecfg.collect(agents.load(self.e.toml), self.e.profiles, self.e.claude_conf),
                                      self.w.root / "settle-backups")
        self.assertEqual(errors, [])
        self.assertEqual(voicecfg.drift(voicecfg.collect(agents.load(self.e.toml), self.e.profiles, self.e.claude_conf)), [])
        self.pw = PatchWorld()                                    # Hermes' program folder: all fixes in
        self.addCleanup(self.pw.close)
        self.pw.apply_all_by_hand()
        self.send = Sender()

    def run_w(self, at=T0, **kw):
        kw.setdefault("confirm_sec", 0)
        kw.setdefault("send", self.send)
        kw.setdefault("loc", self.pw.loc)
        return W.run_once(self.p, self.e, now=at, **kw)

    def switch(self, key, place, token):
        res = onoff.set_cell(key, place, token, self.p, self.e)
        self.assertTrue(res.ok, res.message)

    def group_agents(self):
        return json.loads(self.p.mirror_config.read_text())["voice_notes"]["agents"]

    def put_kylie_back_in_group(self):
        data = json.loads(self.p.mirror_config.read_text())
        data["voice_notes"]["agents"].append("assistant")
        self.p.mirror_config.write_text(json.dumps(data))


class NothingDrifted(Base):
    def test_a_fresh_world_is_quiet_and_writes_its_status(self):
        rep = self.run_w()
        self.assertTrue(rep.quiet(), (rep.repaired, rep.problems))
        self.assertGreater(rep.checked, 30)               # voice settings + patches were looked at
        ok, label, _ = W.light(self.p, now=T0 + 120)
        self.assertIs(ok, True)
        self.assertIn("checked 2 min ago", label)
        self.assertEqual(self.send.sent, [])

    def test_the_status_light_is_grey_when_the_job_is_not_running_and_when_stale(self):
        ok, label, detail = W.light(self.p, now=T0)
        self.assertIs(ok, None)
        self.assertIn("not running", label)
        self.assertIn("install_watcher.sh", detail)
        self.run_w()
        self.assertIs(W.light(self.p, now=T0 + 60)[0], True)
        self.assertIs(W.light(self.p, now=T0 + W.STALE_SEC + 5)[0], None)


class RepairsSwitches(Base):
    def test_a_switch_the_panel_set_is_put_back_through_the_panels_own_code(self):
        self.switch("kylie", "tg_group", G.OFF)
        self.assertNotIn("assistant", self.group_agents())
        self.put_kylie_back_in_group()                    # something else turned her back on
        before = len(VE.read_history(self.e))
        rep = self.run_w()
        self.assertEqual(len(rep.repaired), 1, rep.problems)
        self.assertNotIn("assistant", self.group_agents())
        hist = VE.read_history(self.e)
        self.assertEqual(len(hist), before + 1)           # the repair is a normal, backed-up change in the history
        self.assertTrue(hist[-1]["ok"] and hist[-1]["new"] == G.OFF)
        self.assertTrue(any(f["backup"] and not f["backup"].startswith("(") for f in hist[-1]["files"]))
        self.assertIn("put back", W.history_lines(self.p)[0])
        self.assertEqual(self.send.sent, [])               # repaired = nothing to tell Peter
        self.assertTrue(self.run_w(at=T0 + 300).quiet())    # and it stays quiet afterwards

    def test_an_undo_that_removed_the_mode_file_is_not_written_back_by_the_watcher(self):
        self.switch("kylie", "tg_dm", G.OFF)
        self.assertTrue(VE.undo(self.e, self.p).ok)
        self.assertFalse(self.p.voice_mode_file.exists())
        self.assertTrue(self.run_w().quiet())
        self.assertFalse(self.p.voice_mode_file.exists())

    def test_an_undo_is_the_panels_newest_word_so_the_watcher_leaves_it_alone(self):
        self.switch("kylie", "tg_group", G.OFF)
        res = VE.undo(self.e, self.p)
        self.assertTrue(res.ok, res.message)
        self.assertIn("assistant", self.group_agents())
        rep = self.run_w()
        self.assertTrue(rep.quiet(), rep.repaired)
        self.assertIn("assistant", self.group_agents())

    def test_claude_gemini_telegram_direct_and_the_voice_desk_switches_are_all_put_back(self):
        self.switch("claude", "terminal", G.OFF)
        self.switch("gemini", "terminal", G.OFF)
        self.switch("kylie", "tg_dm", G.OFF)
        self.switch("kylie", "mic", "queue:off")
        self.p.gemini_hook.write_text("# hook\nif exists('gemini_voice_off'): quit\n")      # a hook that listens to the switch
        settings = Path(self.p.claude_dir) / "settings.json"
        data = json.loads(settings.read_text())
        data["voice"]["enabled"] = True
        settings.write_text(json.dumps(data))
        self.p.gemini_switch.unlink()
        self.p.voice_mode_file.write_text(json.dumps({"assistant:telegram:7512954760": "all"}))
        (self.p.speech_dir / "off").unlink()
        rep = self.run_w()
        self.assertEqual(len(rep.repaired), 4, (rep.repaired, [x.text for x in rep.problems]))
        self.assertFalse(json.loads(settings.read_text())["voice"]["enabled"])
        self.assertTrue(self.p.gemini_switch.exists())
        self.assertEqual(json.loads(self.p.voice_mode_file.read_text())["assistant:telegram:7512954760"], "off")
        self.assertTrue((self.p.speech_dir / "off").exists())
        self.assertTrue(self.run_w(at=T0 + 300).quiet())

    def test_a_drift_must_be_seen_twice_before_it_is_repaired(self):
        self.switch("kylie", "tg_group", G.OFF)
        self.put_kylie_back_in_group()
        first = self.run_w(confirm_sec=90)
        self.assertEqual((first.repaired, len(first.pending)), ([], 1))
        self.assertIn("assistant", self.group_agents())               # not touched yet: it may be a write in progress
        early = self.run_w(at=T0 + 30, confirm_sec=90)
        self.assertEqual(early.repaired, [])
        late = self.run_w(at=T0 + 300, confirm_sec=90)
        self.assertEqual(len(late.repaired), 1)
        self.assertNotIn("assistant", self.group_agents())

    def test_a_drift_that_fixes_itself_between_runs_is_forgotten(self):
        self.switch("kylie", "tg_group", G.OFF)
        self.put_kylie_back_in_group()
        self.run_w(confirm_sec=90)
        data = json.loads(self.p.mirror_config.read_text())
        data["voice_notes"]["agents"].remove("assistant")
        self.p.mirror_config.write_text(json.dumps(data))
        self.run_w(at=T0 + 300, confirm_sec=90)
        self.assertEqual(W.load_state(self.p)["suspect"], {})

    def test_it_stops_when_something_keeps_changing_a_setting_back(self):
        self.switch("kylie", "tg_group", G.OFF)
        for i in range(W.FLAP_MAX):
            self.put_kylie_back_in_group()
            rep = self.run_w(at=T0 + i * 400)
            self.assertEqual(len(rep.repaired), 1)
        self.put_kylie_back_in_group()
        rep = self.run_w(at=T0 + W.FLAP_MAX * 400)
        self.assertEqual(rep.repaired, [])
        self.assertIn("keeps changing back", rep.problems[0].text)
        self.assertIn("assistant", self.group_agents())                # left alone: no fight
        self.assertEqual(len(self.send.sent), 1)                        # and Peter is told, once
        self.run_w(at=T0 + W.FLAP_MAX * 400 + 600)
        self.assertEqual(len(self.send.sent), 1)

    def test_an_unreadable_file_is_a_problem_not_a_crash(self):
        self.switch("kylie", "tg_group", G.OFF)
        self.p.mirror_config.write_text("{ not json")
        rep = self.run_w()
        self.assertEqual(rep.repaired, [])
        self.assertIn("cannot be read", rep.problems[0].text)
        self.assertEqual(self.p.mirror_config.read_text(), "{ not json")  # never overwrites what it cannot parse
        self.assertEqual(len(self.send.sent), 1)

    def test_an_agent_removed_from_agents_toml_is_skipped(self):
        self.switch("kylie", "tg_group", G.OFF)
        toml = Path(self.e.toml)
        toml.write_text(toml.read_text().replace("[agents.kylie]", "[agents.kylie_gone]"))
        rep = self.run_w()
        self.assertEqual(rep.problems, [])

    def test_gemini_off_with_a_hook_that_does_not_read_the_switch_is_reported(self):
        self.switch("gemini", "terminal", G.OFF)
        rep = self.run_w()
        self.assertIn("gemini-hook", [x.key for x in rep.problems])
        self.p.gemini_hook.write_text("# hook\nif exists('gemini_voice_off'): quit\n")
        self.assertEqual([x for x in self.run_w(at=T0 + 4000).problems], [])


class RepairsVoicesAndFixes(Base):
    def test_agents_toml_is_put_back_into_a_hermes_config_with_backup_and_read_back(self):
        cfg = Path(self.e.profiles) / "assistant/config.yaml"
        cfg.write_text(cfg.read_text().replace("silence_threshold: 200", "silence_threshold: 999"))
        rep = self.run_w()
        self.assertEqual(len(rep.repaired), 1, [x.text for x in rep.problems])
        self.assertIn("silence_threshold: 200", cfg.read_text())
        self.assertTrue(list((Path(self.e.hermes_backups)).glob("*-watcher/*")))      # a backup was taken first
        self.assertEqual(voicecfg.drift(voicecfg.collect(agents.load(self.e.toml), self.e.profiles, self.e.claude_conf)), [])

    def test_a_hand_edit_of_gemini_voice_file_is_put_back_as_the_panel_set_it(self):
        res = VE.change("gemini", "voice", "en-US-AriaNeural", self.e)                    # the panel sets Aria
        self.assertTrue(res.ok, res.message)
        conf = Path(self.e.gemini_conf)
        conf.write_text(conf.read_text().replace("en-US-AriaNeural", "en-US-JennyNeural"))   # someone puts Jenny back
        rep = self.run_w()
        self.assertEqual(len(rep.repaired), 1, [x.text for x in rep.problems])
        self.assertIn("Gemini", rep.repaired[0])
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', conf.read_text())
        self.assertEqual(voicecfg.drift(voicecfg.collect(agents.load(self.e.toml), self.e.profiles, self.e.claude_conf,
                                                         self.e.gemini_conf)), [])

    def test_a_missing_hermes_fix_that_fits_is_put_back_and_a_gateway_one_asks_for_a_restart(self):
        self.pw.gateway_running_since(time.time() - 7200)
        git(self.pw.root, "apply", "-R", str(self.pw.patches / "two.diff"))          # an update removed the gateway fix
        rep = self.run_w(at=time.time())
        self.assertEqual(len(rep.repaired), 1, [x.text for x in rep.problems])
        self.assertTrue(all(s.state == HP.PRESENT for s in HP.status(self.pw.loc)))
        self.assertEqual([x.key for x in rep.problems], ["restart:gateway-live-voice-mode"])
        self.assertEqual(len(self.send.sent), 1)                                      # only a restart can finish this
        self.assertIn("restart", self.send.sent[0])

    def test_a_fix_that_no_longer_fits_is_reported_and_never_forced(self):
        (self.pw.root / "one.py").write_text("def speak():\n    play_v2(volume=3)\n")
        rep = self.run_w()
        self.assertEqual(rep.repaired, [])
        self.assertIn("needs a person", rep.problems[0].text)
        self.assertEqual((self.pw.root / "one.py").read_text(), "def speak():\n    play_v2(volume=3)\n")
        self.assertEqual(len(self.send.sent), 1)

    def test_a_broken_patch_list_is_a_problem_and_the_other_checks_still_run(self):
        (self.pw.patches / "manifest.json").write_text("nope")
        self.switch("kylie", "tg_group", G.OFF)
        self.put_kylie_back_in_group()
        rep = self.run_w()
        self.assertEqual(len(rep.repaired), 1)
        self.assertEqual(rep.problems[0].key, "patches:list")


class Alerts(Base):
    def break_a_fix(self):
        (self.pw.root / "one.py").write_text("def speak():\n    play_v2(volume=3)\n")

    def test_one_plain_sentence_to_peter_and_no_repeats_while_the_problem_stands(self):
        self.break_a_fix()
        r1 = self.run_w(at=T0)
        self.assertTrue(r1.alert_sent)
        self.assertEqual(len(self.send.sent), 1)
        text = self.send.sent[0]
        self.assertTrue(text.startswith("Voice Desk could not put this right by itself:"))
        self.assertEqual(text.count("\n"), 0)
        for secret_like in ("/Users", "token", "Bearer"):
            self.assertNotIn(secret_like, text)
        for i in range(1, 6):
            self.run_w(at=T0 + i * 3600)
        self.assertEqual(len(self.send.sent), 1)

    def test_a_second_new_problem_waits_for_the_hourly_limit_then_goes(self):
        self.break_a_fix()
        self.run_w(at=T0)
        (self.pw.root / "two.py").write_text("def load():\n    return None\n")
        early = self.run_w(at=T0 + 600)
        self.assertEqual(len(early.problems), 2)
        self.assertEqual(len(self.send.sent), 1)                           # too soon
        late = self.run_w(at=T0 + W.ALERT_GAP_SEC + 10)
        self.assertEqual(len(self.send.sent), 2)
        self.assertIn("second fix", self.send.sent[1])
        self.assertNotIn("first fix", self.send.sent[1])

    def test_a_problem_that_clears_and_comes_back_is_told_again(self):
        self.break_a_fix()
        self.run_w(at=T0)
        git(self.pw.root, "checkout", "one.py")
        git(self.pw.root, "apply", str(self.pw.patches / "one.diff"))
        self.assertTrue(self.run_w(at=T0 + 4000).quiet())
        self.break_a_fix()
        self.run_w(at=T0 + 8000)
        self.assertEqual(len(self.send.sent), 2)

    def test_a_failed_send_is_retried_later_not_every_run(self):
        self.break_a_fix()
        self.send.ok = False
        r1 = self.run_w(at=T0)
        self.assertFalse(r1.alert_sent)
        self.run_w(at=T0 + 300)
        self.assertEqual(len(self.send.sent), 1)
        self.send.ok = True
        r3 = self.run_w(at=T0 + W.ALERT_RETRY_SEC + 5)
        self.assertTrue(r3.alert_sent)
        self.assertEqual(len(self.send.sent), 2)

    def test_several_problems_still_make_one_message(self):
        self.break_a_fix()
        (self.pw.root / "two.py").write_text("def load():\n    return None\n")
        self.run_w()
        self.assertEqual(len(self.send.sent), 1)
        self.assertIn("(and 1 more)", self.send.sent[0])

    def test_send_dm_uses_the_gateway_guards_command_and_reports_the_exit_code(self):
        tmp = Path(tempfile.mkdtemp(prefix="vd-fakehermes-"))
        log = tmp / "argv.txt"
        fake = tmp / "hermes"
        fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{log}"\nexit ${{FAKE_EXIT:-0}}\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        with mock.patch.dict(os.environ, {"VOICE_DESK_HERMES_BIN": str(fake), "FAKE_EXIT": "0"}):
            self.assertTrue(W.send_dm("hello there", "7512954760"))
            self.assertEqual(log.read_text().split("\n")[:-1],
                             ["-p", "orchestrator", "send", "--to", "telegram:7512954760", "hello there", "--json"])
        with mock.patch.dict(os.environ, {"VOICE_DESK_HERMES_BIN": str(fake), "FAKE_EXIT": "1"}):
            self.assertFalse(W.send_dm("hello there", "7512954760"))
        with mock.patch.dict(os.environ, {"VOICE_DESK_HERMES_BIN": str(tmp / "missing")}):
            self.assertFalse(W.send_dm("hello there", "7512954760"))


class PauseAndDryRun(Base):
    def test_paused_means_nothing_is_checked_nothing_changed_nothing_sent(self):
        self.switch("kylie", "tg_group", G.OFF)
        self.put_kylie_back_in_group()
        (self.pw.root / "one.py").write_text("def speak():\n    play_v2(volume=3)\n")
        self.assertTrue(W.set_paused(self.p, True))
        rep = self.run_w()
        self.assertTrue(rep.paused)
        self.assertEqual((rep.repaired, rep.problems, self.send.sent), ([], [], []))
        self.assertIn("assistant", self.group_agents())
        ok, label, _ = W.light(self.p, now=T0)
        self.assertIs(ok, None)
        self.assertIn("PAUSED", label)
        self.assertFalse(W.set_paused(self.p, False))
        self.assertEqual(len(self.run_w(at=T0 + 10).repaired), 1)

    def test_toggle_flips_the_pause_file(self):
        self.assertTrue(W.toggle_pause(self.p))
        self.assertTrue(W.pause_file(self.p).exists())
        self.assertFalse(W.toggle_pause(self.p))

    def test_dry_run_changes_nothing_writes_no_status_and_sends_nothing(self):
        self.switch("kylie", "tg_group", G.OFF)
        self.put_kylie_back_in_group()
        (self.pw.root / "one.py").write_text("def speak():\n    play_v2(volume=3)\n")
        before = self.p.mirror_config.read_text()
        rep = self.run_w(dry_run=True)
        self.assertEqual(len(rep.would), 1)
        self.assertEqual(rep.repaired, [])
        self.assertTrue(rep.alert)
        self.assertEqual(self.send.sent, [])
        self.assertEqual(self.p.mirror_config.read_text(), before)
        self.assertFalse(W.state_file(self.p).exists())
        self.assertFalse(W.log_file(self.p).exists())


class CommandLine(Base):
    def test_pause_resume_status_and_a_dry_run_through_main(self):
        real = G.Paths.default()                          # main() uses the default paths: the tests' own temp state folder
        self.assertNotEqual(real.state, Path.home() / ".voice_desk")          # never Peter's real state folder
        self.addCleanup(W.set_paused, real, False)
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(W.main(["pause"]), 0)
            self.assertTrue(W.pause_file(real).exists())
            W.main(["status"])
            self.assertEqual(W.main(["resume"]), 0)
        self.assertFalse(W.pause_file(real).exists())
        self.assertIn("PAUSED", out.getvalue())

    def test_the_launchd_job_file_is_valid_and_runs_a_single_check_every_five_minutes(self):
        r = subprocess.run(["/bin/bash", str(ROOT / "install_watcher.sh"), "--print"], capture_output=True, text=True,
                           env={**os.environ, "HOME": str(self.w.root)})
        self.assertEqual(r.returncode, 0, r.stderr)
        plist = Path(self.w.root) / "w.plist"
        plist.write_text(r.stdout)
        self.assertEqual(subprocess.run(["plutil", "-lint", str(plist)], capture_output=True).returncode, 0)
        self.assertIn("<integer>300</integer>", r.stdout)
        self.assertIn("<string>once</string>", r.stdout)
        self.assertNotIn("KeepAlive", r.stdout)                                      # no process stays alive between checks
        self.assertFalse((Path(self.w.root) / "Library/LaunchAgents").exists())      # --print writes and loads nothing


class OnThePanel(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = F.make_world()

    def app(self):
        return D.Dashboard(paths=self.w.paths, edit_paths=self.w.edit, reader=E.LogReader(self.w.paths.profiles),
                           autostart=False, cfg_loader=lambda: agents.load(self.w.toml))

    async def test_w_pauses_and_resumes_and_the_strip_says_so(self):
        app = self.app()
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            with mock.patch.object(syshealth, "mac_output", return_value=syshealth.Light(True, "Mac output: test")):
                rows, lights = await asyncio.to_thread(app.collect)
            app.apply_snapshot(rows, lights)
            await pilot.pause()
            strip = str(app.screen.query_one("#health", Static).render())
            self.assertIn("watcher not running", strip)
            await pilot.press("w")
            await pilot.pause()
            self.assertTrue(W.is_paused(self.w.paths))
            strip = str(app.screen.query_one("#health", Static).render())
            self.assertIn("WATCHER PAUSED", strip)
            self.assertIn("PAUSED", str(app.screen.query_one("#msg", Static).render()))
            await pilot.press("w")
            await pilot.pause()
            self.assertFalse(W.is_paused(self.w.paths))
            self.assertNotIn("PAUSED", str(app.screen.query_one("#health", Static).render()))


if __name__ == "__main__":
    unittest.main()
