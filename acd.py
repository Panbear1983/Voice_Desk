"""Agent Control Desk, version one: the screen. It shows everything and changes nothing.

    ./acd.sh

HOME, the grid: one row per agent. Columns: the model that is set, the model that really answered the last
reply, the backups, the voice, where the agent shows up, and who it talks to.
ENTER on a row opens that agent's tree: the same facts in full, with the reason behind every colour.

  up / down  choose an agent     Enter  open the agent's tree     Esc  back to the grid
  C  read everything again now   Q  close

It is an overseer, never a lifeline. It only reads files (acd_data.py): nothing is written, no agent is restarted
and no model is called, so closing it or a crash changes nothing for any agent or any voice. It reads gently: ONE
background read every 45 seconds (and on C) for all agents together, never one read per row. Version one has no
editing keys and no way to restart anything; those arrive in later versions.
"""
from __future__ import annotations

import datetime as dt
import textwrap
from typing import Callable, Optional

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Static

import acd_data as D

REFRESH_SEC = 45
READ_ONLY_LABEL = "version one: shows everything, changes nothing"
COLOUR = {D.GREEN: "#3fb950", D.YELLOW: "#d29922", D.BLUE: "#58a6ff", D.RED: "#f85149", D.GREY: "#8b949e"}
DIM = "#8b949e"
MARK = "■"                               # the small square that carries a colour
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
    return join([line(D.GREEN, D.model_name(v.model.model) + lock),
                 Text(f"  {D.service_name(v.model.service, short=True)}", style=DIM),
                 Text(f"  {effort_words(v.model.effort)}", style=DIM)])


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
        t.append(backup_words(b))
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


def tree_text(v: D.AgentView) -> Text:
    """Layout A's right side: the whole tree of one agent, with the reason behind every colour."""
    head = Text()
    head.append(v.name.upper(), style="bold")
    if v.role:
        head.append(f"  ({v.role})", style=DIM)
    out = [head]
    if v.error:
        out += [Text(""), line(D.RED, v.error),
                Text("  Nothing else can be shown for this agent until the settings file reads again.", style=DIM)]
        return join(out)

    out.append(Text("├─ Language model", style="bold"))
    if v.model:
        lock = "  (locked: shown, never changed from the desk)" if v.model_locked else ""
        out += _leaves("│  ├─ ", "Set to", D.GREEN,
                       f"{D.model_name(v.model.model)}, on {D.service_name(v.model.service)}, "
                       f"{effort_words(v.model.effort)}{lock}")
    else:
        out.append(_leaf("│  ├─ ", "Set to", Text("unknown", style=DIM)))
    if v.last:
        out += _leaves("│  ├─ ", "Last reply", v.last.status,
                       f"{v.last.note} ({where(v.last.where)}, {when(v.last.when)})")
    else:
        out.append(_leaf("│  ├─ ", "Last reply", Text("unknown: no reply in the current log", style=DIM)))
    if v.signin_failed and v.model:
        out += _leaves("│  ├─ ", "Sign-in", D.RED,
                       f"{D.service_name(v.model.service)} could not sign in during the last day "
                       f"({D.plain_reason(v.signin_failed)})")
    out.append(_leaf("│  ├─ ", "Drops today", Text(
        f"{v.drops_today} to a backup" if v.drops_today else "none", style="" if v.drops_today else DIM)))
    if v.old_chats:
        out += _leaves("│  ├─ ", "Old chats", D.YELLOW,
                       f"{v.old_chats} open on an older setting. A fresh chat would use the current one")
    if v.override:
        o = v.override
        out.append(_leaf("│  ├─ ", "Temporary", line(
            D.BLUE, f"{D.model_name(o.model)} through {D.service_name(o.service)} "
                    f"({where(o.where)}, {when(o.when)})")))
    else:
        out.append(_leaf("│  ├─ ", "Temporary", Text(
            "none right now (a model typed in a chat or pinned on a job shows here)", style=DIM)))
    out.append(Text("│  └─ Backups, in order", style=""))
    if not v.backups:
        out.append(Text("│        none", style=DIM))
    for i, b in enumerate(v.backups):
        branch = "└─" if i == len(v.backups) - 1 else "├─"
        t = Text()
        t.append(f"│        {branch} {b.position}  ", style=DIM)
        t.append("on   " if b.on else "off  ", style=COLOUR[D.GREEN] if b.on else DIM)
        t.append(backup_words(b, short=False))
        out.append(t)

    out.append(Text("├─ Voice", style="bold"))
    if v.voice:
        speed = f", speed {v.voice.speed:g}" if v.voice.speed is not None else ""
        out.append(_leaf("│  ├─ ", "English", line(D.GREEN, D.voice_label(v.voice.english) + speed)))
        chinese = "none: this agent is English only" if v.voice.english_only else D.voice_label(v.voice.chinese)
        out.append(_leaf("│  ├─ ", "Chinese", Text(chinese, style=DIM if v.voice.english_only else "")))
        out.append(_leaf("│  └─ ", "If the voice fails", Text("stays silent, no stand-in voice"), 20))
    else:
        out.append(Text("│  └─ no voice in the Voice Desk list", style=DIM))

    out.append(Text("├─ Where it shows up", style="bold"))
    for i, p in enumerate(v.presence):
        branch = "└─ " if i == len(v.presence) - 1 else "├─ "
        last = i == len(v.presence) - 1
        out += _leaves("│  " + branch, D.PLACE_NAME[p.key], p.status if p.state == D.ON else None,
                       f"{state_word(p)}: {p.evidence}", 23, dim=p.state != D.ON,
                       more="│     " if last else "│  │  ")

    out.append(Text("└─ Who it may talk to", style="bold"))
    for i, (what, answer) in enumerate(v.talks):
        branch = "└─ " if i == len(v.talks) - 1 else "├─ "
        out.append(_leaf("   " + branch, what, Text(answer), 26))
    return join(out)


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


