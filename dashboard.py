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

import agents
import service
import settings as S
import voicecfg
import voices

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
    #voices { height: auto; border: round #444444; padding: 0 1; }
    #msg { height: 1; color: #888888; padding: 0 1; }
    """ + "".join(f"#light.{state} {{ background: {colour}; }}\n" for state, (_, colour) in LIGHT.items())
    BINDINGS = [
        Binding("m", "mute", "mute / unmute"),
        Binding("h", "hush", "hush"),
        Binding("n", "new_thread", "new conversation"),
        Binding("v", "voices", "voices: per profile / shared"),
        Binding("x", "release", "hand the mic back"),
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
            yield Static("", id="voices")
            yield Static("", id="msg")
            yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        self.refresh_voices()
        self.set_interval(0.5, self.refresh_status)
        self.set_interval(5.0, self.refresh_voices)

    # -- the voices panel (read-only; agents.toml is the one place to change a voice) ---------------
    def refresh_voices(self) -> None:
        cfg = voices.config()
        mode = "per agent" if voices.per_profile_enabled() else f"shared ({voices.short(voices.shared_voice())})"
        rows = [f"{name}: {voices.short(v)}" for name, v in voices.table()]
        claude = next((a for a in agents.agents(cfg) if a.target == "claude"), None)
        if claude and claude.voice:
            rows.append(f"Claude: {voices.short(voices.Voice(claude.voice, claude.voice_zh or voices.zh_for(claude.voice), 'agents'))}")
        for agent in agents.agents(cfg):
            if agent.target in {"gemini", "codex"}:
                rows.append(f"{agent.display}: {voices.short(voices.voice_for(agent.target))}")
        text = f"[b #9b59b6]Voices[/] ({mode}) — V toggles · change them in agents.toml\n[dim]{'   '.join(rows)}[/dim]"
        try:
            off = voicecfg.drift(voicecfg.collect(cfg, voicecfg.PROFILES, voicecfg.CLAUDE_CONF))
        except Exception as exc:                     # never let a bad file take the panel down
            off, text = [], text + f"\n[#e67e22]voice check failed: {exc}[/]"
        if off:
            who = sorted({i.label.split(" (")[0] for i in off})
            text += (f"\n[b #e67e22]⚠ drift:[/] {len(off)} voice setting(s) differ from agents.toml ({', '.join(who)}). "
                     f"Run [b].venv/bin/python -m voicecfg check[/b] to see them, then sync --dry-run.")
        self.query_one("#voices", Static).update(text)

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
        spoke = f"[dim]last line spoken as {data['voice']}[/dim]\n" if data.get("voice") else ""
        body = (f"[b #2ecc71]Heard:[/] {heard}\n\n[b #3498db]{data.get('name') or 'Reply'}:[/] {reply}\n\n"
                + (f"[dim]{note}[/dim]\n" if note else "")
                + spoke + "\n"
                + f"[dim]turns {data.get('turns', 0)} · last took {data.get('took', '—')}s · "
                  f"say \"hey Jarvis\" / \"hey Kylie\" / \"hey Esther\" / \"hey Wanna Buffet\": their own window starts "
                  f"listening; end with \"that's all\" / \"that's all the questions I have\" / \"no further questions\". "
                  f"\"hey Claude, …\" / \"hey Gemini, …\" / \"hey Codex, …\" answer here · "
                  f"\"mute\" / \"unmute\" · X hands the mic back[/dim]")
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

    def action_release(self) -> None:
        service.send("release")
        self.say("the listener takes the mic back and waits for a name")

    def action_new_thread(self) -> None:
        service.send("new_thread")
        self.say("the next turn starts a fresh conversation with whoever is on the line")

    def action_voices(self) -> None:
        on = not voices.per_profile_enabled()
        voices.set_per_profile(on)
        self.refresh_voices()
        self.say("voices: each profile speaks with its own Hermes voice" if on
                 else f"voices: everyone speaks with the shared voice ({voices.short(voices.shared_voice())})")

    def action_restart(self) -> None:
        self.say("listener: " + service.service_restart())

    def action_kill(self) -> None:
        self.say("listener: " + service.service_stop() + " (R starts it again)")


def main() -> None:
    Dashboard().run()


if __name__ == "__main__":
    main()
