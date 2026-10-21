"""Who speaks with which voice.

Peter 2026-10-21: "once I start activating Voice Desk by 'hey someone', the voice should also
correspond to their profile setting". Each Hermes profile keeps its own voice in its
config.yaml (tts.providers.<provider>.voice / command); this module reads it so that
"hey Wanna Buffet" answers as Andrew and "hey Kylie" as Ava, exactly as on Telegram.

Per-profile voices can be switched off from the dashboard (V key): then everyone speaks with
the shared Voice Desk voice (settings.TTS_VOICE_EN), as before. The switch is a file so the
listener, the receiver and the dashboard all see the same answer without a restart.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple, Optional

import settings as S

PER_PROFILE_FILE = S.STATE_DIR / "voices_per_profile"       # exists = off (default on)

# A profile voice is only honoured when it comes through the one TTS chain this Mac can run
# fast (Microsoft Edge neural voices via a `command` provider). Esther's Kokoro server and the
# OpenAI names (alloy, nova, onyx) are not Edge voices; they fall back to a chosen Edge voice.
_EDGE_VOICE = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural$")
_NON_EDGE_FALLBACK = {
    "tax_angel_esther": "en-US-AriaNeural",    # nearest Edge voice to Esther's warm Kokoro voice
}
# Mandarin sibling for each English Edge voice; anything unlisted gets HsiaoChen (female) or
# YunJhe (male) by the English voice's gender.
_ZH_FOR = {
    "en-US-AvaNeural": "zh-TW-HsiaoChenNeural",
    "en-US-JennyNeural": "zh-TW-HsiaoChenNeural",
    "en-US-AriaNeural": "zh-TW-HsiaoYuNeural",
    "en-US-AndrewNeural": "zh-TW-YunJheNeural",
}
_MALE = {"en-US-AndrewNeural", "en-US-BrianNeural", "en-US-GuyNeural", "en-US-ChristopherNeural",
         "en-US-EricNeural", "en-US-RogerNeural", "en-US-SteffanNeural"}


class Voice(NamedTuple):
    en: str              # Edge voice name for English
    zh: str              # Edge voice name for Mandarin
    source: str          # "profile" (read from the Hermes config), "fallback" or "shared"


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
    library (the file may carry anchors Hermes' own loader resolves)."""
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
    return Voice(S.TTS_VOICE_EN, _ZH_FOR.get(S.TTS_VOICE_EN, "zh-TW-HsiaoChenNeural"), "shared")


def voice_for(target: str) -> Voice:
    """The voice for a brain target ("hermes:<profile>", "claude", "gemini")."""
    if not per_profile_enabled() or not target.startswith("hermes:"):
        return shared_voice()
    profile = target.split(":", 1)[1]
    v = _config_voice(profile)
    if v and _EDGE_VOICE.match(v):
        return Voice(v, _ZH_FOR.get(v, "zh-TW-YunJheNeural" if v in _MALE else "zh-TW-HsiaoChenNeural"), "profile")
    fb = _NON_EDGE_FALLBACK.get(profile)
    if fb:
        return Voice(fb, _ZH_FOR.get(fb, "zh-TW-HsiaoChenNeural"), "fallback")
    return shared_voice()


def table() -> list[tuple[str, Voice]]:
    """(spoken name, voice) for every Hermes profile Voice Desk knows, for the dashboard."""
    return [(name, voice_for(t)) for t, (name, _) in S.NAMES.items() if t.startswith("hermes:")]


def short(v: Voice) -> str:
    """'Ava · HsiaoChen' from the Edge names."""
    def nm(x: str) -> str:
        m = re.match(r"^[a-z]{2}-[A-Z]{2}-([A-Za-z]+?)(Multilingual)?Neural$", x)
        return m.group(1) if m else x
    return f"{nm(v.en)} · {nm(v.zh)}"
