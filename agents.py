"""Read agents.toml: every agent's names, voices and voice-mode settings in one file.

Standard library only (tomllib); this module imports nothing from Voice Desk, so settings.py,
voices.py and voicecfg.py can all use it.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PATH = Path(os.environ.get("VOICE_DESK_AGENTS", Path(__file__).resolve().parent / "agents.toml"))

# Kokoro, the local voice (no internet; Peter 2026-11-11: Claude's default, and a choice for any agent).
# The 28 English voices in kokoro-v1.0: af_/am_ American female/male, bf_/bm_ British female/male.
KOKORO_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george",
    "bm_lewis")
# The shared script that speaks any of them: <python> <script> <text file> <out file> <voice>
KOKORO_COMMAND = (os.path.expanduser(os.environ.get("VOICE_DESK_KOKORO_PY", "~/.local/share/claude-voice/venv/bin/python")),
                  os.path.expanduser(os.environ.get("VOICE_DESK_KOKORO_SCRIPT", "~/.claude/hooks/kokoro_say.py")))


def is_kokoro(voice: Optional[str]) -> bool:
    return bool(voice) and voice in KOKORO_VOICES


KNOBS = ("auto_tts", "silence_duration", "silence_threshold", "barge_in", "barge_in_grace_seconds")


@dataclass(frozen=True)
class Agent:
    key: str                         # the [agents.<key>] name
    target: str                      # brain target: "claude", "gemini", "codex" or "hermes:<profile>"
    display: str                     # the name spoken back
    names: tuple[str, ...]           # every spelling whisper produces for it
    voice: Optional[str] = None
    voice_zh: Optional[str] = None
    speed: Optional[float] = None
    command: tuple[str, ...] = ()    # a non-Edge voice is spoken through this script
    fallback: Optional[str] = None   # Edge voice used if that script fails
    effect: Optional[str] = None     # the script's sound effect, passed after the voice (Simba: a recipe such as "16")
    knobs: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    voices_extra: dict = field(default_factory=dict)    # more languages: {"ja": "ja-JP-NanamiNeural"}
    voice_override: str = "auto"     # "auto" = follow the reply's language; "en", "zh" or an extra code pins it
    quiet_hours: dict = field(default_factory=dict)     # place -> "22:00-07:00" (stored and shown only)
    english_only: bool = False       # speaks English only: no Mandarin voice, a Chinese line stays silent (Simba)

    @property
    def profile(self) -> Optional[str]:
        return self.target.split(":", 1)[1] if self.target.startswith("hermes:") else None


def load(path: Optional[Path] = None) -> dict:
    with open(path or PATH, "rb") as fh:
        return tomllib.load(fh)


def _agent(key: str, raw: dict) -> Agent:
    target = f"hermes:{raw['profile']}" if "profile" in raw else raw["target"]
    return Agent(
        key=key, target=target, display=raw["display"], names=tuple(raw.get("names", ())),
        voice=raw.get("voice"), voice_zh=raw.get("voice_zh"), speed=raw.get("speed"),
        command=tuple(os.path.expanduser(c) for c in raw.get("command", ())), fallback=raw.get("fallback"),
        effect=raw.get("effect"),
        knobs={k: raw[k] for k in KNOBS if k in raw}, raw=raw,
        voices_extra=dict(raw.get("voices_extra") or {}), voice_override=str(raw.get("voice_override") or "auto"),
        quiet_hours=dict(raw.get("quiet_hours") or {}), english_only=raw.get("english_only") is True,
    )


def agents(cfg: dict) -> list[Agent]:
    return [_agent(k, v) for k, v in cfg.get("agents", {}).items()]


def names(cfg: dict) -> dict[str, tuple[str, tuple[str, ...]]]:
    """target -> (spoken name, spellings): the shape settings.NAMES always had."""
    return {a.target: (a.display, a.names) for a in agents(cfg)}


def greetings(cfg: dict) -> list[str]:
    return list(cfg["shared"]["greetings"])


def whisper_prompt(cfg: dict) -> str:
    return cfg["shared"]["whisper_prompt"]


def shared(cfg: dict) -> dict[str, Any]:
    return cfg.get("shared", {})


def mic(cfg: dict) -> dict[str, Any]:
    """[shared.mic] as the settings.py constant names (pre_roll_sec -> PRE_ROLL_SEC)."""
    return {k.upper(): v for k, v in shared(cfg).get("mic", {}).items()}


def _spelled(phrase: str, spellings: dict[str, list[str]]) -> list[str]:
    """Every spelling of a phrase: "that's all" -> that's all, that is all, that’s all."""
    out = [phrase]
    for word, forms in spellings.items():
        out = [p.replace(word, f, 1) for p in out for f in (forms if word in p else [word])]
    return out


def end_phrases(cfg: dict) -> list[str]:
    """voice.stop_phrases for every Hermes profile, generated from [shared.end_phrases]:
    always + "<lead-in>, <phrase>" for every phrase, lead-in and spelling. Hermes matches them as
    a set, so voicecfg compares sets and only the strings matter, not their order."""
    ep = shared(cfg).get("end_phrases", {})
    out = list(ep.get("always", []))
    for phrase in ep.get("phrases", []):
        for lead in ep.get("lead_ins", [""]):
            for p in _spelled(phrase, ep.get("spellings", {})):
                s = f"{lead}, {p}" if lead else p
                if s not in out:
                    out.append(s)
    return out
