"""Offline tests: mount ONE open terminal session per service (Claude, Gemini, Codex) onto voice, from the Agent
Control Desk (acd_mount.py, the table's "Mounted session" row, live_session / brain: no silent fallback).

Fakes only: a fake herdr that answers `pane list` from a file the test writes and REFUSES anything else (it
records the attempt, and every test checks nothing was typed), fake transcripts, and a throw-away copy of the
voice line's folder for the pins. No real window, no model, no microphone.

    .venv/bin/python -m unittest tests.test_acd_mount -v
"""
import dataclasses
import datetime as dt
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests.test_acd_table import make_app, menu, menu_list, notice, open_cell, pick, settle, show, shown  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_mount as M  # noqa: E402
import settings as S  # noqa: E402
from textual.widgets import Input  # noqa: E402

WHEN = dt.datetime(2026, 11, 18, 13, 0, 0)
OPEN_A = "aaaaaaaa-1111-4111-8111-111111111111"       # a Claude window that is open
OPEN_B = "bbbbbbbb-2222-4222-8222-222222222222"       # another open Claude window
GEM = "cccccccc-3333-4333-8333-333333333333"          # the open Gemini window
GONE = "dddddddd-4444-4444-8444-444444444444"         # an old Claude session whose window is closed
OLD_PIN = b"dddddddd-4444-4444-8444-444444444444\n"   # today's stale pin, exactly as the voice line wrote it

FAKE_HERDR = r'''#!{python}
import sys
from pathlib import Path
here = Path({root!r})
args = sys.argv[1:]
if args[:2] == ["pane", "list"]:
    print((here / "panes.json").read_text())
    sys.exit(0)
with open(here / "FORBIDDEN", "a") as fh:      # the desk must only ever ask, never type or press keys
    fh.write(" ".join(args) + "\n")
sys.exit(3)
'''


HOME = str(Path.home())


def pane(pid, agent, code="", status="idle", label="", cwd=HOME):
    p = {"pane_id": pid, "agent": agent, "agent_status": status, "cwd": cwd, "foreground_cwd": cwd, "label": label}
    if code:
        p["agent_session"] = {"agent": agent, "kind": "id", "value": code}
    return p


def open_windows(w, *panes):
    (w.root / "panes.json").write_text(json.dumps({"result": {"panes": list(panes)}}))


def transcript(code: str, age: float) -> None:
    """A Claude conversation file for this code, last changed `age` seconds ago."""
    p = Path(S.CLAUDE_PROJECTS_DIR) / "-Users-peter" / f"{code}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"type": "user"}\n')
    t = time.time() - age
    os.utime(p, (t, t))


def world(armed: bool = False, pin: bytes = OLD_PIN):
    """The desk's fake world, plus a COPY of the voice line's folder holding the pins, and the fake herdr."""
    w = F.with_grid(F.edit_world(armed=armed))
    pins = w.root / "voice_desk_copy"
    pins.mkdir()
    if pin is not None:
        (pins / "claude_live_session").write_bytes(pin)
    herdr = w.root / "herdr"
    herdr.write_text(FAKE_HERDR.format(python=sys.executable, root=str(w.root)))
    herdr.chmod(0o755)
    open_windows(w, pane("wV:p1", "claude", OPEN_A, label="Agent Control Desk"),
                 pane("wV:pF", "claude", OPEN_B, status="working", label="Team Room Config",
                      cwd=HOME + "/GitHub/Team_Room"),
                 pane("wV:p5", "agy", GEM, label="Hello Gemini"),
                 pane("wV:p4", "codex", "", label="Codex (Dolly)"),              # herdr gives no code for it
                 pane("wH:pR", "hermes", "", label="Jarvis"))
    transcript(OPEN_A, 30)
    transcript(OPEN_B, 3 * 3600)
    transcript(GONE, 20 * 86400)
    w.pins, w.herdr = pins, str(herdr)
    w.edit = dataclasses.replace(w.edit, pins=pins, herdr=str(herdr))
    w.paths = dataclasses.replace(w.paths, mounts=lambda: M.views(pins, str(herdr)))
    return w


def pin(w, target="claude"):
    p = w.pins / f"{target}_live_session"
    return p.read_bytes() if p.exists() else None


