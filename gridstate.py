"""The voice grid, part 1: what every voice setting says right now (read-only).

One row per agent in agents.toml, one column per place an agent can speak:

    terminal   the agent's own terminal window
    tg_dm      Telegram direct chat with Peter
    tg_group   the Telegram team group
    mic        the Voice Desk mic window / phone door
    iphone     the iPhone (Siri speaks the text the Mac sends)

Each cell is Off / Voice-back (speaks only when Peter spoke) / Always, or NA when the place does
not apply to that agent. Everything here only READS the files where the settings really live
(docs/where-every-voice-setting-lives.md lists them); nothing is written, nothing is spoken and
no process is started, so the panel can never become a lifeline: if it stops, voices keep working
exactly as last set.
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import agents
import settings as S
import voicecfg

# -- the grid's vocabulary ------------------------------------------------------------------------
OFF, VOICE_BACK, ALWAYS, NA, UNKNOWN = "off", "voice_back", "always", "na", "unknown"
LABEL = {OFF: "Off", VOICE_BACK: "Voice-back", ALWAYS: "Always", NA: "—", UNKNOWN: "?"}

PLACES = (
    ("terminal", "Terminal window"),
    ("tg_dm", "Telegram direct"),
    ("tg_group", "Telegram group"),
    ("mic", "Mic window / phone"),
    ("iphone", "iPhone (Siri)"),
)
PLACE_KEYS = tuple(k for k, _ in PLACES)
PLACE_NAME = dict(PLACES)

HERMES, CLAUDE, GEMINI, CODEX, OTHER = "hermes", "claude", "gemini", "codex", "other"
_ORDER = {HERMES: 0, CLAUDE: 1, GEMINI: 2, CODEX: 3, OTHER: 4}
SKIP_PROFILES: set[str] = set()   # 2026-11-13: the rows are exactly the [agents.*] blocks. Builder had been left off
#                                   by name; he has no block, so he is not on the grid until Peter adds him (A key).
# An agent that is not a brain Voice Desk talks to (Simba, 2026-11-10): agents.toml holds only her voice; no place on
# the grid has a switch for her. Her own listener (panbear-daily simba.py) uses that voice for her Team_Room voice
# notes, switched in its simba.yaml (voice_notes.enabled): the Telegram group cell shows that switch, read only.
VOICE_ONLY_NOTE = "voice only: Voice Desk holds this agent's voice, nothing here makes it speak"

_VOICE_MODE_TO_CELL = {"off": OFF, "voice_only": VOICE_BACK, "all": ALWAYS}
CELL_TO_VOICE_MODE = {v: k for k, v in _VOICE_MODE_TO_CELL.items()}
GEMINI_SWITCH_NAME = "gemini_voice_off"


@dataclass(frozen=True)
class Cell:
    value: str                       # OFF / VOICE_BACK / ALWAYS / NA / UNKNOWN
    note: str = ""                   # where it came from, in plain words
    problem: str = ""                # something is broken in the setting itself (shown red)

    @property
    def label(self) -> str:
        return LABEL[self.value]


@dataclass(frozen=True)
class Paths:
    """Every file the grid reads. Tests build one pointing at a throw-away folder."""
    hermes_home: Path
    profiles: Path
    claude_dir: Path
    gemini_dir: Path
    state: Path
    mirror_config: Path
    repo: Path
    tg_dm_chat: str = "7512954760"
    receiver_port: int = 8765
    simba_yaml: Optional[Path] = None            # panbear-daily's simba.yaml: her Team_Room voice-note switch

    @classmethod
    def default(cls) -> "Paths":
        home = Path.home()
        hermes = Path(os.environ.get("VOICE_DESK_HERMES_HOME", home / ".hermes"))
        profiles = S.HERMES_PROFILES_DIR
        return cls(
            hermes_home=hermes, profiles=profiles,
            claude_dir=Path(os.environ.get("VOICE_DESK_CLAUDE_DIR", home / ".claude")),
            gemini_dir=Path(os.environ.get("VOICE_DESK_GEMINI_DIR", home / ".gemini")),
            state=S.STATE_DIR,
            mirror_config=Path(os.environ.get(
                "VOICE_DESK_MIRROR_CONFIG",
                profiles / "orchestrator/scripts/team_room/state/mirror_config.json")),
            repo=S.REPO, tg_dm_chat=os.environ.get("VOICE_DESK_TG_DM_CHAT", "7512954760"),
            receiver_port=S.RECEIVER_PORT,
            simba_yaml=Path(os.environ.get("VOICE_DESK_SIMBA_YAML", home / "GitHub/panbear-daily/simba.yaml")))

    @property
    def voice_mode_file(self) -> Path:           # read ONLY when the gateway starts
        return self.hermes_home / "gateway_voice_mode.json"

    @property
    def claude_conf(self) -> Path:
        return voicecfg.CLAUDE_CONF if "VOICE_DEFAULTS_CONF" in os.environ else self.claude_dir / "voice-defaults.conf"

    @property
    def claude_hook(self) -> Path:
        return self.claude_dir / "hooks/speak-response.sh"

    @property
    def claude_state(self) -> Path:
        return self.claude_dir / "hooks/state"

    @property
    def gemini_hook(self) -> Path:
        return self.gemini_dir / "config/hooks/speak-response.py"

    @property
    def gemini_switch(self) -> Path:
        """Gemini's off switch (step 3 prepares it, step 4 teaches the hook): the file EXISTS = Gemini stays
        silent; absent = Gemini speaks as it always did. Same marker convention as speech/off. See
        docs/gemini-switch-file.md."""
        return self.state / GEMINI_SWITCH_NAME

    @property
    def hermes_locations(self):
        import hermes_patches
        return hermes_patches.Locations.for_home(self.hermes_home)

    @property
    def speech_dir(self) -> Path:
        return self.state / "speech"


def kind_of(a: agents.Agent) -> str:
    if a.profile:
        return HERMES
    return a.target if a.target in (CLAUDE, GEMINI, CODEX) else OTHER


def grid_agents(cfg: dict) -> list[agents.Agent]:
    """The rows, in the order Peter reads them: Hermes agents, then Claude, Gemini, Codex, then anything
    new. A new [agents.*] block in agents.toml becomes a row by itself."""
    rows = [a for a in agents.agents(cfg) if a.profile not in SKIP_PROFILES]
    return sorted(rows, key=lambda a: _ORDER[kind_of(a)])      # sorted() is stable: file order inside a kind


# -- small readers ---------------------------------------------------------------------------------
def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _yaml_value(profiles: Path, profile: str, dotted: str):
    """One value from a profile's config.yaml, read by voicecfg's own line reader (no YAML library).
    Returns (found, value)."""
    try:
        text = (Path(profiles) / profile / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return False, None
    e = voicecfg.scan(text)
    if dotted not in e:
        return False, None
    return True, voicecfg.parse_value(e[dotted].value)


def _conf_value(path: Path, name: str) -> Optional[str]:
    try:
        found = voicecfg._conf_scan(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None
    return found[name][1] if name in found else None


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def gemini_hook_honours_switch(p: Paths) -> bool:
    """True when Gemini's hook source mentions the switch file's name (step 4 puts the check there)."""
    try:
        return GEMINI_SWITCH_NAME in p.gemini_hook.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


# -- one cell per place -------------------------------------------------------------------------
def terminal_cell(a: agents.Agent, p: Paths) -> Cell:
    kind = kind_of(a)
    if kind == HERMES:
        ok, v = _yaml_value(p.profiles, a.profile, "voice.auto_enable")
        if not ok and not (p.profiles / a.profile / "config.yaml").exists():
            return Cell(UNKNOWN, f"no config.yaml for profile {a.profile}")
        if v is True:
            return Cell(ALWAYS, "voice.auto_enable: the window starts with voice on (per window, in memory)")
        return Cell(OFF, "voice starts off in the window (voice.auto_enable is not true); /voice on turns it on per window")
    if kind == CLAUDE:
        settings = _read_json(p.claude_dir / "settings.json") or {}
        enabled = settings.get("voice", {})
        enabled = enabled.get("enabled") if isinstance(enabled, dict) else None
        if enabled in (None, False):                  # the hooks test `(.voice.enabled // .voiceEnabled) == true`, and jq's `//` skips a false
            legacy = settings.get("voiceEnabled")
            enabled = legacy if legacy is not None else enabled
        always_on = _conf_value(p.claude_conf, "VOICE_ALWAYS_ON")
        problem = "" if p.claude_hook.exists() else "speak-response.sh hook is missing"
        if enabled is True:
            return Cell(ALWAYS, "settings.json voice.enabled (global, no per-project switch)"
                        + ("; VOICE_ALWAYS_ON=1 keeps it on" if always_on == "1" else ""), problem)
        return Cell(OFF if enabled is False else UNKNOWN, "settings.json voice.enabled is " + str(enabled).lower(), problem)
    if kind == GEMINI:
        if not p.gemini_hook.exists():
            return Cell(OFF, "no Gemini speak hook found")
        honours = gemini_hook_honours_switch(p)
        en = _conf_value(p.gemini_hook.parent / "voice-defaults.conf", "NEURAL_VOICE_EN")
        zh = _conf_value(p.gemini_hook.parent / "voice-defaults.conf", "NEURAL_VOICE_ZH")
        spoken = (f"Gemini's own Stop hook speaks every reply with the voices Voice Desk writes for it "
                  f"({en or 'none'} / {zh or 'none'})")
        if not a.voice:
            spoken += ("; agents.toml gives Gemini no voice, but its hook then uses its own built-in voice: switch "
                       "this cell Off to keep it silent")
        if p.gemini_switch.exists():
            if honours:
                return Cell(OFF, f"{GEMINI_SWITCH_NAME} exists in ~/.voice_desk and Gemini's hook honours it: it stays silent")
            return Cell(ALWAYS, f"{GEMINI_SWITCH_NAME} says Off, but the hook does not read it yet", problem=(
                "the Off switch is set but Gemini's hook does not read it yet (step 4): Gemini still speaks"))
        return Cell(ALWAYS, spoken + ("; the hook honours the off-switch file" if honours else
                                      f"; the off-switch file ({GEMINI_SWITCH_NAME}) is prepared, the hook reads it from step 4"))
    if kind == CODEX:
        return Cell(NA, "Codex has no speech hook of its own; it speaks only through Voice Desk")
    if kind == OTHER:
        return Cell(NA, VOICE_ONLY_NOTE)
    return Cell(UNKNOWN, "unknown kind of agent")


def tg_dm_cell(a: agents.Agent, p: Paths) -> Cell:
    if kind_of(a) == OTHER:
        return Cell(NA, VOICE_ONLY_NOTE)
    if kind_of(a) != HERMES:
        return Cell(NA, "no Telegram for this agent")
    key = f"{a.profile}:telegram:{p.tg_dm_chat}"
    modes = _read_json(p.voice_mode_file) or {}
    mode = modes.get(key)
    if mode in _VOICE_MODE_TO_CELL:
        import hermes_patches
        live = hermes_patches.live_voice_mode(p.hermes_locations)
        how = {hermes_patches.LIVE: "the gateway re-reads it when it changes",
               hermes_patches.PENDING: "the gateway has the live re-read on disk but has not restarted yet, so it reads this only when it starts",
               }.get(live, "read only when the gateway starts")
        return Cell(_VOICE_MODE_TO_CELL[mode], f"gateway_voice_mode.json says {mode} ({how})")
    ok, auto = _yaml_value(p.profiles, a.profile, "voice.auto_tts")
    if auto is True:
        return Cell(ALWAYS, "no saved chat mode, so voice.auto_tts: true decides (a chat mode, once set, wins)")
    if ok:
        return Cell(OFF, "no saved chat mode and voice.auto_tts is not true")
    return Cell(UNKNOWN, f"cannot read voice.auto_tts for {a.profile}")


def simba_group_cell(p: Paths) -> Cell:
    """Simba's Team_Room voice notes (2026-11-10): her listener sends her replies and chip-ins there as voice notes
    in her Voice Desk voice while panbear-daily's simba.yaml says voice_notes.enabled: true. Shown, never switched."""
    if p.simba_yaml is None:
        return Cell(NA, VOICE_ONLY_NOTE)
    try:
        e = voicecfg.scan(Path(p.simba_yaml).read_text(encoding="utf-8"))
    except OSError:
        return Cell(UNKNOWN, f"cannot read {p.simba_yaml} (Simba's own settings, panbear-daily)")
    on = "voice_notes.enabled" in e and voicecfg.parse_value(e["voice_notes.enabled"].value) is True
    cap = voicecfg.parse_value(e["voice_notes.max_per_day"].value) if "voice_notes.max_per_day" in e else "?"
    if on:
        return Cell(VOICE_BACK, f"simba.yaml voice_notes.enabled: her Team_Room replies and chip-ins also go out as "
                                f"voice notes from her own bot, in this voice, English only, at most {cap} a day "
                                "(panbear-daily simba.py; switched there, not here)")
    return Cell(OFF, "simba.yaml voice_notes.enabled is not true: her Team_Room replies are text only "
                     "(panbear-daily simba.py; switched there, not here)")


