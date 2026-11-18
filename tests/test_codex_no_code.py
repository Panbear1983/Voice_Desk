"""Offline tests: voice finds a Codex window that herdr shows NO session code (2026-11-18 brief, part 2).

herdr reports no session code for the open Codex window ("Codex (Dolly)"), so a mounted Codex session never found its
window and voice Codex always went to a separate background session. Now (live_session.bind_codeless):

  * with a session MOUNTED, the ONE open window of that service that shows no code is used for it, when the mounted
    session's own transcript exists and was written in the last day, and (when both say) in the window's folder;
  * two windows of the service: never a guess;
  * nothing mounted: as before, a window with no code is not used.

The mount menu, the table cell and the "type a code" yes / no box say which way it went and why.

Fakes only: a fake herdr script that answers `pane list` from a file and plays a fake Codex window (it appends the
answer to a throw-away transcript); fake Codex transcripts in a throw-away folder. Nothing is typed into a real window.

    .venv/bin/python -m unittest tests.test_codex_no_code -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests import test_acd_mount as TM  # noqa: E402
from tests import test_acd_table as T  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_mount as M  # noqa: E402
import live_session as L  # noqa: E402
import settings as S  # noqa: E402

CODE = "01a1d2df-aaaa-4bbb-8ccc-000000000001"          # a made-up Codex session code
OTHER = "01a1d2df-aaaa-4bbb-8ccc-000000000002"
HOME = str(Path.home())


def rollout(root: Path, code: str, age: float, cwd: str = HOME) -> Path:
    """A Codex transcript (rollout file) for this code, started in `cwd`, last written `age` seconds ago."""
    p = root / "2026" / "11" / "18" / f"rollout-2026-11-18T09-00-00-{code}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = {"type": "session_meta", "payload": {"id": code, "cwd": cwd} if cwd else {"id": code}}
    p.write_text(json.dumps(meta) + "\n")
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def codex_pane(pid="wV:p4", code="", cwd=HOME, label="Codex (Dolly)"):
    return TM.pane(pid, "codex", code, label=label, cwd=cwd)


class SessionsFolder(unittest.TestCase):
    """Every test gets its own throw-away Codex sessions folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vd-codex-nocode-")
        self.sessions = Path(self._tmp.name) / "codex-sessions"
        self.sessions.mkdir()
        self._p = patch.object(S, "CODEX_SESSIONS_DIR", self.sessions)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()


# ---- the rule itself ------------------------------------------------------------------------------------------------
class TheRule(SessionsFolder):
    def one(self, code="", cwd=HOME):
        return [("wV:p4", "idle", code, cwd)]

    def test_a_recent_transcript_in_the_same_folder_binds_the_one_window_with_no_code(self):
        rollout(self.sessions, CODE, 180)
        yes, why = L.bind_codeless("codex", CODE, self.one())
        self.assertTrue(yes, why)
        self.assertIn("only Codex window open", why)
        self.assertIn("3 min ago", why)

    def test_nothing_mounted_never_binds(self):
        rollout(self.sessions, CODE, 10)
        self.assertEqual(L.bind_codeless("codex", "", self.one()), (False, ""))

    def test_two_windows_are_never_a_guess(self):
        rollout(self.sessions, CODE, 10)
        yes, why = L.bind_codeless("codex", CODE, self.one() + [("wV:p9", "idle", "", HOME)])
        self.assertFalse(yes)
        self.assertIn("does not guess", why)
        yes, why = L.bind_codeless("codex", CODE, self.one() + [("wV:p9", "idle", OTHER, HOME)])
        self.assertFalse(yes, "one window with no code beside one with another code: still two windows")
        self.assertIn("2 Codex windows", why)

    def test_a_window_that_shows_a_code_is_not_this_rules_business(self):
        rollout(self.sessions, CODE, 10)
        self.assertEqual(L.bind_codeless("codex", CODE, self.one(code=OTHER)), (False, ""))

    def test_no_transcript_on_this_mac_never_binds(self):
        yes, why = L.bind_codeless("codex", CODE, self.one())
        self.assertFalse(yes)
        self.assertIn("not found on this Mac", why)

    def test_an_old_transcript_never_binds(self):
        rollout(self.sessions, CODE, L.RECENT_SEC + 60)
        yes, why = L.bind_codeless("codex", CODE, self.one())
        self.assertFalse(yes)
        self.assertIn("last written 24 h ago, longer ago than 24 h", why)
        rollout(self.sessions, CODE, 5 * 86400)
        self.assertIn("5 days ago", L.bind_codeless("codex", CODE, self.one())[1])

    def test_a_transcript_started_in_another_folder_is_not_this_window(self):
        rollout(self.sessions, CODE, 10, cwd=HOME + "/GitHub/Somewhere")
        yes, why = L.bind_codeless("codex", CODE, self.one())
        self.assertFalse(yes)
        self.assertIn("not that session", why)

    def test_the_same_folder_through_a_link_and_an_unknown_folder_still_bind(self):
        real = Path(self._tmp.name) / "real"
        real.mkdir()
        (Path(self._tmp.name) / "link").symlink_to(real)
        rollout(self.sessions, CODE, 10, cwd=str(real))
        self.assertTrue(L.bind_codeless("codex", CODE, self.one(cwd=str(Path(self._tmp.name) / "link")))[0])
        self.assertTrue(L.bind_codeless("codex", CODE, self.one(cwd=""))[0], "herdr names no folder: not checked")
        rollout(self.sessions, CODE, 10, cwd="")
        self.assertTrue(L.bind_codeless("codex", CODE, self.one())[0], "the transcript names no folder: not checked")