class Reading(unittest.TestCase):
    def tearDown(self):
        self.assertFalse((self.w.root / "FORBIDDEN").exists(), "the desk typed into a window")

    def test_each_service_lists_its_open_windows_newest_first_and_the_light_follows_the_pin(self):
        self.w = w = world()
        v = M.views(w.pins, w.herdr)
        self.assertEqual(v["claude"].state, "closed")                      # the stale pin: mounted, not open
        self.assertEqual([s.code for s in v["claude"].sessions], [OPEN_A, OPEN_B])
        self.assertEqual(v["claude"].sessions[1].folder, HOME + "/GitHub/Team_Room")
        self.assertEqual(v["gemini"].state, "none")
        self.assertEqual([s.code for s in v["gemini"].sessions], [GEM])
        self.assertEqual([(s.pane, s.code) for s in v["codex"].sessions], [("wV:p4", "")])
        (w.pins / "claude_live_session").write_text(OPEN_B + "\n")
        v = M.view(w.pins, w.herdr, "claude")
        self.assertEqual((v.state, v.window.pane), ("open", "wV:pF"))
        self.assertIn("Team Room Config", M.session_label(v.window))
        self.assertIn("3 h ago", M.session_label(v.window))

    def test_a_herdr_that_cannot_be_asked_is_said_and_never_taken_for_open(self):
        self.w = w = world()
        (w.root / "panes.json").write_text("not json")
        v = M.view(w.pins, w.herdr, "claude")
        self.assertEqual(v.state, "closed")
        self.assertIn("could not be read", v.error)
        self.assertEqual(M.view(w.pins, str(w.root / "no-herdr"), "gemini").state, "none")


