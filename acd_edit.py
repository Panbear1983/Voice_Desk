"""Agent Control Desk: the changes. Voice, backups on/off, language model (version two); Team Room membership
and the talk rules (version three).

Everything the desk can CHANGE lives here, so acd_data.py stays a module that only reads.

THE ARMED SWITCH. Editing ships switched OFF. A change is written only when BOTH are true:

    1. the launcher was started with --armed            (./acd.sh --armed)
    2. the desk's own folder holds armed.json           (.venv/bin/python acd_edit.py arm   /   disarm)

Otherwise every change is a DRY RUN: the desk works out exactly what would change, shows it, and writes
nothing at all (no settings file, no history, no record, no restart request).

A real change does what Peter would do by hand, safely:

    voice            through the Voice Desk's own voiceedit / voicecfg path (one line of agents.toml, then the
                     agent's settings file: backup, atomic write, read back with Hermes' own reader, auto-restore)
    model, effort    one line of the agent's config.yaml
    backups on/off   the agent's fallback_providers list; a backup switched off is taken out of the file and
                     remembered here (backups_off.json), so it can be shown as "off" and switched on again
    for all three    a dated backup first, only the needed lines change and every comment stays, atomic replace,
                     the file is read back through Hermes' own settings reader, and the backup is put back at once
                     if the value did not take. A file the desk does not recognise is refused: nothing changes.

Version three adds two more, with the same guarantees:

    Team Room        in or out of the room. The desk never edits the member list and never switches the add-on by
                     itself: it runs the Team Room's OWN add / kick, which changes the terminal room and the
                     Telegram group in one go, then checks both doors and runs the command backwards if they
                     do not agree. Both doors, or nothing.
    talk rules       acd_rules.toml, the one file that says who may talk to whom: dated backup, only the needed
                     lines, atomic replace, read back, put back at once if a value did not take.

Every change is one line in history.jsonl (who, when, what, before, after), and U undoes the newest one by
running it backwards through the same safe path.

The desk never restarts anything. "Request a restart" only writes the request file the existing helper watches
(gateway_guard/restart_helper.py), which restarts when no agent is mid-reply and no job is running.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import agents as A
import acd_data as D
import settings as S
import voicecfg
import voiceedit

HOME = Path.home()
WHO = "Agent Control Desk"

# ---- the few plain choices (never a typed number) ---------------------------------------------------------
SPEEDS = (("slower", 0.9), ("normal", 1.0), ("a bit faster", 1.1), ("fast", 1.2))
EFFORTS = (("low", "low effort"), ("medium", "medium effort"), ("high", "high effort"))
MODELS = {      # what an agent can run on its main service
    "anthropic": ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
    "antigravity-subscription-directsdk": ("claude-opus-5-5", "claude-sonnet-5-5", "gemini-4-argon", "gemini-3.8-flash"),
    "openai-codex": ("gpt-5.4-mini",),
}
NVIDIA = ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")       # offered as a backup choice to every agent
# Jarvis's NVIDIA backup was removed by hand on 2026-11-13, before the desk kept a record: remembered here.
SEED_OFF = {"orchestrator": [{"provider": NVIDIA[0], "model": NVIDIA[1], "position": 4, "off_since": "2026-11-13",
                              "note": "switched off 13 Nov", "raw": []}]}
NEEDS = {
    "model": "No restart needed. " + D.PENDING_NOTE[0].upper() + D.PENDING_NOTE[1:] + ".",
    "effort": "No restart needed. New chats and job cards use it at once; open terminal chats keep the old "
              "setting until a fresh chat is started.",
    "backup": "No restart needed. New chats and job cards use the new list at once; open terminal chats keep the "
              "old list until a fresh chat is started.",
    "ladder": "No restart needed. New chats and job cards use the new ladder at once; open terminal chats keep the "
              "old list until a fresh chat is started.",
    "voice": "No restart needed. The Voice Desk speaks it from the next line; a Hermes agent's own window uses it "
             "from its next session.",
    "rule": "No restart needed. The desk shows it at once. The Team Room follows a rule only where its line says so; "
            "a rule marked 'recorded, not yet enforced' changes nothing in the room yet.",
    "voice_place": "No restart needed for most places: the Telegram team group, Claude's and Gemini's own windows "
                   "and the mic window read their switch before every line, and an agent's own terminal windows "
                   "before their next reply (a window opened before the fix went in follows it once reopened). "
                   "A Telegram private chat follows from Peter's next message when the agents' system has the "
                   "live-reload fix; the result says so.",
    "team_room": "No restart needed in the normal case: the Team Room makes its next room through the agents' system "
                 "and switches the Telegram side in the same step. The chat on screen starts fresh; the old lines "
                 "stay in the saved history.",
}
UNDOABLE = ("model", "effort", "backup", "ladder", "voice", "rule", "team_room", "voice_place", "agent")
_DESK_NOTE = re.compile(r"^[ \t]*# \d{4}-\d\d-\d\d " + re.escape(WHO) + r": ")


class EditError(ValueError):
    """A change the desk refuses, in words Peter can read. Nothing was written."""


# ---- where the desk writes -------------------------------------------------------------------------------
@dataclass(frozen=True)
class EditPaths:
    state: Path                     # the desk's own folder: armed.json, history.jsonl, backups_off.json
    profiles: Path
    rules: Path
    restart_request: Path           # the file the restart helper watches
    config_backups: Path
    voice: voiceedit.EditPaths      # the Voice Desk's own paths (agents.toml and what it syncs)
    launcher_armed: bool = False    # True only when the launcher was started with --armed
    members: Optional[Path] = None          # the Team Room's saved member list (read, never written by the desk)
    team_room_cmd: Optional[Path] = None    # the Team Room's own command (its add / kick does both doors)
    gateway_state: Optional[Path] = None    # the agents' own status file: which agent has a Telegram bot
    grid: Any = None                        # the Voice Desk grid's own files (gridstate.Paths): the voice switches

    @classmethod
    def default(cls, launcher_armed: bool = False) -> "EditPaths":
        env = os.environ.get
        base = D.Paths.default()
        vp = voiceedit.EditPaths.default()
        vp = dataclasses.replace(vp, toml=base.agents_toml)
        return cls(state=Path(env("ACD_STATE", S.STATE_DIR / "acd")), profiles=base.profiles, rules=base.rules,
                   restart_request=Path(env("ACD_RESTART_REQUEST",
                                            HOME / ".hermes/profiles/orchestrator/state/gateway_restart.request")),
                   config_backups=Path(env("ACD_CONFIG_BACKUPS", HOME / ".hermes/profile-backups")),
                   voice=vp, launcher_armed=launcher_armed, members=base.members,
                   team_room_cmd=Path(env("ACD_TEAM_ROOM_CMD", HOME / ".local/bin/team-room")),
                   gateway_state=base.gateway_state, grid=base.grid)

    @property
    def armed_file(self) -> Path:
        return self.state / "armed.json"

    @property
    def history(self) -> Path:
        return self.state / "history.jsonl"

    @property
    def off_file(self) -> Path:
        return self.state / "backups_off.json"

    @property
    def rules_backups(self) -> Path:
        return self.state / "rules-backups"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def armed(paths: EditPaths) -> bool:
    """True only when the launcher flag AND the desk's own armed.json both say so."""
    data = _read_json(paths.armed_file)
    return bool(paths.launcher_armed and isinstance(data, dict) and data.get("armed") is True)


def why_not_armed(paths: EditPaths) -> str:
    data = _read_json(paths.armed_file)
    file_ok = isinstance(data, dict) and data.get("armed") is True
    if paths.launcher_armed and file_ok:
        return ""
    missing = []
    if not paths.launcher_armed:
        missing.append("the desk was not started with --armed")
    if not file_ok:
        missing.append("the desk's armed switch is off")
    return " and ".join(missing)


def set_armed(paths: EditPaths, on: bool, now: Optional[_dt.datetime] = None) -> None:
    """Flip the desk's own switch file (the launcher flag is still needed as well)."""
    now = now or _dt.datetime.now()
    paths.state.mkdir(parents=True, exist_ok=True)
    _atomic_write_new(paths.armed_file, json.dumps({"armed": bool(on), "at": now.isoformat(timespec="seconds")}) + "\n")


