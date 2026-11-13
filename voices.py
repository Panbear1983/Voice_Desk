"""Who speaks with which voice.

Peter 2026-10-21: "once I start activating Voice Desk by 'hey someone', the voice should also
correspond to their profile setting". Since 2026-10-22 the voices live in agents.toml (Peter:
one file in Voice Desk owns every agent's voice), and `python -m voicecfg sync` writes the same
values into each Hermes profile, so every agent answers in Voice Desk exactly as in its own window
and on Telegram. Esther, Jarvis and Simba keep their Kokoro voice everywhere: their lines go through
their own script.

ONE voice per language, no fallback, empty = silent (Peter 2026-11-11: "If the primary voice ... did
not work ... it's just gonna stay silent, so that there is no 2nd voice automatically switched on";
agents with no voice "can just be empty ... such as builder"):
    - an agent's English line is spoken with its `voice` and nothing else;
    - its Mandarin line with its `voice_zh` and nothing else (no voice_zh, or english_only: silent);
    - an agent with no voice, or a target that is not in agents.toml, is silent.
There is no shared Voice Desk voice any more, and no V key to switch everyone to one.

agents.toml is read fresh before every line (a changed voice needs no restart); a file broken
mid-edit keeps the last good values.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional

import agents
import settings as S

_EDGE_VOICE = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural$")


class Voice(NamedTuple):
    en: str                          # English voice: an Edge name, a Kokoro name or the script's own name; "" = silent
    zh: str                          # Mandarin Edge voice; "" = a line with Chinese in it stays silent
    source: str                      # "agents" (agents.toml) or "silent" (no voice set, or not in agents.toml)
    command: tuple[str, ...] = ()    # non-Edge voice: English lines are made by this script, and only by it
    effect: Optional[str] = None     # passed to that script after the voice (Simba's effect recipe, e.g. "16")


SILENT = Voice("", "", "silent")


def is_edge(voice: Optional[str]) -> bool:
    return bool(voice and _EDGE_VOICE.match(voice))


_last_good: Optional[dict] = None


def config() -> dict:
    """agents.toml as it is now; the last good copy if it is broken mid-edit."""
    global _last_good
    try:
        _last_good = agents.load()
    except (OSError, ValueError):
        if _last_good is None:
            _last_good = S.AGENTS
    return _last_good


def _config_voice(profile: str) -> Optional[str]:
    """The `voice:` of the active tts provider in the profile's config.yaml, without a YAML
    library (the file may carry anchors Hermes' own loader resolves). voicecfg check compares
    this with agents.toml."""
    cfg = S.HERMES_PROFILES_DIR / profile / "config.yaml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^tts:\n((?:[ \t]+.*\n|\n)*)", text, re.M)
    if not m:
        return None
    block = m.group(1)
    prov = re.search(r"^\s*provider:\s*(\S+)", block, re.M)
    provider = prov.group(1).strip("'\"") if prov else "edge"
    sect = re.search(rf"^\s+{re.escape(provider)}:\n((?:[ \t]{{4,}}.*\n|\n)*)", block, re.M)
    if sect:
        v = re.search(r"^\s+voice:\s*(\S+)", sect.group(1), re.M)
        if v:
            return v.group(1).strip("'\"")
    v = re.search(r"^\s+voice:\s*(\S+)", block, re.M)
    return v.group(1).strip("'\"") if v else None


def voice_for(target: str) -> Voice:
    """The voice for a brain target ("hermes:<profile>", "claude", "gemini", "codex", "simba"): exactly the one in
    agents.toml. No voice set, or a target agents.toml does not know, is SILENT (there is no shared voice)."""
    return resolve(next((a for a in agents.agents(config()) if a.target == target), None))


def resolve(a: Optional[agents.Agent]) -> Voice:
    """One agent's voice, by the rule above (the dashboard and the silent test use this on their own copy of
    agents.toml). English and Mandarin are separate: an agent with a Mandarin voice but no English one speaks
    Mandarin lines only."""
    if a is None:
        return SILENT
    zh = "" if a.english_only else (a.voice_zh or "")
    if not a.voice:
        return Voice("", zh, "agents" if zh else "silent")
    if is_edge(a.voice):
        return Voice(a.voice, zh, "agents")
    if a.command:                                   # Esther, Jarvis, Simba: their own script, nothing else
        return Voice(a.voice, zh, "agents", a.command, a.effect)
    if agents.is_kokoro(a.voice):                   # any other agent with a Kokoro voice: the shared Kokoro script
        return Voice(a.voice, zh, "agents", agents.KOKORO_COMMAND)
    return Voice(a.voice, zh, "agents")             # a name nothing can speak: tts asks Edge for it, then silence


def table() -> list[tuple[str, Voice]]:
    """(spoken name, voice) for every Hermes agent in agents.toml, for the dashboard."""
    return [(a.display, voice_for(a.target)) for a in agents.agents(config()) if a.target.startswith("hermes:")]


def short(v: Voice) -> str:
    """'Ava · HsiaoChen' from the Edge names; 'Kokoro alloy · HsiaoYu' for a script voice; 'silent' for none."""
    if not v.en and not v.zh:
        return "silent (no voice set)"

    def nm(x: str) -> str:
        m = re.match(r"^[a-z]{2}-[A-Z]{2}-([A-Za-z]+?)(Multilingual)?Neural$", x)
        return m.group(1) if m else x
    en = f"{'Kokoro ' if v.command else ''}{nm(v.en)}" if v.en else "English silent"
    return f"{en} · {nm(v.zh) if v.zh else 'English only'}"
