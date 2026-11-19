"""Agent Control Desk: Telegram group rows, added by hand (2026-11-19, card 2).

Peter: "Manually adding additional row of telegram group ... click individual cell ... type in the telegram ID ...
press enter ... sends the scan to the telegram and check if those particular agents are actually being added to that
group." One agent in three groups = three group rows for everyone; the others are blank in the extra rows.

    g                   a new group row: its name and its ID are typed once (the desk also offers group IDs its
                        logs have seen that are not on the desk yet: a suggestion only, never added by itself).
                        On Enter the desk asks Telegram, with each agent's OWN bot, whether that bot is in the
                        group, then asks who goes in the row (the agents found in it, or every agent).
    a cell              green   its bot is a member and the Hermes gateway has it connected (it answers there)
                        red     not a member, or not confirmed: the reason, which bot to add in Telegram, then c
                        blank   marked "not in this group" (a mark on the desk only)
                        and that agent's voice mode in the group (Off / Voice-back / Always): the Hermes gateway's
                        own per-chat mode, the same file and the same switch as the Telegram private chat row.
                        The light is only about membership; the mode is separate.
    the row's name      check every agent again; mark it as the Team Room's group (the old "Speaks in Telegram
                        group" row moves into it: its switch and every setting stay exactly where they are); take
                        the row off the desk (no bot ever leaves a Telegram group).

What it asks Telegram: getChatMember only (getChat is the one other method the asker allows), with each agent's
own bot token, read from that agent's .env at the moment of the call and handed to Telegram and to nothing else: it
is never kept, shown, written or logged, and every error text is scrubbed of it. getUpdates is refused outright (it
would steal the gateway's messages). A bot cannot join a group or add another bot; only a person adds a bot.

The rows live only in the desk's own settings file on this Mac (~/.voice_desk/acd/tg_groups.json): the repository
ships made-up examples only (docs/tg_groups.example.json). Every change goes through one safe path: dry run unless
armed, backup, atomic write, read back (put back on any doubt), one history line, and u undoes it.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import acd_edit as E
import acd_setup

GROUPS_FILE = "tg_groups.json"
ID_OK = re.compile(r"^-\d{5,20}$")                     # a group's chat ID: a minus sign, then digits
NAME_MAX = 40
TOKEN_NAME = acd_setup.TOKEN_NAME
API = "https://api.telegram.org"
ALLOWED = ("getChatMember", "getChat")                 # never getUpdates: it would steal the gateway's messages
TIMEOUT = 8                                            # seconds per question to Telegram
NEEDS = ("No restart needed: the desk keeps these rows in its own settings file. Telegram itself is not changed; a "
         "bot is put into a group only by a person, in Telegram.")
NEEDS_MODE = ("No restart needed when the agents' system has the live-reload fix (the result says so): the gateway "
              "reads its voice-mode file again when it changes.")
ID_HINT = ("The group's ID: a number that starts with a minus sign, for example -1001234567890 (a person can see it "
           "with a bot like @getidsbot in that group, or it is in the desk's suggestions).")

MEMBER, NOT_MEMBER, ERROR, NO_TOKEN, NO_TELEGRAM = "member", "not_member", "error", "no_token", "no_telegram"


class TgUnreachable(Exception):
    """Telegram could not be asked at all (no network, a timeout). The text never holds a token."""


# ---- the rows ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Group:
    id: str
    name: str
    team: bool = False                          # the Team Room's own group (the old single row moved into it)
    marks: dict = field(default_factory=dict)   # profile -> True (in this group) / False (blank: not in it)

    def marked(self, profile: str) -> bool:
        return bool(self.marks.get(profile, False))


def groups_file(paths: E.EditPaths) -> Path:
    return Path(paths.state) / GROUPS_FILE


def parse(data: Any) -> list[Group]:
    """The rows in a file's JSON, in their order. Anything that is not a row is skipped."""
    rows = data.get("groups") if isinstance(data, dict) else None
    out: list[Group] = []
    for g in rows if isinstance(rows, list) else ():
        if not isinstance(g, dict) or not ID_OK.match(str(g.get("id") or "")):
            continue
        marks = g.get("in") if isinstance(g.get("in"), dict) else {}
        out.append(Group(str(g["id"]), str(g.get("name") or g["id"]), bool(g.get("team")),
                         {str(k): bool(v) for k, v in marks.items()}))
    return out


