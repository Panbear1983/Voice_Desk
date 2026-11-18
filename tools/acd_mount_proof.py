"""Proof for "mount one open terminal session per service" (brief 2026-11-18-acd-mount-one-session.md, "Done
means"): run the Agent Control Desk headless on a COPY of agents.toml and a COPY of ~/.voice_desk, mount a real open
Claude session there through the screen, then undo it, and show that the old pin comes back byte for byte.

    .venv/bin/python tools/acd_mount_proof.py --out <folder>

What it does:
  1. copies agents.toml and ~/.voice_desk into <out> (leaving out the phone token, the Telegram chat number, the
     transcript, logs and the desk's own record) and points the desk and the voice line's state there
     (VOICE_DESK_AGENTS, VOICE_DESK_STATE, ACD_STATE ...). The Hermes profiles are only read;
  2. herdr is reached through a GUARD script in <out>: `herdr pane list` is passed to the real herdr (read-only,
     so the picker shows the windows that are really open); ANY other herdr call (typing, keys) is refused and
     written to <out>/herdr-refused.log. The proof fails if that file exists;
  3. the desk is drawn headless, armed only inside <out> (its armed switch is the copy's): the "Mounted session" row
     is read, Claude's menu is opened, the newest open Claude window is mounted (the pin in the COPY changes), the
     light is read again, then u undoes it. The typed-code line is driven to its yes / no question and left with
     Esc. Pictures (SVG) of each step are saved;
  4. fingerprints: the copy's pin before and after the undo must be identical; the live pin and the live
     agents.toml must not change at all.

Nothing is typed into any window, nothing is played, the live listener is never asked. Report: <out>/proof.json.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
LIVE_STATE = HOME / ".voice_desk"
LIVE_TOML = HOME / "GitHub/Voice_Desk/agents.toml"
PRIVATE = ("receiver_token", "tg_dm_chat", "transcript.log", "*.log", "acd", "saved_drafts", "*.bundle", "sounds",
           "speech", "*.lock")
FAKE_CODE = "00000000-0000-4000-8000-000000000000"

GUARD = r'''#!/bin/sh
# Only "herdr pane list" (read-only) reaches the real herdr; anything else is refused and written down.
if [ "$1 $2" = "pane list" ] && [ $# -eq 2 ]; then
  exec {herdr} pane list
fi
echo "$@" >> {log}
exit 3
'''


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else "absent"


def prepare(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    copy = out / "voice_desk_copy"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(LIVE_STATE, copy, ignore=shutil.ignore_patterns(*PRIVATE))
    toml = out / "agents.toml"
    shutil.copy2(LIVE_TOML, toml)
    acd_state = out / "acd-state"
    if acd_state.exists():
        shutil.rmtree(acd_state)
    real = os.environ.get("VOICE_DESK_HERDR_BIN", "/opt/homebrew/bin/herdr")
    guard = out / "herdr-guard.sh"
    refused = out / "herdr-refused.log"
    refused.unlink(missing_ok=True)
    guard.write_text(GUARD.format(herdr=real, log=refused))
    guard.chmod(0o755)
    os.environ.update(VOICE_DESK_STATE=str(copy), VOICE_DESK_AGENTS=str(toml), ACD_STATE=str(acd_state),
                      ACD_CONFIG_BACKUPS=str(out / "backups"), ACD_RESTART_REQUEST=str(out / "restart.request"),
                      ACD_USAGE="off", VOICE_DESK_HERDR_BIN=str(guard), ACD_PINS=str(copy))
    return {"copy": copy, "toml": toml, "refused": refused}


async def drive(out: Path, report: dict) -> None:
    sys.path.insert(0, str(REPO))
    import acd
    import acd_data as D
    import acd_edit as E
    import acd_mount as M
    from textual.widgets import Input, OptionList, Static

    edit = E.EditPaths.default(launcher_armed=True)
    E.set_armed(edit, True)                              # the COPY's armed switch (ACD_STATE is in <out>)
    paths = D.Paths.default()
    reader = D.LogReader(paths.profiles)
    report["pins_dir"] = str(edit.pins)
    report["herdr"] = str(edit.herdr)
    assert str(edit.pins).startswith(str(out)) and str(paths.agents_toml).startswith(str(out)), "not a copy"
    pin = M.pin_file(edit.pins, "claude")
    report["copy_pin_before"] = sha(pin)

    def collector():
        return D.collect(paths, reader, desk=E.record(edit), watch=None)

    app = acd.Desk(collector=collector, autostart=False, edit=edit, loader=None, tester=None, watching=False)
    shots = out / "shots"
    shots.mkdir(exist_ok=True)

    def shot(name: str, title: str) -> None:
        (shots / f"{name}.svg").write_text(app.export_screenshot(title=title), encoding="utf-8")

    def table_screen():
        return next(s for s in reversed(app.screen_stack) if isinstance(s, acd.TableScreen))

    def cell(name: str) -> str:
        screen = table_screen()
        t = screen.query_one("#table")
        r = next(i for i, row in enumerate(screen.rows) if row.key == "mount")
        c = next(i for i, col in enumerate(screen.cols) if col.name == name) + 1
        return str(t.get_cell_at((r, c)))

    def strip() -> str:
        return " ".join(str(table_screen().query_one("#strip", Static).render()).split())

    def menu_rows() -> list:
        lst = app.screen.query_one("#menu-list", OptionList)
        return [str(lst.get_option_at_index(i).prompt) for i in range(lst.option_count)]

    def popup() -> str:
        return " ".join(" ".join(str(w.render()) for name in ("box-title", "box-note", "box-text")
                                 for w in app.screen.query(f"#{name}")).split())

    async def settle(pilot):
        for _ in range(3):
            await app.workers.wait_for_complete()
            await pilot.pause()

    async with app.run_test(size=(230, 72)) as pilot:
        await pilot.pause()
        app.apply_snapshot(await asyncio.to_thread(collector))
        await pilot.pause()
        report["warnings"] = [w.text for w in app.snapshot.warnings]
        report["cells_before"] = {n: cell(n) for n in ("Claude", "Gemini", "Codex")}
        assert table_screen().go("mount", "Claude")
        await pilot.pause()
        report["strip_before"] = strip()
        shot("1-table", "Mounted session row, as read")
        await pilot.press("enter")
        await pilot.pause()
        report["claude_menu"] = menu_rows()
        report["claude_menu_note"] = " ".join(str(app.screen.query_one("#menu-note", Static).render()).split())
        shot("2-claude-menu", "Claude: the one session voice goes to")
        spec = app.screen.spec
        choice = next((o for o in spec.options if o.token.startswith("mount|") and not o.now and not o.disabled), None)
        if choice is None:
            report["skipped_mount"] = "no open Claude window with a session code to mount"
            await pilot.press("escape")
        else:
            report["mounted_label"] = choice.label
            app.screen.query_one("#menu-list", OptionList).highlighted = spec.options.index(choice)
            await pilot.press("enter")
            await pilot.pause()
            report["confirm"] = " ".join(str(app.screen.query_one("#menu-confirm", Static).render()).split())
            shot("3-confirm", "Claude: mount, the yes line")
            await pilot.press("enter")
            await settle(pilot)
            report["saved"] = popup()
            shot("4-saved", "Saved")
            await pilot.press("enter")
            await settle(pilot)
            app.apply_snapshot(await asyncio.to_thread(collector))
            await pilot.pause()
            report["copy_pin_mounted"] = sha(pin)
            report["cell_after_mount"] = cell("Claude")
            assert table_screen().go("mount", "Claude")
            await pilot.pause()
            report["strip_after_mount"] = strip()
            shot("5-mounted", "Claude mounted (in the copy)")
            await pilot.press("u")
            await pilot.pause()
            report["undo_question"] = popup()
            await pilot.press("y")
            await settle(pilot)
            report["undone"] = popup()
            await pilot.press("enter")
            await settle(pilot)
            app.apply_snapshot(await asyncio.to_thread(collector))
            await pilot.pause()
            report["cell_after_undo"] = cell("Claude")
            shot("6-undone", "After u")
        assert table_screen().go("mount", "Codex")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        report["codex_menu"] = menu_rows()
        shot("7-codex-menu", "Codex: the one session voice goes to")
        tokens = [o.token for o in app.screen.spec.options]
        app.screen.query_one("#menu-list", OptionList).highlighted = tokens.index(M.TYPE)
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#box-input", Input).value = FAKE_CODE
        await pilot.press("enter")
        await pilot.pause()
        report["typed_question"] = popup()
        shot("8-typed-question", "Codex: a typed code, the yes / no question (left with Esc)")
        await pilot.press("escape")
        await settle(pilot)
    report["copy_pin_after"] = sha(pin)
    report["copy_codex_pin_after"] = sha(M.pin_file(edit.pins, "codex"))
    report["history"] = [{k: e.get(k) for k in ("kind", "did", "what", "undoes")} for e in E.read_history(edit)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args(argv).out).expanduser().resolve()
    live_pin, live_toml = LIVE_STATE / "claude_live_session", LIVE_TOML
    report: dict = {"live_pin_before": sha(live_pin), "live_toml_before": sha(live_toml)}
    files = prepare(out)
    report["copy_toml_before"] = sha(files["toml"])
    try:
        asyncio.run(drive(out, report))
    finally:
        report["live_pin_after"] = sha(live_pin)
        report["live_toml_after"] = sha(live_toml)
        report["copy_toml_after"] = sha(files["toml"])
        report["herdr_refused"] = files["refused"].read_text() if files["refused"].exists() else ""
        checks = {
            "undo restores the copy's pin byte for byte": report.get("copy_pin_after") == report.get("copy_pin_before"),
            "the mount changed the copy's pin": report.get("copy_pin_mounted", "") not in ("", report.get("copy_pin_before")),
            "the live pin did not change": report["live_pin_after"] == report["live_pin_before"],
            "the live agents.toml did not change": report["live_toml_after"] == report["live_toml_before"],
            "the copy of agents.toml did not change": report["copy_toml_after"] == report["copy_toml_before"],
            "herdr was only asked, never typed into": not report["herdr_refused"],
            "the typed code was not saved (Esc)": report.get("copy_codex_pin_after") == "absent"
            or report.get("copy_codex_pin_after") == sha(LIVE_STATE / "codex_live_session"),
        }
        report["checks"] = checks
        (out / "proof.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        for k, ok in checks.items():
            print(("PASS " if ok else "FAIL ") + k)
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
