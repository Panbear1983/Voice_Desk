"""Agent Control Desk: the facts. This module only READS (every change is made in acd_edit.py).

For every Hermes agent this module answers, from the files where the truth already lives:

    language model   what is set, what really answered the last reply, the backups in order
    voice            from agents.toml (the Voice Desk master list)
    where it shows   terminal chat, Telegram private chat, Telegram team group, terminal Team Room,
                     voice on the Mac, voice on the iPhone: on / off / unknown, each with its evidence
    who it talks to  the starting rules in acd_rules.toml

Everything here only READS: each agent's settings file, each agent's log, agents.toml, the Team Room members
file and acd_rules.toml. Nothing is written, nothing is restarted, no model is called and no network is used, so
the desk can never become a lifeline: if it stops, every agent and every voice keeps working as last set.

A fact that cannot be read is "unknown". It is never guessed.

The four colours (Peter, 2026-11-16):
    green   live everywhere
    yellow  saved, but waiting on a restart or a fresh chat (an open chat still on an older setting)
    blue    a temporary override (a model picked inside one chat, or pinned on one job)
    red     failed, or changed behind the desk's back (the main model failed and a backup answered)
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import agents as A
import acd_health as H

HOME = Path.home()
REPO = Path(__file__).resolve().parent

GREEN, YELLOW, BLUE, RED, GREY = "green", "yellow", "blue", "red", "grey"
ON, OFF, UNKNOWN = "on", "off", "unknown"

PLACES = (
    ("terminal", "Terminal chat"),
    ("tg_private", "Telegram private chat"),
    ("tg_group", "Telegram team group"),
    ("team_room", "Terminal Team Room"),
    ("mac_voice", "Voice on the Mac"),
    ("iphone_voice", "Voice on the iPhone"),
)
PLACE_NAME = dict(PLACES)

TAIL_BYTES = 2_500_000          # the newest part of a log; older history is not needed
RECENT_SEC = 7 * 24 * 3600      # "it shows up there" = a reply was logged there within this long
OPEN_CHAT_SEC = 24 * 3600       # a chat counts as open if it had a reply within this long
AUTH_NEAR_SEC = 8               # a sign-in failure this close before a reply belongs to that reply
TEAM_ROOM_PLUGIN = "team-room-telegram"
DLS_PLUGIN = "dls-observer"

# Plain names. Anything not listed is shown as it is written in the settings file.
MODEL_NAMES = {
    "claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5", "claude-fable-5-2": "Claude Fable 5.2",
    "gemini-3.8-flash": "Gemini 3.8 Flash", "gemini-4-argon": "Gemini 4 Argon",
    "x-ai/grok-4.20": "Grok 4.20", "gpt-5.4-mini": "GPT 5.4 mini",
}
SERVICE_NAMES = {
    "anthropic": "the Claude subscription", "antigravity-subscription-directsdk": "Antigravity",
    "nvidia": "NVIDIA", "openai-codex": "the ChatGPT subscription", "openrouter": "OpenRouter",
}
SERVICE_SHORT = {
    "anthropic": "Claude sub.", "antigravity-subscription-directsdk": "Antigravity", "nvidia": "NVIDIA",
    "openai-codex": "ChatGPT sub.", "openrouter": "OpenRouter",
}
PLATFORM_NAMES = {"cli": "terminal chat", "local": "terminal chat", "telegram": "Telegram", "bot_room": "Team Room",
                  "kanban": "job card", "cron": "scheduled job"}


def model_name(model: Optional[str]) -> str:
    if not model:
        return "unknown"
    if model in MODEL_NAMES:
        return MODEL_NAMES[model]
    if model.startswith("nvidia/"):                # nvidia/nemotron-3.5-lightning-30b-a3b -> NVIDIA Nemotron 3.5 Lightning
        words = re.sub(r"(-\d+b)?(-a\d+b)?$", "", model.split("/", 1)[1]).split("-")
        return "NVIDIA " + " ".join(w.capitalize() for w in words)
    return model


SERVICE_TINY = {"anthropic": "Claude", "antigravity-subscription-directsdk": "Antigravity", "nvidia": "NVIDIA",
                "openai-codex": "ChatGPT", "openrouter": "OpenRouter"}


def short_model(model: Optional[str]) -> str:
    """A model's name cut down for a table cell: "Opus 5.5", "Sonnet 5.5", "Gemini Flash", "Nemotron 3.5". The
    maker's name and the small print are left to the strip under the table, which has the full name."""
    name = model_name(model)
    for brand in ("Claude ", "NVIDIA "):
        if name.startswith(brand):
            name = name[len(brand):]
    name = re.sub(r"^Gemini [\d.]+ Flash$", "Gemini Flash", name)
    name = re.sub(r"^(Nemotron [\d.]+) Lightning$", r"\1", name)
    return name


def model_family(model: Optional[str]) -> str:
    """The one word a person calls it by: "Opus", "Sonnet", "Gemini", "Nemotron", "GPT", "Grok"."""
    return short_model(model).split(" ")[0]


def service_name(service: Optional[str], short: bool = False) -> str:
    if not service:
        return "unknown"
    return (SERVICE_SHORT if short else SERVICE_NAMES).get(service, service)


def voice_label(voice: Optional[str]) -> str:
    """A voice's short name: am_puck -> "Puck, local"; en-US-AvaNeural -> "Ava"; alloy -> "Alloy"."""
    if not voice:
        return "none"
    if re.fullmatch(r"[a-z][fm]_[a-z]+", voice):
        return voice.split("_", 1)[1].capitalize() + ", local"
    m = re.fullmatch(r"[a-z]{2}-[A-Z]{2}-(\w+?)(?:Multilingual)?Neural", voice)
    if m:
        return m.group(1)
    return voice.capitalize() if voice.islower() else voice


def plain_reason(reason: str) -> str:
    """The log's failure text in a few plain words. Anything not recognised is shown as written, cut short."""
    if not reason:
        return ""
    m = re.search(r"quota reached.*?Resets in (\d+)h", reason, re.I)
    if m:
        return f"its allowance is used up, resets in about {m.group(1)} hours"
    if re.search(r"quota reached|rate.?limit|\b429\b", reason, re.I):
        return "its allowance is used up or it is being slowed down"
    if re.search(r"No \w+ credentials found|invalid_grant|not configured", reason, re.I):
        return "no sign-in found for it"
    if re.search(r"overloaded|\b529\b|\b503\b", reason, re.I):
        return "the service was overloaded"
    if re.search(r"timed? ?out", reason, re.I):
        return "it took too long to answer"
    return reason if len(reason) <= 90 else reason[:87].rstrip() + "..."


# ---- where everything is read from ---------------------------------------------------------------------
@dataclass(frozen=True)
class Paths:
    """Every file the desk reads. Tests build one that points at a throw-away folder."""
    profiles: Path
    agents_toml: Path
    members: Path                   # the Team Room's saved members
    rules: Path
    shortcuts: Path                 # the folder that holds the "Talk to <agent>.shortcut" files (names only)
    gateway_state: Optional[Path] = None   # the agents' own status file: which Telegram bots are connected
    team_room_code: Optional[Path] = None  # the Team Room's own program, read only to see if it follows the desk's rules
    grid: Any = None                       # the Voice Desk grid's own files (gridstate.Paths): where a voice speaks
    lights: Optional[Callable] = None      # the Voice Desk's health strip (syshealth.collect), read-only
    mounts: Optional[Callable] = None      # () -> target -> acd_mount.MountView: the session voice goes to (read-only)

    @classmethod
    def default(cls) -> "Paths":
        env = os.environ.get
        return cls(
            profiles=Path(env("HERMES_PROFILES_DIR", HOME / ".hermes/profiles")),
            agents_toml=Path(env("VOICE_DESK_AGENTS", HOME / "GitHub/Agent_Control_Desk/agents.toml")),
            members=Path(env("ACD_TEAM_ROOM_MEMBERS", HOME / "GitHub/Team_Room/members.json")),
            rules=Path(env("ACD_RULES", REPO / "acd_rules.toml")),
            shortcuts=Path(env("ACD_SHORTCUTS_DIR", HOME / "GitHub/Agent_Control_Desk")),
            gateway_state=Path(env("ACD_GATEWAY_STATE", HOME / ".hermes/gateway_state.json")),
            team_room_code=Path(env("ACD_TEAM_ROOM_CODE", HOME / "GitHub/Team_Room/team_room.py")),
            grid=_grid_default(), lights=_lights_default(), mounts=_mounts_default())


