"""Agent Control Desk, version four: the watcher. "The desk is final."

It compares each agent's LIVE settings with the desk's own record of what was last saved THROUGH the desk
(history.jsonl in the desk's folder). Where they differ, something changed the setting behind the desk's back:
that is DRIFT.

    .venv/bin/python acd_watch.py --once            one look: print what drifted, change nothing
    .venv/bin/python acd_watch.py --once --act --armed    one look, and (only if every switch below is on) put it back
    .venv/bin/python acd_watch.py status | pause | resume | auto on | auto off

What it watches: the language model, the thinking effort and each backup the desk has switched on or off.
(The voice switches have their own watcher, the Voice Desk's watcher.py, which keeps working unchanged.)

BY DEFAULT IT ONLY REPORTS. A drift is shown red with what the desk set, what the file says now and since when,
and the desk offers two answers in a pop-up:

    put it back                 the desk's value is written again, through the desk's own safe path
    accept it                   the new value becomes what the desk expects from now on

AUTOMATIC PUT-BACK exists but is OFF unless ALL of these are true:

    * its own switch is on            (acd_watch.py auto on  ->  auto_putback.json in the desk's folder)
    * the desk is armed               (the launcher's --armed AND the desk's own armed switch)
    * it is not paused                (P in the desk, or acd_watch.py pause  ->  watcher_paused)
    * the drift was seen on two looks at least CONFIRM_SEC apart, so a file that is being written is never fought
    * the same setting has not been put back FLAP_MAX times within the hour (then something else keeps changing
      it, and fighting it would never end: that is reported instead)
    * the drifted value is not a temporary choice: a model picked inside one chat or pinned on one job lives in
      that chat, not in the settings file, and the watcher never touches it. If the settings file's new model is
      the same one a chat is using as a temporary choice right now, the watcher leaves it alone and says so.

It is an overseer, never a lifeline: if it stops, every agent and every voice keeps working exactly as last
set. It sends nothing to Telegram. Its notes go on the desk's screen and into watch.log in the desk's folder.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import acd_data as D
import acd_edit as E

CONFIRM_SEC = 90
FLAP_MAX = 3
FLAP_WINDOW_SEC = 3600
LOG_LIMIT_BYTES = 200_000
KINDS = ("model", "effort", "backup")


def auto_file(paths: E.EditPaths) -> Path:
    return paths.state / "auto_putback.json"


def pause_file(paths: E.EditPaths) -> Path:
    return paths.state / "watcher_paused"


def state_file(paths: E.EditPaths) -> Path:
    return paths.state / "watch_state.json"


def log_file(paths: E.EditPaths) -> Path:
    return paths.state / "watch.log"


def auto_on(paths: E.EditPaths) -> bool:
    data = E._read_json(auto_file(paths))
    return isinstance(data, dict) and data.get("on") is True


def set_auto(paths: E.EditPaths, on: bool, now: Optional[float] = None) -> None:
    E._atomic_write_new(auto_file(paths), json.dumps({"on": bool(on), "at": time.time() if now is None else now}) + "\n")


def is_paused(paths: E.EditPaths) -> bool:
    return pause_file(paths).exists()


def set_paused(paths: E.EditPaths, paused: bool) -> bool:
    if paused:
        E._atomic_write_new(pause_file(paths), _dt.datetime.now().isoformat(timespec="seconds") + "\n")
    else:
        pause_file(paths).unlink(missing_ok=True)
    return paused


def _load_state(paths: E.EditPaths) -> dict:
    data = E._read_json(state_file(paths))
    return data if isinstance(data, dict) else {}


def _log(paths: E.EditPaths, now: float, kind: str, text: str) -> None:
    """One line in the desk's own watch.log. Only ever called when the desk's folder already has a history."""
    path = log_file(paths)
    try:
        if path.exists() and path.stat().st_size > LOG_LIMIT_BYTES:
            keep = path.read_text(encoding="utf-8").splitlines()[-400:]
            E._atomic_write_new(path, "\n".join(keep) + "\n")
        paths.state.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now, "kind": kind, "text": text}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_lines(paths: E.EditPaths, n: int = 5) -> list[str]:
    try:
        rows = [json.loads(x) for x in log_file(paths).read_text(encoding="utf-8").splitlines()[-n:]]
    except (OSError, ValueError):
        return []
    return [f"{_dt.datetime.fromtimestamp(r['ts']):%-d %b %H:%M}  {r['text']}" for r in reversed(rows)]


# ---- what the desk expects ----------------------------------------------------------------------------------
def expected(history: list[dict]) -> dict:
    """(profile, key) -> {"want", "ts", "words"}: the newest thing the desk saved (or accepted) for each setting.
    key is "model", "effort" or "backup|<service>|<model>". An undo counts: it is simply the newest change."""
    out: dict = {}
    for e in history:
        if not e.get("ok") or not e.get("profile"):
            continue
        kind = e.get("kind")
        if kind == "accept":
            out[(e["profile"], str(e.get("key")))] = {"want": e.get("after"), "ts": e.get("ts", 0)}
        elif kind in ("model", "effort"):
            out[(e["profile"], kind)] = {"want": e.get("after"), "ts": e.get("ts", 0)}
        elif kind == "backup" and e.get("model"):
            out[(e["profile"], f"backup|{e.get('provider') or ''}|{e['model']}")] = {
                "want": bool(e.get("after")), "ts": e.get("ts", 0)}
    return out


@dataclass(frozen=True)
class Drift:
    profile: str
    name: str                       # the agent's name, as shown
    key: str                        # "model" / "effort" / "backup|service|model"
    want: Any                       # what the desk last saved
    have: Any                       # what the settings file says now
    saved_at: float                 # when the desk saved its value
    changed_at: Optional[float]     # when the settings file was last written (the best "since when" there is)
    first_seen: Optional[float] = None
    note: str = ""                  # why the watcher is leaving it alone, if it is

    @property
    def what(self) -> str:
        if self.key == "model":
            return "language model"
        if self.key == "effort":
            return "thinking effort"
        _, service, model = self.key.split("|", 2)
        return f"backup {D.model_name(model)} via {D.service_name(service, short=True)}"

    def words(self, value: Any) -> str:
        if self.key == "model":
            return D.model_name(str(value)) if value else "nothing"
        if self.key == "effort":
            return f"{value} effort" if value else "nothing"
        return "on" if value else "off"

    @property
    def sentence(self) -> str:
        when = f" The file was last written {_dt.datetime.fromtimestamp(self.changed_at):%-d %b %H:%M}." \
            if self.changed_at else ""
        return (f"{self.name}'s {self.what}: the desk set {self.words(self.want)} "
                f"({_dt.datetime.fromtimestamp(self.saved_at):%-d %b %H:%M}), the settings file now says "
                f"{self.words(self.have)}.{when}")


@dataclass
class Report:
    drifts: list[Drift] = field(default_factory=list)
    put_back: list[str] = field(default_factory=list)
    left_alone: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    paused: bool = False
    auto: bool = False
    armed: bool = False
    acted: bool = False             # this look was allowed to change things

    @property
    def mode(self) -> str:
        if self.paused:
            return "paused: it reports nothing and changes nothing until it is resumed"
        if self.auto and self.armed:
            return "automatic put-back is ON"
        if self.auto:
            return "automatic put-back is switched on, but the desk is not armed, so it only reports"
        return "reports only (automatic put-back is off)"


def _live(paths: E.EditPaths, profile: str) -> Optional[dict]:
    """The three managed things as the settings file's own lines say them. None when it cannot be read."""
    try:
        text = (paths.profiles / profile / "config.yaml").read_text(encoding="utf-8")
        got = E.managed(text)
        listed = {(i.provider, i.model) for i in E.read_block(text).items}
    except (OSError, E.EditError, ValueError, KeyError):
        return None
    return {"model": got["model"]["default"], "effort": got["agent"]["reasoning_effort"], "listed": listed}


