"""Offline tests of the Agent Control Desk as ONE table (Peter, 2026-11-17): rows are settings, columns are
agents; every cell starts with a light; Enter opens a floating menu under the cell, then one confirm line; Enter
on a row's name changes that setting for every agent; the strip under the table tells the whole story; and the
fallback ladder has one fixed order for everyone.

Fakes and throw-away folders only: no live settings file, no Team Room, no Telegram, no window, no sound. Every
writer is given the throw-away world's own paths, never the real ones.

    .venv/bin/python -m unittest tests.test_acd_table -v
"""
import asyncio
import dataclasses
import datetime as dt
import json
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
import acd_snapshot  # noqa: E402
import acd_watch as W  # noqa: E402
import gridstate as G  # noqa: E402
from textual.widgets import DataTable, OptionList, Static  # noqa: E402

AGY = F.AGY
L = F.lines_loader
WHEN = dt.datetime(2026, 11, 17, 10, 0, 0)
NV = "nvidia/nemotron-3.5-lightning-30b-a3b"
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"
CODEX = ("openai-codex", "gpt-5.4-mini")


def cfg(w, profile="assistant") -> Path:
    return w.profiles / profile / "config.yaml"


def listed(w, profile="assistant") -> list:
    return [(i.provider, i.model) for i in E.read_block(cfg(w, profile).read_text(encoding="utf-8")).items]


def set_list(w, profile: str, pairs: list) -> None:
    """Write a backups list by hand into the throw-away world (the way a person editing the file would)."""
    text = cfg(w, profile).read_text(encoding="utf-8")
    cfg(w, profile).write_text(E.rewrite_backups(text, pairs, "2026-11-01", "test"), encoding="utf-8")


def never(words):
    raise AssertionError(f"the Team Room command was run in a dry run or a refusal: {words}")


def members(w) -> list:
    return [m["profile"] for m in json.loads(w.edit.members.read_text(encoding="utf-8"))["members"]]


def make_app(w, **more):
    more.setdefault("tester", None)
    more.setdefault("watching", False)
    return acd.Desk(collector=lambda: D.collect(w.paths, yaml_loader=L, now=w.now, desk=E.record(w.edit),
                                                watch=W.view(w.edit, now=w.now)),
                    autostart=False, edit=w.edit, loader=L, **more)


async def show(app, pilot) -> None:
    snap = await asyncio.to_thread(app.collector)
    app.apply_snapshot(snap)
    await pilot.pause()


async def settle(app, pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()
    await app.workers.wait_for_complete()                 # the read that follows a change
    await pilot.pause()


def table(app) -> DataTable:
    return app.screen.query_one("#table", DataTable)


def text_of(app, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).render())


def shown(app, row_key: str, name: str) -> str:
    """What the table draws in one cell."""
    screen = next(s for s in reversed(app.screen_stack) if isinstance(s, acd.TableScreen))
    t = screen.query_one("#table", DataTable)
    r = next(i for i, row in enumerate(screen.rows) if row.key == row_key)
    c = next(i for i, col in enumerate(screen.cols) if col.name == name) + 1
    return str(t.get_cell_at((r, c)))


def menu_list(app) -> str:
    lst = app.screen.query_one("#menu-list", OptionList)
    return "\n".join(str(lst.get_option_at_index(i).prompt) for i in range(lst.option_count))


def menu(app, part: str) -> str:
    return str(app.screen.query_one(f"#menu-{part}", Static).render())


def pick(app, token: str) -> None:
    """Put the menu's highlight on a choice (the test's stand-in for pressing up or down to it)."""
    app.screen.query_one("#menu-list", OptionList).highlighted = \
        [o.token for o in app.screen.spec.options].index(token)


def notice(app) -> str:
    return text_of(app, "#box-title") + "\n" + text_of(app, "#box-text")


async def open_cell(app, pilot, row_key: str, column) -> None:
    assert app.screen.go(row_key, column), (row_key, column)
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


