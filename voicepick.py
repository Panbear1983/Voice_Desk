"""The voice pick-list behind the grid's E, Z and F keys (Peter 2026-11-11: "I have to actually manually type the name
of the voice in order to switch").

A short list of the voices the agent's own speaker can really make, the current one marked, arrows + Enter (the
dashboard shows it with the same ChoiceScreen the on/off switches use). The last line is always "other, type it",
which opens the old typing box. Picking a line goes through voiceedit.change like every other edit: one agents.toml
line, the agent's own files, a backup, a read-back, a history line, and U undoes it.

    E  English voice   Kokoro voices (local, no internet) then Microsoft Edge voices; Gemini: Edge only (its own
                       hook speaks Edge only); Esther and Jarvis: Kokoro only (their own Kokoro scripts); Simba:
                       American Kokoro voices only, none that another agent already has (her english_only rule)
    Z  Mandarin voice  the Taiwan Mandarin voices only (zh-TW); refused for an English-only agent
    F  voice effect    Simba only: Jarvis's numbered samples and the named starting points

"silent" is a line of its own (Peter 2026-11-11: an agent with no voice is silent; there is no shared voice).
This module only builds the lists; it writes nothing.
"""
from __future__ import annotations

from typing import Optional

import agents
import onoff
import voiceedit

OTHER = "::other"          # "other, type it": the old typing box
SILENT = "::silent"        # no voice for this language

# Microsoft Edge English voices offered on the list (Peter 2026-11-11: no Jenny). Any other Edge name can still be
# typed on the "other" line.
EDGE_EN = (
    ("en-US-AvaNeural", "Ava, American female"), ("en-US-EmmaNeural", "Emma, American female"),
    ("en-US-AriaNeural", "Aria, American female"), ("en-US-MichelleNeural", "Michelle, American female"),
    ("en-US-AndrewNeural", "Andrew, American male"), ("en-US-BrianNeural", "Brian, American male"),
    ("en-US-GuyNeural", "Guy, American male"), ("en-US-ChristopherNeural", "Christopher, American male"),
    ("en-US-EricNeural", "Eric, American male"), ("en-GB-RyanNeural", "Ryan, British male"),
    ("en-GB-SoniaNeural", "Sonia, British female"),
)
# Taiwan Mandarin (Peter 2026-11-09: Taiwan accent only): the three zh-TW voices Edge has.
TAIWAN = (("zh-TW-HsiaoChenNeural", "HsiaoChen 曉臻, female"), ("zh-TW-HsiaoYuNeural", "HsiaoYu 曉雨, female"),
          ("zh-TW-YunJheNeural", "YunJhe 雲哲, male"))
_KOKORO_KIND = {"af": "American female", "am": "American male", "bf": "British female", "bm": "British male"}


def kokoro_label(name: str) -> str:
    kind, _, who = name.partition("_")
    return f"{who.capitalize()}, {_KOKORO_KIND.get(kind, kind)} · Kokoro, local"


def _same_voice(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a) and bool(b) and voiceedit._kokoro_name(a) == voiceedit._kokoro_name(b)


def _finish(rows: list[tuple[str, str]], current: Optional[str], silent_label: str) -> list[onoff.Option]:
    """Rows -> options with the current one marked (put on top, marked, if it is not on the list), then the
    silent line, then "other, type it" last."""
    out = [onoff.Option(token, label, _same_voice(token, current)) for token, label in rows]
    if current and not any(o.current for o in out):
        out.insert(0, onoff.Option(current, f"{current}   (now; not on this list)", True))
    out.append(onoff.Option(SILENT, silent_label, not current))
    out.append(onoff.Option(OTHER, "other, type it…"))
    return out


def english(agent: agents.Agent, cfg: dict) -> list[onoff.Option]:
    if voiceedit.uses_simba_script(agent):
        import simba_tts
        taken = {voiceedit._kokoro_name(a.voice): a.display for a in agents.agents(cfg) if a.key != agent.key and a.voice}
        rows = [(v, kokoro_label(v)) for v in simba_tts.VOICES if voiceedit._kokoro_name(v) not in taken]
        return _finish(rows, agent.voice, "silent — no voice: her lines are not spoken")
    kokoro = [(v, kokoro_label(v)) for v in agents.KOKORO_VOICES]
    edge = [(v, f"{label} · Microsoft Edge, online") for v, label in EDGE_EN]
    if agent.target == "gemini":
        rows = edge                                   # Gemini's own hook speaks Edge voices only
    elif agent.command:
        rows = kokoro                                 # Esther / Jarvis: their own Kokoro script
    else:
        rows = kokoro + edge
    return _finish(rows, agent.voice, "silent — no English voice: English lines are not spoken")


def mandarin(agent: agents.Agent) -> list[onoff.Option]:
    return _finish(list(TAIWAN), agent.voice_zh, "silent — no Mandarin voice: Chinese lines are not spoken")


def effect(agent: agents.Agent) -> list[onoff.Option]:
    import simba_tts
    rows = [(str(n), f"sample {n} — first made with {v}") for n, (v, _) in sorted(simba_tts.SAMPLES.items())]
    rows += [(name, f"{name} — named starting point") for name in simba_tts.PRESETS]
    current = agent.effect
    out = [onoff.Option(token, label, bool(current) and simba_tts.clean_effect(current) == token) for token, label in rows]
    if current and not any(o.current for o in out):
        out.insert(0, onoff.Option(current, f"{current}   (now: {simba_tts.describe(current)})", True))
    out.append(onoff.Option(OTHER, "other, type it… (a number, then changes: \"23 but slower and breathier\")"))
    return out


def menu(agent: agents.Agent, field_name: str, cfg: dict) -> tuple[str, list[onoff.Option], str]:
    """(title, options, why_not). why_not is set (and options empty) when the key does not apply to this agent."""
    if field_name == "voice":
        return f"{agent.display} — English voice (one voice, no backup)", english(agent, cfg), ""
    if field_name == "voice_zh":
        if agent.english_only:
            return "", [], (f"{agent.display} speaks English only (english_only in agents.toml): no Mandarin voice, "
                            "a Chinese line stays silent")
        return f"{agent.display} — Mandarin voice (Taiwan, one voice, no backup)", mandarin(agent), ""
    if field_name == "effect":
        if not voiceedit.uses_simba_script(agent):
            return "", [], f"{agent.display} has no voice effect (only Simba's Kokoro voice has one)"
        return f"{agent.display} — voice effect on {agent.voice}", effect(agent), ""
    return "", [], f"{field_name} has no pick-list"
