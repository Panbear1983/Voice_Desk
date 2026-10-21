"""Voice Desk dashboard: the control panel for the always-on listener (listener.py).

It reads ~/.voice_desk/listener.json, which the listener writes, and never opens the
microphone itself. Keys: M mute/unmute · H hush (stop talking) · N new conversation ·
R restart the listener · K kill (stop) the listener · Q close this panel (the listener keeps
running).

    ./dashboard.sh
"""
from __future__ import annotations

import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

import service
import settings as S

LIGHT = {
    "starting": ("●  starting", "#555555"),
    "asleep": ("●  asleep · say a name", "#7f8c8d"),
    "listening": ("●  listening", "#2ecc71"),
    "hearing": ("●  hearing you", "#27ae60"),
    "thinking": ("●  thinking", "#f39c12"),
    "speaking": ("●  speaking", "#3498db"),
    "muted": ("●  muted · say unmute", "#8e44ad"),
    "standby": ("●  standby · the window has the mic", "#555555"),
    "error": ("●  error", "#e74c3c"),
    "off": ("●  listener off · R starts it", "#333333"),
}


def _ago(t: float) -> str:
    s = max(0, int(time.time() - t))
    return f"{s}s" if s < 90 else f"{s // 60}m" if s < 5400 else f"{s // 3600}h"


class Dashboard(App):
    TITLE = "Voice Desk"
    CSS = """
    Screen { layout: vertical; }
    #top { height: 3; }
    #light { width: 1fr; height: 100%; content-align: center middle; text-style: bold; color: white; }
    #who { width: 30; height: 100%; content-align: center middle; color: #dddddd; }
    #body { height: 1fr; border: round #444444; padding: 0 1; }
    #msg { height: 1; color: #888888; padding: 0 1; }
    """ + "".join(f"#light.{state} {{ background: {colour}; }}\n" for state, (_, colour) in LIGHT.items())
    BINDINGS = [
        Binding("m", "mute", "mute / unmute"),
        Binding("h", "hush", "hush"),
        Binding("n", "new_thread", "new conversation"),
        Binding("r", "restart", "restart listener"),
        Binding("k", "kill", "kill listener"),
        Binding("q", "quit", "close panel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._msg = ""
        self._msg_until = 0.0

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="top"):
                yield Static("●  starting", id="light", classes="starting")
                yield Static("", id="who")
            yield Static("", id="body")
            yield Static("", id="msg")
            yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        self.set_interval(0.5, self.refresh_status)

    # -- reading the listener -------------------------------------------------------------------
    def state_of(self, data: dict) -> str:
        if not service.is_fresh(data):
            return "off"
        if data.get("state") in ("asleep", "listening") and (data.get("muted") or service.is_muted()):
            return "muted"
        st = str(data.get("state") or "")
        return st if st in LIGHT else "starting"

    def refresh_status(self) -> None:
        data = service.read_status()
        state = self.state_of(data)
        label, _ = LIGHT[state]
        light = self.query_one("#light", Static)
        light.set_classes(state)                     # one CSS class per state (Textual 7.5 repaint)
        light.update(label + (f"  {_ago(data['since'])}" if state != "off" and data.get("since") else ""))
        level = int(data.get("level") or 0) if state != "off" else 0
        bars = min(10, level // 150)
        deaf = data.get("deaf") or ""
        self.query_one("#who", Static).update(
            f"on the line: {data.get('name') or '—'}\nmic {'▮' * bars}{'▯' * (10 - bars)}"
            + (f" deaf: {deaf}" if deaf else ""))
        heard = data.get("heard") or "—"
        reply = (data.get("reply") or "—").strip()
        if len(reply) > 600:
            reply = reply[:600] + " …"
        note = data.get("note") or ""
        body = (f"[b #2ecc71]Heard:[/] {heard}\n\n[b #3498db]{data.get('name') or 'Reply'}:[/] {reply}\n\n"
                + (f"[dim]{note}[/dim]\n" if note else "")
                + f"[dim]turns {data.get('turns', 0)} · last took {data.get('took', '—')}s · "
                  f"say \"Jarvis, …\", \"hey Kylie\", \"Esther, …\", \"Wanna Buffet, …\", \"hey Claude\"; \"mute\" / \"unmute\"[/dim]")
        self.query_one("#body", Static).update(body)
        msg = self._msg if time.monotonic() < self._msg_until else ""
        self.query_one("#msg", Static).update(msg)

    def say(self, msg: str) -> None:
        self._msg, self._msg_until = msg, time.monotonic() + 6
        self.refresh_status()

    # -- keys ---------------------------------------------------------------------------------------
    def action_mute(self) -> None:
        on = not service.is_muted()
        service.set_muted(on)
        self.say("muted: the listener ignores everything until unmuted" if on else "unmuted: say a name to start")

    def action_hush(self) -> None:
        service.send("hush")
        self.say("told it to stop talking")

    def action_new_thread(self) -> None:
        service.send("new_thread")
        self.say("the next turn starts a fresh conversation with whoever is on the line")

    def action_restart(self) -> None:
        self.say("listener: " + service.service_restart())

    def action_kill(self) -> None:
        self.say("listener: " + service.service_stop() + " (R starts it again)")


def main() -> None:
    Dashboard().run()


if __name__ == "__main__":
    main()
