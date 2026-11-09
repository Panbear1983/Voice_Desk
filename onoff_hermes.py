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
"""
from __future__ import annotations

from pathlib import Path

import agents
import gridstate as G
import hermes_patches as HP
import onoff


def can_switch(agent: agents.Agent, place: str) -> bool:
    kind = G.kind_of(agent)
    return (place == "tg_dm" and kind == G.HERMES) or (place == "terminal" and kind == G.GEMINI)


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


# ---- plans ------------------------------------------------------------------------------------------------------------
def plan_tg_dm(agent: agents.Agent, token: str, p: G.Paths) -> tuple[list[dict], str]:
    mode = G.CELL_TO_VOICE_MODE.get(token)
    if mode is None:
        raise onoff.SwitchError("a Telegram direct chat has Off, Voice-back and Always")
    path = Path(p.voice_mode_file)                       # THE gateway's file; never a profile's stray copy
    key = f"{agent.profile}:telegram:{p.tg_dm_chat}"
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
