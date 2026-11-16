"""Offline tests of the Agent Control Desk, version three: where an agent shows up (the one real switch, Team
Room membership, both doors or nothing) and who it may talk to (the rules file). Fakes and throw-away folders
only: no live settings file, no Team Room, no Telegram, no window, and no real command is ever run.

    .venv/bin/python -m unittest tests.test_acd_v3 -v
"""
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_room  # noqa: E402
import acd_snapshot  # noqa: E402
from textual.widgets import OptionList, Static  # noqa: E402

WHEN = dt.datetime(2026, 11, 17, 10, 0, 0)
L = F.lines_loader
REPO = Path(acd.__file__).resolve().parent


def presence(v, key):
    return next(p for p in v.presence if p.key == key)


def rule(v, key):
    return next(r for r in v.talk_rules if r.key == key)


def never(words):                                                     # a runner that must not be reached
    raise AssertionError(f"the Team Room command was run in a dry run or a refusal: {words}")


def members(w) -> list:
    return [m["profile"] for m in json.loads(w.edit.members.read_text(encoding="utf-8"))["members"]]


def snap(w):
    return F.edit_collect(w)


# ---- what the desk shows ----------------------------------------------------------------------------------
class PresenceFacts(unittest.TestCase):
    def test_only_team_room_membership_is_a_switch_and_it_moves_two_places(self):
        v = F.agent(snap(F.edit_world()), "assistant")
        self.assertTrue(presence(v, "team_room").can_switch)
        self.assertTrue(presence(v, "tg_group").can_switch)
        self.assertIn("one switch moves both", presence(v, "tg_group").evidence)
        for place in ("terminal", "tg_private", "mac_voice", "iphone_voice"):
            p = presence(v, place)
            self.assertFalse(p.can_switch, place)
            self.assertTrue(p.why_not, f"{place} must say why it cannot be switched")
        self.assertIn("never touches secrets", presence(v, "tg_private").why_not)
        self.assertIn("on the iPhone", presence(v, "iphone_voice").why_not)

    def test_the_lead_cannot_be_switched_out(self):
        v = F.agent(snap(F.edit_world()), "orchestrator")
        self.assertTrue(v.room_lead)
        self.assertFalse(presence(v, "team_room").can_switch)
        self.assertIn("leads the Team Room", presence(v, "team_room").why_not)

    def test_unreadable_members_file_means_no_switch(self):
        w = F.edit_world()
        w.edit.members.write_text("not json", encoding="utf-8")
        v = F.agent(snap(w), "assistant")
        self.assertIsNone(v.in_room)
        self.assertFalse(presence(v, "team_room").can_switch)
        self.assertEqual(presence(v, "team_room").state, D.UNKNOWN)
        self.assertIn("could not be read", presence(v, "team_room").why_not)

    def test_doors_that_disagree_are_red_and_named_at_the_top(self):
        w = F.edit_world()
        cfg = w.profiles / "assistant" / "config.yaml"                 # a member with its own bot, add-on off
        cfg.write_text(cfg.read_text(encoding="utf-8").replace(F.ADDON_LINE, ""), encoding="utf-8")
        s = snap(w)
        p = presence(F.agent(s, "assistant"), "tg_group")
        self.assertEqual(p.status, D.RED)
        self.assertIn("do not agree", p.evidence)
        self.assertTrue(any("do not agree for: Kylie" in x.text and x.status == D.RED for x in s.warnings))

    def test_a_non_member_is_off_in_both_places(self):
        w = F.edit_world()
        F.FakeTeamRoom(w)(["kick", "kylie"])
        v = F.agent(snap(w), "assistant")
        self.assertIs(v.in_room, False)
        self.assertEqual((presence(v, "team_room").state, presence(v, "tg_group").state), (D.OFF, D.OFF))
        self.assertTrue(presence(v, "team_room").can_switch)


