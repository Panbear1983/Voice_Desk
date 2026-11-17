"""Offline tests for the parity build of the Agent Control Desk (2026-11-17).

Job A, what was left after Kylie's second look: the tree's cursor no longer jumps from the last setting to the
first, the plain lines between settings are scrolled into view, the end of the tree is marked, the muted
output is said once, the Team Room note is labelled as the room's, the mouse pointer no longer looks like a
second chosen row, and an allowance that could not be read again keeps its last known numbers with their age.

Job B, what only the old dashboard could do: the silent voice test, adding and removing an agent in the Voice
Desk list, and the listener's status and controls.

Fakes and throw-away folders only: no live settings file, no real listener, no window, and no sound (the voice
"maker" here writes a few bytes to a throw-away file).

    .venv/bin/python -m unittest tests.test_acd_parity -v
"""
import dataclasses
import datetime as dt
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests.test_acd_v4 import make_app, show  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_health as H  # noqa: E402
import acd_parity as P  # noqa: E402
import agents as A  # noqa: E402
from textual.widgets import DataTable, Input, OptionList, Static  # noqa: E402

AGY = F.AGY
NOW = F.NOW
WHEN = dt.datetime(2026, 11, 17, 10, 0, 0)
GENERIC = ("No account usage available for provider 'antigravity-subscription-directsdk': no credential is "
           "configured for it, the provider has no usage endpoint, or the fetch failed.")


class FakeListener:
    """A stand-in for the listener's plumbing. It records every order; nothing real is touched."""

    def __init__(self, state: str = "asleep", fresh: bool = True, live: bool = False, **data) -> None:
        self.data = {"state": state, "name": "Jarvis", "heard": "hello Jarvis", "reply": "Good morning.",
                     "since": time.time() - 30, "turns": 3, **data}
        self.fresh, self.live, self.muted = fresh, live, False
        self.orders: list = []
        self.is_loaded = True

    def read_status(self) -> dict:
        return dict(self.data)

    def is_fresh(self, data: dict) -> bool:
        return self.fresh

    def is_muted(self) -> bool:
        return self.muted

    def set_muted(self, on: bool) -> None:
        self.muted = on
        self.orders.append("mute" if on else "unmute")

    def send(self, order: str) -> None:
        self.orders.append(order)

    def loaded(self):
        return self.is_loaded

    def restart(self) -> str:
        self.orders.append("RESTART")
        return "restarted"

    def stop(self) -> str:
        self.orders.append("STOP")
        return "stopped"

    def live_turn(self) -> bool:
        return self.live


class FakeVoice:
    """The voice maker and the sound check, faked: it remembers what it was asked to say and plays nothing."""

    def __init__(self, fail: tuple = ()) -> None:
        self.said: list = []
        self.fail = fail

    def synth(self, txt: Path, out: Path, voice: str, agent, lang: str):
        self.said.append((agent.key, lang, voice, Path(txt).read_text(encoding="utf-8")))
        if agent.key in self.fail:
            return None
        Path(out).write_bytes(b"not really sound")
        return "fake"

    def probe(self, out: Path):
        return True, 1.5, "real sound"


def body(app) -> str:
    """The words of the pop-up on top."""
    out = []
    for name in ("box-title", "box-note", "box-text", "box-body", "box-confirm", "box-keys"):
        for w in app.screen.query(f"#{name}"):
            out.append(str(w.render()))
    return "\n".join(out)