def look(paths: E.EditPaths, now: Optional[float] = None, names: Optional[dict] = None) -> list[Drift]:
    """Every drift there is right now. Read-only: the desk's history and each settings file, nothing else."""
    now = time.time() if now is None else now
    want = expected(E.read_history(paths))
    seen = _load_state(paths).get("seen", {}) if want else {}
    out: list[Drift] = []
    live: dict[str, Optional[dict]] = {}
    for (profile, key), rec in sorted(want.items()):
        if profile not in live:
            live[profile] = _live(paths, profile)
        cur = live[profile]
        if cur is None:
            continue
        if key in ("model", "effort"):
            have = cur[key]
        else:
            _, service, model = key.split("|", 2)
            have = (service, model) in cur["listed"]
        if have == rec["want"]:
            continue
        try:
            changed = (paths.profiles / profile / "config.yaml").stat().st_mtime
        except OSError:
            changed = None
        name = (names or {}).get(profile) or E._name(paths, profile)
        out.append(Drift(profile, name, key, rec["want"], have, float(rec["ts"] or 0), changed,
                         seen.get(f"{profile}|{key}")))
    return out


# ---- the two answers ------------------------------------------------------------------------------------------
def put_back(d: Drift, paths: E.EditPaths, *, now: Optional[_dt.datetime] = None,
             loader: Optional[E.Loader] = None) -> E.Result:
    """Write the desk's value again, through the desk's own safe path (a dry run when the desk is not armed)."""
    if d.key == "model":
        return E.change_model(d.profile, str(d.want), paths, now=now, loader=loader)
    if d.key == "effort":
        return E.change_effort(d.profile, str(d.want), paths, now=now, loader=loader)
    _, service, model = d.key.split("|", 2)
    return E.set_backup(d.profile, service, model, bool(d.want), paths, now=now, loader=loader)