# ---- the ladder, as facts ------------------------------------------------------------------------------------
class LadderFacts(unittest.TestCase):
    def test_every_backup_has_its_rung_and_anything_else_is_an_extra(self):
        self.assertEqual(D.rung_of(AGY, "claude-opus-5-5"), "ag_claude")
        self.assertEqual(D.rung_of(AGY, "gemini-4-argon"), "ag_gemini")
        self.assertEqual(D.rung_of("openai-codex", "gpt-5.4-mini"), "codex")
        self.assertEqual(D.rung_of("nvidia", ULTRA), "nvidia")
        self.assertEqual(D.rung_of("anthropic", "claude-sonnet-5-5"), "")       # a lighter model on the main service
        self.assertEqual(D.LADDER_KEYS, ("ag_claude", "ag_gemini", "codex", "nvidia"))

    def test_a_list_in_the_standard_order_is_green_and_a_missing_rung_is_off(self):
        w = F.edit_world()
        v = F.agent(F.edit_collect(w), "assistant")
        lad = D.ladder(v)
        self.assertTrue(lad.in_order)
        self.assertEqual([(r.key, r.on, r.status) for r in lad.rungs],
                         [("ag_claude", True, D.GREEN), ("ag_gemini", True, D.GREEN), ("codex", False, D.GREY),
                          ("nvidia", True, D.GREEN)])
        self.assertEqual(lad.extras, ())
        self.assertEqual(acd.cell_at("rung:codex", v).words, "off")
        self.assertEqual(acd.cell_at("rung:codex", v).setting, "rung:codex")    # off is a choice: it has a menu

    def test_a_list_that_is_not_in_order_is_shown_as_it_is_with_a_yellow_light_and_never_reordered(self):
        w = F.edit_world()
        set_list(w, "assistant", [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), ("nvidia", ULTRA), CODEX])
        before = F.tree_hashes(w.root)
        snap = F.edit_collect(w)
        v = F.agent(snap, "assistant")
        lad = D.ladder(v)
        self.assertFalse(lad.in_order)
        self.assertIn(D.NOT_STANDARD, lad.order_note)
        self.assertEqual({r.key: r.status for r in lad.rungs},
                         {"ag_claude": D.GREEN, "ag_gemini": D.GREEN, "codex": D.YELLOW, "nvidia": D.YELLOW})
        self.assertEqual([b.model for b in v.backups if b.on],                  # the truth: the file's own order
                         ["claude-opus-5-5", "gemini-3.8-flash", ULTRA, "gpt-5.4-mini"])
        story = acd.column_story(snap, v)
        self.assertIn("Not in the standard order", story)
        self.assertIn("A change on any rung writes the list in the standard order.", story)
        self.assertEqual(F.tree_hashes(w.root), before, "reading the ladder changed a file")

    def test_a_backup_that_is_not_on_the_ladder_is_an_extra_with_its_own_row(self):
        w = F.edit_world()
        set_list(w, "assistant", [(AGY, "claude-opus-5-5"), ("anthropic", "claude-sonnet-5-5"), ("nvidia", NV)])
        snap = F.edit_collect(w)
        v = F.agent(snap, "assistant")
        lad = D.ladder(v)
        self.assertEqual([(b.service, b.model) for b in lad.extras], [("anthropic", "claude-sonnet-5-5")])
        self.assertFalse(lad.in_order)                                          # it sits between two rungs
        self.assertIn("extras", [r.key for r in acd.rows_of(snap)])
        cell = acd.cell_at("extras", v)
        self.assertEqual((cell.status, cell.setting), (D.YELLOW, "extras"))
        self.assertEqual(cell.words, "Sonnet 5.5 (Claude)")
        self.assertEqual(acd.cell_at("extras", F.agent(snap, "orchestrator")).words, "none")
        self.assertNotIn("extras", [r.key for r in acd.rows_of(F.edit_collect(F.edit_world()))])    # nobody has one

    def test_the_rung_that_answered_last_is_marked_and_a_slip_below_rung_one_is_a_banner(self):
        w = F.edit_world()
        F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", "anthropic"),
              F.failed(w.now - 50, "k1", "claude-opus-5-5", "anthropic", "overloaded 529"),
              F.fallback(w.now - 50, "k1", "claude-opus-5-5", "gemini-3.8-flash", AGY))
        F.log(w, "orchestrator", F.turn(w.now - 60, "j1", "claude-sonnet-5-5", "anthropic"))
        snap = F.edit_collect(w)
        kylie, jarvis = F.agent(snap, "assistant"), F.agent(snap, "orchestrator")
        lad = D.ladder(kylie)
        self.assertEqual(lad.answered, "ag_gemini")
        self.assertEqual(lad.below, "rung 2b (Gemini 3.8 Flash on Antigravity)")
        self.assertTrue(acd.cell_at("rung:ag_gemini", kylie).marker)
        self.assertFalse(acd.cell_at("rung:ag_claude", kylie).marker)
        self.assertTrue(acd.cell_text(acd.cell_at("rung:ag_gemini", kylie)).plain.startswith(
            f"{acd.MARK} {acd.ANSWERED} Gemini Flash"))
        self.assertEqual((D.ladder(jarvis).answered, D.ladder(jarvis).below), (D.MAIN_RUNG, ""))
        self.assertTrue(acd.cell_at("rung:main", jarvis).marker)
        texts = [x.text for x in snap.warnings]
        self.assertTrue(any("1 agent had the main model fail on the last reply and a backup answered: Kylie on rung 2b "
                            "(Gemini 3.8 Flash on Antigravity)" in x for x in texts), texts)

    def test_a_rung_whose_allowance_is_used_up_is_red_with_the_day_it_comes_back(self):
        w = F.edit_world()
        F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", AGY),
              F.quota(w.now - 59, "k1", "claude-opus-5-5", resets="86h46m59s"),
              F.fallback(w.now - 58, "k1", "claude-opus-5-5", "gemini-3.8-flash", AGY))
        v = F.agent(F.edit_collect(w), "assistant")
        back = dt.datetime.fromtimestamp(w.now - 59 + 86 * 3600 + 46 * 60 + 59)
        cell = acd.cell_at("rung:ag_claude", v)
        self.assertEqual((cell.status, cell.words), (D.RED, f"Opus, back {back.strftime('%-d %b')}"))
        self.assertIn(f"used up until {back.strftime('%a %-d %b')}", " ".join(cell.detail))    # the strip has it in full
        self.assertEqual(acd.cell_at("rung:ag_gemini", v).status, D.GREEN)

    def test_simba_is_locked_on_her_model_and_her_ladder_rows_are_grey(self):
        v = F.agent(F.edit_collect(F.edit_world()), "simba")
        for key in D.LADDER_KEYS:
            cell = acd.cell_at(f"rung:{key}", v)
            self.assertEqual((cell.status, cell.words, cell.setting), (D.GREY, "none", ""))
            self.assertIn("no backups by design", cell.fixed)
        main = acd.cell_at("rung:main", v)
        self.assertEqual((main.words, main.setting), ("Grok 4.20", ""))
        self.assertIn("locked", acd.cell_at("model", v).words)


