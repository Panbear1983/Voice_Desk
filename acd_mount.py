"""Agent Control Desk: mount ONE open terminal session per service (Claude, Gemini, Codex) onto voice.

Peter 2026-11-18: "I actually just want the voice activation through agent, controlled dashboard to wield one
existing open session at a time, so that we don't confuse multiple different sessions."

The mount is the voice line's own pin, ~/.voice_desk/<target>_live_session: one session code. live_session.LiveBrain
reads it at every voice turn, types the words (prefixed "[voice] ") into that session's herdr window, and reads
the answer from that session's own transcript.

    mounted, window open        green   voice goes to that window, and only to it
    mounted, window not open    yellow  a voice turn says "the mounted Claude session is not open" and starts
                                        nothing else (no background thread, no other window)
    nothing mounted             grey    as before: the only open window of that service, else a separate
                                        background session

A window herdr shows NO session code for (the Codex window "Codex (Dolly)", 2026-11-18) counts as the mounted
session's window only by live_session.bind_codeless's rule: it is the ONE open window of that service, the mounted
session's own transcript exists and was written in the last day, and (when both say) in the window's folder. The
menu and the cell say which way it went and why. Never a guess between two windows.

Sessions in the desktop apps are out of scope: there is no window to type into, and two writers on one
conversation would clash.

Every change goes through the desk's armed gate (acd_edit.armed). A real change backs the old pin up byte for
byte, writes the new one atomically, reads it back (and puts the old one back at once if it does not read back),
and writes one history line; u undoes it, byte for byte. Nothing here types into a window: the desk only asks
herdr which windows are open (`herdr pane list`, read-only).
"""
from __future__ import annotations

import base64
import dataclasses
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import acd_edit as E

SERVICES = {"claude": "Claude", "gemini": "Gemini", "codex": "Codex"}
CODE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,127}$")
HERDR_TIMEOUT = 4                   # seconds: the desk never waits long on herdr
TYPE = "::type"                     # the menu's "type or paste a session code..." line
UNMOUNT = "::unmount"
NEEDS_MOUNT = ("No restart needed: the voice line reads the mounted session at every turn. Only that session's "
               "window gets the words; when it is not open, the voice says so and starts nothing else.")
APP_NOTE = ("Sessions in the desktop apps are not listed and cannot be mounted: there is no window to type into, "
            "and two writers on one conversation would clash.")


class HerdrError(RuntimeError):
    """herdr could not be asked which windows are open."""


@dataclass(frozen=True)
class Session:
    """One open terminal window of a service, as herdr reports it."""
    code: str                       # the session code ("" when herdr does not report one for this window)
    pane: str                       # herdr's window id, e.g. wV:p1
    status: str                     # herdr's word: idle, working, done ...
    folder: str                     # the working folder
    label: str                      # the window's name in herdr (or its title)
    last: float = 0.0               # when its transcript last changed (0 = no transcript yet)


@dataclass(frozen=True)
class MountView:
    """One service: what is mounted, and whether its window is open."""
    target: str
    name: str
    code: str = ""                  # the mounted session code, "" = nothing mounted
    window: Optional[Session] = None        # the mounted session's open window
    sessions: tuple = ()            # every open window of this service, newest activity first
    error: str = ""                 # herdr could not be asked: why
    bound: bool = False             # the window was found by live_session.bind_codeless (herdr shows it no code)
    why: str = ""                   # bind_codeless's words: why the one window with no code is used, or why not

    @property
    def state(self) -> str:
        """"open", "closed" (mounted, its window not found) or "none" (nothing mounted)."""
        return "none" if not self.code else "open" if self.window else "closed"


# ---- reading: the pin and herdr's windows --------------------------------------------------------------------
def pin_file(pins: Path, target: str) -> Path:
    return Path(pins) / f"{target}_live_session"


def _read_bytes(path: Path) -> Optional[bytes]:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def code_of(data: Optional[bytes]) -> str:
    return data.decode("utf-8", errors="replace").strip() if data else ""


def read_pin(pins: Path, target: str) -> str:
    try:
        return code_of(_read_bytes(pin_file(pins, target)))
    except OSError:
        return ""


