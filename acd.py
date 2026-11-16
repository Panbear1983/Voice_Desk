"""Agent Control Desk, version four: the screen.

    ./acd.sh              every change is a dry run: shown in full, nothing written
    ./acd.sh --armed      changes are written, but only if the desk's own armed switch is also on (acd_edit.py)

HOME, the grid: one row per agent. Columns: the model that is set, the model that really answered the last
reply, the backups, the voice, where the agent shows up, and who it talks to. A narrow window shows fewer
columns instead of scrolling sideways; the tree always has everything.
ENTER on a row opens that agent's tree. In the tree, Enter on a setting opens a small pop-up with a few plain
choices: the language model and its thinking effort, each backup on or off, the English and Chinese voice and
the speaking speed. S saves (after one confirm line that says exactly what will change), Esc cancels.

  grid   up / down  choose an agent     Enter  open the agent's tree
  tree   up / down  choose a setting    Enter  change it     left / right  other agent     Esc  back
  both   u  undo the newest change      r  restart review    c  read again now    q  close
         p  pause / resume the watcher  a  (grid) the voices of Claude, Gemini and Codex
         t  silent voice test (one agent or everyone; made to files and checked, never played)
         l  the listener: what the voice line is doing, mute / hush / new conversation / restart / stop
         n  add an agent to the Voice Desk list      d  remove one (shows exactly what goes, asks first)
  (plain lower-case letters, no Shift; the hint line on screen shows them the same way)

The last four are what only the old Voice Desk dashboard could do (acd_parity.py). They go through the same
armed gate as every change; stopping or restarting the listener asks twice and is refused while someone is
speaking or an agent is mid-reply. In the tree the arrow keys stop at the first and the last setting.

Version four adds, per agent: whether each service it uses can still sign in and how much allowance is left
(acd_health.py), anything changed behind the desk's back with "put it back" / "accept it" (acd_watch.py), and
the old Voice Desk grid's own switches for spoken replies, place by place (the grid's own code does the switch).

It is an overseer, never a lifeline: it reads gently (ONE background read every 45 seconds for all agents),
every change goes through acd_edit.py (backup, atomic write, read back, auto-restore, history, undo), and it
never restarts anything itself: R only leaves a request for the existing restart helper.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, OptionList, Static
from textual.widgets.option_list import Option

import agents as A
import gridedit
import acd_data as D
import acd_edit as E
import acd_health as H
import acd_parity as P
import acd_watch as W

REFRESH_SEC = 45
LISTENER_SEC = 2                 # the voice line's small status file, for the status line
DRY_LABEL = "dry run: changes are shown in full, nothing is written"
ARMED_LABEL = "ARMED: changes are written (backup, read-back, undo)"
READ_ONLY_LABEL = DRY_LABEL                 # the old name, kept for callers
COLOUR = {D.GREEN: "#3fb950", D.YELLOW: "#d29922", D.BLUE: "#58a6ff", D.RED: "#f85149", D.GREY: "#8b949e"}
DIM = "#8b949e"
MARK = "■"                               # the small square that carries a colour
CAN_CHANGE = "◆"                         # in the tree: Enter on this line changes the setting
LEGEND = ((D.GREEN, "live everywhere"), (D.YELLOW, "saved, waiting on a restart or a fresh chat"),
          (D.BLUE, "temporary override"), (D.RED, "failed, or changed behind the desk's back"))


def mark(status: str) -> Text:
    t = Text()
    t.append(MARK + " ", style=COLOUR.get(status, DIM))
    return t


def line(status: Optional[str], text: str, dim: bool = False, tint: bool = False) -> Text:
    """A coloured square, then the words. tint=True colours the words too (the warning strip)."""
    t = mark(status) if status else Text("  ")
    t.append(text, style=DIM if dim else (COLOUR.get(status, "") if tint and status else ""))
    return t


def effort_words(effort: str) -> str:
    return "extra thinking off" if effort == "none" else f"{effort} effort"


def backup_words(b: D.Backup, short: bool = True) -> str:
    """"Claude Sonnet 5.5 via Antigravity"; the service is left out when the model's name already says it."""
    model = D.model_name(b.model)
    service = D.service_name(b.service, short=short)
    if model.lower().startswith(service.lower().rstrip(".")):
        return model
    return f"{model} via {service}" if short else f"{model} through {service}"


def join(lines: list[Text]) -> Text:
    return Text("\n").join(lines)


def when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%-d %b %H:%M")


def where(platform: str) -> str:
    return D.PLATFORM_NAMES.get(platform, platform)


def legend_text() -> Text:
    t = Text()
    for status, words in LEGEND:
        t.append(MARK + " ", style=COLOUR[status])
        t.append(words + "   ", style=DIM)
    return t


def warnings_text(snap: D.Snapshot) -> Text:
    return join([line(w.status, w.text, tint=True) for w in snap.warnings])


def state_word(p: D.Presence) -> str:
    return {D.ON: "on", D.OFF: "off", D.UNKNOWN: "unknown"}[p.state]


# ---- the grid's cells ------------------------------------------------------------------------------------
def cell_agent(v: D.AgentView) -> Text:
    t = Text()
    t.append(v.name, style="bold")
    if v.role:
        t.append("\n" + v.role, style=DIM)
    return t


def cell_model_set(v: D.AgentView) -> Text:
    if v.error:
        return join([line(D.RED, "cannot be read"), Text(v.error, style=DIM)])
    if not v.model:
        return line(None, "unknown", dim=True)
    lock = " (locked)" if v.model_locked else ""
    out = [line(v.model_status, D.model_name(v.model.model) + lock),
           Text(f"  {D.service_name(v.model.service, short=True)}", style=DIM),
           Text(f"  {effort_words(v.model.effort)}", style=DIM)]
    if v.drifts:
        out.append(line(D.RED, "changed behind the desk"))
    elif v.model_behind:
        out.append(Text("  changed somewhere else", style=DIM))
    elif v.model_pending:
        out.append(Text("  saved, not live everywhere", style=DIM))
    main = next((h for h in v.health if h.main), None)
    if main is not None:
        words = {H.SIGNIN_DEAD: "sign-in dead", H.ALLOWANCE_OUT: "allowance used up"}.get(
            main.kind, "signed in, working" if main.state == H.GOOD else "sign-in unknown")
        out.append(line(main.status, words) if main.state != H.UNKNOWN else Text("? " + words, style=DIM))
    return join(out)


def cell_last(v: D.AgentView) -> Text:
    if v.error:
        return line(None, "unknown", dim=True)
    if not v.last:
        return line(None, "unknown: no reply in the log", dim=True)
    r = v.last
    out = [line(r.status, D.model_name(r.answered_model)),
           Text(f"  {D.service_name(r.answered_service, short=True)}", style=DIM)]
    if r.dropped:
        out.append(Text("  the main model failed", style=DIM))
    elif r.status == D.YELLOW:
        out.append(Text("  chat on an older setting", style=DIM))
    elif r.status == D.BLUE:
        out.append(Text("  temporary choice", style=DIM))
    elif r.status == D.RED:
        out.append(Text("  main service not signed in", style=DIM))
    out.append(Text(f"  {where(r.where)}, {when(r.when)}", style=DIM))
    if v.drops_today:
        out.append(Text(f"  {v.drops_today} backup drops today", style=DIM))
    return join(out)


def cell_backups(v: D.AgentView) -> Text:
    if v.error:
        return line(None, "unknown", dim=True)
    if not v.backups:
        return Text("none", style=DIM)
    out = []
    for b in v.backups:
        t = Text()
        trouble = D.backup_trouble(v.health, b)
        t.append("on  " if b.on else "off ", style=COLOUR[D.RED if trouble else D.GREEN] if b.on else DIM)
        t.append(backup_words(b), style="" if b.on else DIM)
        out.append(t)
        if trouble:
            out.append(Text("    " + trouble.removeprefix("allowance "), style=COLOUR[D.RED]))   # kept short: the grid is narrow
    return join(out)


def cell_voice(v: D.AgentView) -> Text:
    if not v.voice:
        return Text("no voice", style=DIM)
    out = [line(D.GREEN, D.voice_label(v.voice.english))]
    if v.voice.speed is not None:
        out.append(Text(f"  speed {v.voice.speed:g}", style=DIM))
    if v.voice.english_only:
        out.append(Text("  English only", style=DIM))
    elif v.voice.chinese:
        out.append(Text(f"  Chinese: {D.voice_label(v.voice.chinese)}", style=DIM))
    return join(out)


SHORT_PLACE = {"terminal": "terminal chat", "tg_private": "Telegram private", "tg_group": "Telegram group",
               "team_room": "Team Room", "mac_voice": "Mac voice", "iphone_voice": "iPhone voice"}


SHORT_UNKNOWN = {"iphone_voice": "iPhone: no shortcut"}        # a grey square and the reason, never a bare "?"


def cell_presence(v: D.AgentView) -> Text:
    if v.error:
        return line(None, "unknown", dim=True)
    out = []
    for p in v.presence:
        if p.state == D.ON:
            out.append(line(p.status, SHORT_PLACE[p.key]))
        elif p.state == D.OFF:
            out.append(Text(f"- {SHORT_PLACE[p.key]}: off", style=DIM))
        else:
            out.append(line(D.GREY, SHORT_UNKNOWN.get(p.key, f"{SHORT_PLACE[p.key]}: unknown"), dim=True))
    return join(out)


def cell_talks(v: D.AgentView) -> Text:
    if not v.talks:
        return Text("unknown", style=DIM)
    return join([Text(f"{GRID_TALK.get(what, what)}: {GRID_ANSWER.get(answer, answer)}") for what, answer in v.talks])


GRID_TALK = {"Speaks in the Team Room": "Team Room", "Hands out work": "Hands out work",
             "Sends jobs to Builder": "Builder jobs", "Takes work from": "Work from", "Takes work": "Work",
             "Answers": "Answers"}
GRID_ANSWER = {"yes, like everyone": "speaks", "yes, the only one who does": "yes",
               "yes, by job card only": "by card", "only as job cards": "job cards only",
               "when named in the line": "when named"}

COLUMNS = (("Agent", cell_agent), ("Model set", cell_model_set), ("Model that answered last", cell_last),
           ("Backups", cell_backups), ("Voice", cell_voice), ("Where it shows up", cell_presence),
           ("Talks to", cell_talks))
# A narrow window drops columns from the right instead of scrolling sideways (the tree still has everything).
NARROW = ((178, 7), (150, 6), (124, 5), (96, 4), (0, 3))
KEEP_WHEN_NARROW = ("Agent", "Model set", "Model that answered last", "Voice", "Backups", "Where it shows up",
                    "Talks to")


