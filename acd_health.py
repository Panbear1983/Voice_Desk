"""Agent Control Desk, version four: is each service an agent uses really working?

Two questions per service (the Claude subscription, Antigravity, the ChatGPT subscription, NVIDIA, OpenRouter):

    sign-in      can Hermes still sign in to it?       read from the agent's own log
    allowance    how much is left, and when it resets  read from Hermes' own `hermes usage --json`

Everything here only READS, and nothing here calls a model.

WHY THE LOG WINS. `hermes auth status anthropic` answers "logged in" even when the saved login is dead (seen on
2026-11-16: the log said "OAuth refresh token ... is terminally invalid" and "No Anthropic credentials found"
while the status command still said logged in). So the desk never asks that command. It reads what really
happened: the lines Hermes writes when a sign-in or a reply fails, and the replies that did get an answer.

    sign-in is dead        a sign-in failure is newer than the newest reply that service answered.
                           Only Peter can fix it (he signs in again); the desk says so.
    allowance used up      the service refused a model with "quota reached" / a rate limit. The log often gives
                           the reset time ("Resets in 86h46m59s"); the desk shows it as "about N hours".
    good                   the newest evidence is a reply that service answered.
    unknown                the log holds no evidence either way. Never guessed.

THE ALLOWANCE READER comes from the DLS add-on (dls-observer: dls_supplier.py and dls_core/quotas.py, built in
November 2026, cards "DLS Phase 1A" to "1J"). That reader worked only INSIDE the agents' system, on its
in-memory usage cache, so the code could not be lifted as it was. What is carried over is its tested RULES for
judging a reading, re-applied to the output of the same usage check run from outside (`hermes usage --json`):

    * a reading older than 15 minutes is "unknown", never "full" and never "empty"      (dls_core.quotas.fresh)
    * a percentage must be a real number between 0 and 100, or the reading is unknown   (dls_core.quotas.number)
    * two windows with the same name are never merged or guessed between                (dls_supplier._reading)
    * units stay what Hermes reports: percent of the window; left = 100 - used           (dls_supplier.PERCENT)

The usage check is run seldom (at most once per service every 10 minutes, the DLS refresher's pace), outside the
screen's own thread, and a failed check simply leaves "unknown".
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

GOOD, FAILING, UNKNOWN = "good", "failing", "unknown"
# two allowances can carry the same name (two accounts of one service): say so in ordinary words
TWIN_NOTE = "there are two allowances with this name; each is listed on its own line and they are never added together"
SIGNIN_DEAD, ALLOWANCE_OUT = "signin_dead", "allowance_out"

ANTHROPIC = "anthropic"
USAGE_SERVICES = ("anthropic", "antigravity-subscription-directsdk", "openai-codex", "openrouter")
STALE_SEC = 15 * 60             # DLS: a reading older than this is unknown
REFRESH_SEC = 10 * 60           # DLS refresher (INTERVAL_S): at most one usage check per service this often
USAGE_TIMEOUT = 25
EVIDENCE_SEC = 3 * 24 * 3600    # log evidence older than this says nothing about now

# ---- the log lines that say something about a service ----------------------------------------------------
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \w+ (?:\[([^\]]+)\] )?[\w.]+: (.*)$")
_FAILED = re.compile(r"^API call failed .*?\bprovider=(\S+) .*?\bmodel=(\S+) summary=(.*)$")
_TURN = re.compile(r"^conversation turn: session=(\S+) model=(\S+) provider=(\S+) ")
_FALLBACK = re.compile(r"^Fallback activated: (\S+) → (\S+) \((\S+)\)")
_FALLBACK_EMPTY = re.compile(r"^Fallback activated after empty responses: now using (\S+) on (\S+)")
_NOT_CONFIGURED = re.compile(r"^Fallback to (\S+) failed: provider not configured")
_AUTH_FAILED = re.compile(r"^Primary provider auth failed \((.*?)\)\. Falling back")
_NO_CREDS = re.compile(r"\bNo (\w+) credentials found")
_REQUESTED = re.compile(r"^resolve_provider_client: (\S+) requested but no \w+ credentials found")
_DEAD_TOKEN = re.compile(r"^(\w+)(?: Code)? OAuth refresh token.*is terminally invalid")
_RESETS = re.compile(r"Resets in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", re.I)
_QUOTA = re.compile(r"quota reached|rate.?limit|\b429\b|exceed your account", re.I)
_NAME_TO_SERVICE = {"anthropic": ANTHROPIC, "claude": ANTHROPIC, "openai": "openai-codex", "codex": "openai-codex",
                    "openrouter": "openrouter", "nvidia": "nvidia"}


@dataclass(frozen=True)
class Event:
    """One thing the log says about one service."""
    ts: float
    kind: str                       # "signin" (could not sign in) / "allowance" / "failed" / "answered"
    service: str
    model: str = ""
    detail: str = ""
    reset_ts: Optional[float] = None     # for "allowance": when the log says it resets


def _stamp(text: str) -> float:
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def _service_of(word: str) -> str:
    return _NAME_TO_SERVICE.get(word.lower(), word.lower())


def reset_seconds(text: str) -> Optional[int]:
    """"Resets in 86h46m59s" -> seconds. None when the text gives no time."""
    m = _RESETS.search(text or "")
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def parse_events(text: str) -> list[Event]:
    """Everything a piece of an agent's log says about its services, oldest first. Pure: text in, events out.

    A reply counts as ANSWERED by the service it ended on: the one it began on when nothing failed, or the backup
    it dropped to. (Hermes writes no "answered" line; a reply that failed on every service ends with failures on
    each, and those are newer than its start, so they win.)"""
    out: list[Event] = []
    open_turn: dict[str, tuple[float, str, str, bool]] = {}     # session -> (ts, model, service, something failed)

    def close(session: str) -> None:
        got = open_turn.pop(session, None)
        if got:
            ts, model, service, _ = got
            out.append(Event(ts, "answered", service, model))

    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        ts, session, body = _stamp(m.group(1)), m.group(2), m.group(3)
        t = _TURN.match(body)
        if t and session:
            close(session)
            open_turn[session] = (ts, t.group(2), t.group(3), False)
            continue
        f = _FAILED.match(body)
        if f:
            service, model, summary = f.group(1), f.group(2), f.group(3).strip()
            if _QUOTA.search(summary):
                secs = reset_seconds(summary)
                out.append(Event(ts, "allowance", service, model, summary[:200], ts + secs if secs else None))
            elif _NO_CREDS.search(summary) or "invalid_grant" in summary:
                out.append(Event(ts, "signin", service, model, summary[:200]))
            else:
                out.append(Event(ts, "failed", service, model, summary[:200]))
            if session in open_turn:                      # the reply did not get its answer from this one
                o = open_turn[session]
                if o[2] == service and o[1] == model:
                    open_turn[session] = (o[0], "", "", True)
            continue
        fb = _FALLBACK.match(body)
        if fb and session in open_turn:
            open_turn[session] = (ts, fb.group(2), fb.group(3), True)
            continue
        fe = _FALLBACK_EMPTY.match(body)
        if fe and session in open_turn:
            open_turn[session] = (ts, fe.group(1), fe.group(2), True)
            continue
        nc = _NOT_CONFIGURED.match(body)
        if nc:
            out.append(Event(ts, "signin", _service_of(nc.group(1)), detail="no sign-in is set up for it"))
            continue
        rq = _REQUESTED.match(body)
        if rq:
            out.append(Event(ts, "signin", _service_of(rq.group(1)), detail="no sign-in found"))
            continue
        dead = _DEAD_TOKEN.match(body)
        if dead:
            out.append(Event(ts, "signin", _service_of(dead.group(1)), detail="the saved sign-in is no longer valid"))
            continue
        au = _AUTH_FAILED.match(body)
        if au:
            who = _NO_CREDS.search(au.group(1))
            if who:
                out.append(Event(ts, "signin", _service_of(who.group(1)), detail="no sign-in found"))
    for session in list(open_turn):
        close(session)
    out = [e for e in out if e.service]
    out.sort(key=lambda e: e.ts)
    return out


# ---- the allowance, as Hermes' own usage check reports it ---------------------------------------------------
@dataclass(frozen=True)
class Window:
    label: str                      # Hermes' own name for it: "Weekly", "Claude/GPT (5h)" ...
    left: Optional[float]           # percent of the window left; None = this reading could not be used
    resets_at: Optional[float]      # when it resets (a moment in time), None when Hermes gives none
    note: str = ""                  # why a reading is unknown, or "one of two windows with this name"


@dataclass(frozen=True)
class Allowance:
    service: str
    windows: tuple[Window, ...] = ()
    read_at: Optional[float] = None
    problem: str = ""               # why there is no reading at all, in plain words
    old_note: str = ""              # the windows are the LAST KNOWN reading, not a fresh one: its age and why

    def usable(self, now: float) -> bool:
        return bool(self.windows) and self.read_at is not None and 0 <= now - self.read_at <= STALE_SEC


def _number(value: object) -> bool:
    """DLS (dls_core.quotas.number): a real, finite number; a true/false is not one."""
    return type(value) in (int, float) and math.isfinite(value)


def _moment(text: object) -> Optional[float]:
    if not isinstance(text, str) or not text:
        return None
    try:
        when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.utcoffset() is None:                # DLS (quotas.aware): a time with no zone cannot be trusted
        return None
    return when.timestamp()


def parse_usage(service: str, data: object, now: float) -> Allowance:
    """One `hermes usage --json` document -> an Allowance, judged by the DLS rules. Pure."""
    if not isinstance(data, dict):
        return Allowance(service, problem="the usage check gave nothing readable")
    if data.get("unavailable_reason"):
        return Allowance(service, problem=str(data["unavailable_reason"])[:120])
    read_at = _moment(data.get("fetched_at"))
    if read_at is None:
        return Allowance(service, problem="the usage check did not say when it was read")
    if not 0 <= now - read_at <= STALE_SEC:
        return Allowance(service, read_at=read_at, problem="the last reading is more than 15 minutes old")
    rows = [w for w in data.get("windows") or () if isinstance(w, dict)]
    if not rows:
        return Allowance(service, read_at=read_at, problem="the usage check listed no allowance windows")
    names = [str(w.get("label") or "") for w in rows]
    out = []
    for w, name in zip(rows, names):
        used = w.get("used_percent")
        resets = _moment(w.get("resets_at"))
        note = TWIN_NOTE if names.count(name) > 1 else ""
        if not name:
            out.append(Window("(no name)", None, resets, "this window has no name"))
        elif not _number(used) or not 0 <= used <= 100:
            out.append(Window(name, None, resets, "the reading is not a usable percentage"))
        elif resets is not None and resets <= now:
            out.append(Window(name, None, resets, "its reset time is already past, so the reading is stale"))
        else:
            out.append(Window(name, 100.0 - float(used), resets, note))
    return Allowance(service, tuple(out), read_at)


Runner = Callable[[list], tuple]        # the command's words -> (exit code, what it printed)


def _run(cmd: list) -> tuple:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=USAGE_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return done.returncode, done.stdout if done.returncode == 0 else (done.stdout or "") + (done.stderr or "")


def hermes_bin() -> str:
    """Where the Hermes command is (the same look-up the Voice Desk's own watcher uses)."""
    import shutil
    return (os.environ.get("ACD_HERMES_BIN") or os.environ.get("VOICE_DESK_HERMES_BIN") or shutil.which("hermes")
            or str(Path.home() / ".local/bin/hermes"))


class UsageReader:
    """ONE shared reader of the allowance. `cached()` never waits. `refresh()` is the only thing that runs the
    usage check, at most once per service every REFRESH_SEC, and is meant to run off the screen's thread."""

    def __init__(self, runner: Optional[Runner] = _run, clock: Callable[[], float] = time.time,
                 refresh_sec: float = REFRESH_SEC, hermes: Optional[str] = None,
                 keep: Optional[Path] = None) -> None:
        self.runner, self.clock, self.refresh_sec = runner, clock, refresh_sec
        self.hermes = hermes or hermes_bin()
        self._got: dict[str, Allowance] = {}
        self._good: dict[str, Allowance] = {}       # the last reading that had real numbers, per service
        self._tried: dict[str, float] = {}
        self.calls = 0                      # how many checks were really run (a test keeps this low)
        self.keep = keep                    # a small file of the last good readings, so a new desk still has them
        if keep is not None:
            self._good = load_kept(keep)

    def cached(self, now: Optional[float] = None) -> dict:
        """service -> the newest reading. A reading that is too old, or a check that failed after a good one, is
        never shown as fresh and never thrown away: the LAST KNOWN numbers stay, labelled with their age and the
        reason there is nothing newer. Never runs anything."""
        now = self.clock() if now is None else now
        out = {}
        for service in set(self._got) | set(self._good):
            got = self._got.get(service)
            if got is not None and got.usable(now):
                out[service] = got
                continue
            good = self._good.get(service)
            why = ("" if got is None else got.problem if not got.windows
                   else "the last reading is more than 15 minutes old")
            if good is not None and good.windows and good.read_at is not None and good.read_at <= now:
                out[service] = last_known(good, now, why)
            elif got is not None:
                out[service] = got if not got.windows else Allowance(service, read_at=got.read_at, problem=why)
        return out

    def due(self, services: dict) -> list:
        now = self.clock()
        return [s for s in services if s in USAGE_SERVICES and now - self._tried.get(s, 0) >= self.refresh_sec]

    def refresh(self, services: dict) -> int:
        """services: service -> the profile to ask as. Runs the checks that are due. Returns how many ran."""
        if self.runner is None:
            return 0
        ran = 0
        for service in self.due(services):
            now = self.clock()
            self._tried[service] = now
            self.calls += 1
            ran += 1
            code, out = self.runner([self.hermes, "-p", services[service], "usage", "--provider", service, "--json"])
            try:
                data = json.loads(out) if code == 0 else None
            except ValueError:
                data = None
            if data is None:
                self._got[service] = Allowance(service, problem=why_no_reading(out or ""))
            else:
                got = parse_usage(service, data, self.clock())
                self._got[service] = got
                if any(w.left is not None for w in got.windows):
                    self._good[service] = got
                    if self.keep is not None:
                        save_kept(self.keep, self._good)
        return ran


NO_ANSWER = "Hermes got no allowance reading for it this time"


def why_no_reading(said: str) -> str:
    """Why a usage check gave no numbers, in plain words. Hermes prints ONE sentence for three different causes
    ("no credential is configured for it, the provider has no usage endpoint, or the fetch failed"), so that
    sentence alone never proves the sign-in is missing: the desk must not say so."""
    low = said.lower()
    if "no account usage available" in low or "fetch failed" in low:
        return NO_ANSWER + " (Hermes does not say which: no sign-in, no allowance page, or the look-up failed)"
    return "the usage check failed"


def age_words(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = round(minutes / 60)
    if hours < 48:
        return f"{hours} hours ago"
    return f"{round(hours / 24)} days ago"


def last_known(good: Allowance, now: float, why: str) -> Allowance:
    """The last good reading, kept on screen but marked as old: when it was read and why there is nothing newer.
    A window whose reset time has passed since is no longer true, so it reads unknown."""
    windows = tuple(w if w.left is None or w.resets_at is None or w.resets_at > now
                    else Window(w.label, None, w.resets_at, "it has reset since this was read")
                    for w in good.windows)
    note = "last known, read " + age_words(now - (good.read_at or now))
    if why:
        note += "; no newer reading: " + why
    return Allowance(good.service, windows, good.read_at, old_note=note)


def load_kept(path: Path) -> dict:
    """The last good readings a desk before this one saved. A missing or damaged file is simply nothing."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    for service, row in (data.items() if isinstance(data, dict) else ()):
        try:
            windows = tuple(Window(str(w["label"]), float(w["left"]) if w.get("left") is not None else None,
                                   float(w["resets_at"]) if w.get("resets_at") is not None else None,
                                   str(w.get("note") or "")) for w in row["windows"])
            out[str(service)] = Allowance(str(service), windows, float(row["read_at"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_kept(path: Path, good: dict) -> None:
    """Best effort: the readings are a convenience, so a failed save is never an error."""
    body = {s: {"read_at": a.read_at, "windows": [{"label": w.label, "left": w.left, "resets_at": w.resets_at,
                                                  "note": w.note} for w in a.windows]}
            for s, a in good.items() if a.read_at is not None}
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


# ---- one service's health ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class ServiceHealth:
    service: str
    state: str = UNKNOWN            # GOOD / FAILING / UNKNOWN
    kind: str = ""                  # SIGNIN_DEAD / ALLOWANCE_OUT when failing
    when: Optional[float] = None    # the moment of the newest evidence
    model: str = ""                 # for a used-up allowance: the model that was refused
    reset_ts: Optional[float] = None
    answered: Optional[float] = None       # the newest reply this service did answer (any model)
    answered_model: str = ""
    allowance: Optional[Allowance] = None
    needs_peter: bool = False       # only Peter can fix it (a new sign-in)

    @property
    def failing(self) -> bool:
        return self.state == FAILING


def judge(service: str, events: list, now: float, allowance: Optional[Allowance] = None) -> ServiceHealth:
    """One service's health from what the log says. The newest evidence wins."""
    mine = [e for e in events if e.service == service and now - e.ts <= EVIDENCE_SEC]
    answered = max((e for e in mine if e.kind == "answered"), key=lambda e: e.ts, default=None)
    signin = max((e for e in mine if e.kind == "signin"), key=lambda e: e.ts, default=None)
    out = max((e for e in mine if e.kind == "allowance"), key=lambda e: e.ts, default=None)
    a_ts = answered.ts if answered else None
    a_model = answered.model if answered else ""
    if signin and (answered is None or signin.ts > answered.ts):
        return ServiceHealth(service, FAILING, SIGNIN_DEAD, signin.ts, answered=a_ts, answered_model=a_model,
                             allowance=allowance, needs_peter=True)
    if out:
        back = max((e.ts for e in mine if e.kind == "answered" and e.model == out.model), default=None)
        still = out.reset_ts is None or out.reset_ts > now
        if still and (back is None or back < out.ts):
            return ServiceHealth(service, FAILING, ALLOWANCE_OUT, out.ts, out.model, out.reset_ts, a_ts, a_model,
                                 allowance)
    if answered:
        return ServiceHealth(service, GOOD, when=answered.ts, answered=a_ts, answered_model=a_model, allowance=allowance)
    return ServiceHealth(service, allowance=allowance)


def hours_until(ts: Optional[float], now: float) -> Optional[int]:
    if ts is None or ts <= now:
        return None
    return max(1, round((ts - now) / 3600))
