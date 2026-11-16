"""Offline tests of the Agent Control Desk, version four: login health and allowance per service, the watcher
("the desk is final"), the voices of Claude, Gemini and Codex, and the old Voice Desk grid's own switches.
Fakes and throw-away folders only: no live settings file, no real usage check, no Telegram, no window, no sound.

    .venv/bin/python -m unittest tests.test_acd_v4 -v
"""
import asyncio
import dataclasses
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_health as H  # noqa: E402
import acd_watch as W  # noqa: E402
import gridstate as G  # noqa: E402
from textual.widgets import OptionList, Static  # noqa: E402

AGY = F.AGY
NOW = F.NOW
WHEN = dt.datetime(2026, 11, 17, 10, 0, 0)
L = F.lines_loader


def cfg(w, profile="assistant") -> Path:
    return w.profiles / profile / "config.yaml"


def health(v, service):
    return next(h for h in v.health if h.service == service)


# ---- login health, from the log ----------------------------------------------------------------------------
class LoginHealth(unittest.TestCase):
    def events(self, *lines):
        return H.parse_events("\n".join(lines))

    def test_a_dead_sign_in_is_failing_and_only_peter_can_fix_it(self):
        ev = self.events(F.turn(NOW - 5000, "s1", "claude-opus-5-5", "anthropic"),      # it did answer, earlier
                         F.dead_token(NOW - 100),
                         F.signin_failed(NOW - 99, f"{AGY}/claude-opus-5-5"),
                         F.turn(NOW - 98, "s2", "claude-opus-5-5", AGY))
        h = H.judge("anthropic", ev, NOW)
        self.assertEqual((h.state, h.kind, h.needs_peter), (H.FAILING, H.SIGNIN_DEAD, True))
        self.assertEqual(h.answered, NOW - 5000)                # its last real answer is still known
        words = D.service_view(h, NOW, main=True).words
        self.assertIn("the sign-in is dead", words)
        self.assertIn("only Peter signing in again fixes it", words)
        self.assertEqual(H.judge(AGY, ev, NOW).state, H.GOOD)   # the backup that answered is fine

    def test_provider_not_configured_also_means_no_sign_in(self):
        ev = self.events(F.turn(NOW - 50, "s1", "gemini-3.8-flash", AGY), F.not_configured(NOW - 40, "s1"))
        self.assertEqual(H.judge("anthropic", ev, NOW).kind, H.SIGNIN_DEAD)

    def test_a_used_up_allowance_is_not_a_dead_sign_in_and_carries_its_reset_time(self):
        ev = self.events(F.turn(NOW - 60, "s1", "claude-opus-5-5", AGY),
                         F.quota(NOW - 55, "s1", "claude-opus-5-5", resets="86h46m59s"),
                         F.fallback(NOW - 54, "s1", "claude-opus-5-5", "gemini-3.8-flash", AGY))
        h = H.judge(AGY, ev, NOW)
        self.assertEqual((h.state, h.kind, h.model, h.needs_peter), (H.FAILING, H.ALLOWANCE_OUT, "claude-opus-5-5", False))
        self.assertAlmostEqual(h.reset_ts, NOW - 55 + 86 * 3600 + 46 * 60 + 59)
        self.assertEqual(H.hours_until(h.reset_ts, NOW), 87)
        words = D.service_view(h, NOW).words
        self.assertIn("the allowance for Claude Opus 5.5 is used up, resets in about 87 hours", words)
        self.assertIn("The sign-in itself works", words)
        self.assertIn("Gemini 3.8 Flash there still answered", words)

    def test_an_allowance_whose_reset_time_has_passed_or_that_answered_since_is_good_again(self):
        gone = self.events(F.turn(NOW - 7300, "s1", "claude-opus-5-5", AGY),
                           F.quota(NOW - 7250, "s1", "claude-opus-5-5", resets="1h0m0s"),
                           F.fallback(NOW - 7249, "s1", "claude-opus-5-5", "gemini-3.8-flash", AGY))
        self.assertEqual(H.judge(AGY, gone, NOW).state, H.GOOD)
        back = self.events(F.turn(NOW - 600, "s1", "claude-opus-5-5", AGY),
                           F.quota(NOW - 590, "s1", "claude-opus-5-5"),
                           F.fallback(NOW - 589, "s1", "claude-opus-5-5", "gemini-3.8-flash", AGY),
                           F.turn(NOW - 30, "s2", "claude-opus-5-5", AGY))
        self.assertEqual(H.judge(AGY, back, NOW).state, H.GOOD)

    def test_healthy_and_unknown(self):
        ev = self.events(F.turn(NOW - 30, "s1", "claude-opus-5-5", "anthropic"))
        good = H.judge("anthropic", ev, NOW)
        self.assertEqual((good.state, good.kind), (H.GOOD, ""))
        self.assertIn("working, Claude Opus 5.5 answered", D.service_view(good, NOW).words)
        nothing = H.judge("nvidia", ev, NOW)
        self.assertEqual(nothing.state, H.UNKNOWN)
        self.assertIn("unknown, the log holds no reply or failure for it", D.service_view(nothing, NOW).words)

    def test_a_sign_in_that_works_again_clears_the_failure(self):
        ev = self.events(F.dead_token(NOW - 500), F.turn(NOW - 20, "s1", "claude-opus-5-5", "anthropic"))
        self.assertEqual(H.judge("anthropic", ev, NOW).state, H.GOOD)

    def test_reset_text(self):
        self.assertEqual(H.reset_seconds("Resets in 86h46m59s."), 86 * 3600 + 46 * 60 + 59)
        self.assertEqual(H.reset_seconds("Resets in 5m."), 300)
        self.assertIsNone(H.reset_seconds("quota reached"))

    def test_the_desk_shows_it_per_agent_from_one_read_of_the_log(self):
        w = F.make_world()
        F.add_agent(w, "assistant", backups=[("claude-opus-5-5", AGY), ("nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia")])
        F.add_agent(w, "simba", model="x-ai/grok-4.20", service="openrouter", effort="none")
        F.log(w, "assistant", F.dead_token(w.now - 62), F.signin_failed(w.now - 61, f"{AGY}/claude-opus-5-5"),
              F.turn(w.now - 60, "k1", "claude-opus-5-5", AGY),
              F.quota(w.now - 59, "k1", "claude-opus-5-5"),
              F.fallback(w.now - 58, "k1", "claude-opus-5-5", "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia"))
        F.log(w, "simba", F.turn(w.now - 30, "s1", "x-ai/grok-4.20", "openrouter", "bot_room"))
        reader = D.LogReader(w.paths.profiles)
        snap = F.collect(w, reader)
        v = F.agent(snap, "assistant")
        self.assertEqual([h.service for h in v.health], ["anthropic", AGY, "nvidia"])
        self.assertEqual((health(v, "anthropic").kind, health(v, "anthropic").main), (H.SIGNIN_DEAD, True))
        self.assertEqual(health(v, AGY).kind, H.ALLOWANCE_OUT)
        self.assertEqual(health(v, "nvidia").state, H.GOOD)
        self.assertEqual(v.worst, D.RED)
        self.assertEqual(health(F.agent(snap, "simba"), "openrouter").state, H.GOOD)
        texts = [x.text for x in snap.warnings]
        self.assertTrue(any("could not sign in for 1 agent" in x and "only Peter signing in again fixes it" in x
                            for x in texts), texts)
        self.assertTrue(any(x.startswith("Antigravity: an allowance is used up for 1 agent") for x in texts), texts)
        self.assertEqual(reader.reads, 2, "one read per agent's log serves both the replies and the health")
        F.collect(w, reader)
        self.assertEqual(reader.reads, 2, "an unchanged log is not read again")
        tree = acd.tree_text(v).plain
        self.assertIn("Services it uses: sign-in and allowance", tree)
        self.assertIn("the sign-in is dead", tree)
        self.assertIn("allowance: unknown (not read yet)", tree)
        self.assertIn("sign-in dead", acd.cell_model_set(v).plain)
        self.assertIn("signed in, working", acd.cell_model_set(F.agent(snap, "simba")).plain)

    def test_an_agent_with_no_log_says_unknown(self):
        w = F.make_world()
        F.add_agent(w, "assistant")
        v = F.agent(F.collect(w), "assistant")
        self.assertEqual(health(v, "anthropic").state, H.UNKNOWN)
        self.assertIn("? sign-in unknown", acd.cell_model_set(v).plain)