def accept(d: Drift, paths: E.EditPaths, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Keep what the file says now: it becomes what the desk expects. Only the desk's own record changes."""
    now = now or _dt.datetime.now()
    what = f"accepted as the new setting: {d.name}'s {d.what} is {d.words(d.have)} (the desk had set {d.words(d.want)})"
    if not E.armed(paths):
        return E.Result(True, f"DRY RUN, nothing written ({E.why_not_armed(paths)}). This would happen: {what}",
                        dry=True, needs="Only the desk's own record changes; no agent's settings file is touched.")
    entry = {"id": f"{now:%Y%m%d-%H%M%S}-{d.profile}-accept", "ts": now.timestamp(), "who": E.WHO, "kind": "accept",
             "profile": d.profile, "key": d.key, "what": what, "before": d.want, "after": d.have, "ok": True}
    E._append_history(paths, entry)
    _log(paths, now.timestamp(), "accepted", what)
    return E.Result(True, "Saved: " + what, entry=entry,
                    needs="Only the desk's own record changed; no agent's settings file was touched.")


# ---- one look, with or without acting ---------------------------------------------------------------------------
def run_once(paths: E.EditPaths, *, act: bool = False, now: Optional[float] = None,
             loader: Optional[E.Loader] = None, overrides: Optional[dict] = None,
             names: Optional[dict] = None) -> Report:
    """One look. With act=False (the default), or when the desk is not armed, nothing is ever written, not even
    the watcher's own notes. With act=True on an armed desk the watcher keeps its notes (watch.log, when it first
    saw each drift) and, only if automatic put-back is switched on as well, puts drifted settings back.
    overrides: profile -> the model a chat is using as a temporary choice right now (never fought)."""
    now = time.time() if now is None else now
    rep = Report(paused=is_paused(paths), auto=auto_on(paths), armed=E.armed(paths))
    if rep.paused:
        return rep
    rep.drifts = look(paths, now, names)
    if not act or not rep.armed:                            # an unarmed desk writes nothing, not even these notes
        return rep
    may_act = rep.auto
    state = _load_state(paths)
    seen: dict = dict(state.get("seen", {}))
    repairs: dict = dict(state.get("repairs", {}))
    live_keys = {f"{d.profile}|{d.key}" for d in rep.drifts}
    for k in [k for k in seen if k not in live_keys]:
        seen.pop(k)                                         # it cleared by itself
    fresh = []
    for d in rep.drifts:
        k = f"{d.profile}|{d.key}"
        first = seen.setdefault(k, now)
        d = Drift(d.profile, d.name, d.key, d.want, d.have, d.saved_at, d.changed_at, first)
        if first == now:
            _log(paths, now, "drift", d.sentence)
        if not may_act:
            fresh.append(d)
            continue
        rep.acted = True
        temp = (overrides or {}).get(d.profile)
        if d.key == "model" and temp and temp == d.have:
            note = f"left alone: {d.name}'s new model is the one a chat is using as a temporary choice right now"
            rep.left_alone.append(note)
            fresh.append(Drift(d.profile, d.name, d.key, d.want, d.have, d.saved_at, d.changed_at, first, note))
            continue
        if now - first < CONFIRM_SEC:
            note = "seen once; it is put back only if it is still there on the next look"
            fresh.append(Drift(d.profile, d.name, d.key, d.want, d.have, d.saved_at, d.changed_at, first, note))
            continue
        recent = [t for t in repairs.get(k, []) if now - t <= FLAP_WINDOW_SEC]
        if len(recent) >= FLAP_MAX:
            note = (f"NOT put back: it was put back {len(recent)} times within the hour and keeps changing, so "
                    "something else is setting it. Accept it, or find what changes it")
            if note not in rep.problems:
                rep.problems.append(f"{d.name}'s {d.what} keeps changing back")
            fresh.append(Drift(d.profile, d.name, d.key, d.want, d.have, d.saved_at, d.changed_at, first, note))
            continue
        res = put_back(d, paths, now=_dt.datetime.fromtimestamp(now), loader=loader)
        if res.ok and not res.dry:
            repairs[k] = recent + [now]
            seen.pop(k, None)
            text = f"put back: {d.name}'s {d.what} is {d.words(d.want)} again (the file had said {d.words(d.have)})"
            rep.put_back.append(text)
            _log(paths, now, "put back", text)
        else:
            rep.problems.append(f"{d.name}'s {d.what} could not be put back: {res.message}")
            _log(paths, now, "problem", rep.problems[-1])
            fresh.append(d)
    rep.drifts = fresh
    if seen or repairs or state:
        E._atomic_write_new(state_file(paths), json.dumps({"at": now, "seen": seen, "repairs": repairs}) + "\n")
    return rep


@dataclass(frozen=True)
class WatchView:
    """What the desk's screen shows about the watcher. Built without writing anything."""
    drifts: tuple = ()
    mode: str = ""
    paused: bool = False
    auto: bool = False
    notes: tuple = ()               # the newest lines of the watcher's own log


def view(paths: E.EditPaths, now: Optional[float] = None, names: Optional[dict] = None) -> WatchView:
    rep = run_once(paths, act=False, now=now, names=names)
    return WatchView(tuple(rep.drifts), rep.mode, rep.paused, rep.auto, tuple(log_lines(paths, 3)))


# ---- the command line ---------------------------------------------------------------------------------------------
def _print(rep: Report) -> None:
    print("watcher: " + rep.mode)
    if rep.paused:
        return
    for text in rep.put_back:
        print("  " + text)
    for text in rep.left_alone:
        print("  " + text)
    for d in rep.drifts:
        print("  DRIFT  " + d.sentence + (f"  ({d.note})" if d.note else ""))
    for text in rep.problems:
        print("  PROBLEM  " + text)
    if not (rep.drifts or rep.put_back or rep.problems):
        print("  nothing has drifted from what the desk last saved")


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    armed_flag = "--armed" in args
    paths = E.EditPaths.default(launcher_armed=armed_flag)
    words = [a for a in args if not a.startswith("--")]
    if "--once" in args or words[:1] == ["once"]:
        rep = run_once(paths, act="--act" in args, loader=E.default_loader())
        _print(rep)
        return 1 if rep.drifts or rep.problems else 0
    if words[:1] == ["status"]:
        _print(run_once(paths, act=False))
        for ln in log_lines(paths, 8):
            print("    " + ln)
        return 0
    if words[:1] == ["pause"]:
        set_paused(paths, True)
        print("watcher paused: it reports nothing and changes nothing until `acd_watch.py resume`")
        return 0
    if words[:1] == ["resume"]:
        set_paused(paths, False)
        print("watcher resumed")
        return 0
    if words[:2] in (["auto", "on"], ["auto", "off"]):
        set_auto(paths, words[1] == "on")
        print(f"automatic put-back is {'ON' if words[1] == 'on' else 'OFF'} ({auto_file(paths)}). "
              "It acts only when the desk is armed as well, and never while paused.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