def tg_group_cell(a: agents.Agent, p: Paths) -> Cell:
    if kind_of(a) == OTHER:
        if a.key == "simba":
            return simba_group_cell(p)
        return Cell(NA, VOICE_ONLY_NOTE)
    if kind_of(a) != HERMES:
        return Cell(NA, "no Telegram for this agent")
    data = _read_json(p.mirror_config)
    if data is None:
        return Cell(UNKNOWN, "team-room mirror_config.json not readable")
    vn = data.get("voice_notes")
    if not isinstance(vn, dict):
        return Cell(OFF, "mirror_config.json has no voice_notes section (voice notes are shipped switched off)")
    listed = a.profile in (vn.get("agents") or [])
    if vn.get("on") is True and listed:
        return Cell(ALWAYS, "mirror_config.json voice_notes.on (re-read on every line)")
    return Cell(OFF, "mirror_config.json voice_notes.on is "
                + ("true but this agent is not in its agents list" if vn.get("on") is True else "off"))


def mic_cell(a: agents.Agent, p: Paths) -> Cell:
    if kind_of(a) == OTHER:                                             # not a brain: "hello Simba" wakes nothing
        return Cell(NA, VOICE_ONLY_NOTE)
    queue = " — speech queue is off (windows play straight)" if (p.speech_dir / "off").exists() else ""
    return Cell(VOICE_BACK, "Voice Desk answers aloud when Peter speaks to it, in this agent's own voice "
                            "(none set = silent)" + queue)


