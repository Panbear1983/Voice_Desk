"""The voice grid, part 2: the Voice check. What really happened, read from the logs.

A setting that says "Always" proves nothing; the log shows whether a voice was made. This module
reads, never writes, never makes a sound, and reads gently: ONE shared reader keeps a cached tail of
each profile's log files and only re-reads a file whose size or modified time changed. The caller
(the dashboard) refreshes no faster than every 30-60 s, and never once per row.

Colours (one word each, `Evidence.status`):
    ok    green   it spoke recently and it worked
    idle  grey    nothing to say lately: NOT a failure
    warn  yellow  worked last time, but some of the last replies went out without voice
    bad   red     a mismatch or a failure: the setting says voice, the evidence says text or an error
    na    dim     the place does not apply

What Hermes logs (profiles/<profile>/logs/agent.log and gateway.log):
    gateway.run: response ready: platform=telegram chat=<id> ...      a Telegram reply was produced
    gateway.platforms.base: [Telegram] Sending response ...           the text went out
    tools.tts_tool: Generating speech ...                             a voice was started
    tools.tts_tool: TTS audio saved: <file> (... provider: x)         the voice file exists
    gateway.run: Auto voice reply TTS failed: ...                     the voice failed
Hermes does NOT log that Telegram accepted a voice message, so "voice made" is the strongest
evidence there is for a direct chat; the detail line says so.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import agents
import gridstate as G

LOOKBACK_DAYS = 7
TAIL_BYTES = 2_500_000                 # the newest part of each log file; older history is not needed
DM_WINDOW_SEC = 150                    # a reply's voice is expected within this long of "response ready"
RECENT_SEC = 24 * 3600                 # "spoke recently" for group notes and windows
LAST_N = 5                             # how many latest direct-chat replies the verdict looks at

_STAMP = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ ")
_REPLY = re.compile(r"gateway\.run: response ready: platform=telegram chat=(-?\d+)")
_SAVED = re.compile(r"tools\.tts_tool: TTS audio saved: (\S+)(?: \([^)]*provider: ([^)]*)\))?")


@dataclass(frozen=True)
class Event:
    ts: float
    kind: str                          # dm_reply | tts_gen | tts_saved | tts_fail
    info: str = ""                     # chat id / file path / error text
    provider: str = ""


@dataclass(frozen=True)
class Evidence:
    status: str                        # ok | idle | warn | bad | na
    headline: str                      # short, goes in the grid cell: "voice 2h ago"
    detail: str = ""                   # full sentence(s) for the detail pane
    ts: Optional[float] = None         # time of the last real speech evidence
    voice: str = ""                    # voice / provider that spoke
    ok: Optional[bool] = None          # last speech worked? None = no speech seen


NA_EVIDENCE = Evidence("na", "", "does not apply")


def ago(ts: Optional[float], now: Optional[float] = None) -> str:
    if ts is None:
        return "never"
    s = max(0, int((now or time.time()) - ts))
    return f"{s}s ago" if s < 90 else f"{s // 60}m ago" if s < 5400 else f"{s // 3600}h ago" if s < 172800 else f"{s // 86400}d ago"


# -- reading the logs, once, shared ---------------------------------------------------------------
def _parse(line: str) -> Optional[Event]:
    m = _STAMP.match(line)
    if not m or ("response ready" not in line and "tts_tool" not in line and "voice reply" not in line
                 and "send_voice" not in line):
        return None
    try:
        ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
    r = _REPLY.search(line)
    if r:
        return Event(ts, "dm_reply" if not r.group(1).startswith("-") else "group_reply", r.group(1))
    s = _SAVED.search(line)
    if s:
        return Event(ts, "tts_saved", s.group(1), s.group(2) or "")
    if "tools.tts_tool: Generating speech" in line:
        return Event(ts, "tts_gen")
    if ("Auto voice reply TTS failed" in line or "Auto voice reply failed" in line
            or "TTS generation failed" in line or ("send_voice fallback" in line and "[Telegram]" in line)):
        return Event(ts, "tts_fail", line[m.end():][:200])
    return None


@dataclass
class _Cached:
    sig: tuple
    events: list[Event]
    since: Optional[float]             # the oldest moment this read could see (None = whole file)


class LogReader:
    """One reader for every profile. events(profile) re-reads a log only when it changed."""

    FILES = ("agent.log", "gateway.log")

    def __init__(self, profiles: Path, tail_bytes: int = TAIL_BYTES) -> None:
        self.profiles = Path(profiles)
        self.tail_bytes = tail_bytes
        self._cache: dict[Path, _Cached] = {}
        self.reads = 0                 # how many files were really read (the test checks this stays low)

    def _tail_events(self, path: Path) -> Optional[_Cached]:
        try:
            st = path.stat()
        except OSError:
            return None
        sig = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(path)
        if hit and hit.sig == sig:
            return hit
        self.reads += 1
        try:
            with open(path, "rb") as fh:
                cut = st.st_size > self.tail_bytes
                if cut:
                    fh.seek(st.st_size - self.tail_bytes)
                    fh.readline()                      # drop the half line
                data = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        events = [e for e in map(_parse, data.splitlines()) if e]
        first_stamp = next((e.ts for e in events), None)
        got = _Cached(sig, events, first_stamp if cut else None)
        self._cache[path] = got
        return got

    def events(self, profile: str) -> tuple[list[Event], Optional[float]]:
        """(events sorted by time, the oldest moment the logs cover or None for 'everything')."""
        seen: set[Event] = set()
        since: Optional[float] = None
        for name in self.FILES:
            got = self._tail_events(self.profiles / profile / "logs" / name)
            if got is None:
                continue
            seen.update(got.events)                    # agent.log and gateway.log repeat some lines
            if got.since is not None:
                since = got.since if since is None else max(since, got.since)
        return sorted(seen, key=lambda e: (e.ts, e.kind, e.info)), since


# -- judging ------------------------------------------------------------------------------------
def _is_team(e: Event) -> bool:
    return "team_room_voice_" in e.info


def _voice_name(provider: str, voice: str) -> str:
    return f"{voice} via {provider}" if provider and voice else (voice or provider)


def judge_tg_dm(cell: G.Cell, events: list[Event], since: Optional[float], now: float, voice: str = "") -> Evidence:
    """Direct chat: did each recent Telegram reply get a voice made for it?"""
    cutoff = now - LOOKBACK_DAYS * 86400
    replies = [e for e in events if e.kind == "dm_reply" and e.ts >= cutoff]
    saved = [e for e in events if e.kind == "tts_saved" and not _is_team(e)]
    fails = [e for e in events if e.kind == "tts_fail"]
    gens = [e for e in events if e.kind == "tts_gen"]

    def voiced(r: Event) -> Optional[Event]:
        return next((s for s in saved if r.ts - 2 <= s.ts <= r.ts + DM_WINDOW_SEC), None)

    def failed(r: Event) -> bool:
        if any(r.ts - 2 <= f.ts <= r.ts + DM_WINDOW_SEC for f in fails):
            return True
        return bool(any(r.ts - 2 <= g.ts <= r.ts + DM_WINDOW_SEC for g in gens)) and voiced(r) is None

    coverage = ""
    if since is not None and since > cutoff:
        coverage = f" (the log tail only reaches back to {time.strftime('%d %b', time.localtime(since))})"
    caveat = " Hermes does not log whether Telegram accepted the voice message; this counts voices made."
    if cell.problem:
        return Evidence("bad", "setting broken", cell.problem)
    if cell.value == G.NA:
        return NA_EVIDENCE
    if not replies:
        return Evidence("idle", "no replies", f"No Telegram direct-chat replies in the last {LOOKBACK_DAYS} days{coverage}.")
    last = replies[-LAST_N:]
    marks = [(r, voiced(r), failed(r)) for r in last]
    n_voice = sum(1 for _, v, _f in marks if v)
    latest, latest_voice, latest_failed = marks[-1]
    made = [v for _, v, _f in marks if v]
    spoke_ts = made[-1].ts if made else None
    spoke_voice = _voice_name(made[-1].provider, voice) if made else ""
    n = len(last)
    if cell.value == G.OFF:
        if made and spoke_ts and now - spoke_ts < RECENT_SEC:
            return Evidence("bad", "voice despite Off", f"Set to Off, but a voice was made {ago(spoke_ts, now)}: the setting is not what the gateway does.{caveat}", spoke_ts, spoke_voice, True)
        return Evidence("idle", "text only (Off)", f"Off, and the last {n} replies were text only, as set{coverage}.")
    if latest_failed:
        why = next((f.info for f in reversed(fails) if latest.ts - 2 <= f.ts <= latest.ts + DM_WINDOW_SEC), "the voice was started but never finished")
        return Evidence("bad", "voice failed", f"The voice for the reply at {time.strftime('%d %b %H:%M', time.localtime(latest.ts))} failed: {why.strip()}", latest.ts, spoke_voice, False)
    if cell.value == G.VOICE_BACK:
        # a voice only comes back for a voice message, and the log does not say which messages were spoken
        if made:
            return Evidence("ok", f"voice {ago(spoke_ts, now)}", f"Voice-back: a voice was made {ago(spoke_ts, now)}.{caveat}", spoke_ts, spoke_voice, True)
        return Evidence("idle", "voice msg only", f"Voice-back only answers a voice message; none of the last {n} replies needed one{coverage}.")
    # ALWAYS: every reply should have a voice
    if n_voice == n:
        return Evidence("ok", f"voice {ago(spoke_ts, now)}", f"All of the last {n} replies had a voice made; the latest {ago(latest.ts, now)}.{caveat}", spoke_ts, spoke_voice, True)
    when = time.strftime("%d %b %H:%M", time.localtime(latest.ts))
    if latest_voice:
        return Evidence("warn", f"mixed {n - n_voice}/{n} text", f"The latest reply had a voice, but {n - n_voice} of the last {n} went out as text only.{caveat}", spoke_ts, spoke_voice, True)
    if n_voice == 0:
        first_text = last[0].ts
        days = max(1, int((now - first_text) // 86400))
        return Evidence("bad", f"text only ×{n}", f"Always, but the last {n} replies (since {time.strftime('%d %b', time.localtime(first_text))}, about {days} day{'s' if days != 1 else ''}) went out as text with no voice. Latest: {when}.", None, "", False)
    return Evidence("bad", "text-only reply", f"Always, but the latest reply ({when}) went out as text with no voice; {n_voice} of the last {n} had one. Last voice: {ago(spoke_ts, now)}.", spoke_ts, spoke_voice, False)


def judge_tg_group(cell: G.Cell, events: list[Event], now: float, voice: str = "") -> Evidence:
    if cell.value == G.NA:
        return NA_EVIDENCE
    notes = [e for e in events if e.kind == "tts_saved" and _is_team(e)]
    last = notes[-1] if notes else None
    if cell.value == G.OFF:
        if last and now - last.ts < RECENT_SEC:
            return Evidence("bad", "voice despite Off", f"Set to Off, but a team-room voice note was made {ago(last.ts, now)}.", last.ts, voice, True)
        return Evidence("idle", "off", "Voice notes are off for the team group.")
    if cell.value == G.UNKNOWN:
        return Evidence("idle", "unknown", cell.note)
    if last and now - last.ts < RECENT_SEC:
        return Evidence("ok", f"note {ago(last.ts, now)}", f"A team-room voice note was made {ago(last.ts, now)} ({len(notes)} in the log tail).", last.ts, _voice_name(last.provider, voice), True)
    if last:
        return Evidence("idle", f"last note {ago(last.ts, now)}", f"No voice note in the last day; the last was {ago(last.ts, now)}. Nothing to say lately is not a failure.", last.ts, _voice_name(last.provider, voice), True)
    return Evidence("idle", "no notes yet", f"No team-room voice note in the log tail (last {LOOKBACK_DAYS} days).")


def judge_terminal_hermes(cell: G.Cell, events: list[Event], now: float, queue_last: Optional[float], voice: str = "") -> Evidence:
    """A Hermes terminal window: speech files made outside Telegram replies and team notes."""
    dm_replies = [e for e in events if e.kind == "dm_reply"]
    def tied_to_dm(s: Event) -> bool:
        return any(r.ts - 2 <= s.ts <= r.ts + DM_WINDOW_SEC for r in dm_replies)
    own = [e for e in events if e.kind == "tts_saved" and not _is_team(e) and not tied_to_dm(e)]
    last_ts = max([e.ts for e in own] + ([queue_last] if queue_last else []), default=None)
    recent_fail = [e for e in events if e.kind == "tts_fail" and now - e.ts < RECENT_SEC
                   and not any(r.ts - 2 <= e.ts <= r.ts + DM_WINDOW_SEC for r in dm_replies)]
    if cell.value == G.UNKNOWN:
        return Evidence("idle", "unknown", cell.note)
    if recent_fail and (last_ts is None or recent_fail[-1].ts > last_ts):
        return Evidence("bad", "voice failed", f"The latest window speech attempt failed {ago(recent_fail[-1].ts, now)}: {recent_fail[-1].info.strip()}", recent_fail[-1].ts, voice, False)
    if last_ts and now - last_ts < RECENT_SEC:
        return Evidence("ok", f"spoke {ago(last_ts, now)}", f"A terminal window spoke {ago(last_ts, now)}.", last_ts, voice, True)
    return Evidence("idle", "idle", "No window speech in the last day (nothing to say lately, not a failure)." if last_ts is None
                    else f"The last window speech was {ago(last_ts, now)}.", last_ts, voice, True if last_ts else None)


def _newest_mtime(folder: Path, prefix: str = "") -> Optional[float]:
    best = None
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if prefix and not entry.name.startswith(prefix):
                    continue
                try:
                    m = entry.stat().st_mtime
                except OSError:
                    continue
                best = m if best is None or m > best else best
    except OSError:
        return None
    return best


def judge_file_hook(cell: G.Cell, last_ts: Optional[float], now: float, voice: str, who: str) -> Evidence:
    """Claude and Gemini speak through a Stop hook that leaves a file behind every time it speaks."""
    if cell.value == G.NA:
        return NA_EVIDENCE
    if cell.problem:
        return Evidence("bad", "setting broken", cell.problem)
    if cell.value == G.OFF:
        if last_ts and now - last_ts < 3600:
            return Evidence("bad", "voice despite Off", f"{who} is set to Off but spoke {ago(last_ts, now)}.", last_ts, voice, True)
        return Evidence("idle", "off", f"{who} voice is off.")
    if last_ts and now - last_ts < RECENT_SEC:
        return Evidence("ok", f"spoke {ago(last_ts, now)}", f"{who} spoke {ago(last_ts, now)} (the hook leaves a file each time).", last_ts, voice, True)
    return Evidence("idle", "idle", f"{who} has not spoken in the last day" + (f" (last {ago(last_ts, now)})" if last_ts else "") + ". Not a failure.", last_ts, voice, True if last_ts else None)


def judge_mic(a: agents.Agent, cell: G.Cell, listener: dict, now: float, voice: str) -> Evidence:
    import service
    if not service.is_fresh(listener, now):
        return Evidence("bad", "listener off", "The Voice Desk listener is not running (no heartbeat), so nothing is spoken in the mic window. R on the second screen starts it.")
    on_line = listener.get("target") == a.target
    if not on_line:
        return Evidence("idle", "not on the line", f"{a.display} is not the one on the line right now; the listener is running and waiting.")
    spoke = listener.get("voice")
    if spoke and listener.get("state") not in ("error",):
        return Evidence("ok", "on the line", f"{a.display} is on the line. Last line spoken as {spoke}.", float(listener.get("since") or now), str(spoke), True)
    return Evidence("idle", "on the line", f"{a.display} is on the line; nothing spoken yet in this conversation.")


def judge_iphone(cell: G.Cell, receiver_up: bool, last_request: Optional[float], now: float) -> Evidence:
    if cell.value == G.UNKNOWN:
        return Evidence("idle", "no shortcut", cell.note)
    if not receiver_up:
        return Evidence("bad", "phone door closed", "The Voice Desk receiver is not answering, so the iPhone shortcut cannot reach the Mac.")
    return Evidence("idle", "ready", "The phone door is open; the iPhone speaks in Siri's own voice, which the Mac cannot hear, so only the request log is evidence."
                    + (f" Last request {ago(last_request, now)}." if last_request else ""), last_request)


def last_request_time(receiver_log: Path) -> Optional[float]:
    try:
        size = receiver_log.stat().st_size
        with open(receiver_log, "rb") as fh:
            fh.seek(max(0, size - 65536))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if "POST /ask" in line and len(line) > 19:
            try:
                return time.mktime(time.strptime(line[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
    return None


# -- the whole grid ------------------------------------------------------------------------------
@dataclass
class Row:
    agent: agents.Agent
    kind: str
    cells: dict[str, G.Cell]
    evidence: dict[str, Evidence]
    voice: G.VoiceInfo
    quiet: dict[str, str] = field(default_factory=dict)


def build(cfg: dict, paths: G.Paths, reader: LogReader, now: Optional[float] = None) -> list[Row]:
    """Everything the grid shows, in one pass over shared, cached reads."""
    import service
    now = now or time.time()
    listener = service.read_status(paths.state / "listener.json")
    receiver_up = G.port_open(paths.receiver_port)
    last_req = last_request_time(paths.state / "receiver.log")
    queue = G._read_json(paths.speech_dir / "last.json") or {}
    claude_last = _newest_mtime(paths.claude_state, "last-spoken-")
    gemini_last = _newest_mtime(paths.gemini_dir / "config/hooks/audio_cache")
    rows: list[Row] = []
    for a in G.grid_agents(cfg):
        kind = G.kind_of(a)
        cells = G.cells_for(a, paths)
        info = G.voice_info(a, cfg)
        ev: dict[str, Evidence] = {}
        if kind == G.HERMES:
            events, since = reader.events(a.profile)
            qlast = queue.get("end") if queue.get("owner") == a.profile else None
            ev["terminal"] = judge_terminal_hermes(cells["terminal"], events, now, qlast, info.en)
            ev["tg_dm"] = judge_tg_dm(cells["tg_dm"], events, since, now, info.en)
            ev["tg_group"] = judge_tg_group(cells["tg_group"], events, now, info.en)
        else:
            if kind == G.CLAUDE:
                ev["terminal"] = judge_file_hook(cells["terminal"], claude_last, now, info.en, "Claude")
            elif kind == G.GEMINI:
                ev["terminal"] = judge_file_hook(cells["terminal"], gemini_last, now, "Jenny / HsiaoChen (hard-coded)", "Gemini")
            else:
                ev["terminal"] = NA_EVIDENCE
            ev["tg_dm"] = ev["tg_group"] = NA_EVIDENCE
            if a.key == "simba" and cells["tg_group"].value != G.NA:      # her own listener sends those, not Hermes
                ev["tg_group"] = Evidence("idle", "her listener", "Her Team_Room voice notes are sent by her own "
                                          "listener (panbear-daily simba.py, `simba.py status` counts them); "
                                          "Voice Desk only holds the voice, so it sees no evidence of them.")
        ev["mic"] = judge_mic(a, cells["mic"], listener, now, info.en)
        ev["iphone"] = judge_iphone(cells["iphone"], receiver_up, last_req, now)
        rows.append(Row(a, kind, cells, ev, info, G.quiet_hours(a)))
    return rows
