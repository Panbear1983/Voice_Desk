"""Phone door: a tiny web receiver the iPhone Shortcut talks to.

Siri gives up on any single request that takes much longer than ten seconds, and a
Claude turn can take 20 s or more, so the phone never waits for a whole turn:

POST /ask   {"text": "...", "to": "esther"}  -> {"reply": "...", "pending": bool}
                                the answer if it was ready within ASK_WAIT_SEC, else ".".
                                "to" (optional) picks "claude", "gemini", "codex" or a Hermes
                                profile by name; each phone Shortcut is pinned to one.
                                "talk to Jarvis" / "switch to Jarvis" / "hello Jarvis" / a bare
                                "Jarvis" (brain.addressed, the Mac window's matcher and agents.toml
                                names) switches THIS device to that agent and answers who is listening
                                now; "Jarvis, <question>" switches and asks. Until the phone ends, switches
                                back or is idle SWITCH_KEEP_SEC, its asks and check-backs go there.
                                A goodbye phrase (settings.GOODBYE_WORDS, the Mac window's own
                                list, matched by commands.classify) files the memory note and
                                answers "Goodbye." with "end": true; a sleep or stop phrase
                                answers "Okay." with "end": true. The Shortcut stops on "end".
GET  /reply                  -> {"reply": "..."}       ("end": true when it is the goodbye)
                                the next finished answer (waits up to POLL_WAIT_SEC for one);
                                "." when there is nothing to say; one short "still working"
                                line per question while it is busy (settings.STATUS_REPEAT
                                = True brings back one every few polls)
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

import commands
import settings as S
import tts
from brain import _TOOL_STATUS, Brain, addressed, switch_request

ASK_WAIT_SEC = 8            # how long /ask may hold the phone before answering "not yet"
POLL_WAIT_SEC = 8           # same for /reply
STATUS_EVERY_POLLS = 3      # a spoken "still working" roughly every 24 s of waiting
SILENT = "."                # Speak Text says nothing for this, and never errors on it
KEEP_SEC = 600              # finished jobs are forgotten after this
STALE_SEC = 300             # an answer nobody collected within 5 min is not read out later to a new question

# Peter 2026-11-03: "talk to Jarvis" said into the Kylie Shortcut must put Jarvis on the line, like the
# Mac window does. A Shortcut sends its pinned "to" with every request, so the switch is remembered per
# device and swaps that pinned "to" for the new agent on /ask AND /reply (check-backs carry only "to").
# It lasts until the phone switches back, ends (goodbye / sleep / stop), or is silent this long, so a
# fresh "Talk to Kylie" later talks to Kylie again.
SWITCH_KEEP_SEC = 300

brain = Brain()
_jobs: dict[str, dict] = {}                 # insertion order = oldest first
_client_target: dict[str, str] = {}         # client_ip -> resolved target of its latest /ask
_client_switch: dict[str, dict] = {}        # client_ip -> {"pinned": target, "target": target, "seen": t}
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


# -- "talk to Jarvis" from the phone ----------------------------------------------------------
def _pinned(to: str) -> str:
    """The thread a Shortcut is pinned to, as a target id ("" for the old unpinned Shortcuts)."""
    return (brain.resolve(to) or "") if to else ""


def effective_to(client_ip: str, to: str) -> str:
    """Where this device's words go now: the agent it switched to by voice, else the Shortcut's own "to".
    A switch made from another Shortcut, or one left idle for SWITCH_KEEP_SEC, no longer counts."""
    with _cv:
        sw = _client_switch.get(client_ip)
        if not sw:
            return to
        if sw["pinned"] != _pinned(to) or time.monotonic() - sw["seen"] > SWITCH_KEEP_SEC:
            del _client_switch[client_ip]
            return to
        sw["seen"] = time.monotonic()
        return sw["target"]


def forget_switch(client_ip: str) -> None:
    with _cv:
        _client_switch.pop(client_ip, None)


def phone_switch(text: str, client_ip: str, to: str) -> tuple[str, str, str] | None:
    """The Mac window's name-at-the-start rule (brain.addressed, the names in agents.toml), plus the
    Mac's "talk to X" command for the forms addressed() leaves out ("okay, talk to Jarvis").

    None = not a switch, an ordinary question. Otherwise (target, rest, reply): target "" means the
    name is unknown and reply says so; rest is the question that came with the name ("Jarvis, what
    time is it"), asked of the new agent; reply is what to say when there is no question."""
    hit = addressed(text)                    # "Jarvis", "hello Jarvis, ...", "talk to Jarvis", "switch to Jarvis"
    if hit is None:
        wanted = switch_request(text)
        if not wanted:
            return None
        target = brain.resolve(wanted)
        if not target:
            return "", "", f"I do not know anyone called {wanted}. " + (brain.command("who can I talk to") or "")
        hit = (target, "")
    target, rest = hit
    pinned = _pinned(to) or brain.target()
    with _cv:
        was = _client_switch.get(client_ip)
        if target == pinned:
            _client_switch.pop(client_ip, None)
        else:
            _client_switch[client_ip] = {"pinned": _pinned(to), "target": target, "seen": time.monotonic()}
    who = brain.name(target)
    if was and target == pinned:
        reply = f"Okay, back to {who}."
    elif switch_request(text):               # "talk to Jarvis": the Mac's talk-to answer
        reply = f"Okay, you are talking to {who} now."
    else:                                    # "Jarvis" / "hello Jarvis": the Mac window's answer to a name
        reply = f"{who} here."
    return target, rest, reply


# -- jobs ---------------------------------------------------------------------------------
def start_job(text: str, to: str = "", client_ip: str = "", end: bool = False, prefix: str = "") -> dict:
    """end=True: a goodbye. The brain files its memory note, the phone hears "Goodbye." and stops.
    prefix: said before the answer ("Jarvis here. "), when the question came with a switch."""
    resolved_target = brain.resolve(to) if to else brain.target()
    job = {"id": uuid.uuid4().hex[:8], "text": text, "to": to, "target": resolved_target,
           "client_ip": client_ip, "reply": "", "done": False, "end": end, "prefix": prefix,
           "consumed": False, "started": time.monotonic(), "polls": 0}
    with _cv:
        _prune()
        _jobs[job["id"]] = job
        if client_ip:
            _client_target[client_ip] = resolved_target
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"job-{job['id']}").start()
    return job


