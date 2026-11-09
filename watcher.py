"""The watcher (step 4): the panel is final, so the watcher puts back anything that has drifted from it.

One cheap check every few minutes (a launchd job runs `watcher.py once`; there is no loop and no process
that stays alive). One shared reader: the same files the grid reads, read once per run. It compares the
live settings with what the panel last set and, where they differ, repairs them through the paths the panel
itself uses, so every repair has a backup, a read-back, and (for switches) a line in the change history:

    on/off switches      the newest successful switch per cell in ~/.voice_desk/voice_history.jsonl is what
                         the panel wants (an undo counts: it is the newest change). A cell that no longer
                         matches is switched again with onoff.set_cell (the grid's own code).
    voices and knobs     agents.toml against every Hermes config.yaml and Claude's voice-defaults.conf,
                         fixed with voicecfg.apply (backup, write, read back with Hermes' own parser).
    Hermes local fixes   hermes_patches.apply_missing (backup, git apply, check the markers, never forced).

Rules that keep it from doing harm:
  * Pause. The file ~/.voice_desk/watcher_paused (W in the dashboard) means: look at nothing, change nothing.
  * A drift must be seen on two runs, at least CONFIRM_SEC apart, before it is repaired, so a file that is
    being written right now, or a switch the panel is just making, is never fought.
  * If the same thing has been put back FLAP_MAX times within an hour, the watcher stops: something else is
    changing it back and the two would fight forever. That is reported, not repaired again.
  * Telegram: only for what the watcher cannot repair (a fix that no longer fits Hermes, a file it cannot
    read, a repair that did not read back, a setting that keeps changing back, a restored Hermes fix that
    needs a gateway restart). One plain sentence to Peter's own chat, each problem once until it clears,
    and never more often than every ALERT_GAP_SEC. It uses `hermes -p orchestrator send`, the same way the
    gateway guard does: no token is read or kept here.

    python3 watcher.py once [--dry-run] [--no-wait]   one check (dry-run: changes and sends nothing)
    python3 watcher.py status | pause | resume
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import agents
import gridstate as G
import hermes_patches as HP
import onoff
import onoff_hermes
import voicecfg
import voiceedit

CONFIRM_SEC = 90               # a drift must still be there this long after it was first seen
FLAP_MAX = 3                   # repairs of one thing within FLAP_WINDOW_SEC before the watcher gives up on it
FLAP_WINDOW_SEC = 3600
ALERT_GAP_SEC = 3600           # at most one Telegram message per hour
ALERT_RETRY_SEC = 900          # after a failed send, wait this long before trying again
STALE_SEC = 900                # the panel calls the watcher "not running" when its last check is older
INTERVAL_SEC = 300             # what the launchd job uses (install_watcher.sh)
LOG_LIMIT_BYTES = 200_000

SWITCH_PLACES = ("tg_group", "terminal", "tg_dm", "mic")
PLACE_WORDS = {"tg_group": "Telegram group voice notes", "terminal": "terminal voice",
               "tg_dm": "Telegram direct chat voice", "mic": "Voice Desk switch"}


# ---- files ---------------------------------------------------------------------------------------------------------
def pause_file(p: G.Paths) -> Path:
    return Path(p.state) / "watcher_paused"


def state_file(p: G.Paths) -> Path:
    return Path(p.state) / "watcher.json"


def log_file(p: G.Paths) -> Path:
    return Path(p.state) / "watcher_log.jsonl"


def is_paused(p: G.Paths) -> bool:
    return pause_file(p).exists()


def set_paused(p: G.Paths, paused: bool) -> bool:
    """Pause or resume. Returns the state afterwards."""
    f = pause_file(p)
    if paused:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_dt.datetime.now().strftime("paused %Y-%m-%d %H:%M:%S\n"), encoding="utf-8")
    else:
        f.unlink(missing_ok=True)
    return is_paused(p)


def toggle_pause(p: G.Paths) -> bool:
    return set_paused(p, not is_paused(p))


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_state(p: G.Paths) -> dict:
    try:
        data = json.loads(state_file(p).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _log(p: G.Paths, now: float, kind: str, text: str, **extra) -> None:
    f = log_file(p)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and f.stat().st_size > LOG_LIMIT_BYTES:
            keep = f.read_text(encoding="utf-8").splitlines()[-500:]
            f.write_text("\n".join(keep) + "\n", encoding="utf-8")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now, "kind": kind, "text": text, **extra}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---- what the panel wants ---------------------------------------------------------------------------------------------
def intents(history: list[dict]) -> dict[tuple[str, str, str], str]:
    """(agent, place, group) -> the token the panel last set. The newest successful switch wins, and an undo is
    a switch back, so it wins too. The Voice Desk mic switches are global and come in groups (own_voices:*,
    queue:*): the agent is '*' and the group is the part before the colon."""
    out: dict[tuple[str, str, str], str] = {}
    for h in history:
        field_ = str(h.get("field", ""))
        if not field_.startswith("switch:") or h.get("ok") is not True or not h.get("new"):
            continue
        place = field_.split(":", 1)[1]
        if place not in SWITCH_PLACES:
            continue
        token = str(h["new"])
        if place == "mic":
            out[("*", "mic", token.split(":")[0])] = token
        else:
            out[(str(h.get("agent", "")), place, "")] = token
    return out


def _plan(cfg: dict, agent_key: str, place: str, token: str, p: G.Paths, e: voiceedit.EditPaths
          ) -> Optional[tuple[Optional[agents.Agent], list[dict]]]:
    """The edits that would put one cell right (read-only: nothing is written). None = no switch behind it.
    The Voice Desk mic switches are global: no agent."""
    if place == "mic":
        return None, onoff._plan_mic(token, p)[0]
    agent = voiceedit._find_agent(cfg, agent_key)
    if not onoff.can_switch(agent, place):
        return None
    if place == "tg_group":
        changes, _ = onoff._plan_tg_group(agent, token, p)
    elif place == "terminal":
        changes, _ = (onoff._plan_claude(agent, token, p, e) if G.kind_of(agent) == G.CLAUDE
                      else onoff_hermes.plan_gemini(agent, token, p))
    else:
        changes, _ = onoff_hermes.plan_tg_dm(agent, token, p)
    return agent, changes


# ---- one run ----------------------------------------------------------------------------------------------------------
@dataclass
class Problem:
    key: str
    text: str                      # plain words, starts with a capital, no full stop


@dataclass
class Drift:
    key: str                       # what is different (the same on the next run)
    text: str
    repair: Callable[[], tuple[bool, str]]
    flap_key: str = ""
    group: str = ""                # drifts of one group are repaired by one call (patches)


@dataclass
class Report:
    when: float
    paused: bool = False
    dry_run: bool = False
    checked: int = 0
    repaired: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)     # seen once, repaired next run if still there
    would: list[str] = field(default_factory=list)       # dry run: what a real run would repair now
    problems: list[Problem] = field(default_factory=list)
    alert: str = ""                                       # the message sent (or that would be)
    alert_sent: bool = False

    def quiet(self) -> bool:
        return not (self.repaired or self.pending or self.would or self.problems or self.alert)


def _switch_drifts(cfg: dict, p: G.Paths, e: voiceedit.EditPaths, history: list[dict], drifts: list[Drift],
                   problems: list[Problem]) -> int:
    n = 0
    for (agent_key, place, group), token in sorted(intents(history).items()):
        n += 1
        try:
            planned = _plan(cfg, agent_key, place, token, p, e)
        except (onoff.SwitchError, voiceedit.EditError, OSError, ValueError) as exc:
            if "is not in agents.toml" in str(exc):
                continue                                  # an agent that was removed from agents.toml
            problems.append(Problem(f"unreadable:{place}:{agent_key}",
                                    f"The file behind {PLACE_WORDS[place]} cannot be read, so I cannot check it ({exc})"))
            continue
        if planned is None:
            continue
        agent, changes = planned
        who = "Voice Desk" if agent is None else agent.display
        label = onoff._label(place, token)
        if agent is not None and place == "terminal" and G.kind_of(agent) == G.GEMINI and token == G.OFF \
                and not G.gemini_hook_honours_switch(p):
            problems.append(Problem("gemini-hook", "Gemini is switched off in the panel but its hook does not read "
                                                   "the off switch, so Gemini still speaks"))
        if not changes:
            continue
        if place == "tg_dm" and agent is not None and G.CELL_FUNCS["tg_dm"](agent, p).value == token:
            continue            # no saved mode, and what voice.auto_tts gives is what the panel wants: do not write a file for it
        def repair(agent_key=agent_key, place=place, token=token) -> tuple[bool, str]:
            who_key = G.grid_agents(cfg)[0].key if place == "mic" else agent_key       # set_cell wants a row for the log
            res = onoff.set_cell(who_key, place, token, p, e)
            if not res.ok:
                return False, res.message
            again = _plan(cfg, agent_key, place, token, p, e)
            if again and again[1]:
                return False, "it did not stay put after the repair"
            return True, res.message

        key = f"switch:{place}:{agent_key}:{group}"
        drifts.append(Drift(key, f"{who}: {PLACE_WORDS[place]} was changed away from {label}; put back to {label}",
                            repair, flap_key=key))
    return n


def _voicecfg_drifts(cfg: dict, p: G.Paths, e: voiceedit.EditPaths, now: float, drifts: list[Drift],
                     problems: list[Problem]) -> int:
    items = voicecfg.collect(cfg, e.profiles, e.claude_conf)
    off = voicecfg.drift(items)
    fixable = [i for i in off if not i.problem]
    for i in off:
        if i.problem:
            problems.append(Problem(f"voicecfg:{i.where}:{i.key}", f"A voice setting for {i.label.split(' (')[0]} "
                                                                   f"cannot be put right ({i.problem})"))
    if fixable:
        names = sorted(f"{i.where}.{i.key}" for i in fixable)
        who = sorted({i.label.split(" (")[0] for i in fixable})

        def repair() -> tuple[bool, str]:
            stamp = _dt.datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
            done, errors = voicecfg.apply(voicecfg.collect(cfg, e.profiles, e.claude_conf),
                                          Path(e.hermes_backups) / f"{stamp}-watcher")
            left = voicecfg.drift(voicecfg.collect(cfg, e.profiles, e.claude_conf))
            if errors or [i for i in left if not i.problem]:
                return False, "; ".join(errors) or "voice settings still differ from agents.toml after the repair"
            return True, f"{len(done)} file(s) rewritten from agents.toml and read back"

        drifts.append(Drift("voicecfg:" + ",".join(names),
                            f"Voice settings for {', '.join(who)} had drifted from agents.toml ({len(fixable)} setting(s)); put back",
                            repair, flap_key="voicecfg"))
    return len(items)


def _patch_drifts(p: G.Paths, loc: HP.Locations, state: dict, drifts: list[Drift], problems: list[Problem],
                  now: float) -> int:
    try:
        states = HP.status(loc)
    except HP.ManifestError as exc:
        problems.append(Problem("patches:list", f"The list of Hermes voice fixes cannot be read ({exc})"))
        return 0
    restored = state.setdefault("restored", {})
    missing: list[HP.PatchState] = []
    for s in states:
        name = s.patch.summary or s.patch.id
        if s.state == HP.PRESENT:
            if s.patch.id in restored:
                if s.loaded is False:
                    problems.append(Problem(f"restart:{s.patch.id}",
                                            f"A Hermes fix ({name}) was put back after an update but only works after the "
                                            f"Hermes gateway restarts"))
                else:
                    restored.pop(s.patch.id, None)
            continue
        if s.state == HP.MISSING:
            missing.append(s)
        else:
            problems.append(Problem(f"patch:{s.patch.id}", f"A Hermes voice fix ({name}) is gone and does not fit the new "
                                                           f"Hermes code, so it needs a person"))
    if missing:
        ids = [s.patch.id for s in missing]

        def repair() -> tuple[bool, str]:
            results = HP.apply_missing(loc, now=_dt.datetime.fromtimestamp(now))
            bad = [r for r in results if not r.ok]
            if bad:
                return False, "; ".join(f"{r.id}: {r.message}" for r in bad)
            for s in missing:
                if s.patch.runs_in == "gateway":
                    restored[s.patch.id] = {"at": now, "name": s.patch.summary or s.patch.id}
            return True, ", ".join(r.id for r in results if "put back" in r.message) + " put back"

        names = ", ".join(s.patch.summary or s.patch.id for s in missing)
        drifts.append(Drift("patches:" + ",".join(ids), f"Hermes voice fix(es) had gone ({names}); put back",
                            repair, flap_key="patches", group="patches"))
    return len(states)


def run_once(p: Optional[G.Paths] = None, e: Optional[voiceedit.EditPaths] = None, *,
             send: Optional[Callable[[str], bool]] = None, dry_run: bool = False, now: Optional[float] = None,
             confirm_sec: float = CONFIRM_SEC, loc: Optional[HP.Locations] = None) -> Report:
    """One check. Never raises: whatever goes wrong becomes a problem in the report."""
    p = p or G.Paths.default()
    e = e or voiceedit.EditPaths.default()
    now = time.time() if now is None else now
    rep = Report(when=now, dry_run=dry_run)
    state = load_state(p)
    if is_paused(p):
        rep.paused = True
        if not dry_run:
            state.update(at=now, paused=True)
            _atomic_json(state_file(p), state)
        return rep

    drifts: list[Drift] = []
    problems: list[Problem] = []
    try:
        cfg = agents.load(e.toml)
    except Exception as exc:                                              # agents.toml itself is broken
        cfg = None
        problems.append(Problem("agents.toml", f"agents.toml cannot be read ({exc})"))
    if cfg is not None:
        for name, fn in (("switches", lambda: _switch_drifts(cfg, p, e, voiceedit.read_history(e), drifts, problems)),
                         ("voices", lambda: _voicecfg_drifts(cfg, p, e, now, drifts, problems)),
                         ("fixes", lambda: _patch_drifts(p, loc or p.hermes_locations, state, drifts, problems, now))):
            try:
                rep.checked += fn()
            except Exception as exc:                                      # one broken area never stops the others
                problems.append(Problem(f"check:{name}", f"The watcher's check of {name} failed ({type(exc).__name__}: {exc})"))

    suspect: dict = state.setdefault("suspect", {})
    repairs: dict = state.setdefault("repairs", {})
    seen = {d.key for d in drifts}
    for k in list(suspect):
        if k not in seen:
            suspect.pop(k)                                                # it fixed itself, or the panel changed it
    done_groups: dict[str, tuple[bool, str]] = {}
    for d in drifts:
        new_sighting = d.key not in suspect
        first = suspect.setdefault(d.key, now)
        flap = [t for t in repairs.get(d.flap_key or d.key, []) if now - t < FLAP_WINDOW_SEC]
        repairs[d.flap_key or d.key] = flap
        if len(flap) >= FLAP_MAX:
            problems.append(Problem(f"flap:{d.flap_key or d.key}",
                                    f"{d.text.split(';')[0]}, and it keeps changing back after I put it right, so "
                                    f"something else is rewriting it"))
            continue
        if confirm_sec > 0 and (new_sighting or now - first < confirm_sec):
            rep.pending.append(d.text)
            continue
        if dry_run:
            rep.would.append(d.text)
            continue
        if d.group and d.group in done_groups:
            ok, msg = done_groups[d.group]
        else:
            try:
                ok, msg = d.repair()
            except Exception as exc:
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            if d.group:
                done_groups[d.group] = (ok, msg)
        flap.append(now)
        if ok:
            rep.repaired.append(d.text)
            suspect.pop(d.key, None)
            _log(p, now, "repair", d.text, key=d.key, detail=msg)
        else:
            problems.append(Problem(f"repair:{d.key}", f"{d.text.split(';')[0]}, and the repair did not work ({msg[:160]})"))
            _log(p, now, "repair-failed", d.text, key=d.key, detail=msg)

    rep.problems = problems
    for pid, info in state.get("restored", {}).items():          # a gateway fix put back just now: only a restart finishes it
        if info.get("at") == now and not any(x.key == f"restart:{pid}" for x in problems):
            problems.append(Problem(f"restart:{pid}", f"A Hermes fix ({info.get('name', pid)}) was put back after an update "
                                                      f"but only works after the Hermes gateway restarts"))
    _alert(p, state, rep, now, send, dry_run)
    if not dry_run:
        state.update(at=now, paused=False, checked=rep.checked,
                     problems=[{"key": x.key, "text": x.text} for x in problems],
                     pending=rep.pending)
        recent = state.setdefault("recent", [])
        for text in rep.repaired:
            recent.append({"at": now, "text": text})
        state["recent"] = recent[-8:]
        _atomic_json(state_file(p), state)
    return rep


def _sentence(problems: list[Problem]) -> str:
    first = problems[0].text.rstrip(". ")
    more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
    return f"Voice Desk could not put this right by itself: {first}{more}. The Watcher line in the panel has the details."


def _alert(p: G.Paths, state: dict, rep: Report, now: float, send: Optional[Callable[[str], bool]],
           dry_run: bool) -> None:
    """Tell Peter about problems the watcher cannot repair: each problem once until it clears, one message per
    ALERT_GAP_SEC at most, and after a failed send not again for ALERT_RETRY_SEC."""
    alerted: dict = state.setdefault("alerted", {})
    current = {x.key for x in rep.problems}
    for k in list(alerted):
        if k not in current:
            alerted.pop(k)                                                # solved: a later return may be told again
    new = [x for x in rep.problems if x.key not in alerted]
    if not new:
        return
    if now - float(state.get("last_alert_at", 0)) < ALERT_GAP_SEC or now - float(state.get("last_attempt_at", 0)) < ALERT_RETRY_SEC:
        return                                                            # told recently: wait, tell later
    text = _sentence(new)
    rep.alert = text
    if dry_run or send is None:
        return
    state["last_attempt_at"] = now
    try:
        ok = bool(send(text))
    except Exception:
        ok = False
    rep.alert_sent = ok
    _log(p, now, "alert" if ok else "alert-failed", text, keys=[x.key for x in new])
    if ok:
        state["last_alert_at"] = now
        for x in new:
            alerted[x.key] = now


# ---- Telegram (the way the gateway guard does it) -------------------------------------------------------------------------
def hermes_bin() -> str:
    return os.environ.get("VOICE_DESK_HERMES_BIN") or shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")


def send_dm(text: str, chat: str, runner=subprocess.run) -> bool:
    """One Telegram message to Peter's own chat: `hermes -p orchestrator send --to telegram:<chat> <text> --json`.
    That command talks to the bot directly with the credentials Hermes already holds, so this program never reads
    or keeps a token, and it works while the gateway is down. True only if Hermes says it went out."""
    cmd = [hermes_bin(), "-p", "orchestrator", "send", "--to", f"telegram:{chat}", text, "--json"]
    try:
        return runner(cmd, capture_output=True, text=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---- what the panel shows ------------------------------------------------------------------------------------------------
def light(p: G.Paths, now: Optional[float] = None) -> tuple[Optional[bool], str, str]:
    """(ok, label, detail) for the top strip: True green, False red, None grey."""
    now = time.time() if now is None else now
    if is_paused(p):
        return None, "WATCHER PAUSED (W resumes)", "the watcher looks at nothing and repairs nothing while paused; the panel is not being enforced"
    st = load_state(p)
    at = float(st.get("at", 0) or 0)
    if not at or now - at > STALE_SEC:
        return None, "watcher not running", ("its check job is not loaded (./install_watcher.sh), or the Mac was asleep; "
                                             "drift is not being put back")
    ago = max(0, int(now - at)) // 60
    when = "just now" if ago < 1 else f"{ago} min ago"
    probs = st.get("problems") or []
    if probs:
        return False, f"watcher cannot repair {len(probs)} thing(s)", "; ".join(x.get("text", "") for x in probs)[:300]
    recent = st.get("recent") or []
    tail = f"last repair: {recent[-1]['text']}" if recent else "nothing has drifted"
    return True, f"watcher OK (checked {when})", tail


def history_lines(p: G.Paths, n: int = 5) -> list[str]:
    try:
        rows = [json.loads(x) for x in log_file(p).read_text(encoding="utf-8").splitlines()[-n:]]
    except (OSError, ValueError):
        return []
    return [f"{_dt.datetime.fromtimestamp(r['ts']):%d %b %H:%M}  watcher: {r['text']}" for r in reversed(rows)]


# ---- command line ----------------------------------------------------------------------------------------------------------
def _print(rep: Report) -> None:
    if rep.paused:
        print("watcher is paused: nothing checked, nothing changed")
        return
    tag = "[dry run] " if rep.dry_run else ""
    for t in rep.repaired:
        print(f"{tag}repaired: {t}")
    for t in rep.would:
        print(f"{tag}would repair now: {t}")
    for t in rep.pending:
        print(f"{tag}drift seen, repaired on the next run if still there: {t}")
    for x in rep.problems:
        print(f"{tag}CANNOT REPAIR: {x.text}")
    if rep.alert:
        print(f"{tag}{'telegram sent' if rep.alert_sent else 'telegram would be sent' if rep.dry_run else 'telegram NOT sent'}: {rep.alert}")
    if rep.quiet():
        print(f"{tag}ok: {rep.checked} things checked, nothing drifted")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="watcher.py", description="Keep Voice Desk's live settings as the panel set them.")
    ap.add_argument("command", choices=("once", "status", "pause", "resume"))
    ap.add_argument("--dry-run", action="store_true", help="once: change nothing, send nothing, only say what would happen")
    ap.add_argument("--no-wait", action="store_true", help="once: repair a drift the first time it is seen (manual use)")
    args = ap.parse_args(argv)
    p = G.Paths.default()
    if args.command == "pause":
        set_paused(p, True)
        print("watcher paused")
        return 0
    if args.command == "resume":
        set_paused(p, False)
        print("watcher resumed")
        return 0
    if args.command == "status":
        ok, label, detail = light(p)
        print(label + (f" — {detail}" if detail else ""))
        for ln in history_lines(p):
            print("  " + ln)
        return 0 if ok is not False else 1
    rep = run_once(p, voiceedit.EditPaths.default(), dry_run=args.dry_run, confirm_sec=0 if args.no_wait else CONFIRM_SEC,
                   send=None if args.dry_run else (lambda text: send_dm(text, p.tg_dm_chat)))
    if not rep.quiet() or args.dry_run:
        _print(rep)
    return 1 if rep.problems else 0


if __name__ == "__main__":
    sys.exit(main())
