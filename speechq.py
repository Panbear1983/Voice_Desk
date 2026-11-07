"""The train station (Peter 2026-10-22): one shared line for every voice on this Mac.

    "these response will line up one after another, make sure that all the responses are being
     all spoken out ... kind of like train coming arriving to the train station."

Every Hermes window speaks by running a bare `afplay <file>`, one file per sentence (Hermes
tools/voice_mode.py _run_system_player). install_speechq.sh puts a small stand-in named afplay
in ~/bin, which comes first on those windows' PATH, so each sentence comes through here first:

    python speechq.py afplay <file>

It takes a ticket and waits its turn, then plays the file with the real /usr/bin/afplay:
  - one voice at a time (play.lock), first come first served (the ticket line);
  - a reply plays whole: for FLOOR_SEC after each of its sentences the floor belongs to that
    agent, so its next sentence goes before anyone else;
  - a reply waits for a pause: the listener writes gate.json (closed while Peter is talking or a
    window is recording or transcribing); a gate older than GATE_STALE_SEC counts as open, so
    nothing hangs when the listener is not running; after PAUSE_WAIT_MAX_SEC it stops waiting;
  - a reply that is not from the agent Peter is talking to, or that follows another agent's
    voice, opens with that agent's name ("Kylie here."), spoken in her own voice;
  - Hermes' record beeps never wait for a pause or the line (Hermes blocks on them before it
    records), only for another agent's floor, so a window never starts recording mid-reply.
Voice Desk's own lines (listener.py) take the same lock. Touch ~/.voice_desk/speech/off to let
every afplay play straight through again; delete ~/bin/afplay to remove the stand-in entirely.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

LISTENER = "voice-desk"         # Voice Desk's own lines
OTHER = "other"                 # an afplay from anything that is not a Hermes window

FLOOR_SEC = 3.0                 # after a sentence the floor stays with its agent this long (its next
                                # sentence is usually made while this one plays)
GATE_STALE_SEC = 2.0            # the listener rewrites gate.json every 0.5 s; older = listener not running
GATE_SETTLE_SEC = 0.5           # the gate must have been open this long (the pause is real)
# Stop waiting for a pause or another agent's floor after this long. Hermes waits 120 s for a
# reply's speech after the text is done (cli_chat_turn_mixin.py _chat_settle_turn) and then drops
# the sentences not yet played, so a reply must be under way well before that.
PAUSE_WAIT_MAX_SEC = 60.0
# A window's record beep waits for another agent's floor as long as a reply does: Hermes opens
# that window's mic right after the beep, and it must not record the other agent's reply.
LISTENER_FLOOR_WAIT_SEC = 3.0   # Voice Desk's own short lines wait for another agent's floor at most this long
NAME_GAP_SEC = 30.0             # "Kylie here" if another agent spoke less than this long ago
POLL_SEC = 0.05
STALE_TICKET_SEC = 600.0
REAL_AFPLAY = os.environ.get("VOICE_DESK_REAL_AFPLAY", "/usr/bin/afplay")


def state_dir() -> Path:
    return Path(os.environ.get("VOICE_DESK_STATE", Path.home() / ".voice_desk"))


# -- the decisions (pure: tests drive them with a simulated clock) --------------------------------
def _fresh(gate: Optional[dict], now: float) -> bool:
    return bool(gate) and now - float(gate.get("at", 0)) < GATE_STALE_SEC


def decide(me: dict, line: list, floor: Optional[dict], gate: Optional[dict], last: Optional[dict],
           now: float, gated: bool = True) -> tuple[bool, str]:
    """May ticket `me` play now? (go, why). `line` is every waiting ticket, `floor` who holds the
    floor and until when, `gate` the listener's pause signal, `last` who spoke last and when."""
    waited = now - float(me["t"])
    beep = me.get("kind") == "beep"
    g = gate or {}
    fresh = _fresh(gate, now)
    if floor:
        until = float(floor.get("until", 0))
        if floor.get("owner") != me["owner"]:
            cap = PAUSE_WAIT_MAX_SEC if gated else LISTENER_FLOOR_WAIT_SEC
            if now < until and waited < cap:
                return False, f"floor: {floor.get('owner')}"
        elif now < until and not beep:
            return True, "cont"                # her own next sentence: ahead of the line, whatever the gate
    if beep:
        return True, "beep"                    # never waits for a pause or the line (Hermes blocks on it)
    if not gated:
        return True, "go"                      # Voice Desk answering Peter: it IS the pause
    key = (float(me["t"]), str(me["id"]))
    if any(t["id"] != me["id"] and t.get("kind") != "beep" and (float(t["t"]), str(t["id"])) < key for t in line):
        return False, "line"
    if not fresh:
        return True, "go"
    if waited >= PAUSE_WAIT_MAX_SEC:
        return True, "pause-wait-over"
    if last and float(g.get("at", 0)) <= float(last.get("end", 0)):
        return False, "gate-old"               # the listener has not looked since that voice ended
    if not g.get("open", True):
        return False, f"pause: {g.get('why') or 'busy'}"
    if now - float(g.get("open_since", 0)) < GATE_SETTLE_SEC:
        return False, "settle"
    return True, "go"


