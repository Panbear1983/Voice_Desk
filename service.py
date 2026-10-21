"""The always-on listener's shared plumbing: the status file it writes, the mute flag, the
one-line orders the dashboard leaves for it, and the launchd controls.

The listener is the only writer of the status file; the dashboard only reads it. Nothing in
here touches the microphone, so the dashboard can import it freely.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import settings as S

STATES = ("starting", "asleep", "listening", "hearing", "thinking", "speaking", "handed", "muted", "standby", "error", "off")
HISTORY = 40          # state changes kept in the file (the live test reads the order from here)
STALE_SEC = 10        # no heartbeat for this long -> the listener is not running


# -- status file ------------------------------------------------------------------------------
def read_status(path: Path = S.LISTENER_STATUS) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(data: dict, path: Path = S.LISTENER_STATUS) -> None:
    """Atomic: the dashboard never sees half a file."""
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def is_fresh(data: dict, now: Optional[float] = None) -> bool:
    return bool(data) and (now or time.time()) - float(data.get("updated") or 0) < STALE_SEC


class Status:
    """The listener's view of itself, mirrored to the status file on every change."""

    def __init__(self, path: Path = S.LISTENER_STATUS) -> None:
        self.path = path
        self._lock = threading.Lock()
        now = time.time()
        self.data: dict = {"pid": os.getpid(), "state": "starting", "since": now, "updated": now,
                           "target": "", "name": "", "heard": "", "reply": "", "note": "",
                           "muted": is_muted(), "level": 0, "turns": 0, "history": [[now, "starting"]]}
        self.flush()

    def set_state(self, state: str) -> None:
        with self._lock:
            if self.data["state"] == state:
                return
            now = time.time()
            self.data.update(state=state, since=now)
            self.data["history"] = (self.data["history"] + [[now, state]])[-HISTORY:]
            self._flush()

    def update(self, **fields) -> None:
        with self._lock:
            self.data.update(fields)
            self._flush()

    @property
    def state(self) -> str:
        return self.data["state"]

    def flush(self) -> None:
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        self.data["updated"] = time.time()
        try:
            write_status(self.data, self.path)
        except OSError:
            pass


# -- mute -------------------------------------------------------------------------------------
def is_muted() -> bool:
    return S.MUTE_FILE.exists()


def set_muted(on: bool) -> None:
    if on:
        S.MUTE_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    else:
        try:
            S.MUTE_FILE.unlink()
        except OSError:
            pass


# -- orders from the dashboard ----------------------------------------------------------------
ORDERS = {"hush", "new_thread", "sleep"}


def send(order: str) -> None:
    with open(S.LISTENER_INBOX, "a", encoding="utf-8") as fh:
        fh.write(order + "\n")


def take_orders() -> list[str]:
    taking = S.LISTENER_INBOX.with_name(S.LISTENER_INBOX.name + ".taking")
    try:
        os.replace(S.LISTENER_INBOX, taking)
    except OSError:
        return []
    try:
        return [ln.strip() for ln in taking.read_text(encoding="utf-8").splitlines() if ln.strip() in ORDERS]
    finally:
        taking.unlink(missing_ok=True)


# -- launchd ----------------------------------------------------------------------------------
PLIST = Path.home() / "Library/LaunchAgents" / f"{S.LISTENER_LABEL}.plist"
DOMAIN = f"gui/{os.getuid()}"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=20)


def service_pid() -> Optional[int]:
    """The listener's pid if launchd has it loaded and running, else None."""
    r = _launchctl("list", S.LISTENER_LABEL)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if '"PID"' in line:
            try:
                return int(line.split("=")[1].strip().rstrip(";"))
            except (IndexError, ValueError):
                return None
    return None


def service_loaded() -> bool:
    return _launchctl("list", S.LISTENER_LABEL).returncode == 0


def service_restart() -> str:
    if service_loaded():
        r = _launchctl("kickstart", "-k", f"{DOMAIN}/{S.LISTENER_LABEL}")
        return "restarted" if r.returncode == 0 else (r.stderr.strip() or "restart failed")
    if not PLIST.exists():
        return "not installed: run ./install_listener.sh"
    r = _launchctl("bootstrap", DOMAIN, str(PLIST))
    return "started" if r.returncode == 0 else (r.stderr.strip() or "start failed")


def service_stop() -> str:
    """Unload: the listener stays off (and the mic free) until restarted or the next login."""
    if not service_loaded():
        return "already off"
    r = _launchctl("bootout", f"{DOMAIN}/{S.LISTENER_LABEL}")
    return "stopped" if r.returncode == 0 else (r.stderr.strip() or "stop failed")
