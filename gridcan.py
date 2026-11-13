"""What each place can really do for each agent (command-center brief 2026-11-13, stage 1: "dashboard shows what each
place can really do for each agent").

For every cell, in plain words:

    speaks   which voice really speaks there for this agent, read from the file that place really uses (the Hermes
             profile's own tts provider, Claude's and Gemini's voice-defaults.conf, agents.toml for Voice Desk itself,
             Siri on the iPhone), and whether the agent's own hook still has a backup voice (the Mac voice).
    voice    whether the E / Z keys reach that place (Voice Desk writes the voice there) or not.
    change   what Enter does there (the switch and its choices), or why the cell is view only.

Read-only: nothing is written, played or started. The answers come from the same readers the grid uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import agents
import gridstate as G
import voicecfg

_SAY_CALL = re.compile(r"""\[\s*["']say["']""")      # a hook that runs the Mac's `say` (its backup voice)


@dataclass(frozen=True)
class Can:
    speaks: str            # "" = nothing speaks there for this agent
    voice: str             # do E / Z reach this place
    change: str            # "Enter: Off / Always", or "view only: <why>"


def _hook_has_mac_backup(path: Path) -> bool:
    try:
        return bool(_SAY_CALL.search(Path(path).read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def profile_voice(a: agents.Agent, p: G.Paths) -> tuple[Optional[str], Optional[str]]:
    """(provider, voice) the agent's Hermes profile really speaks with (its active tts provider)."""
    prof = a.profile or ""
    ok, prov = G._yaml_value(p.profiles, prof, "tts.provider")
    prov = str(prov) if ok and prov else "edge"
    for key in (f"tts.providers.{prov}.voice", f"tts.{prov}.voice"):
        ok, v = G._yaml_value(p.profiles, prof, key)
        if ok and v:
            return prov, str(v)
    return prov, None


def _desk_voice(a: agents.Agent) -> str:
    import voices
    v = voices.resolve(a)
    if not v.en and not v.zh:
        return "nothing: no voice set, so Voice Desk stays silent for it"
    how = ("its own script" if a.command else "the local Kokoro script") if v.command else "Microsoft Edge, exactly that voice"
    en = f"{v.en} ({how})" if v.en else "English lines silent"
    zh = f"Mandarin {v.zh}" if v.zh else "Chinese lines silent"
    return f"Voice Desk speaks {en}, {zh}; if it fails the line is silent"


def _reaches(a: agents.Agent, prov: Optional[str], have: Optional[str]) -> str:
    if not a.voice:
        return "E gives it a voice in agents.toml; its profile keeps its own until then"
    if have and voicecfg.same("voice", have, a.voice):
        return "E changes it here too (written into its profile, backed up, undo with U)"
    if agents.is_kokoro(a.voice) and not a.command:
        return f"E does not reach it: a Kokoro voice is not written into its profile, which speaks {have or 'nothing set'}"
    return f"E should reach it but the profile says {have or 'nothing'} (voicecfg check shows it)"


def _change(a: agents.Agent, place: str, cell: G.Cell, p: G.Paths) -> str:
    import onoff
    if place == "mic":
        return "Enter: speech queue on / off (one switch for all agents)"
    if onoff.can_switch(a, place):
        opts = onoff.menu(a, place, cell, p).options
        return "Enter: " + " / ".join(o.label.split(" — ")[0] for o in opts) if opts else "Enter switches it"
    why = onoff.menu(a, place, cell, p).why_not or "nothing to switch here"
    return f"view only: {why}"


def can(a: agents.Agent, place: str, cell: G.Cell, p: G.Paths) -> Can:
    kind = G.kind_of(a)
    change = _change(a, place, cell, p)
    if place == "mic":
        if kind == G.OTHER:
            return Can("", "", change)
        return Can(_desk_voice(a), "E / Z change it at once (agents.toml is read before every line)", change)
    if place == "iphone":
        return Can("Siri's own voice: the Mac sends text, no agent voice reaches the iPhone", "E / Z do not reach it",
                   change)
    if kind == G.HERMES:
        prov, have = profile_voice(a, p)
        if place == "tg_group" and a.key == "simba":
            return Can(f"voice notes from her own listener, made by simba_tts.py with {a.voice} + effect {a.effect}",
                       "E / F change it from her next note (her listener reads agents.toml)", change)
        if place == "tg_group" and cell.value == G.OFF and "not in its agents list" in cell.note:
            return Can("text only: the team room does not voice it", "E does not matter here", change)
        where = {"terminal": "its window speaks", "tg_dm": "its bot's voice notes are made",
                 "tg_group": "the team room's voice notes are made"}[place]
        return Can(f"{where} with its profile's voice: {have or 'none set'} (provider {prov})",
                   _reaches(a, prov, have), change)
    if place in ("tg_dm", "tg_group"):
        return Can("", "", change)
    if kind == G.CLAUDE:
        conf = p.claude_conf
        engine = G._conf_value(conf, "ENGLISH_ENGINE") or "edge"
        en = G._conf_value(conf, "KOKORO_VOICE_EN") if engine == "kokoro" else G._conf_value(conf, "NEURAL_VOICE_EN")
        zh = G._conf_value(conf, "NEURAL_VOICE_ZH")
        backup = _hook_has_mac_backup(p.claude_dir / "hooks/speak-natural.py")
        return Can(f"its own Stop hook speaks {en or 'its built-in default'} ({engine}), Mandarin {zh or 'its default'}"
                   + ("; if that fails the hook still switches to the Mac voice" if backup else "; no backup voice"),
                   "E / Z change it here too (voice-defaults.conf)", change)
    if kind == G.GEMINI:
        conf = p.gemini_hook.parent / "voice-defaults.conf"
        en, zh = G._conf_value(conf, "NEURAL_VOICE_EN"), G._conf_value(conf, "NEURAL_VOICE_ZH")
        backup = _hook_has_mac_backup(p.gemini_hook)
        return Can(f"its own Stop hook speaks Microsoft voices only: {en or 'its built-in default'} / "
                   f"{zh or 'its built-in default'}"
                   + ("; if that fails the hook still switches to the Mac voice" if backup else "; no backup voice"),
                   "E / Z change it here too, Microsoft voices only (its hook's voice-defaults.conf)", change)
    return Can("", "", change)                      # Codex terminal, a voice-only agent


def line(c: Can) -> str:
    """One plain line for the detail panel."""
    parts = [f"speaks: {c.speaks}" if c.speaks else "nothing speaks here"]
    if c.voice:
        parts.append(c.voice)
    parts.append(c.change)
    return " · ".join(parts)
