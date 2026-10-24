"""Voice Desk — the small window: a coloured light, a transcript, four keys.

Peter never types here. Space = tap-to-talk (only in tap mode), M = switch mode,
S = sleep/wake, N = new conversation thread, Q = quit.
"""
from __future__ import annotations

import fcntl
import threading
import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, RichLog, Static

import settings as S
from engine import Engine

LIGHT = {
    "starting": ("●  starting", "#555555"),
    "listening": ("●  listening", "#2ecc71"),
    "hearing": ("●  hearing you", "#27ae60"),
    "thinking": ("●  thinking", "#f39c12"),
    "speaking": ("●  speaking", "#3498db"),
    "asleep": ("●  asleep, say a name", "#7f8c8d"),
    "error": ("●  error", "#e74c3c"),
    "off": ("●  off", "#333333"),
}


class VoiceDesk(App):
    TITLE = "Voice Desk"
    CSS = """
    Screen { layout: vertical; }
    #top { height: 3; }
    #light { width: 1fr; height: 100%; content-align: center middle; text-style: bold; color: white; }
    #mode { width: 28; height: 100%; content-align: center middle; color: #bbbbbb; }
    #log { height: 1fr; border: round #444444; padding: 0 1; }
    """ + "".join(f"#light.{state} {{ background: {colour}; }}\n" for state, (_, colour) in LIGHT.items())
    BINDINGS = [
        Binding("space", "tap", "talk (tap mode)"),
        Binding("m", "mode", "open mic / tap"),
        Binding("s", "sleep", "sleep / wake"),
        Binding("n", "new_thread", "new conversation"),
        Binding("q", "quit_app", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.engine = Engine(on_event=self._event)

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="top"):
                yield Static("●  starting", id="light", classes="starting")
                yield Static("open mic", id="mode")
            yield RichLog(id="log", wrap=True, markup=True, highlight=False)
            yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write("[dim]Just talk. A pause of a second and a half sends. Say \"stop\" to cut me off, "
                  "\"go to sleep\" to pause, \"goodbye\" to end.[/dim]")
        self.engine.start()
        self.set_interval(0.2, self._meter)

    def _meter(self) -> None:
        """Live microphone level under the mode label, so a silent mic is visible at once."""
        level = self.engine.mic.level
        bars = min(10, int(level / 150))
        meter = "▮" * bars + "▯" * (10 - bars)
        mode = "open mic" if self.engine.mode == "open" else "tap to talk"
        self.query_one("#mode", Static).update(f"{mode} · {self.engine.brain.name()}\nmic {meter} {int(level)}")

    # -- events from the loop thread ---------------------------------------------------------------
    def _event(self, kind: str, text: str) -> None:
        if threading.get_ident() == self._thread_id:     # raised by a key press, already on the app thread
            self._apply(kind, text)
        else:
            self.call_from_thread(self._apply, kind, text)

    def _apply(self, kind: str, text: str) -> None:
        log = self.query_one("#log", RichLog)
        if kind == "state":
            label, _ = LIGHT.get(text, (text, ""))
            light = self.query_one("#light", Static)
            # one CSS class per state: an inline styles.background change does not repaint in Textual 7.5
            light.set_classes(text if text in LIGHT else "starting")
            light.update(label)
            if text == "off":
                self.set_timer(1.5, self.exit)
        elif kind == "you":
            log.write(f"[bold #2ecc71]You:[/] {text}")
        elif kind == "claude":
            log.write(f"[bold #3498db]{self.engine.brain.name()}:[/] {text}\n")
        elif kind == "progress":
            log.write(f"[dim]{text}[/dim]")
        elif kind == "status":
            log.write(f"[dim #f39c12]… {text}[/]")
        elif kind == "note":
            log.write(f"[dim]({text})[/dim]")
        self._meter()  # Keep the active brain visible immediately after every event.

    # -- keys -----------------------------------------------------------------------------------
    def action_tap(self) -> None:
        if self.engine.mode == "tap":
            self.engine.tap()
        else:
            self.engine.interrupt()

    def action_mode(self) -> None:
        self.engine.toggle_mode()
        self._apply("note", "")

    def action_sleep(self) -> None:
        if self.engine.asleep:
            self.engine.wake()
        else:
            self.engine.sleep()

    def action_new_thread(self) -> None:
        self.engine.brain.new_thread()
        self._apply("note", f"new conversation thread with {self.engine.brain.name()}; it forgets what we said before")

    def action_quit_app(self) -> None:
        self.engine.quit()
        self.exit()


def main() -> None:
    lock = open(S.STATE_DIR / "window.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        import service
        if service.is_fresh(service.read_status()):
            print("The always-on listener has the microphone (just say a name). "
                  "./dashboard.sh shows it; K in the dashboard stops it if you want this window instead.")
        else:
            print("Voice Desk is already open in another window.")
        sys.exit(1)
    VoiceDesk().run()


if __name__ == "__main__":
    main()
