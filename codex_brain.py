"""Optional Codex CLI backend, with a private Voice Desk session per installation."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
from typing import Callable, Optional
from uuid import UUID

import settings as S


class CodexBrain:
    name = "Codex"
    target = "codex"

    def __init__(self) -> None:
        self.session_id = self._load_session()
        self.last_tool = ""
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def _load_session(self) -> Optional[str]:
        try:
            return str(UUID(S.CODEX_SESSION_FILE.read_text().strip()))
        except (OSError, ValueError):
            return None

    def _save_session(self, sid: str) -> None:
        sid = str(UUID(sid))
        with tempfile.NamedTemporaryFile(mode="w", dir=S.CODEX_SESSION_FILE.parent,
                                         prefix=".codex-session-", delete=False) as stream:
            path = stream.name
            stream.write(sid)
        try:
            os.replace(path, S.CODEX_SESSION_FILE)
        finally:
            if os.path.exists(path):
                os.unlink(path)
        self.session_id = sid

    def new_thread(self) -> None:
        S.CODEX_SESSION_FILE.unlink(missing_ok=True)
        self.session_id = None

    def _command(self) -> list[str]:
        # Never inherit a permission-bypass setting into this unattended voice path.
        cmd = [S.CODEX_BIN, "--sandbox", "workspace-write", "-c", 'approval_policy="never"']
        if S.CODEX_MODEL:
            cmd += ["--model", S.CODEX_MODEL]
        if S.CODEX_EFFORT:
            cmd += ["-c", "model_reasoning_effort=" + json.dumps(S.CODEX_EFFORT)]
        cmd += ["exec", "--json", "--skip-git-repo-check"]
        if self.session_id:
            cmd += ["resume", self.session_id]
        return cmd + ["-"]

    def ask(self, text: str, on_status: Callable = lambda s: None,
            on_text: Callable = lambda s: None) -> str:
        if not text.strip():
            return "I did not hear a question."
        previous = self.session_id
        self._cancelled = False
        self.last_tool = ""
        env = dict(os.environ, VOICE_DESK_LOOP="1")
        prompt = S.VOICE_SYSTEM_PROMPT + "\n\n" + text
        try:
            self._proc = subprocess.Popen(self._command(), cwd=str(S.CODEX_CWD), env=env,
                                          text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, start_new_session=True)
        except (OSError, ValueError):
            return "Codex could not start. Check its installation and Voice Desk working folder."
        stopped = threading.Event()

        def status() -> None:
            while not stopped.wait(S.STATUS_EVERY_SEC):
                on_status("Codex is still working on it")

        ticker = threading.Thread(target=status, daemon=True)
        ticker.start()
        try:
            try:
                out, _ = self._proc.communicate(input=prompt, timeout=S.CODEX_TURN_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                self.cancel()
                self._proc.communicate()
                return "Codex took too long, so I stopped this turn. Check its work before asking again."
            code = self._proc.returncode
        finally:
            stopped.set()
            ticker.join(timeout=1)
            self._proc = None
        if self._cancelled:
            return "Okay, I stopped Codex."
        session, answer, completed, failure = None, "", False, ""
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "thread.started":
                try:
                    session = str(UUID(event.get("thread_id", "")))
                except (ValueError, TypeError, AttributeError):
                    failure = "Codex returned an invalid conversation identifier."
            elif kind == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message" and item.get("text"):
                    answer = item["text"].strip()
            elif kind == "turn.completed":
                completed = True
            elif kind in {"turn.failed", "error"}:
                detail = event.get("error") or event
                message = detail.get("message", "") if isinstance(detail, dict) else str(detail)
                failure = message or "Codex reported a failed turn."
        if previous and session and session != previous:
            return "Codex did not resume the saved conversation. I kept its original identifier; please check Codex."
        if session:
            self._save_session(session)
        if failure or code or not completed or not answer:
            reason = failure.lower()
            if any(word in reason for word in ("usage limit", "usage_limit", "rate limit", "quota", "credits")):
                return "Codex reached its usage limit. Its conversation is saved; try again after the allowance resets."
            if any(word in reason for word in ("login", "log in", "sign in", "unauthorized", "authentication")):
                return "Codex needs you to sign in. Run codex login on this Mac, then try again."
            return "Codex did not complete this turn. Check its login, permissions or saved session before retrying."
        # Same transcript format as the other brains, without a circular brain import.
        import time
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(S.TRANSCRIPT_LOG, "a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] {S.USER_NAME.upper()}: {text}\n[{stamp}] CODEX: {answer}\n\n")
        return answer

    def cancel(self) -> None:
        self._cancelled = True
        process = self._proc
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