def _mounts_default() -> Optional[Callable]:
    """Which session each of Claude, Gemini and Codex is mounted on, and whether its window is open: the voice
    line's own pins (~/.voice_desk) and ONE read-only `herdr pane list`."""
    try:
        import settings as S
        pins, herdr = Path(os.environ.get("ACD_PINS", S.STATE_DIR)), S.HERDR_BIN
    except Exception:                               # noqa: BLE001  the desk still opens without the voice line
        return None

    def read() -> dict:
        import acd_mount
        return acd_mount.views(pins, herdr)
    return read


def _grid_default() -> Any:
    try:
        import gridstate
        return gridstate.Paths.default()
    except Exception:                               # noqa: BLE001  the desk still opens without the voice grid
        return None


def _lights_default() -> Optional[Callable]:
    try:
        import syshealth
        return syshealth.collect
    except Exception:                               # noqa: BLE001
        return None


# ---- an agent's log ------------------------------------------------------------------------------------
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \w+ (?:\[([^\]]+)\] )?[\w.]+: (.*)$")
_TURN = re.compile(r"^conversation turn: session=(\S+) model=(\S+) provider=(\S+) platform=(\S+) history=(\d+)")
_FALLBACK = re.compile(r"^Fallback activated: (\S+) → (\S+) \((\S+)\)")
_FALLBACK_EMPTY = re.compile(r"^Fallback activated after empty responses: now using (\S+) on (\S+)")
_FAILED = re.compile(r"^API call failed .*?\bprovider=(\S+) .*?\bmodel=(\S+) summary=(.*)$")
_AUTH = re.compile(r"^Primary provider auth failed \((.*)\)\. Falling back to (\S+)")


@dataclass
class Turn:
    """One reply, as the log tells it."""
    ts: float
    session: str
    platform: str
    history: int
    start_model: str
    start_service: str
    end_model: str = ""
    end_service: str = ""
    drops: int = 0                      # how many times it dropped to a backup during this reply
    reason: str = ""                    # why the model it began on failed, in the log's own words
    signin_failed: str = ""             # the main service could not sign in just before this reply

    def __post_init__(self) -> None:
        self.end_model = self.end_model or self.start_model
        self.end_service = self.end_service or self.start_service

    @property
    def dropped(self) -> bool:
        return self.drops > 0


def _stamp(text: str) -> float:
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def parse_log(text: str) -> list[Turn]:
    """Every reply in a piece of an agent's log, oldest first. Pure: text in, replies out."""
    turns: list[Turn] = []
    current: dict[str, Turn] = {}              # session -> the reply it is in the middle of
    last_failure: dict[str, str] = {}          # session -> the newest "API call failed" summary
    signin: Optional[tuple[float, str]] = None  # the newest sign-in failure (it carries no session)
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        when, session, body = m.group(1), m.group(2), m.group(3)
        if session is None:
            a = _AUTH.match(body)
            if a:
                signin = (_stamp(when), a.group(1))
            continue
        t = _TURN.match(body)
        if t:
            ts = _stamp(when)
            turn = Turn(ts, t.group(1), t.group(4), int(t.group(5)), t.group(2), t.group(3))
            if signin and 0 <= ts - signin[0] <= AUTH_NEAR_SEC:
                turn.signin_failed = signin[1]
            turns.append(turn)
            current[session] = turn
            last_failure.pop(session, None)
            continue
        turn = current.get(session)
        if turn is None:
            continue
        f = _FAILED.match(body)
        if f:
            last_failure[session] = f.group(3).strip()
            continue
        fb = _FALLBACK.match(body)
        if fb:
            turn.end_model, turn.end_service, turn.drops = fb.group(2), fb.group(3), turn.drops + 1
            turn.reason = turn.reason or last_failure.get(session, "")
            continue
        fe = _FALLBACK_EMPTY.match(body)
        if fe:
            turn.end_model, turn.end_service, turn.drops = fe.group(1), fe.group(2), turn.drops + 1
            turn.reason = turn.reason or "the model answered with nothing"
    return turns


class LogReader:
    """ONE shared reader for every agent. A log is read again only when its size or time changed."""

    def __init__(self, profiles: Path, tail_bytes: int = TAIL_BYTES) -> None:
        self.profiles = Path(profiles)
        self.tail_bytes = tail_bytes
        self._cache: dict[Path, tuple[tuple, list[Turn]]] = {}
        self._events: dict[Path, list] = {}
        self.reads = 0                     # how many files were really read (a test checks this stays low)
        self.grid_reader: Any = None       # the Voice Desk grid's own shared reader, made on first use

    def turns(self, profile: str) -> Optional[list[Turn]]:
        """The replies in the agent's current log, or None when there is no log to read."""
        path = self.profiles / profile / "logs" / "agent.log"
        try:
            st = path.stat()
        except OSError:
            return None
        sig = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(path)
        if hit and hit[0] == sig:
            return hit[1]
        self.reads += 1
        try:
            with open(path, "rb") as fh:
                if st.st_size > self.tail_bytes:
                    fh.seek(st.st_size - self.tail_bytes)
                    fh.readline()                      # drop the half line
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        got = parse_log(text)
        self._cache[path] = (sig, got)
        self._events[path] = H.parse_events(text)       # the same read: what it says about each service
        return got

    def events(self, profile: str) -> list:
        """What the agent's log says about each service (sign-in, allowance, answered). Same single read."""
        self.turns(profile)
        return self._events.get(self.profiles / profile / "logs" / "agent.log", [])


# ---- what the desk shows ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelSet:
    model: str
    service: str
    effort: str


@dataclass(frozen=True)
class Backup:
    position: int
    model: str
    service: str
    on: bool = True                 # listed in the settings file = on. Off = remembered in the desk's own record
    note: str = ""                  # for an off backup: since when, or "not on this agent's list yet"


@dataclass(frozen=True)
class LastReply:
    when: float
    where: str                      # the log's platform word: cli, telegram, bot_room, kanban, cron ...
    began_model: str
    began_service: str
    answered_model: str
    answered_service: str
    dropped: bool
    reason: str
    status: str
    note: str                       # one plain sentence that explains the colour


@dataclass(frozen=True)
class Voice:
    english: Optional[str]
    chinese: Optional[str]
    speed: Optional[float]
    english_only: bool
    silent_if_fails: bool = True    # 2026-11-13: no agent has a stand-in voice; a failed voice stays silent
    not_playable: str = ""          # 2026-11-18: the English voice is one its own script cannot make yet: why


def not_playable(a: A.Agent) -> str:
    """Why the agent's own speaking script cannot make its English voice yet ("" = it can). Only a voice set behind
    the desk's back can be in this state: the pick-lists never offer one (voicescripts.py, voicepick.py)."""
    try:
        import voicescripts
        return voicescripts.speaker_problem(a, "voice", a.voice or "")
    except Exception:                                   # noqa: BLE001  a look must never take the desk down
        return ""


@dataclass(frozen=True)
class Presence:
    key: str
    state: str                      # ON / OFF / UNKNOWN
    evidence: str                   # where that answer came from, in plain words
    status: str = GREY
    can_switch: bool = False        # a clean switch that can be put back exists today (version three)
    why_not: str = ""               # when it cannot be switched from the desk: why, in plain words


# ---- is each service working (version four) ----------------------------------------------------------------
@dataclass(frozen=True)
class ServiceView:
    """One service an agent uses: can it sign in, is its allowance used up, how much is left."""
    service: str
    state: str                      # H.GOOD / H.FAILING / H.UNKNOWN
    status: str                     # the colour
    words: str                      # one plain sentence
    kind: str = ""                  # H.SIGNIN_DEAD / H.ALLOWANCE_OUT
    needs_peter: bool = False
    allowance: tuple = ()           # plain lines: "Weekly: 25% left, resets Thu 19 Nov 06:35"
    allowance_note: str = ""        # why there is no reading, when there is none
    main: bool = False              # the agent's main service (not a backup's)
    out_model: str = ""             # for a used-up allowance: the model that is refused
    reset_ts: Optional[float] = None