def _atomic_write_new(path: Path, text: str) -> None:
    """Atomic write for the desk's OWN small files (may not exist yet)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".acd", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---- what a change returns ---------------------------------------------------------------------------------
@dataclass
class Result:
    ok: bool
    message: str                    # one plain sentence: what happened (or what would)
    dry: bool = False               # True = nothing was written
    lines: list[str] = field(default_factory=list)     # the exact line changes, "- old" / "+ new"
    needs: str = ""                 # what it takes for the change to be live everywhere
    entry: Optional[dict] = None


def _refused(exc: Exception) -> Result:
    return Result(False, str(exc))


# ---- the settings file, read by lines ----------------------------------------------------------------------
@dataclass
class BItem:
    start: int                      # first line of the entry ("- provider: ...")
    end: int                        # one past its last line
    provider: str
    model: str
    indent: int                     # the column of its dash


@dataclass
class Block:
    entry: voicecfg.Entry
    items: list[BItem]
    inline_empty: bool              # written as `fallback_providers: []`
    item_indent: int


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" \t"))


def _kv(body: str) -> Optional[tuple[str, Any]]:
    m = re.match(r"^([A-Za-z0-9_]+):(?:\s+(.*))?$", body)
    if not m:
        return None
    return m.group(1), voicecfg.parse_value(voicecfg._split_comment(m.group(2) or "")[0])


def read_block(text: str) -> Block:
    """The backups list of a settings file, with the place of every entry. Raises EditError for any shape
    the desk does not know, so an unknown file is never edited."""
    e = voicecfg.scan(text)
    if "fallback_providers" not in e:
        raise EditError("this settings file has no backups list (fallback_providers); nothing changed")
    entry = e["fallback_providers"]
    if entry.indent != 0:
        raise EditError("the backups list is not where the desk expects it; nothing changed")
    if entry.value.strip() == "[]":
        return Block(entry, [], True, entry.indent + 2)
    if entry.value.strip() != "":
        raise EditError("the backups list is written in a way the desk does not know; nothing changed")
    lines = text.splitlines(keepends=True)
    items: list[BItem] = []
    cur: Optional[dict] = None

    def close() -> None:
        if cur is None:
            return
        if not cur.get("provider") or not cur.get("model"):
            raise EditError("a backup entry has a shape the desk does not know; nothing changed")
        items.append(BItem(cur["start"], cur["last"] + 1, str(cur["provider"]), str(cur["model"]), cur["indent"]))

    dash: Optional[int] = None                          # the column every entry's dash sits in
    for n in range(entry.line + 1, len(lines)):
        raw = lines[n]
        body = raw.strip()
        if not body or body.startswith("#"):
            continue
        ind = _indent(raw)
        if body.startswith("- ") or body == "-":
            if ind < entry.indent:
                break
            if dash is not None and ind > dash:
                raise EditError("a backup entry holds a list the desk does not know; nothing changed")
            if dash is not None and ind < dash:
                break
            close()
            dash = ind
            cur = {"start": n, "last": n, "indent": ind}
            kv = _kv(body[1:].strip())
            if kv:
                cur[kv[0]] = kv[1]
            continue
        if cur is not None and ind > cur["indent"]:
            if ind == cur["indent"] + 2:
                kv = _kv(body)
                if kv:
                    cur[kv[0]] = kv[1]
            cur["last"] = n
            continue
        break
    close()
    return Block(entry, items, False, items[0].indent if items else entry.indent + 2)


def managed(text: str) -> dict:
    """The three things the desk manages, read by lines: the model, the effort and the backups list."""
    e = voicecfg.scan(text)
    val = lambda k: voicecfg.parse_value(e[k].value) if k in e and e[k].value != "" else None     # noqa: E731
    try:
        fb = [{"provider": i.provider, "model": i.model} for i in read_block(text).items]
    except EditError:
        fb = None
    return {"model": {"default": val("model.default"), "provider": val("model.provider")},
            "agent": {"reasoning_effort": val("agent.reasoning_effort")}, "fallback_providers": fb}


def _note(indent: int, date: str, words: str) -> str:
    return f"{' ' * indent}# {date} {WHO}: {words}\n"


def set_scalar(text: str, dotted: str, value: Any, words: str, date: str) -> str:
    """The text with one `key: value` line changed and a dated desk comment above it. Every other line and
    every comment stays byte for byte."""
    e = voicecfg.scan(text)
    if dotted not in e or e[dotted].value == "":
        raise EditError(f"this settings file has no `{dotted}` line the desk can change; nothing changed")
    entry = e[dotted]
    lines = text.splitlines(keepends=True)
    lines[entry.line] = entry.head + voicecfg.format_value(value) + entry.tail
    note = _note(entry.indent, date, words)
    if entry.line > 0 and _DESK_NOTE.match(lines[entry.line - 1]) and _indent(lines[entry.line - 1]) == entry.indent:
        lines[entry.line - 1] = note                    # the desk's own older note on this line is replaced
    else:
        lines.insert(entry.line, note)
    return "".join(lines)


def _backup_words(provider: str, model: str) -> str:
    return f"{model} via {provider}"


def _block_span(lines: list[str], block: Block) -> tuple[int, int]:
    """First and one-past-last line of the list's body (its entries and the comments between them)."""
    start = block.entry.line + 1
    end = start
    for n in range(start, len(lines)):
        body = lines[n].strip()
        if not body or body.startswith("#"):
            if body.startswith("#") and _indent(lines[n]) > block.entry.indent:
                end = n + 1
            continue
        if _indent(lines[n]) > block.entry.indent or body.startswith("- "):
            end = n + 1
            continue
        break
    return start, end


def remove_backup(text: str, provider: str, model: str, date: str) -> tuple[str, list[str], int]:
    """(new text, the entry's own lines, its place in the order starting at 1). The entry's lines are taken
    out and a dated desk comment is left where it stood; every other comment stays."""
    block = read_block(text)
    hit = next((i for i in block.items if (i.provider, i.model) == (provider, model)), None)
    if hit is None:
        raise EditError(f"{_backup_words(provider, model)} is not in this agent's backups list; nothing changed")
    position = block.items.index(hit) + 1
    lines = text.splitlines(keepends=True)
    raw = lines[hit.start:hit.end]
    words = _backup_words(provider, model)
    lines[hit.start:hit.end] = [_note(hit.indent, date, f"backup switched off (the desk remembers it): {words}")]
    start, end = _block_span(lines, block)
    for n in range(end - 1, start - 1, -1):             # the older "switched on" note for the same backup goes
        if _DESK_NOTE.match(lines[n]) and lines[n].rstrip().endswith(f"backup switched on: {words}"):
            del lines[n]
    if len(block.items) == 1:                           # the list is now empty: say so the way YAML wants it
        en = block.entry
        lines[en.line] = en.head.rstrip() + " []" + (en.tail if en.tail.strip() else "\n")
    return "".join(lines), raw, position


def insert_backup(text: str, provider: str, model: str, raw: list[str], position: int, date: str) -> str:
    """The text with a backup put (back) into the list at `position` (1 = first). The entry's own remembered
    lines are used when they still fit; otherwise two fresh lines are written."""
    block = read_block(text)
    if any((i.provider, i.model) == (provider, model) for i in block.items):
        raise EditError(f"{_backup_words(provider, model)} is already in this agent's backups list; nothing changed")
    lines = text.splitlines(keepends=True)
    words = _backup_words(provider, model)
    ind = block.item_indent
    fresh = [f"{' ' * ind}- provider: {provider}\n", f"{' ' * ind}  model: {model}\n"]
    body = fresh
    if raw and _indent(raw[0]) == ind and all(isinstance(x, str) for x in raw):
        body = [x if x.endswith("\n") else x + "\n" for x in raw]
    new = [_note(ind, date, f"backup switched on: {words}")] + body
    if block.inline_empty:
        en = block.entry
        lines[en.line] = en.head.rstrip() + (en.tail if en.tail.strip() else "\n")
        at = en.line + 1
    else:
        index = max(0, min(position - 1, len(block.items)))
        at = block.items[index].start if index < len(block.items) else block.items[-1].end
    if lines and at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = new
    start, end = _block_span(lines, read_block("".join(lines)))
    for n in range(end - 1, start - 1, -1):             # the "switched off" note for the same backup goes
        if _DESK_NOTE.match(lines[n]) and lines[n].rstrip().endswith(f"(the desk remembers it): {words}"):
            del lines[n]
    return "".join(lines)


# ---- writing one settings file safely ------------------------------------------------------------------------
Loader = Callable[[Path], Any]


def default_loader() -> Optional[Loader]:
    """Hermes' own settings reader, when Hermes is on this machine."""
    return voicecfg.hermes_readback if voicecfg.HERMES_PY.exists() else None


def _check(data: Any, expect: dict) -> list[str]:
    bad = []
    for key, want in expect.items():
        if key == "fallback_providers":
            got = data.get(key) if isinstance(data, dict) else None
            pairs = [(str(d.get("provider") or ""), str(d.get("model") or "")) for d in got or () if isinstance(d, dict)]
            if pairs != [tuple(x) for x in want]:
                bad.append(f"the backups list reads back as {pairs}, wanted {[tuple(x) for x in want]}")
        elif voicecfg.dig(data, key) != want:
            bad.append(f"{key} reads back as {voicecfg.dig(data, key)!r}, wanted {want!r}")
    return bad


def readable(path: Path, loader: Optional[Loader]) -> str:
    """The file's text, after checking that Hermes can read it as it stands. Raises EditError otherwise."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise EditError(f"the settings file cannot be read ({exc.strerror or exc}); nothing changed") from exc
    if loader is not None:
        try:
            data = loader(Path(path))
        except Exception as exc:                        # noqa: BLE001
            raise EditError(f"Hermes cannot read this settings file as it stands ({str(exc)[:120]}); "
                            "nothing changed") from exc
        if not isinstance(data, dict):
            raise EditError("this settings file is not a list of settings; nothing changed")
    return text


def write_config(path: Path, old: str, new: str, expect: dict, backup_dir: Path, stamp: str,
                 loader: Optional[Loader]) -> Path:
    """Back up, write atomically, read back, restore on any doubt. Returns the backup's path."""
    path = Path(path)
    if path.read_text(encoding="utf-8") != old:
        raise EditError("the settings file changed while the edit was prepared; nothing written, try again")
    backup_dir.mkdir(parents=True, exist_ok=True)
    bak = backup_dir / f"{stamp}-{path.parent.name}-config.yaml"
    n = 1
    while bak.exists():
        n += 1
        bak = backup_dir / f"{stamp}-{path.parent.name}-config.{n}.yaml"
    shutil.copy2(path.resolve(), bak)
    voicecfg._atomic_write(path, new)
    bad: list[str] = []
    try:
        if loader is not None:
            bad += _check(loader(path), expect)
        bad += _check(managed(path.read_text(encoding="utf-8")), expect)
    except Exception as exc:                            # noqa: BLE001  any doubt at all = put it back
        bad.append(f"it could not be read back ({str(exc)[:120]})")
    if bad:
        voicecfg._atomic_write(path, old)
        raise EditError("the change did not read back right, so the file was put back exactly as it was: "
                        + "; ".join(bad))
    return bak


def diff_lines(old: str, new: str) -> list[str]:
    out = []
    for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if ln.startswith(("---", "+++", "@@")):
            continue
        out.append(("- " if ln[0] == "-" else "+ ") + ln[1:].strip())
    return out