def _run(job: dict) -> None:
    if job.get("end"):                             # same as the Mac window's goodbye: note first, then "Goodbye."
        try:
            brain.ask(commands.GOODBYE_PROMPT, to=job["to"] or None)
        except Exception as exc:
            log(f"job {job['id']} goodbye note failed: {exc}")
        reply = commands.GOODBYE_REPLY
    else:
        try:
            reply = brain.ask(job["text"], to=job["to"] or None)
        except Exception as exc:                       # the phone should hear it, not silence
            reply = f"I hit an error: {exc}"
    with _cv:
        job["reply"] = job.get("prefix", "") + (tts.speakable(reply) or "I did not get an answer back.")
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
    text, is_answer, _ = take_reply_job(timeout, job_id, to, client_ip)
    return text, is_answer


def take_reply_job(timeout: float, job_id: str | None = None, to: str | None = None,
                   client_ip: str | None = None) -> tuple[str, bool, dict | None]:
    """take_reply, plus the job whose answer was handed out (None for a status line or silence)."""
    deadline = time.monotonic() + timeout
    resolved_to = brain.resolve(to) if to else ""
    # Shortcuts made before 2026-10-25 check back with no job and no target: then the asking device
    # decides (its latest target, and only its own jobs), so iPhone and iPad never take each other's answer.
    by_device = bool(client_ip) and not job_id and not to
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
                if by_device and job.get("client_ip") and job["client_ip"] != client_ip:
                    continue
                if by_device and time.monotonic() - job["started"] - job.get("took", 0) > STALE_SEC:
                    job["consumed"] = True                # walked away from long ago: drop, do not read it out now
                    continue
                job["consumed"] = True
                return job["reply"], True, job

            # 2. Look for matching pending jobs
            matching_pending = []
            for j in _jobs.values():
                if j["done"]:
                    continue
                if job_id and j["id"] != job_id:
                    continue
                if resolved_to and j.get("target") != resolved_to:
                    continue
                if by_device and j.get("client_ip") and j["client_ip"] != client_ip:
                    continue
                matching_pending.append(j)

            if not matching_pending:
                return SILENT, False, None           # nothing in flight for this filter: answer at once

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                job = matching_pending[0]
                job["polls"] += 1
                if job["polls"] % STATUS_EVERY_POLLS == 0 and (S.STATUS_REPEAT or not job.get("status_said")):
                    job["status_said"] = True             # STATUS_REPEAT off: once per question, then "." until the answer
                    if not S.STATUS_REPEAT:
                        return S.STATUS_ONCE_LINE + ".", False, None
                    status_text = _TOOL_STATUS.get(brain.last_tool_for(job.get("target")), "still working on it")
                    return status_text + ".", False, None
                return SILENT, False, None
            _cv.wait(remaining)


