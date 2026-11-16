"""Offline tests of the Agent Control Desk's changes (acd_edit.py): every write goes to a throw-away folder.

    .venv/bin/python -m unittest tests.test_acd_edit -v
"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import voicecfg  # noqa: E402

NV = ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")
AGY = F.AGY
WHEN = dt.datetime(2026, 11, 17, 10, 0, 0)
L = F.lines_loader


def cfg(w, profile="assistant") -> Path:
    return w.profiles / profile / "config.yaml"


class TextTransforms(unittest.TestCase):
    def test_scalar_changes_one_line_and_keeps_every_comment(self):
        old = F.config_yaml("claude-opus-5-5", "anthropic", "medium")
        new = E.set_scalar(old, "model.default", "claude-sonnet-5-5", "model was claude-opus-5-5", "2026-11-17")
        self.assertEqual(E.diff_lines(old, new), [
            "- default: claude-opus-5-5   # the main model",
            "+ # 2026-11-17 Agent Control Desk: model was claude-opus-5-5",
            "+ default: claude-sonnet-5-5   # the main model"])
        again = E.set_scalar(new, "model.default", "claude-haiku-5-5", "model was claude-sonnet-5-5", "2026-11-18")
        self.assertEqual(again.count("Agent Control Desk"), 1, "the desk's own older note is replaced, not piled up")
        for keep in ("# a comment that must stay", "# first choice", "# last resort"):
            self.assertIn(keep, again)

    def test_backup_off_then_on_puts_the_same_lines_back_in_the_same_place(self):
        old = F.config_yaml("claude-opus-5-5", "anthropic", "medium")
        off, raw, position = E.remove_backup(old, AGY, "gemini-3.8-flash", "2026-11-17")
        self.assertEqual(position, 2)
        self.assertEqual([i.model for i in E.read_block(off).items],
                         ["claude-opus-5-5", "nvidia/nemotron-3.5-lightning-30b-a3b"])
        self.assertIn("backup switched off (the desk remembers it): gemini-3.8-flash", off)
        on = E.insert_backup(off, AGY, "gemini-3.8-flash", raw, position, "2026-11-18")
        self.assertEqual([i.model for i in E.read_block(on).items],
                         ["claude-opus-5-5", "gemini-3.8-flash", "nvidia/nemotron-3.5-lightning-30b-a3b"])
        self.assertNotIn("switched off", on)
        self.assertEqual(E.diff_lines(old, on), ["+ # 2026-11-18 Agent Control Desk: backup switched on: "
                                                 f"gemini-3.8-flash via {AGY}"])
        self.assertIn("# last resort", on)

    def test_last_backup_off_leaves_an_empty_list_and_first_on_rebuilds_it(self):
        text = "model:\n  default: m\nfallback_providers:\n  - provider: nvidia\n    model: x\nagent:\n  reasoning_effort: low\n"
        off, raw, _ = E.remove_backup(text, "nvidia", "x", "2026-11-17")
        self.assertIn("fallback_providers: []", off)
        self.assertEqual(E.managed(off)["fallback_providers"], [])
        on = E.insert_backup(off, "nvidia", "x", raw, 1, "2026-11-17")
        self.assertEqual(E.managed(on)["fallback_providers"], [{"provider": "nvidia", "model": "x"}])
        self.assertEqual(E.managed(on)["agent"]["reasoning_effort"], "low")

    def test_shapes_the_desk_does_not_know_are_refused(self):
        for bad in ("fallback_providers: [a, b]\n", "x: 1\n", "fallback_providers:\n  - provider: nvidia\n",
                    "fallback_providers:\n  - provider: a\n    model: b\n    more:\n      - deep\n"):
            with self.assertRaises(E.EditError, msg=bad):
                E.read_block(bad)
        with self.assertRaises(E.EditError):
            E.set_scalar("model: x\n", "model.default", "y", "w", "2026-11-17")


class DryRun(unittest.TestCase):
    def test_not_armed_writes_nothing_at_all(self):
        for armed, launcher in ((False, True), (True, False), (False, False)):
            w = F.edit_world(armed=armed, launcher=launcher)
            before = F.tree_hashes(w.root)
            results = [E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN),
                       E.change_effort("assistant", "high", w.edit, loader=L, now=WHEN),
                       E.set_backup("assistant", *NV, False, w.edit, loader=L, now=WHEN),
                       E.set_backup("tax_angel_esther", "openai-codex", "gpt-5.4-mini", True, w.edit, loader=L, now=WHEN),
                       E.change_voice("kylie", "voice", "en-US-AriaNeural", w.edit, now=WHEN),
                       E.change_voice("kylie", "speed", 1.2, w.edit, now=WHEN),
                       E.request_restart(w.edit, "test", now=WHEN),
                       E.undo(w.edit, loader=L)]
            self.assertEqual(F.tree_hashes(w.root), before, f"a dry run wrote something (armed={armed}, launcher={launcher})")
            for r in results[:7]:
                self.assertTrue(r.ok and r.dry, r.message)
                self.assertIn("DRY RUN, nothing written", r.message)
                self.assertTrue(r.lines, "a dry run shows the exact lines")
            self.assertFalse(results[7].ok)                    # nothing to undo: nothing was ever written
            self.assertIn("- default: claude-opus-5-5   # the main model", results[0].lines)
            self.assertTrue(any("en-US-AriaNeural" in ln for ln in results[4].lines))
            self.assertTrue(any("Voice Desk list" in ln for ln in results[4].lines))

    def test_why_not_armed_names_what_is_missing(self):
        self.assertIn("not started with --armed", E.why_not_armed(F.edit_world(armed=True, launcher=False).edit))
        self.assertIn("armed switch is off", E.why_not_armed(F.edit_world(armed=False, launcher=True).edit))
        self.assertEqual(E.why_not_armed(F.edit_world(armed=True).edit), "")


class ArmedWrites(unittest.TestCase):
    def setUp(self):
        self.w = F.edit_world(armed=True)

    def test_model_change_backs_up_writes_reads_back_and_records(self):
        w = self.w
        old = cfg(w).read_text()
        r = E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(r.message, "Saved: Kylie's model changes from Claude Opus 5.5 to Claude Sonnet 5.5")
        new = cfg(w).read_text()
        self.assertEqual(E.managed(new)["model"]["default"], "claude-sonnet-5-5")
        self.assertEqual(len(E.diff_lines(old, new)), 3, "one line changed, one dated note added")
        self.assertIn("2026-11-17 Agent Control Desk: model was claude-opus-5-5", new)
        backup = Path(r.entry["files"][0]["backup"])
        self.assertEqual(backup.read_text(), old)
        hist = E.read_history(w.edit)
        self.assertEqual((hist[-1]["who"], hist[-1]["before"], hist[-1]["after"]),
                         ("Agent Control Desk", "claude-opus-5-5", "claude-sonnet-5-5"))
        self.assertIn("No restart needed", r.needs)

    def test_a_change_that_does_not_read_back_is_put_back_at_once(self):
        w = self.w
        old = cfg(w).read_text()
        calls = []

        def liar(path):                                   # reads fine before the write, then reports the old value
            calls.append(1)
            data = L(path)
            if len(calls) > 1:
                data["model"]["default"] = "claude-opus-5-5"
            return data
        r = E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=liar, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("put back exactly as it was", r.message)
        self.assertEqual(cfg(w).read_text(), old)
        self.assertEqual(E.read_history(w.edit), [])

    def test_a_file_hermes_cannot_read_is_refused_untouched(self):
        w = self.w
        cfg(w).write_text(cfg(w).read_text() + "# BROKEN\n")
        old = cfg(w).read_text()
        for r in (E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L),
                  E.set_backup("assistant", *NV, False, w.edit, loader=L),
                  E.change_effort("assistant", "high", w.edit, loader=L)):
            self.assertFalse(r.ok)
            self.assertIn("nothing changed", r.message)
        self.assertEqual(cfg(w).read_text(), old)
        cfg(w, "wanna-buffet").write_text("fallback_providers: [a]\nmodel:\n  default: m\n  provider: anthropic\n")
        r = E.set_backup("wanna-buffet", *NV, True, w.edit, loader=L)
        self.assertFalse(r.ok)
        self.assertIn("does not know", r.message)

    def test_a_model_the_service_does_not_offer_is_refused(self):
        r = E.change_model("assistant", "gpt-99", self.w.edit, loader=L)
        self.assertFalse(r.ok)
        self.assertIn("not a model", r.message)
        self.assertFalse(E.change_effort("assistant", "enormous", self.w.edit, loader=L).ok)

    def test_backup_off_is_remembered_and_on_restores_it_and_undo_works(self):
        w = self.w
        old = cfg(w).read_text()
        off = E.set_backup("assistant", *NV, False, w.edit, loader=L, now=WHEN)
        self.assertTrue(off.ok and not off.dry, off.message)
        self.assertEqual([d["model"] for d in E.managed(cfg(w).read_text())["fallback_providers"]],
                         ["claude-opus-5-5", "gemini-3.8-flash"])
        rec = E.read_off(w.edit)["assistant"]
        self.assertEqual((rec[0]["provider"], rec[0]["model"], rec[0]["position"]), (*NV, 3))
        self.assertIn("# last resort", "".join(rec[0]["raw"]))
        desk = E.record(w.edit)
        self.assertTrue(any(r["model"] == NV[1] and not r.get("offered") for r in desk.off["assistant"]))
        self.assertEqual(desk.last_change, off.entry["what"])

        back = E.undo(w.edit, loader=L, now=WHEN + dt.timedelta(minutes=1))
        self.assertTrue(back.ok, back.message)
        self.assertTrue(back.message.startswith("Undone: "))
        now = cfg(w).read_text()
        self.assertEqual(E.managed(now), E.managed(old))
        self.assertIn("# last resort", now)
        self.assertEqual(E.read_off(w.edit)["assistant"], [])
        self.assertIsNone(E.last_undoable(w.edit), "an undone change is not offered for undo again")
        self.assertEqual(len(E.read_history(w.edit)), 2)

    def test_the_seed_shows_jarvis_nvidia_as_off_and_nvidia_is_a_choice_where_it_is_missing(self):
        w = F.edit_world()
        text = cfg(w, "orchestrator").read_text()
        cfg(w, "orchestrator").write_text(E.remove_backup(text, *NV, "2026-11-13")[0])     # as it is on the real Mac
        cfg(w, "tax_angel_esther").write_text(E.remove_backup(cfg(w, "tax_angel_esther").read_text(), *NV, "2026-11-13")[0])
        desk = E.record(w.edit)
        jarvis = desk.off["orchestrator"]
        self.assertEqual([(r["provider"], r["model"], r["note"]) for r in jarvis], [(*NV, "switched off 13 Nov")])
        esther = desk.off["tax_angel_esther"]
        self.assertTrue(esther[0].get("offered") and esther[0]["provider"] == "nvidia")
        self.assertEqual(desk.off["assistant"], [], "NVIDIA is listed (on) for Kylie, so nothing is offered or off")
        snap = F.edit_collect(w)
        shown = {v.profile: [(b.model, b.on) for b in v.backups] for v in snap.agents}
        self.assertIn((NV[1], False), shown["orchestrator"])
        self.assertIn((NV[1], True), shown["assistant"])
        self.assertEqual(shown["simba"], [], "Simba has no backups by design; nothing is offered")

        w2 = F.edit_world(armed=True)
        cfg(w2, "orchestrator").write_text(E.remove_backup(cfg(w2, "orchestrator").read_text(), *NV, "2026-11-13")[0])
        on = E.set_backup("orchestrator", *NV, True, w2.edit, loader=L, now=WHEN)
        self.assertTrue(on.ok, on.message)
        self.assertEqual(E.managed(cfg(w2, "orchestrator").read_text())["fallback_providers"][-1],
                         {"provider": NV[0], "model": NV[1]})
        self.assertEqual(E.record(w2.edit).off["orchestrator"], [], "once switched on it is no longer shown as off")

    def test_effort_and_undo(self):
        w = self.w
        r = E.change_effort("assistant", "high", w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(E.managed(cfg(w).read_text())["agent"]["reasoning_effort"], "high")
        self.assertTrue(E.undo(w.edit, loader=L).ok)
        self.assertEqual(E.managed(cfg(w).read_text())["agent"]["reasoning_effort"], "medium")

    def test_simba_is_locked(self):
        for r in (E.change_model("simba", "claude-opus-5-5", self.w.edit, loader=L),
                  E.change_effort("simba", "high", self.w.edit, loader=L),
                  E.set_backup("simba", *NV, True, self.w.edit, loader=L)):
            self.assertFalse(r.ok)
        self.assertIn("locked", E.change_model("simba", "claude-opus-5-5", self.w.edit, loader=L).message)
        self.assertIn("fallback_providers: []", cfg(self.w, "simba").read_text())

    def test_the_file_changing_under_the_edit_is_noticed(self):
        w = self.w
        seen = []

        def racing(path):
            if not seen:
                seen.append(1)
                data = L(path)
                Path(path).write_text(Path(path).read_text() + "# someone else edited this\n")
                return data
            return L(path)
        r = E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=racing, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("changed while the edit was prepared; nothing written", r.message)
        now = cfg(w).read_text()
        self.assertIn("# someone else edited this", now)                 # the other edit is kept
        self.assertEqual(E.managed(now)["model"]["default"], "claude-opus-5-5")
        self.assertEqual(E.read_history(w.edit), [])

    def test_restart_request_goes_only_to_the_helper_file(self):
        w = self.w
        r = E.request_restart(w.edit, "load an add-on change", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        body = json.loads(w.edit.restart_request.read_text())
        self.assertEqual((body["who"], body["reason"]), ("agent-control-desk", "load an add-on change"))
        self.assertIn("requested_at", body)
        self.assertIn("waits for a quiet moment", r.message)
        again = E.request_restart(w.edit, "again", now=WHEN)
        self.assertIn("already waiting", again.message)
        self.assertEqual(json.loads(w.edit.restart_request.read_text())["reason"], "load an add-on change")


class Voice(unittest.TestCase):
    def test_voice_change_goes_through_the_voice_desk_path_and_undo_restores_it(self):
        w = F.edit_world(armed=True)
        before_toml, before_cfg = w.toml.read_text(), cfg(w).read_text()
        r = E.change_voice("kylie", "voice", "en-US-AriaNeural", w.edit, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(r.message, "Saved: Kylie's English voice changes from Ava to Aria")
        self.assertIn('voice = "en-US-AriaNeural"', w.toml.read_text())
        self.assertEqual(cfg(w).read_text().count("en-US-AriaNeural"), 2)
        self.assertEqual(E.read_history(w.edit)[-1]["kind"], "voice")
        back = E.undo(w.edit, now=WHEN + dt.timedelta(minutes=1))
        self.assertTrue(back.ok, back.message)
        self.assertEqual(w.toml.read_text(), before_toml)
        self.assertEqual(cfg(w).read_text(), before_cfg)

    def test_speed_is_a_preset_never_a_typed_number(self):
        w = F.edit_world(armed=True)
        self.assertFalse(E.change_voice("kylie", "speed", 1.37, w.edit).ok)
        r = E.change_voice("kylie", "speed", 1.2, w.edit, now=WHEN)
        self.assertTrue(r.ok, r.message)
        self.assertIn("from normal to fast", r.message)
        self.assertIn("speed = 1.2", w.toml.read_text())
        self.assertIn("speed: 1.2", cfg(w).read_text())
        self.assertTrue(E.undo(w.edit).ok)
        self.assertIn("speed: 1.0", cfg(w).read_text())

    def test_a_short_list_without_a_typing_box(self):
        import agents as A
        w = F.edit_world()
        toml = A.load(w.toml)
        kylie = next(a for a in A.agents(toml) if a.key == "kylie")
        english = E.voice_options(kylie, "voice", toml)
        tokens = [t for t, *_ in english]
        self.assertIn("", tokens, "silent is a choice")
        self.assertNotIn("::other", tokens)
        self.assertTrue(any(cur for _, _, cur, _ in english))
        self.assertFalse([t for t in tokens if t.startswith("en-AU-")], "only the local, American and British voices")
        self.assertTrue(all(t == "" or t.startswith("zh-TW-") for t, *_ in E.voice_options(kylie, "voice_zh", toml)))

    def test_refused_voice_changes_nothing(self):
        w = F.edit_world(armed=True)
        before = F.tree_hashes(w.root)
        self.assertFalse(E.change_voice("nobody", "voice", "en-US-AriaNeural", w.edit).ok)
        self.assertFalse(E.change_voice("kylie", "effect", "1", w.edit).ok)
        after = F.tree_hashes(w.root)
        self.assertEqual({k: v for k, v in after.items() if k in before}, before)


class StatusAfterAChange(unittest.TestCase):
    def test_yellow_until_a_reply_on_the_new_model_then_green_and_red_if_changed_elsewhere(self):
        w = F.edit_world(armed=True)
        when = dt.datetime.fromtimestamp(w.now - 600)
        self.assertTrue(E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=when).ok)
        F.log(w, "assistant", F.turn(w.now - 900, "old", "claude-opus-5-5", "anthropic"))
        v = F.agent(F.edit_collect(w), "assistant")
        self.assertEqual(v.model_status, D.YELLOW)
        self.assertIn("open terminal chats keep the old model until a fresh chat is started", v.model_pending)
        self.assertIn("expected", v.model_pending)
        snap = F.edit_collect(w)
        self.assertTrue(any("not live everywhere yet for: Kylie" in x.text for x in snap.warnings))

        F.log(w, "assistant", F.turn(w.now - 60, "new", "claude-sonnet-5-5", "anthropic"))
        v = F.agent(F.edit_collect(w), "assistant")
        self.assertEqual((v.model_status, v.model_pending), (D.GREEN, ""))

        text = cfg(w).read_text().replace("default: claude-sonnet-5-5", "default: claude-haiku-5-5")
        cfg(w).write_text(text)                           # someone changed it by hand afterwards
        v = F.agent(F.edit_collect(w), "assistant")
        self.assertEqual(v.model_status, D.RED)
        self.assertIn("changed somewhere else", v.model_behind)

    def test_an_undone_model_change_is_not_pending(self):
        w = F.edit_world(armed=True)
        E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=dt.datetime.fromtimestamp(w.now - 600))
        E.undo(w.edit, loader=L, now=dt.datetime.fromtimestamp(w.now - 500))
        self.assertNotIn("assistant", {k for k, v in E.record(w.edit).pending.items() if v["model"] == "claude-sonnet-5-5"})

    def test_changes_that_need_a_restart_wait_until_one_was_requested_and_done(self):
        w = F.edit_world(armed=True)
        E._append_history(w.edit, {"id": "a", "ts": w.now - 100, "kind": "addon", "what": "an add-on was switched off",
                                   "ok": True, "needs_restart": True})
        self.assertEqual(E.record(w.edit).waiting, ("an add-on was switched off",))
        snap = F.edit_collect(w)
        self.assertTrue(any("1 saved change is waiting for a restart: press r" in x.text for x in snap.warnings))
        E.request_restart(w.edit, "x", now=dt.datetime.fromtimestamp(w.now - 50))
        self.assertEqual(len(E.record(w.edit).waiting), 1, "still waiting: the helper has not restarted yet")
        w.edit.restart_request.unlink()                   # the helper removes the request once it restarted
        self.assertEqual(E.record(w.edit).waiting, ())

    def test_telegram_private_chat_from_the_agents_own_status(self):
        w = F.edit_world()
        snap = F.edit_collect(w)
        p = {v.profile: next(x for x in v.presence if x.key == "tg_private") for v in snap.agents}
        self.assertEqual(p["assistant"].state, D.ON)
        self.assertIn("own status says", p["assistant"].evidence)
        self.assertEqual(p["tax_angel_esther"].state, D.OFF)
        (w.root / "gateway_state.json").unlink()
        p = {v.profile: next(x for x in v.presence if x.key == "tg_private") for v in F.edit_collect(w).agents}
        self.assertEqual(p["assistant"].state, D.UNKNOWN, "no status file and no log line: unknown, never a guess")


@unittest.skipUnless(voicecfg.HERMES_PY.exists(), "Hermes is not installed on this machine")
class RealHermesReader(unittest.TestCase):
    def test_changes_read_back_through_hermes_own_reader(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.change_model("assistant", "claude-sonnet-5-5", w.edit, now=WHEN).ok)
        self.assertTrue(E.set_backup("assistant", *NV, False, w.edit, now=WHEN).ok)
        self.assertTrue(E.set_backup("assistant", "openai-codex", "gpt-5.4-mini", True, w.edit, now=WHEN).ok)
        data = voicecfg.hermes_readback(cfg(w))
        self.assertEqual(data["model"]["default"], "claude-sonnet-5-5")
        self.assertEqual([d["model"] for d in data["fallback_providers"]],
                         ["claude-opus-5-5", "gemini-3.8-flash", "gpt-5.4-mini"])
        self.assertEqual(data["tts"]["provider"], "natural", "the rest of the file still reads")


class ArmedSwitchCommand(unittest.TestCase):
    def test_arm_and_disarm_only_touch_the_desks_own_file(self):
        w = F.edit_world()
        E.set_armed(w.edit, True)
        self.assertTrue(E.armed(w.edit))
        E.set_armed(w.edit, False)
        self.assertFalse(E.armed(w.edit))
        self.assertEqual(sorted(p.name for p in w.edit.state.iterdir()), ["armed.json"])


if __name__ == "__main__":
    unittest.main()