# ---- the desk's own record: history and switched-off backups --------------------------------------------------
def read_history(paths: EditPaths) -> list[dict]:
    try:
        rows = paths.history.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in rows:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _append_history(paths: EditPaths, entry: dict) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    with open(paths.history, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def last_undoable(paths: EditPaths) -> Optional[dict]:
    hist = read_history(paths)
    undone = {e.get("undoes") for e in hist if e.get("undoes")}
    for e in reversed(hist):
        if e.get("ok") and not e.get("undoes") and e.get("kind") in UNDOABLE and e.get("id") not in undone:
            return e
    return None


def read_off(paths: EditPaths) -> dict:
    """profile -> the backups the desk remembers as switched off. Before the first change this is the seed."""
    data = _read_json(paths.off_file)
    if isinstance(data, dict) and isinstance(data.get("off"), dict):
        return {k: [r for r in v if isinstance(r, dict)] for k, v in data["off"].items() if isinstance(v, list)}
    return {k: [dict(r) for r in v] for k, v in SEED_OFF.items()}


def _write_off(paths: EditPaths, off: dict) -> None:
    _atomic_write_new(paths.off_file, json.dumps({"seeded": True, "off": off}, ensure_ascii=False, indent=1) + "\n")


def off_view(paths: EditPaths, profiles: Optional[list[str]] = None) -> dict:
    """What the grid shows as "off": the remembered ones, plus NVIDIA as a choice for every agent that has
    neither listed nor remembered it."""
    off = read_off(paths)
    out: dict = {}
    for profile in profiles if profiles is not None else list(off):
        rows = [dict(r) for r in off.get(profile, ())]
        if not any((r.get("provider"), r.get("model")) == NVIDIA or r.get("provider") == NVIDIA[0] for r in rows):
            rows.append({"provider": NVIDIA[0], "model": NVIDIA[1], "position": 99,
                         "note": "a choice, not on this agent's list yet", "raw": [], "offered": True})
        out[profile] = rows
    return out


def record(paths: EditPaths, profiles: Optional[list[str]] = None) -> D.DeskRecord:
    """Everything acd_data.collect needs from the desk's own folder. Read-only."""
    hist = read_history(paths)
    undone = {e.get("undoes") for e in hist if e.get("undoes")}
    pending: dict = {}
    for e in hist:
        if e.get("ok") and e.get("kind") == "model" and e.get("profile"):
            if e.get("id") in undone:
                pending.pop(e["profile"], None)
                continue
            pending[e["profile"]] = {"ts": e.get("ts", 0), "model": e.get("after"), "service": e.get("service")}
    last_restart = max((e.get("ts", 0) for e in hist if e.get("kind") == "restart_request" and e.get("ok")),
                       default=0)
    still_asked = paths.restart_request.exists()
    waiting = tuple(str(e.get("what")) for e in hist
                    if e.get("ok") and e.get("needs_restart") and e.get("id") not in undone
                    and (e.get("ts", 0) > last_restart or still_asked))
    last = last_undoable(paths)
    if profiles is None:
        try:
            profiles = D.discover_profiles(paths.profiles, D.load_rules(paths.rules))
        except (OSError, ValueError):
            profiles = None
    off = off_view(paths, profiles)
    for prof in list(off):                                # a backup that is listed in the file is not "off"
        try:
            listed = {(i.provider, i.model) for i in read_block(
                (paths.profiles / prof / "config.yaml").read_text(encoding="utf-8")).items}
        except (OSError, EditError):
            listed = set()
        off[prof] = [r for r in off[prof] if (r.get("provider"), r.get("model")) not in listed
                     and not (r.get("offered") and any(p == NVIDIA[0] for p, _ in listed))]
    return D.DeskRecord(off=off, pending=pending, waiting=waiting, armed=armed(paths),
                        last_change=str(last.get("what")) if last else "")


# ---- helpers shared by the changes -------------------------------------------------------------------------------
def _now(now: Optional[_dt.datetime]) -> _dt.datetime:
    return now or _dt.datetime.now()


def _rules(paths: EditPaths, profile: str) -> dict:
    try:
        return D.rules_for(D.load_rules(paths.rules), profile)
    except (OSError, ValueError):
        return {}


def _name(paths: EditPaths, profile: str) -> str:
    try:
        cfg = A.load(paths.voice.toml)
    except (OSError, ValueError):
        cfg = None
    return D.display_name(profile, cfg, None)


def _config(paths: EditPaths, profile: str) -> Path:
    return paths.profiles / profile / "config.yaml"


def _finish(paths: EditPaths, profile: str, kind: str, what: str, old: str, new: str, expect: dict, before: Any,
            after: Any, now: _dt.datetime, loader: Optional[Loader], undoes: Optional[str], extra: Optional[dict] = None,
            after_write: Optional[Callable[[], None]] = None) -> Result:
    """The common end of a settings-file change: dry run, or the safe write plus one history line."""
    lines = diff_lines(old, new)
    needs = NEEDS[kind]
    if not armed(paths):
        return Result(True, f"DRY RUN, nothing written ({why_not_armed(paths)}). This would happen: {what}",
                      dry=True, lines=lines, needs=needs)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    path = _config(paths, profile)
    try:
        bak = write_config(path, old, new, expect, paths.config_backups / f"{now:%Y-%m-%d}-agent-control-desk", stamp,
                           loader)
    except (EditError, OSError) as exc:
        return Result(False, f"Not changed. {exc}", lines=lines)
    entry = {"id": f"{stamp}-{profile}-{kind}", "ts": now.timestamp(), "who": WHO, "kind": kind, "profile": profile,
             "what": what, "before": before, "after": after, "ok": True,
             "files": [{"path": str(path), "backup": str(bak)}], **(extra or {})}
    if undoes:
        entry["undoes"] = undoes
    if after_write:
        after_write()
    _append_history(paths, entry)
    return Result(True, ("Undone: " if undoes else "Saved: ") + what, lines=lines, needs=needs, entry=entry)


# ---- language model and effort ---------------------------------------------------------------------------------
def model_choices(service: str, current: str) -> list[str]:
    known = list(MODELS.get(service, ()))
    return known if current in known else [current] + known


def change_model(profile: str, model: str, paths: EditPaths, *, now: Optional[_dt.datetime] = None,
                 loader: Optional[Loader] = None, undoes: Optional[str] = None) -> Result:
    now = _now(now)
    name = _name(paths, profile)
    try:
        if _rules(paths, profile).get("model_locked") and not undoes:
            raise EditError(f"{name}'s model is locked: it is shown, never changed from the desk")
        loader = loader or default_loader()
        old = readable(_config(paths, profile), loader)
        have = managed(old)["model"]
        if not have["default"]:
            raise EditError("this settings file has no model line the desk can change; nothing changed")
        if have["default"] == model:
            return Result(True, f"{name} is already on {D.model_name(model)}; nothing to change")
        if not undoes and model not in model_choices(str(have["provider"] or ""), str(have["default"])):
            raise EditError(f"{D.model_name(model)} is not a model {name}'s main service offers; nothing changed")
        what = f"{name}'s model changes from {D.model_name(have['default'])} to {D.model_name(model)}"
        new = set_scalar(old, "model.default", model, f"model was {have['default']}", f"{now:%Y-%m-%d}")
    except EditError as exc:
        return _refused(exc)
    return _finish(paths, profile, "model", what, old, new, {"model.default": model}, have["default"], model, now,
                   loader, undoes, {"service": have["provider"]})


def change_effort(profile: str, effort: str, paths: EditPaths, *, now: Optional[_dt.datetime] = None,
                  loader: Optional[Loader] = None, undoes: Optional[str] = None) -> Result:
    now = _now(now)
    name = _name(paths, profile)
    try:
        if _rules(paths, profile).get("model_locked") and not undoes:
            raise EditError(f"{name}'s thinking effort is locked with its model")
        if effort not in [e for e, _ in EFFORTS] and not undoes:
            raise EditError(f"{effort} is not one of the effort choices; nothing changed")
        loader = loader or default_loader()
        old = readable(_config(paths, profile), loader)
        have = managed(old)["agent"]["reasoning_effort"]
        if have is None:
            raise EditError("this settings file has no thinking-effort line the desk can change; nothing changed")
        if have == effort:
            return Result(True, f"{name} is already on {effort} effort; nothing to change")
        what = f"{name}'s thinking effort changes from {have} to {effort}"
        new = set_scalar(old, "agent.reasoning_effort", effort, f"effort was {have}", f"{now:%Y-%m-%d}")
    except EditError as exc:
        return _refused(exc)
    return _finish(paths, profile, "effort", what, old, new, {"agent.reasoning_effort": effort}, have, effort, now,
                   loader, undoes)


# ---- backups on / off ------------------------------------------------------------------------------------------
def set_backup(profile: str, provider: str, model: str, on: bool, paths: EditPaths, *,
               now: Optional[_dt.datetime] = None, loader: Optional[Loader] = None,
               undoes: Optional[str] = None) -> Result:
    now = _now(now)
    name = _name(paths, profile)
    words = f"{D.model_name(model)} via {D.service_name(provider, short=True)}"
    date = f"{now:%Y-%m-%d}"
    try:
        if _rules(paths, profile).get("model_locked"):
            raise EditError(f"{name} has no backups by design, and the desk does not change that")
        loader = loader or default_loader()
        old = readable(_config(paths, profile), loader)
        listed = [(i.provider, i.model) for i in read_block(old).items]
        off = read_off(paths)
        mine = off.get(profile, [])
        rec = next((r for r in mine if (r.get("provider"), r.get("model")) == (provider, model)), None)
        if on:
            if (provider, model) in listed:
                return Result(True, f"{words} is already on for {name}; nothing to change")
            position = int((rec or {}).get("position") or len(listed) + 1)
            new = insert_backup(old, provider, model, list((rec or {}).get("raw") or ()), position, date)
            what = f"{name}'s backup {words} is switched on"
            keep = [r for r in mine if r is not rec]
        else:
            if (provider, model) not in listed:
                return Result(True, f"{words} is already off for {name}; nothing to change")
            new, raw, position = remove_backup(old, provider, model, date)
            what = f"{name}'s backup {words} is switched off (the desk remembers it and can switch it on again)"
            keep = [r for r in mine if (r.get("provider"), r.get("model")) != (provider, model)]
            keep.append({"provider": provider, "model": model, "position": position, "off_since": date,
                         "note": f"switched off {now:%-d %b}", "raw": raw})
        want = [(i.provider, i.model) for i in read_block(new).items]
    except EditError as exc:
        return _refused(exc)

    def remember() -> None:
        off[profile] = keep
        _write_off(paths, off)

    return _finish(paths, profile, "backup", what, old, new, {"fallback_providers": want}, not on, on, now, loader,
                   undoes, {"provider": provider, "model": model}, remember)


# ---- voice -------------------------------------------------------------------------------------------------------
# ---- the fallback ladder: ONE order for every agent (Peter, 2026-11-17) -----------------------------------------
# Rung 1 is the agent's main model. The backups follow in this order and no other:
#   rung 2  Antigravity: Claude there first, Gemini there second
#   rung 3  ChatGPT (Codex)
#   rung 4  NVIDIA Nemotron
LADDER_MODELS = {      # rung -> (service, the models offered on it)
    "ag_claude": (D.AGY, ("claude-opus-5-5", "claude-sonnet-5-5")),
    "ag_gemini": (D.AGY, ("gemini-3.8-flash", "gemini-4-argon")),
    "codex": ("openai-codex", ("gpt-5.4-mini",)),
    "nvidia": ("nvidia", ("nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3-ultra-550b-a55b")),
}
RUNG_NAME = {"ag_claude": "rung 2a (Antigravity, Claude)", "ag_gemini": "rung 2b (Antigravity, Gemini)",
             "codex": "rung 3 (ChatGPT)", "nvidia": "rung 4 (NVIDIA)"}


def rung_choices(rung: str, current: Optional[str] = None) -> list[str]:
    known = list(LADDER_MODELS[rung][1])
    return known if not current or current in known else [current] + known


def ladder_parts(pairs: list) -> tuple[dict, list]:
    """(rung -> (service, model), the rest in file order). A second entry on a rung counts as an extra."""
    parts: dict = {}
    extras: list = []
    for pair in pairs:
        key = D.rung_of(pair[0], pair[1])
        if key and key not in parts:
            parts[key] = tuple(pair)
        else:
            extras.append(tuple(pair))
    return parts, extras


def ladder_order(pairs: list) -> list:
    """The same backups in the standard order: the ladder's rungs first, anything that is not on the ladder after."""
    parts, extras = ladder_parts(pairs)
    return [parts[k] for k in D.LADDER_KEYS if k in parts] + extras


def pair_words(pair) -> str:
    return f"{D.model_name(pair[1])} via {D.service_name(pair[0], short=True)}"


def pairs_words(pairs: list) -> str:
    return ", then ".join(pair_words(p) for p in pairs) if pairs else "no backups"


def rewrite_backups(text: str, pairs: list, date: str, words: str, raws: Optional[dict] = None) -> str:
    """The text with the backups list holding exactly `pairs`, in that order. An entry that is already there keeps
    its own lines byte for byte; a new one gets two fresh lines (or its remembered lines). Comments that stood
    inside the list are kept above the entries; every line outside the list stays byte for byte."""
    block = read_block(text)
    lines = text.splitlines(keepends=True)
    have = {(i.provider, i.model): lines[i.start:i.end] for i in block.items}
    ind = block.item_indent
    body: list[str] = []
    for prov, model in pairs:
        raw = have.get((prov, model)) or list((raws or {}).get(f"{prov}|{model}") or ())
        if raw and all(isinstance(x, str) for x in raw) and _indent(raw[0]) == ind:
            body += [x if x.endswith("\n") else x + "\n" for x in raw]
        else:
            body += [f"{' ' * ind}- provider: {prov}\n", f"{' ' * ind}  model: {model}\n"]
    en = block.entry
    keep: list[str] = []
    if block.inline_empty:
        if not pairs:
            return text
        lines[en.line] = en.head.rstrip() + (en.tail if en.tail.strip() else "\n")
        start = end = en.line + 1
    else:
        start, end = _block_span(lines, block)
        inside = {n for i in block.items for n in range(i.start, i.end)}
        keep = [lines[n] for n in range(start, end)
                if n not in inside and lines[n].strip().startswith("#") and not _DESK_NOTE.match(lines[n])]
        if not pairs:
            lines[en.line] = en.head.rstrip() + " []" + (en.tail if en.tail.strip() else "\n")
    if start > 0 and not lines[start - 1].endswith("\n"):
        lines[start - 1] += "\n"
    lines[start:end] = keep + [_note(ind, date, words)] + body
    return "".join(lines)


def set_ladder(profile: str, pairs: list, paths: EditPaths, *, what: str = "", now: Optional[_dt.datetime] = None,
               loader: Optional[Loader] = None, undoes: Optional[str] = None, raws: Optional[dict] = None) -> Result:
    """Make an agent's backups list exactly `pairs`, in that order, through the safe path (backup, atomic write,
    Hermes read-back, auto-restore, one history line, undo). The desk remembers what left the list."""
    now = _now(now)
    name = _name(paths, profile)
    date = f"{now:%Y-%m-%d}"
    pairs = [(str(p[0]), str(p[1])) for p in pairs]
    try:
        if _rules(paths, profile).get("model_locked"):
            raise EditError(f"{name} has no backups by design, and the desk does not change that")
        if len(set(pairs)) != len(pairs):
            raise EditError("the same backup is in the new list twice; nothing changed")
        loader = loader or default_loader()
        old = readable(_config(paths, profile), loader)
        block = read_block(old)
        before = [(i.provider, i.model) for i in block.items]
        if before == pairs:
            return Result(True, f"{name}'s backups are already exactly that; nothing to change")
        old_lines = old.splitlines(keepends=True)
        kept = {f"{i.provider}|{i.model}": old_lines[i.start:i.end] for i in block.items}
        known = {f"{r.get('provider')}|{r.get('model')}": list(r.get("raw") or ())      # a remembered entry's own lines
                 for r in read_off(paths).get(profile, ())}
        known.update(raws or {})
        new = rewrite_backups(old, pairs, date, "backups set in the standard ladder order", known)
        if [(i.provider, i.model) for i in read_block(new).items] != pairs:
            raise EditError("the new list did not come out as planned; nothing changed")
    except EditError as exc:
        return _refused(exc)
    what = what or f"{name}'s backups change from {pairs_words(before)} to {pairs_words(pairs)}"
    off = read_off(paths)

    def remember() -> None:
        mine = [r for r in off.get(profile, []) if (r.get("provider"), r.get("model")) not in pairs]
        on_rungs = {D.rung_of(p, m) for p, m in pairs} - {""}
        mine = [r for r in mine if D.rung_of(str(r.get("provider")), str(r.get("model"))) not in on_rungs]
        for n, pair in enumerate(before, 1):
            if pair in pairs or D.rung_of(*pair) in on_rungs:
                continue                                # still listed, or its rung now holds another model
            mine = [r for r in mine if (r.get("provider"), r.get("model")) != pair]
            mine.append({"provider": pair[0], "model": pair[1], "position": n, "off_since": date,
                         "note": f"switched off {now:%-d %b}", "raw": kept.get(f"{pair[0]}|{pair[1]}", [])})
        off[profile] = mine
        _write_off(paths, off)

    return _finish(paths, profile, "ladder", what, old, new, {"fallback_providers": pairs},
                   [list(p) for p in before], [list(p) for p in pairs], now, loader, undoes, {"raws": kept}, remember)


def set_rung(profile: str, rung: str, model: Optional[str], paths: EditPaths, *,
             now: Optional[_dt.datetime] = None, loader: Optional[Loader] = None) -> Result:
    """One rung of one agent's ladder: the model it uses there, or None / "" for off. The whole list is written
    in the standard order, so a list that was out of order is put in order by the same change (and it says so)."""
    name = _name(paths, profile)
    try:
        if rung not in LADDER_MODELS:
            raise EditError(f"{rung} is not a rung of the ladder; nothing changed")
        loader = loader or default_loader()
        old = readable(_config(paths, profile), loader)
        before = [(i.provider, i.model) for i in read_block(old).items]
    except EditError as exc:
        return _refused(exc)
    parts, extras = ladder_parts(before)
    had = parts.get(rung)
    service = LADDER_MODELS[rung][0]
    if model:
        if model not in rung_choices(rung, had[1] if had else None):
            return Result(False, f"{D.model_name(model)} is not a model the desk offers on {RUNG_NAME[rung]}; "
                                 "nothing changed")
        parts[rung] = (service, model)
    else:
        parts.pop(rung, None)
    target = [parts[k] for k in D.LADDER_KEYS if k in parts] + extras
    if target == before:
        state = pair_words(had) if had else "off"
        return Result(True, f"{name}'s {RUNG_NAME[rung]} is already {state}; nothing to change")
    if model and had:
        what = f"{name}'s {RUNG_NAME[rung]} changes from {D.model_name(had[1])} to {D.model_name(model)}"
    elif model:
        what = f"{name}'s {RUNG_NAME[rung]} is switched on: {D.model_name(model)}"
    elif had:
        what = f"{name}'s {RUNG_NAME[rung]} is switched off (it was {D.model_name(had[1])}; the desk remembers it)"
    else:
        what = f"{name}'s {RUNG_NAME[rung]} stays off"
    after_same = [p for p in before if p in target]
    if after_same != [p for p in target if p in before]:
        what += "; the list is also put in the standard order"
    return set_ladder(profile, target, paths, what=what, now=now, loader=loader)


@dataclass
class LadderPlan:
    profile: str
    name: str
    before: list
    after: list
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def changes(self) -> bool:
        return not self.error and self.before != self.after


def plan_standard_ladder(paths: EditPaths, profiles: Optional[list[str]] = None, extras: str = "keep",
                         loader: Optional[Loader] = None) -> list[LadderPlan]:
    """What "apply the standard ladder" would do to every agent that is not locked. READ-ONLY, whatever the armed
    switch says: it reads each settings file and writes nothing.

    Each agent keeps its own model on a rung where it already has one. A missing rung gets: on Antigravity the
    same Claude model as its main model, then Gemini 3.8 Flash; on ChatGPT the model an agent already uses there;
    on NVIDIA the Nemotron model it already lists (or remembers), else the one most agents list.
    extras="keep" leaves backups that are not on the ladder after it; "drop" takes them off the list."""
    try:
        rules = D.load_rules(paths.rules)
    except (OSError, ValueError):
        rules = {}
    if profiles is None:
        profiles = D.discover_profiles(paths.profiles, rules)
    loader = loader or default_loader()
    off = read_off(paths)
    read: dict = {}
    for prof in profiles:
        if D.rules_for(rules, prof).get("model_locked"):
            continue
        try:
            text = readable(_config(paths, prof), loader)
            read[prof] = (managed(text)["model"], [(i.provider, i.model) for i in read_block(text).items], "")
        except EditError as exc:
            read[prof] = ({}, [], str(exc))
    codex = next((p for prof in read for p in read[prof][1] if D.rung_of(*p) == "codex"),
                 ("openai-codex", LADDER_MODELS["codex"][1][0]))
    seen_nv = [p[1] for prof in read for p in read[prof][1] if D.rung_of(*p) == "nvidia"]
    seen_nv += [str(r.get("model")) for prof in read for r in off.get(prof, ()) if r.get("provider") == "nvidia"]
    common_nv = max(sorted(set(seen_nv)), key=seen_nv.count) if seen_nv else NVIDIA[1]
    out = []
    for prof, (main, before, err) in read.items():
        name = _name(paths, prof)
        if err:
            out.append(LadderPlan(prof, name, [], [], error=err))
            continue
        parts, rest = ladder_parts(before)
        notes = []
        main_model = str(main.get("default") or "")
        want = dict(parts)
        if "ag_claude" not in want:
            model = main_model if main_model in LADDER_MODELS["ag_claude"][1] else LADDER_MODELS["ag_claude"][1][0]
            want["ag_claude"] = (D.AGY, model)
            notes.append(f"adds rung 2a, Claude on Antigravity: {D.model_name(model)} (the same as its main model)")
        if "ag_gemini" not in want:
            want["ag_gemini"] = (D.AGY, "gemini-3.8-flash")
            notes.append("adds rung 2b, Gemini on Antigravity: Gemini 3.8 Flash")
        if "codex" not in want:
            want["codex"] = tuple(codex)
            notes.append(f"adds rung 3, ChatGPT: {D.model_name(codex[1])}")
        if "nvidia" not in want:
            mine = next((str(r.get("model")) for r in off.get(prof, ()) if r.get("provider") == "nvidia"), "")
            model = mine or common_nv
            want["nvidia"] = ("nvidia", model)
            notes.append(f"adds rung 4, NVIDIA: {D.model_name(model)}"
                         + (" (it was switched off for this agent before)" if mine else ""))
        after = [want[k] for k in D.LADDER_KEYS]
        if [p for p in before if p in after] != [p for p in after if p in before]:
            notes.append("puts the rungs it already has in the standard order")
        for pair in rest:
            if extras == "keep":
                after.append(pair)
                notes.append(f"keeps {pair_words(pair)} after the ladder (it is not on the standard ladder)")
            else:
                notes.append(f"takes {pair_words(pair)} off the list (it is not on the standard ladder; "
                             "the desk remembers it)")
        if before == after:
            notes = ["already on the standard ladder: nothing changes"]
        out.append(LadderPlan(prof, name, before, after, notes))
    return out


def plan_lines(plan: LadderPlan) -> list[str]:
    """One agent's before and after, as the lines a result shows."""
    if plan.error:
        return [f"  could not be read: {plan.error}"]
    out = [f"- before: {pairs_words(plan.before)}", f"+ after:  {pairs_words(plan.after)}"]
    return out + ["  " + n for n in plan.notes]


def apply_standard_ladder(paths: EditPaths, *, dry_run: bool = False, extras: str = "keep",
                          profiles: Optional[list[str]] = None, now: Optional[_dt.datetime] = None,
                          loader: Optional[Loader] = None) -> list[Result]:
    """Put every agent that is not locked on the standard ladder. dry_run=True writes nothing whatever the armed
    switch says: every result is a dry run that lists the agent's before and after. Otherwise each agent goes
    through set_ladder (which is itself a dry run on a desk that is not armed); u undoes the newest one."""
    results = []
    for plan in plan_standard_ladder(paths, profiles, extras, loader):
        if plan.error:
            results.append(Result(False, f"{plan.name}: not changed. {plan.error}"))
        elif not plan.changes:
            results.append(Result(True, f"{plan.name} is already on the standard ladder; nothing to change",
                                  dry=dry_run))
        elif dry_run:
            results.append(Result(True, f"DRY RUN, nothing written. This would happen: {plan.name}'s backups are "
                                        "set to the standard ladder", dry=True, lines=plan_lines(plan),
                                  needs=NEEDS["ladder"]))
        else:
            res = set_ladder(plan.profile, plan.after, paths, now=now, loader=loader,
                             what=f"{plan.name}'s backups are set to the standard ladder: {pairs_words(plan.after)}")
            if res.ok and not res.lines:
                res.lines = plan_lines(plan)
            results.append(res)
    return results


VOICE_FIELDS = ("voice", "voice_zh", "speed", "effect")


def speed_word(speed: Optional[float]) -> str:
    for word, value in SPEEDS:
        if speed is not None and abs(value - float(speed)) < 1e-9:
            return word
    return "not set" if speed is None else f"{speed:g}"


OTHER_VOICE = "::other"                              # "other, type it…": a line to type a voice name


def effect_applies(agent: Optional[A.Agent], voice: Optional[str]) -> bool:
    """A voice effect (Simba's) works on Kokoro voices only: a Microsoft voice is spoken plain."""
    import simba_tts
    return not (voice and simba_tts.is_edge(voice))


def check_typed_voice(agent: A.Agent, field_name: str, text: str) -> tuple[str, str]:
    """(clean name, "") for a typed voice name this agent can take today, else ("", a sentence why not). The same
    check as the pick-list and every other edit (voiceedit.check_voice: the bank, the agent's own script)."""
    try:
        return str(voiceedit.check_voice(field_name, text, agent)), ""
    except voiceedit.EditError as exc:
        return "", str(exc)


def voice_options(agent: A.Agent, field_name: str, cfg: dict) -> list[tuple[str, str, bool, bool]]:
    """The pop-up's lines: (token, label, is today's, cannot be picked). The whole voice bank for the language
    (Peter 2026-11-18: "yes" to the full voice list for every agent): local Kokoro voices first, Microsoft below,
    then "silent", then "other, type it…". A voice this agent's own script cannot make yet is SHOWN, marked
    "not playable yet", and cannot be picked (voicepick.not_yet). The effect list is the named samples only."""
    import voicepick
    _title, options, why = voicepick.menu(agent, field_name, cfg)
    out = []
    for o in options:
        if o.token == voicepick.OTHER:
            if field_name != "effect":                  # never a typed recipe for the effect
                out.append((OTHER_VOICE, "other, type it… (any voice name in the bank)", False, False))
            continue
        if field_name == "effect":                      # the named samples and starting points; never a typed recipe
            out.append((o.token, o.label, o.current, False))
            continue
        if o.token == voicepick.SILENT:
            out.append(("", "silent: no voice for this language", o.current, False))
            continue
        name = o.token[len(voicepick.NOT_YET):] if o.token.startswith(voicepick.NOT_YET) else o.token
        out.append((name, o.label, o.current, o.disabled and not o.current))
    return out


def _speed_change(agent_key: str, value: Optional[float], vp: voiceedit.EditPaths, now: _dt.datetime) -> Result:
    """Speed is one line of agents.toml plus whatever voicecfg syncs for it (tts.edge.speed), same safe path."""
    stamp = now.strftime("%Y%m%d-%H%M%S")
    old_text = Path(vp.toml).read_text(encoding="utf-8")
    cfg = tomllib.loads(old_text)
    agent = voiceedit._find_agent(cfg, agent_key)
    if agent.speed == value:
        return Result(True, f"{agent.display}'s speed is already {speed_word(value)}; nothing to change")
    new_text = voiceedit.set_field(old_text, agent_key, "speed", value)
    Path(vp.toml_backups).mkdir(parents=True, exist_ok=True)
    bak = Path(vp.toml_backups) / f"{stamp}-agents.toml"
    shutil.copy2(vp.toml, bak)
    voiceedit._atomic_write(vp.toml, new_text)
    try:
        items = [i for i in voicecfg.collect(A.load(vp.toml), vp.profiles, vp.claude_conf, vp.gemini_conf)
                 if i.where == agent_key and i.key.endswith(".speed")]
        _done, errors = voicecfg.apply(items, Path(vp.hermes_backups) / f"{stamp}-agent-control-desk")
    except Exception as exc:                            # noqa: BLE001
        voiceedit._atomic_write(vp.toml, old_text)
        return Result(False, f"Not changed. {exc}; the Voice Desk list was put back as it was")
    if errors:
        voiceedit._atomic_write(vp.toml, old_text)
        return Result(False, f"Not changed ({errors[0]}); the Voice Desk list was put back as it was")
    return Result(True, f"{agent.display}'s speaking speed changes from {speed_word(agent.speed)} to {speed_word(value)}",
                  entry={"before": agent.speed, "after": value})


def _voice_do(agent_key: str, field_name: str, value: Any, vp: voiceedit.EditPaths, now: _dt.datetime,
              undo: bool) -> Result:
    if field_name == "speed":
        return _speed_change(agent_key, value, vp, now)
    cfg = A.load(vp.toml)
    agent = voiceedit._find_agent(cfg, agent_key)
    before = {"voice": agent.voice, "voice_zh": agent.voice_zh, "effect": agent.effect}[field_name]
    res = voiceedit.change(agent_key, field_name, value or None, vp, now=now, kind="undo" if undo else "change")
    if not res.ok:
        return Result(False, "Not changed. " + res.message)
    if res.entry is None:
        return Result(True, res.message)
    if field_name == "effect":
        return Result(True, f"{agent.display}'s voice effect changes from {before or 'none'} to {value or 'none'}",
                      entry={"before": before, "after": value or None})
    lang = "English" if field_name == "voice" else "Chinese"
    return Result(True, f"{agent.display}'s {lang} voice changes from {D.voice_label(before)} to "
                        f"{D.voice_label(value or None)}", entry={"before": before, "after": value or None})


def _sandbox(vp: voiceedit.EditPaths) -> tuple[Path, voiceedit.EditPaths, dict[Path, Path]]:
    """A throw-away copy of every file a voice change could touch, so a dry run can run the REAL path on the
    copies and show exactly what would change. (live path -> its copy)"""
    root = Path(tempfile.mkdtemp(prefix="acd-dryrun-"))
    pairs: dict[Path, Path] = {}
    (root / "profiles").mkdir()
    toml = root / "agents.toml"
    shutil.copy2(vp.toml, toml)
    pairs[Path(vp.toml)] = toml
    try:
        folders = [p for p in Path(vp.profiles).iterdir() if (p / "config.yaml").is_file()]
    except OSError:
        folders = []
    for folder in folders:
        (root / "profiles" / folder.name).mkdir()
        shutil.copy2(folder / "config.yaml", root / "profiles" / folder.name / "config.yaml")
        pairs[folder / "config.yaml"] = root / "profiles" / folder.name / "config.yaml"
    confs = {}
    for label, live in (("claude", vp.claude_conf), ("gemini", vp.gemini_conf)):
        copy = root / f"{label}-voice-defaults.conf"
        if live and Path(live).is_file():
            shutil.copy2(live, copy)
            pairs[Path(live)] = copy
        confs[label] = copy if live else None
    box = voiceedit.EditPaths(toml=toml, history=root / "history.jsonl", toml_backups=root / "toml-backups",
                              hermes_backups=root / "hermes-backups", profiles=root / "profiles",
                              claude_conf=confs["claude"], gemini_conf=confs["gemini"])
    return root, box, pairs


def _file_words(path: Path) -> str:
    if path.name == "agents.toml":
        return "the Voice Desk list"
    if path.name == "config.yaml":
        return f"{path.parent.name}'s settings file"
    return path.name


def change_voice(agent_key: str, field_name: str, value: Any, paths: EditPaths, *,
                 now: Optional[_dt.datetime] = None, undoes: Optional[str] = None) -> Result:
    """Change an agent's English voice, Chinese voice or speaking speed. value None / "" = silent (or no speed)."""
    now = _now(now)
    if field_name not in VOICE_FIELDS:
        return Result(False, f"{field_name} is not a voice setting the desk changes")
    if field_name == "speed" and value is not None and not undoes \
            and not any(abs(v - float(value)) < 1e-9 for _, v in SPEEDS):
        return Result(False, "that speed is not one of the choices; nothing changed")
    live = armed(paths)
    try:
        if live:
            res = _voice_do(agent_key, field_name, value, paths.voice, now, bool(undoes))
            lines: list[str] = []
        else:
            root, box, pairs = _sandbox(paths.voice)
            try:
                before = {livep: copy.read_text(encoding="utf-8") for livep, copy in pairs.items()}
                res = _voice_do(agent_key, field_name, value, box, now, bool(undoes))
                lines = []
                for livep, copy in pairs.items():
                    after = copy.read_text(encoding="utf-8") if copy.exists() else ""
                    if after != before[livep]:
                        lines.append(f"in {_file_words(livep)}:")
                        lines += ["  " + ln for ln in diff_lines(before[livep], after)]
            finally:
                shutil.rmtree(root, ignore_errors=True)
    except (voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return Result(False, f"Not changed. {exc}")
    if not res.ok or res.entry is None:
        return res
    what = res.message
    if not live:
        return Result(True, f"DRY RUN, nothing written ({why_not_armed(paths)}). This would happen: {what}", dry=True,
                      lines=lines, needs=NEEDS["voice"])
    stamp = now.strftime("%Y%m%d-%H%M%S")
    entry = {"id": f"{stamp}-{agent_key}-{field_name}", "ts": now.timestamp(), "who": WHO, "kind": "voice",
             "agent": agent_key, "field": field_name, "what": what, "before": res.entry["before"],
             "after": res.entry["after"], "ok": True}
    if undoes:
        entry["undoes"] = undoes
    _append_history(paths, entry)
    return Result(True, ("Undone: " if undoes else "Saved: ") + what, needs=NEEDS["voice"], entry=entry)


SPEED_FIXED = "fixed by this voice: its speaker has no speed setting the desk can change"


def speed_fixed(agent_key: str, paths: EditPaths) -> str:
    """Why this agent's speaking speed cannot be changed from the desk; "" when it can. The REAL change is run
    on throw-away copies (never on the live files), and the answer is whether it would go through."""
    try:
        agent = voiceedit._find_agent(A.load(paths.voice.toml), agent_key)
    except Exception as exc:                            # noqa: BLE001
        return f"the Voice Desk list could not be read ({type(exc).__name__})"
    trial = next(v for _, v in SPEEDS if agent.speed is None or abs(v - float(agent.speed)) > 1e-9)
    try:
        root, box, _pairs = _sandbox(paths.voice)
    except OSError as exc:
        return f"the desk could not try it on a copy ({exc.strerror or exc})"
    try:
        res = _speed_change(agent_key, trial, box, _dt.datetime.now())
    except Exception:                                   # noqa: BLE001
        return SPEED_FIXED
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return "" if res.ok else SPEED_FIXED


def real_voice_test(agent: A.Agent, lang: str, voice: str) -> tuple[bool, str]:
    """Make one sentence in this voice to a throw-away file and check it is real sound. Nothing is played."""
    import voicetest
    tmp = Path(tempfile.mkdtemp(prefix="acd-voicetest-"))
    try:
        txt, out = tmp / f"{lang}.txt", tmp / f"{lang}.mp3"
        txt.write_text(voicetest.SENTENCES.get(lang, voicetest.SENTENCES["en"]).format(name=agent.display),
                       encoding="utf-8")
        via = voicetest._real_synth(txt, out, voice, agent, lang)
        if not via or not out.exists():
            return False, "the voice could not be made, so that line would be silent"
        ok, seconds, what = voicetest.probe(out)
        return ok, (f"{seconds:.1f} seconds of real sound was made ({what}); nothing was played" if ok else what)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



# ---- spoken replies, place by place (version four: the old Voice Desk grid's switches) -----------------------------
def _plan_words(change: dict) -> str:
    """One planned file change of the Voice Desk's own switch code, in plain words."""
    where = _file_words(Path(str(change.get("path", ""))))
    kind = change.get("t")
    if kind == "marker":
        return f"{where}: the marker file is {'made' if change.get('after') else 'taken away'}"
    if kind == "jsonlist":
        bits = [f"add {x}" for x in change.get("add", ())] + [f"take out {x}" for x in change.get("remove", ())]
        return f"{where}: {change.get('key')}: " + ", ".join(bits)
    return f"{where}: {change.get('key')}: {change.get('before')!r} -> {change.get('after')!r}"


def voice_place_menu(agent_key: str, place: str, paths: EditPaths) -> tuple[str, list, str]:
    """(title, [(token, label, is today's)], why it cannot be switched). The Voice Desk grid's own menu."""
    import gridstate as G
    import onoff
    if paths.grid is None:
        return "", [], "the Voice Desk's own files are not known to the desk"
    agent = voiceedit._find_agent(A.load(paths.voice.toml), agent_key)
    cell = G.CELL_FUNCS[place](agent, paths.grid)
    menu = onoff.menu(agent, place, cell, paths.grid)
    return menu.title, [(o.token, o.label, o.current) for o in menu.options if not o.disabled], menu.why_not


def set_voice_place(agent_key: str, place: str, token: str, paths: EditPaths, *,
                    now: Optional[_dt.datetime] = None) -> Result:
    """Switch spoken replies at one place, with the Voice Desk grid's OWN switch code (onoff.set_cell): the same
    files, the same backup, read-back and put-back. Not armed = the plan is shown and nothing is written."""
    import gridstate as G
    import onoff
    import onoff_hermes
    now = _now(now)
    if paths.grid is None:
        return Result(False, "Not changed. The Voice Desk's own files are not known to the desk.")
    try:
        agent = voiceedit._find_agent(A.load(paths.voice.toml), agent_key)
        if not onoff.can_switch(agent, place):
            why = onoff.menu(agent, place, G.Cell(G.UNKNOWN), paths.grid).why_not or "this place has no switch"
            raise EditError(why[0].upper() + why[1:])
        before = "queue" if place == "mic" else G.CELL_FUNCS[place](agent, paths.grid).value
        if place == "tg_group":
            changes, note = onoff._plan_tg_group(agent, token, paths.grid)
        elif place == "terminal":
            changes, note = (onoff._plan_claude(agent, token, paths.grid, paths.voice)
                             if G.kind_of(agent) == G.CLAUDE else onoff_hermes.plan_terminal(agent, token, paths.grid))
        elif place == "tg_dm":
            changes, note = onoff_hermes.plan_tg_dm(agent, token, paths.grid)
        else:
            changes, note = onoff._plan_mic(token, paths.grid)
    except (EditError, onoff.SwitchError, voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return Result(False, f"Not changed. {exc}")
    who = "Voice Desk (all agents)" if place == "mic" else agent.display
    what = f"{who}: spoken replies, {D.place_words(place)}, change to {onoff._label(place, token)}"
    if not changes:
        return Result(True, f"{who}: already {onoff._label(place, token)} there; nothing to change")
    lines = ["+ " + _plan_words(c) for c in changes]
    needs = NEEDS["voice_place"] + (note if note else "")
    if not armed(paths):
        return Result(True, f"DRY RUN, nothing written ({why_not_armed(paths)}). This would happen: {what}",
                      dry=True, lines=lines, needs=needs)
    res = onoff.set_cell(agent_key, place, token, paths.grid, paths.voice, now=now)
    if not res.ok:
        return Result(False, "Not changed. " + res.message, lines=lines)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    entry = {"id": f"{stamp}-{agent_key}-place-{place}", "ts": now.timestamp(), "who": WHO, "kind": "voice_place",
             "agent": agent_key, "place": place, "what": what, "before": before, "after": token, "ok": True,
             "switch": res.entry}
    _append_history(paths, entry)
    return Result(True, "Saved: " + what, lines=lines, needs=needs, entry=entry)


def undo_voice_place(last: dict, paths: EditPaths, *, now: Optional[_dt.datetime] = None) -> Result:
    import onoff
    now = _now(now)
    if not armed(paths):
        return Result(True, f"DRY RUN, nothing written ({why_not_armed(paths)}). This would happen: undo of: "
                            f"{last.get('what')}", dry=True)
    res = onoff.undo(dict(last.get("switch") or {}), paths.voice, paths.grid)
    if not res.ok:
        return Result(False, "Not undone. " + res.message)
    entry = {"id": f"{now:%Y%m%d-%H%M%S}-{last.get('agent')}-place-undo", "ts": now.timestamp(), "who": WHO,
             "kind": "voice_place", "agent": last.get("agent"), "place": last.get("place"),
             "what": "undo of: " + str(last.get("what")), "before": last.get("after"), "after": last.get("before"),
             "ok": True, "undoes": last["id"], "switch": res.entry}
    _append_history(paths, entry)
    return Result(True, "Undone: " + str(last.get("what")), needs=NEEDS["voice_place"], entry=entry)


# ---- the talk rules (version three) ------------------------------------------------------------------------------
RULE_KEYS = {"speaks_in_team_room": bool, "hands_out_work": bool, "only_job_cards": bool, "takes_work_from": str,
             "answers": str}
_PROFILE_OK = re.compile(r"^[A-Za-z0-9_\-]+$")


def _rule_words(key: str, value: Any, names: dict) -> str:
    if key == "speaks_in_team_room":
        return "may speak in the Team Room" if value else "may not speak in the Team Room"
    if key == "hands_out_work":
        return "hands out work" if value else "does not hand out work"
    if key == "only_job_cards":
        return "takes work only as job cards" if value else "is not limited to job cards"
    if key == "takes_work_from":
        return f"takes work from {names.get(value, value)}" if value else "takes work from nobody (Peter only)"
    return "answers every line" if value == "always" else "answers when named in the line"


def read_rules_text(paths: EditPaths) -> tuple[str, dict]:
    """The rules file as text and as data. A file the desk does not recognise is refused: nothing changes."""
    try:
        text = Path(paths.rules).read_text(encoding="utf-8")
    except OSError as exc:
        raise EditError(f"the rules file could not be read ({exc.strerror or exc}); nothing changed") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise EditError(f"the rules file cannot be read as it stands ({str(exc)[:100]}); nothing changed") from exc
    if not isinstance(data.get("defaults"), dict) or not isinstance(data.get("agents", {}), dict):
        raise EditError("the rules file is not in the shape the desk knows (no [defaults] or [agents] part); "
                        "nothing changed")
    return text, data


def rules_text_with(text: str, profile: str, changes: dict) -> str:
    """The rules text with `changes` set under [agents.<profile>] (None takes a line out). Only those lines move."""
    if not _PROFILE_OK.match(profile):
        raise EditError(f"{profile!r} is not a name the desk can write into the rules file; nothing changed")
    lines = text.splitlines(keepends=True)
    try:
        voiceedit._block(lines, profile)
    except voiceedit.EditError:
        if all(v is None for v in changes.values()):
            return text
        text = text + ("" if text.endswith("\n") else "\n") + f"\n[agents.{profile}]\n"
    try:
        for key, value in changes.items():
            text = voiceedit.set_field(text, profile, key, value)
    except voiceedit.EditError as exc:
        raise EditError(str(exc).replace("agents.toml", "the rules file")) from exc
    return text


def change_rules(profile: str, changes: dict, paths: EditPaths, *, now: Optional[_dt.datetime] = None,
                 undoes: Optional[str] = None) -> Result:
    """Set talk rules for one agent in acd_rules.toml. `changes` maps a rule key to its new value; None takes the
    agent's own line out, so the default applies again (that is what an undo of a first change does)."""
    now = _now(now)
    name = _name(paths, profile)
    try:
        for key, value in changes.items():
            kind = RULE_KEYS.get(key)
            if kind is None:
                raise EditError(f"{key!r} is not a talk rule the desk knows; nothing changed")
            if value is not None and not isinstance(value, kind):
                raise EditError(f"{key} cannot be set to {value!r}; nothing changed")
            if key == "answers" and value not in (None, "always", "when_named"):
                raise EditError(f"answers cannot be set to {value!r}; nothing changed")
        old, data = read_rules_text(paths)
        mine = data.get("agents", {}).get(profile, {})
        mine = mine if isinstance(mine, dict) else {}
        effective = D.rules_for(data, profile)
        if effective.get("answers_locked") and "answers" in changes and not undoes:
            raise EditError(f"how {name} answers is kept as it is; nothing changed")
        before = {k: mine.get(k) for k in changes}
        todo = {}
        for k, v in changes.items():
            if v is None:
                if k in mine:                               # take the agent's own line out: the default applies again
                    todo[k] = None
            elif effective.get(k) != v or (undoes and mine.get(k) != v):
                todo[k] = v
        if not todo:
            return Result(True, f"{name}'s talk rules already say that; nothing to change")
        new = rules_text_with(old, profile, todo)
        after_data = tomllib.loads(new)
        after_eff = D.rules_for(after_data, profile)
    except EditError as exc:
        return _refused(exc)
    names = {p: _name(paths, p) for p in data.get("agents", {})}
    parts = [_rule_words(k, after_eff.get(k), names) for k in todo]
    what = f"{name} now " + " and ".join(parts)
    lines = diff_lines(old, new)
    if not armed(paths):
        return Result(True, f"DRY RUN, nothing written ({why_not_armed(paths)}). This would happen: {what}",
                      dry=True, lines=lines, needs=NEEDS["rule"])
    stamp = now.strftime("%Y%m%d-%H%M%S")
    path = Path(paths.rules)
    try:
        if path.read_text(encoding="utf-8") != old:
            raise EditError("the rules file changed while the edit was prepared; nothing written, try again")
        paths.rules_backups.mkdir(parents=True, exist_ok=True)
        bak = paths.rules_backups / f"{stamp}-{path.name}"
        n = 1
        while bak.exists():
            n += 1
            bak = paths.rules_backups / f"{stamp}-{n}-{path.name}"
        shutil.copy2(path, bak)
        voicecfg._atomic_write(path, new)
        bad = []
        try:
            got = D.rules_for(D.load_rules(path), profile)
            bad = [f"{k} reads back as {got.get(k)!r}" for k in todo if got.get(k) != after_eff.get(k)]
        except Exception as exc:                            # noqa: BLE001  any doubt at all = put it back
            bad = [f"it could not be read back ({str(exc)[:100]})"]
        if bad:
            voicecfg._atomic_write(path, old)
            raise EditError("the change did not read back right, so the rules file was put back exactly as it "
                            "was: " + "; ".join(bad))
    except (EditError, OSError) as exc:
        return Result(False, f"Not changed. {exc}", lines=lines)
    entry = {"id": f"{stamp}-{profile}-rule", "ts": now.timestamp(), "who": WHO, "kind": "rule", "profile": profile,
             "what": what, "before": {k: before[k] for k in todo}, "after": {k: after_eff.get(k) for k in todo},
             "ok": True, "files": [{"path": str(path), "backup": str(bak)}]}
    if undoes:
        entry["undoes"] = undoes
    _append_history(paths, entry)
    return Result(True, ("Undone: " if undoes else "Saved: ") + what, lines=lines, needs=NEEDS["rule"], entry=entry)


# ---- Team Room membership: both doors, or nothing (version three) ----------------------------------------------
Runner = Callable[[list], tuple]     # (the Team Room command's words) -> (exit code, what it printed)


def _members(paths: EditPaths) -> dict:
    data = D.read_members(paths.members) if paths.members else None
    if data is None:
        raise EditError("the Team Room members file could not be read, so the desk will not change it; "
                        "nothing changed")
    rows = data["members"]
    if not all(isinstance(m, dict) and isinstance(m.get("profile"), str) for m in rows) or not data.get("lead"):
        raise EditError("the Team Room members file is not in the shape the desk knows; nothing changed")
    return data


def _has_bot(paths: EditPaths, profile: str) -> Optional[bool]:
    plats = D.read_gateway_platforms(paths.gateway_state)
    if plats is None:
        return None
    return f"{profile}:telegram" in plats


def _addon_on(paths: EditPaths, profile: str, loader: Optional[Loader]) -> Optional[bool]:
    try:
        path = _config(paths, profile)
        cfg = loader(path) if loader else None
        if not isinstance(cfg, dict):
            return None
        return D.TEAM_ROOM_PLUGIN in D.addons(cfg)
    except Exception:                                       # noqa: BLE001
        return None


def _room_name(m: dict) -> str:
    return str(m.get("name") or m.get("handle") or m.get("profile"))


def set_team_room(profile: str, on: bool, paths: EditPaths, *, runner: Optional[Runner] = None,
                  now: Optional[_dt.datetime] = None, loader: Optional[Loader] = None, undoes: Optional[str] = None,
                  handle: str = "", name: str = "") -> Result:
    """Put an agent into the Team Room or take it out. ONE path for both doors: the Team Room's own add / kick
    changes the terminal room and the Telegram group together. The desk then checks both; if they do not agree,
    it runs the command backwards and reports that nothing changed."""
    now = _now(now)
    shown = _name(paths, profile)
    try:
        data = _members(paths)
        rows = data["members"]
        mine = next((m for m in rows if m.get("profile") == profile), None)
        if profile == data.get("lead"):
            raise EditError(f"{shown} leads the Team Room and cannot be taken out of it; nothing changed")
        if not _config(paths, profile).is_file():
            raise EditError(f"there is no agent called {profile}; nothing changed")
        if on and mine is not None:
            return Result(True, f"{shown} is already in the Team Room; nothing to change")
        if not on and mine is None:
            return Result(True, f"{shown} is already out of the Team Room; nothing to change")
    except EditError as exc:
        return _refused(exc)
    loader = loader or default_loader()
    bot = _has_bot(paths, profile)
    before_names = [_room_name(m) for m in rows]
    if on:
        handle = (handle or profile).strip().lstrip("@").lower().replace("_", "-")
        name = name.strip() or shown
        words = ["add", profile, handle, name]
        after_names = before_names + [name]
        what = f"{shown} joins the Team Room (the terminal room and the Telegram group together)"
        tail = ("Its own Telegram bot has to be in the Telegram group to speak there; putting a bot into the group is "
                "Peter's own tap in Telegram, and until then the lead's bot speaks for it."
                if bot else "The lead's bot speaks its lines in the Telegram group, text only.")
    else:
        handle, name = str(mine.get("handle") or profile), _room_name(mine)
        words = ["kick", handle]
        after_names = [n for m, n in zip(rows, before_names) if m is not mine]
        what = f"{shown} leaves the Team Room (the terminal room and the Telegram group together)"
        tail = ("Its Telegram bot is taken out of the Telegram group; putting the bot back later is Peter's own tap "
                "in Telegram." if bot else "It had no bot of its own in the Telegram group.")
    lines = ["- Team Room members: " + ", ".join(before_names), "+ Team Room members: " + ", ".join(after_names),
             "  runs the Team Room's own command: team-room " + " ".join(words)]
    needs = NEEDS["team_room"] + " " + tail
    if not armed(paths):
        return Result(True, f"DRY RUN, nothing written ({why_not_armed(paths)}). This would happen: {what}",
                      dry=True, lines=lines, needs=needs)
    if runner is None:
        import acd_room                 # the one module that starts a program: the Team Room's own command
        runner = acd_room.default_runner(paths.team_room_cmd)
    run = runner
    code, said = run(words)
    said = str(said or "").strip()
    first = said.splitlines()[0][:200] if said else ""
    try:
        after = _members(paths)
    except EditError as exc:
        return Result(False, f"Not confirmed. The Team Room's command ran (it said: {first or 'nothing'}), but "
                             f"afterwards {exc}", lines=lines)
    is_in = any(m.get("profile") == profile for m in after["members"])
    if is_in != on:
        return Result(False, "Not changed. The Team Room did not make the change, so nothing is different at "
                             f"either door. It said: {first or 'nothing'}", lines=lines)
    addon = _addon_on(paths, profile, loader) if bot else None
    if bot and addon is not None and addon != on:
        back = ["kick", handle] if on else ["add", profile, handle, name]
        _code2, said2 = run(back)
        try:
            again = _members(paths)
            put_back = any(m.get("profile") == profile for m in again["members"]) != on
        except EditError:
            put_back = False
        return Result(False, "Not changed. The terminal room changed but the Telegram side did not follow, so the "
                             "desk ran the Team Room's command backwards: "
                             + ("the room is as it was." if put_back else
                                "AND THAT DID NOT WORK: the two doors do not agree now; look at the Team Room. "
                                f"It said: {str(said2 or '').strip()[:160]}"), lines=lines)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    import acd_room
    restart = acd_room.says_restart(said)
    entry = {"id": f"{stamp}-{profile}-team_room", "ts": now.timestamp(), "who": WHO, "kind": "team_room",
             "profile": profile, "what": what, "before": not on, "after": on, "ok": True, "handle": handle,
             "name": name, "room_before": data.get("room_id"), "room_after": after.get("room_id"),
             "said": said[:600]}
    if restart:
        entry["needs_restart"] = True
        entry["what"] = what + "; the agents' system did not reload its add-ons, so the Telegram side waits for a restart"
        needs = ("A RESTART IS NEEDED for the Telegram side: the running system did not reload its add-ons. "
                 "Press R to review. " + tail)
    if undoes:
        entry["undoes"] = undoes
    _append_history(paths, entry)
    return Result(True, ("Undone: " if undoes else "Saved: ") + entry["what"], lines=lines, needs=needs, entry=entry)


# ---- undo ------------------------------------------------------------------------------------------------------------
def undo(paths: EditPaths, *, now: Optional[_dt.datetime] = None, loader: Optional[Loader] = None,
         runner: Optional[Callable[[list], tuple]] = None) -> Result:
    """Run the newest change backwards, through the same safe path."""
    last = last_undoable(paths)
    if last is None:
        return Result(False, "There is nothing to undo.")
    kind = last["kind"]
    if kind == "model":
        return change_model(last["profile"], last["before"], paths, now=now, loader=loader, undoes=last["id"])
    if kind == "effort":
        return change_effort(last["profile"], last["before"], paths, now=now, loader=loader, undoes=last["id"])
    if kind == "backup":
        return set_backup(last["profile"], last["provider"], last["model"], bool(last["before"]), paths, now=now,
                          loader=loader, undoes=last["id"])
    if kind == "ladder":
        return set_ladder(last["profile"], [tuple(p) for p in last["before"]], paths, now=now, loader=loader,
                          undoes=last["id"], raws=dict(last.get("raws") or {}),
                          what=f"{_name(paths, last['profile'])}'s backups go back to "
                               f"{pairs_words([tuple(p) for p in last['before']])}")
    if kind == "rule":
        return change_rules(last["profile"], dict(last["before"]), paths, now=now, undoes=last["id"])
    if kind == "voice_place":
        return undo_voice_place(last, paths, now=now)
    if kind == "agent":                                   # an agent added to, or taken out of, the Voice Desk list
        import acd_parity
        return acd_parity.undo_agent(last, paths, now=now)
    if kind == "team_room":
        return set_team_room(last["profile"], bool(last["before"]), paths, now=now, loader=loader, runner=runner,
                             undoes=last["id"], handle=str(last.get("handle") or ""), name=str(last.get("name") or ""))
    return change_voice(last["agent"], last["field"], last["before"], paths, now=now, undoes=last["id"])


# ---- the restart request ----------------------------------------------------------------------------------------------
RESTART_WORDS = ("The desk restarts nothing itself. It leaves a request for the restart helper, which waits for a "
                 "quiet moment (no agent mid-reply, no job running) and then restarts the agents' system once.")


def request_restart(paths: EditPaths, reason: str, *, now: Optional[_dt.datetime] = None) -> Result:
    now = _now(now)
    body = {"who": "agent-control-desk", "reason": reason[:200], "requested_at": now.astimezone().isoformat(timespec="seconds")}
    if not armed(paths):
        return Result(True, f"DRY RUN, nothing written ({why_not_armed(paths)}). This would happen: a restart request "
                            f"is left for the helper. {RESTART_WORDS}", dry=True, lines=["+ " + json.dumps(body)])
    if paths.restart_request.exists():
        return Result(True, "A restart request is already waiting; the helper restarts at the next quiet moment.")
    try:
        _atomic_write_new(paths.restart_request, json.dumps(body) + "\n")
    except OSError as exc:
        return Result(False, f"The restart request could not be written ({exc.strerror or exc}).")
    entry = {"id": f"{now:%Y%m%d-%H%M%S}-restart", "ts": now.timestamp(), "who": WHO, "kind": "restart_request",
             "what": "a restart was requested: " + reason, "ok": True}
    _append_history(paths, entry)
    return Result(True, "Restart requested. " + RESTART_WORDS, entry=entry)


# ---- the armed switch from the command line -----------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = EditPaths.default()
    if args[:1] == ["arm"]:
        set_armed(paths, True)
        print(f"armed switch ON ({paths.armed_file}). Changes are written only when the desk is also started with --armed.")
    elif args[:1] == ["disarm"]:
        set_armed(paths, False)
        print(f"armed switch OFF ({paths.armed_file}). Every change is a dry run.")
    elif args[:1] == ["status"]:
        data = _read_json(paths.armed_file)
        on = isinstance(data, dict) and data.get("armed") is True
        print(f"armed switch: {'ON' if on else 'OFF'} ({paths.armed_file}); the launcher flag --armed is also needed")
        for e in read_history(paths)[-8:]:
            print(f"  {_dt.datetime.fromtimestamp(e.get('ts', 0)):%Y-%m-%d %H:%M}  {e.get('what')}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
