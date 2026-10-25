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
from urllib.parse import parse_qs, urlparse

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
_client_target: dict[str, str] = {}         # client_ip -> resolved target of its latest /ask
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
def start_job(text: str, to: str = "", client_ip: str = "") -> dict:
    resolved_target = brain.resolve(to) if to else brain.target()
    job = {"id": uuid.uuid4().hex[:8], "text": text, "to": to, "target": resolved_target,
           "client_ip": client_ip, "reply": "", "done": False,
           "consumed": False, "started": time.monotonic(), "polls": 0}
    with _cv:
        _prune()
        _jobs[job["id"]] = job
        if client_ip:
            _client_target[client_ip] = resolved_target
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


def take_reply(timeout: float, job_id: str | None = None, to: str | None = None,
               client_ip: str | None = None) -> tuple[str, bool]:
    """(text, is_answer). Waits up to timeout for a finished, unspoken answer.

    If job_id is given, matches that exact job.
    If to is given, matches jobs targeted to that agent/profile.
    If neither is given, uses client_ip's most recent active target if known.
    """
    deadline = time.monotonic() + timeout
    resolved_to = brain.resolve(to) if to else ""
    if not resolved_to and not job_id and client_ip:
        resolved_to = _client_target.get(client_ip, "")

    with _cv:
        while True:
            # 1. Look for matching finished, unconsumed jobs
            for job in _jobs.values():
                if not (job["done"] and not job["consumed"]):
                    continue
                if job_id and job["id"] != job_id:
                    continue
                if resolved_to and job.get("target") != resolved_to:
                    continue
                job["consumed"] = True
                return job["reply"], True

            # 2. Look for matching pending jobs
            matching_pending = []
            for j in _jobs.values():
                if j["done"]:
                    continue
                if job_id and j["id"] != job_id:
                    continue
                if resolved_to and j.get("target") != resolved_to:
                    continue
                matching_pending.append(j)

            if not matching_pending:
                return SILENT, False                 # nothing in flight for this filter: answer at once

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                job = matching_pending[0]
                job["polls"] += 1
                if job["polls"] % STATUS_EVERY_POLLS == 0:
                    status_text = _TOOL_STATUS.get(brain.last_tool_for(job.get("target")), "still working on it")
                    return status_text + ".", False
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
        parsed = urlparse(self.path)
        path = parsed.path
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
        query = parse_qs(parsed.query)
        req_to = query.get("to", [""])[0]
        req_job = query.get("job", [""])[0]
        client_ip = self.client_address[0]
        text, is_answer = take_reply(POLL_WAIT_SEC, job_id=req_job or None, to=req_to or None,
                                     client_ip=client_ip)
        sent = self._send(200, {"reply": text})
        if is_answer or text != SILENT:
            log(f"{client_ip} GET /reply {time.monotonic()-t0:.1f}s "
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
        client_ip = self.client_address[0]
        job = start_job(text, to, client_ip=client_ip)
        log(f"{client_ip} POST /ask job {job['id']} -> {brain.name(to or None)}: {text[:80]!r}")
        reply, is_answer = take_reply(ASK_WAIT_SEC, job_id=job["id"], client_ip=client_ip)
        sent = self._send(200, {"reply": reply, "pending": not is_answer, "job": job["id"]})
        log(f"{client_ip} POST /ask job {job['id']} {time.monotonic()-t0:.1f}s "
            f"{'answered' if is_answer else 'pending'}{'' if sent else ' (phone hung up)'}")

    def log_message(self, fmt, *args) -> None:  # quiet; we log our own lines
        pass


def main() -> None:
    srv = ThreadingHTTPServer((S.RECEIVER_HOST, S.RECEIVER_PORT), Handler)
    log(f"Voice Desk receiver on {S.RECEIVER_HOST}:{S.RECEIVER_PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