# ---- the voice line: LiveBrain finds the window and a whole turn goes through it ------------------------------------
FAKE_HERDR = r'''#!{python}
import json, sys
from pathlib import Path
here = Path({root!r})
args = sys.argv[1:]
if args[:2] == ["pane", "list"]:
    print((here / "panes.json").read_text())
elif args[:2] == ["pane", "send-text"]:
    (here / "typed").write_text(args[2] + " " + args[3])
elif args[:2] == ["pane", "send-keys"]:
    pane, said = (here / "typed").read_text().split(" ", 1)
    events = [{{"type": "response_item", "payload": {{"type": "message", "role": "user",
                                                    "content": [{{"type": "input_text", "text": said}}]}}}},
              {{"type": "event_msg", "payload": {{"type": "task_complete", "last_agent_message": "Codex heard you."}}}}]
    with open((here / "transcript").read_text(), "a") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in events))
'''


class TheVoiceLine(SessionsFolder):
    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name)
        herdr = self.root / "herdr"
        herdr.write_text(FAKE_HERDR.format(python=sys.executable, root=str(self.root)))
        herdr.chmod(0o755)
        self._h = patch.object(S, "HERDR_BIN", str(herdr))
        self._h.start()
        self.pin = S.STATE_DIR / "codex_live_session"
        S.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.saved = {n: getattr(L, n) for n in ("ENTER_DELAY_SEC", "RETRY_ENTER_SEC", "DELIVERY_TIMEOUT_SEC")}
        L.ENTER_DELAY_SEC, L.RETRY_ENTER_SEC, L.DELIVERY_TIMEOUT_SEC = 0, 1, 3

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(L, n, v)
        self.pin.unlink(missing_ok=True)       # a pin left behind would be a mounted session for later tests
        self._h.stop()
        super().tearDown()

    def windows(self, *panes):
        (self.root / "panes.json").write_text(json.dumps({"result": {"panes": list(panes)}}))

    def test_a_mounted_code_finds_the_one_window_with_no_code_and_the_turn_goes_through(self):
        path = rollout(self.sessions, CODE, 600)
        (self.root / "transcript").write_text(str(path))
        self.windows(codex_pane(), TM.pane("wV:p1", "claude", TM.OPEN_A))
        self.pin.write_text(CODE + "\n")
        brain = L.LiveBrain("codex")
        self.assertEqual(brain.pane(), ("wV:p4", "idle", CODE))
        import brain as brain_mod
        self.assertIsInstance(brain_mod.Brain().backend("codex"), L.LiveBrain)
        self.assertEqual(brain.ask("are you there"), "Codex heard you.")
        self.assertEqual((self.root / "typed").read_text(), "wV:p4 [voice] are you there")

    def test_nothing_mounted_keeps_todays_behaviour(self):
        rollout(self.sessions, CODE, 10)
        self.windows(codex_pane())
        self.assertIsNone(L.LiveBrain("codex").pane())
        import brain as brain_mod
        from codex_brain import CodexBrain
        self.assertIsInstance(brain_mod.Brain().backend("codex"), CodexBrain)

    def test_never_a_guess_between_two_windows_and_nothing_is_typed(self):
        rollout(self.sessions, CODE, 10)
        self.windows(codex_pane(), codex_pane("wV:p9", label="Codex two"))
        self.pin.write_text(CODE + "\n")
        brain = L.LiveBrain("codex")
        self.assertIsNone(brain.pane())
        self.assertIn("The mounted Codex session is not open", brain.ask("hello"))
        self.assertFalse((self.root / "typed").exists(), "nothing may be typed anywhere")

    def test_an_old_mounted_session_does_not_take_the_window(self):
        rollout(self.sessions, CODE, 3 * 86400)
        self.windows(codex_pane())
        self.pin.write_text(CODE + "\n")
        brain = L.LiveBrain("codex")
        self.assertIsNone(brain.pane())
        self.assertIn("The mounted Codex session is not open", brain.ask("hello"))
        self.assertFalse((self.root / "typed").exists())

    def test_a_window_that_shows_its_code_still_goes_by_the_code(self):
        rollout(self.sessions, CODE, 10)
        rollout(self.sessions, OTHER, 10)
        self.windows(codex_pane(code=OTHER))
        self.pin.write_text(CODE + "\n")
        self.assertIsNone(L.LiveBrain("codex").pane(), "another session's window is never used")
        self.pin.write_text(OTHER + "\n")
        self.assertEqual(L.LiveBrain("codex").pane(), ("wV:p4", "idle", OTHER))


