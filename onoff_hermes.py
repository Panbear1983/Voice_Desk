"""Grid switches, step 3: Telegram direct chats and Gemini's voice.

Same rules as onoff.py (this module only plans; onoff.py backs up, writes, reads back, records the history
and undoes). Nothing new is invented: each switch writes the file the agent already reads.

    Telegram direct chat   ~/.hermes/gateway_voice_mode.json   key "<profile>:telegram:<chat id>" ->
                           "off" | "voice_only" | "all" (the gateway's own /voice modes). The running gateway
                           re-reads the file when it changes ONCE it has Peter's live-reload patch loaded
                           (hermes_patches.py says whether it has); before that the file is read only at
                           the gateway's start. Only this one file is ever written: the stray copies in
                           profiles/*/gateway_voice_mode.json are not read by anything and are never touched.
    Gemini (terminal)      ~/.voice_desk/gemini_voice_off   a marker file: it EXISTS = Gemini stays silent.
                           Gemini's own hook honours it from step 4 (it is not edited in step 3); until then
                           the switch is prepared and the panel says plainly that Gemini still speaks.
    Hermes (terminal)      ~/.hermes/profiles/<profile>/terminal_voice.json   {"voice": "on"} or {"voice": "off"}
                           (Phase C, 2026-11-18). Every window of that agent re-reads it at start and before
                           each reply, through Peter's local Hermes patch "terminal-voice-switch" (hermes_patches.py
                           check / apply). It runs in the windows, never the gateway: no gateway restart; a window
                           opened before the patch follows it once reopened. No file = voice.auto_enable as before.
"""
from __future__ import annotations

from pathlib import Path

import agents
import gridstate as G
import hermes_patches as HP
import onoff


def can_switch(agent: agents.Agent, place: str) -> bool:
    kind = G.kind_of(agent)
    if place == "tg_dm" and agent.key in G.TELEGRAM_HANDS_OFF:
        return False                                       # Simba's own bot: shown, never switched (2026-11-13)
    return (place == "tg_dm" and kind == G.HERMES) or (place == "terminal" and kind in (G.GEMINI, G.HERMES))


def _live_note(p: G.Paths) -> str:
    live = HP.live_voice_mode(p.hermes_locations)
    if live == HP.LIVE:
        return "applies from Peter's next message to that bot (the gateway re-reads the file when it changes)"
    if live == HP.PENDING:
        return ("saved, but the running gateway still has the old code: it reads the file only when it next starts "
                "(restart the Hermes gateway once to make it live)")
    return ("saved, but the Hermes live-reload fix is not in Hermes' code now, so the gateway reads the file only "
            "when it starts (python3 hermes_patches.py apply puts the fix back)")


# ---- menus ------------------------------------------------------------------------------------------------------------
def menu_tg_dm(agent: agents.Agent, cell: G.Cell, p: G.Paths) -> onoff.Menu:
    title = f"{agent.display} — {G.PLACE_NAME['tg_dm']}"
    live = HP.live_voice_mode(p.hermes_locations)
    when = {HP.LIVE: "switches live", HP.PENDING: "saved now, live after one gateway restart"}.get(
        live, "saved now, read when the gateway starts")
    return onoff.Menu(f"{title} ({when})", [
        onoff.Option(G.OFF, "Off — text only in this chat", cell.value == G.OFF),
        onoff.Option(G.VOICE_BACK, "Voice-back — a voice note only after a voice message from Peter", cell.value == G.VOICE_BACK),
        onoff.Option(G.ALWAYS, "Always — a voice note after every reply", cell.value == G.ALWAYS)])


def menu_gemini(agent: agents.Agent, cell: G.Cell, p: G.Paths) -> onoff.Menu:
    honours = G.gemini_hook_honours_switch(p)
    when = "switches live" if honours else "the switch file is written now, Gemini's hook reads it from step 4"
    return onoff.Menu(f"{agent.display} — {G.PLACE_NAME['terminal']} ({when})", [
        onoff.Option(G.OFF, "Off — Gemini does not speak its replies", cell.value == G.OFF),
        onoff.Option(G.ALWAYS, "Always — Gemini speaks every reply (as today)", cell.value == G.ALWAYS)])


def _hermes_terminal_when(p: G.Paths) -> str:
    state, since = HP.terminal_voice_switch(p.hermes_locations)
    if state != HP.LIVE:
        return ("saved, but the Hermes fix that makes windows read it is not in Hermes' code now: "
                "python3 hermes_patches.py apply puts it back")
    return (f"open windows follow it from their next reply; a window opened before {G._when_words(since)} "
            f"follows it once reopened")


