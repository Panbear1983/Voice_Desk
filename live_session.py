"""Live terminal sessions: "talk to Claude / Gemini / Codex" continues the window Peter has open.

Peter 2026-10-25: "talk to Claude ... activate this particular session I'm talking to you right
now", then "yes, do the same for Gemini and Codex". The background threads in brain.py
(ClaudeBrain, GeminiBrain, CodexBrain) are separate conversations; these type into the real one.

One turn, for any of the three:
  1. Pick the window. herdr reports, for every pane, which agent runs in it ("claude", "agy",
     "codex") and that agent's own session id. A session id pinned in
     ~/.voice_desk/<target>_live_session wins; with no pin (or a pin that is not open), the only
     open pane of that agent is used. None open, or several and no pin: not available, and the
     background thread answers instead.
  2. Type the words, prefixed "[voice] ", into that pane and press Enter, exactly as if Peter
     had typed them.
  3. Follow that session's own transcript file until the turn is over (Codex writes an explicit
     task_complete; Claude and Gemini: the pane is no longer "working" and the file has been
     quiet for QUIET_SEC). The answer is the last text the agent wrote, never a Stop hook's
     auto-save note.
  4. ~/.voice_desk/live_turn exists while the turn runs, so the window's own speak-aloud hooks
     stay silent and the answer is spoken once, on the device that asked.
"""
from __future__ import annotations

import glob
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

import settings as S

StatusCb = Callable[[str], None]
TextCb = Callable[[str], None]

PREFIX = "[voice] "
DELIVERY_TIMEOUT_SEC = 25        # the typed words must show up in the transcript by then
QUIET_SEC = 3.0                  # transcript unchanged this long while the pane is idle = done
POLL_SEC = 0.5
ENTER_DELAY_SEC = 0.6           # Codex takes an Enter that arrives with the pasted text as part of it
RETRY_ENTER_SEC = 5.0           # words typed but not sent by then: press Enter once more

_TOOL_STATUS = {
    "Read": "still reading files", "Grep": "still searching the code", "Glob": "still looking for files",
    "Bash": "still running a command", "Edit": "still editing a file", "Write": "still writing a file",
    "WebSearch": "still searching the web", "Agent": "still waiting on a helper",
}


