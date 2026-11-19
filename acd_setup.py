"""Agent Control Desk: what a new agent has set up, read from its own files (phase 1, 2026-11-19).

Peter: "a manually added feature for a new agent ... only Hermes dedicated feature ... type the agent name that we
named when we 1st created Hermes, and then start to configure all the features."

An agent's block in the Voice Desk list (agents.toml) carries a `kind`:

    kind = "hermes"   a Hermes profile on this Mac: this module is its connector
    kind = "cli"      another assistant that runs in a terminal (Claude, Gemini, Codex and the like); no connector yet

A block with no `kind` line is read as before: "hermes" when it names a profile, "cli" otherwise. Only a block that
SAYS kind = "hermes" (the desk's n key writes it) gets the full set-up check, so every column that was on the desk
before looks exactly as it did. Another kind is added later with one connector of its own; none is built here.

The Hermes connector reads, and only reads, the profile's own files:

    model, thinking effort    its settings file (config.yaml): model.default, model.provider, agent.reasoning_effort
    service and sign-in       its auth.json: is a sign-in saved for the main service, and what Hermes last marked it
                              (never a secret: only the service's name, how many sign-ins and their status words)
    fallback ladder           its settings file: fallback_providers
    Telegram bot              its .env: is a TELEGRAM_BOT_TOKEN line there with a value (yes / no ONLY; the value is
                              never kept, shown, logged or passed on), and the Hermes gateway's own status file: is
                              that bot connected (reachable). No Telegram call is made.
    terminal window           herdr's window list (read-only): is a window titled "Hermes — <profile>" open
    Team Room                 the Team Room's saved members
    Siri shortcut             is there a "Talk to <name>.shortcut" (its name only)

Nothing is changed. Anything that is not there is red, with the words "not set up" and a plain reason; a green
light is only given for something the files really show.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

GREEN, YELLOW, BLUE, RED, GREY = "green", "yellow", "blue", "red", "grey"     # the desk's colours (acd_data)

HERMES, CLI = "hermes", "cli"
KINDS = (HERMES, CLI)
KIND_WORDS = {HERMES: "a Hermes agent", CLI: "another assistant that runs in a terminal"}
NOT_SET_UP = "not set up"
TOKEN_NAME = "TELEGRAM_BOT_TOKEN"
AGY = "antigravity-subscription-directsdk"
SERVICE_WORDS = {"anthropic": "the Claude subscription", AGY: "Antigravity", "nvidia": "NVIDIA",
                 "openai-codex": "the ChatGPT subscription", "openrouter": "OpenRouter"}


def kind_of(raw: dict) -> str:
    """The block's kind: what it says, else read from the block as before (a profile line = a Hermes agent)."""
    said = str(raw.get("kind") or "").strip().lower()
    if said in KINDS:
        return said
    return HERMES if raw.get("profile") else CLI


def checked(raw: dict) -> bool:
    """Gets the full set-up check: only a block that says kind = "hermes" itself (written by the desk's n)."""
    return str(raw.get("kind") or "").strip().lower() == HERMES and bool(raw.get("profile"))


def _service(service: str) -> str:
    return SERVICE_WORDS.get(service, service or "its service")


# ---- one piece of the set-up ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Piece:
    key: str            # model / effort / signin / ladder / telegram / window / team_room / iphone
    label: str          # the piece's name, for the list after n
    status: str         # the light
    words: str          # a few words for the cell
    why: str            # one plain sentence: what was found, or why it is not set up
    found: bool         # True = it is there


@dataclass(frozen=True)
class Setup:
    """Everything the connector found for one profile."""
    profile: str
    kind: str
    pieces: tuple = ()
    error: str = ""     # the settings file could not be read

    def piece(self, key: str) -> Optional[Piece]:
        return next((p for p in self.pieces if p.key == key), None)

    @property
    def missing(self) -> list:
        return [p for p in self.pieces if not p.found]


def _missing(key: str, label: str, why: str, words: str = NOT_SET_UP) -> Piece:
    return Piece(key, label, RED, words, why, False)


