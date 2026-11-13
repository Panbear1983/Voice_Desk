"""The voice pick-list behind the grid's E, Z and F keys (Peter 2026-11-11: "I have to actually manually type the name
of the voice in order to switch").

A list of voices with the current one marked, arrows + Enter (the dashboard shows it with the same ChoiceScreen the
on/off switches use). The last line is always "other, type it", which opens the old typing box. Picking a line goes
through voiceedit.change like every other edit: one agents.toml line, the agent's own files, a backup, a read-back, a
history line, and U undoes it.

Since 2026-11-13 (command-center brief, decisions 2 and 14) the lines come from the voice bank (voicebank.py): what is
really installed, read from the Kokoro voice files and Microsoft's own list, not a list written here. EVERY agent sees
the whole bank for the language, Simba included: local Kokoro voices first, Microsoft below, then "silent", then
"other, type it". Nothing changes by itself: the current voice stays marked and stays until Peter picks another.

A line the agent's own speaker cannot make today is still shown, marked ◌ and not pickable, with the reason (decision
14: "verify her script can actually play any bank voice before offering it"). Today that is:

    Gemini          its own hook speaks Microsoft voices only            -> Kokoro lines ◌ until stage 2
    Esther, Jarvis  their own Kokoro scripts                              -> Microsoft lines ◌ until stage 2
    Simba           simba_tts.py: American Kokoro only (it refuses the rest) -> British Kokoro and Microsoft lines ◌
                    until stage 2; on a Microsoft line the note also says her sound effect would not apply to it
    English only    (Simba, Jarvis) Z shows the Taiwan voices ◌: a Chinese line stays silent for them today
    everyone else   the whole list is pickable

Voices other agents already use are no longer hidden from Simba (decision 14 lifts that limit from the list).
"silent" is a line of its own (Peter 2026-11-11: an agent with no voice is silent; there is no shared voice).
This module only builds the lists; it writes nothing.
"""
from __future__ import annotations

from typing import Optional

import agents
import onoff
import voicebank
import voiceedit

OTHER = "::other"          # "other, type it": the old typing box
SILENT = "::silent"        # no voice for this language
NOT_YET = "::not-yet:"     # token prefix of a ◌ line (disabled: it cannot be picked)


def kokoro_label(name: str) -> str:
    """'Heart, American female · Kokoro, local' for any Kokoro name (also one not in the bank)."""
    v = voicebank._kokoro_voice(name)
    return v.label if v else f"{name} · Kokoro, local"


def _same_voice(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a) and bool(b) and voiceedit._kokoro_name(a) == voiceedit._kokoro_name(b)


def not_yet(agent: agents.Agent, field_name: str, v: voicebank.BankVoice) -> str:
    """Why this agent's own speaker cannot make this bank voice today ("" = it can, the line is pickable)."""
    if field_name == "voice_zh":
        if agent.english_only:
            return f"{agent.display} is English only today"
    elif voiceedit.uses_simba_script(agent):
        import simba_tts
        if v.engine == "edge":
            return "her script: Kokoro only (stage 2); her effect would not apply"
        if simba_tts.voice_problem(v.name):
            return "her script: American Kokoro only (stage 2)"
    elif agent.target == "gemini" and v.engine != "edge":
        return "Gemini's hook: Microsoft voices only (stage 2)"
    elif agent.command and v.engine != "kokoro":
        return f"{agent.display}'s script: Kokoro only (stage 2)"
    try:
        voiceedit.check_voice(field_name, v.name, agent)       # the same check a typed name gets
    except voiceedit.EditError as exc:
        return str(exc)
    return ""


def _rows(agent: agents.Agent, field_name: str, bank: list[voicebank.BankVoice]) -> list[onoff.Option]:
    current = agent.voice if field_name == "voice" else agent.voice_zh
    out = []
    for v in bank:
        if _same_voice(v.name, current):
            note = ("   (stored, but silent today: English only)" if field_name == "voice_zh" and agent.english_only
                    else "")
            out.append(onoff.Option(v.name, v.label + note, True))     # today's voice: always shown as it is
            continue
        why = not_yet(agent, field_name, v)
        out.append(onoff.Option(NOT_YET + v.name, f"{v.label} — not yet: {why}", False, True) if why
                   else onoff.Option(v.name, v.label))
    return out


def _finish(out: list[onoff.Option], current: Optional[str], silent_label: str) -> list[onoff.Option]:
    """Options with the current one marked (put on top, marked, if it is not in the bank), then the silent line,
    then "other, type it" last."""
    if current and not any(o.current for o in out):
        out.insert(0, onoff.Option(current, f"{current}   (now; not in the voice bank)", True))
    out.append(onoff.Option(SILENT, silent_label, not current))
    out.append(onoff.Option(OTHER, "other, type it…"))
    return out


def english(agent: agents.Agent, cfg: dict) -> list[onoff.Option]:
    silent = ("silent — no voice: her lines are not spoken" if voiceedit.uses_simba_script(agent)
              else "silent — no English voice: English lines are not spoken")
    return _finish(_rows(agent, "voice", voicebank.english()), agent.voice, silent)


def mandarin(agent: agents.Agent) -> list[onoff.Option]:
    return _finish(_rows(agent, "voice_zh", voicebank.mandarin_taiwan()), agent.voice_zh,
                   "silent — no Mandarin voice: Chinese lines are not spoken")


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
        title = f"{agent.display} — Mandarin voice (Taiwan, one voice, no backup)"
        if agent.english_only:
            title += f" — {agent.display} is English only today: a Chinese line stays silent"
        return title, mandarin(agent), ""
    if field_name == "effect":
        if not voiceedit.uses_simba_script(agent):
            return "", [], f"{agent.display} has no voice effect (only Simba's Kokoro voice has one)"
        return f"{agent.display} — voice effect on {agent.voice}", effect(agent), ""
    return "", [], f"{field_name} has no pick-list"


HINT = ("Up / Down, then Enter (● = now; ◌ = in the voice bank, but this agent's own speaker cannot make it yet: "
        "stage 2 moves its script onto Voice Desk's shared speaker; Simba's sound effect works on Kokoro voices only). "
        "Esc cancels. The files are backed up and read back; U undoes.")