def menu_hermes_terminal(agent: agents.Agent, cell: G.Cell, p: G.Paths) -> onoff.Menu:
    return onoff.Menu(f"{agent.display} — {G.PLACE_NAME['terminal']} ({_hermes_terminal_when(p)})", [
        onoff.Option(G.OFF, f"Off — {agent.display}'s windows do not read replies aloud (mic left alone)",
                     cell.value == G.OFF),
        onoff.Option(G.ALWAYS, f"Always — {agent.display}'s windows turn voice on and read every reply aloud",
                     cell.value == G.ALWAYS)])


# ---- plans ------------------------------------------------------------------------------------------------------------
def plan_terminal(agent: agents.Agent, token: str, p: G.Paths) -> tuple[list[dict], str]:
    """A terminal switch this module owns: Gemini's marker or a Hermes agent's terminal_voice.json."""
    if G.kind_of(agent) == G.HERMES:
        return plan_hermes_terminal(agent, token, p)
    return plan_gemini(agent, token, p)


def plan_hermes_terminal(agent: agents.Agent, token: str, p: G.Paths) -> tuple[list[dict], str]:
    """{"voice": "on"|"off"} in the profile's own terminal_voice.json (Phase C, 2026-11-18). Created on the first
    switch, so undoing that first switch removes it again and the windows go back to what they started with."""
    if token not in (G.OFF, G.ALWAYS):
        raise onoff.SwitchError("a Hermes terminal window has Off and Always only")
    if not agent.profile or not (Path(p.profiles) / agent.profile).is_dir():
        raise onoff.SwitchError(f"there is no Hermes profile folder for {agent.display}")
    path = G.hermes_terminal_switch(p, agent.profile)
    want = "on" if token == G.ALWAYS else "off"
    create = not path.exists()
    cur = None
    if not create:
        if G.read_terminal_switch(path) == "broken":
            raise onoff.SwitchError(f"{path.name} for {agent.profile} cannot be read; nothing changed, so a file "
                                    f"someone else wrote is never overwritten")
        cur = onoff._read_json(path).get("voice")
    if cur == want:
        return [], ""
    change = {"t": "json", "path": str(path), "key": "voice", "before": cur, "after": want}
    if create:
        change["create"] = True
    state, since = HP.terminal_voice_switch(p.hermes_locations)
    if state != HP.LIVE:
        note = (" Saved, but the Hermes fix that makes windows read it is not in Hermes' code now, so the windows "
                "ignore it (python3 hermes_patches.py apply puts the fix back).")
    else:
        note = (f" Open {agent.display} windows follow it from their next reply; a window opened before "
                f"{G._when_words(since)} follows it once it is reopened. No gateway restart.")
    return [change], note


def plan_tg_dm(agent: agents.Agent, token: str, p: G.Paths) -> tuple[list[dict], str]:
    return plan_tg_chat(str(agent.profile), token, p, p.tg_dm_chat, "a Telegram direct chat")


def plan_tg_chat(profile: str, token: str, p: G.Paths, chat: str, what: str = "a Telegram chat") -> tuple[list[dict], str]:
    """One agent's bot in one Telegram chat (a private chat or a group): the gateway's own /voice mode for that
    (bot, chat) pair, "<profile>:telegram:<chat id>" in the gateway's one voice-mode file."""
    mode = G.CELL_TO_VOICE_MODE.get(token)
    if mode is None:
        raise onoff.SwitchError(f"{what} has Off, Voice-back and Always")
    path = Path(p.voice_mode_file)                       # THE gateway's file; never a profile's stray copy
    key = f"{profile}:telegram:{chat}"
    if "." in key:
        raise onoff.SwitchError(f"{key!r} cannot be written safely")
    create = not path.exists()
    cur = None
    if not create:
        try:
            data = onoff._read_json(path)
        except (OSError, ValueError) as exc:
            raise onoff.SwitchError(f"gateway_voice_mode.json cannot be read ({exc}); nothing changed, "
                                    "so a file the gateway wrote is never overwritten") from exc
        cur = data.get(key)
    if cur == mode:
        return [], ""
    change = {"t": "json", "path": str(path), "key": key, "before": cur, "after": mode}
    if create:
        change["create"] = True
    note = _live_note(p)
    return [change], " " + note[0].upper() + note[1:] + "."


def plan_gemini(agent: agents.Agent, token: str, p: G.Paths) -> tuple[list[dict], str]:
    if token not in (G.OFF, G.ALWAYS):
        raise onoff.SwitchError("Gemini's terminal has Off and Always only")
    path = Path(p.gemini_switch)
    want_file = token == G.OFF                           # the file exists = silent
    note = ""
    if token == G.OFF:
        note = (" Gemini's hook reads this file from step 4; until then Gemini still speaks."
                if not G.gemini_hook_honours_switch(p) else " Gemini's hook reads it on its next reply.")
    if path.exists() == want_file:
        return [], ""
    return [{"t": "marker", "path": str(path), "before": path.exists(), "after": want_file}], note
