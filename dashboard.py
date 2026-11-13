"""Voice Desk dashboard: the voice grid (main screen) and the listener controls (second screen).

    ./dashboard.sh

MAIN SCREEN, the grid: one row per agent, one column per place an agent can speak. Each cell shows the
setting (Off / Voice-back / Always) read from the file where it really lives, and under it what the
logs say really happened (green spoke OK · grey idle, nothing to say · yellow mixed · red mismatch or
failure). The top strip: voice engine · Hermes gateway · speech queue · Hermes fixes · Mac output.
It is an overseer, never a lifeline: every voice setting stays where the agents already read it, so
if this panel closes, voices keep working exactly as last set.

ON/OFF CELLS (steps 2-3): ← → pick a place, Enter changes it. Live today: the Telegram team group (Off / Always), Claude's terminal
(Off / Always), the Voice Desk mic window / phone (speech queue on / off), the Telegram direct chats
(Off / Voice-back / Always, gateway_voice_mode.json) and Gemini's terminal (Off / Always, a switch file its hook reads).
W pauses / resumes the watcher (watcher.py puts drifted settings back; the top strip shows its light).
Each change backs up the files, writes them, reads them back and goes into the history; U undoes. Hermes windows and the
iPhone stay read-only. Enter on the voice column edits the voice; Enter on the Voice check column runs the silent test.

  ↑ ↓ pick an agent   ← → pick a place   T silent voice test (no sound)   Shift-T test everyone   C check now
  E English voice   Z Mandarin voice (pick-lists: arrows + Enter, last line "other, type it")   F voice effect
  (Simba only)   U undo last change
  A add an agent (a Hermes profile on this Mac, or another CLI agent)   D remove the agent from the grid (its
  agents.toml block only: the profile and every file stay; U puts it back)
  X here / not here at the selected place (separate from the voice switch; not here = a blank cell)
  P activation phrase of the selected place (stored and shown)   ? what the colours mean
  M mute / unmute · H hush · S listener screen · Q close (the listener keeps running)
The footer shows only the keys that apply to the selected row and cell. L (extra languages) and O (language
override) still work but are hidden: they are stored only, nothing enforces them yet.

SECOND SCREEN, the listener (it never opens the microphone): who is on the line, what was heard and
said, and M mute · H hush · N new conversation · X hand the mic back ·
R restart · K kill the listener · Esc back to the grid · Q close. (V, the shared-voice switch, is gone: every
agent speaks with its own voice or stays silent.)

The grid reads gently: ONE background read every 45 s (and on C) for all rows together, never a
loop per row; the second screen's per-window herdr look-up runs only while that screen is open.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

import agents
import gridedit
import gridevidence as E
import gridstate as G
import gridview as V
import syshealth
import onoff
import voiceedit
import voicepick
import voicetest
import service
import voicecfg
import live_session
import brain as brain_mod
import voices
import watcher

REFRESH_SEC = 45            # the grid and the top strip are re-read this often, all rows in one pass
LISTENER_SEC = 15           # the second screen's window look-ups, only while it is open

LIGHT = {
    "starting": ("●  starting", "#555555"),
    "asleep": ("●  asleep · say a name", "#7f8c8d"),
    "listening": ("●  listening", "#2ecc71"),
    "hearing": ("●  hearing you", "#27ae60"),
    "thinking": ("●  thinking", "#f39c12"),
    "speaking": ("●  speaking", "#3498db"),
    "handed": ("●  talking in the agent's window · end phrase hands back", "#16a085"),
    "muted": ("●  muted · say unmute", "#8e44ad"),
    "standby": ("●  standby · the window has the mic", "#555555"),
    "error": ("●  error", "#e74c3c"),
    "off": ("●  listener off · R starts it", "#333333"),
}
_LIGHT_CSS = "".join(f"#light.{state} {{ background: {colour}; }}\n" for state, (_, colour) in LIGHT.items())


def _ago(t: float) -> str:
    s = max(0, int(time.time() - t))
    return f"{s}s" if s < 90 else f"{s // 60}m" if s < 5400 else f"{s // 3600}h"


class AskScreen(ModalScreen[Optional[str]]):
    """A one-line question; Enter answers, Esc cancels."""
    CSS = """
    AskScreen { align: center middle; }
    #ask { width: 80; height: auto; border: round #9b59b6; background: #1b1b1b; padding: 1 2; }
    #ask-title { text-style: bold; }
    #ask-hint { color: #888888; }
    """
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, title: str, hint: str, value: str = "") -> None:
        super().__init__()
        self._title, self._hint, self._value = title, hint, value

    def compose(self) -> ComposeResult:
        with Vertical(id="ask"):
            yield Static(self._title, id="ask-title")
            yield Static(self._hint, id="ask-hint")
            yield Input(value=self._value, id="ask-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoiceScreen(ModalScreen[Optional[str]]):
    """A short list of choices for one cell; Enter picks, Esc cancels. `options` are (token, label, is_current)."""
    CSS = """
    ChoiceScreen { align: center middle; }
    #choice { width: 124; max-width: 96%; height: auto; border: round #9b59b6; background: #1b1b1b; padding: 1 2; }
    #choice-title { text-style: bold; }
    #choice-hint { color: #888888; }
    #choice-list { height: auto; max-height: 24; }
    """
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, title: str, options: list[onoff.Option], hint: Optional[str] = None) -> None:
        super().__init__()
        self._title, self._options = title, options
        self._hint = hint or "Up / Down, then Enter (● = now). Esc cancels. The files are backed up and read back; U undoes."

    def compose(self) -> ComposeResult:
        with Vertical(id="choice"):
            yield Static(self._title, id="choice-title")
            yield Static(self._hint, id="choice-hint")
            yield OptionList(*[Option(("● " if o.current else "◌ " if o.disabled else "  ") + o.label, id=o.token,
                                      disabled=o.disabled) for o in self._options], id="choice-list")

    def on_mount(self) -> None:
        lst = self.query_one(OptionList)
        lst.focus()
        current = next((i for i, o in enumerate(self._options) if o.current), 0)
        lst.highlighted = current

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """"?" on the grid: what the colours and the keys mean (Part 5). Esc, ? or Enter closes it."""
    CSS = """
    HelpScreen { align: center middle; }
    #help { width: 100; height: auto; border: round #9b59b6; background: #1b1b1b; padding: 1 2; }
    """
    BINDINGS = [Binding("escape", "close", "close"), Binding("question_mark", "close", "close", show=False),
                Binding("enter", "close", "close", show=False)]

    def compose(self) -> ComposeResult:
        yield Static(help_text(), id="help")

    def action_close(self) -> None:
        self.dismiss(None)


def help_text() -> Text:
    t = Text()
    t.append("What the grid's colours mean\n", style="bold #9b59b6")
    t.append("● ", style=V.COLOUR["ok"])
    t.append("green   spoke recently and it worked (only real evidence turns a cell green)\n")
    t.append("● ", style=V.COLOUR["idle"])
    t.append("grey    idle: nothing to say lately, or switched Off (not a failure)\n")
    t.append("● ", style=V.COLOUR["warn"])
    t.append("yellow  worked last time, but some recent replies were text only\n")
    t.append("● ", style=V.COLOUR["bad"])
    t.append("red     a mismatch or a failure; a failed voice is red and its line was silent (no backup voice)\n")
    t.append("◌ ", style=V.COLOUR["unfinished"])
    t.append("orange  here, but not wired yet (switched here, nothing speaks there yet; never green)\n")
    t.append("        blank cell: the agent is not here at that place (X puts it here)\n\n")
    t.append("Keys on the grid\n", style="bold #9b59b6")
    t.append("T voice test of the row (silent) · Shift-T test all · C check now\n"
             "E English voice · Z Mandarin voice (pick-lists; last line \"other, type it\") · F voice effect (Simba)\n"
             "A add an agent · D remove it from the grid (its settings block only; the profile stays)\n"
             "X here / not here at the selected place (not the voice switch: Enter changes that)\n"
             "P activation phrase of the selected place (stored and shown; it renames nothing yet)\n"
             "U undo the last change · W pause / resume the watcher · S listener screen\n"
             "M mute / unmute · H hush · Q close (the listener keeps running)\n", style="default")
    t.append("The footer shows only the keys that apply to the selected row and cell. Esc closes this.", style="#7f8c8d")
    return t


class GridScreen(Screen):
    CSS = """
    GridScreen { layout: vertical; }
    #top { height: 1; }
    #light { width: 1fr; height: 1; color: white; text-style: bold; padding: 0 1; }
    #who { width: 40; height: 1; color: #dddddd; }
    #health { height: auto; max-height: 3; padding: 0 1; }
    #grid { height: 1fr; min-height: 8; }
    #detail { height: auto; max-height: 14; border: round #444444; padding: 0 1; }
    #history { height: auto; max-height: 7; padding: 0 1; }
    #msg { height: 1; color: #bbbbbb; padding: 0 1; }
    """ + _LIGHT_CSS
    BINDINGS = [
        Binding("t", "test_row", "test"),
        Binding("T", "test_all", "test all"),
        Binding("c", "check_now", "check"),
        Binding("e", "edit_en", "English"),
        Binding("z", "edit_zh", "Mandarin"),
        Binding("f", "edit_effect", "effect"),
        # Part 5 (Peter 2026-11-11): L and O are stored only, nothing enforces them: hidden, the keys still work
        Binding("l", "edit_extra", "extra language", show=False),
        Binding("o", "edit_override", "language override", show=False),
        Binding("a", "add_agent", "add"),
        Binding("d", "remove_agent", "remove"),
        Binding("x", "toggle_here", "here / not"),
        Binding("p", "edit_phrase", "phrase"),
        Binding("u", "undo", "undo"),
        Binding("w", "watcher", "watcher"),
        Binding("s", "listener", "listener"),
        Binding("question_mark", "help", "colours", key_display="?"),
    ]
    # The footer shows only the keys that apply to the selected row or cell (Part 5)
    ROW_ACTIONS = {"test_row", "edit_en", "edit_zh", "edit_effect", "edit_extra", "edit_override", "remove_agent"}
    CELL_ACTIONS = {"toggle_here", "edit_phrase"}

    def check_action(self, action: str, parameters: tuple[object, ...]) -> Optional[bool]:
        if action not in self.ROW_ACTIONS | self.CELL_ACTIONS:
            return True
        try:
            app = self.app
            row = app.selected()                        # type: ignore[attr-defined]
            if row is None:
                return False
            a = row.agent
            if action in ("edit_en", "edit_zh", "edit_extra", "edit_override") and not voiceedit.editable(a):
                return False
            if action == "edit_zh":
                return True                             # decision 14: everyone sees the list (English-only: ◌ lines)
            if action == "edit_effect":
                return voiceedit.uses_simba_script(a)   # F only on Simba's row
            if action in self.CELL_ACTIONS:
                place = app.selected_place()            # type: ignore[attr-defined]
                if place is None:
                    return False
                if action == "edit_phrase":
                    return row.is_here(place)           # a phrase belongs to a "here" cell
            return True
        except Exception:                               # never let the footer take the panel down
            return True

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static("●  starting", id="light", classes="starting")
            yield Static("", id="who")
        yield Static("reading…", id="health")
        yield DataTable(id="grid", cursor_type="cell", zebra_stripes=False)
        yield Static("", id="detail")
        yield Static("", id="history")
        yield Static("", id="msg")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label in V.header_labels():
            table.add_column(label)
        table.focus()
        self.app.start_grid()          # type: ignore[attr-defined]

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        self.app.show_detail()         # type: ignore[attr-defined]
        self.refresh_bindings()        # the footer follows the selected row and cell

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        self.app.open_cell(event.coordinate.column)    # type: ignore[attr-defined]

    def action_test_row(self) -> None:
        self.app.test_selected()       # type: ignore[attr-defined]

    def action_test_all(self) -> None:
        self.app.test_everyone()       # type: ignore[attr-defined]

    def action_check_now(self) -> None:
        self.app.collect_in_background(force=True)   # type: ignore[attr-defined]

    def action_edit_en(self) -> None:
        self.app.ask_edit("voice")     # type: ignore[attr-defined]

    def action_edit_zh(self) -> None:
        self.app.ask_edit("voice_zh")  # type: ignore[attr-defined]

    def action_edit_extra(self) -> None:
        self.app.ask_edit("voices_extra")   # type: ignore[attr-defined]

    def action_edit_override(self) -> None:
        self.app.ask_edit("voice_override")  # type: ignore[attr-defined]

    def action_edit_effect(self) -> None:
        self.app.ask_edit("effect")    # type: ignore[attr-defined]

    def action_add_agent(self) -> None:
        self.app.ask_add()             # type: ignore[attr-defined]

    def action_remove_agent(self) -> None:
        self.app.ask_remove()          # type: ignore[attr-defined]

    def action_toggle_here(self) -> None:
        self.app.toggle_here()         # type: ignore[attr-defined]

    def action_edit_phrase(self) -> None:
        self.app.ask_phrase()          # type: ignore[attr-defined]

    def action_undo(self) -> None:
        self.app.undo_last()           # type: ignore[attr-defined]

    def action_watcher(self) -> None:
        self.app.toggle_watcher()      # type: ignore[attr-defined]

    def action_listener(self) -> None:
        self.app.push_screen("listener")


class ListenerScreen(Screen):
    CSS = """
    ListenerScreen { layout: vertical; }
    #top { height: 3; }
    #light { width: 1fr; height: 100%; content-align: center middle; text-style: bold; color: white; }
    #who { width: 30; height: 100%; content-align: center middle; color: #dddddd; }
    #body { height: 1fr; border: round #444444; padding: 0 1; }
    #voices { height: auto; border: round #444444; padding: 0 1; }
    #msg { height: 1; color: #888888; padding: 0 1; }
    """ + _LIGHT_CSS
    BINDINGS = [
        Binding("n", "new_thread", "new conversation"),
        Binding("x", "release", "hand the mic back"),
        Binding("r", "restart", "restart listener"),
        Binding("k", "kill", "kill listener"),
        Binding("escape", "back", "back to the grid"),
        Binding("s", "back", "back to the grid", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static("●  starting", id="light", classes="starting")
            yield Static("", id="who")
        yield Static("", id="body")
        yield Static("", id="voices")
        yield Static("", id="msg")
        yield Footer()

    def on_mount(self) -> None:
        self.app.refresh_status()      # type: ignore[attr-defined]
        self.app.refresh_voices()      # type: ignore[attr-defined]
        self.timer = self.set_interval(LISTENER_SEC, self.app.refresh_voices)   # type: ignore[attr-defined]

    def on_screen_suspend(self) -> None:
        getattr(self, "timer", None) and self.timer.pause()

    def on_screen_resume(self) -> None:
        if getattr(self, "timer", None):
            self.timer.resume()
            self.app.refresh_voices()  # type: ignore[attr-defined]

    def action_back(self) -> None:
        self.app.pop_screen()
        self.app.call_after_refresh(self.app.restore_grid)    # type: ignore[attr-defined]

    def action_new_thread(self) -> None:
        service.send("new_thread")
        self.app.say("the next turn starts a fresh conversation with whoever is on the line")  # type: ignore[attr-defined]

    def action_release(self) -> None:
        service.send("release")
        self.app.say("the listener takes the mic back and waits for a name")   # type: ignore[attr-defined]

    def action_restart(self) -> None:
        self.app.say("listener: " + service.service_restart())   # type: ignore[attr-defined]

    def action_kill(self) -> None:
        self.app.say("listener: " + service.service_stop() + " (R starts it again)")   # type: ignore[attr-defined]


class Dashboard(App):
    TITLE = "Voice Desk"
    SCREENS = {"listener": ListenerScreen}
    BINDINGS = [
        Binding("m", "mute", "mute / unmute"),
        Binding("h", "hush", "hush"),
        Binding("q", "quit", "close panel"),
    ]

    def __init__(self, paths: Optional[G.Paths] = None, edit_paths: Optional[voiceedit.EditPaths] = None,
                 reader: Optional[E.LogReader] = None, autostart: bool = True,
                 cfg_loader: Optional[Callable[[], dict]] = None) -> None:
        super().__init__()
        self._msg = ""
        self._msg_until = 0.0
        self.paths = paths or G.Paths.default()
        self.edit_paths = edit_paths or voiceedit.EditPaths.default()
        self.reader = reader or E.LogReader(self.paths.profiles)
        self.cfg_loader = cfg_loader or voices.config        # agents.toml as it is now (tests hand in a copy)
        self.autostart = autostart            # tests switch the background reading off and call collect() themselves
        self.rows: list[E.Row] = []
        self.lights: list[syshealth.Light] = []
        self.tests: dict[str, list[voicetest.TestResult]] = voicetest.load()
        self.testing: set[str] = set()
        self._last_collect = 0.0
        self._collecting = False

    def on_mount(self) -> None:
        self.push_screen(GridScreen())
        self.refresh_status()
        self.set_interval(0.5, self.refresh_status)

    # -- the grid: one shared reader, one pass for all rows -----------------------------------------
    def start_grid(self) -> None:
        if self.autostart:
            self.collect_in_background(force=True)
            self.set_interval(REFRESH_SEC, self.collect_in_background)

    def collect(self) -> tuple[list[E.Row], list[syshealth.Light]]:
        """Read every setting and every log once. Safe off the main thread; touches no widget."""
        return E.build(self.cfg_loader(), self.paths, self.reader), syshealth.collect(self.paths)

    def collect_in_background(self, force: bool = False) -> None:
        if self._collecting or (not force and time.monotonic() - self._last_collect < REFRESH_SEC - 1):
            return
        self._collecting = True
        self._collect_worker()

    @work(thread=True, exclusive=True, group="collect")
    def _collect_worker(self) -> None:
        snap = None
        try:
            snap = self.collect()
        except Exception as exc:                          # never let a bad file take the panel down
            self.call_from_thread(self.say, f"check failed: {exc}")
        finally:
            self._collecting = False
        if snap:
            self.call_from_thread(self.apply_snapshot, *snap)

    def apply_snapshot(self, rows: list[E.Row], lights: list[syshealth.Light]) -> None:
        self.rows, self.lights = rows, lights
        self._last_collect = time.monotonic()
        screen = self.screen
        if not isinstance(screen, GridScreen):            # the listener screen is open: shown when we return
            return
        screen.query_one("#health", Static).update(V.health_text(lights))
        self.fill_table()

    def fill_table(self) -> None:
        screen = self.screen
        if not isinstance(screen, GridScreen):
            return
        table = screen.query_one(DataTable)
        keep = table.cursor_row if table.row_count else 0
        keep_col = table.cursor_column if table.row_count else 0
        table.clear()
        for row in self.rows:
            key = row.agent.key
            table.add_row(Text(row.agent.display, style="bold"), V.voice_text(row),
                          *[V.cell_text(row.cells[p], row.evidence[p], row.is_here(p), p) for p in G.PLACE_KEYS],
                          V.test_text(self.tests.get(key), key in self.testing, row.voice.silent), key=key, height=2)
        if self.rows:
            table.move_cursor(row=min(keep, len(self.rows) - 1), column=keep_col)
        self.show_detail()
        screen.refresh_bindings()

    def restore_grid(self) -> None:
        """Back from the listener screen: show what was collected while it was open."""
        if isinstance(self.screen, GridScreen) and self.lights:
            self.screen.query_one("#health", Static).update(V.health_text(self.lights))
            self.fill_table()
        self.refresh_status()

    def selected(self) -> Optional[E.Row]:
        screen = self.screen
        if not isinstance(screen, GridScreen) or not self.rows:
            return None
        table = screen.query_one(DataTable)
        return self.rows[table.cursor_row] if 0 <= table.cursor_row < len(self.rows) else None

    def selected_place(self) -> Optional[str]:
        """The place (column) under the cursor, or None on the Agent / Voice / Voice check columns."""
        screen = self.screen
        if not isinstance(screen, GridScreen):
            return None
        col = screen.query_one(DataTable).cursor_column - 2
        return G.PLACE_KEYS[col] if 0 <= col < len(G.PLACE_KEYS) else None

    def show_detail(self) -> None:
        screen = self.screen
        if not isinstance(screen, GridScreen):
            return
        row = self.selected()
        screen.query_one("#detail", Static).update(
            V.detail_text(row, self.tests.get(row.agent.key), place=self.selected_place(), paths=self.paths)
            if row else "reading the settings and the logs…")
        screen.query_one("#history", Static).update(V.history_text(self.edit_paths, watcher_lines=watcher.history_lines(self.paths, 3)))

    def toggle_watcher(self) -> None:
        """W: pause / resume the watcher (the file ~/.voice_desk/watcher_paused). The strip shows it at once."""
        paused = watcher.toggle_pause(self.paths)
        fresh = syshealth.watcher(self.paths)
        self.lights = [fresh if l.label.lower().startswith("watcher") else l for l in self.lights]
        screen = self.screen
        if isinstance(screen, GridScreen) and self.lights:
            screen.query_one("#health", Static).update(V.health_text(self.lights))
        self.say("watcher PAUSED: it repairs nothing until you press W again" if paused
                 else "watcher resumed: it checks every few minutes and puts drifted settings back")

    # -- the silent voice test ---------------------------------------------------------------------
    def test_selected(self) -> None:
        row = self.selected()
        if row:
            self._run_tests([row.agent])

    def test_everyone(self) -> None:
        self._run_tests([r.agent for r in self.rows])

    def _run_tests(self, which: list[agents.Agent]) -> None:
        todo = [a for a in which if a.key not in self.testing]
        if not todo:
            return
        self.testing.update(a.key for a in todo)
        self.say(f"silent voice test: rendering a sentence for {', '.join(a.display for a in todo)} (no sound)…")
        self.fill_table()
        self._test_worker(todo)

    @work(thread=True, group="voicetest")
    def _test_worker(self, which: list[agents.Agent]) -> None:
        cfg = self.cfg_loader()
        for a in which:
            results = voicetest.run(a, cfg)
            self.call_from_thread(self.test_done, a, results)

    def test_done(self, agent: agents.Agent, results: list[voicetest.TestResult]) -> None:
        self.testing.discard(agent.key)
        self.tests[agent.key] = results
        voicetest.save(self.tests)
        bad = [r for r in results if not r.ok]
        self.say(f"{agent.display}: voice check " + ("passed — " + ", ".join(r.short() for r in results) if not bad
                                                     else "FAILED — " + "; ".join(f"{r.lang}: {r.detail}" for r in bad)))
        self.fill_table()

    # -- switching a cell on / off (Enter) ---------------------------------------------------------
    def open_cell(self, column: int) -> None:
        """Enter on a cell: the voice column edits the voice, the Voice check column tests, a place column switches."""
        row = self.selected()
        if not row:
            return
        if column == 1:
            self.ask_edit("voice")
            return
        if column == 7:
            self.test_selected()
            return
        place = self.selected_place()
        if place is None:
            return
        cell = row.cells[place]
        menu = onoff.menu(row.agent, place, cell, self.paths)
        if not menu.options:
            self.say(f"{row.agent.display} — {G.PLACE_NAME[place]}: {menu.why_not}")
            return

        def picked(token: Optional[str]) -> None:
            if token is not None:
                self.apply_switch(row.agent.key, place, token)

        self.push_screen(ChoiceScreen(menu.title, menu.options), picked)

    def apply_switch(self, agent_key: str, place: str, token: str) -> None:
        self.say("switching, with a backup and a read-back…")
        self._switch_worker(agent_key, place, token)

    @work(thread=True, exclusive=True, group="edit")
    def _switch_worker(self, agent_key: str, place: str, token: str) -> None:
        res = onoff.set_cell(agent_key, place, token, self.paths, self.edit_paths)
        self.call_from_thread(self.edit_done, res)

    # -- changing a voice -------------------------------------------------------------------------
    PROMPTS = {
        "voice": ("English voice", "An Edge voice such as en-US-AvaNeural, or a Kokoro voice (local, no internet) such as af_heart, "
                  "am_adam, bf_emma (af_/am_ American, bf_/bm_ British) (Esther: a Kokoro name such as alloy; "
                  "Simba: an American Kokoro voice such as am_fenrir or af_nicole). Enter saves, Esc cancels."),
        "voice_zh": ("Mandarin voice", "A Taiwan Mandarin Edge voice such as zh-TW-HsiaoChenNeural (zh-TW only). Enter saves, Esc cancels."),
        "voices_extra": ("Extra languages", "code=voice, comma separated, e.g. ja=ja-JP-NanamiNeural. Empty removes them all."),
        "voice_override": ("Language override", "auto (follow the reply's language), en, zh, or an extra code. Stored now, enforced later."),
        "effect": ("Voice effect", "A sample number 1-30 (or fenrir16, smoky, raspy, plain), then changes if you like: "
                   "higher / lower, slower / faster, crunchier, breathier, rougher, echo, 21-30, or knob=value "
                   "(speed pitch tempo bits mix noise tremolo echo). E.g. 23 but slower and breathier. "
                   "Enter saves, Esc cancels."),
    }

    def current_value(self, row: E.Row, field_name: str) -> str:
        v = row.voice
        return {"voice": v.en, "voice_zh": v.zh, "voices_extra": ", ".join(f"{k}={x}" for k, x in v.extra.items()),
                "voice_override": v.override, "effect": v.effect or ""}[field_name]

    def ask_edit(self, field_name: str) -> None:
        row = self.selected()
        if not row:
            return
        if not voiceedit.editable(row.agent):
            self.say(f"{row.agent.display}: no voice setting the panel can change yet")
            return
        if field_name == "effect" and not voiceedit.uses_simba_script(row.agent):
            self.say(f"{row.agent.display} has no voice effect (only Simba's Kokoro voice has one)")
            return
        if field_name in ("voice", "voice_zh", "effect"):
            # Part 2 (Peter 2026-11-11): a pick-list, current one marked; its last line opens the typing box
            title, options, why_not = voicepick.menu(row.agent, field_name, self.cfg_loader())
            if why_not:
                self.say(why_not)
                return

            def picked(token: Optional[str]) -> None:
                if token and token.startswith(voicepick.NOT_YET):
                    self.say(f"{row.agent.display}: {token[len(voicepick.NOT_YET):]} is not available yet, nothing changed")
                elif token == voicepick.OTHER:
                    self.ask_typed(row, field_name)
                elif token == voicepick.SILENT:
                    self.apply_edit(row.agent.key, field_name, None)
                elif token is not None:
                    self.apply_edit(row.agent.key, field_name, token)

            self.push_screen(ChoiceScreen(title, options,
                                          voicepick.HINT if any(o.disabled for o in options) else None), picked)
            return
        self.ask_typed(row, field_name)

    def ask_typed(self, row: E.Row, field_name: str) -> None:
        """The typing box: L and O, and the "other, type it" line of the pick-list."""
        title, hint = self.PROMPTS[field_name]

        def answered(text: Optional[str]) -> None:
            if text is not None:
                self.apply_edit(row.agent.key, field_name, text)

        self.push_screen(AskScreen(f"{row.agent.display} — {title}", hint, self.current_value(row, field_name)), answered)

    def apply_edit(self, agent_key: str, field_name: str, text: Optional[str]) -> None:
        try:
            value = voiceedit.parse_extra(text or "") if field_name == "voices_extra" else (text or None)
        except voiceedit.EditError as exc:
            self.say(str(exc))
            return
        self.say("saving, with a backup and a read-back…")
        self._edit_worker(agent_key, field_name, value)

    @work(thread=True, exclusive=True, group="edit")
    def _edit_worker(self, agent_key: str, field_name: str, value) -> None:
        res = voiceedit.change(agent_key, field_name, value, self.edit_paths)
        self.call_from_thread(self.edit_done, res)

    # -- adding / removing an agent (A / D, Part 3) --------------------------------------------------
    CLI = "::cli"

    def ask_add(self) -> None:
        """A: which agent: a Hermes profile on this Mac not on the grid yet, or another CLI agent. Then its display
        name, its spoken name(s), and a voice from the pick-list (or silent). Telegram chat ids are not asked for."""
        cfg = self.cfg_loader()
        profiles = gridedit.addable_profiles(cfg, self.edit_paths.profiles)
        options = [onoff.Option(p, f"{brain_mod.hermes_display_name(p)} — Hermes profile “{p}” (it knows its own "
                                   f"Telegram bot)") for p in profiles]
        options.append(onoff.Option(self.CLI, "other CLI agent (a program like Claude, Gemini or Codex) …"))
        hint = ("Up / Down, then Enter. Esc cancels. Only agents.toml gets a new block (backed up, read back; U takes "
                "it off again). No profile or other file is changed.")

        def picked(token: Optional[str]) -> None:
            if token is not None:
                self._ask_add_name(None if token == self.CLI else token)

        self.push_screen(ChoiceScreen("Add an agent to the grid", options, hint), picked)

    def _ask_add_name(self, profile: Optional[str]) -> None:
        start = brain_mod.hermes_display_name(profile) if profile else ""

        def named(display: Optional[str]) -> None:
            if display is None:
                return
            if not display:
                self.say("✗ the new agent needs a display name; nothing written")
                return
            self._ask_add_spoken(profile, display)

        self.push_screen(AskScreen("New agent — display name", "The name shown on the grid and spoken back, e.g. "
                                   "Builder. Enter goes on, Esc cancels.", start), named)

    def _ask_add_spoken(self, profile: Optional[str], display: str) -> None:
        def spoken(text: Optional[str]) -> None:
            if text is not None:
                self._ask_add_voice(profile, display, text)

        self.push_screen(AskScreen(f"{display} — spoken name(s)", "Every way the listener may hear the name, comma "
                                   "separated (the display name is always one). Enter goes on, Esc cancels.",
                                   display.lower()), spoken)

    def _ask_add_voice(self, profile: Optional[str], display: str, names: str) -> None:
        key = gridedit.key_for(profile or display)
        a = gridedit.draft(key, display, profile=profile)
        options = voicepick.english(a, self.cfg_loader())

        def picked(token: Optional[str]) -> None:
            if token is None or token.startswith(voicepick.NOT_YET):
                return
            if token == voicepick.OTHER:
                def typed(text: Optional[str]) -> None:
                    if text is not None:
                        self._add_worker(profile, display, names, text or None)
                self.push_screen(AskScreen(f"{display} — English voice", self.PROMPTS["voice"][1]), typed)
                return
            self._add_worker(profile, display, names, None if token == voicepick.SILENT else token)

        self.push_screen(ChoiceScreen(f"{display} — English voice (one voice, no backup; Z adds Mandarin later)",
                                      options, voicepick.HINT if any(o.disabled for o in options) else None), picked)

    @work(thread=True, exclusive=True, group="edit")
    def _add_worker(self, profile: Optional[str], display: str, names: str, voice: Optional[str]) -> None:
        res = gridedit.add_agent(self.edit_paths, display, names, voice, profile=profile)
        self.call_from_thread(self.edit_done, res)

    def ask_remove(self) -> None:
        """D: take the selected agent off the grid (its agents.toml block only), after a yes."""
        row = self.selected()
        if not row:
            return
        a = row.agent
        keeps = f"its Hermes profile “{a.profile}” and every file stay" if a.profile else "every file stays"
        options = [onoff.Option("keep", f"keep {a.display} on the grid", True),
                   onoff.Option("remove", f"remove {a.display} from the grid — {keeps}; U puts it back")]

        def picked(token: Optional[str]) -> None:
            if token == "remove":
                self._remove_worker(a.key)

        self.push_screen(ChoiceScreen(f"Remove {a.display} from the grid?", options,
                                      "Only its block in agents.toml goes (a copy is kept). Esc cancels."), picked)

    @work(thread=True, exclusive=True, group="edit")
    def _remove_worker(self, agent_key: str) -> None:
        res = gridedit.remove_agent(self.edit_paths, agent_key)
        self.call_from_thread(self.edit_done, res)

    # -- the wiring matrix: here / not here (X) and the activation phrase (P), Part 4 -------------------
    PHRASE_HINT = {
        "terminal": "What starts it in its window, e.g. the command name. ",
        "tg_dm": "The bot Peter writes to, e.g. @JarvisBot. ",
        "tg_group": "The group, e.g. Team_Room. ",
        "mic": "The words that wake it at the Mac's microphone, e.g. hello Jarvis. ",
        "iphone": "The Siri shortcut's name, e.g. Talk to Jarvis. ",
    }

    def _cell_or_say(self, what: str) -> Optional[tuple[E.Row, str]]:
        row, place = self.selected(), self.selected_place()
        if not row:
            return None
        if place is None:
            self.say(f"{what} works on a place: ← → to a place column first")
            return None
        return row, place

    def toggle_here(self) -> None:
        """X: the agent is here at this place, or not (a blank cell). The voice on/off switch is separate."""
        got = self._cell_or_say("X (here / not here)")
        if got:
            row, place = got
            self.say("saving, with a backup and a read-back…")
            self._here_worker(row.agent.key, place, not row.is_here(place), row.cells)

    @work(thread=True, exclusive=True, group="edit")
    def _here_worker(self, agent_key: str, place: str, here: bool, cells: dict) -> None:
        res = gridedit.set_here(self.edit_paths, agent_key, place, here, cells)
        self.call_from_thread(self.edit_done, res)

    def ask_phrase(self) -> None:
        """P: the activation phrase of the selected cell. Stored and shown; it renames nothing."""
        got = self._cell_or_say("P (activation phrase)")
        if not got:
            return
        row, place = got
        name = G.PLACE_NAME[place]
        if not row.is_here(place):
            self.say(f"{row.agent.display} is not here at {name}: X puts it here first")
            return
        phrase, stored = row.phrase(place)
        default = G.default_phrase(row.agent, place)
        hint = (self.PHRASE_HINT[place] + (f"Empty = today's: {default}. " if default else "Empty = none. ")
                + "Stored and shown only: it does not rename a Siri shortcut or change the wake words yet. "
                  "Enter saves, Esc cancels.")

        def answered(text: Optional[str]) -> None:
            if text is not None:
                self.say("saving, with a backup and a read-back…")
                self._phrase_worker(row.agent.key, place, text)

        self.push_screen(AskScreen(f"{row.agent.display} at {name} — activation phrase", hint,
                                   phrase if stored else ""), answered)

    @work(thread=True, exclusive=True, group="edit")
    def _phrase_worker(self, agent_key: str, place: str, text: str) -> None:
        res = gridedit.set_phrase(self.edit_paths, agent_key, place, text)
        self.call_from_thread(self.edit_done, res)

    def undo_last(self) -> None:
        self.say("undoing the last change…")
        self._undo_worker()

    @work(thread=True, exclusive=True, group="edit")
    def _undo_worker(self) -> None:
        res = voiceedit.undo(self.edit_paths, self.paths)
        self.call_from_thread(self.edit_done, res)

    def edit_done(self, res: voiceedit.Result) -> None:
        self.say(("✓ " if res.ok else "✗ ") + res.message)
        self._last_collect = 0.0
        if self.autostart:
            self.collect_in_background(force=True)
        else:
            self.show_detail()

    # -- the listener (light on both screens, details on the second) -------------------------------
    def state_of(self, data: dict) -> str:
        if not service.is_fresh(data):
            return "off"
        if data.get("state") in ("asleep", "listening") and (data.get("muted") or service.is_muted()):
            return "muted"
        st = str(data.get("state") or "")
        return st if st in LIGHT else "starting"

    def refresh_voices(self) -> None:
        """The second screen's voices panel (per-window herdr look-ups: runs only while that screen is open)."""
        screen = self.screen
        if not isinstance(screen, ListenerScreen):
            return
        cfg = voices.config()
        rows = [f"{a.display}: {voices.short(voices.voice_for(a.target))}" for a in agents.agents(cfg)]
        text = (f"[b #9b59b6]Voices[/] — each agent its own, one per language, no backup (a failed voice is silent) · "
                f"change them on the grid (E / Z) or in agents.toml\n[dim]{'   '.join(rows)}[/dim]")
        lives = [live_session.LiveBrain(t) for t in ("claude", "gemini", "codex")]
        lives += [live_session.HermesLiveBrain(p, brain_mod.hermes_display_name(p)) for p in brain_mod.hermes_profiles()]
        for live in lives:                             # where "talk to X" goes (2026-10-25)
            target = live.target
            pane = live.pane()
            text += (f"\n[b #2ecc71]Talk to {live.name} →[/] your open window (herdr pane {pane[0]})" if pane else
                     f"\n[#e67e22]Talk to {live.name} →[/] the background thread (no single open window"
                     + ("" if live.session_id or target.startswith("hermes:") else f"; pin one in ~/.voice_desk/{target}_live_session") + ")")
        try:
            off = voicecfg.drift(voicecfg.collect(cfg, voicecfg.PROFILES, voicecfg.CLAUDE_CONF, voicecfg.GEMINI_CONF))
        except Exception as exc:                     # never let a bad file take the panel down
            off, text = [], text + f"\n[#e67e22]voice check failed: {exc}[/]"
        if off:
            who = sorted({i.label.split(" (")[0] for i in off})
            text += (f"\n[b #e67e22]⚠ drift:[/] {len(off)} voice setting(s) differ from agents.toml ({', '.join(who)}). "
                     f"Run [b].venv/bin/python -m voicecfg check[/b] to see them, then sync --dry-run.")
        screen.query_one("#voices", Static).update(text)

    def refresh_status(self) -> None:
        data = service.read_status()
        state = self.state_of(data)
        label, _ = LIGHT[state]
        screen = self.screen
        lights = list(screen.query("#light"))
        if not lights:
            return
        light = lights[0]
        assert isinstance(light, Static)
        light.set_classes(state)                     # one CSS class per state (Textual 7.5 repaint)
        light.update(label + (f"  {_ago(data['since'])}" if state != "off" and data.get("since") else ""))
        level = int(data.get("level") or 0) if state != "off" else 0
        bars = min(10, level // 150)
        deaf = data.get("deaf") or ""
        who = screen.query_one("#who", Static)
        if isinstance(screen, GridScreen):
            who.update(f"on the line: {data.get('name') or '—'}")
        else:
            who.update(f"on the line: {data.get('name') or '—'}\nmic {'▮' * bars}{'▯' * (10 - bars)}"
                       + (f" deaf: {deaf}" if deaf else ""))
        for body in screen.query("#body"):
            heard = data.get("heard") or "—"
            reply = (data.get("reply") or "—").strip()
            if len(reply) > 600:
                reply = reply[:600] + " …"
            note = data.get("note") or ""
            spoke = f"[dim]last line spoken as {data['voice']}[/dim]\n" if data.get("voice") else ""
            assert isinstance(body, Static)
            body.update(f"[b #2ecc71]Heard:[/] {heard}\n\n[b #3498db]{data.get('name') or 'Reply'}:[/] {reply}\n\n"
                        + (f"[dim]{note}[/dim]\n" if note else "")
                        + spoke + "\n"
                        + f"[dim]turns {data.get('turns', 0)} · last took {data.get('took', '—')}s · "
                          f"say \"hey Jarvis\" / \"hey Kylie\" / \"hey Esther\" / \"hey Wanna Buffet\": their own window starts "
                          f"listening; end with \"that's all\" / \"that's all the questions I have\" / \"no further questions\". "
                          f"\"hey Claude, …\" / \"hey Gemini, …\" / \"hey Codex, …\" answer here · "
                          f"\"mute\" / \"unmute\" · X hands the mic back[/dim]")
        msg = self._msg if time.monotonic() < self._msg_until else ""
        for m in screen.query("#msg"):
            assert isinstance(m, Static)
            m.update(msg)

    def say(self, msg: str) -> None:
        self._msg, self._msg_until = msg, time.monotonic() + 8
        self.refresh_status()

    # -- keys that work on both screens -----------------------------------------------------------
    def action_mute(self) -> None:
        on = not service.is_muted()
        service.set_muted(on)
        self.say("muted: the listener ignores everything until unmuted" if on else "unmuted: say a name to start")

    def action_hush(self) -> None:
        service.send("hush")
        self.say("told it to stop talking")


def main() -> None:
    Dashboard().run()


if __name__ == "__main__":
    main()