# ---- the ladder, changed ---------------------------------------------------------------------------------------
class LadderChanges(unittest.TestCase):
    def test_a_dry_run_writes_nothing_and_says_exactly_what_would_change(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        r = E.set_rung("assistant", "codex", "gpt-5.4-mini", w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok and r.dry, r.message)
        self.assertIn("Kylie's rung 3 (ChatGPT) is switched on: GPT 5.4 mini", r.message)
        self.assertIn("+ - provider: openai-codex", r.lines)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_armed_a_rung_goes_into_its_place_everything_else_stays_and_undo_puts_it_back(self):
        w = F.edit_world(armed=True)
        old = cfg(w).read_text(encoding="utf-8")
        r = E.set_rung("assistant", "codex", "gpt-5.4-mini", w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(listed(w), [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), CODEX, ("nvidia", NV)])
        new = cfg(w).read_text(encoding="utf-8")
        for kept in ("# a comment that must stay", "  # first choice", "# last resort", "# the main model",
                     "reasoning_effort: medium", "    - team-room-telegram"):
            self.assertIn(kept, new)
        self.assertIn("Agent Control Desk: backups set in the standard ladder order", new)
        entry = E.read_history(w.edit)[-1]
        self.assertEqual((entry["kind"], entry["profile"]), ("ladder", "assistant"))
        self.assertEqual(Path(entry["files"][0]["backup"]).read_text(encoding="utf-8"), old)    # the backup is the old file
        self.assertEqual(acd.cell_at("rung:codex", F.agent(F.edit_collect(w), "assistant")).words, "GPT 5.4 mini")
        u = E.undo(w.edit, loader=L, now=WHEN + dt.timedelta(minutes=1))
        self.assertTrue(u.ok and not u.dry, u.message)
        self.assertIn("Undone:", u.message)
        self.assertEqual(listed(w), [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), ("nvidia", NV)])
        self.assertIn("# last resort", cfg(w).read_text(encoding="utf-8"))

    def test_off_is_remembered_and_on_again_brings_the_entrys_own_lines_back(self):
        w = F.edit_world(armed=True)
        r = E.set_rung("assistant", "nvidia", None, w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIn("switched off (it was NVIDIA Nemotron 3.5 Lightning; the desk remembers it)", r.message)
        self.assertEqual(listed(w), [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash")])
        v = F.agent(F.edit_collect(w), "assistant")
        cell = acd.cell_at("rung:nvidia", v)
        self.assertEqual((cell.status, cell.words), (D.GREY, "off (Nemotron 3.5)"))
        r = E.set_rung("assistant", "nvidia", NV, w.edit, loader=L, now=WHEN + dt.timedelta(minutes=1))
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(listed(w)[-1], ("nvidia", NV))
        self.assertIn(f"model: {NV}   # last resort", cfg(w).read_text(encoding="utf-8"))     # its own line, comment and all
        self.assertEqual(E.read_off(w.edit).get("assistant"), [])

    def test_another_model_on_the_same_rung_takes_its_place(self):
        w = F.edit_world(armed=True)
        r = E.set_rung("assistant", "ag_claude", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIn("changes from Claude Opus 5.5 to Claude Sonnet 5.5", r.message)
        self.assertEqual(listed(w)[0], (AGY, "claude-sonnet-5-5"))
        self.assertEqual(len(listed(w)), 3)
        self.assertEqual(E.read_off(w.edit).get("assistant"), [])               # replaced, not "switched off"

    def test_a_list_out_of_order_is_put_in_order_by_the_same_write_and_it_says_so(self):
        w = F.edit_world(armed=True)
        set_list(w, "assistant", [("nvidia", NV), (AGY, "gemini-3.8-flash"), ("anthropic", "claude-sonnet-5-5")])
        r = E.set_rung("assistant", "ag_claude", "claude-opus-5-5", w.edit, loader=L, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIn("the list is also put in the standard order", r.message)
        self.assertEqual(listed(w), [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), ("nvidia", NV),
                                     ("anthropic", "claude-sonnet-5-5")])       # the extra is kept, after the ladder
        self.assertTrue(D.ladder(F.agent(F.edit_collect(w), "assistant")).in_order)

    def test_refusals_change_nothing(self):
        w = F.edit_world(armed=True)
        before = F.tree_hashes(w.root)
        for res in (E.set_rung("assistant", "codex", "gpt-9", w.edit, loader=L),
                    E.set_rung("assistant", "moon", "x", w.edit, loader=L),
                    E.set_rung("simba", "codex", "gpt-5.4-mini", w.edit, loader=L),
                    E.set_ladder("assistant", [CODEX, CODEX], w.edit, loader=L)):
            self.assertFalse(res.ok, res.message)
        same = E.set_rung("assistant", "nvidia", NV, w.edit, loader=L)
        self.assertTrue(same.ok)
        self.assertIn("nothing to change", same.message)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_a_write_that_does_not_read_back_is_put_back_exactly(self):
        w = F.edit_world(armed=True)
        old = cfg(w).read_text(encoding="utf-8")
        calls = []

        def liar(path):
            calls.append(path)
            data = L(path)
            if len(calls) > 1:                                # the look after the write sees something else
                data["fallback_providers"] = []
            return data
        r = E.set_rung("assistant", "codex", "gpt-5.4-mini", w.edit, loader=liar, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("put back exactly as it was", r.message)
        self.assertEqual(cfg(w).read_text(encoding="utf-8"), old)
        self.assertEqual(E.read_history(w.edit), [])

    def test_the_standard_ladder_plan_only_reads_whatever_the_armed_switch_says(self):
        w = F.edit_world(armed=True)
        set_list(w, "wanna-buffet", [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), ("nvidia", ULTRA), CODEX])
        set_list(w, "tax_angel_esther", [(AGY, "gemini-3.8-flash"), ("anthropic", "claude-sonnet-5-5")])
        set_list(w, "orchestrator", [(AGY, "claude-sonnet-5-5"), (AGY, "gemini-3.8-flash")])
        before = F.tree_hashes(w.root)
        plans = {p.profile: p for p in E.plan_standard_ladder(w.edit, loader=L)}
        self.assertEqual(sorted(plans), ["assistant", "orchestrator", "tax_angel_esther", "wanna-buffet"])   # not Simba
        self.assertEqual(plans["assistant"].after,
                         [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), CODEX, ("nvidia", NV)])
        self.assertEqual(plans["wanna-buffet"].after,                           # keeps its own NVIDIA model, in order now
                         [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), CODEX, ("nvidia", ULTRA)])
        self.assertIn("puts the rungs it already has in the standard order", plans["wanna-buffet"].notes)
        self.assertEqual(plans["tax_angel_esther"].after,                       # Claude on Antigravity = her main model
                         [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), CODEX, ("nvidia", NV),
                          ("anthropic", "claude-sonnet-5-5")])
        self.assertTrue(any("keeps Claude Sonnet 5.5 via Claude sub. after the ladder" in n
                            for n in plans["tax_angel_esther"].notes))
        self.assertEqual(plans["orchestrator"].after[0], (AGY, "claude-sonnet-5-5"))
        self.assertTrue(any("it was switched off for this agent before" in n for n in plans["orchestrator"].notes))
        dropped = {p.profile: p for p in E.plan_standard_ladder(w.edit, loader=L, extras="drop")}
        self.assertEqual(len(dropped["tax_angel_esther"].after), 4)
        results = E.apply_standard_ladder(w.edit, dry_run=True, loader=L, now=WHEN)      # forced dry on an ARMED desk
        self.assertTrue(all(r.ok and r.dry for r in results), [r.message for r in results])
        self.assertTrue(any(ln.startswith("- before:") for ln in results[0].lines))
        self.assertTrue(any(ln.startswith("+ after:") for ln in results[0].lines))
        self.assertEqual(F.tree_hashes(w.root), before, "the plan or the forced dry run wrote something")

    def test_applied_on_an_armed_desk_every_agent_is_on_the_ladder_and_each_can_be_undone(self):
        w = F.edit_world(armed=True)
        results = E.apply_standard_ladder(w.edit, loader=L, now=WHEN)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.ok and not r.dry for r in results), [r.message for r in results])
        for prof in ("orchestrator", "assistant", "tax_angel_esther", "wanna-buffet"):
            self.assertEqual([D.rung_of(*p) for p in listed(w, prof)], list(D.LADDER_KEYS), prof)
        self.assertEqual(listed(w, "simba"), [])
        again = E.plan_standard_ladder(w.edit, loader=L)
        self.assertFalse(any(p.changes for p in again))
        self.assertEqual(again[0].notes, ["already on the standard ladder: nothing changes"])
        u = E.undo(w.edit, loader=L, now=WHEN + dt.timedelta(minutes=1))                    # the newest: Wanna Buffet
        self.assertTrue(u.ok and not u.dry, u.message)
        self.assertEqual(len(listed(w, "wanna-buffet")), 3)
        self.assertEqual(len(listed(w, "assistant")), 4)

    def test_not_armed_the_same_call_is_a_dry_run(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        results = E.apply_standard_ladder(w.edit, loader=L, now=WHEN)
        self.assertTrue(all(r.ok and r.dry for r in results))
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_the_watcher_expects_the_ladder_the_desk_wrote_and_reports_a_hand_edit_after_it(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.set_backup("assistant", "nvidia", NV, False, w.edit, loader=L, now=WHEN).ok)
        t1 = WHEN + dt.timedelta(minutes=1)
        self.assertTrue(E.apply_standard_ladder(w.edit, loader=L, now=t1)[1].ok)            # NVIDIA comes back with it
        self.assertEqual(W.look(w.edit, now=t1.timestamp() + 600), [])                      # the desk's own write: no drift
        text = cfg(w).read_text(encoding="utf-8")
        cfg(w).write_text(E.rewrite_backups(text, [(AGY, "claude-opus-5-5")], "2026-11-17", "by hand"), encoding="utf-8")
        drifts = W.look(w.edit, now=t1.timestamp() + 900)
        self.assertEqual(sorted(d.key for d in drifts if d.profile == "assistant"),
                         sorted([f"backup|{AGY}|gemini-3.8-flash", "backup|openai-codex|gpt-5.4-mini", f"backup|nvidia|{NV}"]))


# ---- the table, as facts ---------------------------------------------------------------------------------------
class TableFacts(unittest.TestCase):
    def test_the_rows_are_the_settings_under_five_headings(self):
        snap = F.edit_collect(F.with_grid(F.edit_world()))
        rows = acd.rows_of(snap)
        self.assertEqual([r.label for r in rows if r.section], list(acd.SECTIONS))
        keys = [r.key for r in rows]
        for key in ("model", "effort", "signin", "last", "drops", "rung:main", "rung:ag_claude", "rung:ag_gemini",
                    "rung:codex", "rung:nvidia", "voice", "voice_zh", "speed", "vplace:terminal", "vplace:tg_dm",
                    "vplace:tg_group", "vplace:mic", "names", "presence:terminal", "presence:team_room",
                    "presence:iphone_voice", "talk:speaks", "talk:hands_out", "talk:takes_from", "talk:answers"):
            self.assertIn(key, keys)
        self.assertLess(keys.index("rung:ag_claude"), keys.index("rung:ag_gemini"))     # the ladder's own order
        self.assertLess(keys.index("rung:codex"), keys.index("rung:nvidia"))
        names = [c.name for c in acd.columns_of(snap)]
        self.assertEqual(names[:5], ["Jarvis", "Kylie", "Esther", "Wanna Buffet", "Simba"])
        self.assertEqual(sorted(names[5:]), ["Claude", "Codex", "Gemini"])              # the assistants come after

    def test_every_cell_that_is_not_blank_starts_with_a_light(self):
        snap = F.edit_collect(F.with_grid(F.edit_world()))
        seen = set()
        for row in acd.rows_of(snap):
            for col in acd.columns_of(snap):
                cell = acd.cell_of(row, col)
                if cell.blank:
                    continue
                self.assertTrue(acd.cell_text(cell).plain.startswith(acd.MARK + " "), (row.key, col.name))
                self.assertIn(cell.status, (D.GREEN, D.YELLOW, D.BLUE, D.RED, D.GREY))
                self.assertTrue(cell.setting or cell.fixed, (row.key, col.name))        # a menu, or the reason for none
                seen.add(cell.status)
        self.assertLessEqual({D.GREEN, D.GREY}, seen)
        self.assertEqual([w for _, w in acd.LEGEND][-1], "off or not applicable")

    def test_the_assistants_have_voice_cells_and_call_names_and_blank_rows_elsewhere(self):
        snap = F.edit_collect(F.with_grid(F.edit_world()))
        claude = next(o for o in snap.others if o.key == "claude")
        for key in ("model", "effort", "rung:codex", "presence:team_room", "talk:answers", "speed"):
            self.assertTrue(acd.cell_at(key, claude).blank, key)
        self.assertEqual(acd.cell_at("voice", claude).setting, "voice")
        names = acd.cell_at("names", claude)
        # the names themselves are never changed; with the Voice Desk grid known, Enter opens the here / not here
        # marks and the activation phrases (tests/test_acd_two_keys.py)
        self.assertEqual((names.status, names.setting), (D.GREEN, "wire:*"))
        self.assertIn("claude", names.words)
        self.assertEqual(acd.cell_at("vplace:terminal", claude).setting, "vplace:terminal")
        self.assertTrue(acd.cell_at("names", F.agent(snap, "assistant")).blank)

    def test_facts_have_a_light_and_no_menu_and_say_why(self):
        w = F.edit_world()
        F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", "anthropic", "telegram"))
        snap = F.edit_collect(w)
        v = F.agent(snap, "assistant")
        for key in ("signin", "last", "drops"):
            cell = acd.cell_at(key, v)
            self.assertEqual((cell.setting, cell.fixed), ("", acd.FACT), key)
        self.assertTrue(acd.cell_at("last", v).words.startswith("Opus 5.5, "))
        self.assertEqual((acd.cell_at("drops", v).words, acd.cell_at("drops", v).status), ("0", D.GREEN))
        strip = acd.strip_text(acd.Row("last", "Who answered last"), v, acd.cell_at("last", v)).plain
        self.assertIn("Kylie, Who answered last:", strip)
        self.assertIn("Nothing to change here: a fact read from the logs", strip)
        strip = acd.strip_text(acd.Row("model", "Model"), v, acd.cell_at("model", v)).plain
        self.assertTrue(" ".join(strip.split()).endswith("Enter opens the choices."))
        self.assertLessEqual(len(strip.splitlines()), acd.STRIP_LINES)

    def test_a_speed_the_voice_cannot_take_is_shown_as_fixed_and_has_no_menu(self):
        snap = F.edit_collect(F.with_grid(F.edit_world()))
        v = F.agent(snap, "assistant")
        free = acd.cell_at("speed", v)
        self.assertEqual((free.words, free.setting), ("normal (1)", "speed"))
        fixed = acd.cell_at("speed", v, {v.voice_key: E.SPEED_FIXED})
        self.assertEqual(fixed.setting, "")
        self.assertIn("fixed by this voice", fixed.fixed)
        none = acd.cell_at("speed", dataclasses.replace(v, voice=dataclasses.replace(v.voice, speed=None)),
                           {v.voice_key: E.SPEED_FIXED})
        self.assertEqual((none.words, none.status), ("fixed by this voice", D.GREY))

    def test_whether_a_speed_can_be_changed_is_found_out_on_copies_never_on_the_live_files(self):
        w = F.with_grid(F.edit_world(armed=True))
        before = F.tree_hashes(w.root)
        self.assertEqual(E.speed_fixed("kylie", w.edit), "")                   # her speaker takes a speed
        self.assertEqual(F.tree_hashes(w.root), before)
        self.assertIn("could not be read", E.speed_fixed("nobody-by-that-name", w.edit))

    def test_one_width_for_every_agent_column_and_a_cut_cell_ends_in_an_ellipsis(self):
        snap = F.edit_collect(F.with_grid(F.edit_world()))
        wide, narrow = acd.column_width(snap, 300), acd.column_width(snap, 100)
        self.assertLessEqual(wide, acd.COL_MAX)
        self.assertGreaterEqual(narrow, acd.COL_MIN)
        self.assertGreaterEqual(wide, narrow)
        cell = acd.Cell(D.GREEN, "a very long sentence that cannot fit into the column")
        cut = acd.cell_text(cell, 20).plain
        self.assertEqual(cut, f"{acd.MARK} a very long…")                      # cut BETWEEN two words, never inside one
        self.assertLessEqual(len(cut), 20)
        self.assertEqual(acd.cell_text(acd.Cell()).plain, "")                  # a row that does not apply is blank


# ---- the screen ------------------------------------------------------------------------------------------------
class TheTable(unittest.IsolatedAsyncioTestCase):
    async def test_rows_are_settings_columns_are_agents_and_the_strip_tells_the_story(self):
        w = F.with_grid(F.edit_world())
        F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", "anthropic", "telegram"))
        app = make_app(w)
        async with app.run_test(size=(260, 70)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TableScreen)
            self.assertIn("reading every agent", text_of(app, "#warnings"))     # nothing read yet, and it says so
            await show(app, pilot)
            self.assertIn(acd.DRY_LABEL, text_of(app, "#title"))
            legend = text_of(app, "#legend")
            for words in ("live everywhere", "waiting on a restart or a fresh chat", "temporary override",
                          "changed behind the desk's back", "off or not applicable"):
                self.assertIn(words, legend)
            t = table(app)
            heads = [str(c.label).split("\n")[0] for c in t.columns.values()]
            self.assertEqual(heads[1:6], ["Jarvis", "Kylie", "Esther", "Wanna Buffet", "Simba"])
            self.assertEqual(t.fixed_columns, 1)                                # the row names stay pinned
            self.assertEqual(t.row_count, len(acd.rows_of(app.snapshot)))
            self.assertEqual(shown(app, "model", "Kylie"), f"{acd.MARK} Claude Opus 5.5")
            self.assertEqual(shown(app, "effort", "Kylie"), f"{acd.MARK} medium")
            self.assertEqual(shown(app, "rung:codex", "Kylie"), f"{acd.MARK} off")
            self.assertEqual(shown(app, "rung:main", "Kylie"), f"{acd.MARK} {acd.ANSWERED} Opus 5.5")
            self.assertEqual(shown(app, "rung:nvidia", "Simba"), f"{acd.MARK} none")
            self.assertEqual(shown(app, "voice", "Kylie"), f"{acd.MARK} Ava")
            self.assertEqual(shown(app, "presence:team_room", "Jarvis"), f"{acd.MARK} in, leads it")
            self.assertEqual(shown(app, "talk:hands_out", "Kylie"), f"{acd.MARK} no")
            self.assertEqual(shown(app, "model", "Claude"), "")                 # blank: the row does not apply
            self.assertEqual(t.cursor_coordinate, (1, 1))                       # the first setting of the first agent
            self.assertTrue(text_of(app, "#strip").startswith("Jarvis, Model: Jarvis's model is set to Claude Sonnet 5.5"))
            await pilot.press("right")
            await pilot.press("down")
            await pilot.pause()
            self.assertTrue(text_of(app, "#strip").startswith("Kylie, Thinking effort: Kylie thinks with medium effort."))
            self.assertFalse(app.screen.query_one("#more", Static).display)     # wide and tall enough: nothing hidden
            app.screen.go("presence:team_room", "Kylie")
            await pilot.pause()
            self.assertIn("The Team Room (all agents): Jarvis leads the Team Room.", text_of(app, "#strip").replace("\n", " "))

    async def test_the_arrows_step_over_the_headings_and_stop_at_the_edges_and_say_so(self):
        w = F.with_grid(F.edit_world())
        app = make_app(w)
        async with app.run_test(size=(260, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            t, screen = table(app), app.screen
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(t.cursor_coordinate.row, 1)                        # row 0 is a heading: there is no going up
            self.assertIn("that is the first row", text_of(app, "#strip"))
            for _ in range(t.row_count + 3):
                await pilot.press("down")
                self.assertFalse(screen.rows[t.cursor_coordinate.row].section, "the cursor sat on a heading")
            await pilot.pause()
            self.assertEqual(screen.rows[t.cursor_coordinate.row].key, "talk:answers")
            self.assertIn("that is the last row: nothing is below it", text_of(app, "#strip"))
            await pilot.press("left")
            await pilot.pause()
            self.assertEqual(t.cursor_coordinate.column, 0)
            self.assertIn("Answers, every agent:", text_of(app, "#strip"))
            await pilot.press("left")
            await pilot.pause()
            self.assertEqual(t.cursor_coordinate.column, 0)
            self.assertIn("that is the first column", text_of(app, "#strip"))
            for _ in range(len(t.columns) + 2):
                await pilot.press("right")
            await pilot.pause()
            self.assertEqual(t.cursor_coordinate.column, len(t.columns) - 1)
            self.assertIn("that is the last column", text_of(app, "#strip"))

    async def test_the_chosen_cell_keeps_its_colour_and_the_mouse_never_looks_like_a_second_choice(self):
        w = F.with_grid(F.edit_world())
        app = make_app(w)
        async with app.run_test(size=(260, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            t = table(app)
            self.assertEqual(t.cursor_foreground_priority, "renderable")
            self.assertEqual(t.cursor_type, "cell")
            line = t.render_line(t.header_height + 1)                            # the line the cursor is on
            colours = {seg.style.color.name for seg in line if seg.style and seg.style.color and seg.text.strip()}
            self.assertIn(acd.COLOUR[D.GREEN], colours, "a green light must stay green under the highlight")
            await pilot.hover("#table", offset=(60, 12))
            await pilot.pause()
            self.assertFalse(t._show_hover_cursor)

    async def test_a_narrow_window_pins_the_row_names_scrolls_the_agents_and_names_what_is_hidden(self):
        w = F.with_grid(F.edit_world())
        app = make_app(w)
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.pause()
            t = table(app)
            self.assertEqual(t.fixed_columns, 1)
            self.assertGreater(t.virtual_size.width, t.size.width)              # it scrolls sideways: nothing is dropped
            self.assertEqual(len(t.columns), 1 + len(app.screen.cols))
            more = app.screen.query_one("#more", Static)
            self.assertTrue(more.display)
            hint = str(more.render()).replace("\n", " ")
            self.assertIn("more columns to the right (right arrow):", hint)
            for name in ("Simba", "Claude", "Codex"):
                self.assertIn(name, hint)
            self.assertNotIn("Jarvis", hint)
            self.assertIn("more rows below (down arrow):", hint)
            self.assertIn("WHO IT MAY TALK TO", hint)
            for _ in range(len(t.columns)):
                await pilot.press("right")
            await pilot.pause()
            await pilot.pause()
            hint = str(more.render()).replace("\n", " ")
            self.assertIn("more columns to the left (left arrow):", hint)
            self.assertIn("Jarvis", hint)
            self.assertNotIn("to the right", hint)
            keys = text_of(app, "#keys")
            self.assertGreater(keys.count("\n"), 0)                              # the hints wrap between two keys
            self.assertEqual([p for ln in keys.split("\n") for p in ln.split("   ")], list(acd.TABLE_KEYS))

    async def test_a_broken_settings_file_is_one_red_cell_and_the_desk_stays_up(self):
        w = F.edit_world()
        cfg(w).write_text("BROKEN", encoding="utf-8")
        app = make_app(w)
        async with app.run_test(size=(220, 60)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            self.assertEqual(shown(app, "model", "Kylie"), f"{acd.MARK} cannot be read")
            self.assertEqual(shown(app, "model", "Jarvis"), f"{acd.MARK} Claude Sonnet 5.5")
            self.assertIn("settings file could not be read for: Kylie", text_of(app, "#warnings"))
            await open_cell(app, pilot, "model", "Kylie")
            self.assertIsInstance(app.screen, acd.TableScreen)                  # nothing to choose: no menu opens
            self.assertIn("Nothing to change here: the settings file cannot be read.", text_of(app, "#strip"))

    async def test_a_failed_read_keeps_the_last_good_picture(self):
        w = F.edit_world()
        app = make_app(w)
        async with app.run_test(size=(220, 60)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            rows = table(app).row_count

            def boom():
                raise RuntimeError("disk gone")
            app.collector = boom
            app.action_reread()
            await settle(app, pilot)
            self.assertIn("this read failed", text_of(app, "#warnings"))
            self.assertEqual(table(app).row_count, rows)
            self.assertEqual(shown(app, "model", "Kylie"), f"{acd.MARK} Claude Opus 5.5")

    async def test_the_keys(self):
        app = make_app(F.edit_world())
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.pause()
            keys = {b.key for b in app.BINDINGS} | {b.key for b in acd.TableScreen.BINDINGS}
            self.assertEqual(keys, {"q", "c", "u", "r", "p", "t", "l", "n", "d", "s"})
            self.assertFalse(hasattr(acd, "TreeScreen"))                        # the tree is gone
            self.assertFalse(hasattr(acd, "GridScreen"))

    async def test_pictures_are_drawn_without_a_window_and_a_picture_run_is_never_armed(self):
        w = F.with_grid(F.edit_world(armed=True))
        before = F.tree_hashes(w.root)
        out = Path(tempfile.mkdtemp(prefix="acd-shot-"))
        one = await acd_snapshot.render(out, collector=lambda: F.edit_collect(w), size=(200, 62))
        self.assertTrue(one.read_text(encoding="utf-8").lstrip().startswith("<svg"))
        made = await acd_snapshot.render_table(
            out, collector=lambda: F.edit_collect(w), size=(200, 62), narrow=(110, 44),
            desk_kwargs={"edit": w.edit, "loader": L, "tester": None, "launcher_armed": True, "watching": False})
        self.assertEqual([p.name for p in made],
                         ["table_full.svg", "table_narrow.svg", "table_cell_menu.svg", "table_confirm_line.svg",
                          "table_ladder_menu.svg", "table_row_wide_confirm.svg", "table_standard_ladder.svg",
                          "table_standard_ladder_narrow.svg", "table_right_end.svg", "table_blank_cell.svg",
                          "table_listener_popup.svg"])
        self.assertIn("LANGUAGE", made[0].read_text(encoding="utf-8"))
        self.assertIn("language", made[2].read_text(encoding="utf-8"))          # "Kylie: language model"
        self.assertEqual(F.tree_hashes(w.root), before, "a picture run wrote something into the world")


class TheMenus(unittest.IsolatedAsyncioTestCase):
    async def test_a_cell_opens_its_menu_under_it_then_one_confirm_line_and_a_dry_run_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            anchor = None
            app.screen.go("model", "Kylie")
            await pilot.pause()
            anchor = app.screen.cell_anchor()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertEqual(menu(app, "title"), "Kylie: language model")
            self.assertEqual(menu_list(app).split("\n"),
                             ["(o) Claude Opus 5.5   now", "( ) Claude Sonnet 5.5", "( ) Claude Haiku 5.5"])
            box = app.screen.query_one("#menu")
            self.assertEqual((box.region.x, box.region.y), anchor)             # it floats right under the cell
            await pilot.press("enter")                                          # today's choice: nothing to do
            await pilot.pause()
            self.assertIn("nothing to change", menu(app, "status"))
            self.assertIsInstance(app.screen, acd.CellMenu)
            await pilot.press("down")
            await pilot.press("enter")                                          # Sonnet: the confirm line
            await pilot.pause()
            confirm = menu(app, "confirm").replace("\n", " ")
            self.assertIn("DRY RUN", confirm)
            self.assertIn("Kylie's model changes from Claude Opus 5.5 to Claude Sonnet 5.5.", " ".join(confirm.split()))
            self.assertIn("No restart needed.", confirm)                        # whether a restart is needed
            self.assertIn("Enter = yes", menu(app, "keys"))
            await pilot.press("escape")                                         # back to the choices, not closed
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertEqual(menu(app, "confirm"), "")
            await pilot.press("enter")
            await pilot.press("enter")                                          # yes
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.ResultPopup)
            self.assertIn("Dry run: nothing was written", notice(app))
            self.assertIn("This would happen: Kylie's model changes", notice(app))
            self.assertIn("+ default: claude-sonnet-5-5", notice(app))
            await pilot.press("escape")                                         # Esc only closes the result box
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.TableScreen)
        self.assertEqual(F.tree_hashes(w.root), before, "a dry run from the screen wrote something")

    async def test_escape_changes_nothing(self):
        w = F.edit_world(armed=True)
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "model", "Kylie")
            await pilot.press("down")
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.TableScreen)
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_armed_a_save_writes_the_cell_turns_yellow_and_u_undoes(self):
        w = F.edit_world(armed=True)
        app = make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            self.assertIn(acd.ARMED_LABEL, text_of(app, "#title"))
            await open_cell(app, pilot, "model", "Kylie")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("WRITTEN now", menu(app, "confirm"))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved", notice(app))
            self.assertEqual(E.managed(cfg(w).read_text())["model"]["default"], "claude-sonnet-5-5")
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertEqual(shown(app, "model", "Kylie"), f"{acd.MARK} Claude Sonnet 5.5")
            kylie = F.agent(app.snapshot, "assistant")
            self.assertEqual(acd.cell_at("model", kylie).status, D.YELLOW)      # saved, not live everywhere yet
            self.assertIn("Not live everywhere yet", " ".join(acd.cell_at("model", kylie).detail))
            await pilot.press("u")
            await pilot.pause()
            self.assertIn("Undo the newest change?", notice(app))
            self.assertIn("Kylie's model changes from Claude Opus 5.5 to Claude Sonnet 5.5", notice(app))
            await pilot.press("y")
            await settle(app, pilot)
            self.assertIn("Undone:", notice(app))
            self.assertEqual(E.managed(cfg(w).read_text())["model"]["default"], "claude-opus-5-5")

    async def test_a_fact_and_a_locked_cell_open_no_menu_and_the_strip_says_why(self):
        w = F.edit_world()
        app = make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "last", "Kylie")
            self.assertIsInstance(app.screen, acd.TableScreen)
            self.assertIn("Nothing to change here: a fact read from the logs", text_of(app, "#strip").replace("\n", " "))
            await open_cell(app, pilot, "model", "Simba")
            self.assertIsInstance(app.screen, acd.TableScreen)
            self.assertIn("Simba's model is locked", text_of(app, "#strip").replace("\n", " "))
            await open_cell(app, pilot, "model", "Esther")                      # her note travels with the menu
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertIn("meant to stay fixed on Claude Opus 5.5", menu(app, "note").replace("\n", " "))
            self.assertIn("Claude Sonnet 5.5", menu_list(app))

    async def test_the_speed_menu_offers_presets_only(self):
        w = F.edit_world()
        app = make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "speed", "Kylie")
            self.assertEqual(menu_list(app).split("\n"), ["( ) slower (0.9)", "(o) normal (1)   now",
                                                           "( ) a bit faster (1.1)", "( ) fast (1.2)"])
            self.assertEqual(len(app.screen.query("Input")), 0, "there is no box to type a number into")
            await pilot.press("down", "down")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Kylie's speaking speed changes from normal to fast.", menu(app, "confirm"))

    async def test_the_voice_menu_tests_a_voice_without_playing_it_and_saves_as_a_dry_run(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        asked = []

        def fake_tester(agent, lang, voice):
            asked.append((agent.key, lang, voice))
            return True, "1.4 seconds of real sound was made (mp3); nothing was played"
        app = make_app(w, tester=fake_tester)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "voice", "Kylie")
            self.assertEqual(menu(app, "title"), "Kylie: English voice")
            self.assertIn("t test this voice", menu(app, "keys"))
            pick(app, "en-US-AriaNeural")
            await pilot.press("t")
            await settle(app, pilot)
            self.assertEqual(asked, [("kylie", "en", "en-US-AriaNeural")])
            self.assertIn("Test passed", menu(app, "status"))
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Kylie's English voice changes from Ava to Aria.", menu(app, "confirm"))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Dry run", notice(app))
            self.assertIn("in the Voice Desk list:", notice(app))
            self.assertIn("en-US-AriaNeural", notice(app))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_an_assistants_voice_is_a_cell_in_its_own_column(self):
        w = F.with_grid(F.edit_world())
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(260, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "voice", "Claude")
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertEqual(menu(app, "title"), "Claude: English voice")
            self.assertIn("Claude Code's own voice file", menu(app, "note").replace("\n", " "))
            pick(app, "en-US-AriaNeural")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Claude's English voice changes from Jenny to Aria", " ".join(menu(app, "confirm").split()))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].ok and app.last_results[0].dry)
            await pilot.press("escape")
            await pilot.pause()
            await open_cell(app, pilot, "model", "Claude")                      # a row that does not apply to it
            self.assertIsInstance(app.screen, acd.TableScreen)
            self.assertIn("this row does not apply to Claude", text_of(app, "#strip"))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_a_voice_switch_shows_the_voice_desks_own_choices_and_saves_through_its_code(self):
        w = F.with_grid(F.edit_world(armed=True))
        app = make_app(w)
        async with app.run_test(size=(260, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "vplace:tg_group", "Kylie")
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertIn("same switch the Voice Desk dashboard had", menu(app, "note"))
            self.assertEqual([o.token for o in app.screen.spec.options], [G.OFF, G.ALWAYS])
            pick(app, G.OFF)
            await pilot.press("enter")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].ok and not app.last_results[0].dry, app.last_results[0].message)
            self.assertNotIn("assistant", json.loads(Path(w.grid.mirror_config).read_text())["voice_notes"]["agents"])

    async def test_a_ladder_cell_picks_the_model_for_its_rung_or_off_and_never_the_order(self):
        w = F.edit_world(armed=True)
        app = make_app(w)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "rung:codex", "Kylie")
            self.assertEqual(menu(app, "title"), "Kylie: rung 3 (ChatGPT)")
            self.assertEqual(menu_list(app).split("\n"), ["( ) GPT 5.4 mini", "(o) off: skip this rung   now"])
            self.assertIn("The order of the ladder is fixed", menu(app, "note").replace("\n", " "))
            pick(app, "gpt-5.4-mini")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Kylie's rung 3 (ChatGPT) changes from off to GPT 5.4 mini.",
                          " ".join(menu(app, "confirm").split()))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved", notice(app))
            self.assertEqual(listed(w), [(AGY, "claude-opus-5-5"), (AGY, "gemini-3.8-flash"), CODEX, ("nvidia", NV)])
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertEqual(shown(app, "rung:codex", "Kylie"), f"{acd.MARK} GPT 5.4 mini")
            await open_cell(app, pilot, "rung:ag_claude", "Kylie")
            self.assertEqual(menu_list(app).split("\n"),
                             ["(o) Claude Opus 5.5   now", "( ) Claude Sonnet 5.5", "( ) off: skip this rung"])
            await pilot.press("escape")
            await pilot.pause()
            await open_cell(app, pilot, "rung:main", "Kylie")                   # rung 1 is the main model itself
            self.assertEqual(menu(app, "title"), "Kylie: language model")

    async def test_a_setting_changed_behind_the_desk_is_a_red_cell_that_asks_put_back_or_accept(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN).ok)
        text = cfg(w).read_text(encoding="utf-8")
        cfg(w).write_text(text.replace("default: claude-sonnet-5-5", "default: claude-haiku-5-5", 1), encoding="utf-8")
        w.now = WHEN.timestamp() + 600
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            self.assertIn("press Enter on the red cell to put it back or accept it", text_of(app, "#warnings"))
            self.assertEqual(acd.cell_at("model", F.agent(app.snapshot, "assistant")).status, D.RED)
            await open_cell(app, pilot, "model", "Kylie")
            self.assertIn("was changed behind the desk's back", menu(app, "title"))
            labels = [o.label for o in app.screen.spec.options]
            self.assertTrue(labels[0].startswith("put it back: Claude Sonnet 5.5 again"))
            self.assertTrue(labels[1].startswith("accept it: keep Claude Haiku 5.5"))
            await pilot.press("enter")                                          # put it back
            await pilot.pause()
            self.assertIn("is put back to Claude Sonnet 5.5", " ".join(menu(app, "confirm").split()))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(E.managed(cfg(w).read_text())["model"]["default"], "claude-sonnet-5-5")
            self.assertTrue(app.last_results[0].ok and not app.last_results[0].dry)

    async def test_accepting_a_drift_in_a_dry_run_changes_nothing(self):
        w = F.edit_world(armed=True)
        self.assertTrue(E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=L, now=WHEN).ok)
        text = cfg(w).read_text(encoding="utf-8")
        cfg(w).write_text(text.replace("default: claude-sonnet-5-5", "default: claude-haiku-5-5", 1), encoding="utf-8")
        w.edit = dataclasses.replace(w.edit, launcher_armed=False)
        w.now = WHEN.timestamp() + 600
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "model", "Kylie")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("DRY RUN", menu(app, "confirm"))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].dry)
        self.assertEqual(F.tree_hashes(w.root), before)