# ---- small readers (each only reads; none raises) ----------------------------------------------------------------
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def env_value(path: Path, name: str) -> Optional[str]:
    """The value of `name` in a .env file ("" = not there or empty, None = the file cannot be read). Only for the one
    caller that must hand a bot's token to Telegram itself (acd_groups.py): it is never kept, shown or logged."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        return None
    for ln in text.splitlines():
        m = _ENV_LINE.match(ln)
        if m and m.group(1) == name:
            value = m.group(2).strip()
            if value[:1] in ("'", '"'):
                value = value[1:].split(value[0], 1)[0]
            else:
                value = re.split(r"(?:^|\s)#", value, maxsplit=1)[0]
            if value.strip():
                return value.strip()
    return ""


def env_has(path: Path, name: str) -> Optional[bool]:
    """True when the .env file has `name` with a value, False when it has not, None when it cannot be read. ONLY the
    yes / no leaves this function: the value itself is never returned, kept or put into any message."""
    value = env_value(path, name)
    return None if value is None else bool(value)


def read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sign_ins(auth: Optional[dict], service: str) -> list[str]:
    """The status word of every saved sign-in for one service ("ok", "dead", "exhausted", "" = not used yet).
    Only the status words are read; the sign-ins themselves are never looked at."""
    if not auth or not service:
        return []
    out = []
    pool = auth.get("credential_pool")
    for entry in (pool.get(service) if isinstance(pool, dict) else None) or ():
        if isinstance(entry, dict):
            out.append(str(entry.get("last_status") or ""))
    providers = auth.get("providers")
    if isinstance(providers, dict) and providers.get(service):
        out.append("")
    return out


# ---- the pieces --------------------------------------------------------------------------------------------------
def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def model_piece(cfg: dict) -> Piece:
    m = _as_dict(cfg.get("model"))
    model, service = str(m.get("default") or ""), str(m.get("provider") or "")
    if not model:
        return _missing("model", "Model", "this profile's settings name no main model")
    if not service:
        return _missing("model", "Model", f"this profile's settings name the model {model} but no service for it")
    return Piece("model", "Model", GREEN, model, f"the main model is {model} on {_service(service)}", True)


def effort_piece(cfg: dict) -> Piece:
    effort = _as_dict(cfg.get("agent")).get("reasoning_effort")
    if effort is None or str(effort).strip() == "":
        return _missing("effort", "Thinking effort", "this profile's settings name no thinking effort, so Hermes uses "
                                                     "its own default")
    return Piece("effort", "Thinking effort", GREEN, str(effort), f"thinking effort is {effort}", True)


def signin_piece(cfg: dict, own: Optional[dict], shared: Optional[dict]) -> Piece:
    """Is a sign-in saved for the main service: the profile's own auth.json first, then Hermes' shared one."""
    label = "Service and sign-in"
    service = str(_as_dict(cfg.get("model")).get("provider") or "")
    if not service:
        return _missing("signin", label, "no service is named for the main model, so there is nothing to sign in to")
    name = _service(service)
    mine = sign_ins(own, service)
    if mine:
        if "ok" in mine:
            return Piece("signin", label, GREEN, "signed in", f"{name}: a saved sign-in in this profile that Hermes "
                                                             "last marked as working", True)
        if all(s in ("dead", "invalid", "revoked") for s in mine):
            return _missing("signin", label, f"{name}: every saved sign-in in this profile is dead; only Peter "
                                             "signing in again fixes it", "sign-in dead")
        if "exhausted" in mine and not any(s in ("", "ok") for s in mine):
            return Piece("signin", label, RED, "used up", f"{name}: the saved sign-in works, but Hermes last marked "
                                                         "its allowance as used up", True)
        return Piece("signin", label, GREY, "saved, not used yet", f"{name}: a sign-in is saved in this profile, but "
                                                                   "it has not answered yet, so it is not confirmed",
                     True)
    if sign_ins(shared, service):
        return Piece("signin", label, GREY, "shared sign-in", f"{name}: no sign-in in this profile itself; one is saved "
                                                              "in Hermes' shared settings, not confirmed for this "
                                                              "profile", True)
    if service == AGY:
        on = AGY in (_as_dict(cfg.get("plugins")).get("enabled") or ())
        if on:
            return Piece("signin", label, GREY, "add-on", "Antigravity signs in through its own add-on, which is "
                                                          "switched on here; the desk cannot check that sign-in", True)
        return _missing("signin", label, "the main service is Antigravity, but its add-on is not switched on in this "
                                         "profile")
    return _missing("signin", label, f"this profile has no saved sign-in for {name}")


def ladder_piece(cfg: dict) -> Piece:
    listed = [e for e in (cfg.get("fallback_providers") or ()) if isinstance(e, dict) and e.get("model")]
    if not listed:
        return _missing("ladder", "Fallback ladder", "this profile has no backups: when its main model fails, nothing "
                                                     "answers")
    n = len(listed)
    return Piece("ladder", "Fallback ladder", GREEN, f"{n} backup{'s' if n != 1 else ''}",
                 f"{n} backup{'s are' if n != 1 else ' is'} listed in its settings", True)


def telegram_piece(profile: str, env_file: Path, platforms: Optional[dict]) -> Piece:
    """Is a bot token there (yes / no only), and is that bot connected, as the Hermes gateway's own status says."""
    label = "Telegram bot"
    token = env_has(env_file, TOKEN_NAME)
    if token is None:
        return Piece("telegram", label, GREY, "could not be checked", "this profile's .env file could not be read, so "
                                                                      "the desk cannot tell if it has a bot token",
                     False)
    if not token:
        return _missing("telegram", label, "this profile has no Telegram bot token")
    if platforms is None:
        return Piece("telegram", label, GREY, "token, not confirmed", "a bot token is there, but the Hermes gateway's "
                                                                      "status file could not be read, so it is not "
                                                                      "confirmed that the bot is reachable", True)
    state = str(_as_dict(platforms.get(f"{profile}:telegram")).get("state") or "")
    if state == "connected":
        return Piece("telegram", label, GREEN, "connected", "a bot token is there, and the Hermes gateway's own status "
                                                            "says its bot is connected", True)
    if state:
        return Piece("telegram", label, RED, f"bot {state}", f"a bot token is there, but the Hermes gateway's status "
                                                             f"says its bot is {state}", True)
    return Piece("telegram", label, RED, "token, not connected", "a bot token is there, but the Hermes gateway has not "
                                                                  "connected this bot, so it is not reachable yet (the "
                                                                  "gateway picks up a new bot when it restarts)", True)