def iphone_cell(a: agents.Agent, p: Paths) -> Cell:
    if kind_of(a) == OTHER:
        return Cell(NA, VOICE_ONLY_NOTE)
    shortcut = p.repo / f"Talk to {a.display}.shortcut"        # only the NAME is looked at, never its contents
    note = "the Mac sends text; Siri reads it in Siri's own voice"
    if not shortcut.exists() and not (kind_of(a) == HERMES and (p.repo / "Talk to Orchestrator.shortcut").exists()):
        return Cell(UNKNOWN, "no Siri shortcut for this agent in the repo")
    return Cell(VOICE_BACK, note)


CELL_FUNCS = {"terminal": terminal_cell, "tg_dm": tg_dm_cell, "tg_group": tg_group_cell,
              "mic": mic_cell, "iphone": iphone_cell}


def cells_for(a: agents.Agent, p: Paths) -> dict[str, Cell]:
    return {place: fn(a, p) for place, fn in CELL_FUNCS.items()}


# -- voices and the optional per-place fields stored in agents.toml --------------------------------
@dataclass(frozen=True)
class VoiceInfo:
    en: str                          # "" = English lines are silent (no voice set: there is no shared voice)
    zh: str                          # "" = Chinese lines are silent (no Mandarin voice, or English only)
    extra: dict
    override: str
    effect: Optional[str] = None     # Simba's sound effect on top of her Kokoro voice (F key)

    @property
    def silent(self) -> bool:
        return not self.en and not self.zh


def voice_info(a: agents.Agent, cfg: dict) -> VoiceInfo:
    """Exactly the voices the agent speaks with (voices.resolve): one per language, none = silent."""
    import voices
    v = voices.resolve(a)
    return VoiceInfo(v.en, v.zh, dict(a.voices_extra), a.voice_override, a.effect)


QUIET_PLACES = set(PLACE_KEYS)


def quiet_hours(a: agents.Agent) -> dict[str, str]:
    """{place: "22:00-07:00"} from the agent's optional `quiet_hours` table. Stored and shown only;
    nothing enforces it yet. Entries for unknown places are ignored."""
    return {k: str(v) for k, v in a.quiet_hours.items() if k in QUIET_PLACES}