def columns_for(width: int, agents: tuple = ()) -> tuple:
    """The columns that fit a window this wide. With the agents given, the real width of what the cells hold
    decides (so nothing ever scrolls sideways); without them, round thresholds do."""
    if not agents:
        count = next(n for least, n in NARROW if width >= least)
    else:
        need = {}
        for label, build in COLUMNS:
            widest = max((len(ln) for v in agents for ln in build(v).plain.split("\n")), default=0)
            need[label] = max(len(label), widest) + 2          # the table pads every cell by one on each side
        count = 3
        for n in range(len(COLUMNS), 2, -1):
            if sum(need[label] for label in KEEP_WHEN_NARROW[:n]) <= width:
                count = n
                break
    keep = set(KEEP_WHEN_NARROW[:count])
    return tuple(c for c in COLUMNS if c[0] in keep)


# ---- one agent's tree --------------------------------------------------------------------------------------
WRAP = 78                                      # the tree's value column wraps here, so a narrow pane still reads


def _leaf(prefix: str, label: str, value: Text, width: int = 17) -> Text:
    t = Text()
    t.append(prefix, style=DIM)
    t.append(label.ljust(width))
    t.append_text(value)
    return t


def _leaves(prefix: str, label: str, status: Optional[str], words: str, width: int = 17, dim: bool = False,
            more: str = "│  │  ") -> list[Text]:
    """One tree line whose words may be long: wrapped, with the later lines lined up under the first."""
    parts = textwrap.wrap(words, WRAP) or [""]
    first = line(status, parts[0], dim=dim) if status else Text(parts[0], style=DIM if dim else "")
    out = [_leaf(prefix, label, first, width)]
    pad = "  " if status else ""
    for part in parts[1:]:
        out.append(_leaf(more, "", Text(pad + part, style=DIM if dim else ""), width))
    return out


END_OF_TREE = "── end of {name}'s tree: nothing below this line ──"


def tree_rows(v: D.AgentView) -> list[tuple[list[Text], Optional[str]]]:
    """One agent's whole tree as rows: (the lines, the name of the setting Enter changes there, or None)."""
    rows: list[tuple[list[Text], Optional[str]]] = []

    def add(lines, setting: Optional[str] = None) -> None:
        rows.append((lines if isinstance(lines, list) else [lines], setting))

    head = Text()
    head.append(v.name.upper(), style="bold")
    if v.role:
        head.append(f"  ({v.role})", style=DIM)
    add(head)
    if v.error:
        add([Text(""), line(D.RED, v.error),
             Text("  Nothing else can be shown for this agent until the settings file reads again.", style=DIM)])
        return rows

    add(Text("├─ Language model", style="bold"))
    if v.model:
        lock = "  (locked: shown, never changed from the desk)" if v.model_locked else ""
        note = f"  ({v.model_note})" if v.model_note else ""
        add(_leaves("│  ├─ ", "Set to", v.model_status,
                    f"{D.model_name(v.model.model)}, on {D.service_name(v.model.service)}, "
                    f"{effort_words(v.model.effort)}{lock}{note}"), "model")
        if v.model_behind and not any(d.key == "model" for d in v.drifts):
            add(_leaves("│  ├─ ", "Changed", D.RED, v.model_behind))
        if v.model_pending:
            add(_leaves("│  ├─ ", "Not live yet", D.YELLOW, v.model_pending))
    else:
        add(_leaf("│  ├─ ", "Set to", Text("unknown", style=DIM)))
    if v.last:
        add(_leaves("│  ├─ ", "Last reply", v.last.status,
                    f"{v.last.note} ({where(v.last.where)}, {when(v.last.when)})"))
    else:
        add(_leaf("│  ├─ ", "Last reply", Text("unknown: no reply in the current log", style=DIM)))
    if v.health:
        add(Text("│  ├─ Services it uses: sign-in and allowance", style=""))
        for h in v.health:
            add(_leaves("│  │     ", "", h.status if h.state != H.UNKNOWN else None, h.words, 0,
                        dim=h.state == H.UNKNOWN, more="│  │     "))
            if h.allowance:
                for ln in h.allowance:
                    add(_leaves("│  │         ", "", None, "allowance: " + ln, 0, dim=True, more="│  │         "))
            elif h.allowance_note:
                add(_leaves("│  │         ", "", None, "allowance: unknown (" + h.allowance_note + ")", 0, dim=True,
                            more="│  │         "))
    elif v.signin_failed and v.model:
        add(_leaves("│  ├─ ", "Sign-in", D.RED,
                    f"{D.service_name(v.model.service)} could not sign in during the last day "
                    f"({D.plain_reason(v.signin_failed)})"))
    for i, d in enumerate(v.drifts):
        add(_leaves("│  ├─ ", "Changed", D.RED, d.sentence + (f" ({d.note})" if d.note else "")
                    + " Enter: put it back, or accept it."), f"drift:{i}")
    add(_leaf("│  ├─ ", "Drops today", Text(
        f"{v.drops_today} to a backup" if v.drops_today else "none", style="" if v.drops_today else DIM)))
    if v.old_chats:
        add(_leaves("│  ├─ ", "Old chats", D.YELLOW,
                    f"{v.old_chats} open on an older setting. A fresh chat would use the current one"))
    if v.override:
        o = v.override
        add(_leaf("│  ├─ ", "Temporary", line(
            D.BLUE, f"{D.model_name(o.model)} through {D.service_name(o.service)} "
                    f"({where(o.where)}, {when(o.when)})")))
    else:
        add(_leaf("│  ├─ ", "Temporary", Text(
            "none right now (a model typed in a chat or pinned on a job shows here)", style=DIM)))
    add(Text("│  └─ Backups, in order", style=""))
    if not v.backups:
        add(Text("│        none" + (", by design" if v.backups_locked else ""), style=DIM), "backups")
    for i, b in enumerate(v.backups):
        branch = "└─" if i == len(v.backups) - 1 else "├─"
        t = Text()
        t.append(f"│        {branch} {b.position if b.on else '-'}  ", style=DIM)
        trouble = D.backup_trouble(v.health, b)
        t.append("on   " if b.on else "off  ", style=COLOUR[D.RED if trouble else D.GREEN] if b.on else DIM)
        t.append(backup_words(b, short=False), style="" if b.on else DIM)
        if trouble:
            t.append(f"  (on, but {trouble}: it cannot answer until then)" if "until" in trouble
                     else f"  (on, but {trouble}: it cannot answer)", style=COLOUR[D.RED])
        if b.note:
            t.append(f"  ({b.note})", style=DIM)
        add(t, "backups")

    add(Text("├─ Voice", style="bold"))
    if v.voice:
        add(_leaf("│  ├─ ", "English", line(D.GREEN, D.voice_label(v.voice.english))), "voice")
        chinese = "none: this agent is English only" if v.voice.english_only else D.voice_label(v.voice.chinese)
        add(_leaf("│  ├─ ", "Chinese", Text(chinese, style=DIM if v.voice.english_only else "")), "voice_zh")
        speed = E.speed_word(v.voice.speed) + (f" ({v.voice.speed:g})" if v.voice.speed is not None else "")
        add(_leaf("│  ├─ ", "Speed", Text(speed, style=DIM if v.voice.speed is None else "")), "speed")
        if v.effect:
            add(_leaf("│  ├─ ", "Effect", Text(str(v.effect))), "effect")
        add(_leaf("│  ├─ " if v.voice_places else "│  └─ ", "If the voice fails",
                  Text("stays silent, no stand-in voice"), 20))
    else:
        add(Text("│  ├─ no voice in the Voice Desk list" if v.voice_places else
                 "│  └─ no voice in the Voice Desk list", style=DIM))
    if v.voice_places:
        add(Text("│  └─ Spoken replies, place by place (the setting, then what the logs say really happened)"))
        for ln, setting in place_rows(v.voice_places, "│        "):
            add(ln, setting)

    add(Text("├─ Where it shows up", style="bold"))
    for i, p in enumerate(v.presence):
        last = i == len(v.presence) - 1
        branch = "└─ " if last else "├─ "
        more = "│     " if last else "│  │  "
        lines = _leaves("│  " + branch, D.PLACE_NAME[p.key], p.status if p.state == D.ON or p.status == D.RED else None,
                        f"{state_word(p)}: {p.evidence}", 23, dim=p.state != D.ON and p.status != D.RED, more=more)
        if not p.can_switch and p.why_not:
            lines += _leaves(more, "", None, p.why_not, 23, dim=True, more=more)
        add(lines, "team_room" if p.can_switch else None)

    add(Text("└─ Who it may talk to", style="bold"))
    rules = v.talk_rules
    if not rules:
        for i, (what, answer) in enumerate(v.talks):
            branch = "└─ " if i == len(v.talks) - 1 else "├─ "
            add(_leaf("   " + branch, what, Text(answer), 26))
    for i, r in enumerate(rules):
        last = i == len(rules) - 1
        branch = "└─ " if last else "├─ "
        more = "      " if last else "   │  "
        colour = D.GREEN if r.standing.startswith((D.WORKS_TODAY, D.ENFORCED)) else None
        lines = _leaves("   " + branch, r.label, colour, r.answer, 26, more=more)
        lines += _leaves(more, "", None, r.standing + (f"; {r.locked}" if r.locked else ""), 26, dim=True, more=more)
        add(lines, "talk")
    add([Text(""), Text(END_OF_TREE.format(name=v.name), style=DIM), Text(" ")])     # a space: an empty last line is dropped
    return rows


def place_rows(places: tuple, pad: str) -> list[tuple[list[Text], Optional[str]]]:
    """The voice switches of one agent as tree rows: (the lines, the setting Enter changes there or None)."""
    out = []
    for i, p in enumerate(places):
        branch = "└─ " if i == len(places) - 1 else "├─ "
        more = pad + ("   " if i == len(places) - 1 else "│  ")
        words = p.value + (f"   ({p.evidence})" if p.evidence else "")
        lines = _leaves(pad + branch, D.VOICE_PLACE_NAME.get(p.key, p.key), p.status, words, 31, more=more)
        if p.problem:
            lines += _leaves(more, "", D.RED, p.problem, 31, more=more)
        if not p.can_switch and p.why_not:
            lines += _leaves(more, "", None, p.why_not, 31, dim=True, more=more)
        out.append((lines, f"vplace:{p.key}" if p.can_switch else None))
    return out


def tree_text(v: D.AgentView) -> Text:
    """The whole tree of one agent as one text (what the tree screen shows, line for line)."""
    return join([ln for lines, _ in tree_rows(v) for ln in lines])


ROOM_HEADING = "TEAM ROOM (all agents)"       # the note under it is about the room, not the chosen agent


def agent_list_text(snap: D.Snapshot, index: int) -> Text:
    out = [Text("AGENTS", style=DIM)]
    for i, v in enumerate(snap.agents):
        t = Text()
        t.append("> " if i == index else "  ", style="bold" if i == index else "")
        t.append(v.name.ljust(14), style="bold reverse" if i == index else "")
        t.append(" ")
        t.append(MARK, style=COLOUR.get(v.worst, DIM))
        out.append(t)
    out += [Text(""), Text(ROOM_HEADING, style=DIM), Text(snap.room_note, style=DIM)]
    return join(out)