def herdr_panes(herdr: str) -> list:
    """Every pane herdr knows (read-only). Raises HerdrError in plain words."""
    try:
        p = subprocess.run([str(herdr), "pane", "list"], capture_output=True, text=True, timeout=HERDR_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HerdrError(f"herdr could not be asked ({type(exc).__name__})") from exc
    if p.returncode:
        raise HerdrError("herdr could not be asked (" + ((p.stderr or p.stdout).strip()[-120:] or "no reason given") + ")")
    try:
        panes = json.loads(p.stdout)["result"]["panes"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HerdrError("herdr's answer could not be read") from exc
    return [x for x in panes if isinstance(x, dict)]


def _last(target: str, code: str) -> float:
    if not code:
        return 0.0
    import live_session
    try:
        path = live_session.AGENTS[target].locate(code)
        return path.stat().st_mtime if path and path.exists() else 0.0
    except OSError:
        return 0.0


def known(target: str, code: str) -> bool:
    """Does this Mac hold a conversation with this code for this service (its transcript file)?"""
    return _last(target, code) > 0


def sessions_in(panes: list, target: str) -> tuple:
    """The open windows of one service in herdr's pane list, newest activity first."""
    import live_session
    agent = live_session.AGENTS[target].herdr_agent
    out = []
    for p in panes:
        if p.get("agent") != agent:
            continue
        code = str((p.get("agent_session") or {}).get("value") or "")
        out.append(Session(code, str(p.get("pane_id") or ""), str(p.get("agent_status") or ""),
                           str(p.get("foreground_cwd") or p.get("cwd") or ""),
                           str(p.get("label") or p.get("terminal_title_stripped") or ""), _last(target, code)))
    out.sort(key=lambda s: (-s.last, s.pane))
    return tuple(out)


def would_bind(target: str, code: str, sessions: tuple) -> tuple[Optional[Session], str]:
    """For a code with no window of its own: the one window herdr shows no code that voice would use for it
    (live_session.bind_codeless), and the words why, or (None, why not / "")."""
    if not code or not sessions or any(s.code == code for s in sessions):
        return None, ""
    import live_session
    yes, why = live_session.bind_codeless(target, code, [(s.pane, s.status, s.code, s.folder) for s in sessions])
    # the window's last activity is the mounted session's own record (herdr gives this window no code to look up)
    return (dataclasses.replace(sessions[0], last=_last(target, code)) if yes else None), why


def _view(pins: Path, target: str, panes: Optional[list], error: str) -> MountView:
    code = read_pin(pins, target)
    found = sessions_in(panes, target) if panes is not None else ()
    window = next((s for s in found if code and s.code == code), None)
    bound, why = False, ""
    if window is None:
        window, why = would_bind(target, code, found)
        bound = window is not None
    return MountView(target, SERVICES[target], code, window, found, error, bound, why)


def view(pins: Path, herdr: str, target: str) -> MountView:
    """One service, read fresh (one herdr call)."""
    try:
        return _view(pins, target, herdr_panes(herdr), "")
    except HerdrError as exc:
        return _view(pins, target, None, str(exc))


def views(pins: Path, herdr: str) -> dict:
    """target -> MountView for all three services, from ONE herdr call (the desk's 45 second read)."""
    try:
        panes, error = herdr_panes(herdr), ""
    except HerdrError as exc:
        panes, error = None, str(exc)
    return {t: _view(pins, t, panes, error) for t in SERVICES}


# ---- words -----------------------------------------------------------------------------------------------------
def short(code: str) -> str:
    return code[:8] if code else ""


def ago(ts: float, now: Optional[float] = None) -> str:
    if not ts:
        return "no activity yet"
    s = max(0, (time.time() if now is None else now) - ts)
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 2 * 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} days ago"


def folder_words(folder: str) -> str:
    home = str(Path.home())
    if folder == home:
        return "~"
    return "~/" + folder[len(home) + 1:] if folder.startswith(home + "/") else folder


def session_label(s: Session, now: Optional[float] = None) -> str:
    """One line of the picker: code, window name, folder, last activity."""
    name = s.label[:22] if s.label else s.pane
    folder = folder_words(s.folder)
    folder = folder if len(folder) <= 18 else "…" + folder[-17:]
    return f"{short(s.code)}  {name}  {folder}  {ago(s.last, now)}"


def no_code_words(s: Session, name: str, m: Optional["MountView"] = None) -> str:
    """A window of the service that herdr reports WITHOUT a session code (Kylie 2026-11-18: the Codex window "Codex
    (Dolly)"). Voice uses it only for a mounted code, and only by live_session.bind_codeless's rule: the ONE window
    of the service, the mounted session's own record written lately (and in the window's folder)."""
    which = f"\"{s.label[:28]}\" ({s.pane})" if s.label else s.pane
    head = f"{which}: herdr shows this {name} window no session code"
    if m is None:
        return head + ", so voice cannot mount it by itself"
    if m.bound and m.window is not None and m.window.pane == s.pane:
        return (f"{which}: no session code from herdr; voice uses it for the mounted session {short(m.code)}, "
                f"the only {name} window open  {folder_words(m.window.folder)}  {ago(m.window.last)}")
    if m.code and m.why:
        return f"{head}; not used for the mounted session {short(m.code)}: {m.why}"
    if len(m.sessions) > 1:
        return f"{head}, and it cannot be told apart from the other {name} windows, so voice never uses it"
    return (f"{head}. Mount its session code (type or paste it below): while it is the only {name} window open "
            "and that session wrote its record lately, voice uses this window")


def where_words(s: Session) -> str:
    out = f"window {s.pane}" + (f" \"{s.label}\"" if s.label else "")
    if s.folder:
        out += f", folder {folder_words(s.folder)}"
    return out + (f", {s.status}" if s.status and s.status != "unknown" else "")


def _words(data: Optional[bytes]) -> str:
    return f"mounted: {code_of(data)}" if data is not None and code_of(data) else "nothing mounted"


# ---- writing: mount, unmount, undo -------------------------------------------------------------------------------
def _ready(paths: E.EditPaths, target: str) -> Path:
    if target not in SERVICES:
        raise E.EditError(f"{target!r} is not Claude, Gemini or Codex; nothing changed")
    if getattr(paths, "pins", None) is None:
        raise E.EditError("the desk does not know where the voice line keeps its mounted sessions; nothing changed")
    return pin_file(paths.pins, target)


def _put(path: Path, data: bytes, mode: int) -> None:
    """Atomic replace with exactly these bytes and this file mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".acd", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _b64(data: Optional[bytes]) -> Optional[str]:
    return None if data is None else base64.b64encode(data).decode("ascii")


def _unb64(text: Optional[str]) -> Optional[bytes]:
    return None if text is None else base64.b64decode(text)


def _write(paths: E.EditPaths, target: str, before: Optional[bytes], after: Optional[bytes], what: str,
           now: _dt.datetime, did: str, undoes: Optional[str] = None, mode: Optional[int] = None) -> E.Result:
    """The one safe path: armed gate, backup, atomic write, read back (put back on any doubt), history."""
    pin = _ready(paths, target)
    lines = [f"- {_words(before)}", f"+ {_words(after)}"]
    if not E.armed(paths):
        return E.Result(True, f"DRY RUN, nothing written ({E.why_not_armed(paths)}). This would happen: {what}",
                        dry=True, lines=lines, needs=NEEDS_MOUNT)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    bak: Optional[Path] = None
    try:
        if _read_bytes(pin) != before:
            raise E.EditError("the mounted session changed while the change was prepared; nothing written, try again")
        if before is not None:
            folder = paths.state / "pin-backups"
            folder.mkdir(parents=True, exist_ok=True)
            bak = folder / f"{stamp}-{pin.name}"
            n = 1
            while bak.exists():
                n += 1
                bak = folder / f"{stamp}-{n}-{pin.name}"
            shutil.copy2(pin, bak)
            if bak.read_bytes() != before:
                raise E.EditError("the backup of the old mounted session did not read back; nothing written")
            if mode is None:
                mode = pin.stat().st_mode & 0o777
        try:
            if after is None:
                pin.unlink()
            else:
                _put(pin, after, mode if mode is not None else 0o644)
            got = _read_bytes(pin)
        except OSError as exc:
            got = exc
        if got != after:
            if before is None:
                pin.unlink(missing_ok=True)
            else:
                _put(pin, before, mode if mode is not None else 0o644)
            raise E.EditError("the change did not read back right, so the old mounted session was put back exactly "
                              "as it was")
    except (E.EditError, OSError) as exc:
        return E.Result(False, f"Not changed. {exc}", lines=lines)
    entry = {"id": f"{stamp}-{target}-mount", "ts": now.timestamp(), "who": E.WHO, "kind": "mount",
             "target": target, "did": did, "what": what, "ok": True, "pin": str(pin),
             "backup": str(bak) if bak else None, "before_b64": _b64(before), "after_b64": _b64(after)}
    if undoes:
        entry["undoes"] = undoes
        entry["id"] += "-undo"
    taken = {e.get("id") for e in E.read_history(paths)}
    base, n = entry["id"], 1
    while entry["id"] in taken:                  # two changes in the same second keep their own ids
        n += 1
        entry["id"] = f"{base}-{n}"
    E._append_history(paths, entry)
    return E.Result(True, ("Undone: " if undoes else "Saved: ") + what, lines=lines, needs=NEEDS_MOUNT, entry=entry)


def mount(paths: E.EditPaths, target: str, code: str, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Mount one session code for a service. The window does not have to be open: a code that is not open is
    mounted all the same (yellow light; voice says the mounted session is not open)."""
    now = now or _dt.datetime.now()
    code = (code or "").strip()
    try:
        pin = _ready(paths, target)
        name = SERVICES[target]
        if not CODE_OK.match(code):
            raise E.EditError(f"{code[:60]!r} does not look like a session code (letters, digits, dots, dashes, "
                              "4 to 128 of them); nothing changed")
        before = _read_bytes(pin)
    except (E.EditError, OSError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    if code_of(before) == code:
        return E.Result(True, f"Voice {name} is already mounted on session {code}; nothing to change")
    return _write(paths, target, before, (code + "\n").encode("utf-8"),
                  f"voice {name} is mounted on session {code}", now, "mount")


def unmount(paths: E.EditPaths, target: str, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Take the mount away: with nothing mounted, voice uses the only open window, else a background session."""
    now = now or _dt.datetime.now()
    try:
        pin = _ready(paths, target)
        name = SERVICES[target]
        before = _read_bytes(pin)
    except (E.EditError, OSError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    if before is None:
        return E.Result(True, f"Nothing is mounted for {name}; nothing to change")
    return _write(paths, target, before, None, f"voice {name} is unmounted (it was on session {code_of(before)}); "
                  "with nothing mounted it uses its only open window, else a separate background session", now,
                  "unmount")


def undo_mount(last: dict, paths: E.EditPaths, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Put the pin back exactly as it was before the newest mount change: the same bytes, or no pin at all.
    Refused when the pin was changed since (the desk never writes over a change it did not make)."""
    now = now or _dt.datetime.now()
    try:
        target = str(last.get("target"))
        pin = _ready(paths, target)
        name = SERVICES[target]
        old = _unb64(last.get("before_b64"))
        made = _unb64(last.get("after_b64"))
        now_there = _read_bytes(pin)
    except (E.EditError, OSError, ValueError) as exc:
        return E.Result(False, f"Not undone. {exc}")
    if now_there != made:
        return E.Result(False, f"Not undone. {name}'s mounted session was changed since ({_words(now_there)}), so "
                               "the desk will not put the old one over it.")
    mode = None
    bak = Path(str(last.get("backup") or ""))
    if old is not None and last.get("backup") and bak.is_file():
        try:
            if bak.read_bytes() == old:
                mode = bak.stat().st_mode & 0o777
        except OSError:
            pass
    what = f"{name}'s mounted session goes back to {_words(old)} (undo of: {last.get('what')})"
    return _write(paths, target, now_there, old, what, now, "undo", undoes=str(last.get("id")), mode=mode)