def backup_trouble(health: tuple, b: "Backup", now: Optional[float] = None) -> str:
    """Why a backup that is switched on cannot answer right now, in a few plain words ("" = no known reason).
    "on" is the setting; this is the state beside it, so the two never contradict each other in silence."""
    if not b.on:
        return ""
    for h in health:
        if h.service != b.service:
            continue
        if h.kind == H.SIGNIN_DEAD:
            return "sign-in dead"
        if h.kind == H.ALLOWANCE_OUT and h.out_model == b.model:
            if h.reset_ts and (now is None or h.reset_ts > now):
                return "allowance used up until " + dt.datetime.fromtimestamp(h.reset_ts).strftime("%a %-d %b")
            return "allowance used up"
    return ""


def _day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%a %-d %b %H:%M")


def allowance_lines(a: Optional[H.Allowance]) -> tuple[tuple, str]:
    """(the windows as plain lines, why there is no reading)."""
    if a is None:
        return (), "not read yet"
    if not a.windows:
        return (), a.problem or "unknown"
    out = []
    for w in a.windows:
        if w.left is None:
            out.append(f"{w.label}: unknown ({w.note})")
            continue
        resets = f", resets {_day(w.resets_at)}" if w.resets_at else ""
        note = f" ({w.note})" if w.note else ""
        out.append(f"{w.label}: {w.left:.0f}% left{resets}{note}")
    if a.old_note:                                  # not a fresh reading: say so first, then the last known numbers
        out.insert(0, a.old_note)
    return tuple(out), ""


def service_view(h: H.ServiceHealth, now: float, main: bool = False) -> ServiceView:
    name = service_name(h.service)
    name = name[0].upper() + name[1:]
    lines, why = allowance_lines(h.allowance)
    if h.kind == H.SIGNIN_DEAD:
        last = f" Its last answer was {_when(h.answered)}." if h.answered else ""
        words = (f"{name}: the sign-in is dead (seen {_when(h.when)}). Hermes cannot sign in, so nothing reaches it; "
                 f"only Peter signing in again fixes it.{last}")
        return ServiceView(h.service, h.state, RED, words, h.kind, True, lines, why, main)
    if h.kind == H.ALLOWANCE_OUT:
        hrs = H.hours_until(h.reset_ts, now)
        resets = f", resets in about {hrs} hours ({_day(h.reset_ts)})" if hrs else ", reset time not given"
        other = ""
        if h.answered and h.answered_model and h.answered_model != h.model:
            other = f" {model_name(h.answered_model)} there still answered {_when(h.answered)}."
        words = (f"{name}: the allowance for {model_name(h.model)} is used up{resets} "
                 f"(seen {_when(h.when)}). The sign-in itself works.{other}")
        return ServiceView(h.service, h.state, RED, words, h.kind, False, lines, why, main, h.model, h.reset_ts)
    if h.state == H.GOOD:
        return ServiceView(h.service, h.state, GREEN,
                           f"{name}: working, {model_name(h.answered_model)} answered {_when(h.answered)}",
                           allowance=lines, allowance_note=why, main=main)
    return ServiceView(h.service, h.state, GREY, f"{name}: unknown, the log holds no reply or failure for it",
                       allowance=lines, allowance_note=why, main=main)


def services_of(ms: Optional["ModelSet"], listed: tuple) -> list[str]:
    out = [ms.service] if ms and ms.service else []
    for b in listed:
        if b.on and b.service and b.service not in out:
            out.append(b.service)
    return out


# ---- where a voice speaks (version four: the old Voice Desk grid's switches) --------------------------------
VOICE_PLACE_NAME = {"terminal": "In its terminal window", "tg_dm": "Telegram private chat",
                    "tg_group": "Telegram team group", "mic": "Voice Desk mic window / phone",
                    "iphone": "iPhone (Siri)"}
_EVIDENCE_COLOUR = {"ok": GREEN, "idle": GREY, "warn": YELLOW, "bad": RED, "na": GREY}


def place_words(place: str) -> str:
    """A place's name for the middle of a sentence ("in its terminal window", "Telegram team group")."""
    name = VOICE_PLACE_NAME.get(place, place)
    return name[0].lower() + name[1:] if name.startswith("In ") else name


@dataclass(frozen=True)
class VoicePlace:
    """Spoken replies at one place: the setting, what the logs say really happened, and whether it can be switched."""
    key: str
    value: str                      # Off / Voice-back / Always / "—" / "?"
    note: str                       # where the setting was read from, in plain words
    status: str = GREY              # what the logs say: green spoke, grey idle, yellow mixed, red mismatch
    evidence: str = ""              # the short line behind the colour
    can_switch: bool = False
    why_not: str = ""
    problem: str = ""
    set_by: str = ""                # a Hermes terminal: "desk" (its terminal_voice.json says so) or "default" (no file)


_SETTING_NAME = re.compile(r"\s*\((?:[a-z_]+\.)+[a-z_]+\)")        # "(voice.auto_enable)": a setting's name


def plain_why(why: str) -> str:
    """The Voice Desk grid's own reason, in ordinary words: no setting names."""
    if "decides per window" in why:
        return ("each open Hermes window keeps its own choice until it is closed, so the desk cannot switch it "
                "from here")
    return _SETTING_NAME.sub("", why)


@dataclass(frozen=True)
class Wire:
    """One place of the Voice Desk list's own "here / not here" marks and activation phrases (the old dashboard's
    X and P keys). Both are marks the list keeps and the desk shows: neither switches anything."""
    place: str                      # terminal / tg_dm / tg_group / mic / iphone
    here: bool                      # is the agent marked here at this place
    here_stored: bool               # True = the list says so itself; False = it follows what is wired today
    phrase: str                     # the activation phrase shown (the stored one, else today's)
    phrase_stored: bool
    wired: bool = True              # False = marked here, but nothing makes it speak there yet
    hint: str = ""                  # what an unwired place would need, in plain words


WIRE_OF = {"terminal": "terminal", "tg_private": "tg_dm", "tg_group": "tg_group", "mac_voice": "mic",
           "iphone_voice": "iphone"}       # a row of WHERE IT SHOWS UP -> the Voice Desk list's place


def voice_places(paths: "Paths", agents_cfg: Optional[dict], reader: "LogReader", now: float) -> dict:
    """agent key -> the VoicePlace lines, read with the Voice Desk grid's own code (same files, same reader rules).
    Empty when the grid's files are not known (tests that do not care)."""
    return grid_view(paths, agents_cfg, reader, now)[0]


def grid_view(paths: "Paths", agents_cfg: Optional[dict], reader: "LogReader", now: float) -> tuple[dict, dict]:
    """(agent key -> the VoicePlace lines, agent key -> the Wire marks), from ONE read of the Voice Desk grid."""
    if paths.grid is None or agents_cfg is None:
        return {}, {}
    try:
        import gridevidence
        import gridstate as G
        import onoff
        if reader.grid_reader is None:
            reader.grid_reader = gridevidence.LogReader(paths.grid.profiles)
        rows = gridevidence.build(agents_cfg, paths.grid, reader.grid_reader, now)
    except Exception:                               # noqa: BLE001  never let the voice grid take the desk down
        return {}, {}
    out: dict = {}
    wiring: dict = {}
    for row in rows:
        lines = []
        stored = isinstance(row.agent.raw.get("here"), list)
        marks = []
        for place in G.PLACE_KEYS:
            try:
                here = row.is_here(place)
                phrase, kept = row.phrase(place)
                unwired = G.unwired(row.cells[place], place)
                marks.append(Wire(place, here, stored, phrase, kept, not unwired,
                                  G.wiring_hint(row.agent, place) if unwired else ""))
            except Exception:                       # noqa: BLE001
                continue
        wiring[row.agent.key] = tuple(marks)
        for place in G.PLACE_KEYS:
            cell, ev = row.cells[place], row.evidence.get(place)
            if cell.value == G.NA:
                continue
            try:
                menu = onoff.menu(row.agent, place, cell, paths.grid)
                can, why = bool(menu.options), plain_why(menu.why_not)
            except Exception:                       # noqa: BLE001
                can, why = False, "the switch could not be read"
            status = RED if cell.problem else _EVIDENCE_COLOUR.get(ev.status if ev else "na", GREY)
            set_by = ""
            if place == "terminal" and G.kind_of(row.agent) == G.HERMES and cell.value in (G.OFF, G.ALWAYS):
                try:        # the same file the windows read: set by the desk, or nothing set (the window's default)
                    got = G.read_terminal_switch(G.hermes_terminal_switch(paths.grid, str(row.agent.profile)))
                    set_by = "desk" if got in ("on", "off") else "default" if got is None else ""
                except Exception:                   # noqa: BLE001
                    set_by = ""
            lines.append(VoicePlace(place, cell.label, cell.note, status, ev.headline if ev else "", can, why,
                                    cell.problem, set_by))
        out[row.agent.key] = tuple(lines)
    return out, wiring