class TalkRuleFacts(unittest.TestCase):
    def test_rules_say_honestly_where_they_stand_without_the_adapter(self):
        s = snap(F.edit_world())
        self.assertFalse(s.room_follows_rules)
        kylie, jarvis, simba = (F.agent(s, p) for p in ("assistant", "orchestrator", "simba"))
        self.assertEqual(rule(kylie, "speaks").standing, D.WORKS_TODAY)
        self.assertEqual(rule(kylie, "hands_out").standing, D.RECORDED)
        self.assertEqual((rule(kylie, "takes_from").token, rule(kylie, "takes_from").answer), ("lead", "Jarvis"))
        self.assertEqual(rule(kylie, "takes_from").standing, D.RECORDED)
        self.assertEqual(rule(kylie, "answers").token, "when_named")
        self.assertTrue(rule(kylie, "answers").standing.startswith(D.WORKS_TODAY))
        self.assertEqual(rule(jarvis, "answers").token, "always")       # the lead gets every line with no name
        self.assertTrue(rule(jarvis, "answers").standing.startswith(D.WORKS_TODAY))
        self.assertEqual(rule(jarvis, "takes_from").token, "nobody")
        self.assertTrue(rule(jarvis, "speaks").locked)
        self.assertEqual(rule(simba, "answers").token, "when_named")

    def test_a_no_speak_rule_is_recorded_until_the_room_has_the_adapter(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.change_rules("assistant", {"speaks_in_team_room": False}, w.edit, now=WHEN).ok)
        self.assertEqual(rule(F.agent(snap(w), "assistant"), "speaks").standing, D.RECORDED)
        code = w.root / "team_room.py"
        code.write_text("# " + D.ADAPTER_MARK + "\n", encoding="utf-8")
        import dataclasses
        w.paths = dataclasses.replace(w.paths, team_room_code=code)
        s = snap(w)
        self.assertTrue(s.room_follows_rules)
        self.assertTrue(rule(F.agent(s, "assistant"), "speaks").standing.startswith(D.ENFORCED))
        self.assertEqual(rule(F.agent(s, "assistant"), "hands_out").standing, D.TOLD)

    def test_the_real_rules_file_parses_and_keeps_simba_as_she_is(self):
        rules = D.load_rules(REPO / "acd_rules.toml")
        simba = D.rules_for(rules, "simba")
        self.assertTrue(simba["answers_locked"])
        self.assertEqual(D.answers_token(simba, False), "when_named")
        self.assertTrue(D.rules_for(rules, "orchestrator")["hands_out_work"])
        self.assertTrue(D.rules_for(rules, "builder")["only_job_cards"])


