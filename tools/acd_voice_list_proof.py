"""Proof for the full voice list (brief 2026-11-18-acd-full-voice-list.md, "Done means"): run the Agent Control Desk
headless on a COPY of agents.toml and show that nothing about any agent's voice changes by itself.

    .venv/bin/python tools/acd_voice_list_proof.py --out <folder> [--old <a checkout of the code before>]
                                                  [--toml ~/GitHub/Voice_Desk/agents.toml]

What it does, all silent and read-only towards everything live:
  1. copies agents.toml, Claude's and Gemini's voice-defaults.conf into a throw-away folder and points the desk,
     Voice Desk's state and the desk's own state there (VOICE_DESK_AGENTS, VOICE_DESK_STATE, ACD_STATE, ...). The
     Hermes profiles are read where they are (never written: the desk is never armed here);
  2. "values": every agent's voice, Mandarin voice, effect, speed and on/off settings as the code reads them, the
     voice Voice Desk would speak with (voices.resolve) and what a sync would write (voicecfg). With --old the same
     is read by the OLD code (a checkout of the commit before) and both are compared: they must be identical;
  3. "desk": the desk is drawn headless (no window). Every agent's voice cells are read; every voice menu is opened
     and counted (lines, lines that cannot be picked, "not playable yet" lines); the typed-name line is driven to its
     yes / no question and left unanswered; pictures (SVG) of the menus are saved;
  4. agents.toml's copy is fingerprinted before and after: it must be byte for byte the same.

Nothing is played, nothing is armed, no live window or file is touched. The report is <out>/proof.json.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
VOICE_KEYS = ("voice", "voice_zh", "effect", "speed", "always_on", "auto_tts", "english_only", "no_fallback", "here",
              "mode", "silence_duration", "silence_threshold", "barge_in", "barge_in_grace_seconds", "command")


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sandbox_env(toml_src: Path, root: Path) -> dict:
    """Copies of the files a voice change could write, and the environment that points the code at them."""
    root.mkdir(parents=True, exist_ok=True)
    toml = root / "agents.toml"
    shutil.copy2(toml_src, toml)
    env = {"VOICE_DESK_AGENTS": str(toml), "VOICE_DESK_STATE": str(root / "state"), "ACD_STATE": str(root / "acd"),
           "ACD_CONFIG_BACKUPS": str(root / "backups"), "ACD_RESTART_REQUEST": str(root / "restart.request"),
           "ACD_USAGE": "off"}
    for name, live, var in (("claude", HOME / ".claude/voice-defaults.conf", "VOICE_DEFAULTS_CONF"),
                            ("gemini", HOME / ".gemini/config/hooks/voice-defaults.conf", "VOICE_DESK_GEMINI_CONF")):
        copy = root / f"{name}-voice-defaults.conf"
        if live.is_file():
            shutil.copy2(live, copy)
        env[var] = str(copy)
    return env


# ---- 2. values (run in a child process, once with the new code and once with the old) ------------------------------
def values(code: Path) -> dict:
    sys.path.insert(0, str(code))
    import agents
    import voicecfg
    import voices
    cfg = agents.load(Path(os.environ["VOICE_DESK_AGENTS"]))
    out: dict = {"agents": {}}
    for a in agents.agents(cfg):
        v = voices.resolve(a)
        out["agents"][a.key] = {
            "settings": {k: a.raw.get(k) for k in VOICE_KEYS if k in a.raw},
            "voice_desk_speaks": {"en": v.en, "zh": v.zh, "command": list(v.command), "effect": v.effect},
            "claude_conf_wants": voicecfg.claude_wants(a) if a.target == "claude" else None,
            "gemini_conf_wants": list(voicecfg.gemini_wants(a, cfg)) if a.target == "gemini" else None,
        }
    items = voicecfg.collect(cfg, voicecfg.PROFILES, Path(os.environ["VOICE_DEFAULTS_CONF"]),
                             Path(os.environ["VOICE_DESK_GEMINI_CONF"]))
    out["sync_would_write"] = sorted(f"{i.where} {i.key} = {i.want!r}" for i in voicecfg.drift(items))
    out["sync_wants"] = sorted(f"{i.where} {i.key} = {i.want!r}" for i in items)
    return out


def values_in_child(code: Path, env: dict) -> dict:
    r = subprocess.run([sys.executable, __file__, "--values", str(code)], capture_output=True, text=True,
                       env={**os.environ, **env}, timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"reading the values with {code} failed:\n{r.stderr[-2000:]}")
    return json.loads(r.stdout)


# ---- 3. the desk, headless --------------------------------------------------------------------------------------------
async def desk(out: Path) -> dict:
    sys.path.insert(0, str(REPO))
    import acd
    import acd_data as D
    import acd_edit as E
    import voicepick

    paths = D.Paths.default()
    edit = E.EditPaths.default(launcher_armed=False)
    snap = await asyncio.to_thread(lambda: D.collect(paths, desk=E.record(edit)))
    app = acd.Desk(collector=lambda: snap, autostart=False, edit=edit, watching=False, tester=None)
    report: dict = {"cells": {}, "menus": {}, "pictures": []}
    async with app.run_test(size=(200, 62)) as pilot:
        await pilot.pause()
        await asyncio.to_thread(app.learn_speeds, snap)
        app.apply_snapshot(snap)
        await pilot.pause()
        screen = app.screen
        names = [c.name for c in screen.cols]
        for name in names:
            for row in ("voice", "voice_zh", "speed", "effect"):
                if not screen.go(row, name):
                    continue
                await pilot.pause()
                cell = screen.cells.get((row, acd.col_id(next(c for c in screen.cols if c.name == name))))
                if cell is None or cell.blank:
                    continue
                report["cells"].setdefault(name, {})[row] = {"words": cell.words, "status": cell.status}
                if row not in ("voice", "voice_zh", "effect") or not cell.setting:
                    continue
                await pilot.press("enter")
                await pilot.pause()
                if not isinstance(app.screen, acd.CellMenu):
                    continue
                spec = app.screen.spec
                opts = spec.options
                report["menus"].setdefault(name, {})[row] = {
                    "lines": len(opts),
                    "pickable": sum(1 for o in opts if not o.disabled and not o.now),
                    "cannot_be_picked": sum(1 for o in opts if o.disabled),
                    "not_playable_yet": sum(1 for o in opts if voicepick.NOT_PLAYABLE in o.label),
                    "now": [o.label for o in opts if o.now],
                    "typed_line": any(o.goes_on for o in opts),
                    "no_effect_notes": sum(1 for o in opts if voicepick.NO_EFFECT in o.label),
                    "first": [o.label for o in opts[:3]], "last": [o.label for o in opts[-3:]],
                }
                if (name, row) in (("Gemini", "voice"), ("Simba", "voice"), ("Jarvis", "voice"), ("Esther", "voice"),
                                   ("Simba", "effect"), ("Kylie", "voice")):
                    p = out / f"menu_{name.lower()}_{row}.svg"
                    p.write_text(app.export_screenshot(title=f"{name}: {row} menu"), encoding="utf-8")
                    report["pictures"].append(str(p))
                await pilot.press("escape")
                await pilot.pause()
        # the typed-name line, driven to its yes / no question and left unanswered
        if screen.go("voice", "Kylie"):
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            if isinstance(app.screen, acd.CellMenu):
                lst = app.screen.query_one("#menu-list")
                lst.highlighted = next(i for i, o in enumerate(app.screen.spec.options) if o.goes_on)
                await pilot.press("enter")
                await pilot.pause()
                if isinstance(app.screen, acd.AskPopup):
                    from textual.widgets import Input
                    app.screen.query_one(Input).value = "en-GB-RyanNeural"
                    await pilot.press("enter")
                    await pilot.pause()
                    report["typed_question"] = type(app.screen).__name__
                    p = out / "typed_voice_question.svg"
                    p.write_text(app.export_screenshot(title="Kylie: a typed voice, the yes / no question"),
                                 encoding="utf-8")
                    report["pictures"].append(str(p))
                    await pilot.press("escape")                 # never yes
                    await pilot.pause()
        p = out / "table.svg"
        while not isinstance(app.screen, acd.TableScreen):
            await pilot.press("escape")
            await pilot.pause()
        screen.go("voice", "Kylie")
        await pilot.pause()
        p.write_text(app.export_screenshot(title="Agent Control Desk"), encoding="utf-8")
        report["pictures"].append(str(p))
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--old", type=Path, help="a checkout of the code before the change, to compare the values")
    ap.add_argument("--toml", type=Path, default=HOME / "GitHub/Voice_Desk/agents.toml")
    ap.add_argument("--values", type=Path, help=argparse.SUPPRESS)          # the child process
    a = ap.parse_args()
    if a.values:
        print(json.dumps(values(a.values), default=str))
        return 0
    if not a.out:
        ap.error("--out is needed")
    a.out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="acd-voice-proof-"))
    env = sandbox_env(a.toml, root)
    os.environ.update(env)
    copy = Path(env["VOICE_DESK_AGENTS"])
    before = sha(copy)
    report: dict = {"agents_toml_source": str(a.toml), "copy": str(copy), "source_sha_matches_copy": sha(a.toml) == before}
    report["new"] = values_in_child(REPO, env)
    if a.old:
        report["old"] = values_in_child(a.old, env)
        report["old_equals_new"] = report["old"] == report["new"]
        if not report["old_equals_new"]:
            report["differences"] = [k for k in report["new"]["agents"]
                                     if report["new"]["agents"][k] != report["old"]["agents"].get(k)]
            if report["new"]["sync_wants"] != report["old"]["sync_wants"]:
                report["differences"].append("sync_wants")
    report["desk"] = asyncio.run(desk(a.out))
    # nothing changes by default: no agent's voice of today may be shown as "not playable yet"
    report["current_voice_not_playable"] = [f"{n}: {c['voice']['words']}" for n, c in report["desk"]["cells"].items()
                                            if "not playable yet" in c.get("voice", {}).get("words", "")]
    report["copy_unchanged"] = sha(copy) == before
    report["source_unchanged"] = sha(a.toml) == before
    (a.out / "proof.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    ok = report["copy_unchanged"] and report.get("old_equals_new", True) and not report["current_voice_not_playable"]
    print(json.dumps({"copy_unchanged": report["copy_unchanged"], "old_equals_new": report.get("old_equals_new"),
                      "differences": report.get("differences", []),
                      "current_voice_not_playable": report["current_voice_not_playable"],
                      "pictures": len(report["desk"]["pictures"]),
                      "menus": {n: {r: (m["lines"], m["cannot_be_picked"], m["not_playable_yet"])
                                    for r, m in rows.items()} for n, rows in report["desk"]["menus"].items()},
                      "report": str(a.out / "proof.json")}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
