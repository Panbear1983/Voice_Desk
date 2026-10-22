"""Who speaks with which voice.

Peter 2026-10-21: "once I start activating Voice Desk by 'hey someone', the voice should also
correspond to their profile setting". Since 2026-10-22 the voices live in agents.toml (Peter:
one file in Voice Desk owns every agent's voice), and `python -m voicecfg sync` writes the same
values into each Hermes profile, so "hey Wanna Buffet" answers as Andrew and "hey Kylie" as Ava,
exactly as in their own windows and on Telegram. Esther keeps her Kokoro voice everywhere: her
lines go through her own script (tax_angel_esther/scripts/esther_tts.py), with an Edge voice only
as the fallback if Kokoro fails.

agents.toml is read fresh before every line (a changed voice needs no restart); a file broken
mid-edit keeps the last good values.

Per-profile voices can be switched off from the dashboard (V key): then everyone speaks with
the shared Voice Desk voice (agents.toml [shared] voice). The switch is a file so the listener,
the receiver and the dashboard all see the same answer without a restart.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional

import agents
import settings as S

PER_PROFILE_FILE = S.STATE_DIR / "voices_per_profile"       # exists = off (default on)

_EDGE_VOICE = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural$")
# Mandarin sibling for an English Edge voice when agents.toml names none; anything unlisted gets
# HsiaoChen (female) or YunJhe (male) by the English voice's gender.
_ZH_FOR = {
    "en-US-AvaNeural": "zh-TW-HsiaoChenNeural",
    "en-US-JennyNeural": "zh-TW-HsiaoChenNeural",
    "en-US-AriaNeural": "zh-TW-HsiaoYuNeural",
    "en-US-AndrewNeural": "zh-TW-YunJheNeural",
}
_MALE = {"en-US-AndrewNeural", "en-US-BrianNeural", "en-US-GuyNeural", "en-US-ChristopherNeural",
         "en-US-EricNeural", "en-US-RogerNeural", "en-US-SteffanNeural"}


class Voice(NamedTuple):
    en: str                          # Edge voice name for English (or the script's own voice name)
    zh: str                          # Edge voice name for Mandarin
    source: str                      # "agents" (agents.toml), "fallback" or "shared"
    command: tuple[str, ...] = ()    # non-Edge voice: English lines are made by this script
    fallback: Optional[str] = None   # Edge voice if that script fails


def is_edge(voice: Optional[str]) -> bool:
    return bool(voice and _EDGE_VOICE.match(voice))


def zh_for(en: str) -> str:
    return _ZH_FOR.get(en, "zh-TW-YunJheNeural" if en in _MALE else "zh-TW-HsiaoChenNeural")


_last_good: Optional[dict] = None


def _cfg() -> dict:
    """agents.toml as it is now; the last good copy if it is broken mid-edit."""
    global _last_good
    try:
        _last_good = agents.load()
    except (OSError, ValueError):
        if _last_good is None:
            _last_good = S.AGENTS
    return _last_good


def per_profile_enabled() -> bool:
    return not PER_PROFILE_FILE.exists()


def set_per_profile(on: bool) -> None:
    if on:
        try:
            PER_PROFILE_FILE.unlink()
        except OSError:
            pass
    else:
        PER_PROFILE_FILE.write_text("off\n", encoding="utf-8")


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


def shared_voice() -> Voice:
    sh = agents.shared(_cfg())
    en = sh.get("voice", S.TTS_VOICE_EN)
    return Voice(en, sh.get("voice_zh") or zh_for(en), "shared")


def voice_for(target: str) -> Voice:
    """The voice for a brain target ("hermes:<profile>", "claude", "gemini")."""
    if not per_profile_enabled() or not target.startswith("hermes:"):
        return shared_voice()
    a = next((a for a in agents.agents(_cfg()) if a.target == target), None)
    if a is None or not a.voice:
        return shared_voice()
    if is_edge(a.voice):
        return Voice(a.voice, a.voice_zh or zh_for(a.voice), "agents")
    if a.command:
        return Voice(a.voice, a.voice_zh or zh_for(a.fallback or ""), "agents", a.command, a.fallback)
    if a.fallback:
        return Voice(a.fallback, a.voice_zh or zh_for(a.fallback), "fallback")
    return shared_voice()


def table() -> list[tuple[str, Voice]]:
    """(spoken name, voice) for every Hermes agent in agents.toml, for the dashboard."""
    return [(a.display, voice_for(a.target)) for a in agents.agents(_cfg()) if a.target.startswith("hermes:")]


def short(v: Voice) -> str:
    """'Ava · HsiaoChen' from the Edge names; 'Kokoro alloy · HsiaoYu' for a script voice."""
    def nm(x: str) -> str:
        m = re.match(r"^[a-z]{2}-[A-Z]{2}-([A-Za-z]+?)(Multilingual)?Neural$", x)
        return m.group(1) if m else x
    return f"{'Kokoro ' if v.command else ''}{nm(v.en)} · {nm(v.zh)}"
