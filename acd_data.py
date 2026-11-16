"""Agent Control Desk, version one: the facts. It SHOWS everything and CHANGES nothing.

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

import datetime as dt
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import agents as A

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

    @classmethod
    def default(cls) -> "Paths":
        env = os.environ.get
        return cls(
            profiles=Path(env("HERMES_PROFILES_DIR", HOME / ".hermes/profiles")),
            agents_toml=Path(env("VOICE_DESK_AGENTS", HOME / "GitHub/Voice_Desk/agents.toml")),
            members=Path(env("ACD_TEAM_ROOM_MEMBERS", HOME / "GitHub/Team_Room/members.json")),
            rules=Path(env("ACD_RULES", REPO / "acd_rules.toml")),
            shortcuts=Path(env("ACD_SHORTCUTS_DIR", HOME / "GitHub/Voice_Desk")))


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
        self.reads = 0                     # how many files were really read (a test checks this stays low)

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
        return got


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
    on: bool = True                 # version one: a listed backup is on. "Off" needs the desk's own record (later)


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


@dataclass(frozen=True)
class Presence:
    key: str
    state: str                      # ON / OFF / UNKNOWN
    evidence: str                   # where that answer came from, in plain words
    status: str = GREY


@dataclass(frozen=True)
class Override:
    """A temporary choice the desk leaves alone and shows (blue)."""
    model: str
    service: str
    where: str
    when: float


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

    @property
    def model_status(self) -> str:
        if self.error:
            return RED
        return GREEN if self.model else GREY

    @property
    def worst(self) -> str:
        """The colour of the row's marker: the most serious thing on it."""
        seen = {self.model_status, self.last.status if self.last else GREY}
        if self.old_chats:
            seen.add(YELLOW)
        if self.override:
            seen.add(BLUE)
        for colour in (RED, YELLOW, BLUE, GREEN):
            if colour in seen:
                return colour
        return GREY


@dataclass(frozen=True)
class OtherAssistant:
    """Claude, Gemini, Codex: called by name through the Voice Desk. Voice and call names only."""
    name: str
    english: Optional[str]
    chinese: Optional[str]
    call_names: tuple[str, ...]


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


def recent_signin_failure(turns: Optional[list[Turn]], now: float) -> str:
    for t in reversed(turns or ()):
        if now - t.ts > OPEN_CHAT_SEC:
            break
        if t.signin_failed:
            return t.signin_failed
    return ""


def voice_for(profile: str, agents_cfg: Optional[dict]) -> Optional[Voice]:
    if agents_cfg is None:
        return None
    for a in A.agents(agents_cfg):
        if a.profile == profile:
            if not a.voice:
                return None
            return Voice(a.voice, None if a.english_only else a.voice_zh, a.speed, a.english_only)
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


def presence(profile: str, name: str, turns: Optional[list[Turn]], plugin_names: tuple[str, ...],
             members: Optional[dict], voice: Optional[Voice], shortcuts: Path, now: float,
             old_chat_count: int = 0) -> tuple[Presence, ...]:
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
    if t:
        out.append(Presence("tg_private", ON, f"a Telegram reply was logged {_when(t.ts)}", GREEN))
    else:
        out.append(Presence("tg_private", UNKNOWN, no_log or "no Telegram reply in the current log"))

    if TEAM_ROOM_PLUGIN in plugin_names:
        out.append(Presence("tg_group", ON, "the team-room add-on is switched on for this agent", GREEN))
    else:
        out.append(Presence("tg_group", OFF, "the team-room add-on is not switched on for this agent"))

    if members is None:
        out.append(Presence("team_room", UNKNOWN, "the Team Room members file could not be read"))
    elif any(isinstance(m, dict) and m.get("profile") == profile for m in members["members"]):
        lead = ", leads the room" if members.get("lead") == profile else ""
        out.append(Presence("team_room", ON, "a saved member of the Team Room" + lead, GREEN))
    else:
        out.append(Presence("team_room", OFF, "not a saved member of the Team Room"))

    if voice:
        out.append(Presence("mac_voice", ON, "has a voice in the Voice Desk list", GREEN))
    else:
        out.append(Presence("mac_voice", OFF, "no voice in the Voice Desk list"))

    if (Path(shortcuts) / f"Talk to {name}.shortcut").exists():         # only the NAME is looked at
        out.append(Presence("iphone_voice", ON, "a Siri shortcut for this agent exists; Siri reads the answer", GREEN))
    else:
        out.append(Presence("iphone_voice", UNKNOWN, "no Siri shortcut for this agent was found"))
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
def collect(paths: Paths, reader: Optional[LogReader] = None, yaml_loader: Callable[[Path], Any] = hermes_yaml,
            now: Optional[float] = None) -> Snapshot:
    """Read every source once and return what the desk shows. Never raises for a bad file: a file that cannot
    be read becomes an error on that agent (or a line in `problems`), and the rest is still shown."""
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
        views.append(AgentView(
            profile=profile, name=name, role=role, model=ms, model_locked=bool(r.get("model_locked")),
            last=last_reply(turns, ms, listed), drops_today=drops_on(turns, today), old_chats=stale,
            signin_failed=recent_signin_failure(turns, now),
            override=find_override(turns, ms, listed, now), backups=listed, voice=voice,
            presence=presence(profile, name, turns, plug, members, voice, paths.shortcuts, now, stale),
            talks=talk_lines(profile, rules, names), addons=plug))

    others = tuple(OtherAssistant(a.display, a.voice, a.voice_zh, a.names)
                   for a in (A.agents(agents_cfg) if agents_cfg else ()) if not a.profile)
    if members is None:
        room = "The Team Room members file could not be read."
    else:
        lead = names.get(str(members.get("lead")), str(members.get("lead") or "nobody"))
        saved = str(members.get("saved") or "")[:10]
        room = f"{lead} leads the Team Room. {len(members['members'])} members saved{' ' + saved if saved else ''}."
    views_t = tuple(views)
    return Snapshot(views_t, others, warnings(views_t, problems), room, now, tuple(problems))


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def warnings(views: tuple[AgentView, ...], problems: list[str] = ()) -> tuple[Warning, ...]:
    """The strip at the top. Real conditions only; when nothing is wrong it says so."""
    out = [Warning(RED, p) for p in problems]
    broken = [v.name for v in views if v.error]
    if broken:
        out.append(Warning(RED, "settings file could not be read for: " + ", ".join(broken)))
    dropped = [v for v in views if v.last and v.last.dropped]
    if dropped:
        out.append(Warning(RED, _count(len(dropped), "agent", "agents")
                           + " had the main model fail on the last reply and a backup answered"))
    locked_out = [v for v in views if v.signin_failed and v.model]
    if locked_out:
        services = sorted({service_name(v.model.service) for v in locked_out})
        out.append(Warning(RED, f"{' and '.join(services)} could not sign in for "
                           + _count(len(locked_out), "agent", "agents") + ": replies begin on a backup"))
    stale = sum(v.old_chats for v in views)
    if stale:
        out.append(Warning(YELLOW, _count(stale, "open chat is", "open chats are")
                           + " still on an older setting (a fresh chat picks up the current one)"))
    pinned = [v for v in views if v.override]
    if pinned:
        out.append(Warning(BLUE, _count(len(pinned), "agent has", "agents have") + " a temporary model choice"))
    for v in views:
        if DLS_PLUGIN in v.addons:
            out.append(Warning(YELLOW, f"the DLS add-on is still switched on inside {v.name}"))
    if not out:
        out.append(Warning(GREEN, "nothing needs attention"))
    return tuple(out)
