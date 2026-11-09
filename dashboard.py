"""Voice Desk dashboard: the voice grid (main screen) and the listener controls (second screen).

    ./dashboard.sh

MAIN SCREEN, the grid: one row per agent, one column per place an agent can speak. Each cell shows the
setting (Off / Voice-back / Always) read from the file where it really lives, and under it what the
logs say really happened (green spoke OK · grey idle, nothing to say · yellow mixed · red mismatch or
failure). The top strip: voice engine · Hermes gateway · speech queue · Hermes fixes · Mac output.
It is an overseer, never a lifeline: every voice setting stays where the agents already read it, so
if this panel closes, voices keep working exactly as last set. On/off cells are read-only in this step.

  ↑ ↓ pick an agent   T silent voice test (no sound)   A test everyone   C check now
  E English voice   Z Mandarin voice   L extra language   O language override   U undo last change
  M mute / unmute · H hush · S listener screen · Q close (the listener keeps running)

SECOND SCREEN, the listener (it never opens the microphone): who is on the line, what was heard and
said, and M mute · H hush · N new conversation · V voices per profile/shared · X hand the mic back ·
R restart · K kill the listener · Esc back to the grid · Q close.

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
from textual.widgets import DataTable, Footer, Input, Static

import agents
import gridevidence as E
import gridstate as G
import gridview as V
import syshealth
import voiceedit
import voicetest
import service
import voicecfg
import live_session
import brain as brain_mod
import voices

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


class GridScreen(Screen):
    CSS = """
    GridScreen { layout: vertical; }
    #top { height: 1; }
    #light { width: 1fr; height: 1; color: white; text-style: bold; padding: 0 1; }
    #who { width: 40; height: 1; color: #dddddd; }
    #health { height: 1; padding: 0 1; }
    #grid { height: 1fr; min-height: 8; }
    #detail { height: auto; max-height: 14; border: round #444444; padding: 0 1; }
    #history { height: auto; max-height: 7; padding: 0 1; }
    #msg { height: 1; color: #bbbbbb; padding: 0 1; }
    """ + _LIGHT_CSS
    BINDINGS = [
        Binding("t", "test_row", "voice test (silent)"),
        Binding("a", "test_all", "test all"),
        Binding("c", "check_now", "check now"),
        Binding("e", "edit_en", "English voice"),
        Binding("z", "edit_zh", "Mandarin voice"),
        Binding("l", "edit_extra", "extra language"),
        Binding("o", "edit_override", "language override"),
        Binding("u", "undo", "undo"),
        Binding("s", "listener", "listener screen"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static("●  starting", id="light", classes="starting")
            yield Static("", id="who")
        yield Static("reading…", id="health")
        yield DataTable(id="grid", cursor_type="row", zebra_stripes=False)
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

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.app.show_detail()         # type: ignore[attr-defined]

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

    def action_undo(self) -> None:
        self.app.undo_last()           # type: ignore[attr-defined]

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
        Binding("v", "voices", "voices: per profile / shared"),
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

    def action_voices(self) -> None:
        on = not voices.per_profile_enabled()
        voices.set_per_profile(on)
        self.app.refresh_voices()      # type: ignore[attr-defined]
        self.app.say("voices: each profile speaks with its own Hermes voice" if on   # type: ignore[attr-defined]
                     else f"voices: everyone speaks with the shared voice ({voices.short(voices.shared_voice())})")

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
        table.clear()
        for row in self.rows:
            key = row.agent.key
            table.add_row(Text(row.agent.display, style="bold"), V.voice_text(row),
                          *[V.cell_text(row.cells[p], row.evidence[p]) for p in G.PLACE_KEYS],
                          V.test_text(self.tests.get(key), key in self.testing), key=key, height=2)
        if self.rows:
            table.move_cursor(row=min(keep, len(self.rows) - 1))
        self.show_detail()

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

    def show_detail(self) -> None:
        screen = self.screen
        if not isinstance(screen, GridScreen):
            return
        row = self.selected()
        screen.query_one("#detail", Static).update(
            V.detail_text(row, self.tests.get(row.agent.key)) if row else "reading the settings and the logs…")
        screen.query_one("#history", Static).update(V.history_text(self.edit_paths))

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

    # -- changing a voice -------------------------------------------------------------------------
    PROMPTS = {
        "voice": ("English voice", "An Edge voice such as en-US-AvaNeural (Esther: a Kokoro name such as alloy). Enter saves, Esc cancels."),
        "voice_zh": ("Mandarin voice", "An Edge voice such as zh-TW-HsiaoChenNeural. Enter saves, Esc cancels."),
        "voices_extra": ("Extra languages", "code=voice, comma separated, e.g. ja=ja-JP-NanamiNeural. Empty removes them all."),
        "voice_override": ("Language override", "auto (follow the reply's language), en, zh, or an extra code. Stored now, enforced later."),
    }

    def current_value(self, row: E.Row, field_name: str) -> str:
        v = row.voice
        return {"voice": v.en, "voice_zh": v.zh, "voices_extra": ", ".join(f"{k}={x}" for k, x in v.extra.items()),
                "voice_override": v.override}[field_name]

    def ask_edit(self, field_name: str) -> None:
        row = self.selected()
        if not row:
            return
        if not voiceedit.editable(row.agent):
            self.say(f"{row.agent.display}: no voice setting the panel can change yet (shared Voice Desk voice + its own hook)")
            return
        title, hint = self.PROMPTS[field_name]

        def answered(text: Optional[str]) -> None:
            if text is not None:
                self.apply_edit(row.agent.key, field_name, text)

        self.push_screen(AskScreen(f"{row.agent.display} — {title}", hint, self.current_value(row, field_name)), answered)

    def apply_edit(self, agent_key: str, field_name: str, text: str) -> None:
        try:
            value = voiceedit.parse_extra(text) if field_name == "voices_extra" else text
        except voiceedit.EditError as exc:
            self.say(str(exc))
            return
        self.say("saving, with a backup and a read-back…")
        self._edit_worker(agent_key, field_name, value)

    @work(thread=True, exclusive=True, group="edit")
    def _edit_worker(self, agent_key: str, field_name: str, value) -> None:
        res = voiceedit.change(agent_key, field_name, value, self.edit_paths)
        self.call_from_thread(self.edit_done, res)

    def undo_last(self) -> None:
        self.say("undoing the last change…")
        self._undo_worker()

    @work(thread=True, exclusive=True, group="edit")
    def _undo_worker(self) -> None:
        res = voiceedit.undo(self.edit_paths)
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
        mode = "per agent" if voices.per_profile_enabled() else f"shared ({voices.short(voices.shared_voice())})"
        rows = [f"{name}: {voices.short(v)}" for name, v in voices.table()]
        claude = next((a for a in agents.agents(cfg) if a.target == "claude"), None)
        if claude and claude.voice:
            rows.append(f"Claude: {voices.short(voices.Voice(claude.voice, claude.voice_zh or voices.zh_for(claude.voice), 'agents'))}")
        for agent in agents.agents(cfg):
            if agent.target in {"gemini", "codex"}:
                rows.append(f"{agent.display}: {voices.short(voices.voice_for(agent.target))}")
        text = f"[b #9b59b6]Voices[/] ({mode}) — V toggles · change them on the grid (E / Z) or in agents.toml\n[dim]{'   '.join(rows)}[/dim]"
        lives = [live_session.LiveBrain(t) for t in ("claude", "gemini", "codex")]
        lives += [live_session.HermesLiveBrain(p, brain_mod.hermes_display_name(p)) for p in brain_mod.hermes_profiles()]
        for live in lives:                             # where "talk to X" goes (2026-10-25)
            target = live.target
            pane = live.pane()
            text += (f"\n[b #2ecc71]Talk to {live.name} →[/] your open window (herdr pane {pane[0]})" if pane else
                     f"\n[#e67e22]Talk to {live.name} →[/] the background thread (no single open window"
                     + ("" if live.session_id or target.startswith("hermes:") else f"; pin one in ~/.voice_desk/{target}_live_session") + ")")
        try:
            off = voicecfg.drift(voicecfg.collect(cfg, voicecfg.PROFILES, voicecfg.CLAUDE_CONF))
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