def others_text(snap: D.Snapshot) -> Text:
    if not snap.others:
        return Text("")
    out = [Text("Called by name through the Voice Desk (voice and call names only; press a to change their voices)", style=DIM)]
    for o in snap.others:
        t = Text()
        t.append(f"  {o.name.ljust(8)}", style="bold")
        t.append(f"voice {D.voice_label(o.english)}")
        if o.chinese:
            t.append(f", Chinese {D.voice_label(o.chinese)}")
        own = next((p for p in o.voice_places if p.key == "terminal"), None)
        if own is not None:
            t.append(f"   speaks in its own window: {own.value}")
        t.append("   answers to: " + ", ".join(o.call_names), style=DIM)
        out.append(t)
    return join(out)


def listener_item(view: "P.ListenerView") -> Text:
    """The voice line in a few words, for the status line: what the listener is doing and who is on the line."""
    colour = COLOUR[D.RED] if not view.running or view.state == "error" else \
        COLOUR[D.YELLOW] if view.state == "muted" else COLOUR[D.GREEN]
    t = Text()
    t.append("● ", style=colour)
    t.append("voice line: " + view.words + (f" ({view.on_line} on the line)" if view.on_line and view.running else ""),
             style=colour if not view.running else DIM)
    return t


def systems_text(snap: Optional[D.Snapshot], width: int = 10_000, listener: Optional["P.ListenerView"] = None) -> Text:
    """One line: the voice line, the watcher's mode, then the Voice Desk's own health strip (voice engine,
    gateway, queue ...). A light that is failing is not repeated here: it is already a red line above."""
    if snap is None:
        return Text()
    items: list[Text] = []
    if listener is not None:
        items.append(listener_item(listener))
    if snap.watch_mode:
        t = Text()
        t.append("watcher: ", style=DIM)
        t.append(snap.watch_mode, style=COLOUR[D.YELLOW] if snap.watch_paused else DIM)
        items.append(t)
    for ok, label, _detail in snap.lights:
        if ok is False:                                 # already a red line in the warning strip: said once
            continue
        if label.lower().startswith("watcher"):         # the Voice Desk's own watcher (voice switches), not the desk's
            label = "Voice Desk " + label.replace("(W resumes)", "").strip()
            label = label.replace("WATCHER PAUSED", "watcher paused")
        colour = DIM if ok is None else COLOUR[D.GREEN] if ok else COLOUR[D.RED]
        t = Text()
        t.append("● ", style=colour)
        t.append(label, style=colour if ok is False else DIM)
        items.append(t)
    return flow(items, width)


def flow(items: list[Text], width: int, gap: str = "   ") -> Text:
    """Items side by side, broken into lines BETWEEN items only: no item is ever split across two lines
    (unless one item alone is wider than the window)."""
    rows: list[Text] = []
    row = Text()
    for item in items:
        if row.plain and len(row.plain) + len(gap) + len(item.plain) > width:
            rows.append(row)
            row = Text()
        if row.plain:
            row.append(gap)
        row.append_text(item)
    if row.plain:
        rows.append(row)
    return join(rows)


# ---- the pop-ups -------------------------------------------------------------------------------------------
@dataclass
class Opt:
    token: str
    label: str
    now: bool = False               # radio: today's choice.  toggle: switched on today
    disabled: bool = False
    note: str = ""


@dataclass
class Group:
    key: str
    label: str
    kind: str                       # "radio" (pick one) or "toggle" (each line on / off)
    options: list[Opt] = field(default_factory=list)
    testable: str = ""              # "en" / "zh": T renders the highlighted voice to a file and checks it


_POPUP_CSS = """
{name} {{ align: center middle; background: #0d1117 60%; }}
#box {{ width: 104; max-width: 96%; height: auto; max-height: 92%; border: round #58a6ff; background: #161b22;
       padding: 1 2; }}
#box-title {{ text-style: bold; }}
#box-note {{ color: #8b949e; height: auto; }}
#box-list {{ height: auto; max-height: 22; background: #161b22; border: none; padding: 0; }}
#box-list > .option-list--option-disabled {{ color: #8b949e; }}
#box-status {{ height: auto; color: #d29922; }}
#box-confirm {{ height: auto; }}
#box-keys {{ color: #8b949e; height: auto; }}
"""


class EditPopup(ModalScreen[Optional[dict]]):
    """A few plain choices for one setting. Enter or Space chooses; S saves after one confirm line that says
    exactly what will change; Esc cancels. Returns {group key: token} for radios and {group key: {token: on}}
    for toggles, holding only what changed, or None."""
    CSS = _POPUP_CSS.format(name="EditPopup")
    BINDINGS = [Binding("escape", "cancel", "cancel"), Binding("s", "save", "save"),
                Binding("space", "choose", "choose"), Binding("t", "test", "test"),
                Binding("enter", "enter", "enter", show=False)]

    def __init__(self, title: str, groups: list[Group], describe: Callable[[dict], list[str]], note: str = "",
                 locked: str = "", tester: Optional[Callable[[str, str], tuple[bool, str]]] = None,
                 dry: bool = True) -> None:
        super().__init__()
        self.title_words, self.groups, self.describe, self.note, self.locked = title, groups, describe, note, locked
        self.tester, self.dry = tester, dry
        self.staged: dict[str, str] = {}
        self.flipped: dict[str, dict[str, bool]] = {}
        self.confirming = False
        self.rows: list[tuple[Optional[Group], Optional[Opt]]] = []

    # -- what is on the list
    def is_on(self, g: Group, o: Opt) -> bool:
        if g.kind == "toggle":
            return self.flipped.get(g.key, {}).get(o.token, o.now)
        return self.staged.get(g.key, next((x.token for x in g.options if x.now), None)) == o.token

    def row_text(self, g: Group, o: Opt) -> Text:
        on = self.is_on(g, o)
        t = Text()
        box = ("[x] " if on else "[ ] ") if g.kind == "toggle" else ("(o) " if on else "( ) ")
        t.append("  " + box, style=COLOUR[D.GREEN] if on else DIM)
        t.append(o.label, style=DIM if o.disabled else "")
        if o.now and g.kind == "radio":
            t.append("   now", style=COLOUR[D.BLUE])
        if g.kind == "toggle":
            t.append("   on now" if o.now else "   off now", style=DIM)
            if on != o.now:
                t.append("   -> " + ("on" if on else "off"), style=COLOUR[D.YELLOW])
        elif on and not o.now:
            t.append("   <- chosen", style=COLOUR[D.YELLOW])
        if o.note:
            t.append(f"   ({o.note})", style=DIM)
        return t

    def options(self) -> list[Option]:
        self.rows = []
        out = []
        for g in self.groups:
            self.rows.append((g, None))
            out.append(Option(Text(g.label, style="bold"), disabled=True))
            for o in g.options:
                self.rows.append((g, o))
                out.append(Option(self.row_text(g, o), disabled=o.disabled))
        return out

    def changes(self) -> dict:
        out: dict = {}
        for g in self.groups:
            if g.kind == "radio":
                now = next((x.token for x in g.options if x.now), None)
                if g.key in self.staged and self.staged[g.key] != now:
                    out[g.key] = self.staged[g.key]
            else:
                flips = {tok: on for tok, on in self.flipped.get(g.key, {}).items()
                         if on != next(x.now for x in g.options if x.token == tok)}
                if flips:
                    out[g.key] = flips
        return out

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            yield Static(self.locked or self.note, id="box-note")
            yield OptionList(*([] if self.locked else self.options()), id="box-list")
            yield Static("", id="box-status")
            yield Static("", id="box-confirm")
            yield Static("", id="box-keys")

    def on_mount(self) -> None:
        lst = self.query_one("#box-list", OptionList)
        lst.display = not self.locked
        if self.locked:
            lst.disabled = True
            self.set_focus(None)
        else:
            lst.focus()
            first = next((i for i, (g, o) in enumerate(self.rows) if o and self.is_on(g, o) and not o.disabled),
                         next((i for i, (_, o) in enumerate(self.rows) if o and not o.disabled), None))
            lst.highlighted = first
        self.show_keys()

    def show_keys(self) -> None:
        if self.locked:
            words = "Esc close"
        elif self.confirming:
            words = "Enter = yes, do exactly that     Esc = back to the choices"
        else:
            words = "Up / Down move   Enter or Space choose   S save   Esc cancel"
            if self.tester and any(g.testable for g in self.groups):
                words += "   T test the highlighted voice (made to a file and checked, never played)"
        self.query_one("#box-keys", Static).update(words)

    def highlighted(self) -> tuple[Optional[Group], Optional[Opt]]:
        i = self.query_one("#box-list", OptionList).highlighted
        return self.rows[i] if i is not None and 0 <= i < len(self.rows) else (None, None)

    def redraw(self) -> None:
        lst = self.query_one("#box-list", OptionList)
        keep = lst.highlighted
        lst.clear_options()
        lst.add_options(self.options())
        lst.highlighted = keep

    # -- keys
    def action_choose(self) -> None:
        if self.locked or self.confirming:
            return
        g, o = self.highlighted()
        if g is None or o is None or o.disabled:
            return
        if g.kind == "toggle":
            self.flipped.setdefault(g.key, {})[o.token] = not self.is_on(g, o)
        else:
            self.staged[g.key] = o.token
        self.query_one("#box-status", Static).update("")
        self.redraw()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.action_enter()

    def action_enter(self) -> None:
        if self.locked:
            self.dismiss(None)
        elif self.confirming:
            self.dismiss(self.changes())
        else:
            self.action_choose()

    def action_save(self) -> None:
        if self.locked or self.confirming:
            return
        changes = self.changes()
        if not changes:
            self.query_one("#box-status", Static).update("Nothing is different from now, so there is nothing to save.")
            return
        self.confirming = True
        head = ("This is a DRY RUN: the desk will show exactly what would change and write nothing."
                if self.dry else "This will be WRITTEN now (backup first, read back, u undoes it).")
        body = [Text(head, style=COLOUR[D.YELLOW] if self.dry else COLOUR[D.RED])]
        body += [Text("  " + s) for s in self.describe(changes)]
        self.query_one("#box-confirm", Static).update(join(body))
        self.query_one("#box-list", OptionList).disabled = True
        self.set_focus(None)
        self.show_keys()

    def action_cancel(self) -> None:
        if self.confirming:
            self.confirming = False
            self.query_one("#box-confirm", Static).update("")
            lst = self.query_one("#box-list", OptionList)
            lst.disabled = False
            lst.focus()
            self.show_keys()
        else:
            self.dismiss(None)

    def action_test(self) -> None:
        g, o = self.highlighted()
        if self.locked or self.confirming or not self.tester or g is None or o is None or not g.testable:
            return
        if not o.token:
            self.query_one("#box-status", Static).update("Silent has nothing to test.")
            return
        self.query_one("#box-status", Static).update(f"Making {o.label} to a file (nothing is played) ...")
        self.run_voice_test(g.testable, o.token, o.label)

    @work(thread=True, exclusive=True, group="acd-voice-test")
    def run_voice_test(self, lang: str, voice: str, label: str) -> None:
        try:
            ok, detail = self.tester(lang, voice)
        except Exception as e:                           # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        words = f"{'Test passed' if ok else 'Test FAILED'} for {label}: {detail}"
        self.app.call_from_thread(self.query_one("#box-status", Static).update, words)