# ---- who may talk to whom (version three) ---------------------------------------------------------------
WORKS_TODAY = "how the room works today"
ENFORCED = "enforced by the Team Room"
TOLD = "told to the agents in the room's notes, not enforced"
RECORDED = "recorded, not yet enforced"
ADAPTER_MARK = "ACD_RULES_ADAPTER"     # the line the Team Room carries once it reads the desk's rules


@dataclass(frozen=True)
class TalkRule:
    key: str                        # speaks / hands_out / takes_from / answers
    label: str
    answer: str                     # the plain answer shown
    token: str                      # the choice as the pop-up names it
    standing: str                   # WORKS_TODAY / ENFORCED / TOLD / RECORDED
    locked: str = ""                # why this rule cannot be changed for this agent, if it cannot


@dataclass(frozen=True)
class Override:
    """A temporary choice the desk leaves alone and shows (blue)."""
    model: str
    service: str
    where: str
    when: float


@dataclass(frozen=True)
class DeskRecord:
    """What the desk itself remembers (acd_edit.py reads it from the desk's own folder). All of it is optional:
    with no record the desk shows exactly what the files say."""
    off: dict = field(default_factory=dict)        # profile -> [{"provider", "model", "position", "note"}, ...]
    pending: dict = field(default_factory=dict)    # profile -> {"ts", "model", "service"}: the desk's last model change
    waiting: tuple = ()                            # saved changes that wait for a restart, in plain words
    armed: bool = False                            # False = every change is a dry run: shown, never written
    last_change: str = ""                          # the newest change, in plain words (what U would undo)


@dataclass(frozen=True)
class AgentView:
    profile: str
    name: str
    role: str = ""
    error: str = ""                                   # the settings file could not be read: say so, show nothing else
    model: Optional[ModelSet] = None
    model_locked: bool = False
    last: Optional[LastReply] = None
    drops_today: int = 0
    old_chats: int = 0                                # open chats still on an older setting
    signin_failed: str = ""                           # the newest sign-in failure for the main service, if recent
    override: Optional[Override] = None
    backups: tuple[Backup, ...] = ()
    voice: Optional[Voice] = None
    presence: tuple[Presence, ...] = ()
    talks: tuple[tuple[str, str], ...] = ()           # (what, answer) lines
    addons: tuple[str, ...] = ()
    voice_key: str = ""                               # this agent's name in the Voice Desk list ([agents.<key>])
    model_note: str = ""                              # a plain sentence about a note on the model (meant to stay fixed)
    model_pending: str = ""                           # the desk changed the model; why it is not live everywhere yet
    model_behind: str = ""                            # the settings file no longer says what the desk last set
    backups_locked: bool = False
    talk_rules: tuple = ()                            # TalkRule lines: the same rules, with where each one stands
    room_lead: bool = False                           # leads the Team Room (cannot be taken out of it)
    in_room: Optional[bool] = None                    # a saved member of the Team Room (None = could not be read)
    health: tuple = ()                                # ServiceView lines: sign-in and allowance of each service it uses
    drifts: tuple = ()                                # acd_watch.Drift: settings changed behind the desk's back
    voice_places: tuple = ()                          # VoicePlace lines: spoken replies, place by place
    effect: Optional[str] = None                      # a voice effect, for the one voice that has one (Simba)
    wiring: tuple = ()                                # Wire marks: here / not here and the phrase, place by place

    @property
    def model_status(self) -> str:
        if self.error or self.model_behind:
            return RED
        if self.model_pending:
            return YELLOW
        return GREEN if self.model else GREY

    @property
    def worst(self) -> str:
        """The colour of the row's marker: the most serious thing on it."""
        seen = {self.model_status, self.last.status if self.last else GREY}
        if self.drifts or any(h.status == RED and h.main for h in self.health):
            seen.add(RED)
        if self.old_chats:
            seen.add(YELLOW)
        if self.override:
            seen.add(BLUE)
        for colour in (RED, YELLOW, BLUE, GREEN):
            if colour in seen:
                return colour
        return GREY


# ---- the fallback ladder: one order for every agent (Peter, 2026-11-17) ---------------------------------------
AGY = "antigravity-subscription-directsdk"
MAIN_RUNG = "main"
EXTRA_RUNG = "extra"
# (key, the row's name, the service). Rung 2 has two lines: Claude on Antigravity first, Gemini there second.
LADDER = (
    ("ag_claude", "2a Antigravity: Claude", AGY),
    ("ag_gemini", "2b Antigravity: Gemini", AGY),
    ("codex", "3  ChatGPT (Codex)", "openai-codex"),
    ("nvidia", "4  NVIDIA Nemotron", "nvidia"),
)
LADDER_KEYS = tuple(k for k, _, _ in LADDER)
RUNG_LABEL = {MAIN_RUNG: "1  Main model", EXTRA_RUNG: "Extra, not on the ladder", **{k: label for k, label, _ in LADDER}}
RUNG_WORDS = {MAIN_RUNG: "rung 1", "ag_claude": "rung 2a", "ag_gemini": "rung 2b", "codex": "rung 3", "nvidia": "rung 4",
              EXTRA_RUNG: "an extra backup that is not on the ladder"}
NOT_STANDARD = "not in the standard order"


def rung_of(service: str, model: str) -> str:
    """Which rung of the ladder a backup sits on; "" when it is not on the ladder at all."""
    if service == AGY:
        low = (model or "").lower()
        return "ag_claude" if low.startswith("claude") else "ag_gemini" if low.startswith("gemini") else ""
    return {"openai-codex": "codex", "nvidia": "nvidia"}.get(service, "")


@dataclass(frozen=True)
class Rung:
    """One rung of one agent's ladder."""
    key: str                        # main / ag_claude / ag_gemini / codex / nvidia
    backup: Optional[Backup] = None  # the entry on this rung: listed (on) or remembered (off); None = nothing
    status: str = GREY
    trouble: str = ""               # "allowance used up until Fri 20 Nov": it is on, but cannot answer
    answered: bool = False          # the last reply came from this rung
    out_of_order: bool = False      # it is on the list, but not where the standard order puts it

    @property
    def on(self) -> bool:
        return self.backup is not None and self.backup.on


@dataclass(frozen=True)
class Ladder:
    rungs: tuple = ()               # the four backup rungs, in ladder order (rung 1 is the main model)
    extras: tuple = ()              # listed backups that are not on the ladder (or a second one on a rung)
    extras_off: tuple = ()          # remembered-off backups that are not on the ladder
    in_order: bool = True
    order_note: str = ""            # why it is not in the standard order, in plain words
    answered: str = ""              # the rung key that answered the last reply ("" = unknown)
    below: str = ""                 # when the last reply came from below rung 1: "rung 2 (Gemini 3.8 Flash on Antigravity)"

    def rung(self, key: str) -> Optional[Rung]:
        return next((r for r in self.rungs if r.key == key), None)