# ---- the allowance reader (the DLS rules) --------------------------------------------------------------------
class AllowanceReader(unittest.TestCase):
    def test_a_fresh_reading_gives_percent_left_and_the_reset_time(self):
        doc = json.loads(F.usage_json(NOW - 60, [("Session", 0.0, NOW + 3600), ("Weekly", 75.0, NOW + 86400)]))
        a = H.parse_usage("openai-codex", doc, NOW)
        self.assertEqual([(w.label, w.left) for w in a.windows], [("Session", 100.0), ("Weekly", 25.0)])
        self.assertEqual(a.windows[1].resets_at, NOW + 86400)
        lines, why = D.allowance_lines(a)
        self.assertEqual(why, "")
        self.assertTrue(lines[1].startswith("Weekly: 25% left, resets "))

    def test_a_reading_older_than_15_minutes_is_unknown_never_full_or_empty(self):
        doc = json.loads(F.usage_json(NOW - 16 * 60, [("Weekly", 10.0, NOW + 86400)]))
        a = H.parse_usage("openai-codex", doc, NOW)
        self.assertEqual(a.windows, ())
        self.assertIn("more than 15 minutes old", a.problem)
        self.assertEqual(D.allowance_lines(a), ((), a.problem))

    def test_unusable_readings_are_unknown_and_twins_are_never_added_up(self):
        doc = json.loads(F.usage_json(NOW - 5, [("Gemini (Weekly)", 36.0, NOW + 9000), ("Gemini (Weekly)", 1.0, NOW + 99000),
                                                ("Broken", "lots", NOW + 9000), ("Over", 140, NOW + 9000),
                                                ("Past", 10.0, NOW - 5), ("No reset", 0.08, None)]))
        a = H.parse_usage(AGY, doc, NOW)
        by = {(w.label, i): w for i, w in enumerate(a.windows)}
        self.assertEqual((by[("Gemini (Weekly)", 0)].left, by[("Gemini (Weekly)", 1)].left), (64.0, 99.0))
        self.assertIn("never added together", by[("Gemini (Weekly)", 0)].note)
        for label, i in (("Broken", 2), ("Over", 3), ("Past", 4)):
            self.assertIsNone(by[(label, i)].left, label)
        self.assertAlmostEqual(by[("No reset", 5)].left, 99.92)
        self.assertIsNone(by[("No reset", 5)].resets_at)
        self.assertTrue(any("unknown" in ln for ln in D.allowance_lines(a)[0]))

    def test_a_document_with_no_time_zone_or_no_windows_is_unknown(self):
        self.assertIn("did not say when", H.parse_usage("x", {"fetched_at": "2026-11-16T12:00:00", "windows": []}, NOW).problem)
        doc = json.loads(F.usage_json(NOW - 5, []))
        self.assertIn("no allowance windows", H.parse_usage("x", doc, NOW).problem)
        self.assertIn("nothing readable", H.parse_usage("x", "junk", NOW).problem)
        self.assertEqual(H.parse_usage("x", {"unavailable_reason": "down"}, NOW).problem, "down")

    def test_the_shared_reader_asks_at_most_once_per_service_every_ten_minutes(self):
        clock = [NOW]
        asked = []

        def runner(cmd):
            asked.append(cmd)
            service = cmd[cmd.index("--provider") + 1]
            if service == "anthropic":
                return 0, "No account usage available for provider 'anthropic': no credential is configured for it"
            return 0, F.usage_json(clock[0] - 1, [("Weekly", 75.0, clock[0] + 86400)])

        r = H.UsageReader(runner=runner, clock=lambda: clock[0], hermes="hermes")
        self.assertEqual(r.cached(), {})                                  # nothing is ever run by a look
        want = {"anthropic": "assistant", "openai-codex": "wanna-buffet", "nvidia": "assistant"}
        self.assertEqual(r.refresh(want), 2)                              # NVIDIA has no usage check: never asked
        self.assertEqual(asked[1], ["hermes", "-p", "wanna-buffet", "usage", "--provider", "openai-codex", "--json"])
        got = r.cached()
        self.assertEqual(got["openai-codex"].windows[0].left, 25.0)
        self.assertIn("no sign-in for it", got["anthropic"].problem)
        self.assertEqual(r.refresh(want), 0)                              # too soon: not asked again
        clock[0] += 16 * 60
        self.assertIn("more than 15 minutes old", r.cached()["openai-codex"].problem)   # stale = unknown
        self.assertEqual(r.refresh(want), 2)
        self.assertEqual(r.calls, 4)

    def test_a_failed_check_leaves_unknown_and_no_runner_means_never_asked(self):
        r = H.UsageReader(runner=lambda cmd: (1, "boom"), clock=lambda: NOW)
        r.refresh({"openai-codex": "x"})
        self.assertIn("the usage check failed", r.cached()["openai-codex"].problem)
        self.assertEqual(H.UsageReader(runner=None).refresh({"openai-codex": "x"}), 0)

    def test_the_desk_shows_the_allowance_under_the_service(self):
        w = F.make_world()
        F.add_agent(w, "assistant", service="openai-codex", model="gpt-5.4-mini")
        F.log(w, "assistant", F.turn(w.now - 30, "k1", "gpt-5.4-mini", "openai-codex"))
        usage = {"openai-codex": H.parse_usage("openai-codex", json.loads(F.usage_json(
            w.now - 5, [("Weekly", 75.0, w.now + 86400)])), w.now)}
        snap = D.collect(w.paths, yaml_loader=F.loader, now=w.now, usage=usage)
        v = F.agent(snap, "assistant")
        self.assertTrue(health(v, "openai-codex").allowance[0].startswith("Weekly: 25% left, resets "))
        self.assertIn("allowance: Weekly: 25% left", acd.tree_text(v).plain)
        self.assertTrue(snap.allowance_read)