class Writing(unittest.TestCase):
    def tearDown(self):
        self.assertFalse((self.w.root / "FORBIDDEN").exists(), "the desk typed into a window")

    def test_a_dry_desk_shows_the_change_and_writes_nothing(self):
        self.w = w = world(armed=False)
        before = F.tree_hashes(w.root)
        for r in (M.mount(w.edit, "claude", OPEN_A, now=WHEN), M.unmount(w.edit, "claude", now=WHEN)):
            self.assertTrue(r.ok and r.dry, r.message)
        self.assertEqual(r.lines, [f"- mounted: {GONE}", "+ nothing mounted"])
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_mount_backs_up_writes_reads_back_and_u_restores_the_old_pin_byte_for_byte(self):
        self.w = w = world(armed=True)
        os.chmod(w.pins / "claude_live_session", 0o644)
        r = M.mount(w.edit, "claude", f"  {OPEN_A} ", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(pin(w), (OPEN_A + "\n").encode())
        self.assertEqual(r.lines, [f"- mounted: {GONE}", f"+ mounted: {OPEN_A}"])
        backups = list((w.edit.state / "pin-backups").iterdir())
        self.assertEqual([b.read_bytes() for b in backups], [OLD_PIN])
        last = E.last_undoable(w.edit)
        self.assertEqual((last["kind"], last["target"], last["did"]), ("mount", "claude", "mount"))
        self.assertIn("Already".lower(), M.mount(w.edit, "claude", OPEN_A, now=WHEN).message.lower())
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=3))
        self.assertTrue(u.ok and not u.dry, u.message)
        self.assertEqual(pin(w), OLD_PIN, "undo must put the old pin back byte for byte")
        self.assertEqual(os.stat(w.pins / "claude_live_session").st_mode & 0o777, 0o644)
        self.assertIsNone(E.last_undoable(w.edit))

    def test_odd_bytes_and_a_missing_pin_come_back_exactly(self):
        self.w = w = world(armed=True, pin=b"  " + GONE.encode() + b"\r\n\r\n")
        odd = pin(w)
        M.mount(w.edit, "claude", OPEN_B, now=WHEN)
        E.undo(w.edit, now=WHEN + dt.timedelta(seconds=1))
        self.assertEqual(pin(w), odd)
        r = M.mount(w.edit, "gemini", GEM, now=WHEN + dt.timedelta(seconds=2))           # no Gemini pin before
        self.assertTrue(r.ok, r.message)
        self.assertEqual(pin(w, "gemini"), (GEM + "\n").encode())
        E.undo(w.edit, now=WHEN + dt.timedelta(seconds=3))
        self.assertIsNone(pin(w, "gemini"), "there was no pin before, so there is none after the undo")

    def test_unmount_and_its_undo(self):
        self.w = w = world(armed=True)
        r = M.unmount(w.edit, "claude", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertIsNone(pin(w))
        self.assertIn("Nothing is mounted", M.unmount(w.edit, "claude", now=WHEN).message)
        E.undo(w.edit, now=WHEN + dt.timedelta(seconds=1))
        self.assertEqual(pin(w), OLD_PIN)

    def test_two_changes_in_one_second_undo_one_at_a_time(self):
        self.w = w = world(armed=True)
        M.mount(w.edit, "claude", OPEN_A, now=WHEN)
        M.mount(w.edit, "claude", OPEN_B, now=WHEN)
        E.undo(w.edit, now=WHEN)
        self.assertEqual(pin(w), (OPEN_A + "\n").encode())
        E.undo(w.edit, now=WHEN)
        self.assertEqual(pin(w), OLD_PIN)

    def test_undo_never_writes_over_a_pin_changed_behind_the_desks_back(self):
        self.w = w = world(armed=True)
        M.mount(w.edit, "claude", OPEN_A, now=WHEN)
        (w.pins / "claude_live_session").write_text(OPEN_B + "\n")
        u = E.undo(w.edit, now=WHEN + dt.timedelta(seconds=1))
        self.assertFalse(u.ok)
        self.assertIn("was changed since", u.message)
        self.assertEqual(pin(w), (OPEN_B + "\n").encode())

    def test_refusals_write_nothing(self):
        self.w = w = world(armed=True)
        before = F.tree_hashes(w.root)
        for r in (M.mount(w.edit, "claude", "", now=WHEN), M.mount(w.edit, "claude", "../../etc/passwd", now=WHEN),
                  M.mount(w.edit, "claude", "a b c d", now=WHEN), M.mount(w.edit, "jarvis", OPEN_A, now=WHEN),
                  M.mount(dataclasses.replace(w.edit, pins=None), "claude", OPEN_A, now=WHEN)):
            self.assertFalse(r.ok, r.message)
            self.assertTrue(r.message.startswith("Not changed."), r.message)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_a_write_that_does_not_read_back_puts_the_old_pin_back(self):
        self.w = w = world(armed=True)
        real = M._put

        def bad(path, data, mode):
            real(path, data + b"garbled" if data != OLD_PIN else data, mode)
        with patch.object(M, "_put", bad):
            r = M.mount(w.edit, "claude", OPEN_A, now=WHEN)
        self.assertFalse(r.ok)
        self.assertIn("put back exactly as it was", r.message)
        self.assertEqual(pin(w), OLD_PIN)
        self.assertIsNone(E.last_undoable(w.edit))


class TheTable(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        self.assertFalse((self.w.root / "FORBIDDEN").exists(), "the desk typed into a window")

    async def test_the_row_shows_each_services_mount_with_its_light_and_hermes_agents_stay_blank(self):
        self.w = w = world()
        snap = D.collect(w.paths, yaml_loader=F.lines_loader, now=w.now, desk=E.record(w.edit))
        rows = [r.key for r in acd.rows_of(snap)]
        self.assertEqual(rows[rows.index("names") + 1], "mount")
        claude = next(o for o in snap.others if o.name == "Claude")
        cell = acd.cell_at("mount", claude)
        self.assertEqual((cell.status, cell.words), (D.YELLOW, "dddddddd not open"))
        self.assertIn("The mounted Claude session is not open", " ".join(cell.detail))
        self.assertIn("starts no other session", " ".join(cell.detail))
        self.assertTrue(any("the mounted Claude session is not open" in x.text for x in snap.warnings))
        gem = acd.cell_at("mount", next(o for o in snap.others if o.name == "Gemini"))
        self.assertEqual((gem.status, gem.words), (D.GREY, "none mounted"))
        self.assertIn("(1 open now)", gem.detail[0])
        jarvis = next(v for v in snap.agents if v.name == "Jarvis")
        self.assertTrue(acd.cell_of(acd.Row("mount", "Mounted session"), jarvis).blank)
        (w.pins / "claude_live_session").write_text(OPEN_A + "\n")
        snap = D.collect(w.paths, yaml_loader=F.lines_loader, now=w.now, desk=E.record(w.edit))
        cell = acd.cell_at("mount", next(o for o in snap.others if o.name == "Claude"))
        self.assertEqual((cell.status, cell.words), (D.GREEN, "aaaaaaaa open"))
        self.assertIn("wV:p1", cell.detail[0])
        self.assertFalse(any("mounted" in x.text for x in snap.warnings))

    async def test_without_the_voice_line_the_row_is_not_there(self):
        self.w = w = world()
        snap = D.collect(dataclasses.replace(w.paths, mounts=None), yaml_loader=F.lines_loader, now=w.now)
        self.assertNotIn("mount", [r.key for r in acd.rows_of(snap)])

    async def test_pick_an_open_session_then_u_puts_the_old_pin_back_byte_for_byte(self):
        self.w = w = world(armed=True)
        app = make_app(w)
        async with app.run_test(size=(220, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            self.assertIn("dddddddd not open", shown(app, "mount", "Claude"))
            await open_cell(app, pilot, "mount", "Claude")
            self.assertEqual(menu(app, "title"), "Claude: the one session voice goes to")
            note = " ".join(menu(app, "note").split())
            self.assertIn("Sessions in the desktop apps are not listed and cannot be mounted", note)
            lines = menu_list(app).split("\n")
            self.assertTrue(lines[0].startswith("(o) dddddddd  mounted, its window is not open"), lines)
            self.assertIn("aaaaaaaa  Agent Control Desk  ~  just now", lines[1])
            self.assertIn("bbbbbbbb  Team Room Config  ~/GitHub/Team_Room  3 h ago", lines[2])
            self.assertIn("type or paste a session code...", lines[3])
            self.assertIn("unmount: nothing mounted", lines[4])
            pick(app, f"mount|{OPEN_A}")
            await pilot.press("enter")
            await pilot.pause()
            confirm = " ".join(menu(app, "confirm").split())
            self.assertIn("WRITTEN", confirm)
            self.assertIn(f"Voice Claude is mounted on session {OPEN_A} (window wV:p1", confirm)
            self.assertIn("starts nothing else", confirm)
            self.assertEqual(pin(w), OLD_PIN, "nothing is written before the second Enter")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn(f"Saved: voice Claude is mounted on session {OPEN_A}", notice(app))
            await pilot.press("escape")
            await settle(app, pilot)
            self.assertEqual(pin(w), (OPEN_A + "\n").encode())
            self.assertIn("aaaaaaaa open", shown(app, "mount", "Claude"))
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
        self.assertEqual(pin(w), OLD_PIN)

    async def test_type_a_code_that_is_not_open_is_mounted_yellow_after_one_yes(self):
        self.w = w = world(armed=True, pin=None)
        app = make_app(w)
        async with app.run_test(size=(220, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            self.assertIn("none mounted", shown(app, "mount", "Codex"))
            await open_cell(app, pilot, "mount", "Codex")
            self.assertIn('"Codex (Dolly)" (wV:p4): this Codex window shows herdr no session code, so voice cannot '
                          'mount it yet', menu_list(app))
            self.assertNotIn("no Codex window", menu_list(app))           # it is open: it just cannot be mounted
            pick(app, M.TYPE)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.AskPopup)
            app.screen.query_one("#box-input", Input).value = "not a code!"
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("does not look like a session code", notice(app))
            await pilot.press("enter")
            await pilot.pause()
            await open_cell(app, pilot, "mount", "Codex")
            pick(app, M.TYPE)
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#box-input", Input).value = GONE
            await pilot.press("enter")
            await pilot.pause()
            words = " ".join(notice(app).split())
            self.assertIn("Mount this Codex session?", words)
            self.assertIn("No open Codex window has this code right now", words)
            self.assertIn("No Codex conversation with this code was found on this Mac either", words)
            self.assertIsNone(pin(w, "codex"), "nothing is saved before the yes")
            await pilot.press("y")
            await settle(app, pilot)
            await pilot.press("escape")                                    # Esc closes "Saved"
            await settle(app, pilot)
            self.assertIn("dddddddd not open", shown(app, "mount", "Codex"))
        self.assertEqual(pin(w, "codex"), (GONE + "\n").encode())

    async def test_a_dry_desk_from_the_screen_writes_nothing(self):
        self.w = w = world(armed=False)
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 70)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await open_cell(app, pilot, "mount", "Gemini")
            pick(app, f"mount|{GEM}")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("DRY RUN", menu(app, "confirm"))
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].dry)
        self.assertEqual(F.tree_hashes(w.root), before)


if __name__ == "__main__":
    unittest.main()
