"""Grid edits that change WHICH agents and places are on the grid (Parts 3 and 4 of the 2026-11-13 iteration).

    A   add an agent      a new [agents.<key>] block: a Hermes profile found on disk, or another CLI agent
    D   remove an agent   its [agents.<key>] block only (with the comment lines above it). The Hermes profile and
                          every other file stay exactly as they are; U puts the block back, byte for byte
    X   here / not here   `here = [...]` in the agent's block: the places the agent appears at. Separate from the
                          voice on/off switch (Enter). Not set = the places that are wired today
    P   activation phrase `phrases = { place = "..." }`: what wakes the agent at that place (Siri shortcut name,
                          "hello Jarvis", the bot or group). Stored and shown; it renames nothing

Same rules as every panel edit (voiceedit.py): only agents.toml is written, a copy is kept before the change,
the result is read back with Python's TOML parser, the change goes into voice_history.jsonl, and U undoes it.
An undo puts back the agent's whole block exactly as it was; it refuses (and says where the backup is) if the
block was changed by hand since.

Peter 2026-11-11: Telegram chat ids are NOT how an agent is added: a Hermes profile already knows its own bot.
"""
from __future__ import annotations

import datetime as _dt
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any, Optional

import agents
import gridstate as G
import voiceedit as VE

_HEADER = re.compile(r"^\[")
_AGENT_HEADER = re.compile(r"^\[agents\.([A-Za-z0-9_\-]+)\]\s*(#.*)?$")
_KEY_OK = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


# ---- whole blocks ---------------------------------------------------------------------------------
def _header_index(lines: list[str], key: str) -> Optional[int]:
    for i, ln in enumerate(lines):
        m = _AGENT_HEADER.match(ln)
        if m and m.group(1) == key:
            return i
    return None


def _span(lines: list[str], key: str) -> Optional[tuple[int, int]]:
    """[start, end) of an agent's block as Peter sees it: the comment lines right above its header (Simba's), the
    header, its statements, and the blank lines after it; NOT the next block's own comment lines. The last block
    in the file takes the blank lines before it instead, so removing it leaves no stray blank line."""
    h = _header_index(lines, key)
    if h is None:
        return None
    s = h
    while s > 0 and lines[s - 1].lstrip().startswith("#"):
        s -= 1
    n = next((j for j in range(h + 1, len(lines)) if _HEADER.match(lines[j])), len(lines))
    e = n
    if n < len(lines):
        while e - 1 > h and lines[e - 1].lstrip().startswith("#"):
            e -= 1
    else:
        while s > 0 and not lines[s - 1].strip():
            s -= 1
    return s, e


def block_text(text: str, key: str) -> Optional[str]:
    lines = text.splitlines(keepends=True)
    sp = _span(lines, key)
    return "".join(lines[sp[0]:sp[1]]) if sp else None


def _next_agent(text: str, key: str) -> Optional[str]:
    """The agent whose block follows this one (where an undone removal goes back)."""
    lines = text.splitlines(keepends=True)
    h = _header_index(lines, key)
    for ln in lines[(h or 0) + 1:]:
        m = _AGENT_HEADER.match(ln)
        if m:
            return m.group(1)
    return None


def put_block(text: str, key: str, block: Optional[str], anchor: Optional[str] = None) -> str:
    """The text with agent `key`'s block replaced by `block` (None removes it). A block that is not there yet goes
    in front of agent `anchor`'s block, or at the end of the file."""
    lines = text.splitlines(keepends=True)
    sp = _span(lines, key)
    if sp:
        return "".join(lines[:sp[0]]) + (block or "") + "".join(lines[sp[1]:])
    if block is None:
        return text
    at = _span(lines, anchor) if anchor else None
    if at:
        return "".join(lines[:at[0]]) + block + "".join(lines[at[0]:])
    if text and not text.endswith("\n"):
        text += "\n"
    return text + block


def new_block(key: str, display: str, names: list[str], voice: Optional[str], *, profile: Optional[str] = None,
              target: Optional[str] = None, when: Optional[_dt.date] = None) -> str:
    """The block A writes, at the end of the file (a blank line in front of it, as between the others)."""
    when = when or _dt.date.today()
    lines = ["", f"# Added on the dashboard grid (A key) {when:%Y-%m-%d}. No voice line = silent.",
             f"[agents.{key}]"]
    lines.append(f"profile = {VE._toml_value(profile)}" if profile else f"target = {VE._toml_value(target or key)}")
    lines.append(f"display = {VE._toml_value(display)}")
    lines.append("names = [" + ", ".join(VE._toml_value(n) for n in names) + "]")
    if voice:
        lines.append(f"voice = {VE._toml_value(voice)}")
    return "\n".join(lines) + "\n"