async def settle(app, pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


class SaidOnce(unittest.TestCase):
    def snap(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        w.paths = dataclasses.replace(w.paths, grid=object(), lights=lambda grid: [
            type("L", (), {"ok": True, "label": "voice engine OK", "detail": ""})(),
            type("L", (), {"ok": False, "label": "Mac output: volume 94%, MUTED, MacBook Pro Speakers", "detail": ""})()])
        return F.collect(w)

    def test_a_failing_light_is_a_red_warning_and_is_not_repeated_in_the_status_line(self):
        snap = self.snap()
        warnings = acd.warnings_text(snap).plain
        status = acd.systems_text(snap, 200).plain
        self.assertEqual(warnings.count("MUTED"), 1)
        self.assertNotIn("MUTED", status)
        self.assertIn("voice engine OK", status)                      # a light that is fine stays in the status line
        self.assertEqual((warnings + status).count("Mac output"), 1)

    def test_the_room_note_is_labelled_as_the_rooms_whichever_agent_is_chosen(self):
        w = F.make_world()
        F.add_agent(w, "orchestrator")
        F.add_agent(w, "assistant")
        snap = F.collect(w)
        for v in snap.agents:                                         # the same words under every agent's column
            cell = acd.cell_of(acd.Row("presence:team_room", "Team Room"), v, None, snap.room_note)
            self.assertIn(f"{acd.ROOM_HEADING}: Jarvis leads the Team Room.", cell.detail[-1])
        self.assertIn("Team Room", acd.ROOM_HEADING)
        self.assertIn("all agents", acd.ROOM_HEADING)


class TheAllowanceKeepsItsLastKnownNumbers(unittest.TestCase):
    def reader(self, clock, answers, keep=None):
        return H.UsageReader(runner=lambda cmd: answers.pop(0), clock=lambda: clock[0], hermes="hermes", keep=keep)

    def test_hermes_one_sentence_for_three_causes_is_never_read_as_no_sign_in(self):
        why = H.why_no_reading(GENERIC)
        self.assertNotIn("has no sign-in for it, so it cannot ask", why)
        self.assertIn("got no allowance reading", why)
        self.assertIn("does not say which", why)
        self.assertEqual(H.why_no_reading("boom"), "the usage check failed")

    def test_a_check_that_fails_after_a_good_one_keeps_the_numbers_and_says_how_old_they_are(self):
        clock = [NOW]
        good = (0, F.usage_json(NOW - 1, [("Gemini (Weekly)", 38.0, NOW + 4 * 86400), ("Claude/GPT (5h)", 0.0, None)]))
        r = self.reader(clock, [good, (0, GENERIC)])
        r.refresh({AGY: "orchestrator"})
        self.assertEqual(r.cached()[AGY].old_note, "")                # fresh: no "last known" label
        clock[0] += 20 * 60
        r.refresh({AGY: "orchestrator"})                              # this one fails
        got = r.cached()[AGY]
        self.assertEqual([w.left for w in got.windows], [62.0, 100.0])
        self.assertIn("last known, read 20 minutes ago", got.old_note)
        self.assertIn("no newer reading", got.old_note)
        self.assertNotIn("no sign-in for it, so it cannot ask", got.old_note)
        lines, why = D.allowance_lines(got)
        self.assertEqual(why, "")
        self.assertTrue(lines[0].startswith("last known, read 20 minutes ago"))
        self.assertTrue(lines[1].startswith("Gemini (Weekly): 62% left"))

    def test_the_tree_shows_the_old_numbers_with_their_age_not_a_wrong_reason(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("gemini-3.8-flash", AGY)])
        F.log(w, "assistant", F.turn(w.now - 30, "k1", "gemini-3.8-flash", AGY))
        clock = [w.now - 20 * 60]
        r = self.reader(clock, [(0, F.usage_json(clock[0] - 1, [("Gemini (Weekly)", 38.0, w.now + 86400)])), (0, GENERIC)])
        r.refresh({AGY: "assistant"})
        clock[0] = w.now
        r.refresh({AGY: "assistant"})
        snap = D.collect(w.paths, yaml_loader=F.loader, now=w.now, usage=r.cached())
        tree = acd.column_story(snap, F.agent(snap, "assistant"))
        self.assertIn("allowance: last known, read 20 minutes ago", tree)
        self.assertIn("allowance: Gemini (Weekly): 62% left", tree)
        self.assertNotIn("Hermes has no sign-in for it", tree)

    def test_a_window_that_has_reset_since_is_no_longer_claimed(self):
        good = H.parse_usage(AGY, json.loads(F.usage_json(NOW - 5, [("Gemini (5h)", 90.0, NOW + 600)])), NOW)
        later = H.last_known(good, NOW + 3600, "the usage check failed")
        self.assertIsNone(later.windows[0].left)
        self.assertIn("has reset since", later.windows[0].note)

    def test_a_new_desk_still_has_the_numbers_the_desk_before_it_read(self):
        keep = Path(tempfile.mkdtemp(prefix="acd-keep-")) / "sub" / "allowance_last_known.json"
        clock = [NOW]
        first = self.reader(clock, [(0, F.usage_json(NOW - 1, [("Weekly", 75.0, NOW + 86400)]))], keep=keep)
        first.refresh({AGY: "assistant"})
        self.assertTrue(keep.is_file())
        clock[0] += 22 * 60
        second = self.reader(clock, [(0, GENERIC)], keep=keep)        # a fresh desk, twenty minutes later
        before = second.cached()[AGY]                                 # even before it has asked anything itself
        self.assertEqual(before.windows[0].left, 25.0)
        self.assertIn("read 22 minutes ago", before.old_note)
        second.refresh({AGY: "assistant"})
        self.assertEqual(second.cached()[AGY].windows[0].left, 25.0)
        self.assertIn("got no allowance reading", second.cached()[AGY].old_note)

    def test_a_damaged_keep_file_is_simply_nothing_and_no_reading_at_all_still_says_why(self):
        keep = Path(tempfile.mkdtemp(prefix="acd-keep-")) / "k.json"
        keep.write_text("{ not json", encoding="utf-8")
        r = H.UsageReader(runner=lambda cmd: (0, GENERIC), clock=lambda: NOW, hermes="hermes", keep=keep)
        self.assertEqual(r.cached(), {})
        r.refresh({AGY: "assistant"})
        got = r.cached()[AGY]
        self.assertEqual(got.windows, ())
        self.assertIn("got no allowance reading", got.problem)


# ================================== Job B ======================================================================
class TheVoiceTest(unittest.TestCase):
    def test_a_dry_run_says_exactly_what_would_be_spoken_and_in_which_voice_and_makes_nothing(self):
        w = F.edit_world()
        fake = FakeVoice()
        before = F.tree_hashes(w.root)
        r = P.test_voices(w.edit, ["kylie"], synth=fake.synth, probe=fake.probe, now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertIn("DRY RUN, nothing written", r.message)
        self.assertIn("Nothing is played", r.message)
        self.assertEqual(fake.said, [])                               # nothing was made
        self.assertEqual(F.tree_hashes(w.root), before)
        text = "\n".join(r.lines)
        self.assertIn('Kylie, English, voice Ava: "This is a Voice Desk test for Kylie."', text)
        self.assertIn("Kylie, Chinese, voice", text)
        self.assertNotIn("Jarvis", text)

    def test_everyone_lists_every_agent_and_names_the_silent_ones(self):
        w = F.edit_world()
        plan, silent = P.test_plan(w.edit)
        listed = [a for a in A.agents(A.load(w.edit.voice.toml))]
        self.assertEqual({s.key for s in plan} | {a.key for a in listed if a.display in silent},
                         {a.key for a in listed})
        r = P.test_voices(w.edit, None, now=WHEN)
        self.assertTrue(r.dry)
        self.assertEqual(len([ln for ln in r.lines if '"' in ln]), len(plan))

    def test_armed_it_makes_each_sentence_to_a_file_checks_it_and_plays_nothing(self):
        w = F.edit_world(armed=True)
        fake = FakeVoice()
        r = P.test_voices(w.edit, ["kylie"], synth=fake.synth, probe=fake.probe, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIn("Nothing was played", r.message)
        self.assertEqual([(k, lang) for k, lang, _v, _t in fake.said], [("kylie", "en"), ("kylie", "zh")])
        self.assertEqual(fake.said[0][3], "This is a Voice Desk test for Kylie.")
        self.assertTrue(all(ln.startswith("+ passed") for ln in r.lines), r.lines)
        self.assertEqual(E.read_history(w.edit)[-1]["kind"], "voice_test")
        self.assertIsNone(E.last_undoable(w.edit))                    # a test is nothing to undo

    def test_a_voice_that_cannot_be_made_is_a_failed_test_and_says_the_line_would_be_silent(self):
        w = F.edit_world(armed=True)
        fake = FakeVoice(fail=("kylie",))
        r = P.test_voices(w.edit, ["kylie", "jarvis"], synth=fake.synth, probe=fake.probe, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("FAILED for Kylie", r.message)
        self.assertIn("would be silent", r.message)
        self.assertTrue(any(ln.startswith("+ passed: Jarvis") for ln in r.lines))

    def test_an_agent_that_is_not_in_the_list_is_refused(self):
        w = F.edit_world(armed=True)
        r = P.test_voices(w.edit, ["nobody"], now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("not in the Voice Desk list", r.message)


class AddAndRemove(unittest.TestCase):
    def test_what_can_be_added_is_a_profile_on_this_mac_that_is_not_listed_yet(self):
        w = F.edit_world()
        self.assertIn("simba", [p for p, _ in P.addable(w.edit)])
        self.assertNotIn("assistant", [p for p, _ in P.addable(w.edit)])

    def test_a_dry_run_add_shows_the_exact_new_lines_and_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        r = P.add_agent(w.edit, "Simba", "simba, sim bar", "am_fenrir", profile="simba", now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertEqual(F.tree_hashes(w.root), before)
        text = "\n".join(r.lines)
        self.assertIn("in the Voice Desk list:", text)
        self.assertIn('+ [agents.simba]', text)
        self.assertIn('+ voice = "am_fenrir"', text)
        self.assertIn('"sim bar"', text)

    def test_armed_add_then_undo_puts_the_list_back_exactly(self):
        w = F.edit_world(armed=True)
        toml = Path(w.edit.voice.toml)
        original = toml.read_text(encoding="utf-8")
        r = P.add_agent(w.edit, "Simba", "simba", "am_fenrir", profile="simba", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        added = next(a for a in A.agents(A.load(toml)) if a.key == "simba")
        self.assertEqual((added.display, added.profile, added.voice), ("Simba", "simba", "am_fenrir"))
        self.assertTrue(list(Path(w.edit.voice.toml_backups).glob("*-agents.toml")), "a copy was kept first")
        last = E.last_undoable(w.edit)
        self.assertEqual((last["kind"], last["did"]), ("agent", "added"))
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=5), loader=F.lines_loader)
        self.assertTrue(u.ok and not u.dry, u.message)
        self.assertEqual(toml.read_text(encoding="utf-8"), original)
        self.assertIsNone(E.last_undoable(w.edit))

    def test_a_bad_add_is_refused_in_plain_words_and_nothing_is_written(self):
        w = F.edit_world(armed=True)
        before = Path(w.edit.voice.toml).read_text(encoding="utf-8")
        for args, kw in ((("", "x", None), {}), (("Kylie", "kylie", None), {"profile": "assistant"}),
                         (("Claude", "claude", None), {})):
            r = P.add_agent(w.edit, *args, now=WHEN, **kw)
            self.assertFalse(r.ok, r.message)
            self.assertTrue(r.message.startswith("Not added."), r.message)
        self.assertEqual(Path(w.edit.voice.toml).read_text(encoding="utf-8"), before)

    def test_the_removal_plan_shows_exactly_the_lines_that_go_and_what_stays(self):
        w = F.edit_world()
        plan = P.removal_plan(w.edit, "kylie")
        self.assertEqual(plan.name, "Kylie")
        self.assertIn("[agents.kylie]", plan.block)
        self.assertTrue(any(ln.startswith("voice = ") for ln in plan.block))
        self.assertFalse(any("[agents.jarvis]" in ln for ln in plan.block))
        self.assertIn("Its Hermes profile assistant", plan.stays)
        self.assertIn("all stay", plan.stays)

    def test_a_dry_run_remove_writes_nothing_and_shows_the_same_lines(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        r = P.remove_agent(w.edit, "kylie", now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertEqual(F.tree_hashes(w.root), before)
        self.assertIn("- [agents.kylie]", "\n".join(r.lines))

    def test_armed_remove_touches_only_the_list_and_undo_brings_the_block_back(self):
        w = F.edit_world(armed=True)
        toml = Path(w.edit.voice.toml)
        original = toml.read_text(encoding="utf-8")
        profile_before = F.tree_hashes(w.profiles)
        r = P.remove_agent(w.edit, "kylie", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertNotIn("kylie", [a.key for a in A.agents(A.load(toml))])
        self.assertIn("jarvis", [a.key for a in A.agents(A.load(toml))])
        self.assertEqual(F.tree_hashes(w.profiles), profile_before, "no agent's own file was touched")
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=5), loader=F.lines_loader)
        self.assertTrue(u.ok, u.message)
        self.assertEqual(toml.read_text(encoding="utf-8"), original)

    def test_undo_is_refused_when_the_block_was_changed_by_hand_since(self):
        w = F.edit_world(armed=True)
        toml = Path(w.edit.voice.toml)
        self.assertTrue(P.add_agent(w.edit, "Simba", "simba", "am_fenrir", profile="simba", now=WHEN).ok)
        toml.write_text(toml.read_text(encoding="utf-8").replace('voice = "am_fenrir"', 'voice = "am_adam"'),
                        encoding="utf-8")
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=5), loader=F.lines_loader)
        self.assertFalse(u.ok)
        self.assertIn("changed since", u.message)
        self.assertIn("simba", [a.key for a in A.agents(A.load(toml))])

    def test_a_dry_run_undo_of_an_add_writes_nothing(self):
        w = F.edit_world(armed=True)
        self.assertTrue(P.add_agent(w.edit, "Simba", "simba", "am_fenrir", profile="simba", now=WHEN).ok)
        cold = dataclasses.replace(w.edit, launcher_armed=False)
        before = F.tree_hashes(w.root)
        u = E.undo(cold, now=WHEN, loader=F.lines_loader)
        self.assertTrue(u.ok and u.dry, u.message)
        self.assertEqual(F.tree_hashes(w.root), before)
        self.assertIn("- [agents.simba]", "\n".join(u.lines))


class TheListener(unittest.TestCase):
    def test_the_view_says_what_the_old_second_screen_said(self):
        v = P.listener_view(FakeListener("speaking"))
        self.assertEqual((v.state, v.on_line, v.heard, v.reply, v.turns), ("speaking", "Jarvis", "hello Jarvis",
                                                                           "Good morning.", 3))
        self.assertTrue(v.loaded and v.running)
        labels = [label for label, _ in P.listener_text_lines(v)]
        for label in ("State", "On the line", "Muted", "Its job", "Heard last", "Last reply", "Turns", "Safe to stop"):
            self.assertIn(label, labels)
        self.assertIn("no: a reply is being spoken", dict(P.listener_text_lines(v))["Safe to stop"])

    def test_no_heartbeat_means_off_and_muted_shows_as_muted(self):
        self.assertEqual(P.listener_view(FakeListener(fresh=False)).state, "off")
        self.assertFalse(P.listener_view(FakeListener(fresh=False)).running)
        fake = FakeListener("asleep")
        fake.muted = True
        self.assertEqual(P.listener_view(fake).state, "muted")
        self.assertEqual(P.listener_view(FakeListener("something new")).state, "starting")

    def test_stop_and_restart_are_refused_while_anyone_speaks_or_an_agent_is_mid_reply(self):
        w = F.edit_world(armed=True)
        for state in ("hearing", "thinking", "speaking", "handed"):
            for action in ("stop", "restart"):
                fake = FakeListener(state)
                r = P.listener_do(action, w.edit, svc=fake, confirmed=2, now=WHEN)
                self.assertFalse(r.ok, (state, action))
                self.assertIn("never stops or restarts the voice line", r.message)
                self.assertEqual(fake.orders, [])
        fake = FakeListener("asleep", live=True)                      # quiet itself, but an agent's window is answering
        r = P.listener_do("restart", w.edit, svc=fake, confirmed=2, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("answering a spoken turn", r.message)
        self.assertEqual(fake.orders, [])

    def test_stop_and_restart_need_two_confirmations(self):
        w = F.edit_world(armed=True)
        for confirmed in (0, 1):
            fake = FakeListener("asleep")
            r = P.listener_do("stop", w.edit, svc=fake, confirmed=confirmed, now=WHEN)
            self.assertFalse(r.ok)
            self.assertIn("this stops the voice line", r.message)
            self.assertIn("asks twice", r.message)
            self.assertEqual(fake.orders, [])
        fake = FakeListener("asleep")
        r = P.listener_do("stop", w.edit, svc=fake, confirmed=2, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(fake.orders, ["STOP"])
        self.assertIn("this stops the voice line", r.message)
        fake = FakeListener("asleep")
        self.assertTrue(P.listener_do("restart", w.edit, svc=fake, confirmed=2, now=WHEN).ok)
        self.assertEqual(fake.orders, ["RESTART"])
        self.assertEqual(E.read_history(w.edit)[-1]["kind"], "listener")
        self.assertIsNone(E.last_undoable(w.edit))

    def test_on_a_desk_that_is_not_armed_nothing_is_sent_or_stopped(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        for action in ("mute", "hush", "new_thread", "release", "restart", "stop"):
            fake = FakeListener("asleep")
            r = P.listener_do(action, w.edit, svc=fake, confirmed=2, now=WHEN)
            self.assertTrue(r.ok and r.dry, (action, r.message))
            self.assertEqual(fake.orders, [], action)
        self.assertIn("this stops the voice line", P.listener_do("stop", w.edit, svc=FakeListener(), confirmed=2).message)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_the_small_orders_go_out_as_the_old_dashboard_sent_them(self):
        w = F.edit_world(armed=True)
        fake = FakeListener("speaking")                               # hush must work while it is speaking
        for action in ("mute", "unmute", "hush", "new_thread", "release"):
            self.assertTrue(P.listener_do(action, w.edit, svc=fake, now=WHEN).ok, action)
        self.assertEqual(fake.orders, ["mute", "unmute", "hush", "new_thread", "release"])

    def test_stopping_a_listener_that_is_off_does_nothing_and_restart_then_means_start(self):
        w = F.edit_world(armed=True)
        fake = FakeListener(fresh=False)
        r = P.listener_do("stop", w.edit, svc=fake, confirmed=2, now=WHEN)
        self.assertTrue(r.ok)
        self.assertIn("already off", r.message)
        self.assertEqual(fake.orders, [])
        r = P.listener_do("restart", w.edit, svc=fake, confirmed=2, now=WHEN)
        self.assertTrue(r.ok)
        self.assertIn("is started", r.message)
        self.assertEqual(fake.orders, ["RESTART"])

    def test_a_failed_restart_is_reported_and_an_unknown_order_is_refused(self):
        w = F.edit_world(armed=True)
        fake = FakeListener("asleep")
        fake.restart = lambda: "not installed: run ./install_listener.sh"
        r = P.listener_do("restart", w.edit, svc=fake, confirmed=2, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("not installed", r.message)
        self.assertFalse(P.listener_do("explode", w.edit, svc=fake).ok)


class OnTheScreen(unittest.IsolatedAsyncioTestCase):
    async def test_the_status_line_shows_the_voice_line_and_a_test_desk_never_reaches_the_real_listener(self):
        w = F.with_grid(F.edit_world())
        plain = make_app(w)
        self.assertIsNone(plain.listener_svc)                         # no listener handed over: none is used
        fake = FakeListener("listening")
        app = make_app(w, listener=fake)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            status = str(app.screen.query_one("#systems", Static).render())
            self.assertIn("voice line: listening (Jarvis on the line)", status)
            fake.fresh = False
            app.look_at_listener()
            await pilot.pause()
            self.assertIn("voice line: off", str(app.screen.query_one("#systems", Static).render()))

    async def test_t_asks_who_then_shows_what_would_be_said_and_a_dry_run_makes_nothing(self):
        w = F.with_grid(F.edit_world())
        fake = FakeVoice()
        app = make_app(w, synth=fake.synth, probe=fake.probe)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("right")                                # Kylie's column
            await pilot.press("t")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.PickPopup)
            self.assertEqual([label for _tok, label in app.screen.rows], ["only Kylie", "everyone in the Voice Desk list"])
            await pilot.press("enter")
            await pilot.pause()
            words = body(app)
            self.assertIn("NOTHING IS PLAYED", words)
            self.assertIn('Kylie, English, voice Ava: "This is a Voice Desk test for Kylie."', words)
            self.assertIn("DRY RUN", words)
            await pilot.press("y")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].dry)
            self.assertEqual(fake.said, [])

    async def test_armed_t_for_everyone_runs_the_silent_test(self):
        w = F.with_grid(F.edit_world(armed=True))
        fake = FakeVoice()
        app = make_app(w, synth=fake.synth, probe=fake.probe)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("down")                                 # everyone
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("ARMED", body(app))
            await pilot.press("y")
            await settle(app, pilot)
            r = app.last_results[0]
            self.assertTrue(r.ok and not r.dry, r.message)
            self.assertGreater(len({k for k, *_ in fake.said}), 2)
            self.assertIn("Nothing was played", r.message)

    async def test_d_shows_exactly_what_goes_and_asks_before_anything_is_removed(self):
        w = F.with_grid(F.edit_world(armed=True))
        toml = Path(w.edit.voice.toml)
        original = toml.read_text(encoding="utf-8")
        app = make_app(w)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("right")                                # Kylie's column
            await pilot.press("d")
            await pilot.pause()
            self.assertEqual(app.screen.rows[0], ("kylie", "Kylie"))   # the chosen agent is offered first
            await pilot.press("enter")
            await pilot.pause()
            words = body(app)
            self.assertIn("Remove Kylie from the Voice Desk list?", words)
            self.assertIn("- [agents.kylie]", words)
            self.assertIn("Its Hermes profile assistant", words)
            self.assertIn("u puts it back", words)
            await pilot.press("escape")                               # no: nothing is removed
            await settle(app, pilot)
            self.assertEqual(toml.read_text(encoding="utf-8"), original)
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("y")                                    # yes
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].ok and not app.last_results[0].dry, app.last_results[0].message)
            self.assertNotIn("[agents.kylie]", toml.read_text(encoding="utf-8"))
            await pilot.press("enter")                                # close "Saved"
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            self.assertIn("Kylie is taken out of the Voice Desk list", body(app))
            await pilot.press("y")
            await settle(app, pilot)
            self.assertEqual(toml.read_text(encoding="utf-8"), original)

    async def test_n_walks_through_who_name_names_voice_and_one_confirm(self):
        w = F.with_grid(F.edit_world())
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("n")
            await pilot.pause()
            tokens = [tok for tok, _ in app.screen.rows]
            self.assertIn("simba", tokens)
            self.assertEqual(tokens[-1], P.CLI)
            app.screen.query_one("#box-list", OptionList).highlighted = tokens.index("simba")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.AskPopup)
            box = app.screen.query_one("#box-input", Input)
            box.value = "Simba"
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.screen.query_one("#box-input", Input).value, "simba")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.PickPopup)
            self.assertIn(acd.SILENT, [tok for tok, _ in app.screen.rows])
            picked = app.screen.rows[app.screen.query_one("#box-list", OptionList).highlighted][0]
            await pilot.press("enter")
            await pilot.pause()
            words = body(app)
            self.assertIn("Add Simba?", words)
            self.assertIn("answers to      simba", words)
            self.assertIn("the Hermes agent simba", words)
            self.assertIn("DRY RUN", words)
            await pilot.press("y")
            await settle(app, pilot)
            r = app.last_results[0]
            self.assertTrue(r.ok and r.dry, r.message)
            self.assertIn("+ [agents.simba]", "\n".join(r.lines))
            if picked != acd.SILENT:
                self.assertIn(f'+ voice = "{picked}"', "\n".join(r.lines))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_l_shows_the_listener_and_stop_asks_twice_before_the_order_goes_out(self):
        w = F.with_grid(F.edit_world(armed=True))
        fake = FakeListener("asleep")
        app = make_app(w, listener=fake)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("l")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.ListenerPopup)
            words = body(app)
            for part in ("asleep, waiting for a name", "On the line", "Jarvis", "hello Jarvis", "loaded on this Mac",
                         "yes, nobody is speaking", "asks twice"):
                self.assertIn(part, words)
            await pilot.press("k")                                    # stop
            await pilot.pause()
            self.assertIn("This stops the voice line.", body(app))
            self.assertIn("first of two questions", body(app))
            await pilot.press("y")
            await pilot.pause()
            self.assertIn("Second and last question", body(app))
            self.assertEqual(fake.orders, [], "one yes is not enough")
            await pilot.press("escape")                               # no at the second question
            await settle(app, pilot)
            self.assertEqual(fake.orders, [])
            await pilot.press("k")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
            self.assertEqual(fake.orders, ["STOP"])
            self.assertTrue(app.last_results[0].ok)

    async def test_the_listener_pop_up_refuses_a_restart_while_a_reply_is_being_spoken(self):
        w = F.with_grid(F.edit_world(armed=True))
        fake = FakeListener("speaking")
        app = make_app(w, listener=fake)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("l")
            await settle(app, pilot)
            self.assertIn("no: a reply is being spoken right now", body(app))
            await pilot.press("r")
            await pilot.pause()
            self.assertIn("Not now: a reply is being spoken right now", body(app))
            self.assertNotIn("Y =", body(app))                        # there is nothing to say yes to
            await pilot.press("y")
            await settle(app, pilot)
            self.assertEqual(fake.orders, [])
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("h")                                    # hush is allowed while it speaks
            await settle(app, pilot)
            self.assertEqual(fake.orders, ["hush"])

    async def test_m_in_the_pop_up_mutes_and_then_unmutes_and_a_dry_desk_sends_nothing(self):
        w = F.with_grid(F.edit_world(armed=True))
        fake = FakeListener("asleep")
        app = make_app(w, listener=fake)
        async with app.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("l")
            await settle(app, pilot)
            await pilot.press("m")
            await settle(app, pilot)
            await pilot.press("enter")                                # close the result
            await pilot.pause()
            await pilot.press("m")
            await settle(app, pilot)
            self.assertEqual(fake.orders, ["mute", "unmute"])
        cold = F.with_grid(F.edit_world())
        quiet = FakeListener("asleep")
        app2 = make_app(cold, listener=quiet)
        async with app2.run_test(size=(200, 56)) as pilot:
            await pilot.pause()
            await show(app2, pilot)
            await pilot.press("l")
            await settle(app2, pilot)
            self.assertIn("Dry run", body(app2))
            await pilot.press("m")
            await settle(app2, pilot)
            self.assertTrue(app2.last_results[0].dry)
            self.assertEqual(quiet.orders, [])


if __name__ == "__main__":
    unittest.main()