class NoticePopup(ModalScreen[bool]):
    """A message, or a yes / no question. Y (or Enter on a plain message) answers yes; Esc closes."""
    CSS = _POPUP_CSS.format(name="NoticePopup") + "#box-body { height: auto; max-height: 30; }"
    BINDINGS = [Binding("escape", "no", "close"), Binding("y", "yes", "yes"), Binding("enter", "enter", "ok")]

    def __init__(self, title: str, body: Text, ask: str = "") -> None:
        super().__init__()
        self.title_words, self.body, self.ask = title, body, ask

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            with VerticalScroll(id="box-body"):
                yield Static(self.body, id="box-text")
            yield Static(f"Y = {self.ask}     Esc = no, close" if self.ask else "Enter or Esc closes", id="box-keys")

    def action_yes(self) -> None:
        if self.ask:
            self.dismiss(True)

    def action_enter(self) -> None:
        if not self.ask:
            self.dismiss(False)

    def action_no(self) -> None:
        self.dismiss(False)


class PickPopup(ModalScreen[Optional[str]]):
    """A short list to pick one line from. Enter picks, Esc closes. Returns the line's token, or None."""
    CSS = _POPUP_CSS.format(name="PickPopup")
    BINDINGS = [Binding("escape", "cancel", "close")]

    def __init__(self, title: str, rows: list[tuple[str, str]], note: str = "") -> None:
        super().__init__()
        self.title_words, self.rows, self.note = title, rows, note

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            yield Static(self.note, id="box-note")
            yield OptionList(*[Option(label, disabled=not token) for token, label in self.rows], id="box-list")
            yield Static("Up / Down move   Enter open   Esc close", id="box-keys")

    def on_mount(self) -> None:
        lst = self.query_one("#box-list", OptionList)
        lst.focus()
        lst.highlighted = next((i for i, (token, _) in enumerate(self.rows) if token), None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        token = self.rows[event.option_index][0]
        if token:
            self.dismiss(token)

    def action_cancel(self) -> None:
        self.dismiss(None)


SILENT = "::silent"             # the pick-list's token for "no voice"


class AskPopup(ModalScreen[Optional[str]]):
    """One line to type (a name). Enter answers, Esc cancels. Returns the text, or None."""
    CSS = _POPUP_CSS.format(name="AskPopup") + "#box-input { margin: 1 0; }"
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, title: str, note: str, value: str = "") -> None:
        super().__init__()
        self.title_words, self.note, self.value = title, note, value

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            yield Static(self.note, id="box-note")
            yield Input(value=self.value, id="box-input")
            yield Static("Enter goes on     Esc cancels, nothing is changed", id="box-keys")

    def on_mount(self) -> None:
        self.query_one("#box-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


LISTENER_KEYS = ("m mute / unmute   h hush   n new conversation   x hand the microphone back   "
                 "r restart   k stop   Esc close")


class ListenerPopup(ModalScreen[None]):
    """The voice line: what the listener is doing, and its controls (the old dashboard's second screen). It never
    opens the microphone. It reads the listener's own small status file every two seconds while it is open."""
    CSS = _POPUP_CSS.format(name="ListenerPopup") + "#box-body { height: auto; }"
    BINDINGS = [Binding("escape", "close", "close"), Binding("m", "order('mute')", "mute"),
                Binding("h", "order('hush')", "hush"), Binding("n", "order('new_thread')", "new"),
                Binding("x", "order('release')", "hand back"), Binding("r", "order('restart')", "restart"),
                Binding("k", "order('stop')", "stop")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("The voice line (the listener)", id="box-title")
            yield Static("", id="box-note")
            yield Static("", id="box-body")
            yield Static(LISTENER_KEYS, id="box-keys")

    def on_mount(self) -> None:
        self.loaded: Optional[bool] = None
        self.show()
        self.timer = self.set_interval(2, self.show)
        self.ask_loaded()

    @work(thread=True, exclusive=True, group="acd-listener-job")
    def ask_loaded(self) -> None:
        svc = self.app.listener_svc
        try:
            got = svc.loaded() if svc is not None else None
        except Exception:                                # noqa: BLE001
            got = None
        self.app.call_from_thread(self.set_loaded, got)

    def set_loaded(self, got: Optional[bool]) -> None:
        self.loaded = got
        self.show()

    def view(self) -> Optional["P.ListenerView"]:
        svc = self.app.listener_svc
        if svc is None:
            return None
        return dataclasses.replace(P.listener_view(svc, ask_loaded=False), loaded=self.loaded)

    def show(self) -> None:
        v = self.view()
        try:
            note, body = self.query_one("#box-note", Static), self.query_one("#box-body", Static)
        except Exception:                                # noqa: BLE001  the pop-up is closing
            return
        if v is None:
            note.update("This desk was not given the listener, so there is nothing to show or control here.")
            return
        armed = self.app.is_armed
        note.update(Text(("ARMED: an order is really sent." if armed else
                          "Dry run: a key shows what would happen, nothing is sent.")
                         + " Stop and restart stop the voice line, so the desk asks twice, and refuses while "
                           "someone is speaking or an agent is mid-reply.",
                         style=COLOUR[D.RED] if armed else COLOUR[D.YELLOW]))
        out = []
        for label, words in P.listener_text_lines(v):
            t = Text()
            t.append(label.ljust(14), style=DIM)
            t.append(words, style=COLOUR[D.RED] if (label == "State" and not v.running)
                     or (label == "Safe to stop" and v.busy) else "")
            out.append(t)
        body.update(join(out))

    def action_order(self, action: str) -> None:
        v = self.view()
        if v is None:
            return
        if action == "mute" and v.muted:
            action = "unmute"
        self.app.listener_order(action)

    def action_close(self) -> None:
        self.dismiss(None)


def results_text(results: list[E.Result]) -> tuple[str, Text]:
    """(title, body) for what a save did, or would do."""
    dry = any(r.dry for r in results)
    failed = [r for r in results if not r.ok]
    title = ("Not changed" if failed and len(failed) == len(results) else
             "Dry run: nothing was written" if dry else "Saved")
    out: list[Text] = []
    for r in results:
        out.append(line(D.RED if not r.ok else D.YELLOW if r.dry else D.GREEN, r.message))
        for ln in r.lines[:16]:
            out.append(Text("      " + ln, style=COLOUR[D.RED] if ln.lstrip().startswith("- ") else
                            COLOUR[D.GREEN] if ln.lstrip().startswith("+ ") else DIM))
        if len(r.lines) > 16:
            out.append(Text(f"      ... and {len(r.lines) - 16} more lines", style=DIM))
        if r.needs and r.ok:
            for part in textwrap.wrap(r.needs, 92):
                out.append(Text("    " + part, style=DIM))
        out.append(Text(""))
    return title, join(out[:-1] if out else out)


# ---- the screens -------------------------------------------------------------------------------------------
_TOP_CSS = """
#title { height: 1; background: #161b22; padding: 0 1; }
#warnings { height: auto; padding: 0 1; }
#legend { height: 1; padding: 0 1; background: #161b22; }
#systems { height: auto; padding: 0 1; }
#keys { height: 1; padding: 0 1; background: #161b22; color: #8b949e; dock: bottom; }
"""
_SHARED_KEYS = [Binding("t", "app.voice_test", "voice test", show=False),
                Binding("l", "app.listener", "listener", show=False),
                Binding("n", "app.add_agent", "add an agent", show=False),
                Binding("d", "app.remove_agent", "remove an agent", show=False),
                Binding("c", "app.reread", "read again", show=False), Binding("q", "app.quit", "close", show=False),
                Binding("u", "app.undo", "undo", show=False), Binding("r", "app.restart_review", "restart", show=False),
                Binding("p", "app.pause_watcher", "pause the watcher", show=False)]


def hidden_below(table: DataTable, agents: tuple) -> list[str]:
    """The names of the agents whose block is not fully on screen below the table's bottom edge."""
    bottom = table.scroll_y + max(0, table.size.height - 1)          # the header takes one line
    out, top = [], 0
    for i, v in enumerate(agents):
        try:
            height = table.ordered_rows[i].height
        except IndexError:
            break
        if top + height - 1 > bottom:                                # the last line of a block is a blank spacer
            out.append(v.name)
        top += height
    return out


# The letters are shown the way they are typed: plain lower-case keys, no Shift.
GRID_KEYS = ("up/down agent  Enter open its tree  a assistants' voices  t voice test  l listener  "
             "n add  d remove  u undo  r restart review  p pause watcher  c read again  q close")
TREE_KEYS = (f"{CAN_CHANGE} = can change: up/down, Enter  left/right agent  Esc grid  t voice test  "
             "l listener  n add  d remove  u undo  r restart review  p pause watcher  c read again  q close")


EDGE_WORDS = {1: "that was the last setting: nothing is below it. up goes back; Esc returns to the grid",
              -1: "that was the first setting: nothing is above it. down goes on; Esc returns to the grid"}


class QuietTable(DataTable):
    """The grid's table, without the faint band that follows the mouse pointer: with the chosen row also
    highlighted, that band looked like a second agent was selected. A click still chooses a row."""

    def _set_hover_cursor(self, active: bool) -> None:
        super()._set_hover_cursor(False)


class GridScreen(Screen):
    """Home: one row per agent."""
    CSS = _TOP_CSS + """
    #grid { height: 1fr; }
    #grid > .datatable--cursor { background: #30363d; }
    #more { height: 1; padding: 0 1; color: #d29922; display: none; }
    #grid > .datatable--header { background: #161b22; color: #8b949e; }
    #others { height: auto; padding: 1 1 0 1; }
    """
    BINDINGS = [Binding("a", "app.assistants", "assistants' voices", show=False), *_SHARED_KEYS]

    def compose(self) -> ComposeResult:
        yield Static("", id="title")
        yield Static("reading every agent's settings and log ...", id="warnings")
        yield Static(legend_text(), id="legend")
        yield Static("", id="systems")
        yield QuietTable(id="grid", cursor_type="row", zebra_stripes=False,
                         cursor_foreground_priority="renderable")    # the chosen row keeps its green and red
        yield Static("", id="more")
        yield Static("", id="others")
        yield Static("", id="keys")

    def on_mount(self) -> None:
        self.shown: tuple = ()
        self.query_one("#grid", DataTable).focus()
        self.show(self.app.snapshot)

    def on_resize(self) -> None:
        snap = self.app.snapshot
        if columns_for(self.size.width, snap.agents if snap else ()) != self.shown:
            self.show(snap)
        elif snap is not None:
            self.query_one("#systems", Static).update(systems_text(snap, self.app.text_width(), self.app.listener_now))
        self.call_after_refresh(self.more_hint)

    def show(self, snap: Optional[D.Snapshot]) -> None:
        self.query_one("#title", Static).update(self.app.title_text())
        table = self.query_one("#grid", DataTable)
        cols = columns_for(self.size.width or self.app.size.width, snap.agents if snap else ())
        if cols != self.shown:
            table.clear(columns=True)
            for label, _ in cols:
                table.add_column(label, key=label)
            self.shown = cols
        hidden = len(COLUMNS) - len(cols)
        self.query_one("#keys", Static).update(
            GRID_KEYS
            + (f"   ({hidden} columns hidden: window is narrow, the tree has them)" if hidden else ""))
        if snap is None:
            return
        self.query_one("#warnings", Static).update(warnings_text(snap))
        self.query_one("#systems", Static).update(systems_text(snap, self.app.text_width(), self.app.listener_now))
        keep = table.cursor_row
        table.clear()
        for v in snap.agents:
            cells = [build(v) for _, build in cols]
            table.add_row(*cells, height=max(c.plain.count("\n") + 1 for c in cells) + 1, key=v.profile)
        if snap.agents:
            table.move_cursor(row=min(keep, len(snap.agents) - 1))
        self.query_one("#others", Static).update(others_text(snap))
        self.call_after_refresh(self.more_hint)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.call_after_refresh(self.more_hint)

    def more_hint(self) -> None:
        """One line under the grid when agents sit below the bottom edge, naming them: nothing is cut off in silence."""
        snap = self.app.snapshot
        try:
            table, more = self.query_one("#grid", DataTable), self.query_one("#more", Static)
        except Exception:                               # noqa: BLE001  the screen is closing
            return
        below = hidden_below(table, snap.agents if snap else ())
        more.display = bool(below)
        if below:
            more.update("more below, press down: " + ", ".join(below))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.open_tree(event.cursor_row)


class TreeList(OptionList):
    """The tree's list. Two differences from a plain list: the arrow keys STOP at the first and the last setting
    (no silent jump to the other end), and each step also brings the plain lines around the new setting into
    view, so nothing between two settings is ever skipped off screen."""

    class Edge(Message):
        """Up was pressed on the first setting, or down on the last one."""
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    def step(self, direction: int) -> None:
        options = self.options
        at = self.highlighted
        j = (at if at is not None else (-1 if direction > 0 else len(options))) + direction
        while 0 <= j < len(options) and options[j].disabled:
            j += direction
        if 0 <= j < len(options):
            self.highlighted = j
            self.call_after_refresh(self.show_around, j, direction)
        else:
            (self.scroll_end if direction > 0 else self.scroll_home)(animate=False)
            self.post_message(self.Edge(direction))

    def action_cursor_down(self) -> None:
        self.step(1)

    def action_cursor_up(self) -> None:
        self.step(-1)

    def show_around(self, j: int, direction: int) -> None:
        """Bring the setting and the plain lines that follow it (going down) or come before it (going up) into
        view. When they do not all fit, the setting itself wins."""
        options = self.options
        if not (0 <= j < len(options)) or not self.is_mounted:
            return
        k = j
        while 0 <= k + direction < len(options) and options[k + direction].disabled:
            k += direction
        try:
            first, last = min(j, k), max(j, k)
            top = self._index_to_line[first]
            height = self._index_to_line[last] + self._heights[last] - top
        except KeyError:
            return
        room = self.scrollable_content_region.height
        if height <= room:
            self.scroll_to_region(Region(0, top, self.scrollable_content_region.width, height), force=True,
                                  animate=False, immediate=True)
        else:
            self.scroll_to_highlight(top=direction > 0)


class TreeScreen(Screen):
    """One agent's whole tree. Up and down move through its settings; Enter changes the highlighted one."""
    CSS = _TOP_CSS + """
    #body { height: 1fr; }
    #agents { width: 26; padding: 1 1; border-right: solid #30363d; }
    #tree { width: 1fr; height: 1fr; padding: 1 2; background: #0d1117; border: none; }
    #tree > .option-list--option-disabled { color: #e6edf3; }
    #tree > .option-list--option-highlighted { background: #30363d; }
    """
    BINDINGS = [Binding("escape", "back", "back", show=False), Binding("left", "move(-1)", "agent", show=False),
                Binding("right", "move(1)", "agent", show=False), Binding("space", "change", "change", show=False),
                *_SHARED_KEYS]

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index
        self.settings: list[Optional[str]] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="title")
        yield Static("", id="warnings")
        yield Static(legend_text(), id="legend")
        yield Static("", id="systems")
        with Horizontal(id="body"):
            yield Static("", id="agents")
            yield TreeList(id="tree")
        yield Static(TREE_KEYS, id="keys")

    def on_mount(self) -> None:
        self.query_one("#tree", OptionList).focus()
        self.show(self.app.snapshot)

    def on_resize(self) -> None:
        if self.app.snapshot is not None:
            self.query_one("#systems", Static).update(systems_text(self.app.snapshot, self.app.text_width(), self.app.listener_now))

    def view(self) -> Optional[D.AgentView]:
        snap = self.app.snapshot
        if snap is None or not snap.agents:
            return None
        self.index = max(0, min(self.index, len(snap.agents) - 1))
        return snap.agents[self.index]

    def show(self, snap: Optional[D.Snapshot], reset: bool = False) -> None:
        self.query_one("#title", Static).update(self.app.title_text())
        v = self.view()
        if snap is None or v is None:
            return
        self.query_one("#warnings", Static).update(warnings_text(snap))
        self.query_one("#systems", Static).update(systems_text(snap, self.app.text_width(), self.app.listener_now))
        self.query_one("#agents", Static).update(agent_list_text(snap, self.index))
        tree = self.query_one("#tree", OptionList)
        keep = None if reset else tree.highlighted
        options, self.settings = [], []
        block: list[Text] = []

        def flush() -> None:
            if block:
                options.append(Option(join(list(block)), disabled=True))
                self.settings.append(None)
                block.clear()

        for lines, setting in tree_rows(v):
            if setting is None:
                block.extend(lines)
                continue
            flush()
            lines = [ln.copy() for ln in lines]
            lines[0].append("   " + CAN_CHANGE, style=COLOUR[D.BLUE])
            options.append(Option(join(lines)))
            self.settings.append(setting)
        flush()
        tree.clear_options()
        tree.add_options(options)
        first = next((i for i, s in enumerate(self.settings) if s), None)
        tree.highlighted = keep if keep is not None and keep < len(options) and self.settings[keep] else first

    def on_tree_list_edge(self, event: TreeList.Edge) -> None:
        """Down on the last setting, or up on the first: the cursor stays put and the hint line says why."""
        self.query_one("#keys", Static).update(Text(EDGE_WORDS[event.direction], style=COLOUR[D.YELLOW]))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Headings above the first setting and notes under the last one are not settings, so the arrow keys would
        stop short of them: at either end, scroll the rest into view."""
        self.query_one("#keys", Static).update(TREE_KEYS)
        chosen = [i for i, s in enumerate(self.settings) if s]
        if not chosen or event.option_index is None:
            return
        tree = self.query_one("#tree", OptionList)
        if event.option_index == chosen[-1]:
            self.call_after_refresh(tree.scroll_end, animate=False)
        elif event.option_index == chosen[0]:
            self.call_after_refresh(tree.scroll_home, animate=False)

    def tree_plain(self) -> str:
        v = self.view()
        return tree_text(v).plain if v else ""

    def action_move(self, step: int) -> None:
        snap = self.app.snapshot
        if snap and snap.agents:
            self.index = (self.index + step) % len(snap.agents)
            self.show(snap, reset=True)

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.action_change()

    def action_change(self) -> None:
        i = self.query_one("#tree", OptionList).highlighted
        v = self.view()
        if v is None or i is None or not (0 <= i < len(self.settings)) or not self.settings[i]:
            return
        self.app.open_setting(v, self.settings[i])


class Desk(App):
    """The Agent Control Desk. `collector` returns a Snapshot; tests pass their own, plus their own EditPaths."""
    TITLE = "Agent Control Desk"
    CSS = "Screen { background: #0d1117; color: #e6edf3; }"
    BINDINGS = [Binding("q", "quit", "close"), Binding("c", "reread", "read again")]

    def __init__(self, collector: Optional[Callable[[], D.Snapshot]] = None, autostart: bool = True,
                 edit: Optional[E.EditPaths] = None, loader: Optional[E.Loader] = None,
                 tester: Optional[Callable[[A.Agent, str, str], tuple[bool, str]]] = E.real_voice_test,
                 launcher_armed: bool = False, runner: Optional[Callable[[list], tuple]] = None,
                 usage: Optional[H.UsageReader] = None, watching: bool = True, listener: Any = None,
                 synth: Optional[Callable] = None, probe: Optional[Callable] = None) -> None:
        super().__init__()
        # the voice line's own plumbing. A desk built with its own collector (a test, a picture) gets none unless
        # it is handed one, so it can never reach the real listener by accident.
        self.listener_svc = listener if listener is not None else (P.RealListener() if collector is None else None)
        self.listener_now: Optional[P.ListenerView] = None
        self.synth, self.probe = synth, probe                    # tests pass a fake voice maker
        self.runner = runner                                     # tests pass a fake Team Room command
        self.usage = usage                                       # the ONE shared allowance reader (None = not asked)
        self.watching = watching                                 # False = the watcher never acts from this desk
        self.snapshot: Optional[D.Snapshot] = None
        self.autostart = autostart
        self.edit = edit or E.EditPaths.default(launcher_armed=launcher_armed)
        self.loader, self.tester = loader, tester
        self.last_results: list[E.Result] = []
        if collector is None:
            paths = D.Paths.default()
            reader = D.LogReader(paths.profiles)                 # ONE reader, kept for the life of the desk
            edit_paths = self.edit
            if self.usage is None and os.environ.get("ACD_USAGE", "on") != "off":
                self.usage = H.UsageReader(keep=self.edit.state / "allowance_last_known.json")
            usage_reader = self.usage

            def collector() -> D.Snapshot:
                got = usage_reader.cached() if usage_reader is not None else None
                return D.collect(paths, reader, desk=E.record(edit_paths), usage=got or None,
                                 watch=W.view(edit_paths))
        self.collector = collector

    def on_mount(self) -> None:
        self.push_screen(GridScreen())
        self.look_at_listener()
        if self.autostart:
            self.action_reread()
            self.set_interval(REFRESH_SEC, self.action_reread)
            if self.listener_svc is not None:
                self.set_interval(LISTENER_SEC, self.look_at_listener)

    def look_at_listener(self) -> None:
        """The voice line's state for the status line: ONE small file read, never the microphone, never a command."""
        if self.listener_svc is None:
            return
        try:
            now = P.listener_view(self.listener_svc, ask_loaded=False)
        except Exception:                                # noqa: BLE001  a failed look must never take the desk down
            return
        if now == self.listener_now:
            return
        self.listener_now = now
        if self.snapshot is None:
            return
        for screen in self.screen_stack:
            if isinstance(screen, (GridScreen, TreeScreen)):
                try:
                    screen.query_one("#systems", Static).update(
                        systems_text(self.snapshot, self.text_width(), now))
                except Exception:                        # noqa: BLE001
                    pass

    @property
    def is_armed(self) -> bool:
        return E.armed(self.edit)

    def text_width(self) -> int:
        """How many characters fit on one line of the strips at the top (they pad one column on each side)."""
        return max(20, self.size.width - 2)

    def title_text(self) -> Text:
        t = Text("AGENT CONTROL DESK", style="bold")
        if self.is_armed:
            t.append("     " + ARMED_LABEL, style=COLOUR[D.RED])
        else:
            t.append("     " + DRY_LABEL, style=COLOUR[D.YELLOW])
        if self.snapshot:
            t.append("     read " + dt.datetime.fromtimestamp(self.snapshot.read_at).strftime("%a %-d %b %H:%M:%S"),
                     style=DIM)
        return t

    def action_reread(self) -> None:
        self.read_in_background()

    @work(thread=True, exclusive=True, group="acd-read")
    def read_in_background(self) -> None:
        self.watch_step()
        try:
            snap = self.collector()
        except Exception as e:                       # noqa: BLE001  a failed read must never take the desk down
            self.call_from_thread(self.read_failed, f"{type(e).__name__}: {e}")
            return
        self.call_from_thread(self.apply_snapshot, snap)

    def watch_step(self) -> None:
        """The watcher's own look, on the same slow timer as the read. It runs only on an ARMED desk (an unarmed
        desk writes nothing at all), and it changes a setting only when automatic put-back is switched on too."""
        if not self.watching or not self.is_armed:
            return
        snap = self.snapshot
        overrides = {v.profile: v.override.model for v in snap.agents if v.override} if snap else {}
        try:
            self.last_watch = W.run_once(self.edit, act=True, loader=self.loader or E.default_loader(),
                                         overrides=overrides)
        except Exception:                            # noqa: BLE001  the watcher must never take the desk down
            self.last_watch = None

    def usage_services(self) -> dict:
        """service -> one agent that uses it (whose sign-in the usage check is asked as)."""
        out: dict = {}
        for v in (self.snapshot.agents if self.snapshot else ()):
            for h in v.health:
                out.setdefault(h.service, v.profile)
        return out

    @work(thread=True, exclusive=True, group="acd-usage")
    def read_usage_in_background(self) -> None:
        """Hermes' own usage check, at most once per service every ten minutes, never on the screen's thread."""
        try:
            ran = self.usage.refresh(self.usage_services()) if self.usage is not None else 0
        except Exception:                            # noqa: BLE001
            ran = 0
        if ran:
            self.call_from_thread(self.action_reread)

    def read_failed(self, why: str) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, (GridScreen, TreeScreen)):
                screen.query_one("#warnings", Static).update(line(D.RED, f"this read failed ({why[:160]}); "
                                                                         "showing the last good one"))

    def apply_snapshot(self, snap: D.Snapshot) -> None:
        self.snapshot = snap
        for screen in self.screen_stack:
            if isinstance(screen, (GridScreen, TreeScreen)):
                screen.show(snap)
        if self.usage is not None and self.autostart and self.usage.due(self.usage_services()):
            self.read_usage_in_background()

    def open_tree(self, index: int) -> None:
        if self.snapshot and self.snapshot.agents:
            self.push_screen(TreeScreen(index))

    # -- changing a setting ------------------------------------------------------------------------------------
    def voice_agent(self, v) -> tuple[Optional[A.Agent], dict]:
        try:
            cfg = A.load(self.edit.voice.toml)
        except (OSError, ValueError):
            return None, {}
        key = getattr(v, "voice_key", "") or getattr(v, "key", "")
        return next((a for a in A.agents(cfg) if a.key == key), None), cfg

    def open_setting(self, v: D.AgentView, setting: str) -> None:
        build = {"model": self.model_popup, "backups": self.backups_popup, "voice": self.voice_popup,
                 "voice_zh": self.voice_popup, "speed": self.voice_popup, "effect": self.voice_popup,
                 "team_room": self.team_room_popup, "talk": self.talk_popup}.get(setting)
        if setting.startswith("drift:"):
            build = self.drift_popup
        elif setting.startswith("vplace:"):
            build = self.place_popup
        if not build:
            return
        popup, jobs_for = build(v, setting)
        self.push_screen(popup, lambda changes: self.run_jobs(jobs_for(changes)) if changes else None)

    def model_popup(self, v: D.AgentView, _setting: str):
        edit, loader, dry = self.edit, self.loader, not self.is_armed
        if v.model_locked or not v.model:
            why = (f"{v.name}'s model is locked: it is shown here, never changed from the desk." if v.model
                   else "The model could not be read, so it cannot be changed from here.")
            return EditPopup(f"{v.name}: language model", [], lambda c: [], locked=why, dry=dry), lambda c: []
        ms = v.model
        efforts = list(E.EFFORTS) if ms.effort in dict(E.EFFORTS) else [(ms.effort, effort_words(ms.effort))] + list(E.EFFORTS)
        groups = [
            Group("model", f"Model, on {D.service_name(ms.service)}", "radio",
                  [Opt(m, D.model_name(m), m == ms.model) for m in E.model_choices(ms.service, ms.model)]),
            Group("effort", "Thinking effort", "radio", [Opt(e, words, e == ms.effort) for e, words in efforts])]

        def describe(c: dict) -> list[str]:
            out = []
            if "model" in c:
                out.append(f"{v.name}'s model changes from {D.model_name(ms.model)} to {D.model_name(c['model'])}.")
            if "effort" in c:
                out.append(f"{v.name}'s thinking effort changes from {ms.effort} to {c['effort']}.")
            return out

        def jobs(c: dict) -> list:
            out = []
            if "model" in c:
                out.append(lambda: E.change_model(v.profile, c["model"], edit, loader=loader))
            if "effort" in c:
                out.append(lambda: E.change_effort(v.profile, c["effort"], edit, loader=loader))
            return out

        note = "Pick a model and how hard it thinks."
        if v.model_note:
            note += f" Note for {v.name}: {v.model_note}."
        return EditPopup(f"{v.name}: language model", groups, describe, note=note, dry=dry), jobs

    def backups_popup(self, v: D.AgentView, _setting: str):
        edit, loader, dry = self.edit, self.loader, not self.is_armed
        if v.backups_locked:
            return (EditPopup(f"{v.name}: backups", [], lambda c: [], dry=dry,
                              locked=f"{v.name} has no backups by design, and the desk does not change that."),
                    lambda c: [])
        pairs = {f"{b.service}|{b.model}": b for b in v.backups}
        group = Group("backups", "Backups, in the order they are tried", "toggle",
                      [Opt(tok, f"{b.position if b.on else '-'}  {backup_words(b, short=False)}", b.on, note=b.note)
                       for tok, b in pairs.items()])

        def describe(c: dict) -> list[str]:
            return [f"{v.name}'s backup {backup_words(pairs[tok], short=False)} is switched {'on' if on else 'off'}"
                    + ("." if on else " (the desk remembers it, so it can be switched on again).")
                    for tok, on in c.get("backups", {}).items()]

        def jobs(c: dict) -> list:
            return [(lambda tok=tok, on=on: E.set_backup(v.profile, pairs[tok].service, pairs[tok].model, on, edit,
                                                         loader=loader))
                    for tok, on in c.get("backups", {}).items()]

        return EditPopup(f"{v.name}: backups", [group], describe, dry=dry,
                         note="A backup answers only when the model before it fails. Space switches one on or off."), jobs

    def drift_popup(self, v: D.AgentView, setting: str):
        """Something changed a setting behind the desk's back: put it back, or accept it."""
        edit, loader, dry = self.edit, self.loader, not self.is_armed
        i = int(setting.split(":", 1)[1])
        if i >= len(v.drifts):
            return EditPopup(f"{v.name}: changed", [], lambda c: [], locked="That change is no longer there.", dry=dry), \
                lambda c: []
        d = v.drifts[i]
        group = Group("answer", "What should the desk do?", "radio", [
            Opt("put", f"put it back: {d.words(d.want)} again (what the desk set)"),
            Opt("accept", f"accept it: keep {d.words(d.have)}, and the desk expects that from now on")])

        def describe(c: dict) -> list[str]:
            if c.get("answer") == "put":
                return [f"{v.name}'s {d.what} is put back to {d.words(d.want)}, through the desk's own safe path "
                        "(backup, read back, u undoes it)."]
            if c.get("answer") == "accept":
                return [f"{v.name}'s {d.what} stays {d.words(d.have)}. Only the desk's own record changes; "
                        "no agent's settings file is touched."]
            return []

        def jobs(c: dict) -> list:
            if c.get("answer") == "put":
                return [lambda: W.put_back(d, edit, loader=loader)]
            if c.get("answer") == "accept":
                return [lambda: W.accept(d, edit)]
            return []

        return EditPopup(f"{v.name}: {d.what} was changed behind the desk's back", [group], describe,
                         note=d.sentence, dry=dry), jobs

    def place_popup(self, v, setting: str):
        """Spoken replies at one place: the old Voice Desk grid's own switch (its code does the change)."""
        edit, dry = self.edit, not self.is_armed
        place = setting.split(":", 1)[1]
        key = getattr(v, "voice_key", "") or getattr(v, "key", "")
        name = D.place_words(place)
        try:
            title, options, why = E.voice_place_menu(key, place, edit)
        except Exception as e:                           # noqa: BLE001
            title, options, why = "", [], f"the switch could not be read ({type(e).__name__})"
        if not options:
            return (EditPopup(f"{v.name}: spoken replies, {name}", [], lambda c: [], dry=dry,
                              locked=(why or "nothing to switch here")[:1].upper() + (why or "nothing to switch here")[1:]),
                    lambda c: [])
        labels = {tok: label for tok, label, _ in options}
        group = Group("place", title or name, "radio", [Opt(tok, label, cur) for tok, label, cur in options])

        def describe(c: dict) -> list[str]:
            if "place" not in c:
                return []
            return [f"{v.name}: spoken replies, {name}: {labels[c['place']]}.",
                    "The Voice Desk's own switch code makes the change: every file is backed up, written, read back "
                    "and put back at once if it does not read back."]

        def jobs(c: dict) -> list:
            return [lambda: E.set_voice_place(key, place, c["place"], edit)] if "place" in c else []

        return EditPopup(f"{v.name}: spoken replies, {name}", [group], describe, dry=dry,
                         note="This is the same switch the Voice Desk dashboard had."), jobs

    def voice_popup(self, v, setting: str):
        edit, dry = self.edit, not self.is_armed
        agent, cfg = self.voice_agent(v)
        if agent is None:
            return (EditPopup(f"{v.name}: voice", [], lambda c: [], dry=dry,
                              locked=f"{v.name} has no voice in the Voice Desk list, so there is nothing to change."),
                    lambda c: [])
        if setting == "effect":
            opts = [Opt(tok, label, cur, disabled) for tok, label, cur, disabled in E.voice_options(agent, "effect", cfg)]
            groups = [Group("effect", f"Voice effect on {D.voice_label(agent.voice)}", "radio", opts)]
        elif setting == "speed":
            now = E.speed_word(agent.speed)
            opts = [Opt(word, f"{word} ({value:g})", word == now) for word, value in E.SPEEDS]
            groups = [Group("speed", "Speaking speed" + ("" if any(o.now for o in opts) else f" (now: {now})"),
                            "radio", opts)]
        else:
            lang = "English" if setting == "voice" else "Chinese"
            opts = [Opt(tok, label, cur, disabled) for tok, label, cur, disabled in E.voice_options(agent, setting, cfg)]
            groups = [Group(setting, f"{lang} voice (one voice, silent if it fails)", "radio", opts,
                            testable="en" if setting == "voice" else "zh")]
        speeds = dict(E.SPEEDS)

        def describe(c: dict) -> list[str]:
            out = []
            for key, tok in c.items():
                if key == "speed":
                    out.append(f"{v.name}'s speaking speed changes from {E.speed_word(agent.speed)} to {tok}.")
                elif key == "effect":
                    out.append(f"{v.name}'s voice effect changes from {agent.effect or 'none'} to {tok}.")
                else:
                    before = agent.voice if key == "voice" else agent.voice_zh
                    lang = "English" if key == "voice" else "Chinese"
                    out.append(f"{v.name}'s {lang} voice changes from {D.voice_label(before)} to "
                               f"{D.voice_label(tok) if tok else 'silent'}.")
            return out

        def jobs(c: dict) -> list:
            return [(lambda key=key, tok=tok: E.change_voice(
                        agent.key, key, speeds[tok] if key == "speed" else (tok or None), edit))
                    for key, tok in c.items()]

        tester = (lambda lang, voice: self.tester(agent, lang, voice)) if self.tester else None
        note = "Only the voices this agent's own speaker can make are listed. One voice, silent if it fails."
        lands = getattr(v, "takes_effect", "")
        if lands:
            note += f" Where it lands: {lands}."
        return EditPopup(f"{v.name}: voice", groups, describe, tester=tester, dry=dry, note=note), jobs

    def team_room_popup(self, v: D.AgentView, _setting: str):
        """In or out of the Team Room: ONE switch for the terminal room and the Telegram group together."""
        edit, loader, runner, dry = self.edit, self.loader, self.runner, not self.is_armed
        title = f"{v.name}: Team Room and Telegram team group"
        if v.room_lead or v.in_room is None:
            why = (f"{v.name} leads the Team Room and cannot be taken out of it." if v.room_lead else
                   "The Team Room members file could not be read, so the desk will not change it.")
            return EditPopup(title, [], lambda c: [], locked=why, dry=dry), lambda c: []
        has_bot = any(p.key == "tg_private" and p.state == D.ON for p in v.presence)
        group = Group("room", "The Team Room (terminal) and the Telegram team group: one switch for both", "radio",
                      [Opt("in", "in the room", bool(v.in_room)), Opt("out", "out of the room", not v.in_room)])

        def describe(c: dict) -> list[str]:
            if "room" not in c:
                return []
            joining = c["room"] == "in"
            out = [f"{v.name} {'joins' if joining else 'leaves'} the Team Room: the terminal room and the Telegram "
                   "group change together, or not at all.",
                   "The room is rebuilt, so the chat on screen starts fresh. The old lines stay in the saved history."]
            if has_bot:
                out.append("Putting its bot into the Telegram group is Peter's own tap in Telegram; until then the "
                           "lead's bot speaks for it." if joining else
                           "Its bot is taken out of the Telegram group. Putting it back later is Peter's own tap in Telegram.")
            return out

        def jobs(c: dict) -> list:
            if "room" not in c:
                return []
            return [lambda: E.set_team_room(v.profile, c["room"] == "in", edit, loader=loader, runner=runner)]

        note = ("One switch moves both places. The desk runs the Team Room's own add or kick, checks both doors, and "
                "runs it backwards if they do not agree.")
        return EditPopup(title, [group], describe, note=note, dry=dry), jobs

    def talk_popup(self, v: D.AgentView, _setting: str):
        """Who it may talk to: a few plain choices, kept in the one rules file the Team Room can read."""
        edit, dry = self.edit, not self.is_armed
        rules = {r.key: r for r in v.talk_rules}
        if not rules:
            return (EditPopup(f"{v.name}: who it may talk to", [], lambda c: [], dry=dry,
                              locked="The rules file could not be read, so there is nothing to change."), lambda c: [])
        lead = next((a for a in (self.snapshot.agents if self.snapshot else ()) if a.room_lead), None)
        lead_name, lead_profile = (lead.name, lead.profile) if lead else ("the lead", "orchestrator")
        sp, ho, tk, an = rules["speaks"], rules["hands_out"], rules["takes_from"], rules["answers"]
        groups = [
            Group("speaks", "May speak in the Team Room", "radio",
                  [Opt("yes", "yes", sp.token == "yes"),
                   Opt("no", "no", sp.token == "no", disabled=bool(sp.locked), note=sp.locked)]),
            Group("hands_out", f"Hands out work (only {lead_name} by default)", "radio",
                  [Opt("yes", "yes", ho.token == "yes"), Opt("no", "no", ho.token == "no")]),
            Group("takes_from", "Takes work from", "radio",
                  [Opt("lead", lead_name, tk.token == "lead", disabled=v.room_lead,
                       note="this is the lead" if v.room_lead else ""),
                   Opt("cards", "job cards only", tk.token == "cards"),
                   Opt("nobody", "nobody (Peter only)", tk.token == "nobody")]),
            Group("answers", "Answers", "radio",
                  [Opt("always", "always", an.token == "always", disabled=bool(an.locked), note=an.locked),
                   Opt("when_named", "when named in the line", an.token == "when_named", disabled=bool(an.locked),
                       note=an.locked)])]
        takes_words = {"lead": f"takes work from {lead_name}", "cards": "takes work only as job cards",
                       "nobody": "takes work from nobody (Peter only)"}

        def describe(c: dict) -> list[str]:
            out = []
            if "speaks" in c:
                out.append(f"{v.name} {'may' if c['speaks'] == 'yes' else 'may not'} speak in the Team Room.")
            if "hands_out" in c:
                out.append(f"{v.name} {'hands' if c['hands_out'] == 'yes' else 'does not hand'} out work.")
            if "takes_from" in c:
                out.append(f"{v.name} {takes_words[c['takes_from']]}.")
            if "answers" in c:
                out.append(f"{v.name} answers {'every line' if c['answers'] == 'always' else 'when named in the line'}.")
            if out:
                out.append("The rule is written into the desk's rules file. " + (
                    "The Team Room reads that file." if self.snapshot and self.snapshot.room_follows_rules else
                    "The Team Room does not read that file yet: recorded, not yet enforced."))
            return out

        def changes_for(c: dict) -> dict:
            out: dict = {}
            if "speaks" in c:
                out["speaks_in_team_room"] = c["speaks"] == "yes"
            if "hands_out" in c:
                out["hands_out_work"] = c["hands_out"] == "yes"
            if "takes_from" in c:
                out["only_job_cards"] = c["takes_from"] == "cards"
                out["takes_work_from"] = "" if c["takes_from"] == "nobody" else lead_profile
            if "answers" in c:
                out["answers"] = c["answers"]
            return out

        def jobs(c: dict) -> list:
            todo = changes_for(c)
            return [lambda: E.change_rules(v.profile, todo, edit)] if todo else []

        note = ("Each line of the tree says where a rule stands: how the room works today, enforced, or recorded and "
                "not yet enforced.")
        return EditPopup(f"{v.name}: who it may talk to", groups, describe, note=note, dry=dry), jobs

    @work(thread=True, group="acd-change")
    def run_jobs(self, jobs: list) -> None:
        results = []
        for job in jobs:
            try:
                results.append(job())
            except Exception as e:                       # noqa: BLE001  a failed change must never take the desk down
                results.append(E.Result(False, f"Not changed. {type(e).__name__}: {e}"))
        self.call_from_thread(self.show_results, results)

    def show_results(self, results: list[E.Result]) -> None:
        self.last_results = results
        if results:
            title, body = results_text(results)
            self.push_screen(NoticePopup(title, body))
        self.action_reread()

    def action_assistants(self) -> None:
        """Claude, Gemini and Codex: pick one of their voice settings, then the usual pop-up opens."""
        snap = self.snapshot
        if snap is None or not snap.others:
            self.push_screen(NoticePopup("Assistants' voices", Text("No assistant is in the Voice Desk list.")))
            return
        rows: list[tuple[str, str]] = []
        for o in snap.others:
            rows.append(("", f"{o.name}   ({o.takes_effect})"))
            rows.append((f"{o.key}|voice", f"    English voice: {D.voice_label(o.english)}"))
            rows.append((f"{o.key}|voice_zh", f"    Chinese voice: {D.voice_label(o.chinese)}"))
            for p in o.voice_places:
                if p.can_switch:
                    rows.append((f"{o.key}|vplace:{p.key}",
                                 f"    Spoken replies, {D.place_words(p.key)}: {p.value}"))
        by_key = {o.key: o for o in snap.others}

        def picked(token: Optional[str]) -> None:
            if not token:
                return
            key, setting = token.split("|", 1)
            build = self.place_popup if setting.startswith("vplace:") else self.voice_popup
            popup, jobs_for = build(by_key[key], setting)
            self.push_screen(popup, lambda changes: self.run_jobs(jobs_for(changes)) if changes else None)

        self.push_screen(PickPopup("Claude, Gemini and Codex: voices", rows,
                                   note="Called by name through the Voice Desk. Pick a setting to change."), picked)

    # -- what only the old dashboard could do: the voice test, add / remove an agent, the listener ------------------
    def chosen(self) -> Optional[D.AgentView]:
        """The agent under the cursor: the grid's row, or the tree's agent."""
        snap = self.snapshot
        if snap is None or not snap.agents:
            return None
        for screen in reversed(self.screen_stack):
            if isinstance(screen, TreeScreen):
                return screen.view()
            if isinstance(screen, GridScreen):
                row = screen.query_one("#grid", DataTable).cursor_row
                return snap.agents[row] if 0 <= row < len(snap.agents) else None
        return None

    def dry_line(self) -> Text:
        if self.is_armed:
            return Text("The desk is ARMED: this is really done.", style=COLOUR[D.RED])
        return Text(f"DRY RUN ({E.why_not_armed(self.edit)}): the desk only shows what would happen.",
                    style=COLOUR[D.YELLOW])

    def action_voice_test(self) -> None:
        """t: the silent voice test, for the chosen agent or for everyone. Nothing is ever played."""
        edit = self.edit
        v = self.chosen()
        rows: list[tuple[str, str]] = []
        if v is not None and v.voice_key:
            rows.append((v.voice_key, f"only {v.name}"))
        rows.append(("*", "everyone in the Voice Desk list"))

        def picked(token: Optional[str]) -> None:
            if not token:
                return
            keys = None if token == "*" else [token]
            try:
                plan, silent = P.test_plan(edit, keys)
            except (E.EditError, OSError, ValueError) as exc:
                self.push_screen(NoticePopup("Voice test", Text(f"The Voice Desk list could not be read ({exc}).")))
                return
            body = [Text("One sentence is made in each voice to a throw-away file and checked for real sound."),
                    Text("NOTHING IS PLAYED: no sound comes out of the Mac.", style="bold"), Text(""),
                    Text("What would be said, and in which voice:", style=DIM)]
            body += [Text("  " + ln) for ln in P.plan_lines(plan, silent)]
            body += [Text(""), self.dry_line()]
            self.push_screen(
                NoticePopup("Voice test", join(body), ask="yes, run the silent test"),
                lambda yes: self.run_jobs([lambda: P.test_voices(edit, keys, synth=self.synth, probe=self.probe)])
                if yes else None)

        self.push_screen(PickPopup("Voice test: who?", rows,
                                   note="A silent test: the voices are made to files and checked, never played."),
                         picked)

    def action_add_agent(self) -> None:
        """n: a new agent in the Voice Desk list: which one, its name, what it answers to, its voice, then one
        confirm line that says exactly what is added."""
        edit = self.edit
        rows = [(prof, f"{name}: the Hermes agent \"{prof}\" on this Mac") for prof, name in P.addable(edit)]
        rows.append((P.CLI, "another assistant that runs in a terminal (like Claude, Gemini or Codex)"))

        def confirm(profile: Optional[str], display: str, names: str, voice: str) -> None:
            body = [Text("This is added to the Voice Desk list, and nothing else is touched:"),
                    Text(f"  name            {display}"),
                    Text(f"  answers to      {', '.join(gridedit.parse_names(names, display))}"),
                    Text(f"  voice           {D.voice_label(voice) if voice else 'silent: no voice'}"),
                    Text("  it is           " + (f"the Hermes agent {profile}" if profile
                                                 else "another terminal assistant")),
                    Text(""), Text(P.NEEDS_AGENT, style=DIM), Text(""), self.dry_line()]
            self.push_screen(
                NoticePopup(f"Add {display}?", join(body), ask=f"yes, add {display}"),
                lambda yes: self.run_jobs([lambda: P.add_agent(edit, display, names, voice or None, profile=profile)])
                if yes else None)

        def ask_voice(profile: Optional[str], display: str, names: str) -> None:
            try:
                cfg = A.load(edit.voice.toml)
                options = E.voice_options(P.draft_agent(display, profile), "voice", cfg)
            except Exception as exc:                     # noqa: BLE001
                self.push_screen(NoticePopup("Add an agent", Text(f"The voices could not be listed ({exc}).")))
                return
            voices = [(tok or SILENT, label) for tok, label, _cur, off in options if not off]
            if not any(tok == SILENT for tok, _ in voices):
                voices.append((SILENT, "silent: no voice"))
            self.push_screen(PickPopup(f"{display}: its English voice", voices,
                                       note="One voice, silent if it fails. A Chinese voice can be added later in its tree."),
                             lambda tok: confirm(profile, display, names, "" if tok == SILENT else tok) if tok else None)

        def ask_names(profile: Optional[str], display: str) -> None:
            self.push_screen(AskPopup(f"{display}: what it answers to",
                                      "Every way the listener may hear the name, separated by commas. The name "
                                      "itself is always one of them.", display.lower()),
                             lambda text: ask_voice(profile, display, text) if text is not None else None)

        def ask_name(token: Optional[str]) -> None:
            if not token:
                return
            profile = None if token == P.CLI else token
            start = next((name for prof, name in P.addable(edit) if prof == profile), "")

            def named(text: Optional[str]) -> None:
                if text is None:
                    return
                if not text:
                    self.push_screen(NoticePopup("Add an agent", Text("A new agent needs a name. Nothing was added.")))
                    return
                ask_names(profile, text)

            self.push_screen(AskPopup("New agent: its name", "The name shown on the desk and spoken back.", start), named)

        self.push_screen(PickPopup("Add an agent to the Voice Desk list", rows,
                                   note="Only the Voice Desk list gets a new block. No agent's own files change."),
                         ask_name)

    def action_remove_agent(self) -> None:
        """d: take an agent out of the Voice Desk list, after a confirm line that shows exactly what goes."""
        edit = self.edit
        try:
            listed = A.agents(A.load(edit.voice.toml))
        except (OSError, ValueError) as exc:
            self.push_screen(NoticePopup("Remove an agent", Text(f"The Voice Desk list could not be read ({exc}).")))
            return
        v = self.chosen()
        first = v.voice_key if v is not None else ""
        rows = sorted(((a.key, a.display) for a in listed), key=lambda r: r[0] != first)

        def picked(key: Optional[str]) -> None:
            if not key:
                return
            try:
                plan = P.removal_plan(edit, key)
            except Exception as exc:                     # noqa: BLE001
                self.push_screen(NoticePopup("Remove an agent", Text(f"Nothing was removed ({exc}).")))
                return
            body = [Text(f"Exactly these {len(plan.block)} lines are removed from the Voice Desk list:")]
            body += [Text("  - " + ln, style=COLOUR[D.RED]) for ln in plan.block]
            body += [Text("")] + [Text(part) for part in textwrap.wrap(plan.stays, 92)]
            body += [Text("u puts it back.", style=DIM), Text(""), self.dry_line()]
            self.push_screen(
                NoticePopup(f"Remove {plan.name} from the Voice Desk list?", join(body),
                            ask=f"yes, remove {plan.name}"),
                lambda yes: self.run_jobs([lambda: P.remove_agent(edit, key)]) if yes else None)

        self.push_screen(PickPopup("Remove which agent from the Voice Desk list?", rows,
                                   note="The next step shows exactly what would be removed, and asks first."), picked)

    def action_listener(self) -> None:
        """l: the voice line: what the listener is doing, and its controls."""
        self.push_screen(ListenerPopup())

    def listener_order(self, action: str) -> None:
        edit, svc = self.edit, self.listener_svc
        if svc is None:
            return
        if action not in P.NEEDS_TWO:
            self.run_jobs([lambda: P.listener_do(action, edit, svc=svc)])
            return
        view = P.listener_view(svc, ask_loaded=False)
        verb = "Restart" if action == "restart" else "Stop"
        if view.busy:
            self.push_screen(NoticePopup(f"{verb} the listener", join([
                Text(f"Not now: {view.busy}.", style=COLOUR[D.RED]),
                Text("The desk never stops or restarts the voice line in the middle of that. Try again when it is quiet.")])))
            return
        first = [Text(f"{verb} the listener?"), Text(P.STOPS_THE_LINE[0].upper() + P.STOPS_THE_LINE[1:] + ".",
                                                      style=COLOUR[D.RED]),
                 Text(P.ACTIONS[action][0].upper() + P.ACTIONS[action][1:] + "."), Text(""),
                 Text("This is the first of two questions.", style=DIM)]
        second = [Text("Second and last question.", style="bold"),
                  Text(P.STOPS_THE_LINE[0].upper() + P.STOPS_THE_LINE[1:]
                       + ": nobody can talk to an agent by voice until it is running again.", style=COLOUR[D.RED]),
                  Text(""), self.dry_line()]

        def again(yes: bool) -> None:
            if yes:
                self.push_screen(
                    NoticePopup(f"{verb} the listener: are you sure?", join(second),
                                ask=f"yes, {action} the listener now"),
                    lambda sure: self.run_jobs([lambda: P.listener_do(action, edit, svc=svc, confirmed=2)])
                    if sure else None)

        self.push_screen(NoticePopup(f"{verb} the listener", join(first), ask="yes, go on"), again)

    def action_pause_watcher(self) -> None:
        """P: pause or resume the watcher. While paused it reports nothing and puts nothing back. The pause is the
        desk's own small file, so, like every other change, it is written only on an armed desk."""
        edit = self.edit
        paused = not W.is_paused(edit)
        word = "paused" if paused else "resumed"
        if not self.is_armed:
            self.push_screen(NoticePopup("Watcher", join([
                Text(f"DRY RUN, nothing written ({E.why_not_armed(edit)}). This would happen: the watcher is {word}"
                     + (": it reports nothing and puts nothing back until P is pressed again." if paused else ".")),
                Text("On a desk that is not armed the watcher only reports, and never changes a setting.", style=DIM)])))
            return
        try:
            W.set_paused(edit, paused)
        except OSError as e:
            self.push_screen(NoticePopup("Watcher", Text(f"The pause could not be saved ({e.strerror or e}).")))
            return
        out = [Text("The watcher is PAUSED: it reports nothing and puts nothing back. p resumes it." if paused else
                    "The watcher is running again: it reports what was changed behind the desk's back.")]
        if edit.grid is not None:
            try:
                import watcher as voice_watcher
                voice_watcher.set_paused(edit.grid, paused)
                out.append(Text(f"The Voice Desk's own watcher (the voice switches) was {word} as well.", style=DIM))
            except Exception as e:                       # noqa: BLE001
                out.append(Text(f"The Voice Desk's own watcher could not be {word} ({type(e).__name__}).", style=DIM))
        self.push_screen(NoticePopup("Watcher", join(out)))
        self.action_reread()

    def action_undo(self) -> None:
        edit, loader, runner = self.edit, self.loader, self.runner
        last = E.last_undoable(edit)
        if last is None:
            self.push_screen(NoticePopup("Undo", Text("There is no change from the desk to undo.")))
            return
        body = join([Text("The newest change from the desk:"), Text("  " + str(last.get("what"))), Text(""),
                     Text("Undo runs it backwards through the same safe path (backup, read back).", style=DIM)])
        self.push_screen(NoticePopup("Undo the newest change?", body, ask="yes, undo it"),
                         lambda yes: self.run_jobs([lambda: E.undo(edit, loader=loader, runner=runner)]) if yes else None)

    def action_restart_review(self) -> None:
        edit = self.edit
        waiting = list(self.snapshot.waiting) if self.snapshot else []
        out = []
        if waiting:
            out.append(Text(f"{len(waiting)} saved change{'s are' if len(waiting) != 1 else ' is'} waiting for a restart:",
                            style=COLOUR[D.YELLOW]))
            out += [Text("  - " + w) for w in waiting]
        else:
            out.append(Text("No saved change is waiting for a restart."))
            out.append(Text("Model, backup and voice changes need none: new chats pick them up; an open terminal chat "
                            "keeps its old setting until a fresh chat is started.", style=DIM))
            out.append(Text("A Team Room change needs one only when the agents' system did not reload its add-ons; "
                            "the desk lists it here when that happens.", style=DIM))
        out.append(Text(""))
        out += [Text(part) for part in textwrap.wrap(E.RESTART_WORDS, 92)]
        if edit.restart_request.exists():
            out.append(Text("A restart request is already waiting for a quiet moment.", style=COLOUR[D.YELLOW]))
        if not self.is_armed:
            out.append(Text(f"The desk is in dry run ({E.why_not_armed(edit)}): Y only shows the request, it is not written.",
                            style=COLOUR[D.YELLOW]))
        reason = "; ".join(waiting)[:180] if waiting else "asked for from the Agent Control Desk"
        self.push_screen(NoticePopup("Restart review", join(out), ask="leave a restart request for the helper"),
                         lambda yes: self.run_jobs([lambda: E.request_restart(edit, reason)]) if yes else None)


if __name__ == "__main__":
    Desk(launcher_armed="--armed" in sys.argv[1:]).run()