class WhereAndWho(unittest.IsolatedAsyncioTestCase):
    async def test_the_team_room_switch_as_a_dry_run_runs_nothing_and_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = make_app(w, runner=never)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "presence:team_room", "Kylie")
            self.assertEqual(menu(app, "title"), "Kylie: Team Room and Telegram team group")
            self.assertEqual(menu_list(app).split("\n"), ["(o) in the room   now", "( ) out of the room"])
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("DRY RUN", confirm)
            self.assertIn("change together, or not at all", confirm)
            self.assertIn("chat on screen starts fresh", confirm)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("This would happen: Kylie leaves the Team Room", notice(app))
            self.assertIn("team-room kick kylie", notice(app))
        self.assertEqual(F.tree_hashes(w.root), before, "a dry run from the screen wrote something")

    async def test_armed_the_team_room_switch_moves_both_doors_and_u_undoes(self):
        w = F.edit_world(armed=True)
        room = F.FakeTeamRoom(w)
        app = make_app(w, runner=room)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "presence:team_room", "Kylie")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved: Kylie leaves the Team Room", notice(app))
            self.assertNotIn("assistant", members(w))
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertEqual(shown(app, "presence:team_room", "Kylie"), f"{acd.MARK} out")
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
            self.assertIn("assistant", members(w))
        self.assertEqual(room.calls, [["kick", "kylie"], ["add", "assistant", "kylie", "Kylie"]])

    async def test_the_lead_cannot_be_taken_out_of_the_room(self):
        w = F.edit_world()
        app = make_app(w, runner=never)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "presence:team_room", "Jarvis")
            words = (menu(app, "note") if isinstance(app.screen, acd.CellMenu) else text_of(app, "#strip"))
            self.assertIn("lead", words.replace("\n", " "))
            self.assertFalse(isinstance(app.screen, acd.CellMenu) and app.screen.spec.options)

    async def test_a_talk_rule_has_plain_choices_and_says_where_it_stands(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "talk:speaks", "Kylie")
            self.assertEqual(menu(app, "title"), "Kylie: speaks in the team room")
            self.assertEqual(menu_list(app).split("\n"), ["(o) yes   now", "( ) no"])
            self.assertIn("Where the rule stands today:", menu(app, "note"))
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("Kylie may not speak in the Team Room.", confirm)
            self.assertIn("recorded, not yet enforced", confirm)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("DRY RUN, nothing written", notice(app))
            self.assertIn("+ speaks_in_team_room = false", notice(app))
            await pilot.press("escape")
            await pilot.pause()
            await open_cell(app, pilot, "talk:takes_from", "Kylie")
            self.assertEqual([o.label for o in app.screen.spec.options], ["Jarvis", "job cards only", "nobody (Peter only)"])
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_armed_a_talk_rule_shows_in_its_cell_at_once(self):
        w = F.edit_world(armed=True)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "talk:takes_from", "Esther")
            pick(app, "cards")
            await pilot.press("enter")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("Saved: Esther now takes work only as job cards", " ".join(notice(app).split()))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(shown(app, "talk:takes_from", "Esther"), f"{acd.MARK} job cards only")
        self.assertTrue(tomllib.loads(w.edit.rules.read_text(encoding="utf-8"))["agents"]["tax_angel_esther"]["only_job_cards"])

    async def test_simba_keeps_answering_when_named_and_the_lead_cannot_be_muted(self):
        w = F.edit_world()
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "talk:answers", "Simba")
            self.assertEqual([o.disabled for o in app.screen.spec.options], [True, True])
            self.assertIn("kept as it is", menu_list(app))
            await pilot.press("escape")
            await pilot.pause()
            await open_cell(app, pilot, "talk:speaks", "Jarvis")
            no = next(o for o in app.screen.spec.options if o.token == "no")
            self.assertTrue(no.disabled)
            self.assertIn("leads the room", no.note)