# ---- the desk: mount menu, cell and the "type a code" box say it --------------------------------------------------
class TheDesk(SessionsFolder):
    def tearDown(self):
        self.assertFalse((self.w.root / "FORBIDDEN").exists(), "the desk typed into a window")
        super().tearDown()

    def codex(self, w):
        snap = D.collect(w.paths, yaml_loader=F.lines_loader, now=w.now, desk=E.record(w.edit))
        app = T.make_app(w)
        app.snapshot = snap
        return app, next(o for o in snap.others if o.name == "Codex")

    def test_bound_the_menu_marks_the_window_and_the_cell_is_green_with_the_reason(self):
        self.w = w = TM.world(pin=None)
        rollout(self.sessions, CODE, 300)
        (w.pins / "codex_live_session").write_text(CODE + "\n")
        v = M.view(w.pins, w.herdr, "codex")
        self.assertEqual((v.state, v.bound, v.window.pane), ("open", True, "wV:p4"))
        app, codex = self.codex(w)
        spec = app.spec_mount(codex)
        line = next(o for o in spec.options if o.token == f"mount|{CODE}")
        self.assertTrue(line.now and not line.disabled, "the window is the mounted session's: marked, choosable")
        self.assertIn('"Codex (Dolly)" (wV:p4): voice uses this window for the mounted session 01a1d2df', line.label)
        self.assertIn("only Codex window open", line.label)
        self.assertFalse(any("its window is not open" in o.label for o in spec.options))
        self.assertIn("window wV:p4", " ".join(spec.describe(f"mount|{CODE}")))
        cell = acd.cell_at("mount", codex)
        self.assertEqual((cell.status, cell.words), (D.GREEN, "01a1d2df open"))
        self.assertIn("herdr shows that window no session code", " ".join(cell.detail))

    def test_not_bound_the_menu_and_the_cell_say_why(self):
        self.w = w = TM.world(pin=None)
        rollout(self.sessions, CODE, 3 * 86400)
        (w.pins / "codex_live_session").write_text(CODE + "\n")
        app, codex = self.codex(w)
        spec = app.spec_mount(codex)
        line = next(o for o in spec.options if o.token == "nocode|wV:p4")
        self.assertTrue(line.disabled)
        self.assertIn("not used for the mounted session 01a1d2df", line.label)
        self.assertIn("last written 3 days ago", line.label)
        self.assertTrue(next(o for o in spec.options if o.token == f"mount|{CODE}").now)
        cell = acd.cell_at("mount", codex)
        self.assertEqual(cell.status, D.YELLOW)
        self.assertIn("The Codex window with no session code is not used for it", " ".join(cell.detail))

    def test_two_codex_windows_the_menu_says_it_never_guesses(self):
        self.w = w = TM.world(pin=None)
        TM.open_windows(w, codex_pane(), codex_pane("wV:p9", label="Codex two"))
        rollout(self.sessions, CODE, 10)
        (w.pins / "codex_live_session").write_text(CODE + "\n")
        app, codex = self.codex(w)
        labels = [o.label for o in app.spec_mount(codex).options if o.token.startswith("nocode|")]
        self.assertEqual(len(labels), 2)
        self.assertTrue(all("does not guess" in x for x in labels), labels)
        self.assertEqual(acd.cell_at("mount", codex).status, D.YELLOW)


class TheTypedCode(unittest.IsolatedAsyncioTestCase):
    """The "type or paste a session code..." box: its yes / no line says the window with no code will be used."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vd-codex-nocode-")
        self.sessions = Path(self._tmp.name)
        self._p = patch.object(S, "CODEX_SESSIONS_DIR", self.sessions)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()
        self.assertFalse((self.w.root / "FORBIDDEN").exists(), "the desk typed into a window")

    async def test_a_typed_code_that_will_use_the_window_says_so_before_the_yes(self):
        from textual.widgets import Input
        self.w = w = TM.world(armed=True, pin=None)
        rollout(self.sessions, CODE, 60)
        app = T.make_app(w)
        async with app.run_test(size=(220, 70)) as pilot:
            await pilot.pause()
            await T.show(app, pilot)
            await T.open_cell(app, pilot, "mount", "Codex")
            T.pick(app, M.TYPE)
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#box-input", Input).value = CODE
            await pilot.press("enter")
            await pilot.pause()
            words = " ".join(T.notice(app).split())
            self.assertIn("No window shows this code, but voice will use window wV:p4 \"Codex (Dolly)\"", words)
            self.assertNotIn("stays yellow", words)
            await pilot.press("y")
            await T.settle(app, pilot)
            await pilot.press("escape")
            await T.settle(app, pilot)
            self.assertIn("01a1d2df open", T.shown(app, "mount", "Codex"))
        self.assertEqual(TM.pin(w, "codex"), (CODE + "\n").encode())


if __name__ == "__main__":
    unittest.main()
