"""Agent Control Desk, version two: the screen.

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
  both   U  undo the newest change      R  restart review    C  read again now    Q  close

It is an overseer, never a lifeline: it reads gently (ONE background read every 45 seconds for all agents),
every change goes through acd_edit.py (backup, atomic write, read back, auto-restore, history, undo), and it
never restarts anything itself: R only leaves a request for the existing restart helper.
"""
from __future__ import annotations

import datetime as dt
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Callable, Optional

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, OptionList, Static
from textual.widgets.option_list import Option

import agents as A
import acd_data as D
import acd_edit as E

REFRESH_SEC = 45
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
    if v.model_behind:
        out.append(Text("  changed somewhere else", style=DIM))
    elif v.model_pending:
        out.append(Text("  saved, not live everywhere", style=DIM))
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
        t.append("on  " if b.on else "off ", style=COLOUR[D.GREEN] if b.on else DIM)
        t.append(backup_words(b), style="" if b.on else DIM)
        out.append(t)
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
            out.append(Text(f"? {SHORT_PLACE[p.key]}", style=DIM))
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
        if v.model_behind:
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
    if v.signin_failed and v.model:
        add(_leaves("│  ├─ ", "Sign-in", D.RED,
                    f"{D.service_name(v.model.service)} could not sign in during the last day "
                    f"({D.plain_reason(v.signin_failed)})"))
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
        t.append("on   " if b.on else "off  ", style=COLOUR[D.GREEN] if b.on else DIM)
        t.append(backup_words(b, short=False), style="" if b.on else DIM)
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
        add(_leaf("│  └─ ", "If the voice fails", Text("stays silent, no stand-in voice"), 20))
    else:
        add(Text("│  └─ no voice in the Voice Desk list", style=DIM))

    add(Text("├─ Where it shows up", style="bold"))
    for i, p in enumerate(v.presence):
        branch = "└─ " if i == len(v.presence) - 1 else "├─ "
        last = i == len(v.presence) - 1
        add(_leaves("│  " + branch, D.PLACE_NAME[p.key], p.status if p.state == D.ON else None,
                    f"{state_word(p)}: {p.evidence}", 23, dim=p.state != D.ON,
                    more="│     " if last else "│  │  "))

    add(Text("└─ Who it may talk to", style="bold"))
    for i, (what, answer) in enumerate(v.talks):
        branch = "└─ " if i == len(v.talks) - 1 else "├─ "
        add(_leaf("   " + branch, what, Text(answer), 26))
    return rows


def tree_text(v: D.AgentView) -> Text:
    """The whole tree of one agent as one text (what the tree screen shows, line for line)."""
    return join([ln for lines, _ in tree_rows(v) for ln in lines])


def agent_list_text(snap: D.Snapshot, index: int) -> Text:
    out = [Text("AGENTS", style=DIM)]
    for i, v in enumerate(snap.agents):
        t = Text()
        t.append("> " if i == index else "  ", style="bold" if i == index else "")
        t.append(v.name.ljust(14), style="bold reverse" if i == index else "")
        t.append(" ")
        t.append(MARK, style=COLOUR.get(v.worst, DIM))
        out.append(t)
    out += [Text(""), Text(snap.room_note, style=DIM)]
    return join(out)


