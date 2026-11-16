"""Offline tests of the Agent Control Desk's facts (acd_data.py): fake settings files and fake logs in a
throw-away folder. No window, no network, no model, and nothing of Peter's is read or written.

    .venv/bin/python -m unittest tests.test_acd_data -v
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd_data as D  # noqa: E402

AGY = F.AGY
QUOTA = "Antigravity model error: Individual quota reached. Please upgrade. Resets in 82h29m17s."


def presence(view: D.AgentView, key: str) -> D.Presence:
    return next(p for p in view.presence if p.key == key)


class ParseLog(unittest.TestCase):
    def test_plain_reply(self):
        turns = D.parse_log(F.turn(F.NOW, "s1", "claude-opus-5-5", "anthropic", "telegram", 4))
        self.assertEqual(len(turns), 1)
        t = turns[0]
        self.assertEqual((t.start_model, t.start_service, t.end_model, t.platform, t.history, t.dropped),
                         ("claude-opus-5-5", "anthropic", "claude-opus-5-5", "telegram", 4, False))

    def test_drop_to_a_backup_keeps_the_reason(self):
        text = "\n".join([F.turn(F.NOW, "s1", "claude-sonnet-5-5", AGY),
                          F.failed(F.NOW + 8, "s1", "claude-sonnet-5-5", AGY, QUOTA),
                          F.fallback(F.NOW + 8, "s1", "claude-sonnet-5-5", "gemini-3.8-flash", AGY)])
        t = D.parse_log(text)[0]
        self.assertTrue(t.dropped)
        self.assertEqual((t.end_model, t.end_service, t.drops), ("gemini-3.8-flash", AGY, 1))
        self.assertIn("quota reached", t.reason)

    def test_a_drop_belongs_to_its_own_chat_only(self):
        text = "\n".join([F.turn(F.NOW, "a", "claude-opus-5-5", "anthropic"),
                          F.turn(F.NOW + 1, "b", "claude-opus-5-5", "anthropic"),
                          F.fallback(F.NOW + 2, "a", "claude-opus-5-5", "gemini-3.8-flash", AGY)])
        a, b = D.parse_log(text)
        self.assertTrue(a.dropped)
        self.assertFalse(b.dropped)

    def test_empty_answer_drop(self):
        text = "\n".join([F.turn(F.NOW, "s1", "claude-opus-5-5", "anthropic"),
                          f"{F.stamp(F.NOW + 3)} INFO [s1] agent.conversation_loop: Fallback activated after empty "
                          "responses: now using nvidia/nemotron-3.5-lightning-30b-a3b on nvidia"])
        t = D.parse_log(text)[0]
        self.assertEqual((t.end_service, t.drops), ("nvidia", 1))
        self.assertIn("nothing", t.reason)

    def test_sign_in_failure_attaches_to_the_next_reply_only(self):
        text = "\n".join([F.signin_failed(F.NOW), F.turn(F.NOW + 1, "s1", "gemini-3.8-flash", AGY),
                          F.turn(F.NOW + 600, "s1", "gemini-3.8-flash", AGY, history=2)])
        first, second = D.parse_log(text)
        self.assertIn("No Anthropic credentials", first.signin_failed)
        self.assertEqual(second.signin_failed, "")

    def test_rubbish_lines_are_ignored(self):
        self.assertEqual(D.parse_log("not a log line\n\n2026-11-16 garbage\n"), [])


class Words(unittest.TestCase):
    def test_names(self):
        self.assertEqual(D.model_name("claude-sonnet-5-5"), "Claude Sonnet 5.5")
        self.assertEqual(D.model_name("nvidia/nemotron-3.5-lightning-30b-a3b"), "NVIDIA Nemotron 3.5 Lightning")
        self.assertEqual(D.model_name("some-new-model"), "some-new-model")
        self.assertEqual(D.model_name(None), "unknown")
        self.assertEqual(D.service_name("anthropic"), "the Claude subscription")
        self.assertEqual(D.service_name("brand-new"), "brand-new")

    def test_voice_labels(self):
        self.assertEqual(D.voice_label("am_puck"), "Puck, local")
        self.assertEqual(D.voice_label("en-US-AvaNeural"), "Ava")
        self.assertEqual(D.voice_label("zh-TW-HsiaoChenNeural"), "HsiaoChen")
        self.assertEqual(D.voice_label("alloy"), "Alloy")
        self.assertEqual(D.voice_label(None), "none")

    def test_plain_reasons(self):
        self.assertEqual(D.plain_reason(QUOTA), "its allowance is used up, resets in about 82 hours")
        self.assertEqual(D.plain_reason("No Anthropic credentials found. Run x"), "no sign-in found for it")
        self.assertEqual(D.plain_reason(""), "")
        self.assertTrue(D.plain_reason("x" * 300).endswith("..."))


class Collect(unittest.TestCase):
    def test_plain_reply_is_green(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("claude-opus-5-5", AGY), ("gemini-3.8-flash", AGY)])
        F.log(w, "assistant", F.turn(w.now - 60, "s1", "claude-opus-5-5", "anthropic", "telegram"))
        snap = F.collect(w)
        v = F.agent(snap, "assistant")
        self.assertEqual(v.name, "Kylie")
        self.assertEqual((v.model.model, v.model.service, v.model.effort), ("claude-opus-5-5", "anthropic", "medium"))
        self.assertEqual((v.last.status, v.last.answered_model), (D.GREEN, "claude-opus-5-5"))
        self.assertEqual([(b.position, b.model, b.on) for b in v.backups],
                         [(1, "claude-opus-5-5", True), (2, "gemini-3.8-flash", True)])
        self.assertEqual(v.worst, D.GREEN)
        self.assertEqual([wn.status for wn in snap.warnings], [D.GREEN])
        self.assertEqual(snap.warnings[0].text, "nothing needs attention")

    def test_reply_that_dropped_to_a_backup_is_red_with_the_reason(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("gemini-3.8-flash", AGY)])
        F.log(w, "assistant", F.turn(w.now - 60, "s1", "claude-opus-5-5", "anthropic"),
              F.failed(w.now - 50, "s1", "claude-opus-5-5", "anthropic", QUOTA),
              F.fallback(w.now - 50, "s1", "claude-opus-5-5", "gemini-3.8-flash", AGY))
        snap = F.collect(w)
        v = F.agent(snap, "assistant")
        self.assertEqual(v.last.status, D.RED)
        self.assertTrue(v.last.dropped)
        self.assertEqual(v.last.answered_model, "gemini-3.8-flash")
        self.assertIn("allowance is used up", v.last.note)
        self.assertIn("Gemini 3.8 Flash answered", v.last.note)
        self.assertEqual(v.drops_today, 1)
        self.assertTrue(any(wn.status == D.RED and "1 agent had the main model fail" in wn.text
                            for wn in snap.warnings))

    def test_drops_from_yesterday_are_not_counted_today(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        day = 24 * 3600
        F.log(w, "assistant", F.turn(w.now - day - 60, "s0", "claude-opus-5-5", "anthropic"),
              F.fallback(w.now - day - 50, "s0", "claude-opus-5-5", "gemini-3.8-flash", AGY),
              F.turn(w.now - 60, "s1", "claude-opus-5-5", "anthropic"))
        self.assertEqual(F.agent(F.collect(w), "assistant").drops_today, 0)

    def test_old_chat_on_an_older_service_is_yellow(self):
        w = F.make_world()
        F.add_agent(w, "orchestrator", model="claude-sonnet-5-5", backups=[("claude-sonnet-5-5", AGY)])
        F.log(w, "orchestrator", F.turn(w.now - 60, "20261104_old", "claude-sonnet-5-5", AGY, "cli", history=88))
        snap = F.collect(w)
        v = F.agent(snap, "orchestrator")
        self.assertEqual(v.last.status, D.YELLOW)
        self.assertIn("older setting", v.last.note)
        self.assertEqual(v.old_chats, 1)
        self.assertEqual(presence(v, "terminal").status, D.YELLOW)
        self.assertTrue(any(wn.status == D.YELLOW and "1 open chat is still on an older setting" in wn.text
                            for wn in snap.warnings))

    def test_sign_in_failure_is_red_not_an_old_chat(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("gemini-3.8-flash", AGY)])
        F.log(w, "assistant", F.signin_failed(w.now - 61),
              F.turn(w.now - 60, "s1", "gemini-3.8-flash", AGY, "cli"))
        snap = F.collect(w)
        v = F.agent(snap, "assistant")
        self.assertEqual(v.last.status, D.RED)
        self.assertIn("could not sign in", v.last.note)
        self.assertEqual(v.old_chats, 0)
        self.assertTrue(v.signin_failed)
        self.assertTrue(any("could not sign in for 1 agent" in wn.text for wn in snap.warnings))

    def test_job_pin_and_chat_choice_are_blue(self):
        w = F.make_world()
        F.add_agent(w, "builder", backups=[("gemini-4-argon", AGY)], addons=())
        F.log(w, "builder", F.turn(w.now - 60, "job1", "claude-sonnet-5-5", "anthropic", "kanban"))
        v = F.agent(F.collect(w), "builder")
        self.assertEqual(v.last.status, D.BLUE)
        self.assertEqual((v.override.model, v.override.where), ("claude-sonnet-5-5", "kanban"))
        self.assertEqual(v.worst, D.BLUE)

        w2 = F.make_world()
        F.add_agent(w2, "assistant")
        F.log(w2, "assistant", F.turn(w2.now - 60, "s1", "claude-haiku-5-5", "anthropic", "cli"))
        v2 = F.agent(F.collect(w2), "assistant")
        self.assertEqual(v2.last.status, D.BLUE)
        self.assertIn("picked for this chat only", v2.last.note)

    def test_agent_with_no_log_shows_unknown_never_a_guess(self):
        w = F.make_world()
        folder = F.add_agent(w, "assistant")
        (folder / "logs").rmdir()
        v = F.agent(F.collect(w), "assistant")
        self.assertIsNone(v.last)
        self.assertEqual(v.drops_today, 0)
        self.assertEqual(presence(v, "terminal").state, D.UNKNOWN)
        self.assertIn("no log", presence(v, "terminal").evidence)
        self.assertEqual(presence(v, "tg_private").state, D.UNKNOWN)
        self.assertEqual(v.worst, D.GREEN)             # the settings read fine; nothing is known to be wrong

    def test_agent_with_no_voice(self):
        w = F.make_world()
        F.add_agent(w, "builder", addons=())
        v = F.agent(F.collect(w), "builder")
        self.assertIsNone(v.voice)
        self.assertEqual(v.name, "Builder")            # not in the voice list: the Team Room's name for it
        self.assertEqual(presence(v, "mac_voice").state, D.OFF)
        self.assertEqual(presence(v, "tg_group").state, D.OFF)
        self.assertEqual(presence(v, "team_room").state, D.ON)
        self.assertIn(("Takes work", "only as job cards"), v.talks)

    def test_simba_is_locked_and_answers_when_named(self):
        w = F.make_world()
        F.add_agent(w, "simba", model="x-ai/grok-4.20", service="openrouter", effort="none")
        F.log(w, "simba", F.turn(w.now - 60, "s1", "x-ai/grok-4.20", "openrouter", "bot_room"))
        v = F.agent(F.collect(w), "simba")
        self.assertTrue(v.model_locked)
        self.assertEqual(v.backups, ())
        self.assertEqual(v.last.status, D.GREEN)
        self.assertTrue(v.voice.english_only)
        self.assertIsNone(v.voice.chinese)
        self.assertIn(("Answers", "when named in the line"), v.talks)
        self.assertFalse(any(what == "Takes work from" for what, _ in v.talks))

    def test_presence_evidence(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        (w.paths.shortcuts / "Talk to Kylie.shortcut").write_text("x")
        F.log(w, "assistant", F.turn(w.now - 3 * 3600, "t1", "claude-opus-5-5", "anthropic", "telegram"),
              F.turn(w.now - 60, "c1", "claude-opus-5-5", "anthropic", "cli"))
        v = F.agent(F.collect(w), "assistant")
        self.assertEqual([p.key for p in v.presence], [k for k, _ in D.PLACES])
        self.assertEqual({p.key: p.state for p in v.presence},
                         {"terminal": D.ON, "tg_private": D.ON, "tg_group": D.ON, "team_room": D.ON,
                          "mac_voice": D.ON, "iphone_voice": D.ON})
        self.assertTrue(all(p.evidence for p in v.presence))

    def test_unknown_presence(self):
        w = F.make_world(members=None)                 # no Team Room members file at all
        F.add_agent(w, "assistant", addons=())
        F.log(w, "assistant", F.turn(w.now - 30 * 24 * 3600, "c1", "claude-opus-5-5", "anthropic", "telegram"))
        snap = F.collect(w)
        v = F.agent(snap, "assistant")
        self.assertEqual(presence(v, "team_room").state, D.UNKNOWN)
        self.assertEqual(presence(v, "tg_private").state, D.UNKNOWN)       # a month-old reply proves nothing
        self.assertEqual(presence(v, "iphone_voice").state, D.UNKNOWN)
        self.assertIn("could not be read", snap.room_note)

    def test_a_new_unknown_profile_becomes_a_row_by_itself(self):
        w = F.make_world()
        F.add_agent(w, "orchestrator", model="claude-sonnet-5-5")
        F.add_agent(w, "night_owl", model="some-new-model", service="brand-new")
        F.add_agent(w, "default")                                          # listed under ignore
        (w.paths.profiles / "empty_folder").mkdir()                        # no settings file: not an agent
        snap = F.collect(w)
        self.assertEqual([v.profile for v in snap.agents], ["orchestrator", "night_owl"])
        v = F.agent(snap, "night_owl")
        self.assertEqual(v.name, "Night Owl")
        self.assertEqual(v.model.model, "some-new-model")
        self.assertIn(("Takes work from", "Jarvis"), v.talks)              # the default rules apply

    def test_malformed_settings_file_is_an_error_on_that_agent_only(self):
        w = F.make_world()
        F.add_agent(w, "orchestrator", model="claude-sonnet-5-5")
        F.add_agent(w, "assistant", raw="{ this is : not [ valid")
        snap = F.collect(w)                                                # must not raise
        bad, good = F.agent(snap, "assistant"), F.agent(snap, "orchestrator")
        self.assertIn("could not be read", bad.error)
        self.assertIsNone(bad.model)
        self.assertEqual(bad.worst, D.RED)
        self.assertEqual(good.error, "")
        self.assertTrue(any(wn.status == D.RED and "Kylie" in wn.text for wn in snap.warnings))

    def test_settings_that_are_not_a_mapping_are_an_error_too(self):
        w = F.make_world()
        F.add_agent(w, "assistant", raw="[1, 2, 3]")
        self.assertTrue(F.agent(F.collect(w), "assistant").error)

    def test_dls_add_on_warning_and_other_assistants(self):
        w = F.make_world()
        F.add_agent(w, "orchestrator", model="claude-sonnet-5-5", addons=("dls-observer", "team-room-telegram"))
        snap = F.collect(w)
        self.assertTrue(any("DLS add-on is still switched on inside Jarvis" in wn.text for wn in snap.warnings))
        self.assertEqual([(o.name, o.english, o.call_names) for o in snap.others],
                         [("Claude", "af_heart", ("claude", "clod"))])
        self.assertIn("leads the Team Room", snap.room_note)

    def test_missing_rules_and_voice_list_are_reported_not_fatal(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        os.remove(w.paths.rules)
        os.remove(w.paths.agents_toml)
        snap = F.collect(w)
        self.assertEqual(len(snap.problems), 2)
        v = F.agent(snap, "assistant")
        self.assertEqual(v.name, "Assistant")          # falls back to the Team Room's name
        self.assertIsNone(v.voice)

    def test_one_shared_reader_reads_a_log_once_until_it_changes(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        F.add_agent(w, "orchestrator")
        F.log(w, "assistant", F.turn(w.now - 60, "s1", "claude-opus-5-5", "anthropic"))
        F.log(w, "orchestrator", F.turn(w.now - 60, "s2", "claude-opus-5-5", "anthropic"))
        reader = D.LogReader(w.paths.profiles)
        F.collect(w, reader)
        F.collect(w, reader)
        F.collect(w, reader)
        self.assertEqual(reader.reads, 2)              # one read per log, not one per refresh
        F.log(w, "assistant", F.turn(w.now - 30, "s1", "claude-opus-5-5", "anthropic", history=2))
        F.collect(w, reader)
        self.assertEqual(reader.reads, 3)

    def test_collect_writes_nothing(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        F.log(w, "assistant", F.turn(w.now - 60, "s1", "claude-opus-5-5", "anthropic"))
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in w.root.rglob("*") if p.is_file()}
        F.collect(w)
        after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in w.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class RulesFile(unittest.TestCase):
    """The real acd_rules.toml that ships with the desk."""

    def test_the_four_starting_rules(self):
        rules = D.load_rules(Path(D.__file__).resolve().parent / "acd_rules.toml")
        names = {"orchestrator": "Jarvis"}
        jarvis = dict(D.talk_lines("orchestrator", rules, names))
        kylie = dict(D.talk_lines("assistant", rules, names))
        builder = dict(D.talk_lines("builder", rules, names))
        simba = dict(D.talk_lines("simba", rules, names))
        for lines in (jarvis, kylie, builder, simba):
            self.assertEqual(lines["Speaks in the Team Room"], "yes, like everyone")
        self.assertEqual(jarvis["Hands out work"], "yes, the only one who does")
        self.assertEqual([kylie["Hands out work"], builder["Hands out work"], simba["Hands out work"]], ["no"] * 3)
        self.assertEqual(kylie["Takes work from"], "Jarvis")
        self.assertEqual(builder["Takes work"], "only as job cards")
        self.assertEqual(simba["Answers"], "when named in the line")
        self.assertTrue(D.rules_for(rules, "simba")["model_locked"])
        self.assertFalse(D.rules_for(rules, "assistant")["model_locked"])


if __name__ == "__main__":
    unittest.main()