def ladder(v: "AgentView") -> Ladder:
    """One agent's backups laid on the standard ladder. Read-only and truthful: a list that is not in ladder
    order is shown as it is and flagged, never silently reordered."""
    listed = [b for b in v.backups if b.on]
    off = [b for b in v.backups if not b.on]
    on_rung: dict = {}
    extras: list = []
    for b in listed:
        key = rung_of(b.service, b.model)
        if key and key not in on_rung:
            on_rung[key] = b
        else:
            extras.append(b)
    off_rung: dict = {}
    extras_off: list = []
    for b in off:
        key = rung_of(b.service, b.model)
        if key:
            off_rung.setdefault(key, b)
        else:
            extras_off.append(b)
    # the order the settings file really has, against the standard one
    seen = [rung_of(b.service, b.model) for b in listed if on_rung.get(rung_of(b.service, b.model)) is b]
    want = [k for k in LADDER_KEYS if k in on_rung]
    misplaced = {k for i, k in enumerate(seen) if i >= len(want) or want[i] != k}
    first_extra = next((i for i, b in enumerate(listed) if b in extras), None)
    last_rung = max((i for i, b in enumerate(listed) if b not in extras), default=-1)
    extra_inside = first_extra is not None and first_extra < last_rung
    notes = []
    if misplaced:
        notes.append("the file tries " + ", then ".join(_rung_short(on_rung[k]) for k in seen)
                     + "; the standard order is " + ", then ".join(_rung_short(on_rung[k]) for k in want))
    if extra_inside:
        notes.append("an extra backup sits between the rungs")
    answered = ""
    below = ""
    if v.last and v.model:
        r = v.last
        got = (r.answered_service, r.answered_model)
        if got == (v.model.service, v.model.model):
            answered = MAIN_RUNG
        elif any((b.service, b.model) == got for b in extras):
            answered = EXTRA_RUNG
        elif r.answered_service == v.model.service:
            answered = MAIN_RUNG
        else:
            answered = rung_of(r.answered_service, r.answered_model) or EXTRA_RUNG
        if answered != MAIN_RUNG and r.status != BLUE:
            below = (f"{RUNG_WORDS[answered]} ({model_name(r.answered_model)} on "
                     f"{service_name(r.answered_service)})")
    rungs = []
    for key in LADDER_KEYS:
        b = on_rung.get(key) or off_rung.get(key)
        if b is None:
            rungs.append(Rung(key, answered=answered == key))
            continue
        trouble = backup_trouble(v.health, b) if b.on else ""
        status = GREY if not b.on else RED if trouble else YELLOW if key in misplaced else GREEN
        rungs.append(Rung(key, b, status, trouble, answered == key, b.on and key in misplaced))
    return Ladder(tuple(rungs), tuple(extras), tuple(extras_off), not misplaced and not extra_inside,
                  NOT_STANDARD + ": " + "; ".join(notes) if notes else "", answered, below)


def _rung_short(b: Backup) -> str:
    return f"{model_name(b.model)} on {service_name(b.service, short=True)}"


@dataclass(frozen=True)
class OtherAssistant:
    """Claude, Gemini, Codex: called by name through the Voice Desk. Voice and call names only."""
    name: str
    english: Optional[str]
    chinese: Optional[str]
    call_names: tuple[str, ...]
    key: str = ""                   # its name in the Voice Desk list ([agents.<key>])
    takes_effect: str = ""          # where a voice change lands, in plain words
    voice_places: tuple = ()        # VoicePlace lines (Claude's and Gemini's own terminal voice, the mic window)
    wiring: tuple = ()              # Wire marks: here / not here and the phrase, place by place
    not_playable: str = ""          # its English voice is one its own script cannot make yet: why (voicescripts.py)
    mount: Any = None               # acd_mount.MountView: the one terminal session voice goes to (None = not read)


@dataclass(frozen=True)
class Warning:
    status: str
    text: str


@dataclass(frozen=True)
class Snapshot:
    agents: tuple[AgentView, ...]
    others: tuple[OtherAssistant, ...]
    warnings: tuple[Warning, ...]
    room_note: str
    read_at: float
    problems: tuple[str, ...] = ()                    # files the desk itself could not read
    waiting: tuple[str, ...] = ()                     # saved changes that wait for a restart
    armed: bool = False                               # False = changes are dry runs
    last_change: str = ""
    room_follows_rules: bool = False                  # the Team Room has the small adapter that reads the desk's rules
    watch_mode: str = ""                              # the watcher: reports only / automatic put-back / paused
    watch_paused: bool = False
    watch_notes: tuple = ()                           # the newest lines of the watcher's own log
    lights: tuple = ()                                # the Voice Desk's health strip: (ok, label, detail)
    allowance_read: bool = False                      # the usage check has been run at least once


# ---- small readers ---------------------------------------------------------------------------------------
def hermes_yaml(path: Path) -> Any:
    """A settings file exactly as Hermes' own loader sees it (raises ValueError when Hermes cannot parse it)."""
    import voicecfg
    return voicecfg.hermes_readback(path)