# ---- what can be added ----------------------------------------------------------------------------
def addable_profiles(cfg: dict, profiles_dir: Path) -> list[str]:
    """Hermes profiles on this Mac (a folder with a config.yaml) that no [agents.*] block stands for yet. Simba's
    block has no profile line but is still her: a profile whose name is a block's key or target counts as on."""
    taken: set[str] = set()
    for a in agents.agents(cfg):
        taken.update(x for x in (a.profile, a.key, a.target) if x)
    try:
        found = sorted(p.name for p in Path(profiles_dir).iterdir() if p.is_dir() and (p / "config.yaml").exists())
    except OSError:
        return []
    return [p for p in found if p not in taken and p.replace("-", "_") not in taken]


def key_for(name: str) -> str:
    """agents.toml key from a profile or display name: lowercase, _ for anything else (wanna-buffet ->
    wanna_buffet)."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")[:40]


def parse_names(text: str, display: str) -> list[str]:
    """Spoken names, comma separated; the display name itself is always one of them."""
    out: list[str] = []
    for n in [display, *(text or "").split(",")]:
        n = re.sub(r"\s+", " ", n.strip().lower())
        if n and n not in out:
            out.append(n)
    return out


def draft(key: str, display: str, *, profile: Optional[str] = None, target: Optional[str] = None,
          names: tuple[str, ...] = ()) -> agents.Agent:
    """The agent as it will be, for the voice pick-list and the voice check before anything is written."""
    return agents.Agent(key=key, target=f"hermes:{profile}" if profile else (target or key), display=display,
                        names=names)


# ---- the edits ------------------------------------------------------------------------------------
def _now(now: Optional[_dt.datetime]) -> _dt.datetime:
    return now or _dt.datetime.now()


def _write(paths: VE.EditPaths, agent_key: str, display: str, field_name: str, what: str, old_label: str,
           new_label: str, old_text: str, new_text: str, check, *, kind: str = "grid", undoes: Optional[str] = None,
           now: Optional[_dt.datetime] = None, extra: Optional[dict] = None) -> VE.Result:
    """Backup, write, read back (check(cfg) -> error or ""), history line. agents.toml is put back on any failure."""
    now = _now(now)
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")          # to the microsecond: two edits in one second stay two
    try:
        cfg = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        return VE.Result(False, f"the edited agents.toml would not parse ({exc}); nothing written")
    bad = check(cfg)
    if bad:
        return VE.Result(False, f"{bad}; nothing written")
    entry = {"id": f"{stamp}-{agent_key}-{field_name}", "ts": now.timestamp(), "kind": kind, "agent": agent_key,
             "display": display, "field": field_name, "what": what, "old_label": old_label, "new_label": new_label,
             "block_before": block_text(old_text, agent_key), "block_after": block_text(new_text, agent_key),
             "anchor": _next_agent(old_text, agent_key) if block_text(old_text, agent_key) else None, "ok": False,
             **(extra or {})}
    if undoes:
        entry["undoes"] = undoes
    Path(paths.toml_backups).mkdir(parents=True, exist_ok=True)
    toml_bak = Path(paths.toml_backups) / f"{stamp}-agents.toml"
    shutil.copy2(paths.toml, toml_bak)
    entry["toml_backup"] = str(toml_bak)
    try:
        if Path(paths.toml).read_text(encoding="utf-8") != old_text:
            return VE.Result(False, "agents.toml changed while the edit was prepared; nothing written, try again")
        VE._atomic_write(paths.toml, new_text)
        bad = check(agents.load(paths.toml))                       # read back from the disk
        if bad:
            raise VE.EditError(f"read back wrong: {bad}")
    except Exception as exc:                                        # put agents.toml back, whatever happened
        VE._atomic_write(paths.toml, old_text)
        entry["error"] = str(exc)
        VE._append_history(paths, entry)
        return VE.Result(False, f"{display}: {exc}; agents.toml put back as it was", entry)
    entry["ok"] = True
    VE._append_history(paths, entry)
    return VE.Result(True, "", entry, [str(paths.toml)])


def add_agent(paths: VE.EditPaths, display: str, names_text: str, voice: Optional[str], *,
              profile: Optional[str] = None, now: Optional[_dt.datetime] = None) -> VE.Result:
    """A: a new [agents.*] block for a Hermes profile (profile=...) or for another CLI agent (profile=None)."""
    paths = paths or VE.EditPaths.default()
    display = (display or "").strip()
    if not display:
        return VE.Result(False, "the new agent needs a display name; nothing written")
    try:
        old_text = Path(paths.toml).read_text(encoding="utf-8")
        cfg = tomllib.loads(old_text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return VE.Result(False, str(exc))
    key = key_for(profile or display)
    if not _KEY_OK.match(key):
        return VE.Result(False, f"{display!r} gives no usable settings name (letters and digits, please); nothing written")
    if key in cfg.get("agents", {}):
        return VE.Result(False, f"{key} is already on the grid (agents.toml has [agents.{key}]); nothing written")
    if profile and profile not in addable_profiles(cfg, paths.profiles):
        return VE.Result(False, f"the Hermes profile {profile} is not on this Mac or is already on the grid; nothing written")
    if not profile and key in (G.CLAUDE, G.GEMINI, G.CODEX):
        return VE.Result(False, f"{display} is one of the built-in agents; nothing written")
    names = parse_names(names_text, display)
    target = None if profile else key
    a = draft(key, display, profile=profile, target=target, names=tuple(names))
    try:
        voice = VE.check_voice("voice", voice, a) if voice else None
    except VE.EditError as exc:
        return VE.Result(False, f"{exc} Nothing written.")
    new_text = put_block(old_text, key, new_block(key, display, names, voice, profile=profile, target=target,
                                                  when=_now(now).date()))

    def check(c: dict) -> str:
        raw = c.get("agents", {}).get(key)
        if not raw:
            return f"[agents.{key}] is not in the file"
        got = agents._agent(key, raw)
        if (got.display, got.profile, got.voice, list(got.names)) != (display, profile, voice, names):
            return f"[agents.{key}] reads back as {got.display!r} / {got.profile!r} / {got.voice!r}"
        return ""

    res = _write(paths, key, display, "grid:agent", "on the grid", "no", "added", old_text, new_text, check, now=now)
    if res.ok:
        sound = f"speaks as {voice}" if voice else "silent (no voice)"
        how = (f"Hermes profile {profile}: Voice Desk answers to \"hello {display}\" after its next restart; the "
               f"profile itself was not changed (the watcher may write this voice into it on its next round)"
               if profile else "another CLI agent: Voice Desk shows its voice here but cannot talk to it yet")
        res.message = f"{display} added to the grid ({sound}); {how}. U takes it off again."
    return res


def remove_agent(paths: VE.EditPaths, agent_key: str, now: Optional[_dt.datetime] = None) -> VE.Result:
    """D: take an agent off the grid by deleting its [agents.*] block (and the comment lines above it). Nothing
    else is touched: not the Hermes profile, not its voice files, not its history."""
    paths = paths or VE.EditPaths.default()
    try:
        old_text = Path(paths.toml).read_text(encoding="utf-8")
        a = VE._find_agent(tomllib.loads(old_text), agent_key)
    except (OSError, tomllib.TOMLDecodeError, VE.EditError) as exc:
        return VE.Result(False, str(exc))
    new_text = put_block(old_text, agent_key, None)

    def check(c: dict) -> str:
        return f"[agents.{agent_key}] is still there" if agent_key in c.get("agents", {}) else ""

    res = _write(paths, agent_key, a.display, "grid:agent", "on the grid", "yes", "removed", old_text, new_text,
                 check, now=now)
    if res.ok and res.entry:
        res.message = (f"{a.display} taken off the grid: only its block in agents.toml was removed (a copy is in "
                       f"{Path(res.entry['toml_backup']).name}); its "
                       + (f"Hermes profile {a.profile} and " if a.profile else "") +
                       "files are untouched. Voice Desk stops answering to its name after its next restart. U puts it back.")
    return res


def here_map(a: agents.Agent, cells: dict[str, G.Cell]) -> dict[str, bool]:
    return G.here_map(a, cells)


def default_phrase(a: agents.Agent, place: str) -> str:
    return G.default_phrase(a, place)


def phrase_map(a: agents.Agent) -> dict[str, str]:
    return G.phrase_map(a)


def set_here(paths: VE.EditPaths, agent_key: str, place: str, here: bool, cells: dict[str, G.Cell],
             now: Optional[_dt.datetime] = None) -> VE.Result:
    """X: put the agent at a place or take it away (the voice on/off switch there is not changed)."""
    paths = paths or VE.EditPaths.default()
    if place not in G.PLACE_KEYS:
        return VE.Result(False, f"{place} is not a place on the grid")
    try:
        old_text = Path(paths.toml).read_text(encoding="utf-8")
        a = VE._find_agent(tomllib.loads(old_text), agent_key)
    except (OSError, tomllib.TOMLDecodeError, VE.EditError) as exc:
        return VE.Result(False, str(exc))
    now_map = here_map(a, cells)
    if now_map[place] == here:
        return VE.Result(True, f"{a.display} is already {'here' if here else 'not here'} at {G.PLACE_NAME[place]}")
    now_map[place] = here
    want = [p for p in G.PLACE_KEYS if now_map[p]]
    try:
        new_text = VE.set_field(old_text, agent_key, "here", want)
    except VE.EditError as exc:
        return VE.Result(False, str(exc))

    def check(c: dict) -> str:
        got = c.get("agents", {}).get(agent_key, {}).get("here")
        return "" if got == want else f"here reads back as {got!r}"

    name = G.PLACE_NAME[place]
    res = _write(paths, agent_key, a.display, "grid:here", f"at {name}", "not here" if here else "here",
                 "here" if here else "not here", old_text, new_text, check, now=now)
    if res.ok:
        wired = not G.unwired(cells[place], place)
        if not here:
            tail = "; the voice switch there is unchanged (Enter changes it)"
        elif wired:
            tail = ""
        else:
            tail = f", but it is not wired there yet, so the cell shows as unfinished: {wiring_hint(a, place)}"
        res.message = f"{a.display} is {'here' if here else 'not here'} at {name}{tail}. U undoes."
    return res


def wiring_hint(a: agents.Agent, place: str) -> str:
    return G.wiring_hint(a, place)


def set_phrase(paths: VE.EditPaths, agent_key: str, place: str, phrase: Optional[str],
               now: Optional[_dt.datetime] = None) -> VE.Result:
    """P: the activation phrase of one cell (empty = back to the default). Stored and shown only."""
    paths = paths or VE.EditPaths.default()
    phrase = re.sub(r"\s+", " ", (phrase or "").strip()) or None
    if place not in G.PLACE_KEYS:
        return VE.Result(False, f"{place} is not a place on the grid")
    if phrase and len(phrase) > 80:
        return VE.Result(False, "an activation phrase of at most 80 characters, please; nothing written")
    try:
        old_text = Path(paths.toml).read_text(encoding="utf-8")
        a = VE._find_agent(tomllib.loads(old_text), agent_key)
    except (OSError, tomllib.TOMLDecodeError, VE.EditError) as exc:
        return VE.Result(False, str(exc))
    have = phrase_map(a)
    if have.get(place) == phrase:
        return VE.Result(True, f"{a.display}: already {phrase or 'the default'}, nothing to change")
    want = dict(have)
    if phrase:
        want[place] = phrase
    else:
        want.pop(place, None)
    try:
        new_text = VE.set_field(old_text, agent_key, "phrases", want or None)
    except VE.EditError as exc:
        return VE.Result(False, str(exc))

    def check(c: dict) -> str:
        got = c.get("agents", {}).get(agent_key, {}).get("phrases")
        return "" if (got or {}) == want else f"phrases reads back as {got!r}"

    name = G.PLACE_NAME[place]
    old_label = have.get(place) or f"default ({default_phrase(a, place) or 'none'})"
    new_label = phrase or f"default ({default_phrase(a, place) or 'none'})"
    res = _write(paths, agent_key, a.display, "grid:phrase", f"phrase at {name}", f"“{old_label}”",
                 f"“{new_label}”", old_text, new_text, check, now=now)
    if res.ok:
        res.message = (f"{a.display} at {name}: activation phrase “{new_label}” saved. Stored and shown only: it does "
                       "not rename a Siri shortcut or change the wake words yet. U undoes.")
    return res


# ---- undo -----------------------------------------------------------------------------------------
def undo(last: dict, paths: VE.EditPaths) -> VE.Result:
    """Put the agent's block back exactly as it was before `last` (add: remove it; remove: put it back where it
    was). Refused when the block was changed by hand since: the backup file is named instead."""
    try:
        text = Path(paths.toml).read_text(encoding="utf-8")
    except OSError as exc:
        return VE.Result(False, str(exc))
    key, before, after = last["agent"], last.get("block_before"), last.get("block_after")
    if block_text(text, key) != after:
        return VE.Result(False, f"{last['display']}'s block in agents.toml was changed since; not undone. The copy "
                                f"from before the change is {last.get('toml_backup')}")
    new_text = put_block(text, key, before, last.get("anchor"))

    def check(c: dict) -> str:
        there = key in c.get("agents", {})
        if before is None:
            return f"[agents.{key}] is still there" if there else ""
        if not there:
            return f"[agents.{key}] did not come back"
        return "" if block_text(new_text, key) == before else "the block did not come back as it was"

    res = _write(paths, key, last["display"], last["field"], last.get("what", ""), last.get("new_label", ""),
                 last.get("old_label", ""), text, new_text, check, kind="undo", undoes=last["id"])
    if res.ok:
        res.message = f"Undone: {last['display']} {last.get('what', '')}: {last.get('new_label')} → {last.get('old_label')}"
    return res