def read_groups(path: Optional[Path]) -> tuple[list[Group], str]:
    """(the rows, why they could not be read). No file = no rows, nothing wrong."""
    if path is None:
        return [], ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], ""
    except OSError as exc:
        return [], f"the desk's Telegram group list could not be read ({exc.strerror or exc})"
    try:
        data = json.loads(text)
    except ValueError:
        return [], "the desk's Telegram group list is not readable JSON"
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return [], "the desk's Telegram group list has no list of groups in it"
    return parse(data), ""


def dump(groups: list[Group]) -> bytes:
    rows = [{"id": g.id, "name": g.name, "team": g.team, "in": dict(sorted(g.marks.items()))} for g in groups]
    return (json.dumps({"version": 1, "groups": rows}, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def check_id(text: str) -> tuple[str, str]:
    """(the clean ID, why it is not one). Spaces are dropped; a plain positive number is a person, not a group."""
    gid = "".join((text or "").split())
    if not gid:
        return "", "No ID was typed."
    if re.fullmatch(r"\d{5,20}", gid):
        return "", (f"{gid} is a person's chat, not a group: a group's ID starts with a minus sign "
                    "(for example -1001234567890).")
    if not ID_OK.match(gid):
        return "", "That is not a Telegram group ID: a minus sign, then 5 to 20 digits (for example -1001234567890)."
    return gid, ""


def check_name(text: str) -> tuple[str, str]:
    name = " ".join((text or "").split())
    if not name:
        return "", "A group row needs a name."
    if len(name) > NAME_MAX:
        return "", f"That name is {len(name)} characters; at most {NAME_MAX}, please."
    return name, ""


def marks_words(marks: dict, names: Optional[dict] = None) -> str:
    names = names or {}
    inside = [names.get(p, p) for p, on in marks.items() if on]
    return ("in it: " + ", ".join(inside)) if inside else "nobody marked in it yet"


# ---- writing: the one safe path (dry run unless armed, backup, atomic, read back, history, undo) -----------------
def _read_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _put(path: Path, data: bytes, mode: int) -> None:
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


def _row_line(g: Group, names: dict) -> str:
    return f"{g.name} ({g.id}{', the Team Room group' if g.team else ''}): {marks_words(g.marks, names)}"


def diff(before: list[Group], after: list[Group], names: Optional[dict] = None) -> list[str]:
    names = names or {}
    old = {g.id: _row_line(g, names) for g in before}
    new = {g.id: _row_line(g, names) for g in after}
    out = [f"- {old[k]}" for k in old if old[k] != new.get(k)]
    out += [f"+ {new[k]}" for k in new if new[k] != old.get(k)]
    return out


def _write(paths: E.EditPaths, before: Optional[bytes], after: Optional[bytes], what: str, now: _dt.datetime,
           did: str, lines: list[str], undoes: Optional[str] = None) -> E.Result:
    path = groups_file(paths)
    if not E.armed(paths):
        return E.Result(True, f"DRY RUN, nothing written ({E.why_not_armed(paths)}). This would happen: {what}",
                        dry=True, lines=lines, needs=NEEDS)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    bak: Optional[Path] = None
    mode = 0o600                                    # the group names are Peter's own: readable by him only
    try:
        if _read_bytes(path) != before:
            raise E.EditError("the group list changed while the change was prepared; nothing written, try again")
        if before is not None:
            folder = Path(paths.state) / "group-backups"
            folder.mkdir(parents=True, exist_ok=True)
            bak = folder / f"{stamp}-{path.name}"
            n = 1
            while bak.exists():
                n += 1
                bak = folder / f"{stamp}-{n}-{path.name}"
            shutil.copy2(path, bak)
            if bak.read_bytes() != before:
                raise E.EditError("the backup of the group list did not read back; nothing written")
            mode = path.stat().st_mode & 0o777
        try:
            if after is None:
                path.unlink()
            else:
                _put(path, after, mode)
            got = _read_bytes(path)
            if got is not None and parse(json.loads(got)) != parse(json.loads(after or b"{}")):
                got = b"(does not read back as the same rows)"
        except (OSError, ValueError) as exc:
            got = str(exc).encode()
        if got != after:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                _put(path, before, mode)
            raise E.EditError("the change did not read back right, so the group list was put back exactly as it was")
    except (E.EditError, OSError) as exc:
        return E.Result(False, f"Not changed. {exc}", lines=lines)
    entry = {"id": f"{stamp}-tg-groups", "ts": now.timestamp(), "who": E.WHO, "kind": "tg_groups", "did": did,
             "what": what, "ok": True, "file": str(path), "backup": str(bak) if bak else None,
             "before_b64": _b64(before), "after_b64": _b64(after)}
    if undoes:
        entry["undoes"] = undoes
        entry["id"] += "-undo"
    taken = {e.get("id") for e in E.read_history(paths)}
    base, n = entry["id"], 1
    while entry["id"] in taken:
        n += 1
        entry["id"] = f"{base}-{n}"
    E._append_history(paths, entry)
    return E.Result(True, ("Undone: " if undoes else "Saved: ") + what, lines=lines, needs=NEEDS, entry=entry)


def _change(paths: E.EditPaths, edit: Callable[[list[Group]], tuple[list[Group], str]], did: str,
            now: Optional[_dt.datetime], names: Optional[dict] = None) -> E.Result:
    """Read the rows, let `edit` make the new ones (or refuse with E.EditError), then the safe write."""
    now = now or _dt.datetime.now()
    path = groups_file(paths)
    try:
        before = _read_bytes(path)
        groups, why = read_groups(path)
        if why:
            raise E.EditError(why + "; nothing changed, so a file the desk cannot read is never written over")
        new, what = edit(list(groups))
    except (E.EditError, OSError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    if new == groups:
        return E.Result(True, what + " (already so; nothing to change)")
    return _write(paths, before, dump(new), what, now, did, diff(groups, new, names))


def _find(groups: list[Group], gid: str) -> int:
    i = next((i for i, g in enumerate(groups) if g.id == gid), None)
    if i is None:
        raise E.EditError(f"there is no group row {gid} on the desk")
    return i


def add_group(paths: E.EditPaths, gid: str, name: str, marks: dict, *, now: Optional[_dt.datetime] = None,
              names: Optional[dict] = None) -> E.Result:
    """A new group row. marks: profile -> in this group or not (blank)."""
    def edit(groups: list[Group]) -> tuple[list[Group], str]:
        clean, why = check_id(gid)
        if why:
            raise E.EditError(why)
        label, why = check_name(name)
        if why:
            raise E.EditError(why)
        if any(g.id == clean for g in groups):
            raise E.EditError(f"the group {clean} already has a row on the desk")
        g = Group(clean, label, False, {str(k): bool(v) for k, v in marks.items()})
        return groups + [g], f"the Telegram group row \"{label}\" is added to the desk ({marks_words(g.marks, names)})"
    return _change(paths, edit, "add", now, names)


def remove_group(paths: E.EditPaths, gid: str, *, now: Optional[_dt.datetime] = None,
                 names: Optional[dict] = None) -> E.Result:
    """The row goes from the desk only: no bot leaves the group in Telegram."""
    def edit(groups: list[Group]) -> tuple[list[Group], str]:
        g = groups[_find(groups, gid)]
        extra = "; the old row \"Speaks in Telegram group\" comes back" if g.team else ""
        return [x for x in groups if x.id != gid], (f"the Telegram group row \"{g.name}\" is taken off the desk (no "
                                                    f"bot leaves the group in Telegram{extra})")
    return _change(paths, edit, "remove", now, names)


def set_mark(paths: E.EditPaths, gid: str, profile: str, inside: bool, *, name: str = "",
             now: Optional[_dt.datetime] = None, names: Optional[dict] = None) -> E.Result:
    def edit(groups: list[Group]) -> tuple[list[Group], str]:
        i = _find(groups, gid)
        g = groups[i]
        marks = dict(g.marks)
        marks[profile] = bool(inside)
        groups[i] = Group(g.id, g.name, g.team, marks)
        who = name or profile
        return groups, (f"{who} is marked in the group \"{g.name}\"" if inside else
                        f"{who} is marked not in the group \"{g.name}\" (its cell goes blank)")
    return _change(paths, edit, "mark", now, names)


def set_team(paths: E.EditPaths, gid: str, team: bool, *, now: Optional[_dt.datetime] = None,
             names: Optional[dict] = None) -> E.Result:
    """Mark a row as the Team Room's own group (the conversion): only this mark is written. The team group's voice
    notes keep their one switch in the Team Room's own file, and every setting stays where it is."""
    def edit(groups: list[Group]) -> tuple[list[Group], str]:
        i = _find(groups, gid)
        g = groups[i]
        other = next((x for x in groups if x.team and x.id != gid), None)
        if team and other is not None:
            raise E.EditError(f"\"{other.name}\" is already the Team Room's group; take that mark off it first")
        groups[i] = Group(g.id, g.name, bool(team), dict(g.marks))
        return groups, (f"\"{g.name}\" is marked as the Team Room's group: the row \"Speaks in Telegram group\" moves "
                        "into it, with its switch and every setting unchanged" if team else
                        f"\"{g.name}\" is no longer marked as the Team Room's group: the old row \"Speaks in Telegram "
                        "group\" comes back")
    return _change(paths, edit, "team", now, names)


def undo_groups(last: dict, paths: E.EditPaths, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Put the group list back exactly as it was before the newest change (the same bytes, or no file at all).
    Refused when the list was changed since: the desk never writes over a change it did not make."""
    now = now or _dt.datetime.now()
    path = groups_file(paths)
    try:
        old = _unb64(last.get("before_b64"))
        made = _unb64(last.get("after_b64"))
        there = _read_bytes(path)
    except (OSError, ValueError) as exc:
        return E.Result(False, f"Not undone. {exc}")
    if there != made:
        return E.Result(False, "Not undone. The desk's Telegram group list was changed since, so the desk will not put "
                               "the old one over it.")
    try:
        lines = diff(parse(json.loads(there or b"{}")), parse(json.loads(old or b"{}")))
    except ValueError:
        lines = []
    return _write(paths, there, old, f"the Telegram group rows go back (undo of: {last.get('what')})", now, "undo",
                  lines, undoes=str(last.get("id")))


# ---- the voice mode of one agent's bot in one group (the gateway's own per-chat mode) ----------------------------
def mode_cell(profile: str, gid: str, grid: Any, modes: Optional[dict] = None) -> Any:
    """gridstate.Cell: the gateway's voice mode for (this agent's bot, this group); None when not known."""
    if grid is None:
        return None
    import gridstate as G
    try:
        return G.tg_chat_reading(profile, grid, gid, modes)
    except Exception:                                   # noqa: BLE001  a look must never take the desk down
        return None


def hands_off(profile: str) -> str:
    import gridstate as G
    return G.TELEGRAM_HANDS_OFF.get(profile, "")


def set_mode(paths: E.EditPaths, gid: str, profile: str, token: str, *, name: str = "", group: str = "",
             now: Optional[_dt.datetime] = None) -> E.Result:
    """One agent's voice mode in one group: the gateway's own per-chat mode ("<profile>:telegram:<group id>"), with
    the Voice Desk's own switch code (backup, write, read back, put back on any doubt)."""
    import gridstate as G
    import onoff
    import onoff_hermes
    now = now or _dt.datetime.now()
    who = name or profile
    try:
        clean, why = check_id(gid)
        if why:
            raise E.EditError(why)
        if paths.grid is None:
            raise E.EditError("the Voice Desk's own files are not known to the desk")
        if hands_off(profile):
            raise E.EditError(hands_off(profile)[0].upper() + hands_off(profile)[1:])
        if not (Path(paths.grid.profiles) / profile).is_dir():
            raise E.EditError(f"there is no Hermes profile folder for {who}")
        before = mode_cell(profile, clean, paths.grid)
        changes, note = onoff_hermes.plan_tg_chat(profile, token, paths.grid, clean, "a Telegram group")
    except (E.EditError, onoff.SwitchError, OSError, ValueError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    label = G.LABEL.get(token, token)
    what = f"{who}: spoken replies in the Telegram group \"{group or clean}\", change to {label}"
    if not changes:
        return E.Result(True, f"{who}: already {label} in that group; nothing to change")
    lines = ["+ " + E._plan_words(c) for c in changes]
    needs = NEEDS_MODE + (note if note else "")
    if not E.armed(paths):
        return E.Result(True, f"DRY RUN, nothing written ({E.why_not_armed(paths)}). This would happen: {what}",
                        dry=True, lines=lines, needs=needs)
    return _apply_mode(paths, changes, what, lines, needs, now, profile, clean,
                       before.value if before is not None else None, token)


def _apply_mode(paths: E.EditPaths, changes: list, what: str, lines: list, needs: str, now: _dt.datetime,
                profile: str, gid: str, before: Optional[str], after: str, undoes: Optional[str] = None) -> E.Result:
    import onoff
    stamp = now.strftime("%Y%m%d-%H%M%S")
    files, errors = onoff.apply_changes(changes, Path(paths.voice.hermes_backups) / f"{stamp}-voice-panel", None, stamp)
    if errors:
        return E.Result(False, f"Not changed ({errors[0]}); every file was put back as it was.", lines=lines)
    entry = {"id": f"{stamp}-{profile}-tg-group-mode", "ts": now.timestamp(), "who": E.WHO, "kind": "tg_group_mode",
             "profile": profile, "group": gid, "what": what, "before": before, "after": after, "ok": True,
             "changes": changes, "files": files}
    if undoes:
        entry["undoes"] = undoes
        entry["id"] += "-undo"
    taken = {e.get("id") for e in E.read_history(paths)}
    base, n = entry["id"], 1
    while entry["id"] in taken:
        n += 1
        entry["id"] = f"{base}-{n}"
    E._append_history(paths, entry)
    return E.Result(True, ("Undone: " if undoes else "Saved: ") + what, lines=lines, needs=needs, entry=entry)


def undo_mode(last: dict, paths: E.EditPaths, *, now: Optional[_dt.datetime] = None) -> E.Result:
    import onoff
    now = now or _dt.datetime.now()
    what = f"undo of: {last.get('what')}"
    back = onoff.invert(list(last.get("changes") or []))
    lines = ["+ " + E._plan_words(c) for c in back]
    if not E.armed(paths):
        return E.Result(True, f"DRY RUN, nothing written ({E.why_not_armed(paths)}). This would happen: {what}",
                        dry=True, lines=lines)
    return _apply_mode(paths, back, what, lines, NEEDS_MODE, now, str(last.get("profile")), str(last.get("group")),
                       last.get("after"), str(last.get("before")), undoes=str(last.get("id")))


# ---- asking Telegram: is this agent's bot in that group ----------------------------------------------------------
@dataclass(frozen=True)
class Check:
    state: str              # MEMBER / NOT_MEMBER / ERROR / NO_TOKEN / NO_TELEGRAM
    words: str              # a few words for the cell
    why: str                # one plain sentence
    at: float = 0.0
    bot: str = ""           # the bot's @name, when Telegram said it


def scrub(text: str, secret: str) -> str:
    """Never let a token through: the whole token, and its secret half, become ***."""
    out = str(text or "")
    if secret:
        out = out.replace(secret, "***")
        if ":" in secret:
            out = out.replace(secret.split(":", 1)[1], "***")
    return out


def telegram_ask(token: str, method: str, params: dict, timeout: float = TIMEOUT) -> dict:
    """The one door to Telegram: getChatMember or getChat, with one bot's token. Returns Telegram's answer (a dict
    with "ok"), also for Telegram's own refusals; raises TgUnreachable when it could not be asked at all."""
    if method not in ALLOWED:
        raise ValueError(f"the desk never asks Telegram {method!r}")
    body = urllib.parse.urlencode(params).encode("ascii")
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:            # noqa: S310  a fixed https host
            raw = resp.read(200_000)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(200_000)
        except Exception:                               # noqa: BLE001
            raw = b""
        finally:
            exc.close()
        if not raw:
            return {"ok": False, "error_code": exc.code, "description": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        raise TgUnreachable(scrub(f"could not reach Telegram ({getattr(exc, 'reason', exc)})", token)) from None
    except (TimeoutError, OSError) as exc:
        raise TgUnreachable(scrub(f"could not reach Telegram ({type(exc).__name__})", token)) from None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {"ok": False, "error_code": 0, "description": "Telegram's answer was not readable"}
    return data if isinstance(data, dict) else {"ok": False, "error_code": 0, "description": "an unexpected answer"}


Ask = Callable[[str, str, dict], dict]


def bot_token(profiles: Path, profile: str) -> Optional[str]:
    """The agent's own bot token from its .env, for one call ("" = none there, None = the file cannot be read)."""
    return acd_setup.env_value(Path(profiles) / profile / ".env", TOKEN_NAME)


GONE = {"left": "it has left the group", "kicked": "it was removed (banned) from the group"}


def check_one(token: Optional[str], gid: str, ask: Optional[Ask], now: Optional[float] = None) -> Check:
    """Is the bot behind this token a member of the group? Never raises; no message ever holds the token."""
    now = time.time() if now is None else now
    if ask is None:
        return Check(NO_TELEGRAM, "not checked", "this desk was not given a way to ask Telegram", now)
    if token is None:
        return Check(ERROR, "not confirmed", "the agent's .env file could not be read, so its bot could not be asked",
                     now)
    if not token:
        return Check(NO_TOKEN, "no bot", "this agent has no Telegram bot token, so it has no bot that could be in a "
                                         "group", now)
    bot_id = token.split(":", 1)[0]
    if ":" not in token or not bot_id.isdigit():
        return Check(ERROR, "not confirmed", "the agent's TELEGRAM_BOT_TOKEN does not look like a Telegram bot token",
                     now)
    try:
        got = ask(token, "getChatMember", {"chat_id": gid, "user_id": bot_id})
    except TgUnreachable as exc:
        return Check(ERROR, "not confirmed", scrub(f"not confirmed: {exc}", token), now)
    except Exception as exc:                            # noqa: BLE001  a failed look must never take the desk down
        return Check(ERROR, "not confirmed", f"not confirmed: the check failed ({type(exc).__name__})", now)
    if not isinstance(got, dict):
        return Check(ERROR, "not confirmed", "not confirmed: Telegram's answer was not readable", now)
    if got.get("ok"):
        result = got.get("result") if isinstance(got.get("result"), dict) else {}
        user = result.get("user") if isinstance(result.get("user"), dict) else {}
        bot = str(user.get("username") or "")
        status = str(result.get("status") or "")
        if status in ("creator", "administrator", "member") or (status == "restricted" and result.get("is_member")):
            role = {"creator": ", its owner", "administrator": ", an admin"}.get(status, "")
            return Check(MEMBER, "member", f"Telegram says its bot is a member of this group{role}", now, bot)
        why = GONE.get(status, f"Telegram says its bot's place in this group is: {status or 'not given'}")
        return Check(NOT_MEMBER, "not a member", why if status not in GONE else f"Telegram says {why}", now, bot)
    code = got.get("error_code")
    text = scrub(str(got.get("description") or ""), token)
    low = text.lower()
    if code == 401 or "unauthorized" in low:
        return Check(ERROR, "not confirmed", "Telegram refused this bot's token (Unauthorized): the token may be old",
                     now)
    if code == 429 or "too many requests" in low:
        return Check(ERROR, "not confirmed", "Telegram asked the desk to slow down; press c again in a minute", now)
    if "chat not found" in low:
        return Check(NOT_MEMBER, "not a member", "Telegram says this bot cannot see that group (chat not found): it is "
                                                 "not in it, or the ID is not right", now)
    if code == 403 or "not a member" in low or "was kicked" in low or "user not found" in low \
            or "member not found" in low or "participant_id_invalid" in low:
        return Check(NOT_MEMBER, "not a member", f"Telegram says its bot is not in this group ({text[:80]})", now)
    return Check(ERROR, "not confirmed", f"not confirmed: Telegram answered \"{text[:80] or code}\"", now)


def check_group(gid: str, profiles: list[str], profiles_dir: Path, ask: Optional[Ask],
                now: Optional[float] = None) -> dict:
    """profile -> Check, each agent asked with its OWN bot. The token is read for the call and dropped at once."""
    out: dict = {}
    for profile in profiles:
        out[profile] = check_one(bot_token(profiles_dir, profile) if ask is not None else "", gid, ask, now)
    return out


# ---- group IDs the logs have seen that the desk does not have (a suggestion; never added by itself) -------------
_SEEN = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ .*\bplatform=telegram\b.*?\bchat=(-\d{5,20})\b")
SEEN_TAIL = 1_500_000


def seen_groups(profiles_dir: Path, profiles: list[str], known: set, tail: int = SEEN_TAIL) -> list[tuple]:
    """[(group ID, the agent whose log saw it, when last seen)], newest first: Telegram group chats in the newest
    part of each agent's log that have no row on the desk. Only reads."""
    best: dict = {}
    for profile in profiles:
        path = Path(profiles_dir) / profile / "logs" / "agent.log"
        try:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                if size > tail:
                    fh.seek(size - tail)
                    fh.readline()
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            if "platform=telegram" not in ln:
                continue
            m = _SEEN.match(ln)
            if not m or m.group(2) in known:
                continue
            ts = _dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            if m.group(2) not in best or ts > best[m.group(2)][2]:
                best[m.group(2)] = (m.group(2), profile, ts)
    return sorted(best.values(), key=lambda x: -x[2])
