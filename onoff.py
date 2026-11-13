"""Live on/off from the grid: the cells that can really be switched today (step 2).

Panel = overseer, never a lifeline. Nothing new is invented here: every switch writes the file the agent
already reads, exactly the way Peter would by hand, only safely:

    Telegram team group   ~/.hermes/profiles/orchestrator/scripts/team_room/state/mirror_config.json
                          voice_notes.on + this agent in voice_notes.agents (the team room's own
                          `mirror voice on|off <agent>` rule, same file format). Re-read on every line: live.
    Claude (terminal)     ~/.claude/settings.json voice.enabled (and the legacy voiceEnabled, because both the
                          Stop hook and the SessionStart hook read `.voice.enabled // .voiceEnabled`, and jq's
                          `//` skips a false) + ~/.claude/voice-defaults.conf VOICE_ALWAYS_ON (else the
                          SessionStart hook switches it back on) + `always_on` in agents.toml when it is there
                          (else `voicecfg check` would call it drift). ~/.claude/hooks is NOT touched.
    Voice Desk (mic/phone) one global switch: ~/.voice_desk/speech/off (exists = the speech queue lets windows
                          play straight, no taking turns). (The old voices_per_profile / shared-voice switch is gone.)

A change is: the exact edits are worked out first (`plan_*`), every file is backed up, written atomically,
read back the way its own reader reads it (json, bash `source`, tomllib, jq for the Claude hook's own test),
and put back at once if anything does not read back. The edits are saved in the change history
(voice_history.jsonl) so U undoes them key by key, never discarding a hand edit made to the same file meanwhile.

Cell values: Off / Voice-back / Always. A place only offers the values it really has: terminals and the team
group have no "voice-back" (nothing there knows whether Peter spoke), so they offer Off / Always.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import agents
import gridstate as G
import voicecfg
import voiceedit

# The profiles the team room can voice (voice_notes.py VOICED_AGENTS; Builder is never voiced).
TEAM_VOICED = ("orchestrator", "assistant", "tax_angel_esther", "wanna-buffet")

# mic-window switches: token -> (label, marker file relative to state dir, "marker exists" means this token's value).
# The "own voices / shared voice" switch is gone with the shared voice (Peter 2026-11-11): every agent speaks with
# its own voice, or not at all.
MIC_SWITCHES = {
    "queue:on": ("Speech queue on (voices take turns)", "speech/off", False),
    "queue:off": ("Speech queue off (windows play straight)", "speech/off", True),
}
RETIRED_MIC_GROUPS = ("own_voices",)        # old history lines may still name it; nothing acts on them


@dataclass
class Option:
    token: str
    label: str
    current: bool = False
    disabled: bool = False         # shown on the list, cannot be picked (the label says why)


@dataclass
class Menu:
    title: str
    options: list[Option] = field(default_factory=list)
    why_not: str = ""              # set when the cell cannot be changed from the panel (yet)


def _later(step: str) -> str:
    return f"read-only for now; it is switchable in {step}"


def can_switch(agent: agents.Agent, place: str) -> bool:
    """Pure look at the kind of agent and place: is there a switch behind this cell at all?"""
    kind = G.kind_of(agent)
    if place in ("tg_dm", "tg_group") and agent.key in G.TELEGRAM_HANDS_OFF:
        return False                                       # shown, never switched (Simba's own bot, 2026-11-13)
    if place == "tg_group":
        return kind == G.HERMES and agent.profile in TEAM_VOICED
    if place == "terminal" and kind == G.CLAUDE:
        return True
    if place == "mic":
        return True
    import onoff_hermes                                    # step 3: Telegram direct chat + Gemini (imports this module)
    return onoff_hermes.can_switch(agent, place)


def menu(agent: agents.Agent, place: str, cell: G.Cell, p: G.Paths) -> Menu:
    """What the chooser offers for one cell (or why nothing)."""
    name = G.PLACE_NAME.get(place, place)
    title = f"{agent.display} — {name}"
    kind = G.kind_of(agent)
    if cell.value == G.NA:
        return Menu(title, why_not=f"{agent.display} has nothing in {name}")
    if place in ("tg_dm", "tg_group") and agent.key in G.TELEGRAM_HANDS_OFF:
        return Menu(title, why_not=G.TELEGRAM_HANDS_OFF[agent.key][:1].upper() + G.TELEGRAM_HANDS_OFF[agent.key][1:])
    import onoff_hermes
    if onoff_hermes.can_switch(agent, place):
        return (onoff_hermes.menu_tg_dm if place == "tg_dm" else onoff_hermes.menu_gemini)(agent, cell, p)
    if place == "tg_group":
        if kind == G.HERMES and agent.profile not in TEAM_VOICED:
            return Menu(title, why_not=f"the team room does not voice {agent.display} (text only)")
        if can_switch(agent, place):
            return Menu(title, [Option(G.OFF, "Off — text only in the group", cell.value == G.OFF),
                                Option(G.ALWAYS, "Always — a voice note follows each long reply", cell.value == G.ALWAYS)])
    if place == "terminal" and can_switch(agent, place):
        return Menu(title, [Option(G.OFF, "Off — Claude does not speak its replies", cell.value == G.OFF),
                            Option(G.ALWAYS, "Always — Claude speaks every reply", cell.value == G.ALWAYS)])
    if place == "mic":
        queue_off = (p.speech_dir / "off").exists()
        return Menu(f"Voice Desk mic window / phone (all agents; Voice Desk answers aloud in each agent's own voice)", [
            Option("queue:on" if queue_off else "queue:off",
                   ("Switch the speech queue on (voices take turns)" if queue_off else "Switch the speech queue off (windows play straight)")
                   + f"   (now: {'off' if queue_off else 'on'})")])
    if place == "tg_dm":
        return Menu(title, why_not="nothing to switch here")
    if place == "iphone":
        return Menu(title, why_not="the iPhone speaks in Siri's own voice; the Mac only sends text, nothing to switch here")
    if place == "terminal":
        why = ("Codex has no speech of its own" if kind == G.CODEX else
               "a Hermes window decides per window in memory (voice.auto_enable); not switchable from the panel")
        return Menu(title, why_not=why)
    return Menu(title, why_not="nothing to switch here")


# ---- small file helpers -------------------------------------------------------------------------------
_ABSENT = object()


def _dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return _ABSENT
        data = data[part]
    return data


def _put(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(data.get(part), dict):
            data[part] = {}
        data = data[part]
    if value is None:
        data.pop(parts[-1], None)
    else:
        data[parts[-1]] = value


def _read_json(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return data


def _dump_like(text: str, data: dict) -> str:
    """JSON text for `data` laid out like the file it came from (indent and key order are kept whenever the
    original is plain json.dumps output, which both settings.json and mirror_config.json are)."""
    original = json.loads(text.lstrip("\ufeff"))
    m = re.search(r"^( +)\S", text, re.M)
    guess = len(m.group(1)) if m else 2
    for indent in dict.fromkeys((guess, 2, 1, 4)):
        for sort in (False, True):
            if json.dumps(original, indent=indent, sort_keys=sort, ensure_ascii=False) == text.rstrip("\n"):
                out = json.dumps(data, indent=indent, sort_keys=sort, ensure_ascii=False)
                return out + ("\n" if text.endswith("\n") else "")
    return json.dumps(data, indent=guess, ensure_ascii=False) + ("\n" if text.endswith("\n") else "")


def _edit_json(text: str, changes: list[dict]) -> str:
    data = json.loads(text.lstrip("\ufeff"))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    for ch in changes:
        if ch["t"] == "json":
            _put(data, ch["key"], ch["after"])
        else:                                             # jsonlist
            cur = _dig(data, ch["key"])
            items = [x for x in cur if x not in ch["remove"]] if isinstance(cur, list) else []
            for x in ch["add"]:
                if x not in items:
                    at = ch.get("add_at", {}).get(x)                   # an undo puts a name back where it stood
                    items.insert(len(items) if at is None else min(at, len(items)), x)
            _put(data, ch["key"], items)
    return _dump_like(text, data)


def _edit_conf(text: str, changes: list[dict]) -> str:
    lines = text.splitlines(keepends=True)
    for ch in changes:
        found = voicecfg._conf_scan("".join(lines))
        key, after = ch["key"], ch["after"]
        if key in found:
            n, _, quote, tail = found[key]
            if after is None:
                del lines[n]
            else:
                lines[n] = f"{key}={quote}{after}{quote}{tail}"
        elif after is not None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            quote = "" if key == "VOICE_ALWAYS_ON" else '"'
            lines.append(f"{key}={quote}{after}{quote}\n")
    return "".join(lines)


def _edit_toml(text: str, changes: list[dict]) -> str:
    for ch in changes:
        text = voiceedit.set_field(text, ch["agent"], ch["key"], ch["after"])
    return text


_EDITORS = {"json": _edit_json, "jsonlist": _edit_json, "conf": _edit_conf, "toml": _edit_toml}


# ---- checking a written file the way its own reader reads it ------------------------------------------------
def _verify_file(path: Path, changes: list[dict]) -> list[str]:
    bad: list[str] = []
    t = changes[0]["t"]
    try:
        if t in ("json", "jsonlist"):
            data = _read_json(path)
            for ch in changes:
                got = _dig(data, ch["key"])
                if ch["t"] == "json":
                    if (got is _ABSENT and ch["after"] is not None) or (got is not _ABSENT and got != ch["after"]):
                        bad.append(f"{path.name}: {ch['key']} reads {None if got is _ABSENT else got!r}, wanted {ch['after']!r}")
                else:
                    cur = got if isinstance(got, list) else []
                    bad += [f"{path.name}: {x} missing from {ch['key']}" for x in ch["add"] if x not in cur]
                    bad += [f"{path.name}: {x} still in {ch['key']}" for x in ch["remove"] if x in cur]
            if path.name == "settings.json" and any(c["key"] == "voice.enabled" for c in changes) and shutil.which("jq"):
                en, legacy = _dig(data, "voice.enabled"), _dig(data, "voiceEnabled")
                en = None if en is _ABSENT else en
                legacy = None if legacy is _ABSENT else legacy
                want = (en if en not in (None, False) else legacy) is True              # jq's `a // b`
                r = subprocess.run(["jq", "-r", "(.voice.enabled // .voiceEnabled) == true", str(path)],
                                   capture_output=True, text=True, timeout=10)
                if r.stdout.strip() != ("true" if want else "false"):
                    bad.append(f"the Claude hook's own test (jq) reads {r.stdout.strip() or r.stderr.strip()!r}, expected {want}")
        elif t == "conf":
            names = [c["key"] for c in changes]
            r = subprocess.run(["/bin/bash", "-c", '. "$1" && for k in "${@:2}"; do printf "%s=%s\\n" "$k" "${!k}"; done',
                                "_", str(path), *names], capture_output=True, text=True, timeout=10)
            got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
            for c in changes:
                want = "" if c["after"] is None else c["after"]
                if got.get(c["key"], "") != want:
                    bad.append(f"bash reads {c['key']} as {got.get(c['key'])!r}, wanted {want!r}")
        elif t == "toml":
            cfg = agents.load(path)
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            for c in changes:
                got = raw.get("agents", {}).get(c["agent"], {}).get(c["key"])
                if got != c["after"]:
                    bad.append(f"agents.toml reads {c['agent']}.{c['key']} as {got!r}, wanted {c['after']!r}")
            if not agents.agents(cfg):
                bad.append("agents.toml has no agents after the edit")
    except Exception as exc:
        bad.append(f"{path.name}: could not be read back ({exc})")
    return bad


# ---- applying and undoing a list of changes -------------------------------------------------------------------
def _backup_name(path: Path) -> str:
    return f"{path.parent.name}-{path.name}"


def apply_changes(changes: list[dict], backup_dir: Path, toml_backups: Optional[Path] = None,
                  stamp: str = "") -> tuple[list[dict], list[str]]:
    """Backup, write and read back every file the changes touch. Returns (files done, errors). Any error puts
    every file back as it was."""
    by_path: dict[str, list[dict]] = {}
    for ch in changes:
        by_path.setdefault(ch["path"], []).append(ch)
    originals: dict[str, tuple[str, Any]] = {}             # path -> ("text", text before) or ("marker", existed)
    files: list[dict] = []
    errors: list[str] = []
    try:
        for sp, chs in by_path.items():
            path = Path(sp)
            if chs[0]["t"] == "marker":
                want = chs[-1]["after"]
                had = path.exists()
                if had == want:
                    continue
                originals[sp] = ("marker", had)
                if want:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("off\n", encoding="utf-8")
                else:
                    path.unlink()
                if path.exists() != want:
                    errors.append(f"{path.name} {'did not appear' if want else 'is still there'} after the change")
                    break
                files.append({"path": sp, "backup": "(a marker file: nothing to back up)"})
                continue
            old = path.read_text(encoding="utf-8") if (path.exists() or not chs[0].get("create")) else "{}\n"
            new = _EDITORS[chs[0]["t"]](old, chs)
            if new == old and path.exists():
                continue
            if path.exists():
                if chs[0]["t"] == "toml" and toml_backups is not None:
                    bak_dir, bak_name = Path(toml_backups), f"{stamp}-agents.toml"
                else:
                    bak_dir, bak_name = Path(backup_dir), _backup_name(path)
                bak_dir.mkdir(parents=True, exist_ok=True)
                bak = bak_dir / bak_name
                shutil.copy2(path.resolve(), bak)
                originals[sp] = ("text", old)
                backup_note = str(bak)
                if chs[0]["t"] == "json" and any(c.get("remove_if_empty") for c in chs) and json.loads(new.lstrip("\ufeff")) == {}:
                    path.unlink()                    # undo of "this change created the file": the file goes away again, not an empty shell
                    if path.exists():
                        errors.append(f"{path.name} is still there after the undo")
                        break
                    files.append({"path": sp, "backup": backup_note})
                    continue
            else:                                                # a file that did not exist yet (a first Telegram chat mode)
                path.parent.mkdir(parents=True, exist_ok=True)
                originals[sp] = ("absent", None)
                backup_note = "(the file did not exist: nothing to back up)"
            voiceedit._atomic_write(path, new)
            files.append({"path": sp, "backup": backup_note})
            bad = _verify_file(path, chs)
            if bad:
                errors += bad
                break
    except Exception as exc:
        errors.append(str(exc))
    if errors:
        for sp, (how, old) in originals.items():
            path = Path(sp)
            try:
                if how == "marker":
                    if old:
                        path.write_text("off\n", encoding="utf-8")
                    else:
                        path.unlink(missing_ok=True)
                elif how == "absent":
                    path.unlink(missing_ok=True)
                else:
                    voiceedit._atomic_write(path, old)
            except OSError as exc:
                errors.append(f"could not put {path.name} back: {exc}")
        return [], errors
    return files, []


def invert(changes: list[dict]) -> list[dict]:
    out = []
    for ch in reversed(changes):
        c = dict(ch)
        if ch["t"] == "jsonlist":
            c["add"], c["remove"] = ch["remove"], ch["add"]
            c["add_at"], c["remove_at"] = ch.get("remove_at", {}), {}
        else:
            c["before"], c["after"] = ch["after"], ch["before"]
            if ch.get("create"):                      # the change made the file: its undo removes the file if it ends up empty
                c.pop("create")
                c["remove_if_empty"] = True
            elif ch.get("remove_if_empty"):           # ... and undoing that undo makes it again
                c.pop("remove_if_empty")
                c["create"] = True
        out.append(c)
    return out


# ---- planning a switch ---------------------------------------------------------------------------------------
class SwitchError(ValueError):
    """Refused before anything was written."""


def _plan_tg_group(agent: agents.Agent, token: str, p: G.Paths) -> tuple[list[dict], str]:
    path = Path(p.mirror_config)
    try:
        data = _read_json(path)
    except (OSError, ValueError) as exc:
        raise SwitchError(f"the team room's mirror_config.json cannot be read ({exc}); nothing changed") from exc
    block: dict = data["voice_notes"] if isinstance(data.get("voice_notes"), dict) else {}
    listed = [x for x in block.get("agents", []) if isinstance(x, str)] if isinstance(block.get("agents"), list) else []
    on = block.get("on") is True
    changes: list[dict] = []
    note = ""
    if token == G.ALWAYS:
        if agent.profile not in listed:
            changes.append({"t": "jsonlist", "path": str(path), "key": "voice_notes.agents", "add": [agent.profile], "remove": []})
        if not on:
            changes.append({"t": "json", "path": str(path), "key": "voice_notes.on",
                            "before": block.get("on"), "after": True})
            others = [x for x in listed if x != agent.profile]
            if others:
                note = (" Voice notes were switched off for the whole group, so they are now also on for "
                        + ", ".join(others) + " (they were already on the list).")
    elif token == G.OFF:
        if agent.profile in listed:
            changes.append({"t": "jsonlist", "path": str(path), "key": "voice_notes.agents", "add": [], "remove": [agent.profile],
                            "remove_at": {agent.profile: listed.index(agent.profile)}})
    else:
        raise SwitchError("the team group has Off and Always only: nothing there knows whether Peter spoke")
    return changes, note


def _plan_claude(agent: agents.Agent, token: str, p: G.Paths, e: voiceedit.EditPaths) -> tuple[list[dict], str]:
    if token not in (G.OFF, G.ALWAYS):
        raise SwitchError("Claude's terminal has Off and Always only")
    want = token == G.ALWAYS
    settings = Path(p.claude_dir) / "settings.json"
    try:
        data = _read_json(settings)
    except (OSError, ValueError) as exc:
        raise SwitchError(f"Claude's settings.json cannot be read ({exc}); nothing changed") from exc
    conf = Path(e.claude_conf)
    try:
        conf_vars = voicecfg._conf_scan(conf.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SwitchError(f"voice-defaults.conf cannot be read ({exc.strerror}); without it the SessionStart hook "
                          "would switch voice back on, so nothing was changed") from exc
    changes: list[dict] = []
    raw_toml = agent.raw
    if "always_on" in raw_toml and raw_toml["always_on"] is not want:
        changes.append({"t": "toml", "path": str(e.toml), "agent": agent.key, "key": "always_on",
                        "before": raw_toml["always_on"], "after": want})
    cur_conf = conf_vars.get("VOICE_ALWAYS_ON", (0, None))[1]
    want_conf = "1" if want else "0"
    if cur_conf != want_conf:
        changes.append({"t": "conf", "path": str(conf), "key": "VOICE_ALWAYS_ON", "before": cur_conf, "after": want_conf})
    cur = _dig(data, "voice.enabled")
    if cur is not want:
        changes.append({"t": "json", "path": str(settings), "key": "voice.enabled",
                        "before": None if cur is _ABSENT else cur, "after": want})
    legacy = _dig(data, "voiceEnabled")
    if legacy is not _ABSENT and legacy is not want:            # jq's `//` falls through a false to this one
        changes.append({"t": "json", "path": str(settings), "key": "voiceEnabled", "before": legacy, "after": want})
    return changes, " Takes effect on the next reply; Claude Code may rewrite its settings if you toggle /voice in a window."


def _plan_mic(token: str, p: G.Paths) -> tuple[list[dict], str]:
    if token not in MIC_SWITCHES:
        raise SwitchError(f"{token!r} is not a Voice Desk switch")
    _, rel, exists = MIC_SWITCHES[token]
    path = Path(p.state) / rel
    if path.exists() == exists:
        return [], ""
    return [{"t": "marker", "path": str(path), "before": path.exists(), "after": exists}], \
        " Voice Desk and the speech queue look at it before every line: no restart."


def _label(place: str, token: str) -> str:
    if place == "mic":
        return MIC_SWITCHES[token][0] if token in MIC_SWITCHES else token
    return G.LABEL.get(token, token)


# ---- making a switch --------------------------------------------------------------------------------------------
def set_cell(agent_key: str, place: str, token: str, p: G.Paths, e: Optional[voiceedit.EditPaths] = None, *,
             now: Optional[_dt.datetime] = None) -> voiceedit.Result:
    """Switch one cell. Never raises for a refused or failed switch."""
    e = e or voiceedit.EditPaths.default()
    now = now or _dt.datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    import onoff_hermes                                    # step 3 planners (Telegram direct chat, Gemini)
    try:
        cfg = agents.load(e.toml)
        agent = voiceedit._find_agent(cfg, agent_key)
        if not can_switch(agent, place):
            raise SwitchError(menu(agent, place, G.Cell(G.UNKNOWN), p).why_not or "this cell cannot be switched")
        if place == "tg_group":
            changes, note = _plan_tg_group(agent, token, p)
        elif place == "terminal":
            changes, note = _plan_claude(agent, token, p, e) if G.kind_of(agent) == G.CLAUDE else \
                onoff_hermes.plan_gemini(agent, token, p)
        elif place == "tg_dm":
            changes, note = onoff_hermes.plan_tg_dm(agent, token, p)
        else:
            changes, note = _plan_mic(token, p)
    except (SwitchError, voiceedit.EditError, OSError, ValueError) as exc:
        return voiceedit.Result(False, str(exc))
    return _run(agent, place, token, changes, note, p, e, stamp, now)


def _current_token(agent: agents.Agent, place: str, p: G.Paths) -> str:
    cell = G.CELL_FUNCS[place](agent, p)
    return cell.value


def _run(agent: agents.Agent, place: str, token: str, changes: list[dict], note: str, p: G.Paths,
         e: voiceedit.EditPaths, stamp: str, now: _dt.datetime, *, kind: str = "switch",
         undoes: Optional[str] = None, old_token: Optional[str] = None) -> voiceedit.Result:
    old_token = old_token if old_token is not None else _opposite(place, token, agent, p)
    who = "Voice Desk (all agents)" if place == "mic" else agent.display
    what = {"tg_group": "Telegram group voice notes", "terminal": "terminal voice", "mic": "Voice Desk switch",
            "tg_dm": "Telegram direct chat voice"}[place]
    if not changes:
        return voiceedit.Result(True, f"{who}: {what} already {_label(place, token)}, nothing to change")
    entry = {"id": f"{stamp}-{agent.key}-switch-{place}", "ts": now.timestamp(), "kind": kind, "agent": agent.key,
             "display": who, "field": f"switch:{place}", "what": what, "old": old_token, "new": token,
             "old_label": _label(place, old_token), "new_label": _label(place, token), "ok": False, "changes": changes}
    if undoes:
        entry["undoes"] = undoes
    files, errors = apply_changes(changes, Path(e.hermes_backups) / f"{stamp}-voice-panel", e.toml_backups, stamp)
    if errors:
        entry["error"] = "; ".join(errors)
        voiceedit._append_history(e, entry)
        return voiceedit.Result(False, f"{who}: not changed ({errors[0]}); every file put back as it was", entry)
    entry.update(ok=True, files=files)
    voiceedit._append_history(e, entry)
    n = len([f for f in files if not f["backup"].startswith("(")])
    msg = (f"{who} {what}: {entry['old_label']} → {entry['new_label']}; {len(files)} file(s) written and read back"
           + (f", {n} backed up" if n else "") + "." + note)
    return voiceedit.Result(True, msg, entry, [f["path"] for f in files])


def _opposite(place: str, token: str, agent: agents.Agent, p: G.Paths) -> str:
    """The state before the switch (read now, before anything is written)."""
    if place == "mic":
        return {"queue:on": "queue:off", "queue:off": "queue:on"}.get(token, token)
    return _current_token(agent, place, p)


def undo(last: dict, e: voiceedit.EditPaths, p: Optional[G.Paths] = None) -> voiceedit.Result:
    """Run a switch backwards, key by key (the same backup and read-back as the switch itself)."""
    p = p or G.Paths.default()
    now = _dt.datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    try:
        agent = voiceedit._find_agent(agents.load(e.toml), last["agent"])
    except (voiceedit.EditError, OSError, ValueError) as exc:
        return voiceedit.Result(False, str(exc))
    place = last["field"].split(":", 1)[1]
    res = _run(agent, place, last["old"], invert(last["changes"]), "", p, e, stamp, now, kind="undo",
               undoes=last["id"], old_token=last["new"])
    if res.ok and res.entry:
        res.message = "Undone: " + res.message
    return res