# ---- the watcher --------------------------------------------------------------------------------------------
def hand_edit(w, old: str, new: str, profile="assistant") -> None:
    path = cfg(w, profile)
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


class Watcher(unittest.TestCase):
    def setUp(self):
        self.w = F.edit_world(armed=True)
        r = E.change_model("assistant", "claude-sonnet-5-5", self.w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.t0 = WHEN.timestamp() + 600

    def drift(self):
        hand_edit(self.w, "default: claude-sonnet-5-5", "default: claude-haiku-5-5")

    def test_no_drift_says_so_and_writes_nothing(self):
        before = F.tree_hashes(self.w.root)
        rep = W.run_once(self.w.edit, act=True, now=self.t0, loader=L)
        self.assertEqual((rep.drifts, rep.put_back, rep.problems), ([], [], []))
        self.assertEqual(F.tree_hashes(self.w.root), before)
        self.assertIn("reports only", rep.mode)

    def test_drift_is_reported_with_before_after_and_when(self):
        self.drift()
        rep = W.run_once(self.w.edit, act=True, now=self.t0, loader=L)
        self.assertEqual(len(rep.drifts), 1)
        d = rep.drifts[0]
        self.assertEqual((d.profile, d.key, d.want, d.have), ("assistant", "model", "claude-sonnet-5-5", "claude-haiku-5-5"))
        self.assertEqual(d.first_seen, self.t0)
        self.assertIn("Kylie's language model: the desk set Claude Sonnet 5.5", d.sentence)
        self.assertIn("the settings file now says Claude Haiku 5.5", d.sentence)
        self.assertIn("The file was last written", d.sentence)
        self.assertEqual(rep.put_back, [])                              # reports only: automatic put-back is off
        self.assertIn("claude-haiku-5-5", cfg(self.w).read_text())
        self.assertTrue(any("Kylie's language model" in ln for ln in W.log_lines(self.w.edit)))   # one line in watch.log

    def test_not_armed_means_report_only_and_not_one_file_written(self):
        self.drift()
        W.set_auto(self.w.edit, True)
        cold = dataclasses.replace(self.w.edit, launcher_armed=False)      # the same folder, the desk not armed
        before = F.tree_hashes(self.w.root)
        rep = W.run_once(cold, act=True, now=self.t0, loader=L)
        self.assertEqual(len(rep.drifts), 1)
        self.assertIn("not armed, so it only reports", rep.mode)
        self.assertEqual(F.tree_hashes(self.w.root), before)
        d = rep.drifts[0]
        for res in (W.put_back(d, cold, loader=L, now=WHEN), W.accept(d, cold, now=WHEN)):
            self.assertTrue(res.ok and res.dry, res.message)
        self.assertEqual(F.tree_hashes(self.w.root), before)

    def test_put_it_back_writes_the_desks_value_again_through_the_safe_path(self):
        self.drift()
        d = W.run_once(self.w.edit, now=self.t0).drifts[0]
        res = W.put_back(d, self.w.edit, loader=L, now=dt.datetime.fromtimestamp(self.t0))
        self.assertTrue(res.ok and not res.dry, res.message)
        self.assertEqual(E.managed(cfg(self.w).read_text())["model"]["default"], "claude-sonnet-5-5")
        self.assertTrue(Path(res.entry["files"][0]["backup"]).is_file())
        self.assertEqual(W.run_once(self.w.edit, now=self.t0 + 5).drifts, [])

    def test_accept_makes_the_new_value_what_the_desk_expects_and_touches_no_agent_file(self):
        self.drift()
        d = W.run_once(self.w.edit, now=self.t0).drifts[0]
        text = cfg(self.w).read_text()
        res = W.accept(d, self.w.edit, now=dt.datetime.fromtimestamp(self.t0))
        self.assertTrue(res.ok and not res.dry, res.message)
        self.assertIn("accepted as the new setting", res.message)
        self.assertEqual(cfg(self.w).read_text(), text)
        self.assertEqual(W.run_once(self.w.edit, now=self.t0 + 5).drifts, [])
        self.assertEqual(E.read_history(self.w.edit)[-1]["kind"], "accept")

    def test_automatic_put_back_needs_its_switch_two_looks_and_then_repairs(self):
        self.drift()
        W.set_auto(self.w.edit, True)
        first = W.run_once(self.w.edit, act=True, now=self.t0, loader=L)
        self.assertIn("automatic put-back is ON", first.mode)
        self.assertEqual(first.put_back, [])
        self.assertIn("seen once", first.drifts[0].note)                    # a file being written is never fought
        self.assertIn("claude-haiku-5-5", cfg(self.w).read_text())
        soon = W.run_once(self.w.edit, act=True, now=self.t0 + 30, loader=L)
        self.assertEqual(soon.put_back, [])
        second = W.run_once(self.w.edit, act=True, now=self.t0 + W.CONFIRM_SEC + 1, loader=L)
        self.assertEqual(len(second.put_back), 1)
        self.assertIn("put back: Kylie's language model is Claude Sonnet 5.5 again", second.put_back[0])
        self.assertEqual(second.drifts, [])
        self.assertEqual(E.managed(cfg(self.w).read_text())["model"]["default"], "claude-sonnet-5-5")
        self.assertTrue(any("put back" in ln for ln in W.log_lines(self.w.edit)))

    def test_paused_looks_at_nothing_and_changes_nothing(self):
        self.drift()
        W.set_auto(self.w.edit, True)
        W.set_paused(self.w.edit, True)
        before = F.tree_hashes(self.w.root)
        rep = W.run_once(self.w.edit, act=True, now=self.t0 + 1000, loader=L)
        self.assertTrue(rep.paused)
        self.assertEqual((rep.drifts, rep.put_back), ([], []))
        self.assertIn("paused", rep.mode)
        self.assertEqual(F.tree_hashes(self.w.root), before)
        W.set_paused(self.w.edit, False)
        self.assertEqual(len(W.run_once(self.w.edit, now=self.t0 + 1000).drifts), 1)

    def test_a_temporary_choice_is_never_fought(self):
        self.drift()
        W.set_auto(self.w.edit, True)
        W.run_once(self.w.edit, act=True, now=self.t0, loader=L)
        rep = W.run_once(self.w.edit, act=True, now=self.t0 + 200, loader=L,
                         overrides={"assistant": "claude-haiku-5-5"})       # a chat is on that model right now
        self.assertEqual(rep.put_back, [])
        self.assertTrue(any("temporary choice" in x for x in rep.left_alone), rep.left_alone)
        self.assertIn("claude-haiku-5-5", cfg(self.w).read_text())
        self.assertIn("left alone", rep.drifts[0].note)

    def test_something_that_keeps_changing_back_is_reported_not_fought_forever(self):
        W.set_auto(self.w.edit, True)
        t = self.t0
        for i in range(W.FLAP_MAX):
            self.drift()
            W.run_once(self.w.edit, act=True, now=t, loader=L)
            rep = W.run_once(self.w.edit, act=True, now=t + W.CONFIRM_SEC + 1, loader=L)
            self.assertEqual(len(rep.put_back), 1, f"round {i}")
            t += 300
        self.drift()
        W.run_once(self.w.edit, act=True, now=t, loader=L)
        rep = W.run_once(self.w.edit, act=True, now=t + W.CONFIRM_SEC + 1, loader=L)
        self.assertEqual(rep.put_back, [])
        self.assertTrue(any("keeps changing back" in p for p in rep.problems), rep.problems)
        self.assertIn("NOT put back", rep.drifts[0].note)
        self.assertIn("claude-haiku-5-5", cfg(self.w).read_text())

    def test_effort_and_backups_are_watched_too(self):
        NV = ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")
        self.assertTrue(E.change_effort("assistant", "high", self.w.edit, loader=L, now=WHEN).ok)
        self.assertTrue(E.set_backup("assistant", *NV, False, self.w.edit, loader=L, now=WHEN).ok)
        hand_edit(self.w, "reasoning_effort: high", "reasoning_effort: low")
        text = cfg(self.w).read_text()
        cfg(self.w).write_text(text.replace("agent:\n", "  - provider: nvidia\n    model: nvidia/nemotron-3.5-lightning-30b-a3b\nagent:\n", 1))
        keys = sorted(d.key for d in W.run_once(self.w.edit, now=self.t0).drifts)
        self.assertEqual(keys, ["backup|nvidia|nvidia/nemotron-3.5-lightning-30b-a3b", "effort"])
        d = next(x for x in W.run_once(self.w.edit, now=self.t0).drifts if x.key.startswith("backup"))
        self.assertIn("backup NVIDIA Nemotron 3.5 Lightning", d.what)
        self.assertEqual((d.words(d.want), d.words(d.have)), ("off", "on"))

    def test_an_undo_is_simply_the_newest_change(self):
        self.assertTrue(E.undo(self.w.edit, loader=L, now=WHEN + dt.timedelta(seconds=5)).ok)
        self.assertEqual(W.run_once(self.w.edit, now=self.t0).drifts, [])

    def test_the_desk_shows_drift_red_on_the_agent_and_in_the_strip(self):
        self.drift()
        snap = D.collect(self.w.paths, yaml_loader=L, now=self.t0, desk=E.record(self.w.edit),
                         watch=W.view(self.w.edit, now=self.t0))
        v = F.agent(snap, "assistant")
        self.assertEqual(len(v.drifts), 1)
        self.assertEqual(v.worst, D.RED)
        self.assertTrue(any("changed behind the desk's back (Kylie)" in x.text for x in snap.warnings))
        self.assertIn("reports only", snap.watch_mode)
        rows = acd.tree_rows(v)
        self.assertTrue(any(s == "drift:0" for _, s in rows))
        self.assertIn("changed behind the desk", acd.cell_model_set(v).plain)

    def test_the_command_line(self):
        self.assertEqual(W.main(["nonsense"]), 2)
        self.assertEqual(W.expected([{"ok": True, "profile": "a", "kind": "model", "after": "m", "ts": 1},
                                     {"ok": True, "profile": "a", "kind": "accept", "key": "model", "after": "n", "ts": 2}]
                                    )[("a", "model")]["want"], "n")


# ---- Claude, Gemini and Codex: their voices -------------------------------------------------------------------
class AssistantsVoices(unittest.TestCase):
    def test_the_section_says_where_a_voice_change_lands(self):
        w = F.with_grid(F.edit_world())
        snap = F.edit_collect(w)
        by = {o.key: o for o in snap.others}
        self.assertEqual(sorted(by), ["claude", "codex", "gemini"])
        self.assertIn("Claude Code's own voice file", by["claude"].takes_effect)
        self.assertIn("Gemini's own voice file", by["gemini"].takes_effect)
        self.assertIn("the Voice Desk speaks for it", by["codex"].takes_effect)
        self.assertTrue(any(p.key == "terminal" and p.can_switch for p in by["claude"].voice_places))
        self.assertIn("press a to change their voices", acd.others_text(snap).plain)

    def test_claudes_voice_goes_through_the_voice_desk_path_into_its_own_voice_file(self):
        w = F.edit_world(armed=True)
        conf = w.edit.voice.claude_conf
        r = E.change_voice("claude", "voice", "en-US-AriaNeural", w.edit, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', conf.read_text())
        self.assertIn('voice = "en-US-AriaNeural"', w.toml.read_text())
        self.assertTrue(E.undo(w.edit, now=WHEN + dt.timedelta(seconds=3)).ok)
        self.assertIn('NEURAL_VOICE_EN="en-US-JennyNeural"', conf.read_text())

    def test_geminis_and_codexs_voice(self):
        w = F.edit_world(armed=True)
        r = E.change_voice("gemini", "voice", "en-US-AriaNeural", w.edit, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIn('NEURAL_VOICE_EN="en-US-AriaNeural"', w.edit.voice.gemini_conf.read_text())
        r = E.change_voice("codex", "voice", "en-GB-RyanNeural", w.edit, now=WHEN + dt.timedelta(seconds=2))
        self.assertTrue(r.ok and not r.dry, r.message)                  # the list is what the Voice Desk reads for Codex
        self.assertIn('voice = "en-GB-RyanNeural"', w.toml.read_text())

    def test_a_dry_run_of_an_assistants_voice_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        r = E.change_voice("claude", "voice_zh", "zh-TW-HsiaoYuNeural", w.edit, now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertTrue(any("HsiaoYu" in ln for ln in r.lines))
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_an_effect_is_refused_for_a_voice_that_has_none(self):
        w = F.edit_world(armed=True)
        r = E.change_voice("kylie", "effect", "16", w.edit, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("no voice effect", r.message)


# ---- the old Voice Desk grid's switches, carried over -----------------------------------------------------------
class VoicePlaces(unittest.TestCase):
    def test_every_place_is_shown_with_its_setting_and_what_can_be_switched(self):
        w = F.with_grid(F.edit_world())
        snap = F.edit_collect(w)
        v = F.agent(snap, "assistant")
        by = {p.key: p for p in v.voice_places}
        self.assertEqual(sorted(by), ["iphone", "mic", "terminal", "tg_dm", "tg_group"])
        self.assertTrue(by["tg_group"].can_switch and by["tg_dm"].can_switch and by["mic"].can_switch)
        self.assertFalse(by["terminal"].can_switch)                     # a Hermes window decides per window
        self.assertIn("each open Hermes window keeps its own choice", by["terminal"].why_not)
        self.assertFalse(by["iphone"].can_switch)
        self.assertEqual(by["tg_group"].value, "Always")
        rows = acd.tree_rows(v)
        settings = [s for _, s in rows if s and s.startswith("vplace:")]
        self.assertEqual(sorted(settings), ["vplace:mic", "vplace:tg_dm", "vplace:tg_group"])
        self.assertIn("Spoken replies, place by place", acd.tree_text(v).plain)

    def test_not_armed_every_switch_is_a_dry_run_that_writes_nothing(self):
        w = F.with_grid(F.edit_world())
        before = F.tree_hashes(w.root)
        results = [E.set_voice_place("kylie", "tg_group", G.OFF, w.edit, now=WHEN),
                   E.set_voice_place("kylie", "tg_dm", G.VOICE_BACK, w.edit, now=WHEN),
                   E.set_voice_place("claude", "terminal", G.OFF, w.edit, now=WHEN),
                   E.set_voice_place("gemini", "terminal", G.OFF, w.edit, now=WHEN),
                   E.set_voice_place("kylie", "mic", "queue:off", w.edit, now=WHEN)]
        for r in results:
            self.assertTrue(r.ok and r.dry, r.message)
            self.assertTrue(r.lines, r.message)
        self.assertEqual(F.tree_hashes(w.root), before)
        self.assertTrue(any("voice_notes.agents" in ln and "take out assistant" in ln for ln in results[0].lines))

    def test_team_group_voice_notes_switch_and_undo(self):
        w = F.with_grid(F.edit_world(armed=True))
        mirror = Path(w.grid.mirror_config)
        r = E.set_voice_place("kylie", "tg_group", G.OFF, w.edit, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertNotIn("assistant", json.loads(mirror.read_text())["voice_notes"]["agents"])
        self.assertEqual(G.tg_group_cell(next(a for a in __import__("agents").agents(__import__("agents").load(w.toml))
                                              if a.key == "kylie"), w.grid).value, G.OFF)
        self.assertEqual(E.read_history(w.edit)[-1]["kind"], "voice_place")
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=4))
        self.assertTrue(u.ok and not u.dry, u.message)
        self.assertIn("assistant", json.loads(mirror.read_text())["voice_notes"]["agents"])

    def test_claudes_terminal_voice_and_the_mic_queue_and_the_private_chat_and_gemini(self):
        w = F.with_grid(F.edit_world(armed=True))
        r = E.set_voice_place("claude", "terminal", G.OFF, w.edit, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIs(json.loads((Path(w.grid.claude_dir) / "settings.json").read_text())["voice"]["enabled"], False)
        self.assertIn("VOICE_ALWAYS_ON=0", w.edit.voice.claude_conf.read_text())
        r = E.set_voice_place("kylie", "mic", "queue:off", w.edit, now=WHEN + dt.timedelta(seconds=1))
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertTrue((Path(w.grid.state) / "speech/off").exists())
        r = E.set_voice_place("kylie", "tg_dm", G.VOICE_BACK, w.edit, now=WHEN + dt.timedelta(seconds=2))
        self.assertTrue(r.ok and not r.dry, r.message)
        modes = json.loads(Path(w.grid.voice_mode_file).read_text())
        self.assertEqual(modes[f"assistant:telegram:{w.grid.tg_dm_chat}"], "voice_only")
        r = E.set_voice_place("gemini", "terminal", G.OFF, w.edit, now=WHEN + dt.timedelta(seconds=3))
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertTrue(Path(w.grid.gemini_switch).exists())

    def test_a_place_with_no_switch_is_refused(self):
        w = F.with_grid(F.edit_world(armed=True))
        r = E.set_voice_place("kylie", "iphone", G.OFF, w.edit, now=WHEN)
        self.assertFalse(r.ok)
        r = E.set_voice_place("kylie", "terminal", G.OFF, w.edit, now=WHEN)
        self.assertFalse(r.ok)
        self.assertEqual(E.read_history(w.edit), [])

    def test_the_health_strip_is_shown_and_a_red_light_becomes_a_warning(self):
        w = F.with_grid(F.edit_world())
        Light = type("Light", (), {})
        def lights(_grid):
            a, b = Light(), Light()
            a.ok, a.label, a.detail = True, "voice engine OK", ""
            b.ok, b.label, b.detail = False, "Hermes gateway down", "no process"
            return [a, b]
        w.paths = dataclasses.replace(w.paths, lights=lights)
        snap = F.edit_collect(w)
        self.assertEqual([x[1] for x in snap.lights], ["voice engine OK", "Hermes gateway down"])
        self.assertTrue(any(x.text.startswith("voice system: Hermes gateway down") for x in snap.warnings))
        self.assertIn("voice engine OK", acd.systems_text(snap).plain)


# ---- the screen --------------------------------------------------------------------------------------------------
def make_app(w, **more):
    return acd.Desk(collector=lambda: D.collect(w.paths, yaml_loader=L, now=w.now, desk=E.record(w.edit),
                                                watch=W.view(w.edit, now=w.now)),
                    autostart=False, edit=w.edit, loader=L, tester=None, **more)


async def show(app, pilot) -> None:
    app.apply_snapshot(await asyncio.to_thread(app.collector))
    await pilot.pause()


async def open_setting(app, pilot, index: int, setting: str) -> None:
    app.open_tree(index)
    await pilot.pause()
    app.screen.query_one("#tree", OptionList).highlighted = app.screen.settings.index(setting)
    await pilot.press("enter")
    await pilot.pause()


def popup_text(app) -> str:
    return "\n".join(str(app.screen.query_one(f"#{i}", Static).render()) for i in ("box-title", "box-note", "box-confirm"))


class Screen(unittest.IsolatedAsyncioTestCase):
    async def test_p_on_a_desk_that_is_not_armed_writes_nothing(self):
        w = F.with_grid(F.edit_world())
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(190, 64)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            self.assertIn("watcher: reports only", str(app.screen.query_one("#systems", Static).render()))
            await pilot.press("p")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.NoticePopup)
            self.assertIn("DRY RUN, nothing written", str(app.screen.query_one("#box-text", Static).render()))
        self.assertFalse(W.is_paused(w.edit))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_p_pauses_and_resumes_both_watchers_on_an_armed_desk_and_the_strip_says_so(self):
        w = F.with_grid(F.edit_world(armed=True))
        app = make_app(w, watching=False)
        async with app.run_test(size=(190, 64)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("p")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.NoticePopup)
            self.assertTrue(W.is_paused(w.edit))
            self.assertTrue((Path(w.grid.state) / "watcher_paused").exists())       # the Voice Desk's own watcher too
            await pilot.press("escape")
            await show(app, pilot)
            self.assertIn("paused", str(app.screen.query_one("#systems", Static).render()))
            self.assertIn("the watcher is paused (p resumes)", str(app.screen.query_one("#warnings", Static).render()))
            await pilot.press("p")
            await pilot.pause()
            self.assertFalse(W.is_paused(w.edit))
            self.assertFalse((Path(w.grid.state) / "watcher_paused").exists())

    async def test_the_drift_pop_up_offers_put_it_back_and_accept_it(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN).ok)
        hand_edit(w, "default: claude-sonnet-5-5", "default: claude-haiku-5-5")
        w.now = WHEN.timestamp() + 600
        app = make_app(w, watching=False)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_setting(app, pilot, 1, "drift:0")
            self.assertIsInstance(app.screen, acd.EditPopup)
            words = popup_text(app)
            self.assertIn("was changed behind the desk's back", words)
            labels = [o.label for g in app.screen.groups for o in g.options]
            self.assertTrue(labels[0].startswith("put it back: Claude Sonnet 5.5 again"))
            self.assertTrue(labels[1].startswith("accept it: keep Claude Haiku 5.5"))
            await pilot.press("s")                                   # nothing chosen yet: nothing to save
            await pilot.pause()
            self.assertIn("nothing to save", str(app.screen.query_one("#box-status", Static).render()))
            await pilot.press("enter")                               # choose "put it back"
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("is put back to Claude Sonnet 5.5", popup_text(app))
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(E.managed(cfg(w).read_text())["model"]["default"], "claude-sonnet-5-5")
            self.assertTrue(app.last_results[0].ok and not app.last_results[0].dry)

    async def test_accept_from_the_pop_up_in_a_dry_run_changes_nothing(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN).ok)
        hand_edit(w, "default: claude-sonnet-5-5", "default: claude-haiku-5-5")
        w.edit = dataclasses.replace(w.edit, launcher_armed=False)
        w.now = WHEN.timestamp() + 600
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_setting(app, pilot, 1, "drift:0")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("DRY RUN", popup_text(app))
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertTrue(app.last_results[0].dry)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_a_opens_the_assistants_and_a_voice_pop_up_for_the_one_picked(self):
        w = F.with_grid(F.edit_world())
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(190, 64)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.PickPopup)
            tokens = [t for t, _ in app.screen.rows if t]
            for want in ("claude|voice", "claude|voice_zh", "gemini|voice", "codex|voice", "claude|vplace:terminal",
                         "gemini|vplace:terminal"):
                self.assertIn(want, tokens)
            lst = app.screen.query_one("#box-list", OptionList)
            lst.highlighted = [t for t, _ in app.screen.rows].index("claude|voice")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.EditPopup)
            self.assertIn("Claude: voice", popup_text(app))
            self.assertIn("Claude Code's own voice file", popup_text(app))
            options = [o.token for g in app.screen.groups for o in g.options]
            self.assertIn("en-US-AriaNeural", options)
            app.screen.query_one("#box-list", OptionList).highlighted = \
                [o.token if o else None for _, o in app.screen.rows].index("en-US-AriaNeural")
            await pilot.press("enter")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("Claude's English voice changes from Jenny to Aria", popup_text(app))
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertTrue(app.last_results[0].ok and app.last_results[0].dry)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_a_voice_place_pop_up_shows_the_grids_own_choices_and_saves_through_its_code(self):
        w = F.with_grid(F.edit_world(armed=True))
        app = make_app(w, watching=False)
        async with app.run_test(size=(190, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_setting(app, pilot, 1, "vplace:tg_group")
            self.assertIsInstance(app.screen, acd.EditPopup)
            self.assertIn("same switch the Voice Desk dashboard had", popup_text(app))
            options = [o.token for g in app.screen.groups for o in g.options]
            self.assertEqual(options, [G.OFF, G.ALWAYS])
            app.screen.query_one("#box-list", OptionList).highlighted = 1      # Off
            await pilot.press("enter")
            await pilot.press("s")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertTrue(app.last_results[0].ok and not app.last_results[0].dry, app.last_results[0].message)
            self.assertNotIn("assistant", json.loads(Path(w.grid.mirror_config).read_text())["voice_notes"]["agents"])

    async def test_the_usage_check_is_never_run_by_a_desk_that_was_given_none(self):
        w = F.edit_world()
        app = make_app(w)
        self.assertIsNone(app.usage)
        calls = []
        reader = H.UsageReader(runner=lambda cmd: calls.append(cmd) or (1, ""), clock=lambda: NOW)
        app2 = make_app(w, usage=reader)
        async with app2.run_test(size=(190, 64)) as pilot:
            await pilot.pause()
            await show(app2, pilot)                                 # autostart off: the check is not started
            await app2.workers.wait_for_complete()
        self.assertEqual(calls, [])


class WatchStep(unittest.TestCase):
    def test_the_desks_own_timer_step_never_runs_on_an_unarmed_desk(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN).ok)
        hand_edit(w, "default: claude-sonnet-5-5", "default: claude-haiku-5-5")
        W.set_auto(w.edit, True)
        cold = dataclasses.replace(w.edit, launcher_armed=False)
        before = F.tree_hashes(w.root)
        app = acd.Desk(collector=lambda: None, autostart=False, edit=cold, loader=L, tester=None)
        app.watch_step()
        self.assertEqual(F.tree_hashes(w.root), before)
        hot = acd.Desk(collector=lambda: None, autostart=False, edit=w.edit, loader=L, tester=None)
        hot.watch_step()                                            # armed: it notes the drift (first look)
        self.assertIn("claude-haiku-5-5", cfg(w).read_text())       # ... and does not put it back on one look
        self.assertEqual(len(hot.last_watch.drifts), 1)


if __name__ == "__main__":
    unittest.main()
