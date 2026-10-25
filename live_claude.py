"""Claude in Peter's live terminal session: "talk to Claude" continues that session itself.

Peter 2026-10-25: "that trigger phrase will trigger it to activate this particular session I'm
talking to you right now to continue the conversation". The background Claude thread
(brain.ClaudeBrain) is a separate conversation; this one types into the real session.

How a turn works:
  1. The pinned session id is read from ~/.voice_desk/claude_live_session.
  2. herdr finds the pane showing that session (each Claude pane reports its session id).
  3. The words are typed into that pane, prefixed "[voice] ", and Enter is pressed, exactly as
     if Peter had typed them.
  4. The session's own transcript (~/.claude/projects/*/<id>.jsonl) is followed until the pane
     is idle again; the answer is the last text Claude wrote before going idle (or before a
     Stop hook's feedback, so an auto-save note is never read out as the answer).
  5. ~/.voice_desk/live_turn exists while the turn runs, so the session's own Stop hook
     (~/.claude/hooks/speak-response.sh) stays silent and the answer is spoken once, here.

If the session is not open in a herdr pane, `available()` is False and "Claude" falls back to
the background thread, which says so.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import settings as S

StatusCb = Callable[[str], None]
TextCb = Callable[[str], None]

PREFIX = "[voice] "
DELIVERY_TIMEOUT_SEC = 25        # the typed words must show up in the transcript by then
QUIET_SEC = 3.0                  # transcript unchanged this long while the pane is idle = done
POLL_SEC = 0.5

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


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


class LiveClaudeBrain:
    name = "Claude"
    target = "claude"

    def __init__(self) -> None:
        self.session_id = self._load_session()
        self.last_tool = ""
        self._cancel = threading.Event()

    # -- which session ----------------------------------------------------------------------
    def _load_session(self) -> Optional[str]:
        try:
            sid = S.CLAUDE_LIVE_SESSION_FILE.read_text().strip()
        except OSError:
            return None
        return sid or None

    def new_thread(self) -> None:
        """A live session cannot be reset by voice; the pin stays (Peter changes it himself)."""

    def pane(self) -> Optional[tuple[str, str]]:
        """(pane id, agent status) of the herdr pane showing the pinned session, or None."""
        sid = self._load_session()
        if not sid:
            return None
        try:
            panes = json.loads(_herdr("pane", "list"))["result"]["panes"]
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            return None
        for p in panes:
            if (p.get("agent_session") or {}).get("value") == sid:
                return p["pane_id"], p.get("agent_status", "")
        return None

    def available(self) -> bool:
        return self.pane() is not None

    def transcript(self) -> Optional[Path]:
        sid = self._load_session()
        hits = sorted(S.CLAUDE_PROJECTS_DIR.glob(f"*/{sid}.jsonl")) if sid else []
        return hits[0] if hits else None

    # -- one turn ---------------------------------------------------------------------------
    def ask(self, text: str, on_status: StatusCb = lambda s: None, on_text: TextCb = lambda s: None) -> str:
        found = self.pane()
        path = self.transcript()
        if not found or not path:
            return "Your live Claude session is not open in herdr right now, so I could not reach it."
        pane_id = found[0]
        self._cancel.clear()
        self.last_tool = ""
        offset = path.stat().st_size
        message = PREFIX + " ".join(text.split())
        S.LIVE_TURN_FLAG.write_text(str(time.time()))
        try:
            _herdr("pane", "send-text", pane_id, message)
            _herdr("pane", "send-keys", pane_id, "enter")
            return self._follow(path, offset, message, pane_id, on_status, on_text)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            return f"I could not type into your live Claude session. The error was: {e}"
        finally:
            S.LIVE_TURN_FLAG.unlink(missing_ok=True)

    def _follow(self, path: Path, offset: int, message: str, pane_id: str,
                on_status: StatusCb, on_text: TextCb) -> str:
        start = last_change = last_status = time.monotonic()
        delivered, answer, frozen, pending, buf = False, "", False, "", ""
        with path.open("rb") as fh:
            fh.seek(offset)
            while not self._cancel.is_set():
                chunk = fh.read()
                if chunk:
                    last_change = time.monotonic()
                    buf += chunk.decode("utf-8", errors="replace")
                    *lines, buf = buf.split("\n")
                    for line in lines:
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        role = ev.get("type")
                        body = _text_of((ev.get("message") or {}).get("content"))
                        if role == "user" and not delivered and message[:60] in body:
                            delivered = True
                        elif role == "user" and delivered and body.lstrip().startswith("Stop hook feedback"):
                            frozen = True          # an auto-save checkpoint, not Peter: keep the answer as it was
                        elif role == "assistant" and delivered and not frozen:
                            for block in (ev.get("message") or {}).get("content", []) or []:
                                if block.get("type") == "tool_use":
                                    self.last_tool = block.get("name", "")
                                    if pending:
                                        on_text(pending)
                                        pending = ""
                                elif block.get("type") == "text" and block.get("text", "").strip():
                                    if pending:
                                        on_text(pending)
                                    pending = answer = block["text"]
                now = time.monotonic()
                if not delivered and now - start > DELIVERY_TIMEOUT_SEC:
                    return "I typed it into your live Claude session, but it never arrived there."
                if delivered and answer and now - last_change > QUIET_SEC:
                    found = self.pane()
                    if not found or found[1] != "working":
                        return answer
                if now - start > S.CLAUDE_TURN_TIMEOUT_SEC:
                    return answer or "Your live Claude session is still working; the answer will be on the screen."
                if now - last_status > S.STATUS_EVERY_SEC:
                    last_status = now
                    on_status(_TOOL_STATUS.get(self.last_tool, "still working on it"))
                time.sleep(POLL_SEC)
        return answer or "Stopped."

    def cancel(self) -> None:
        self._cancel.set()
