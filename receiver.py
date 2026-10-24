"""Phone door: a tiny web receiver the iPhone Shortcut talks to.

Siri gives up on any single request that takes much longer than ten seconds, and a
Claude turn can take 20 s or more, so the phone never waits for a whole turn:

POST /ask   {"text": "...", "to": "esther"}  -> {"reply": "...", "pending": bool}
                                the answer if it was ready within ASK_WAIT_SEC, else ".".
                                "to" (optional) picks "claude", "gemini", "codex" or a Hermes
                                profile by name; each phone Shortcut is pinned to one.
GET  /reply                  -> {"reply": "..."}
                                the next finished answer (waits up to POLL_WAIT_SEC for one);
                                "." when there is nothing to say, a short "still working"
                                line every few polls while Claude is busy
GET  /health                 -> {"ok": true, "targets": [...]}   who you can talk to

Every request must carry the shared token (header X-Token or JSON field "token").
Each answer is handed out exactly once, so the Shortcut can poll on a fixed schedule and
simply speak whatever comes back. One log line per request goes to receiver.log.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import settings as S
import tts
from brain import _TOOL_STATUS, Brain

ASK_WAIT_SEC = 8            # how long /ask may hold the phone before answering "not yet"
POLL_WAIT_SEC = 8           # same for /reply
STATUS_EVERY_POLLS = 3      # a spoken "still working" roughly every 24 s of waiting
SILENT = "."                # Speak Text says nothing for this, and never errors on it
KEEP_SEC = 600              # finished jobs are forgotten after this

brain = Brain()
_jobs: dict[str, dict] = {}                 # insertion order = oldest first
_cv = threading.Condition()


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def token() -> str:
    try:
        t = S.RECEIVER_TOKEN_FILE.read_text().strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(18)
    S.RECEIVER_TOKEN_FILE.write_text(t)
    S.RECEIVER_TOKEN_FILE.chmod(0o600)
    return t


TOKEN = token()


# -- jobs ---------------------------------------------------------------------------------
def start_job(text: str, to: str = "") -> dict:
    job = {"id": uuid.uuid4().hex[:8], "text": text, "to": to, "reply": "", "done": False,
           "consumed": False, "started": time.monotonic(), "polls": 0}
    with _cv:
        _prune()
        _jobs[job["id"]] = job
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"job-{job['id']}").start()
    return job


def _run(job: dict) -> None:
    try:
        reply = brain.ask(job["text"], to=job["to"] or None)
    except Exception as exc:                       # the phone should hear it, not silence
        reply = f"I hit an error: {exc}"
    with _cv:
        job["reply"] = tts.speakable(reply) or "I did not get an answer back."
        job["done"] = True
        job["took"] = time.monotonic() - job["started"]
        _cv.notify_all()


def _prune() -> None:
    now = time.monotonic()
    for jid in [j for j, job in _jobs.items() if job["consumed"] and now - job["started"] > KEEP_SEC]:
        del _jobs[jid]


def take_reply(timeout: float) -> tuple[str, bool]:
    """(text, is_answer). Waits up to timeout for the oldest finished, unspoken answer."""
    deadline = time.monotonic() + timeout
    with _cv:
        while True:
            for job in _jobs.values():
                if job["done"] and not job["consumed"]:
                    job["consumed"] = True
                    return job["reply"], True
            pending = [j for j in _jobs.values() if not j["done"]]
            if not pending:
                return SILENT, False                 # nothing in flight: answer at once, never hold the phone
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                job = pending[0]
                job["polls"] += 1
                if job["polls"] % STATUS_EVERY_POLLS == 0:
                    return _TOOL_STATUS.get(brain.last_tool, "still working on it") + ".", False
                return SILENT, False
            _cv.wait(remaining)


# -- http ---------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> bool:
        body = json.dumps(payload, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False                            # the phone hung up; the answer stays queued

    def _authed(self, data: dict) -> bool:
        return (self.headers.get("X-Token") or data.get("token")) == TOKEN

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        t0 = time.monotonic()
        if path == "/health":
            self._send(200, {"ok": True, "targets": brain.targets()})
            return
        if path != "/reply":
            self._send(404, {"error": "not found"})
            return
        if not self._authed({}):
            self._send(403, {"error": "wrong token"})
            log(f"{self.client_address[0]} GET /reply wrong token")
            return
        text, is_answer = take_reply(POLL_WAIT_SEC)
        sent = self._send(200, {"reply": text})
        if is_answer or text != SILENT:
            log(f"{self.client_address[0]} GET /reply {time.monotonic()-t0:.1f}s "
                f"{'answer' if is_answer else 'status'}{'' if sent else ' (phone hung up)'}: {text[:60]!r}")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/ask":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "bad json"})
            return
        if not self._authed(data):
            self._send(403, {"error": "wrong token"})
            log(f"{self.client_address[0]} POST /ask wrong token")
            return
        text = (data.get("text") or "").strip()
        if not text:
            self._send(400, {"error": "empty"})
            return
        to = (data.get("to") or "").strip()
        if to and not brain.resolve(to):
            self._send(200, {"reply": f"I do not know anyone called {to}.", "pending": False})
            log(f"{self.client_address[0]} POST /ask unknown target {to!r}")
            return
        t0 = time.monotonic()
        job = start_job(text, to)
        log(f"{self.client_address[0]} POST /ask job {job['id']} -> {brain.name(to or None)}: {text[:80]!r}")
        reply, is_answer = take_reply(ASK_WAIT_SEC)
        sent = self._send(200, {"reply": reply, "pending": not is_answer, "job": job["id"]})
        log(f"{self.client_address[0]} POST /ask job {job['id']} {time.monotonic()-t0:.1f}s "
            f"{'answered' if is_answer else 'pending'}{'' if sent else ' (phone hung up)'}")

    def log_message(self, fmt, *args) -> None:  # quiet; we log our own lines
        pass


def main() -> None:
    srv = ThreadingHTTPServer((S.RECEIVER_HOST, S.RECEIVER_PORT), Handler)
    log(f"Voice Desk receiver on {S.RECEIVER_HOST}:{S.RECEIVER_PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