class EveryAgentAtOnce(unittest.IsolatedAsyncioTestCase):
    async def test_enter_on_a_rows_name_lists_each_agents_before_and_after_and_a_dry_run_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "effort", 0)
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertEqual(menu(app, "title"), "Thinking effort: every agent")
            self.assertEqual(menu_list(app).split("\n"), ["( ) low effort", "(o) medium effort   now", "( ) high effort"])
            pick(app, "high")
            await pilot.press("enter")
            await pilot.pause()
            lines = [" ".join(x.split()) for x in menu(app, "confirm").split("\n")]
            for name in ("Jarvis", "Kylie", "Esther", "Wanna Buffet"):
                self.assertIn(f"{name}: medium effort -> high effort", lines)
            self.assertTrue(any(x.startswith("Simba: not changed (") and "locked" in x for x in lines), lines)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertEqual(len(app.last_results), 4)                          # one change per agent, none for Simba
            self.assertTrue(all(r.ok and r.dry for r in app.last_results))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_armed_it_changes_every_agent_it_applies_to_and_leaves_the_rest(self):
        w = F.edit_world(armed=True)
        simba = cfg(w, "simba").read_text(encoding="utf-8")
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "rung:codex", 0)
            self.assertEqual(menu(app, "title"), "3 ChatGPT (Codex): every agent".replace("3 C", "3  C"))
            pick(app, "gpt-5.4-mini")
            await pilot.press("enter")
            await pilot.pause()
            lines = [" ".join(x.split()) for x in menu(app, "confirm").split("\n")]
            self.assertIn("Kylie: off: skip this rung -> GPT 5.4 mini", lines)
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(all(r.ok and not r.dry for r in app.last_results), [r.message for r in app.last_results])
            for prof in ("orchestrator", "assistant", "tax_angel_esther", "wanna-buffet"):
                self.assertIn(CODEX, listed(w, prof))
                self.assertEqual(listed(w, prof).index(CODEX), 2)               # in its place: before NVIDIA
        self.assertEqual(cfg(w, "simba").read_text(encoding="utf-8"), simba)

    async def test_a_row_of_facts_has_nothing_to_change_for_anyone(self):
        w = F.edit_world()
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "last", 0)
            self.assertIsInstance(app.screen, acd.NoticePopup)
            self.assertIn("No agent can be changed on this row from the desk.", notice(app))

    async def test_s_shows_the_standard_ladder_before_and_after_first_and_a_dry_run_writes_nothing(self):
        w = F.edit_world()
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("s")
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.NoticePopup)
            words = " ".join(notice(app).split())
            self.assertIn("Put every agent on the standard ladder?", words)
            self.assertIn("1 the main model 2a Antigravity: Claude 2b Antigravity: Gemini 3 ChatGPT 4 NVIDIA Nemotron", words)
            for name in ("Jarvis", "Kylie", "Esther", "Wanna Buffet"):
                self.assertIn(name, words)
            self.assertIn("Simba is left out: no backups by design", words)
            self.assertNotIn("Simba - before", words)
            self.assertIn("- before: 2a Opus 5.5 · 2b Gemini Flash", words)
            self.assertIn("adds rung 3, ChatGPT: GPT 5.4 mini", words)
            self.assertIn("DRY RUN", words)
            self.assertEqual(F.tree_hashes(w.root), before, "showing the plan wrote something")
            await pilot.press("y")
            await settle(app, pilot)
            self.assertEqual(len(app.last_results), 4)
            self.assertTrue(all(r.ok and r.dry for r in app.last_results))
        self.assertEqual(F.tree_hashes(w.root), before)

    async def test_s_then_no_changes_nothing_on_an_armed_desk_and_yes_sets_the_ladder(self):
        w = F.edit_world(armed=True)
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("s")
            await settle(app, pilot)
            await pilot.press("escape")                                         # no
            await settle(app, pilot)
            self.assertEqual(F.tree_hashes(w.root), before)
            await pilot.press("s")
            await settle(app, pilot)
            await pilot.press("y")
            await settle(app, pilot)
            self.assertTrue(all(r.ok and not r.dry for r in app.last_results), [r.message for r in app.last_results])
            for prof in ("orchestrator", "assistant", "tax_angel_esther", "wanna-buffet"):
                self.assertEqual([D.rung_of(*p) for p in listed(w, prof)], list(D.LADDER_KEYS), prof)
            await pilot.press("escape")
            await settle(app, pilot)
            await pilot.press("s")
            await settle(app, pilot)
            self.assertIn("Every agent is already on the standard ladder", notice(app))


if __name__ == "__main__":
    unittest.main()