# ---- the screens -------------------------------------------------------------------------------------------
_TOP_CSS = """
#title { height: 1; background: #161b22; padding: 0 1; }
#warnings { height: auto; padding: 0 1; }
#legend { height: 1; padding: 0 1; background: #161b22; }
#keys { height: 1; padding: 0 1; background: #161b22; color: #8b949e; dock: bottom; }
"""


class GridScreen(Screen):
    """Home: one row per agent."""
    CSS = _TOP_CSS + """
    #grid { height: 1fr; }
    #grid > .datatable--cursor { background: #30363d; color: #e6edf3; }
    #grid > .datatable--header { background: #161b22; color: #8b949e; }
    #others { height: auto; padding: 1 1 0 1; }
    """
    BINDINGS = [Binding("c", "app.reread", "read again", show=False), Binding("q", "app.quit", "close", show=False)]

    def compose(self) -> ComposeResult:
        yield Static("", id="title")
        yield Static("reading every agent's settings and log ...", id="warnings")
        yield Static(legend_text(), id="legend")
        yield DataTable(id="grid", cursor_type="row", zebra_stripes=False)
        yield Static("", id="others")
        yield Static("up/down choose agent   Enter open the agent's tree   C read again   Q close   "
                     "? in a cell = could not be read yet", id="keys")

    def on_mount(self) -> None:
        table = self.query_one("#grid", DataTable)
        for label, _ in COLUMNS:
            table.add_column(label, key=label)
        table.focus()
        self.show(self.app.snapshot)

    def show(self, snap: Optional[D.Snapshot]) -> None:
        self.query_one("#title", Static).update(self.app.title_text())
        if snap is None:
            return
        self.query_one("#warnings", Static).update(warnings_text(snap))
        table = self.query_one("#grid", DataTable)
        keep = table.cursor_row
        table.clear()
        for v in snap.agents:
            cells = [build(v) for _, build in COLUMNS]
            table.add_row(*cells, height=max(c.plain.count("\n") + 1 for c in cells) + 1, key=v.profile)
        if snap.agents:
            table.move_cursor(row=min(keep, len(snap.agents) - 1))
        self.query_one("#others", Static).update(others_text(snap))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.open_tree(event.cursor_row)


class TreeScreen(Screen):
    """One agent's whole tree. Up and down move through the agents; Esc goes back."""
    CSS = _TOP_CSS + """
    #body { height: 1fr; }
    #agents { width: 26; padding: 1 1; border-right: solid #30363d; }
    #scroll { width: 1fr; padding: 1 2; }
    #tree { height: auto; }
    """
    BINDINGS = [Binding("escape", "back", "back", show=False), Binding("up", "move(-1)", "up", show=False),
                Binding("down", "move(1)", "down", show=False), Binding("c", "app.reread", "read again", show=False),
                Binding("q", "app.quit", "close", show=False)]

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index

    def compose(self) -> ComposeResult:
        yield Static("", id="title")
        yield Static("", id="warnings")
        yield Static(legend_text(), id="legend")
        with Horizontal(id="body"):
            yield Static("", id="agents")
            with VerticalScroll(id="scroll"):
                yield Static("", id="tree")
        yield Static("up/down choose agent   Esc back to the grid   C read again   Q close   "
                     "(editing keys arrive in later versions)", id="keys")

    def on_mount(self) -> None:
        self.show(self.app.snapshot)

    def show(self, snap: Optional[D.Snapshot]) -> None:
        self.query_one("#title", Static).update(self.app.title_text())
        if snap is None or not snap.agents:
            return
        self.index = max(0, min(self.index, len(snap.agents) - 1))
        self.query_one("#warnings", Static).update(warnings_text(snap))
        self.query_one("#agents", Static).update(agent_list_text(snap, self.index))
        self.query_one("#tree", Static).update(tree_text(snap.agents[self.index]))

    def action_move(self, step: int) -> None:
        snap = self.app.snapshot
        if snap and snap.agents:
            self.index = (self.index + step) % len(snap.agents)
            self.show(snap)
            self.query_one("#scroll", VerticalScroll).scroll_home(animate=False)

    def action_back(self) -> None:
        self.app.pop_screen()


class Desk(App):
    """The Agent Control Desk. `collector` returns a Snapshot; tests pass their own."""
    TITLE = "Agent Control Desk"
    CSS = "Screen { background: #0d1117; color: #e6edf3; }"
    BINDINGS = [Binding("q", "quit", "close"), Binding("c", "reread", "read again")]

    def __init__(self, collector: Optional[Callable[[], D.Snapshot]] = None, autostart: bool = True) -> None:
        super().__init__()
        self.snapshot: Optional[D.Snapshot] = None
        self.autostart = autostart
        if collector is None:
            paths, reader = D.Paths.default(), None
            reader = D.LogReader(paths.profiles)                 # ONE reader, kept for the life of the desk
            collector = lambda: D.collect(paths, reader)         # noqa: E731
        self.collector = collector

    def on_mount(self) -> None:
        self.push_screen(GridScreen())
        if self.autostart:
            self.action_reread()
            self.set_interval(REFRESH_SEC, self.action_reread)

    def title_text(self) -> Text:
        t = Text("AGENT CONTROL DESK", style="bold")
        t.append("     " + READ_ONLY_LABEL, style=COLOUR[D.YELLOW])
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


if __name__ == "__main__":
    Desk().run()