def others_text(snap: D.Snapshot) -> Text:
    if not snap.others:
        return Text("")
    out = [Text("Called by name through the Voice Desk (voice and call names only)", style=DIM)]
    for o in snap.others:
        t = Text()
        t.append(f"  {o.name.ljust(8)}", style="bold")
        t.append(f"voice {D.voice_label(o.english)}")
        if o.chinese:
            t.append(f", Chinese {D.voice_label(o.chinese)}")
        t.append("   answers to: " + ", ".join(o.call_names), style=DIM)
        out.append(t)
    return join(out)


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
                if self.dry else "This will be WRITTEN now (backup first, read back, U undoes it).")
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
#keys { height: 1; padding: 0 1; background: #161b22; color: #8b949e; dock: bottom; }
"""
_SHARED_KEYS = [Binding("c", "app.reread", "read again", show=False), Binding("q", "app.quit", "close", show=False),
                Binding("u", "app.undo", "undo", show=False), Binding("r", "app.restart_review", "restart", show=False)]


class GridScreen(Screen):
    """Home: one row per agent."""
    CSS = _TOP_CSS + """
    #grid { height: 1fr; }
    #grid > .datatable--cursor { background: #30363d; color: #e6edf3; }
    #grid > .datatable--header { background: #161b22; color: #8b949e; }
    #others { height: auto; padding: 1 1 0 1; }
    """
    BINDINGS = list(_SHARED_KEYS)

    def compose(self) -> ComposeResult:
        yield Static("", id="title")
        yield Static("reading every agent's settings and log ...", id="warnings")
        yield Static(legend_text(), id="legend")
        yield DataTable(id="grid", cursor_type="row", zebra_stripes=False)
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
            "up/down choose agent   Enter open the agent's tree (change settings there)   U undo   R restart review   "
            "C read again   Q close" + (f"   ({hidden} columns hidden: window is narrow, the tree has them)"
                                         if hidden else "   ? = could not be read yet"))
        if snap is None:
            return
        self.query_one("#warnings", Static).update(warnings_text(snap))
        keep = table.cursor_row
        table.clear()
        for v in snap.agents:
            cells = [build(v) for _, build in cols]
            table.add_row(*cells, height=max(c.plain.count("\n") + 1 for c in cells) + 1, key=v.profile)
        if snap.agents:
            table.move_cursor(row=min(keep, len(snap.agents) - 1))
        self.query_one("#others", Static).update(others_text(snap))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.open_tree(event.cursor_row)


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
        with Horizontal(id="body"):
            yield Static("", id="agents")
            yield OptionList(id="tree")
        yield Static(f"{CAN_CHANGE} = can be changed: up/down choose, Enter change   left/right other agent   "
                     "Esc back to the grid   U undo   R restart review   C read again   Q close", id="keys")

    def on_mount(self) -> None:
        self.query_one("#tree", OptionList).focus()
        self.show(self.app.snapshot)

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
                 launcher_armed: bool = False) -> None:
        super().__init__()
        self.snapshot: Optional[D.Snapshot] = None
        self.autostart = autostart
        self.edit = edit or E.EditPaths.default(launcher_armed=launcher_armed)
        self.loader, self.tester = loader, tester
        self.last_results: list[E.Result] = []
        if collector is None:
            paths = D.Paths.default()
            reader = D.LogReader(paths.profiles)                 # ONE reader, kept for the life of the desk
            edit_paths = self.edit
            collector = lambda: D.collect(paths, reader, desk=E.record(edit_paths))     # noqa: E731
        self.collector = collector

    def on_mount(self) -> None:
        self.push_screen(GridScreen())
        if self.autostart:
            self.action_reread()
            self.set_interval(REFRESH_SEC, self.action_reread)

    @property
    def is_armed(self) -> bool:
        return E.armed(self.edit)

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
        try:
            snap = self.collector()
        except Exception as e:                       # noqa: BLE001  a failed read must never take the desk down
            self.call_from_thread(self.read_failed, f"{type(e).__name__}: {e}")
            return
        self.call_from_thread(self.apply_snapshot, snap)

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

    def open_tree(self, index: int) -> None:
        if self.snapshot and self.snapshot.agents:
            self.push_screen(TreeScreen(index))

    # -- changing a setting ------------------------------------------------------------------------------------
    def voice_agent(self, v: D.AgentView) -> tuple[Optional[A.Agent], dict]:
        try:
            cfg = A.load(self.edit.voice.toml)
        except (OSError, ValueError):
            return None, {}
        return next((a for a in A.agents(cfg) if a.key == v.voice_key), None), cfg

    def open_setting(self, v: D.AgentView, setting: str) -> None:
        build = {"model": self.model_popup, "backups": self.backups_popup, "voice": self.voice_popup,
                 "voice_zh": self.voice_popup, "speed": self.voice_popup}.get(setting)
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

    def voice_popup(self, v: D.AgentView, setting: str):
        edit, dry = self.edit, not self.is_armed
        agent, cfg = self.voice_agent(v)
        if agent is None:
            return (EditPopup(f"{v.name}: voice", [], lambda c: [], dry=dry,
                              locked=f"{v.name} has no voice in the Voice Desk list, so there is nothing to change."),
                    lambda c: [])
        if setting == "speed":
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
        return EditPopup(f"{v.name}: voice", groups, describe, tester=tester, dry=dry,
                         note="Only the voices this agent's own speaker can make are listed. One voice, silent if it fails."), jobs

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

    def action_undo(self) -> None:
        edit, loader = self.edit, self.loader
        last = E.last_undoable(edit)
        if last is None:
            self.push_screen(NoticePopup("Undo", Text("There is no change from the desk to undo.")))
            return
        body = join([Text("The newest change from the desk:"), Text("  " + str(last.get("what"))), Text(""),
                     Text("Undo runs it backwards through the same safe path (backup, read back).", style=DIM)])
        self.push_screen(NoticePopup("Undo the newest change?", body, ask="yes, undo it"),
                         lambda yes: self.run_jobs([lambda: E.undo(edit, loader=loader)]) if yes else None)

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