def wants_name(owner: str, gate: Optional[dict], last: Optional[dict], now: float, known: Iterable[str]) -> bool:
    """Should this reply open with "<name> here."? Only for a Hermes agent, and only when Peter is
    talking with another agent or the last voice heard was another agent's."""
    known = set(known)
    if owner not in known:
        return False
    current = (gate or {}).get("current") if _fresh(gate, now) else None
    if current and current != owner:
        return True
    return bool(last and last.get("owner") in known and last.get("owner") != owner
                and now - float(last.get("end", 0)) < NAME_GAP_SEC)


def is_beep(path: Path) -> bool:
    """Hermes' record beeps: tiny 16 kHz mono WAVs (0.12 s, or 0.30 s for the double beep)."""
    try:
        with wave.open(str(path), "rb") as w:
            return (w.getnchannels() == 1 and w.getframerate() == 16000
                    and w.getnframes() / w.getframerate() <= 0.35)
    except (OSError, EOFError, wave.Error):
        return False


def owner_from_env(env: dict, ppid_cmd: Optional[str] = None) -> str:
    """Which Hermes profile is speaking: its window's HERMES_HOME (.../profiles/<name>), else the
    parent's `hermes -p <name>` command line, else OTHER."""
    home = env.get("HERMES_HOME") or ""
    if home:
        p = Path(home)
        if p.parent.name == "profiles" and p.name:
            return p.name
    if ppid_cmd is None:
        ppid_cmd = _command_of(os.getppid())
    m = re.search(r"(?:^|\s)(?:-p|--profile)[=\s]+([A-Za-z0-9_.-]+)", ppid_cmd or "")
    return m.group(1) if m else OTHER


def canonical(owner: str, known: Iterable[str]) -> str:
    """The agents.toml profile for an owner whose '_' and '-' are swapped (wanna_buffet -> wanna-buffet)."""
    known = set(known)
    if owner in known:
        return owner
    for alt in (owner.replace("_", "-"), owner.replace("-", "_")):
        if alt in known:
            return alt
    return owner


