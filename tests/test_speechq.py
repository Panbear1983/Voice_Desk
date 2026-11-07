"""Offline tests for the shared speech queue ("train station", Peter 2026-10-22): no speakers, no mic.

  .venv/bin/python tests/test_speechq.py

1 decide(): first come first served; one agent's reply plays whole (the floor lets its next
  sentence in before anyone else); a reply waits for a pause (gate closed while Peter talks or a
  window records), beeps never wait for a pause, a stale gate counts as open, a gate written
  before the last reply ended is not trusted, and nothing waits forever
2 wants_name(): "Kylie here" when the reply is not from the agent Peter is talking to, or when the
  last thing spoken was another agent; never for Voice Desk's own lines or unknown programs
3 Station: tickets, the lock and the floor/last files on a simulated clock
4 the afplay stand-in, run for real as two processes with a fake player: they never overlap and
  play in the order they arrived; a waiting reply opens with the agent's name; SIGTERM cuts it
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VOICE_DESK_STATE"] = tempfile.mkdtemp(prefix="vd-sq-test-")

import tests  # noqa: E402,F401  (the fake no-window herdr, also when this file is run directly)
import speechq as Q  # noqa: E402

ok = True


def report(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok &= bool(passed)
    print(("PASS " if passed else "FAIL ") + name + ("" if passed or not detail else f"  [{detail}]"))


def tk(owner: str, t: float, kind: str = "speech", n: int = 0) -> dict:
    return {"id": f"{t:.3f}-{n}", "owner": owner, "kind": kind, "t": t, "pid": os.getpid()}


# 1 ----------------------------------------------------------------------------------------------
def test_decide() -> None:
    now = 1000.0
    kylie, jarvis = tk("assistant", 990.0), tk("orchestrator", 995.0)
    line = [kylie, jarvis]
    open_gate = {"open": True, "why": "", "current": "orchestrator", "at": now - 0.1}
    report("order: the first reply in line goes first", Q.decide(kylie, line, None, open_gate, None, now)[0])
    go, why = Q.decide(jarvis, line, None, open_gate, None, now)
    report("order: the second waits its turn", not go and why == "line", why)
    report("order: once the first is done, the second goes", Q.decide(jarvis, [jarvis], None, open_gate, None, now)[0])

    # Kylie's reply is several sentences; Jarvis's reply arrived between two of them
    floor = {"owner": "assistant", "until": now + 2.0}
    kylie2 = tk("assistant", 998.0, n=2)
    line = [jarvis, kylie2]
    go, why = Q.decide(kylie2, line, floor, open_gate, None, now)
    report("floor: Kylie's next sentence goes before Jarvis's waiting reply (her reply plays whole)", go and why == "cont", why)
    go, why = Q.decide(jarvis, line, floor, open_gate, None, now)
    report("floor: ... and Jarvis waits while she holds the floor", not go and why.startswith("floor"), why)
    report("floor: ... a beep waits too (no record beep in the middle of her reply)",
           not Q.decide(tk("orchestrator", 999.0, "beep"), line, floor, open_gate, None, now)[0])
    report("floor: ... even after 10 s (that beep opens Jarvis's mic, which must not record Kylie)",
           not Q.decide(tk("orchestrator", now - 10, "beep"), line, {"owner": "assistant", "until": now + 1}, open_gate, None, now)[0])
    report("floor: Voice Desk's own line waits for her floor at most LISTENER_FLOOR_WAIT_SEC",
           not Q.decide(tk(Q.LISTENER, now - 1), [], floor, open_gate, None, now, gated=False)[0]
           and Q.decide(tk(Q.LISTENER, now - Q.LISTENER_FLOOR_WAIT_SEC - 0.1), [], floor, open_gate, None, now, gated=False)[0])
    busy_floor = {"owner": "assistant", "until": now - 0.01}
    report("floor: not held past FLOOR_SEC just because her window is still working (a follow-up question to her)",
           Q.decide(jarvis, [jarvis], busy_floor, dict(open_gate, busy=["assistant"]), None, now)[0])
    report("floor: lapses after FLOOR_SEC", Q.decide(jarvis, [jarvis], {"owner": "assistant", "until": now - 0.01},
                                                    open_gate, None, now)[0])
    closed = {"open": False, "why": "Peter is talking", "current": "orchestrator", "at": now - 0.1}
    report("floor: her continuation plays even while Peter starts talking (a reply plays whole)",
           Q.decide(kylie2, [kylie2], floor, closed, None, now)[0])

    # pause gating
    go, why = Q.decide(jarvis, [jarvis], None, closed, None, now)
    report("pause: a reply waits while the gate is closed (Peter talking / a window recording)",
           not go and why == "pause: Peter is talking", why)
    report("pause: a beep does not wait for a pause", Q.decide(tk("orchestrator", 995.0, "beep"), [], None, closed, None, now)[0])
    report("pause: Voice Desk's own lines (gated=False) do not wait for a pause",
           Q.decide(tk(Q.LISTENER, 995.0), [], None, closed, None, now, gated=False)[0])
    stale = dict(closed, at=now - Q.GATE_STALE_SEC - 0.1)
    report("pause: a stale gate (listener not running) counts as open", Q.decide(jarvis, [jarvis], None, stale, None, now)[0])
    report("pause: no gate file at all counts as open", Q.decide(jarvis, [jarvis], None, None, None, now)[0])
    last = {"owner": "assistant", "end": now - 0.05}
    go, why = Q.decide(jarvis, [jarvis], None, dict(open_gate, at=now - 0.1), last, now)
    report("pause: a gate written before the last reply ended is not trusted yet", not go and why == "gate-old", why)
    report("pause: ... a fresh one after it is", Q.decide(jarvis, [jarvis], None, dict(open_gate, at=now), last, now)[0])
    old = tk("orchestrator", now - Q.PAUSE_WAIT_MAX_SEC - 1)
    go, why = Q.decide(old, [old], None, closed, None, now)
    report("pause: after PAUSE_WAIT_MAX_SEC a reply stops waiting for a pause (never lost)", go and why == "pause-wait-over", why)


# 2 ----------------------------------------------------------------------------------------------
def test_wants_name() -> None:
    now = 1000.0
    known = {"assistant", "orchestrator"}
    g = {"open": True, "why": "", "current": "orchestrator", "at": now}
    report("name: Kylie's reply while Peter talks with Jarvis opens with 'Kylie here'",
           Q.wants_name("assistant", g, None, now, known))
    report("name: Jarvis's reply while Peter talks with Jarvis does not", not Q.wants_name("orchestrator", g, None, now, known))
    report("name: ... unless the last voice was another agent's (back-to-back trains)",
           Q.wants_name("orchestrator", g, {"owner": "assistant", "end": now - 5}, now, known))
    report("name: ... not if that was long ago", not Q.wants_name("orchestrator", g, {"owner": "assistant", "end": now - Q.NAME_GAP_SEC - 1}, now, known))
    report("name: an agent speaking after itself needs no name",
           not Q.wants_name("orchestrator", g, {"owner": "orchestrator", "end": now - 1}, now, known))
    report("name: never for Voice Desk's own lines", not Q.wants_name(Q.LISTENER, g, None, now, known))
    report("name: never for an unknown program", not Q.wants_name("other", g, None, now, known))
    stale = dict(g, at=now - Q.GATE_STALE_SEC - 1)
    report("name: a stale gate says nothing about who Peter is talking to",
           not Q.wants_name("assistant", stale, None, now, known))


# 3 ----------------------------------------------------------------------------------------------
class SimClock:
    def __init__(self) -> None:
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_station() -> None:
    root = Path(tempfile.mkdtemp(prefix="vd-sq-"))
    clk = SimClock()
    st = Q.Station(root=root, clock=clk, sleep=clk.sleep)
    with st.turn("assistant") as t:
        report("station: an empty line plays at once", t.waited == 0 and not t.name)
        report("station: while playing, its ticket is in line", [x["owner"] for x in st.line()] == ["assistant"])
    report("station: done -> ticket gone", st.line() == [])
    report("station: done -> the floor is hers for FLOOR_SEC",
           st.floor()["owner"] == "assistant" and abs(st.floor()["until"] - (clk.t + Q.FLOOR_SEC)) < 1e-6)
    report("station: done -> last speaker recorded", st.last()["owner"] == "assistant")
    with st.turn("assistant", kind="beep"):
        pass
    report("station: a beep does not take the floor or count as last speaker",
           st.last()["owner"] == "assistant" and st.floor()["owner"] == "assistant")
    with st.turn(Q.LISTENER, gated=False, known={"assistant", "orchestrator"}):
        pass
    report("station: Voice Desk's own line does not take the floor (a window's record beep must not wait on it)",
           st.floor()["owner"] == "assistant" and st.last()["owner"] == "assistant", str(st.floor()))
    clk.t += Q.FLOOR_SEC + 0.1
    with st.turn("assistant", known={"assistant", "orchestrator"}):
        pass
    # Jarvis arrives inside Kylie's floor: he waits for it to lapse (simulated time moves on while he waits)
    t0 = clk.t
    st.set_gate(True, "", "orchestrator")
    with st.turn("orchestrator") as t:
        report("station: inside another agent's floor -> waits until it lapses",
               clk.t - t0 >= Q.FLOOR_SEC - 0.2 and t.waited > 0, f"{clk.t - t0:.2f}")
    # the gate
    st.set_gate(False, "Peter is talking", "orchestrator")
    report("station: gate round trip", st.gate()["open"] is False and st.gate()["current"] == "orchestrator")
    report("station: has() sees a waiting beep of one owner",
           not st.has("orchestrator", "beep"))
    ticket = st._ticket("orchestrator", "beep")
    report("station: ... and finds it once it is in line", st.has("orchestrator", "beep") and not st.has("assistant"))
    st._drop(ticket)
    # a ticket left behind by a process that died is ignored and cleaned up
    dead = root / "queue" / "1.000000-999999-x.json"
    dead.write_text(json.dumps({"id": "x", "owner": "assistant", "kind": "speech", "t": 1.0, "pid": 999_999}))
    report("station: a dead process's ticket is dropped", st.line() == [] and not dead.exists())


def write_wav(path: Path, secs: float, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * secs))
    return path


def test_is_beep() -> None:
    d = Path(tempfile.mkdtemp(prefix="vd-sq-wav-"))
    report("beep: Hermes' record beep (16 kHz mono, 0.12 s) is a beep", Q.is_beep(write_wav(d / "tmpabc.wav", 0.12)))
    report("beep: its double stop beep (0.30 s) is a beep", Q.is_beep(write_wav(d / "tmpdef.wav", 0.30)))
    report("beep: a 2 s spoken sentence is not", not Q.is_beep(write_wav(d / "say.wav", 2.0)))
    report("beep: a 0.4 s word at 24 kHz is not", not Q.is_beep(write_wav(d / "ok.wav", 0.4, rate=24000)))
    (d / "r.mp3").write_bytes(b"ID3\x00")
    report("beep: an mp3 is never a beep", not Q.is_beep(d / "r.mp3"))


def test_owner() -> None:
    report("owner: from the window's HERMES_HOME", Q.owner_from_env({"HERMES_HOME": "/home/someone/.hermes/profiles/assistant"}) == "assistant")
    report("owner: unknown otherwise", Q.owner_from_env({}, ppid_cmd="") == "other")
    report("owner: from the parent's 'hermes -p <profile>' when HERMES_HOME is missing",
           Q.owner_from_env({}, ppid_cmd="/x/venv/bin/hermes -p wanna-buffet -c") == "wanna-buffet")
    report("owner: '_' and '-' swapped (a window started as 'hermes -p wanna_buffet') is the same agent",
           Q.canonical("wanna_buffet", {"wanna-buffet", "assistant"}) == "wanna-buffet"
           and Q.canonical("assistant", {"wanna-buffet", "assistant"}) == "assistant"
           and Q.canonical("stranger", {"assistant"}) == "stranger")


# 4 ----------------------------------------------------------------------------------------------
def test_standin_processes() -> None:
    """Two real stand-in processes against one state dir; the 'player' is a script that logs
    start/end times, so overlap and order are read from what actually ran."""
    state = Path(tempfile.mkdtemp(prefix="vd-sq-proc-"))
    log = state / "played.log"
    player = state / "fakeplay.sh"
    player.write_text(f"#!/bin/sh\necho \"start $1 $(python3 -c 'import time;print(time.time())')\" >> {log}\n"
                      f"sleep 0.6\necho \"end $1 $(python3 -c 'import time;print(time.time())')\" >> {log}\n")
    player.chmod(0o755)
    clip = Q.name_clip_path("assistant", state / "speech")
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"x")                                   # "Kylie here." already made
    env = dict(os.environ, VOICE_DESK_STATE=str(state), VOICE_DESK_REAL_AFPLAY=str(player),
               VOICE_DESK_SPEECHQ_NAMES_ONLY_CACHED="1")
    d = Path(tempfile.mkdtemp(prefix="vd-sq-r-"))
    a, b = d / "jarvis.mp3", d / "kylie.mp3"
    a.write_bytes(b"ID3"), b.write_bytes(b"ID3")
    Q.Station(root=state / "speech").set_gate(True, "", "orchestrator")
    pa = subprocess.Popen([sys.executable, str(ROOT / "speechq.py"), "afplay", str(a)],
                          env=dict(env, HERMES_HOME="/p/profiles/orchestrator"))
    time.sleep(0.2)
    pb = subprocess.Popen([sys.executable, str(ROOT / "speechq.py"), "afplay", str(b)],
                          env=dict(env, HERMES_HOME="/p/profiles/assistant"))
    ra, rb = pa.wait(timeout=20), pb.wait(timeout=20)
    rows = [r.split() for r in log.read_text().splitlines()]
    order = [(k, Path(f).name) for k, f, _ in rows]
    report("stand-in: both exit 0 (Hermes then treats playback as done)", ra == 0 and rb == 0, f"{ra} {rb}")
    report("stand-in: played in arrival order, whole, never overlapping; Kylie's opens with her name",
           order == [("start", "jarvis.mp3"), ("end", "jarvis.mp3"), ("start", clip.name), ("end", clip.name),
                     ("start", "kylie.mp3"), ("end", "kylie.mp3")], str(order))
    times = [float(t) for _, _, t in rows]
    report("stand-in: ... and Kylie waited for Jarvis's floor before starting",
           len(times) == 6 and times[2] - times[1] >= Q.FLOOR_SEC - 0.3, f"{[round(x - times[0], 2) for x in times]}")

    log.unlink()
    time.sleep(Q.FLOOR_SEC)                                  # Kylie's floor lapses
    p = subprocess.Popen([sys.executable, str(ROOT / "speechq.py"), "afplay", str(b)],
                         env=dict(env, HERMES_HOME="/p/profiles/assistant"))
    deadline = time.time() + 5
    while not (log.exists() and "start" in log.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    p.terminate()                                            # what Hermes' stop_playback() does
    rc = p.wait(timeout=5)
    time.sleep(0.8)
    rows = log.read_text().splitlines() if log.exists() else []
    report("stand-in: SIGTERM cuts the playing reply (exit 0, the player never reaches its end)",
           rc == 0 and len([r for r in rows if r.startswith("start")]) == 1 and not any(r.startswith("end") for r in rows),
           f"{rc} {rows}")
    report("stand-in: ... and its ticket is gone", Q.Station(root=state / "speech").line() == [])

    off = state / "speech" / "off"
    off.write_text("")
    log.unlink(missing_ok=True)
    r = subprocess.run([sys.executable, str(ROOT / "speechq.py"), "afplay", str(a)], env=env, timeout=10)
    report("stand-in: with speech/off present it plays straight through (the kill switch)",
           r.returncode == 0 and log.exists() and not (state / "speech" / "floor.json").stat().st_mtime > time.time() - 0.5)


# 5 ----------------------------------------------------------------------------------------------
def test_installer() -> None:
    """install_speechq.sh against a throwaway HOME: it puts ~/bin/afplay in place, that stand-in
    really goes through the line, it never overwrites someone else's afplay, and --remove takes
    away only its own."""
    home = Path(tempfile.mkdtemp(prefix="vd-sq-home-"))
    state = home / ".voice_desk"
    log = home / "played.log"
    player = home / "fakeplay.sh"
    player.write_text(f"#!/bin/sh\necho \"played $1\" >> {log}\n")
    player.chmod(0o755)
    env = dict(os.environ, HOME=str(home), VOICE_DESK_SPEECHQ_NO_WARM="1", VOICE_DESK_PYTHON=sys.executable)
    r = subprocess.run(["/bin/bash", str(ROOT / "install_speechq.sh")], env=env, capture_output=True, text=True, timeout=30)
    stand = home / "bin" / "afplay"
    report("installer: puts an executable afplay stand-in in ~/bin", r.returncode == 0 and stand.exists()
           and os.access(stand, os.X_OK), f"{r.returncode} {r.stdout} {r.stderr}")
    clip = home / "x.mp3"
    clip.write_bytes(b"ID3")
    run_env = dict(env, VOICE_DESK_STATE=str(state), VOICE_DESK_REAL_AFPLAY=str(player),
                   HERMES_HOME="/p/profiles/assistant", VOICE_DESK_SPEECHQ_NAMES_ONLY_CACHED="1")
    r2 = subprocess.run([str(stand), str(clip)], env=run_env, capture_output=True, text=True, timeout=20)
    report("installer: the stand-in plays through the real player, in the line (Kylie now holds the floor)",
           r2.returncode == 0 and log.exists() and "x.mp3" in log.read_text()
           and (Q.Station(root=state / "speech").floor() or {}).get("owner") == "assistant", f"{r2.returncode} {r2.stderr}")
    broken = home / "broken-afplay"
    broken.write_text(stand.read_text().replace(sys.executable, "/nonexistent/python")
                      .replace(str(ROOT), "/nonexistent/voice_desk"))
    broken.chmod(0o755)
    log.unlink()
    r3 = subprocess.run([str(broken), str(clip)], env=run_env, capture_output=True, text=True, timeout=20)
    report("installer: Voice Desk moved or its python gone -> the stand-in still plays (plain afplay)",
           r3.returncode == 0 and log.exists() and "x.mp3" in log.read_text(), f"{r3.returncode} {r3.stderr}")
    r4 = subprocess.run(["/bin/bash", str(ROOT / "install_speechq.sh")], env=env, capture_output=True, text=True, timeout=30)
    report("installer: running it again is fine (re-install)", r4.returncode == 0 and stand.exists(), r4.stderr)
    r5 = subprocess.run(["/bin/bash", str(ROOT / "install_speechq.sh"), "--remove"], env=env,
                        capture_output=True, text=True, timeout=30)
    report("installer: --remove takes the stand-in away", r5.returncode == 0 and not stand.exists(), r5.stderr)
    stand.write_text("#!/bin/sh\necho someone else's\n")
    r6 = subprocess.run(["/bin/bash", str(ROOT / "install_speechq.sh")], env=env, capture_output=True, text=True, timeout=30)
    report("installer: an afplay in ~/bin that is not ours is never overwritten",
           r6.returncode != 0 and "someone else's" in stand.read_text(), f"{r6.returncode} {r6.stdout}")
    r7 = subprocess.run(["/bin/bash", str(ROOT / "install_speechq.sh"), "--remove"], env=env,
                        capture_output=True, text=True, timeout=30)
    report("installer: ... nor removed", r7.returncode != 0 and stand.exists(), f"{r7.returncode} {r7.stdout}")


if __name__ == "__main__":
    test_decide()
    test_wants_name()
    test_station()
    test_is_beep()
    test_owner()
    test_standin_processes()
    test_installer()
    print("\nALL PASS" if ok else "\nSOME FAILED")
    sys.exit(0 if ok else 1)