def load_rules(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def rules_for(rules: dict, profile: str) -> dict:
    return {**rules.get("defaults", {}), **rules.get("agents", {}).get(profile, {})}


def discover_profiles(profiles: Path, rules: dict) -> list[str]:
    """Every agent: a profile folder with a settings file, in the rules file's order, then anything new by name.
    A new profile becomes a row by itself."""
    ignore = set(rules.get("ignore", ()))
    try:
        found = sorted(p.name for p in Path(profiles).iterdir()
                       if p.is_dir() and p.name not in ignore and (p / "config.yaml").is_file())
    except OSError:
        return []
    order = [p for p in rules.get("order", ()) if p in found]
    return order + [p for p in found if p not in order]


def read_members(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("members"), list) else None


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


# ---- the pieces of one agent -----------------------------------------------------------------------------
def model_set(cfg: dict) -> Optional[ModelSet]:
    m = _as_dict(cfg.get("model"))
    if not m.get("default"):
        return None
    effort = _as_dict(cfg.get("agent")).get("reasoning_effort")
    return ModelSet(str(m["default"]), str(m.get("provider") or ""), str(effort) if effort is not None else "unknown")


def backups(cfg: dict) -> tuple[Backup, ...]:
    out = []
    for entry in cfg.get("fallback_providers") or ():
        if isinstance(entry, dict) and entry.get("model"):
            out.append(Backup(len(out) + 1, str(entry["model"]), str(entry.get("provider") or "")))
    return tuple(out)


def addons(cfg: dict) -> tuple[str, ...]:
    enabled = _as_dict(cfg.get("plugins")).get("enabled")
    return tuple(str(x) for x in enabled) if isinstance(enabled, list) else ()


def _is_backup(turn_model: str, turn_service: str, listed: tuple[Backup, ...]) -> bool:
    return any(b.model == turn_model and b.service == turn_service for b in listed)


def judge_reply(turn: Turn, ms: ModelSet, listed: tuple[Backup, ...]) -> tuple[str, str]:
    """(colour, one plain sentence) for a reply, measured against what is set."""
    began = f"{model_name(turn.start_model)} through {service_name(turn.start_service)}"
    if turn.dropped:
        why = f" ({plain_reason(turn.reason)})" if turn.reason else ""
        return RED, (f"began on {began}, it failed{why}, and "
                     f"{model_name(turn.end_model)} answered")
    if turn.start_model == ms.model and turn.start_service == ms.service:
        return GREEN, "answered by the model that is set"
    if turn.signin_failed:
        return RED, (f"{service_name(ms.service)} could not sign in ({plain_reason(turn.signin_failed)}), "
                     f"so the reply began on {began}")
    if turn.platform == "kanban":
        return BLUE, f"a job card ran on {began}, not on the model that is set"
    if turn.start_service == ms.service:
        return BLUE, f"this chat is using {model_name(turn.start_model)}, picked for this chat only"
    if _is_backup(turn.start_model, turn.start_service, listed) or turn.history > 0:
        return YELLOW, (f"this chat is still on {began}, an older setting. "
                        "A fresh chat would use the current one")
    return YELLOW, f"this chat began on {began}, not on what is set"


def last_reply(turns: Optional[list[Turn]], ms: Optional[ModelSet], listed: tuple[Backup, ...]) -> Optional[LastReply]:
    if not turns or ms is None:
        return None
    t = turns[-1]
    status, note = judge_reply(t, ms, listed)
    return LastReply(t.ts, t.platform, t.start_model, t.start_service, t.end_model, t.end_service, t.dropped,
                     t.reason, status, note)


def drops_on(turns: Optional[list[Turn]], day: dt.date) -> int:
    return sum(t.drops for t in turns or () if dt.date.fromtimestamp(t.ts) == day)


def newest_per_chat(turns: Optional[list[Turn]], now: float) -> list[Turn]:
    """The newest reply of every chat that is still open (had a reply in the last day)."""
    newest: dict[str, Turn] = {}
    for t in turns or ():
        newest[t.session] = t
    return [t for t in newest.values() if now - t.ts <= OPEN_CHAT_SEC]


def old_chats(turns: Optional[list[Turn]], ms: Optional[ModelSet], listed: tuple[Backup, ...], now: float) -> int:
    """Open terminal chats whose newest reply is still on an older setting (yellow)."""
    if ms is None:
        return 0
    return sum(1 for t in newest_per_chat(turns, now)
               if t.platform in ("cli", "local") and not t.dropped and judge_reply(t, ms, listed)[0] == YELLOW)


def find_override(turns: Optional[list[Turn]], ms: Optional[ModelSet], listed: tuple[Backup, ...],
                  now: float) -> Optional[Override]:
    if ms is None:
        return None
    blue = [t for t in newest_per_chat(turns, now) if not t.dropped and judge_reply(t, ms, listed)[0] == BLUE]
    if not blue:
        return None
    t = max(blue, key=lambda x: x.ts)
    return Override(t.start_model, t.start_service, t.platform, t.ts)


def recent_signin_failure(turns: Optional[list[Turn]], now: float, ms: Optional["ModelSet"] = None) -> str:
    """The newest sign-in failure of the last day, unless a LATER reply began on the main service: once the main
    service has answered again, an older failure is history, not today's state."""
    for t in reversed(turns or ()):
        if now - t.ts > OPEN_CHAT_SEC:
            break
        if t.signin_failed:
            return t.signin_failed
        if ms is not None and ms.service and t.start_service == ms.service and not t.dropped:
            return ""
    return ""


def voice_for(profile: str, agents_cfg: Optional[dict]) -> Optional[Voice]:
    if agents_cfg is None:
        return None
    for a in A.agents(agents_cfg):
        if a.profile == profile:
            if not a.voice:
                return None
            return Voice(a.voice, None if a.english_only else a.voice_zh, a.speed, a.english_only,
                         not_playable=not_playable(a))
    return None


def display_name(profile: str, agents_cfg: Optional[dict], members: Optional[dict]) -> str:
    for a in A.agents(agents_cfg) if agents_cfg else ():
        if a.profile == profile:
            return a.display
    for m in (members or {}).get("members", ()):
        if isinstance(m, dict) and m.get("profile") == profile and m.get("name"):
            return str(m["name"])
    return profile.replace("_", " ").replace("-", " ").title()


def _seen(turns: Optional[list[Turn]], platforms: tuple[str, ...], now: float) -> Optional[Turn]:
    for t in reversed(turns or ()):
        if now - t.ts > RECENT_SEC:
            return None
        if t.platform in platforms:
            return t
    return None


def _when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%-d %b %H:%M")


def read_gateway_platforms(path: Optional[Path]) -> Optional[dict]:
    """The agents' own status file says which bots are connected ("<profile>:telegram" -> {"state": ...}).
    None when it cannot be read. Only the connection words are looked at."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plats = data.get("platforms") if isinstance(data, dict) else None
    return plats if isinstance(plats, dict) else None


def presence(profile: str, name: str, turns: Optional[list[Turn]], plugin_names: tuple[str, ...],
             members: Optional[dict], voice: Optional[Voice], shortcuts: Path, now: float,
             old_chat_count: int = 0, platforms: Optional[dict] = None) -> tuple[Presence, ...]:
    out = []
    no_log = "unknown: this agent has no log to read" if turns is None else ""

    t = _seen(turns, ("cli", "local"), now)
    if t:
        note = f"a terminal reply was logged {_when(t.ts)}"
        if old_chat_count:
            out.append(Presence("terminal", ON, note + "; an open chat is on an older setting", YELLOW))
        else:
            out.append(Presence("terminal", ON, note, GREEN))
    else:
        out.append(Presence("terminal", UNKNOWN, no_log or "no terminal reply in the current log"))

    t = _seen(turns, ("telegram",), now)
    bot = _as_dict((platforms or {}).get(f"{profile}:telegram"))
    state = str(bot.get("state") or "")
    if state == "connected":
        seen = f"; a Telegram reply was logged {_when(t.ts)}" if t else ""
        out.append(Presence("tg_private", ON, "the agents' own status says this agent's Telegram bot is connected"
                            + seen, GREEN))
    elif state:
        out.append(Presence("tg_private", ON, f"this agent has a Telegram bot, but its status says: {state}", RED))
    elif t:
        out.append(Presence("tg_private", ON, f"a Telegram reply was logged {_when(t.ts)}", GREEN))
    elif platforms is not None:
        out.append(Presence("tg_private", OFF, "the agents' own status lists no Telegram bot for this agent"))
    else:
        out.append(Presence("tg_private", UNKNOWN, no_log or "no Telegram reply in the current log"))

    is_lead = bool(members) and members.get("lead") == profile
    is_member = None if members is None else any(
        isinstance(m, dict) and m.get("profile") == profile for m in members["members"])
    has_bot = bool(state)
    addon_on = TEAM_ROOM_PLUGIN in plugin_names
    if is_lead:
        room_why = "leads the Team Room and cannot be taken out of it"
    elif members is None:
        room_why = "the Team Room members file could not be read, so the desk will not change it"
    else:
        room_why = ""
    switch = not room_why
    if is_member is None:
        if addon_on:
            out.append(Presence("tg_group", ON, "the team-room add-on is switched on for this agent", GREEN,
                                why_not=room_why))
        else:
            out.append(Presence("tg_group", OFF, "the team-room add-on is not switched on for this agent",
                                why_not=room_why))
        out.append(Presence("team_room", UNKNOWN, "the Team Room members file could not be read", why_not=room_why))
    elif is_member:
        follows = "follows the Team Room: one switch moves both"
        if has_bot and not addon_on:
            out.append(Presence("tg_group", ON, "a Team Room member with its own Telegram bot, but the team-room "
                                "add-on is off for it: the two doors do not agree (its bot may answer the group "
                                "by itself)", RED, switch, room_why))
        elif addon_on:
            out.append(Presence("tg_group", ON, "its Team Room lines are copied into the Telegram group, through its "
                                f"own bot (the team-room add-on is switched on); {follows}", GREEN, switch, room_why))
        else:
            out.append(Presence("tg_group", ON, "its Team Room lines are copied into the Telegram group by the "
                                f"lead's bot, text only (it has no bot of its own there); {follows}", GREEN, switch,
                                room_why))
        lead = ", leads the room" if is_lead else ""
        out.append(Presence("team_room", ON, "a saved member of the Team Room" + lead, GREEN, switch, room_why))
    else:
        if addon_on:
            out.append(Presence("tg_group", OFF, "not in the Team Room, but the team-room add-on is still on for it: "
                                "the two doors do not agree", RED, switch, room_why))
        else:
            out.append(Presence("tg_group", OFF, "not in the Team Room, so nothing of it reaches the Telegram group",
                                can_switch=switch, why_not=room_why))
        out.append(Presence("team_room", OFF, "not a saved member of the Team Room", can_switch=switch,
                            why_not=room_why))

    if voice:
        out.append(Presence("mac_voice", ON, "has a voice in the Voice Desk list", GREEN, why_not=WHY_NOT["mac_voice"]))
    else:
        out.append(Presence("mac_voice", OFF, "no voice in the Voice Desk list", why_not=WHY_NOT["mac_voice"]))

    if (Path(shortcuts) / f"Talk to {name}.shortcut").exists():         # only the NAME is looked at
        out.append(Presence("iphone_voice", ON, "a Siri shortcut for this agent exists; Siri reads the answer", GREEN,
                            why_not=WHY_NOT["iphone_voice"]))
    else:
        out.append(Presence("iphone_voice", UNKNOWN, "no Siri shortcut for this agent was found",
                            why_not=WHY_NOT["iphone_voice"]))
    out[0] = dataclasses.replace(out[0], why_not=WHY_NOT["terminal"])
    out[1] = dataclasses.replace(out[1], why_not=WHY_NOT["tg_private"])
    return tuple(out)


# Why a place has no switch on the desk yet (docs/acd_presence_facts.md has the long version).
WHY_NOT = {
    "terminal": "no switch: it is a chat window that is open or closed, not a setting",
    "tg_private": "no switch from the desk: it would mean taking the bot's secret out of the agent's private "
                  "settings and restarting, and the desk never touches secrets",
    "mac_voice": "no clean switch: leaving the Voice Desk list would also drop its voice settings, and a window's "
                 "own voice is set inside that window",
    "iphone_voice": "no switch on the Mac: the Siri shortcut lives on the iPhone (import or delete it there)",
}


def room_follows_rules(code: Optional[Path]) -> bool:
    """True when the Team Room's own program carries the small adapter that reads the desk's rules.
    Read-only: one file is opened and searched for one marker word."""
    if not code:
        return False
    try:
        return ADAPTER_MARK in Path(code).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def answers_token(r: dict, is_lead: bool) -> str:
    """'always' or 'when_named'. Not written down = how the room works today: the lead gets every line with no
    name, everyone else answers when named."""
    raw = r.get("answers")
    if raw in ("always", "when_named"):
        return raw
    return "always" if is_lead else "when_named"


def takes_token(r: dict, profile: str) -> str:
    if r.get("only_job_cards"):
        return "cards"
    boss = r.get("takes_work_from")
    return "lead" if boss and boss != profile else "nobody"


def talk_rules(profile: str, rules: dict, names: dict[str, str], lead: str = "",
               adapter: bool = False) -> tuple[TalkRule, ...]:
    """The four talk rules of one agent, each with where it honestly stands today."""
    r = rules_for(rules, profile)
    is_lead = bool(lead) and profile == lead
    lead_name = names.get(lead, "the lead") if lead else "the lead"
    told = TOLD if adapter else RECORDED
    out = []

    speaks = bool(r.get("speaks_in_team_room", True))
    out.append(TalkRule("speaks", "Speaks in the Team Room", "yes, like every member" if speaks else "no",
                        "yes" if speaks else "no",
                        WORKS_TODAY if speaks else (ENFORCED + " at both doors: Peter's plain mention no longer "
                                                    "hands it a turn (an explicit @name still does)" if adapter
                                                    else RECORDED),
                        "leads the room, so it always speaks" if is_lead else ""))

    hands = bool(r.get("hands_out_work"))
    out.append(TalkRule("hands_out", "Hands out work", "yes" if hands else "no", "yes" if hands else "no", told))

    takes = takes_token(r, profile)
    boss = r.get("takes_work_from")
    words = {"cards": "only as job cards", "lead": names.get(boss, str(boss)), "nobody": "nobody (Peter only)"}[takes]
    out.append(TalkRule("takes_from", "Takes work from", words, takes, told))

    ans = answers_token(r, is_lead)
    locked = ("kept as it is for this agent" if r.get("answers_locked") else "")
    if ans == "when_named":
        standing = WORKS_TODAY + ": a member gets a turn only when a line names it"
        if is_lead:
            standing = RECORDED + f" (the room still hands every line with no name to {lead_name})"
    else:
        standing = (WORKS_TODAY + ": every line with no name goes to the lead") if is_lead else RECORDED
    out.append(TalkRule("answers", "Answers", "when named in the line" if ans == "when_named" else "always",
                        ans, standing, locked))
    return tuple(out)


def talk_lines(profile: str, rules: dict, names: dict[str, str]) -> tuple[tuple[str, str], ...]:
    r = rules_for(rules, profile)
    out = [("Speaks in the Team Room", "yes, like everyone" if r.get("speaks_in_team_room") else "no")]
    if r.get("hands_out_work"):
        out.append(("Hands out work", "yes, the only one who does"))
        out.append(("Sends jobs to Builder", "yes, by job card only"))
    else:
        out.append(("Hands out work", "no"))
    boss = r.get("takes_work_from")
    if r.get("only_job_cards"):
        out.append(("Takes work", "only as job cards"))
    elif boss and boss != profile:
        out.append(("Takes work from", names.get(boss, boss)))
    if r.get("answers_when_named"):
        out.append(("Answers", "when named in the line"))
    return tuple(out)


# ---- everything at once ----------------------------------------------------------------------------------
def with_off_backups(listed: tuple[Backup, ...], recorded: list) -> tuple[Backup, ...]:
    """The backups in the settings file (on), then the ones the desk remembers as switched off."""
    have = {(b.service, b.model) for b in listed}
    out = list(listed)
    for r in recorded or ():
        if isinstance(r, dict) and r.get("model") and (str(r.get("provider") or ""), str(r["model"])) not in have:
            out.append(Backup(int(r.get("position") or len(out) + 1), str(r["model"]), str(r.get("provider") or ""),
                              on=False, note=str(r.get("note") or "")))
            have.add((str(r.get("provider") or ""), str(r["model"])))
    return tuple(out)


PENDING_NOTE = ("new chats and job cards use it at once, Telegram on its next message (expected, not live-tested); "
                "open terminal chats keep the old model until a fresh chat is started")


def judge_pending(pending: Optional[dict], ms: Optional[ModelSet], turns: Optional[list[Turn]]) -> tuple[str, str]:
    """(why the desk's own model change is not live everywhere yet, what changed behind the desk's back).
    Both empty = nothing to say (green)."""
    if not pending or ms is None:
        return "", ""
    want_model, want_service = str(pending.get("model") or ""), str(pending.get("service") or ms.service)
    if want_model and (want_model, want_service) != (ms.model, ms.service):
        return "", (f"the desk last set {model_name(want_model)}, but the settings file now says "
                    f"{model_name(ms.model)}: it was changed somewhere else")
    since = float(pending.get("ts") or 0)
    for t in reversed(turns or ()):
        if t.ts < since:
            break
        if not t.dropped and t.start_model == ms.model and t.start_service == ms.service:
            return "", ""                              # a reply on the new setting was logged: live
    return f"saved {_when(since)}, no reply on it logged yet: {PENDING_NOTE}", ""


TAKES_EFFECT = {
    "claude": "lands in Claude Code's own voice file; Claude speaks with it from its next reply",
    "gemini": "lands in Gemini's own voice file; its hook reads that file before every reply",
    "codex": "Codex has no speech of its own: the Voice Desk speaks for it and reads this list directly",
}
NOT_VERIFIED = "set in the list, not verified to take effect"


def collect(paths: Paths, reader: Optional[LogReader] = None, yaml_loader: Callable[[Path], Any] = hermes_yaml,
            now: Optional[float] = None, desk: Optional[DeskRecord] = None, usage: Optional[dict] = None,
            watch: Any = None) -> Snapshot:
    """Read every source once and return what the desk shows. Never raises for a bad file: a file that cannot
    be read becomes an error on that agent (or a line in `problems`), and the rest is still shown.

    usage: service -> acd_health.Allowance (the newest readings of the shared UsageReader; None = not read).
    watch: acd_watch.WatchView (what drifted from the desk's own record; None = the watcher was not asked)."""
    import time
    now = time.time() if now is None else now
    reader = reader or LogReader(paths.profiles)
    problems: list[str] = []

    try:
        rules = load_rules(paths.rules)
    except (OSError, ValueError) as e:
        rules = {}
        problems.append(f"the rules file could not be read ({type(e).__name__})")
    try:
        agents_cfg: Optional[dict] = A.load(paths.agents_toml)
    except (OSError, ValueError) as e:
        agents_cfg = None
        problems.append(f"the Voice Desk list could not be read ({type(e).__name__})")
    members = read_members(paths.members)
    platforms = read_gateway_platforms(paths.gateway_state)
    adapter = room_follows_rules(paths.team_room_code)
    lead = str(members.get("lead") or "") if members else ""
    in_room = None if members is None else {m.get("profile") for m in members["members"] if isinstance(m, dict)}
    desk = desk or DeskRecord()
    listed_agents = A.agents(agents_cfg) if agents_cfg else []
    keys = {a.profile: a.key for a in listed_agents if a.profile}
    effects = {a.profile: a.effect for a in listed_agents if a.profile and a.effect}
    places, wiring = grid_view(paths, agents_cfg, reader, now)
    drifted: dict = {}
    for d in (watch.drifts if watch is not None else ()):
        drifted.setdefault(d.profile, []).append(d)

    profiles = discover_profiles(paths.profiles, rules)
    names = {p: display_name(p, agents_cfg, members) for p in profiles}
    today = dt.date.fromtimestamp(now)
    views = []
    for profile in profiles:
        name = names[profile]
        r = rules_for(rules, profile)
        role = str(r.get("role") or "")
        try:
            cfg = yaml_loader(paths.profiles / profile / "config.yaml")
            if not isinstance(cfg, dict):
                raise ValueError("the settings file is not a list of settings")
        except Exception as e:                     # noqa: BLE001  a bad file must never stop the desk
            views.append(AgentView(profile, name, role,
                                   error=f"the settings file could not be read: {str(e)[:120] or type(e).__name__}"))
            continue
        ms = model_set(cfg)
        listed = backups(cfg)
        turns = reader.turns(profile)
        voice = voice_for(profile, agents_cfg)
        plug = addons(cfg)
        stale = old_chats(turns, ms, listed, now)
        pending, behind = judge_pending(desk.pending.get(profile), ms, turns)
        locked = bool(r.get("model_locked"))
        events = reader.events(profile)
        health = tuple(service_view(H.judge(s, events, now, (usage or {}).get(s) if usage is not None else None),
                                    now, main=bool(ms) and s == ms.service)
                       for s in services_of(ms, listed))
        views.append(AgentView(
            profile=profile, name=name, role=role, model=ms, model_locked=locked,
            last=last_reply(turns, ms, listed), drops_today=drops_on(turns, today), old_chats=stale,
            signin_failed=recent_signin_failure(turns, now, ms),
            override=find_override(turns, ms, listed, now),
            backups=listed if locked else with_off_backups(listed, desk.off.get(profile)), voice=voice,
            presence=presence(profile, name, turns, plug, members, voice, paths.shortcuts, now, stale, platforms),
            talks=talk_lines(profile, rules, names), addons=plug, voice_key=keys.get(profile, ""),
            model_note=str(r.get("model_note") or ""), model_pending=pending, model_behind=behind,
            backups_locked=locked, talk_rules=talk_rules(profile, rules, names, lead, adapter),
            room_lead=bool(lead) and profile == lead,
            in_room=None if in_room is None else profile in in_room,
            health=health, drifts=tuple(drifted.get(profile, ())),
            voice_places=places.get(keys.get(profile, ""), ()), effect=effects.get(profile),
            wiring=wiring.get(keys.get(profile, ""), ())))

    mounts: dict = {}
    if paths.mounts is not None:
        try:
            mounts = paths.mounts() or {}
        except Exception:                           # noqa: BLE001  a failed look must never take the desk down
            mounts = {}
    others = tuple(OtherAssistant(a.display, a.voice, a.voice_zh, a.names, a.key,
                                  TAKES_EFFECT.get(a.target, NOT_VERIFIED), places.get(a.key, ()),
                                  wiring.get(a.key, ()), not_playable(a), mounts.get(a.target))
                   for a in listed_agents if not a.profile)
    lights: tuple = ()
    if paths.lights is not None and paths.grid is not None:
        try:
            lights = tuple((x.ok, x.label, x.detail) for x in paths.lights(paths.grid))
        except Exception:                           # noqa: BLE001  a failed look must never take the desk down
            lights = ()
    if members is None:
        room = "The Team Room members file could not be read."
    else:
        lead = names.get(str(members.get("lead")), str(members.get("lead") or "nobody"))
        saved = str(members.get("saved") or "")[:10]
        room = f"{lead} leads the Team Room. {len(members['members'])} members saved{' ' + saved if saved else ''}."
    views_t = tuple(views)
    return Snapshot(views_t, others, warnings(views_t, problems, desk, watch, lights, others=others), room, now,
                    tuple(problems),
                    waiting=tuple(desk.waiting), armed=desk.armed, last_change=desk.last_change,
                    room_follows_rules=adapter,
                    watch_mode=watch.mode if watch is not None else "",
                    watch_paused=bool(watch.paused) if watch is not None else False,
                    watch_notes=tuple(watch.notes) if watch is not None else (),
                    lights=lights, allowance_read=usage is not None and bool(usage))


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def warnings(views: tuple[AgentView, ...], problems: list[str] = (),
             desk: Optional[DeskRecord] = None, watch: Any = None, lights: tuple = (),
             others: tuple = ()) -> tuple[Warning, ...]:
    """The strip at the top. Real conditions only; when nothing is wrong it says so."""
    out = [Warning(RED, p) for p in problems]
    drifted = [d for v in views for d in v.drifts]
    if drifted:
        who = sorted({d.name for d in drifted})
        out.append(Warning(RED, _count(len(drifted), "setting was", "settings were")
                           + " changed behind the desk's back (" + ", ".join(who)
                           + "): press Enter on the red cell to put it back or accept it"))
    if watch is not None and watch.paused:
        out.append(Warning(YELLOW, "the watcher is paused (p resumes): the desk is not checking for changes "
                                   "made behind its back"))
    if desk and desk.waiting:
        out.append(Warning(YELLOW, _count(len(desk.waiting), "saved change is", "saved changes are")
                           + " waiting for a restart: press r to review"))
    behind = [v.name for v in views if v.model_behind and not v.drifts]      # a drift line above already says it
    if behind:
        out.append(Warning(RED, "the model was changed behind the desk's back for: " + ", ".join(behind)))
    fresh = [v.name for v in views if v.model_pending]
    if fresh:
        out.append(Warning(YELLOW, "a model change from the desk is not live everywhere yet for: " + ", ".join(fresh)))
    broken = [v.name for v in views if v.error]
    if broken:
        out.append(Warning(RED, "settings file could not be read for: " + ", ".join(broken)))
    slipped = [(v, ladder(v).below) for v in views if not v.error]
    slipped = [(v, where) for v, where in slipped if where]
    if slipped:
        dropped = [v for v, _ in slipped if v.last and v.last.dropped]
        head = (_count(len(slipped), "agent", "agents") + " had the main model fail on the last reply and a backup "
                "answered" if len(dropped) == len(slipped) else
                _count(len(slipped), "agent", "agents") + " answered the last reply from below rung 1 of the ladder")
        out.append(Warning(RED, head + ": " + ", ".join(f"{v.name} on {where}" for v, where in slipped)))
    dead_main = {v.profile for v in views for h in v.health if h.main and h.kind == H.SIGNIN_DEAD}
    good_main = {v.profile for v in views for h in v.health if h.main and h.state == H.GOOD}
    # the same evidence as the row: a main service that is working now is not "could not sign in", whatever
    # an older log line said
    locked_out = [v for v in views if v.model and v.profile not in good_main
                  and (v.signin_failed or v.profile in dead_main)]
    if locked_out:
        services = sorted({service_name(v.model.service) for v in locked_out})
        dead = [v for v in locked_out if v.profile in dead_main]
        more = (". The saved sign-in is dead: only Peter signing in again fixes it"
                if dead and len(dead) == len(locked_out) else "")
        out.append(Warning(RED, f"{' and '.join(services)} could not sign in for "
                           + _count(len(locked_out), "agent", "agents") + ": replies begin on a backup" + more))
    used_up: dict = {}
    for v in views:
        for h in v.health:
            if h.kind == H.ALLOWANCE_OUT:
                used_up.setdefault(h.service, []).append(v.name)
    for service, who in used_up.items():
        name = service_name(service)
        out.append(Warning(RED, f"{name[0].upper() + name[1:]}: an allowance is used up for "
                           + _count(len(who), "agent", "agents") + " (the strip under the table says which model and when it resets)"))
    stale = sum(v.old_chats for v in views)
    if stale:
        out.append(Warning(YELLOW, _count(stale, "open chat is", "open chats are")
                           + " still on an older setting (a fresh chat picks up the current one)"))
    pinned = [v for v in views if v.override]
    if pinned:
        out.append(Warning(BLUE, _count(len(pinned), "agent has", "agents have") + " a temporary model choice"))
    split = [v.name for v in views if any(p.key == "tg_group" and p.status == RED for p in v.presence)]
    if split:
        out.append(Warning(RED, "the Team Room and the Telegram group do not agree for: " + ", ".join(split)))
    for v in views:
        if DLS_PLUGIN in v.addons:
            out.append(Warning(YELLOW, f"the DLS add-on is still switched on inside {v.name}"))
    for ok, label, detail in lights:
        if ok is False:
            out.append(Warning(RED, f"voice system: {label}" + (f" ({detail[:120]})" if detail else "")))
    for o in others:                                    # a mounted session never falls back: say it once, up here
        m = getattr(o, "mount", None)
        if m is not None and m.state == "closed":
            out.append(Warning(YELLOW, f"the mounted {o.name} session is not open: voice to {o.name} starts nothing "
                                       "else until its window opens or another session is mounted"))
    if not out:
        out.append(Warning(GREEN, "nothing needs attention"))
    return tuple(out)