def window_piece(profile: str, windows: Any) -> Piece:
    """windows: the set of profiles with an open Hermes window, or a sentence why herdr could not be asked."""
    label = "Terminal window"
    if isinstance(windows, str) or windows is None:
        return Piece("window", label, GREY, "could not be checked",
                     f"the open windows could not be listed ({windows or 'not asked'})", False)
    if profile in windows:
        return Piece("window", label, GREEN, "open", f"a terminal window titled \"Hermes — {profile}\" is open", True)
    return _missing("window", label, f"no terminal window titled \"Hermes — {profile}\" is open (one opens with "
                                     f"hermes -p {profile} in herdr)", "no window open")


def room_piece(profile: str, members: Optional[dict]) -> Piece:
    label = "Team Room"
    if members is None:
        return Piece("team_room", label, GREY, "could not be checked", "the Team Room members file could not be read",
                     False)
    if members.get("lead") == profile:
        return Piece("team_room", label, GREEN, "in, leads it", "it leads the Team Room", True)
    if any(isinstance(m, dict) and m.get("profile") == profile for m in members.get("members") or ()):
        return Piece("team_room", label, GREEN, "in", "a saved member of the Team Room", True)
    return _missing("team_room", label, "it is not a member of the Team Room", "out")


def iphone_piece(name: str, shortcuts: Path) -> Piece:
    label = "Siri shortcut"
    if (Path(shortcuts) / f"Talk to {name}.shortcut").exists():          # only the NAME is looked at
        return Piece("iphone", label, GREEN, "shortcut", f"a Siri shortcut \"Talk to {name}\" exists", True)
    return _missing("iphone", label, f"there is no Siri shortcut \"Talk to {name}\" yet", "no shortcut")


def hermes_setup(profile: str, name: str, cfg: Any, profiles: Path, *, platforms: Optional[dict] = None,
                 members: Optional[dict] = None, windows: Any = None, shortcuts: Optional[Path] = None) -> Setup:
    """The Hermes connector: every piece of one profile's set-up. cfg is its settings file as Hermes reads it (a
    dict), or an exception / None when it could not be read. Only reads; never raises."""
    if not isinstance(cfg, dict):
        why = str(cfg)[:120] if isinstance(cfg, Exception) else "the settings file could not be read"
        return Setup(profile, HERMES, (), error=why or "the settings file could not be read")
    folder = Path(profiles) / profile
    pieces = [model_piece(cfg), effort_piece(cfg),
              signin_piece(cfg, read_json(folder / "auth.json"), read_json(Path(profiles).parent / "auth.json")),
              ladder_piece(cfg), telegram_piece(profile, folder / ".env", platforms), window_piece(profile, windows),
              room_piece(profile, members)]
    if shortcuts is not None:
        pieces.append(iphone_piece(name, shortcuts))
    return Setup(profile, HERMES, tuple(pieces))


def lines(s: Setup) -> list[str]:
    """The set-up as plain lines, for the list shown after n: "+ found" or "- not set up", each with its reason."""
    if s.error:
        return [f"- the settings file of {s.profile} could not be read ({s.error})"]
    out = []
    for p in s.pieces:
        mark = "+" if p.found and p.status == GREEN else "-" if p.status == RED else "?"
        out.append(f"{mark} {p.label}: {p.words}: {p.why}")
    return out


# ---- which Hermes windows are open (herdr, read-only) -------------------------------------------------------------
def open_windows(herdr: Optional[str]) -> Any:
    """The profiles that have a terminal window open ("Hermes — <profile>"), from ONE read-only `herdr pane list`; or
    a sentence why herdr could not be asked."""
    if not herdr:
        return "herdr is not known to the desk"
    try:
        import acd_mount
        panes = acd_mount.herdr_panes(herdr)
    except Exception as exc:                            # noqa: BLE001  a look must never take the desk down
        return str(exc) or type(exc).__name__
    out = set()
    for p in panes:
        title = str(p.get("terminal_title_stripped") or p.get("terminal_title") or "").strip()
        m = re.fullmatch(r"(?i)hermes\s+[—-]\s+(\S+)", title)
        if m:
            out.add(m.group(1))
    return out


def windows_reader(herdr: Optional[str]) -> Callable[[], Any]:
    return lambda: open_windows(herdr)