# ---- the talk rules: the safe writer ------------------------------------------------------------------------
class RuleChanges(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        r = E.change_rules("assistant", {"speaks_in_team_room": False}, w.edit, now=WHEN)
        self.assertTrue(r.ok and r.dry)
        self.assertIn("DRY RUN, nothing written", r.message)
        self.assertIn("Kylie now may not speak in the Team Room", r.message)
        self.assertEqual([ln for ln in r.lines if ln.strip() != "+"],
                         ["+ [agents.assistant]", "+ speaks_in_team_room = false"])
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_armed_change_moves_only_its_line_backs_up_and_undoes_exactly(self):
        w = F.edit_world(armed=True)
        old = w.edit.rules.read_text(encoding="utf-8")
        r = E.change_rules("tax_angel_esther", {"only_job_cards": True, "takes_work_from": "orchestrator"}, w.edit,
                           now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        new = w.edit.rules.read_text(encoding="utf-8")
        self.assertEqual(E.diff_lines(old, new), ["+ only_job_cards = true"])     # takes_work_from already said so
        self.assertIn('model_note = "meant to stay fixed on Claude Opus 5.5"', new)
        self.assertEqual(Path(r.entry["files"][0]["backup"]).read_text(encoding="utf-8"), old)
        self.assertEqual(r.entry["before"], {"only_job_cards": None})
        self.assertEqual(rule(F.agent(snap(w), "tax_angel_esther"), "takes_from").token, "cards")
        self.assertIn("Esther now takes work only as job cards", E.record(w.edit).last_change)
        u = E.undo(w.edit, now=WHEN + dt.timedelta(minutes=1), loader=L)
        self.assertTrue(u.ok, u.message)
        self.assertEqual(w.edit.rules.read_text(encoding="utf-8"), old, "undo must put the file back exactly")
        self.assertIsNone(E.last_undoable(w.edit))

    def test_an_agent_with_no_block_of_its_own_gets_one(self):
        w = F.edit_world(armed=True)
        self.assertNotIn("[agents.assistant]", w.edit.rules.read_text(encoding="utf-8"))
        r = E.change_rules("assistant", {"answers": "always"}, w.edit, now=WHEN)
        self.assertTrue(r.ok, r.message)
        data = tomllib.loads(w.edit.rules.read_text(encoding="utf-8"))
        self.assertEqual(data["agents"]["assistant"], {"answers": "always"})
        self.assertEqual(rule(F.agent(snap(w), "assistant"), "answers").standing, D.RECORDED)

    def test_refusals_change_nothing(self):
        w = F.edit_world(armed=True)
        before = F.tree_hashes(w.root)
        for changes, words in (({"mood": "happy"}, "not a talk rule"),
                               ({"speaks_in_team_room": "maybe"}, "cannot be set"),
                               ({"answers": "sometimes"}, "cannot be set")):
            r = E.change_rules("assistant", changes, w.edit, now=WHEN)
            self.assertFalse(r.ok)
            self.assertIn(words, r.message)
        r = E.change_rules("simba", {"answers": "always"}, w.edit, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("kept as it is", r.message)
        r = E.change_rules("assistant", {"speaks_in_team_room": True}, w.edit, now=WHEN)
        self.assertTrue(r.ok)
        self.assertIn("nothing to change", r.message)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_a_rules_file_the_desk_does_not_recognise_is_refused(self):
        for text, words in (("this is [not toml", "cannot be read as it stands"),
                            ("title = 'no defaults here'\n", "not in the shape the desk knows")):
            w = F.edit_world(armed=True)
            w.edit.rules.write_text(text, encoding="utf-8")
            before = F.tree_hashes(w.root)
            r = E.change_rules("assistant", {"speaks_in_team_room": False}, w.edit, now=WHEN)
            self.assertFalse(r.ok)
            self.assertIn(words, r.message)
            self.assertEqual(F.tree_hashes(w.root), before)

    def test_a_change_that_does_not_read_back_is_put_back(self):
        w = F.edit_world(armed=True)
        old = w.edit.rules.read_text(encoding="utf-8")
        real = D.load_rules
        D.load_rules = lambda path: {"defaults": {"speaks_in_team_room": True}, "agents": {}}   # the edit "did not take"
        try:
            r = E.change_rules("assistant", {"speaks_in_team_room": False}, w.edit, now=WHEN)
        finally:
            D.load_rules = real
        self.assertFalse(r.ok)
        self.assertIn("put back exactly as it was", r.message)
        self.assertEqual(w.edit.rules.read_text(encoding="utf-8"), old)
        self.assertEqual(E.read_history(w.edit), [])


# ---- Team Room membership: both doors, or nothing --------------------------------------------------------------
class TeamRoomSwitch(unittest.TestCase):
    def test_dry_run_runs_nothing_and_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        r = E.set_team_room("assistant", False, w.edit, runner=never, now=WHEN, loader=L)
        self.assertTrue(r.ok and r.dry)
        self.assertIn("Kylie leaves the Team Room (the terminal room and the Telegram group together)", r.message)
        self.assertEqual(r.lines[0], "- Team Room members: Jarvis, Kylie, Esther, Wanna Buffet, Simba")
        self.assertEqual(r.lines[1], "+ Team Room members: Jarvis, Esther, Wanna Buffet, Simba")
        self.assertIn("team-room kick kylie", r.lines[2])
        self.assertIn("Peter's own tap in Telegram", r.needs)
        self.assertIn("chat on screen starts fresh", r.needs)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_armed_leave_changes_both_doors_and_undo_puts_the_agent_back(self):
        w = F.edit_world(armed=True)
        room = F.FakeTeamRoom(w)
        r = E.set_team_room("assistant", False, w.edit, runner=room, now=WHEN, loader=L)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(room.calls, [["kick", "kylie"]])
        self.assertNotIn("assistant", members(w))
        self.assertNotIn(F.ADDON_LINE, (w.profiles / "assistant" / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual((r.entry["handle"], r.entry["name"], r.entry["room_after"]), ("kylie", "Kylie", "team-5"))
        v = F.agent(snap(w), "assistant")
        self.assertEqual((presence(v, "team_room").state, presence(v, "tg_group").state), (D.OFF, D.OFF))
        self.assertEqual(E.record(w.edit).waiting, ())
        u = E.undo(w.edit, now=WHEN + dt.timedelta(minutes=1), loader=L, runner=room)
        self.assertTrue(u.ok, u.message)
        self.assertEqual(room.calls[-1], ["add", "assistant", "kylie", "Kylie"])
        self.assertIn("assistant", members(w))
        self.assertIn(F.ADDON_LINE, (w.profiles / "assistant" / "config.yaml").read_text(encoding="utf-8"))
        self.assertIsNone(E.last_undoable(w.edit))

    def test_when_the_telegram_side_does_not_follow_the_room_is_put_back(self):
        w = F.edit_world(armed=True)
        room = F.FakeTeamRoom(w, wire=False)                         # the member list changes, the add-on does not
        r = E.set_team_room("assistant", False, w.edit, runner=room, now=WHEN, loader=L)
        self.assertFalse(r.ok)
        self.assertIn("ran the Team Room's command backwards", r.message)
        self.assertIn("the room is as it was", r.message)
        self.assertEqual(room.calls, [["kick", "kylie"], ["add", "assistant", "kylie", "Kylie"]])
        self.assertIn("assistant", members(w))
        self.assertEqual(E.read_history(w.edit), [], "a change that was put back leaves no history line")

    def test_when_the_room_cannot_be_made_nothing_changes(self):
        w = F.edit_world(armed=True)
        before_members = members(w)
        room = F.FakeTeamRoom(w, fail=True)
        r = E.set_team_room("assistant", False, w.edit, runner=room, now=WHEN, loader=L)
        self.assertFalse(r.ok)
        self.assertIn("nothing is different at either door", r.message)
        self.assertEqual(members(w), before_members)
        self.assertEqual(E.read_history(w.edit), [])

    def test_refusals_never_reach_the_team_room(self):
        w = F.edit_world(armed=True)
        r = E.set_team_room("orchestrator", False, w.edit, runner=never, now=WHEN, loader=L)
        self.assertFalse(r.ok)
        self.assertIn("leads the Team Room", r.message)
        r = E.set_team_room("nobody-here", True, w.edit, runner=never, now=WHEN, loader=L)
        self.assertFalse(r.ok)
        r = E.set_team_room("assistant", True, w.edit, runner=never, now=WHEN, loader=L)
        self.assertTrue(r.ok)
        self.assertIn("already in the Team Room", r.message)
        for bad, words in (("not json", "could not be read"), (json.dumps({"members": [1, 2]}), "not in the shape")):
            w.edit.members.write_text(bad, encoding="utf-8")
            r = E.set_team_room("assistant", False, w.edit, runner=never, now=WHEN, loader=L)
            self.assertFalse(r.ok)
            self.assertIn(words, r.message)

    def test_an_agent_with_no_bot_of_its_own_needs_no_telegram_wiring(self):
        w = F.edit_world(armed=True)                                 # Esther has no bot in this fake world
        room = F.FakeTeamRoom(w)
        r = E.set_team_room("tax_angel_esther", False, w.edit, runner=room, now=WHEN, loader=L)
        self.assertTrue(r.ok, r.message)
        self.assertIn("no bot of its own", r.needs)
        self.assertEqual(room.calls, [["kick", "esther"]])

    def test_a_join_that_needs_a_restart_lands_on_the_waiting_list(self):
        w = F.edit_world(armed=True)
        quiet = F.FakeTeamRoom(w)
        self.assertTrue(E.set_team_room("assistant", False, w.edit, runner=quiet, now=WHEN, loader=L).ok)
        said = "No running gateway answered. If one is running, restart it for this to take effect"
        room = F.FakeTeamRoom(w, says=said)
        r = E.set_team_room("assistant", True, w.edit, runner=room, now=WHEN + dt.timedelta(minutes=5), loader=L,
                            handle="kylie", name="Kylie")
        self.assertTrue(r.ok, r.message)
        self.assertTrue(r.entry["needs_restart"])
        self.assertIn("A RESTART IS NEEDED", r.needs)
        waiting = E.record(w.edit).waiting
        self.assertEqual(len(waiting), 1)
        self.assertIn("Kylie joins the Team Room", waiting[0])
        self.assertIn("waits for a restart", waiting[0])
        s = snap(w)
        self.assertTrue(any("waiting for a restart" in x.text for x in s.warnings))


class TheOneCommand(unittest.TestCase):
    def test_only_add_and_kick_are_ever_run(self):
        run = acd_room.default_runner(Path("/bin/echo"))
        self.assertEqual(run(["setup"])[0], 2)
        self.assertEqual(run([])[0], 2)
        self.assertEqual(acd_room.default_runner(Path("/no/such/command"))(["add", "x"])[0], 127)
        self.assertEqual(acd_room.default_runner(None)(["kick", "x"])[0], 127)
        code, said = run(["add", "someone", "handle", "Some One"])      # /bin/echo: harmless, proves the words
        self.assertEqual((code, said.strip()), (0, "add someone handle Some One"))

    def test_the_restart_sentence_is_recognised(self):
        self.assertTrue(acd_room.says_restart("Only the saved config changed; restart the gateway serving this profile"))
        self.assertTrue(acd_room.says_restart("No running gateway answered. If one is running, restart it for this"))
        self.assertFalse(acd_room.says_restart("done: add Kylie. New room team-5."))
        self.assertFalse(acd_room.says_restart("No restart is involved at any point."))

    def test_only_this_one_module_starts_a_program(self):
        for name in ("acd.py", "acd_data.py", "acd_edit.py"):
            self.assertNotIn("subprocess", (REPO / name).read_text(encoding="utf-8"), name)
        src = (REPO / "acd_room.py").read_text(encoding="utf-8")
        for forbidden in ("os.system", "launchctl", "kill(", "write_text", "unlink(", "urllib", "socket", "shell=True"):
            self.assertNotIn(forbidden, src)


# ---- the screen ---------------------------------------------------------------------------------------------
def edit_app(w, runner=never):
    return acd.Desk(collector=lambda: F.edit_collect(w), autostart=False, edit=w.edit, loader=F.lines_loader,
                    tester=None, runner=runner)


def box(app, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).render())


def list_text(app) -> str:
    lst = app.screen.query_one("#box-list", OptionList)
    return "\n".join(str(lst.get_option_at_index(i).prompt) for i in range(lst.option_count))


async def open_setting(app, pilot, agent_index: int, setting: str):
    app.apply_snapshot(app.collector())
    await pilot.pause()
    app.open_tree(agent_index)
    await pilot.pause()
    tree = app.screen.query_one("#tree", OptionList)
    tree.highlighted = app.screen.settings.index(setting)
    await pilot.press("enter")
    await pilot.pause()


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


class Screen(unittest.IsolatedAsyncioTestCase):
    async def test_tree_shows_switches_reasons_and_where_each_rule_stands(self):
        w = F.edit_world()
        app = edit_app(w)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            app.apply_snapshot(app.collector())
            app.open_tree(1)                                             # Kylie
            await pilot.pause()
            self.assertEqual(app.screen.settings.count("team_room"), 2)  # the two places that move together
            self.assertEqual(app.screen.settings.count("talk"), 4)
            plain = re.sub(r"[\s│]+", " ", app.screen.tree_plain())
            for words in ("one switch moves both", "never touches secrets", "lives on the iPhone",
                          "Speaks in the Team Room", "recorded, not yet enforced", "how the room works today"):
                self.assertIn(words, plain, words)
            app.pop_screen()
            app.open_tree(0)                                             # Jarvis leads: no switch for him
            await pilot.pause()
            self.assertNotIn("team_room", app.screen.settings)
            self.assertIn("cannot be taken out of it", re.sub(r"[\s│]+", " ", app.screen.tree_plain()))

    async def test_team_room_popup_dry_run_from_the_screen_writes_and_runs_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = edit_app(w)                                                # runner=never: must not be reached
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 1, "team_room")
            self.assertIsInstance(app.screen, acd.EditPopup)
            self.assertIn("Kylie: Team Room and Telegram team group", box(app, "#box-title"))
            self.assertIn("in the room   now", list_text(app))
            await pilot.press("down")
            await pilot.press("enter")                                   # choose "out of the room"
            await pilot.pause()
            self.assertIn("out of the room   <- chosen", list_text(app))
            await pilot.press("s")
            await pilot.pause()
            confirm = box(app, "#box-confirm").replace("\n", " ")
            self.assertIn("DRY RUN", confirm)
            self.assertIn("change together, or not at all", confirm)
            self.assertIn("chat on screen starts fresh", confirm)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.NoticePopup)
            body = box(app, "#box-text")
            self.assertIn("This would happen: Kylie leaves the Team Room", body)
            self.assertIn("team-room kick kylie", body)
        self.assertEqual(F.tree_hashes(w.root), before, "a dry run from the screen wrote something")

    async def test_armed_team_room_change_from_the_screen_and_u_undoes(self):
        w = F.edit_world(armed=True)
        room = F.FakeTeamRoom(w)
        app = edit_app(w, runner=room)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 1, "team_room")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press("s")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved: Kylie leaves the Team Room", box(app, "#box-text"))
            self.assertNotIn("assistant", members(w))
            await pilot.press("enter")
            await settle(app, pilot)
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
            self.assertIn("assistant", members(w))
        self.assertEqual(room.calls, [["kick", "kylie"], ["add", "assistant", "kylie", "Kylie"]])

    async def test_talk_popup_offers_plain_choices_and_saves_as_a_dry_run(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = edit_app(w)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 1, "talk")
            self.assertIn("Kylie: who it may talk to", box(app, "#box-title"))
            listed = list_text(app)
            for words in ("May speak in the Team Room", "Hands out work (only Jarvis by default)", "Takes work from",
                          "job cards only", "nobody (Peter only)", "Answers", "when named in the line   now"):
                self.assertIn(words, listed)
            await pilot.press("down")
            await pilot.press("enter")                                   # May speak: no
            await pilot.press("s")
            await pilot.pause()
            confirm = box(app, "#box-confirm").replace("\n", " ")
            self.assertIn("Kylie may not speak in the Team Room.", confirm)
            self.assertIn("recorded, not yet enforced", confirm)
            await pilot.press("enter")
            await settle(app, pilot)
            body = box(app, "#box-text")
            self.assertIn("DRY RUN, nothing written", body)
            self.assertIn("+ speaks_in_team_room = false", body)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_armed_talk_rule_shows_in_the_tree_at_once(self):
        w = F.edit_world(armed=True)
        app = edit_app(w)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 2, "talk")                    # Esther
            lst = app.screen.query_one("#box-list", OptionList)
            target = next(i for i in range(lst.option_count) if "job cards only" in str(lst.get_option_at_index(i).prompt))
            lst.highlighted = target
            await pilot.press("enter")
            await pilot.press("s")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved: Esther now takes work only as job cards", box(app, "#box-text").replace("\n", " "))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("only as job cards", app.screen.tree_plain())
        self.assertTrue(tomllib.loads(w.edit.rules.read_text(encoding="utf-8"))["agents"]["tax_angel_esther"]["only_job_cards"])

    async def test_simba_keeps_answers_when_named_and_the_lead_cannot_be_muted(self):
        w = F.edit_world()
        app = edit_app(w)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await open_setting(app, pilot, 4, "talk")                    # Simba
            lst = app.screen.query_one("#box-list", OptionList)
            rows = {str(lst.get_option_at_index(i).prompt): lst.get_option_at_index(i).disabled
                    for i in range(lst.option_count)}
            locked = [off for text, off in rows.items() if "kept as it is" in text]
            self.assertEqual(locked, [True, True], rows)                 # both ways of answering are greyed out
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await open_setting(app, pilot, 0, "talk")                    # Jarvis, the lead
            lst = app.screen.query_one("#box-list", OptionList)
            no_row = next(lst.get_option_at_index(i) for i in range(lst.option_count)
                          if "leads the room" in str(lst.get_option_at_index(i).prompt))
            self.assertTrue(no_row.disabled)

    async def test_restart_review_lists_a_real_team_room_entry(self):
        w = F.edit_world(armed=True)
        E.set_team_room("assistant", False, w.edit, runner=F.FakeTeamRoom(w), now=WHEN, loader=L)
        E.set_team_room("assistant", True, w.edit, now=WHEN + dt.timedelta(minutes=5), loader=L, handle="kylie",
                        name="Kylie", runner=F.FakeTeamRoom(w, says="No running gateway answered. If one is running, "
                                                                  "restart it for this to take effect"))
        app = edit_app(w)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            app.apply_snapshot(app.collector())
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            body = box(app, "#box-text").replace("\n", " ")
            self.assertIn("1 saved change is waiting for a restart", body)
            self.assertIn("Kylie joins the Team Room", body)

    async def test_version_three_pictures_are_drawn_without_a_window(self):
        w = F.edit_world()
        out = Path(tempfile.mkdtemp(prefix="acd-shot3-"))
        made = await acd_snapshot.render_v3(out, collector=lambda: F.edit_collect(w), agent=1, size=(170, 60),
                                            desk_kwargs={"edit": w.edit, "loader": F.lines_loader, "tester": None})
        self.assertEqual({p.name for p in made}, {"v3_tree.svg", "v3_presence_popup.svg", "v3_talk_popup.svg",
                                                  "v3_restart_popup.svg"})
        self.assertIn("Team", (out / "v3_presence_popup.svg").read_text(encoding="utf-8"))