# -- http ---------------------------------------------------------------------------------
def _payload(body: dict, job: dict | None) -> dict:
    """Adds "end": true when the answer handed out is the goodbye. The key is left out otherwise,
    so the Shortcut's If can simply ask whether "end" has any value."""
    if job and job.get("end"):
        body["end"] = True
    return body


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
        client_ip = self.client_address[0]
        req_to = effective_to(client_ip, query.get("to", [""])[0])     # after "talk to Jarvis": Jarvis's answers
        req_job = query.get("job", [""])[0]
        text, is_answer, job = take_reply_job(POLL_WAIT_SEC, job_id=req_job or None, to=req_to or None,
                                              client_ip=client_ip)
        if job and job.get("end"):
            forget_switch(client_ip)               # a slow goodbye from the switched-to agent, now heard
        sent = self._send(200, _payload({"reply": text}, job))
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
        command = commands.classify(text)          # the Mac window's own phrase list (settings.py)
        prefix = ""
        before = _pinned(effective_to(client_ip, to)) or brain.target()     # who had the line until now
        switch = None if command else phone_switch(text, client_ip, to)
        if switch:                                 # same order as the Mac window: the name first, then the rest
            target, rest, said = switch
            if not target or not rest:             # "talk to Jarvis" / a bare "Jarvis" / an unknown name
                self._send(200, {"reply": said, "pending": False})
                log(f"{client_ip} POST /ask switch -> {brain.name(target) if target else 'unknown'}: {text[:80]!r}")
                return
            text = rest                            # "Jarvis, what time is it": Jarvis is asked "what time is it"
            command = commands.classify(rest)      # "Jarvis, goodbye" is Jarvis's goodbye
            if command is None and target != before:
                prefix = f"{brain.name(target)} here. "     # Siri reads every agent in one voice: say who answers
            log(f"{client_ip} POST /ask switch -> {brain.name(target)} with more words")
        if command in ("sleep", "stop"):           # the Shortcut has no idle state: these end it too
            forget_switch(client_ip)               # next time this Shortcut talks to its own agent again
            self._send(200, {"reply": "Okay.", "pending": False, "end": True})
            log(f"{client_ip} POST /ask {command} -> end: {text[:80]!r}")
            return
        ask_to = effective_to(client_ip, to)
        job = start_job(text, ask_to, client_ip=client_ip, end=command == "goodbye", prefix=prefix)
        log(f"{client_ip} POST /ask job {job['id']} -> {brain.name(ask_to or None)}"
            f"{' (goodbye)' if job['end'] else ''}: {text[:80]!r}")
        reply, is_answer, done = take_reply_job(ASK_WAIT_SEC, job_id=job["id"], client_ip=client_ip)
        if done and done.get("end"):
            forget_switch(client_ip)               # the goodbye was heard: next time, the Shortcut's own agent
        sent = self._send(200, _payload({"reply": reply, "pending": not is_answer, "job": job["id"]}, done))
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