def _herdr(*args: str, timeout: float = 10) -> str:
    p = subprocess.run([S.HERDR_BIN, *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip()[-200:])
    return p.stdout


# ---- what each agent writes to its transcript ------------------------------------------------
# Each reader turns one transcript line into zero or more of:
#   ("user", text)  ("say", text)  ("tool", name)  ("hook", "")  ("done", final text or "")
Event = tuple[str, str]


def _claude_events(ev: dict) -> Iterator[Event]:
    msg = ev.get("message") or {}
    content = msg.get("content")
    if ev.get("type") == "user":
        text = content if isinstance(content, str) else "\n".join(
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
        yield ("hook", "") if text.lstrip().startswith("Stop hook feedback") else ("user", text)
    elif ev.get("type") == "assistant":
        for b in content or []:
            if b.get("type") == "tool_use":
                yield "tool", b.get("name", "")
            elif b.get("type") == "text" and b.get("text", "").strip():
                yield "say", b["text"]


def _gemini_events(ev: dict) -> Iterator[Event]:
    kind, content = ev.get("type"), ev.get("content") or ""
    if kind == "USER_INPUT":
        m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content, re.S)
        yield "user", m.group(1) if m else content
    elif kind == "PLANNER_RESPONSE" and ev.get("source") == "MODEL":
        yield ("say", content) if content.strip() else ("tool", "")
    elif kind == "GENERIC":
        yield "tool", ""


def _codex_events(ev: dict) -> Iterator[Event]:
    p = ev.get("payload") or {}
    kind = p.get("type")
    if ev.get("type") == "response_item" and kind == "message":
        text = "\n".join(b.get("text", "") for b in p.get("content") or [] if isinstance(b, dict))
        if p.get("role") == "user":
            yield "user", text
        elif p.get("role") == "assistant" and text.strip():
            yield "say", text
    elif ev.get("type") == "response_item" and kind in ("function_call", "custom_tool_call"):
        yield "tool", p.get("name", "")
    elif ev.get("type") == "event_msg" and kind == "task_complete":
        yield "done", p.get("last_agent_message") or ""


@dataclass(frozen=True)
class Agent:
    target: str            # Voice Desk's target id
    name: str              # what it is called out loud
    herdr_agent: str       # what herdr calls it in `pane list`
    locate: Callable[[str], Optional[Path]]
    events: Callable[[dict], Iterator[Event]]
    explicit_done: bool    # True: the transcript says when the turn is over


def _first(pattern: str) -> Optional[Path]:
    hits = sorted(glob.glob(pattern))
    return Path(hits[0]) if hits else None


AGENTS = {
    "claude": Agent("claude", "Claude", "claude",
                    lambda sid: _first(f"{S.CLAUDE_PROJECTS_DIR}/*/{sid}.jsonl"), _claude_events, False),
    "gemini": Agent("gemini", "Gemini", "agy",
                    lambda sid: S.GEMINI_BRAIN_DIR / sid / ".system_generated/logs/transcript.jsonl",
                    _gemini_events, False),
    "codex": Agent("codex", "Codex", "codex",
                   lambda sid: _first(f"{S.CODEX_SESSIONS_DIR}/*/*/*/rollout-*-{sid}.jsonl"), _codex_events, True),
}


class LiveBrain:
    """One agent's live window, behind the same ask/cancel/new_thread interface as the
    background brains."""

    def __init__(self, target: str) -> None:
        self.agent = AGENTS[target]
        self.name = self.agent.name
        self.target = target
        self.pin_file = S.STATE_DIR / f"{target}_live_session"
        self.session_id = self._load_session()
        self.last_tool = ""
        self._cancel = threading.Event()

    # -- which window ---------------------------------------------------------------------
    def _load_session(self) -> Optional[str]:
        try:
            return self.pin_file.read_text().strip() or None
        except OSError:
            return None

    def new_thread(self) -> None:
        """A live window cannot be reset by voice; Peter starts a new one himself."""

    def pane(self) -> Optional[tuple[str, str, str]]:
        """(pane id, agent status, session id) of the window to talk to, or None."""
        try:
            panes = json.loads(_herdr("pane", "list"))["result"]["panes"]
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            return None
        mine = [(p["pane_id"], p.get("agent_status", ""), (p.get("agent_session") or {}).get("value", ""))
                for p in panes if p.get("agent") == self.agent.herdr_agent and (p.get("agent_session") or {}).get("value")]
        pin = self._load_session()
        for found in mine:
            if pin and found[2] == pin:
                return found
        return mine[0] if len(mine) == 1 else None

    def available(self) -> bool:
        return self.pane() is not None

    # -- one turn -----------------------------------------------------------------------------
    def ask(self, text: str, on_status: StatusCb = lambda s: None, on_text: TextCb = lambda s: None) -> str:
        found = self.pane()
        if not found:
            return f"Your {self.name} window is not open in herdr right now, so I could not reach it."
        pane_id, _, sid = found
        path = self.agent.locate(sid)
        offset = path.stat().st_size if path and path.exists() else 0
        self._cancel.clear()
        self.last_tool = ""
        message = PREFIX + " ".join(text.split())
        S.LIVE_TURN_FLAG.write_text(f"{self.target} {time.time()}")
        try:
            _herdr("pane", "send-text", pane_id, message)
            time.sleep(ENTER_DELAY_SEC)
            _herdr("pane", "send-keys", pane_id, "enter")
            return self._follow(pane_id, sid, offset, message, on_status, on_text)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            return f"I could not type into your {self.name} window. The error was: {e}"
        finally:
            S.LIVE_TURN_FLAG.unlink(missing_ok=True)

    def _follow(self, pane_id: str, sid: str, offset: int, message: str,
                on_status: StatusCb, on_text: TextCb) -> str:
        start = last_change = last_status = time.monotonic()
        delivered, frozen, answer, pending, buf, retried = False, False, "", "", "", False
        fh = None
        try:
            while not self._cancel.is_set():
                if fh is None:                               # a brand-new window writes its file on the first turn
                    path = self.agent.locate(sid)
                    if path and path.exists():
                        fh = path.open("rb")
                        fh.seek(offset)
                chunk = fh.read() if fh else b""
                if chunk:
                    last_change = time.monotonic()
                    buf += chunk.decode("utf-8", errors="replace")
                    *lines, buf = buf.split("\n")
                    for line in lines:
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        for kind, value in self.agent.events(ev):
                            if kind == "user" and not delivered and message[:60] in value:
                                delivered = True
                            elif not delivered:
                                continue
                            elif kind == "hook":
                                frozen = True                # an auto-save checkpoint, not Peter
                            elif frozen:
                                continue
                            elif kind == "tool":
                                self.last_tool = value
                                if pending:
                                    on_text(pending)
                                    pending = ""
                            elif kind == "say":
                                if pending:
                                    on_text(pending)
                                pending = answer = value
                            elif kind == "done":
                                return value or answer or "Done."
                now = time.monotonic()
                if not delivered and not retried and now - start > RETRY_ENTER_SEC:
                    retried = True                           # an empty Enter is harmless in all three windows
                    _herdr("pane", "send-keys", pane_id, "enter")
                if not delivered and now - start > DELIVERY_TIMEOUT_SEC:
                    return f"I typed it into your {self.name} window, but it never arrived there."
                if delivered and answer and not self.agent.explicit_done and now - last_change > QUIET_SEC:
                    found = self.pane()
                    if not found or found[1] != "working":
                        return answer
                if now - start > S.CLAUDE_TURN_TIMEOUT_SEC:
                    return answer or f"Your {self.name} window is still working; the answer will be on the screen."
                if now - last_status > S.STATUS_EVERY_SEC:
                    last_status = now
                    on_status(_TOOL_STATUS.get(self.last_tool, "still working on it"))
                time.sleep(POLL_SEC)
            return answer or "Stopped."
        finally:
            if fh:
                fh.close()

    def cancel(self) -> None:
        self._cancel.set()