def _command_of(pid: int) -> str:
    try:
        return subprocess.run(["/bin/ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


# -- agents.toml: who is a Hermes agent, what is their name and voice ------------------------------
def _hermes_agents() -> dict:
    try:
        import agents as A
        return {a.profile: a for a in A.agents(A.load()) if a.profile}
    except Exception:
        return {}


def known_profiles() -> set[str]:
    return set(_hermes_agents())


def name_clip_path(owner: str, root: Optional[Path] = None) -> Optional[Path]:
    a = _hermes_agents().get(owner)
    if a is None:
        return None
    voice = re.sub(r"[^A-Za-z0-9_.-]", "_", a.voice or "shared")
    return (root or state_dir() / "speech") / "names" / f"{owner}-{voice}.wav"


def make_name_clip(owner: str, root: Optional[Path] = None) -> Optional[Path]:
    """"Kylie here." in Kylie's own voice, made once and kept (the file name carries the voice,
    so a voice change in agents.toml makes a new one)."""
    path = name_clip_path(owner, root)
    if path is None or path.exists():
        return path if path and path.exists() else None
    if os.environ.get("VOICE_DESK_SPEECHQ_NAMES_ONLY_CACHED") == "1":
        return None
    try:
        import tts
        import voices
        a = _hermes_agents()[owner]
        made = tts.synthesize(f"{a.display} here.", voices.voice_for(a.target))
    except Exception:
        return None
    if not made:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        shutil.copyfile(made, tmp)
        os.replace(tmp, path)
    finally:
        shutil.rmtree(Path(made).parent, ignore_errors=True)
    return path


def warm_names(root: Optional[Path] = None) -> None:
    """Make every agent's name clip ahead of time, so the first "Kylie here." is instant."""
    for owner in known_profiles():
        make_name_clip(owner, root)


# -- the station: tickets, the lock and the small state files -------------------------------------
class Cut(Exception):
    """The player was told to stop (Hermes cut its TTS) while waiting in line."""


class _Straight(Exception):
    """Play straight through with the real afplay (kill switch on, or nothing to play)."""


@dataclass
class Turn:
    why: str
    waited: float
    name: bool
    keep_floor: bool = True


class Station:
    def __init__(self, root: Optional[Path] = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.root = Path(root) if root else state_dir() / "speech"
        self.clock, self.sleep = clock, sleep
        (self.root / "queue").mkdir(parents=True, exist_ok=True)

    # small JSON files, written atomically
    def _read(self, name: str) -> Optional[dict]:
        try:
            return json.loads((self.root / name).read_text())
        except (OSError, ValueError):
            return None

    def _write(self, name: str, data: dict) -> None:
        tmp = self.root / f".{name}.{os.getpid()}.{threading.get_ident()}"
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.root / name)

    def floor(self) -> Optional[dict]:
        return self._read("floor.json")

    def last(self) -> Optional[dict]:
        return self._read("last.json")

    def gate(self) -> Optional[dict]:
        return self._read("gate.json")

    def set_gate(self, open_: bool, why: str, current: Optional[str]) -> None:
        """The listener's pause signal. open_since survives rewrites while it stays open."""
        now = self.clock()
        prev = self.gate() or {}
        since = prev.get("open_since", now) if (open_ and prev.get("open")) else now
        self._write("gate.json", {"open": bool(open_), "why": why, "current": current,
                                  "at": now, "open_since": since})

    def line(self) -> list[dict]:
        out = []
        for f in (self.root / "queue").glob("*.json"):
            try:
                t = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if not _alive(int(t.get("pid", 0))) or self.clock() - float(t.get("t", 0)) > STALE_TICKET_SEC:
                f.unlink(missing_ok=True)
                continue
            t["_file"] = str(f)
            out.append(t)
        return sorted(out, key=lambda t: (float(t["t"]), str(t["id"])))

    def has(self, owner: str, kind: Optional[str] = None) -> bool:
        return any(t["owner"] == owner and (kind is None or t.get("kind") == kind) for t in self.line())

    def _ticket(self, owner: str, kind: str) -> dict:
        t = self.clock()
        tid = uuid.uuid4().hex[:8]
        ticket = {"id": f"{t:.6f}-{tid}", "owner": owner, "kind": kind, "t": t, "pid": os.getpid()}
        f = self.root / "queue" / f"{t:.6f}-{os.getpid()}-{tid}.json"
        f.write_text(json.dumps(ticket))
        ticket["_file"] = str(f)
        return ticket

    def _drop(self, ticket: dict) -> None:
        Path(ticket["_file"]).unlink(missing_ok=True)

    @contextlib.contextmanager
    def turn(self, owner: str, kind: str = "speech", gated: bool = True, known: Optional[Iterable[str]] = None,
             abort: Callable[[], bool] = lambda: False) -> Iterator[Turn]:
        """Wait for this owner's turn, hold the speaker while the body plays, then hand it on."""
        known = set(known) if known is not None else known_profiles()
        ticket = self._ticket(owner, kind)
        lock = open(self.root / "play.lock", "w")
        locked = False
        try:
            t0 = self.clock()
            while True:
                if abort():
                    raise Cut()
                go, why = decide(ticket, self.line(), self.floor(), self.gate(), self.last(), self.clock(), gated)
                if go:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                        break
                    except OSError:
                        pass
                self.sleep(POLL_SEC)
            name = kind == "speech" and why != "cont" and wants_name(owner, self.gate(), self.last(), self.clock(), known)
            turn = Turn(why=why, waited=self.clock() - t0, name=name)
            yield turn
            # only an agent's reply holds the floor; Voice Desk's own lines and unknown programs do not
            if kind == "speech" and turn.keep_floor and owner in known:
                end = self.clock()
                self._write("floor.json", {"owner": owner, "until": end + FLOOR_SEC})
                self._write("last.json", {"owner": owner, "end": end})
        finally:
            if locked:
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
            self._drop(ticket)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# -- the afplay stand-in ----------------------------------------------------------------------------
def afplay_main(args: list[str]) -> int:
    """Play like afplay, but in turn. Any trouble before the queue is set up -> plain afplay, so a
    bug here can never make a window go silent."""
    real = [REAL_AFPLAY, *args]
    try:
        root = state_dir() / "speech"
        if not args or (root / "off").exists():
            raise _Straight()
        path = Path(args[-1])
        known = known_profiles()
        owner = canonical(owner_from_env(dict(os.environ)), known)
        kind = "beep" if is_beep(path) else "speech"
        station = Station(root)
    except Exception:
        try:
            os.execv(REAL_AFPLAY, real)
        except OSError:
            return 1

    cut = threading.Event()
    child: dict = {}

    def on_term(*_a) -> None:
        cut.set()
        p = child.get("p")
        if p is not None and p.poll() is None:
            p.terminate()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    clip: dict = {}
    if kind == "speech" and owner in known:
        maker = threading.Thread(target=lambda: clip.update(path=make_name_clip(owner, root)), daemon=True)
        maker.start()
    else:
        maker = None

    def play(argv: list[str]) -> int:
        if cut.is_set():
            return 0
        child["p"] = subprocess.Popen([REAL_AFPLAY, *argv], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = child["p"].wait()
        return 0 if cut.is_set() else rc

    rc = 0
    try:
        with station.turn(owner, kind, gated=owner != OTHER, known=known, abort=cut.is_set) as t:
            if t.name and maker is not None:
                maker.join(timeout=3.0)
                if clip.get("path"):
                    play([str(clip["path"])])
            rc = play(args)
            if cut.is_set():
                t.keep_floor = False            # cut short (Peter pressed to talk): nobody holds the floor
    except Cut:
        return 0
    return rc


def main(argv: list[str]) -> int:
    if argv[:1] == ["afplay"]:
        return afplay_main(argv[1:])
    if argv[:1] == ["status"]:
        st = Station()
        print(json.dumps({"line": [{k: v for k, v in t.items() if k != "_file"} for t in st.line()],
                          "floor": st.floor(), "last": st.last(), "gate": st.gate()}, indent=1))
        return 0
    if argv[:1] == ["warm"]:
        warm_names()
        return 0
    print("usage: speechq.py afplay <file> | status | warm", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main(sys.argv[1:]))
