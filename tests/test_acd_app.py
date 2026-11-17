"""Offline test of the Agent Control Desk's screen: it is drawn headless (no window on the screen) against the
fake world from acdfix, with the background reading switched off so the test decides when to read.

    .venv/bin/python -m unittest tests.test_acd_app -v
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_snapshot  # noqa: E402
import voiceedit  # noqa: E402
from textual.widgets import DataTable, OptionList, Static  # noqa: E402

AGY = F.AGY


def world():
    w = F.make_world()
    F.add_agent(w, "orchestrator", model="claude-sonnet-5-5",
                backups=[("claude-sonnet-5-5", AGY), ("gemini-3.8-flash", AGY)])
    F.add_agent(w, "assistant", backups=[("gemini-3.8-flash", AGY), ("nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia")])
    F.add_agent(w, "builder", effort="high", addons=())
    F.add_agent(w, "simba", model="x-ai/grok-4.20", service="openrouter", effort="none")
    F.log(w, "orchestrator", F.turn(w.now - 90, "j1", "claude-sonnet-5-5", "anthropic", "cli"),
          F.failed(w.now - 80, "j1", "claude-sonnet-5-5", "anthropic", "Individual quota reached. Resets in 82h1m."),
          F.fallback(w.now - 80, "j1", "claude-sonnet-5-5", "gemini-3.8-flash", AGY))
    F.log(w, "assistant", F.turn(w.now - 60, "k1", "claude-opus-5-5", "anthropic", "telegram"))
    F.log(w, "simba", F.turn(w.now - 60, "s1", "x-ai/grok-4.20", "openrouter", "bot_room"))
    return w


def edit_paths(w, launcher: bool = True) -> E.EditPaths:
    """Where a change would go for the read-only (JSON) world: only into its own throw-away folder."""
    root = w.root
    voice = voiceedit.EditPaths(toml=w.paths.agents_toml, history=root / "vh.jsonl", toml_backups=root / "vh-backups",
                                hermes_backups=root / "hermes-backups", profiles=w.paths.profiles,
                                claude_conf=root / "no-claude.conf", gemini_conf=None)
    return E.EditPaths(state=root / "acd-state", profiles=w.paths.profiles, rules=w.paths.rules,
                       restart_request=root / "helper" / "gateway_restart.request",
                       config_backups=root / "config-backups", voice=voice, launcher_armed=launcher,
                       members=w.paths.members, team_room_cmd=root / "no-such-team-room-command")


def make_app(w):
    return acd.Desk(collector=lambda: F.collect(w), autostart=False, edit=edit_paths(w), loader=F.loader, tester=None)


class NoWrites(unittest.TestCase):
    def test_the_desk_modules_never_open_a_file_for_writing_or_start_a_process(self):
        root = Path(acd.__file__).resolve().parent
        # the screen and the facts never write; every change lives in acd_edit.py, which never starts a process
        # or restarts anything itself (it only leaves a request file for the restart helper)
        for name in ("acd.py", "acd_data.py"):
            src = (root / name).read_text(encoding="utf-8")
            for forbidden in ("write_text", "write_bytes", "subprocess", "os.system", "os.remove", "unlink(",
                              "shutil", "launchctl", "requests", "urllib", "socket"):
                self.assertNotIn(forbidden, src, f"{name} must stay read-only: found {forbidden!r}")
        src = (root / "acd_edit.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "launchctl", "gateway restart", "requests", "urllib", "socket",
                          "kill("):
            self.assertNotIn(forbidden, src, f"acd_edit.py must never run or restart anything: found {forbidden!r}")


# ---- version two: the pop-ups, against a world whose settings files are real YAML text ----------------------
def edit_app(w, tester=None):
    return acd.Desk(collector=lambda: F.edit_collect(w), autostart=False, edit=w.edit, loader=F.lines_loader,
                    tester=tester)


def box(app, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).render())


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


class Popups(unittest.IsolatedAsyncioTestCase):
    async def test_restart_review_only_leaves_a_request_for_the_helper(self):
        for armed in (False, True):
            w = F.edit_world(armed=armed)
            E._append_history(w.edit, {"id": "a", "ts": w.now - 100, "kind": "addon", "ok": True, "needs_restart": True,
                                       "what": "the DLS add-on was switched off inside Jarvis"}) if armed else None
            before = F.tree_hashes(w.root)
            app = edit_app(w)
            async with app.run_test(size=(180, 58)) as pilot:
                await pilot.pause()
                app.apply_snapshot(app.collector())
                await pilot.pause()
                await pilot.press("r")
                await pilot.pause()
                self.assertIn("Restart review", box(app, "#box-title"))
                body = box(app, "#box-text")
                self.assertIn("waits for a quiet moment", body.replace("\n", " "))
                if armed:
                    self.assertIn("1 saved change is waiting for a restart", body)
                    self.assertIn("the DLS add-on was switched off inside Jarvis", body)
                else:
                    self.assertIn("No saved change is waiting for a restart", body)
                    self.assertIn("dry run", body)
                await pilot.press("y")
                await settle(app, pilot)
                if armed:
                    self.assertTrue(w.edit.restart_request.exists())
                    self.assertIn("Restart requested", box(app, "#box-text"))
                else:
                    self.assertIn("DRY RUN, nothing written", box(app, "#box-text"))
            if not armed:
                self.assertEqual(F.tree_hashes(w.root), before)
            else:
                written = set(F.tree_hashes(w.root)) - set(before)
                self.assertEqual(written, {"helper/gateway_restart.request"}, "only the helper's request file is new")

    async def test_undo_with_nothing_to_undo_says_so(self):
        app = edit_app(F.edit_world())
        async with app.run_test(size=(180, 58)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            self.assertIn("no change from the desk to undo", box(app, "#box-text"))


if __name__ == "__main__":
    unittest.main()