# ---- the Team Room adapter, on a throw-away copy of the Team Room's program -----------------------------------
LIVE_TEAM_ROOM = Path.home() / "GitHub" / "Team_Room" / "team_room.py"
PATCH = REPO / "docs" / "team_room_rules_adapter.patch"


@unittest.skipUnless(LIVE_TEAM_ROOM.is_file() and shutil.which("patch"),
                     "needs the Team Room's program to copy and the patch tool")
class Adapter(unittest.TestCase):
    """The live Team Room is only READ (copied). The patch is applied to the copy, and the copy is loaded with
    every one of its files pointed at the throw-away folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="acd-adapter-"))
        shutil.copy2(LIVE_TEAM_ROOM, cls.tmp / "team_room.py")
        cls.original = (cls.tmp / "team_room.py").read_text(encoding="utf-8")
        done = subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-i", str(PATCH)], cwd=cls.tmp,
                              capture_output=True, text=True)
        cls.patch_out = done.stdout + done.stderr
        cls.applied = done.returncode == 0
        cls.rules = cls.tmp / "rules.toml"
        keep = {k: os.environ.get(k) for k in ("TEAM_ROOM_ROSTER", "TEAM_ROOM_HISTORY", "TEAM_ROOM_STATE",
                                               "TEAM_ROOM_MIRROR_STATE", "TEAM_ROOM_PROFILES", "TEAM_ROOM_DESK_RULES",
                                               "TEAM_ROOM_NO_WIRING")}
        os.environ.update(TEAM_ROOM_ROSTER=str(cls.tmp / "members.json"), TEAM_ROOM_HISTORY=str(cls.tmp / "history"),
                          TEAM_ROOM_STATE=str(cls.tmp / "state.json"), TEAM_ROOM_MIRROR_STATE=str(cls.tmp / "state"),
                          TEAM_ROOM_PROFILES=str(cls.tmp / "profiles"), TEAM_ROOM_DESK_RULES=str(cls.rules),
                          TEAM_ROOM_NO_WIRING="1")
        try:
            def load(name, path):
                spec = importlib.util.spec_from_file_location(name, path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod                     # dataclasses look the module up while it loads
                spec.loader.exec_module(mod)
                return mod
            (cls.tmp / "plain").mkdir()
            (cls.tmp / "plain" / "team_room.py").write_text(cls.original, encoding="utf-8")
            cls.plain = load("acd_test_team_room_plain", cls.tmp / "plain" / "team_room.py")
            cls.tr = load("acd_test_team_room_patched", cls.tmp / "team_room.py") if cls.applied else None
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def setUp(self):
        self.assertTrue(self.applied, f"the patch no longer applies to the Team Room's program:\n{self.patch_out}")
        self.rules.unlink(missing_ok=True)
        self.tr.desk_rules()                                             # forget the last test's rules

    def write_rules(self, text: str) -> None:
        self.rules.write_text(text, encoding="utf-8")
        os.utime(self.rules, ns=(1, hash(text) % 10**12 + len(text)))    # a new change time every time
        self.tr._desk_rules_cache["key"] = None

    def test_the_patch_only_adds_and_carries_the_marker(self):
        removed = [ln for ln in PATCH.read_text(encoding="utf-8").splitlines()
                   if ln.startswith("-") and not ln.startswith("---")]
        self.assertEqual(len(removed), 1, removed)                       # the one notes line that gains the paragraph
        self.assertIn(D.ADAPTER_MARK, (self.tmp / "team_room.py").read_text(encoding="utf-8"))
        self.assertTrue(D.room_follows_rules(self.tmp / "team_room.py"))
        self.assertFalse(D.room_follows_rules(self.tmp / "plain" / "team_room.py"))

    def test_with_no_rules_file_the_room_behaves_exactly_as_before(self):
        self.assertEqual(self.tr.desk_rules(), {})
        self.assertEqual(self.tr.notes(), self.plain.notes())
        self.assertEqual(self.tr.purpose(), self.plain.purpose())
        for said in ("Kylie, check my calendar", "thanks Wanna Buffet", "hey everyone", "Simba what now",
                     "/continue", "[Jarvis] hello Kylie", "@esther and Builder please"):
            self.assertEqual(self.tr.peter_addressing(said), self.plain.peter_addressing(said), said)

    def test_an_unreadable_rules_file_also_changes_nothing(self):
        for text in ("this is [not toml", "title = 'no defaults'\n", "defaults = 3\n[agents]\n"):
            self.write_rules(text)
            self.assertEqual(self.tr.desk_rules(), {}, text)
            self.assertEqual(self.tr.notes(), self.plain.notes())
            self.assertEqual(self.tr.peter_addressing("Kylie, check my calendar"), "@kylie, check my calendar")

    def test_todays_rules_keep_addressing_the_same_and_add_one_paragraph_to_the_notes(self):
        self.write_rules((REPO / "acd_rules.toml").read_text(encoding="utf-8"))
        for said in ("Kylie, check my calendar", "thanks Wanna Buffet", "hey everyone", "Simba what now"):
            self.assertEqual(self.tr.peter_addressing(said), self.plain.peter_addressing(said), said)
        note = self.tr.desk_rules_note()
        self.assertIn("Only Jarvis hands out work.", note)
        self.assertIn("Builder takes work only as job cards.", note)
        self.assertEqual(self.tr.notes().replace(note, ""), self.plain.notes())

    def test_a_no_speak_rule_stops_peters_plain_mention_at_both_doors(self):
        self.write_rules("[defaults]\nspeaks_in_team_room = true\n[agents.assistant]\nspeaks_in_team_room = false\n")
        self.assertFalse(self.tr.may_speak("assistant"))
        self.assertTrue(self.tr.may_speak("tax_angel_esther"))
        # peter_addressing is the one function both doors use (the room's window and the Telegram relay)
        self.assertEqual(self.tr.peter_addressing("Kylie, check my calendar"), "Kylie, check my calendar")
        self.assertEqual(self.tr.peter_addressing("Esther, check my calendar"), "@esther, check my calendar")
        self.assertEqual(self.tr.peter_addressing("@kylie check it"), "@kylie check it")     # his explicit @ stands
        self.assertIn("Kylie is listening only for now", self.tr.notes())

    def test_a_changed_rules_file_is_picked_up_without_a_reload(self):
        self.write_rules("[defaults]\nspeaks_in_team_room = true\n[agents.assistant]\nspeaks_in_team_room = false\n")
        self.assertFalse(self.tr.may_speak("assistant"))
        self.write_rules("[defaults]\nspeaks_in_team_room = true\n[agents.assistant]\nspeaks_in_team_room = true\n")
        self.assertTrue(self.tr.may_speak("assistant"))

    def test_the_live_team_room_was_only_read(self):
        self.assertEqual(LIVE_TEAM_ROOM.read_text(encoding="utf-8"), self.original)


if __name__ == "__main__":
    unittest.main()
