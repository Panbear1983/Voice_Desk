"""Change an agent's voice from the dashboard, with a backup, a read-back and an undo.

The panel never invents a second place for a voice. A change does exactly what Peter would do by
hand, safely:

    1. rewrite ONE line of agents.toml (comments and every other line stay byte for byte),
    2. apply it through the existing voicecfg sync, but only the voice lines of that agent: backup first,
       atomic write, read back with Hermes' own parser, restored at once if it does not read back,
    3. write one line to the change history (what, when, from what to what).

Undo is the same change run backwards (from the history), so it gets the same backup and read-back
and never throws away what else changed in the meantime.

If anything goes wrong the agents.toml text is put back as it was and nothing is left half-done.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import agents
import settings as S
import voicecfg

FIELDS = ("voice", "voice_zh", "voices_extra", "voice_override")
_EDGE = re.compile(r"^([a-z]{2,3})-[A-Z]{2}-[A-Za-z]+Neural$")
_BARE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{1,63}$")
_LANG = re.compile(r"^[a-z]{2,3}$")
_KEY = re.compile(r"^([A-Za-z0-9_\-]+)\s*=")


class EditError(ValueError):
    """The change was refused before anything was written."""


# ---- validating what Peter types ----------------------------------------------------------------
def check_voice(field_name: str, value: Any, agent: agents.Agent) -> Any:
    """Return the cleaned value or raise EditError with a sentence Peter can read."""
    if field_name in ("voice", "voice_zh"):
        v = str(value or "").strip()
        m = _EDGE.match(v)
        if field_name == "voice_zh":
            if not (m and m.group(1) == "zh"):
                raise EditError(f"A Mandarin voice looks like zh-TW-HsiaoChenNeural (zh-… Neural); got {v!r}.")
        elif m:
            if m.group(1) != "en":
                raise EditError(f"The English voice must start with en- (for example en-US-AvaNeural); got {v!r}.")
        elif not (agent.command and _BARE.match(v)):
            raise EditError(f"{agent.display} needs an Edge voice like en-US-AvaNeural; got {v!r}.")
        return v
    if field_name == "voice_override":
        v = str(value or "").strip().lower()
        allowed = {"auto", "en", "zh", *agent.voices_extra}
        if v not in allowed:
            raise EditError(f"The language override must be one of {sorted(allowed)}; got {v!r}.")
        return v
    if field_name == "voices_extra":
        if not isinstance(value, dict):
            raise EditError("Extra languages must be a list like ja=ja-JP-NanamiNeural.")
        out = {}
        for code, voice in value.items():
            code, voice = str(code).strip().lower(), str(voice).strip()
            m = _EDGE.match(voice)
            if not _LANG.match(code) or code in ("en", "zh"):
                raise EditError(f"{code!r} is not a new language code (two or three letters, not en or zh).")
            if not (m and m.group(1) == code):
                raise EditError(f"The {code} voice must start with {code}- and end in Neural; got {voice!r}.")
            out[code] = voice
        return out
    raise EditError(f"{field_name} is not an editable voice field.")


def parse_extra(text: str) -> dict:
    """'ja=ja-JP-NanamiNeural, fr=fr-FR-DeniseNeural' -> {...}; empty text = no extra languages."""
    out = {}
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        if "=" not in part:
            raise EditError(f"Write extra languages as code=voice, for example ja=ja-JP-NanamiNeural (got {part!r}).")
        code, voice = part.split("=", 1)
        out[code.strip()] = voice.strip()
    return out


def editable(agent: agents.Agent) -> bool:
    """Only voices something already reads live can be changed from the panel: Hermes agents (their
    config.yaml and Voice Desk) and Claude (voice-defaults.conf)."""
    return bool(agent.profile) or agent.target == "claude"


# ---- editing agents.toml line by line -------------------------------------------------------------
def _toml_value(v: Any) -> str:
    if isinstance(v, dict):
        def key(k: str) -> str:
            return k if re.fullmatch(r"[A-Za-z0-9_\-]+", k) else json.dumps(k, ensure_ascii=False)
        return "{ " + ", ".join(f"{key(k)} = {_toml_value(x)}" for k, x in v.items()) + " }" if v else "{}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v), ensure_ascii=False)


def _strip_strings(line: str) -> str:
    """The line with strings and the trailing comment blanked, so brackets inside them do not count."""
    out, q, i = [], None, 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "#":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _block(lines: list[str], agent_key: str) -> tuple[int, int]:
    """(first line after the [agents.<key>] header, line of the next table header or end of file)."""
    head = re.compile(rf"^\[agents\.{re.escape(agent_key)}\]\s*(#.*)?$")
    for i, ln in enumerate(lines):
        if head.match(ln):
            for j in range(i + 1, len(lines)):
                if re.match(r"^\[", lines[j]):
                    return i + 1, j
            return i + 1, len(lines)
    raise EditError(f"agents.toml has no [agents.{agent_key}] block.")


def _statements(lines: list[str], start: int, end: int) -> dict[str, tuple[int, int]]:
    """key -> (first line, last line) of every `key = value` statement in a block; a value that
    runs over several lines (an array) is one statement."""
    found: dict[str, tuple[int, int]] = {}
    i = start
    while i < end:
        m = _KEY.match(lines[i])
        if not m:
            i += 1
            continue
        depth, j = 0, i
        while j < end:
            clean = _strip_strings(lines[j])
            depth += clean.count("[") + clean.count("{") - clean.count("]") - clean.count("}")
            if depth <= 0:
                break
            j += 1
        found[m.group(1)] = (i, min(j, end - 1))
        i = j + 1
    return found


def set_field(text: str, agent_key: str, key: str, value: Any) -> str:
    """The agents.toml text with `key` of [agents.<agent_key>] set to value (None removes it)."""
    lines = text.splitlines(keepends=True)
    start, end = _block(lines, agent_key)
    stmts = _statements(lines, start, end)
    if key in stmts:
        a, b = stmts[key]
        if value is None:
            del lines[a:b + 1]
        else:
            old = lines[a]
            m = re.match(r"^(\s*[A-Za-z0-9_\-]+\s*=\s*)", old)
            nl = "\n" if old.endswith("\n") or b < len(lines) - 1 else ""
            comment = voicecfg._split_comment(old[m.end():].rstrip("\r\n"))[1] if a == b else ""
            lines[a:b + 1] = [f"{m.group(1)}{_toml_value(value)}{comment}{nl}"]
    elif value is not None:
        last = max((b for _, b in stmts.values()), default=start - 1)
        if lines and not lines[last].endswith("\n"):
            lines[last] += "\n"
        lines.insert(last + 1, f"{key} = {_toml_value(value)}\n")
    out = "".join(lines)
    try:
        got = tomllib.loads(out)["agents"][agent_key].get(key)
    except (tomllib.TOMLDecodeError, KeyError) as exc:
        raise EditError(f"the edited agents.toml would not parse ({exc}); nothing written") from exc
    if got != value:
        raise EditError(f"the edit did not land (agents.toml would say {got!r}); nothing written")
    return out


# ---- the change history -------------------------------------------------------------------------
@dataclass
class EditPaths:
    toml: Path
    history: Path                     # voice_history.jsonl
    toml_backups: Path                # a copy of agents.toml before every change
    hermes_backups: Path              # parent of the per-change voicecfg backup folders
    profiles: Path
    claude_conf: Path

    @classmethod
    def default(cls) -> "EditPaths":
        return cls(toml=agents.PATH, history=S.STATE_DIR / "voice_history.jsonl",
                   toml_backups=S.STATE_DIR / "voice_history_backups", hermes_backups=voicecfg.BACKUPS,
                   profiles=voicecfg.PROFILES, claude_conf=voicecfg.CLAUDE_CONF)


def read_history(paths: EditPaths) -> list[dict]:
    try:
        lines = Path(paths.history).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _append_history(paths: EditPaths, entry: dict) -> None:
    Path(paths.history).parent.mkdir(parents=True, exist_ok=True)
    with open(paths.history, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _short(v: Any) -> str:
    if v is None or v == {}:
        return "not set"
    if isinstance(v, dict):
        return ", ".join(f"{k}={x}" for k, x in v.items())
    m = re.match(r"^[a-z]{2}-[A-Z]{2}-([A-Za-z]+?)(Multilingual)?Neural$", str(v))
    return m.group(1) if m else str(v)


def describe(entry: dict) -> str:
    when = _dt.datetime.fromtimestamp(entry["ts"]).strftime("%d %b %H:%M")
    if str(entry.get("field", "")).startswith("switch:"):                 # an on/off switch (onoff.py)
        verb = "undo: " if entry.get("kind") == "undo" else ""
        mark = "" if entry.get("ok", True) else "  (failed, nothing changed)"
        return (f"{when}  {verb}{entry['display']} {entry['what']}: "
                f"{entry['old_label']} → {entry['new_label']}{mark}")
    what = {"voice": "English voice", "voice_zh": "Mandarin voice", "voices_extra": "extra languages",
            "voice_override": "language override"}.get(entry["field"], entry["field"])
    verb = "undo: " if entry.get("kind") == "undo" else ""
    mark = "" if entry.get("ok", True) else "  (failed, nothing changed)"
    return f"{when}  {verb}{entry['display']} {what}: {_short(entry['old'])} → {_short(entry['new'])}{mark}"


def recent(paths: EditPaths, n: int = 6) -> list[str]:
    return [describe(e) for e in reversed(read_history(paths)[-n:])]


def last_undoable(paths: EditPaths) -> Optional[dict]:
    """The newest change that worked and has not been undone yet."""
    entries = read_history(paths)
    undone = {e.get("undoes") for e in entries if e.get("kind") == "undo" and e.get("ok", True)}
    for e in reversed(entries):
        if e.get("kind", "change") in ("change", "switch") and e.get("ok", True) and e.get("id") not in undone:
            return e
    return None


# ---- making a change ------------------------------------------------------------------------------
@dataclass
class Result:
    ok: bool
    message: str
    entry: Optional[dict] = None
    changed_files: list[str] = field(default_factory=list)


_WANTED_KEYS = {"voice": lambda i: i.key.endswith(".voice") or i.key == "NEURAL_VOICE_EN",
                "voice_zh": lambda i: i.key == "NEURAL_VOICE_ZH"}
_APPLIED_AS = re.compile(r"^(.*)  \(backup: (.*)\)$")


def _atomic_write(path: Path, text: str) -> None:
    real = Path(path).resolve()
    fd, tmp = tempfile.mkstemp(prefix=f".{real.name}.", suffix=".voicepanel", dir=real.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, (real.stat().st_mode & 0o777) if real.exists() else 0o644)      # a new file (first Telegram chat mode): 0644
        os.replace(tmp, real)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _find_agent(cfg: dict, key: str) -> agents.Agent:
    for a in agents.agents(cfg):
        if a.key == key:
            return a
    raise EditError(f"{key} is not in agents.toml.")


def change(agent_key: str, field_name: str, new: Any, paths: Optional[EditPaths] = None, *,
           kind: str = "change", undoes: Optional[str] = None, now: Optional[_dt.datetime] = None) -> Result:
    """Set one voice field for one agent. Returns a Result; never raises for a refused or failed edit."""
    paths = paths or EditPaths.default()
    now = now or _dt.datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    try:
        if field_name not in FIELDS:
            raise EditError(f"{field_name} is not an editable voice field.")
        old_text = Path(paths.toml).read_text(encoding="utf-8")
        cfg = tomllib.loads(old_text)
        agent = _find_agent(cfg, agent_key)
        if not editable(agent):
            raise EditError(f"{agent.display} has no voice setting the panel can change yet: it speaks with the shared "
                            "Voice Desk voice and its own hook (Gemini: Jenny / HsiaoChen, hard-coded). That comes later.")
        removing = new is None or (new == {} and field_name == "voices_extra")
        if removing and (kind == "undo" or field_name in ("voices_extra", "voice_override")):
            new = None                                  # taking a setting away again (undo, or clearing the extras)
        else:
            new = check_voice(field_name, new, agent)
        old = {"voice": agent.voice, "voice_zh": agent.voice_zh, "voices_extra": agent.voices_extra or None,
               "voice_override": agent.raw.get("voice_override")}[field_name]
        if old == new or (field_name == "voice_override" and old is None and new == "auto"):
            return Result(True, f"{agent.display}: already {_short(new)}, nothing to change")
        new_text = set_field(old_text, agent_key, field_name, new)
    except (EditError, OSError, tomllib.TOMLDecodeError) as exc:
        return Result(False, str(exc))

    entry = {"id": stamp + "-" + agent_key + "-" + field_name, "ts": now.timestamp(), "kind": kind, "agent": agent_key,
             "display": agent.display, "field": field_name, "old": old, "new": new, "ok": False}
    if undoes:
        entry["undoes"] = undoes
    Path(paths.toml_backups).mkdir(parents=True, exist_ok=True)
    toml_bak = Path(paths.toml_backups) / f"{stamp}-agents.toml"
    shutil.copy2(paths.toml, toml_bak)
    entry["toml_backup"] = str(toml_bak)
    try:
        if Path(paths.toml).read_text(encoding="utf-8") != old_text:
            return Result(False, "agents.toml changed while the edit was prepared; nothing written, try again")
        _atomic_write(paths.toml, new_text)
        new_cfg = agents.load(paths.toml)
        keep = _WANTED_KEYS.get(field_name)
        items = [i for i in voicecfg.collect(new_cfg, paths.profiles, paths.claude_conf)
                 if keep and i.where == agent_key and keep(i)]
        done, errors = voicecfg.apply(items, Path(paths.hermes_backups) / f"{stamp}-voice-panel")
    except Exception as exc:                                    # put agents.toml back, whatever happened
        _atomic_write(paths.toml, old_text)
        entry["error"] = str(exc)
        _append_history(paths, entry)
        return Result(False, f"{agent.display}: {exc}; agents.toml put back as it was", entry)
    if errors:
        _atomic_write(paths.toml, old_text)
        entry["error"] = "; ".join(errors)
        _append_history(paths, entry)
        return Result(False, f"{agent.display}: not applied ({errors[0]}); agents.toml put back as it was", entry)
    files = []
    for d in done:
        m = _APPLIED_AS.match(d)
        if m:
            files.append({"path": m.group(1).strip(), "backup": m.group(2)})
    entry.update(ok=True, files=files)
    _append_history(paths, entry)
    where = (f"{len(files)} Hermes/Claude file(s) updated and read back" if files
             else "saved in agents.toml (no Hermes or Claude file holds this setting)")
    tail = (" Voice Desk speaks it from the next line; Hermes agents use it from their next session."
            if field_name in ("voice", "voice_zh") else " Stored for later: nothing enforces it yet.")
    return Result(True, f"{agent.display} {field_name}: {_short(old)} → {_short(new)}; {where}.{tail}", entry,
                  [f["path"] for f in files])


def undo(paths: Optional[EditPaths] = None, grid_paths: Any = None) -> Result:
    """Run the last change backwards (same backup, same read-back). One key in the panel. A voice change and
    an on/off switch (onoff.py) share one history, so U undoes whichever was newest."""
    paths = paths or EditPaths.default()
    last = last_undoable(paths)
    if last is None:
        return Result(False, "nothing to undo")
    if last.get("kind") == "switch":
        import onoff                                            # imports this module, so only here
        return onoff.undo(last, paths, grid_paths)
    res = change(last["agent"], last["field"], last["old"], paths, kind="undo", undoes=last["id"])
    if res.ok and res.entry:
        res.message = "Undone: " + res.message
    return res
