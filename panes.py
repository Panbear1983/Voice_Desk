"""The switchboard: hand a spoken conversation to a Hermes session that is already open in herdr.

Peter 2026-10-21: "use voice activation to select the right pane ... self-trigger the Ctrl+B to
start the voice of the selected pane ... when I'm done ... 'that's all the questions I have' ...
to deactivate it, before I talk to another profile." So for a Hermes name the listener does not
run its own turn: it finds that profile's window, presses Ctrl+B there (exactly what Peter's
finger would do), and keeps out of the way until that window's voice chat is over. The real
session answers, with its own memory, model and voice, at terminal speed.

Everything goes through the herdr CLI: `pane list` (find the window by its title,
"Hermes — <profile>"), `pane read` (the Hermes status bar says what voice mode is doing),
`pane send-keys ctrl+b`, and `pane send-text "/voice on"` only when voice mode is off AND the
input line is empty (never mixed into a half-typed draft).
NEVER send ctrl+c: on an idle Hermes prompt it quits the session.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Optional

import settings as S

# The Hermes status bar, the last line of the window (hermes_cli/cli_status_bar_mixin.py
# _get_voice_status_fragments):
#   " ● REC  Ctrl+B to stop "  / " ● REC "            recording (wide / narrow window)
#   " ◉ Transcribing... " / " ◉ STT "                  turning speech into text
#   " 🎤 Voice mode | TTS on | Continuous — Ctrl+B …"  a voice chat is on (thinking or speaking too)
#   " 🎤 Voice mode | TTS on — Ctrl+B to record"       voice mode on, nobody talking
#   " 🎤 Ctrl+B "                                      the same in a narrow window (< 64 columns):
#                                                      no "Voice mode", never "Continuous"
#   (no voice segment)                                 voice mode off (an end phrase turns it off)
# While a turn runs, the input box's top rule carries the spinner ("── ⠹ Working… · 3s ───") and the
# empty input line says "type to redirect this turn" (classic layout: "msg=interrupt · ..."). That
# is "busy", never "off": her voice mode is still on (seen live 2026-11-17 in Jarvis's window).
ACTIVE = ("recording", "transcribing", "conversation")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_SPINNER_RULE = re.compile(r"^──\s*[\u2800-\u28ff]")          # cli_modern_layout._SPIN frames
_TITLED_RULE = re.compile(r"^─ .+ ─$")                         # "─ Esther online status reply ─"
_BUSY_HINTS = ("type to redirect this turn", "type to steer this turn", "type to queue a message",
               "msg=interrupt")
# Hermes sets "recording" (the ● REC bar) BEFORE it opens the microphone, and prints this line only
# once the audio stream really started (cli_voice_mixin._voice_start_recording). A recorder that
# hangs while opening leaves ● REC on screen with no recording behind it (Kylie 2026-11-17: Voice
# Desk said "Kylie is listening" twice, Hermes never logged "Voice recording started").
_REC_CONFIRMED = "● Recording"
_REC_ENDED = ("Silence detected", "Transcribing", "Recording cancelled", "Voice recording failed",
              "Voice mode disabled", "Voice mode enabled", "Stop phrase", "No speech detected")
STATE_LINES = 24                 # enough for a narrow window's footer plus the rows just above it


def _herdr(*args: str, timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run([S.HERDR_BIN, *args], capture_output=True, text=True, timeout=timeout)


def find(profile: str) -> Optional[str]:
    """The pane id of the open Hermes window for this profile, or None."""
    try:
        r = _herdr("pane", "list")
        panes = json.loads(r.stdout)["result"]["panes"]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None
    want = f"hermes — {profile}".lower()
    hits = [p for p in panes
            if str(p.get("terminal_title_stripped") or p.get("terminal_title") or "").strip().lower() == want]
    hits.sort(key=lambda p: p.get("workspace_id") != S.PANE_WORKSPACE)   # the Hermes Agents workspace first
    return hits[0]["pane_id"] if hits else None


def screen_tail(pane: str, lines: int = 6, ansi: bool = False) -> Optional[str]:
    """The bottom of the window, or None if herdr cannot read it (pane closed).

    Source "recent" is the bottom of the terminal whatever Peter is looking at. "visible" was the
    scrolled viewport: with the window scrolled up to read a long answer it showed old text and no
    status bar, which read as "voice off" (checked live 2026-11-17 on a pane scrolled 48 rows up:
    visible showed its start-up banner, recent showed the prompt at the bottom)."""
    args = ["pane", "read", pane, "--source", "recent", "--lines", str(lines)] + (["--format", "ansi"] if ansi else [])
    try:
        r = _herdr(*args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _is_rule(row: str) -> bool:
    s = row.strip()
    return s.startswith("──") or bool(_TITLED_RULE.match(s))


def _split(tail: str) -> tuple[list, list, Optional[str]]:
    """(rows above the input box, rows below its top rule, the top rule itself or None).

    Hermes also prints a fixed 40-cell rule into the scrollback around every message and answer;
    in a narrow window it wraps into a full row plus a short one ("─────────"). That pair is not the
    input box, whose top rule is followed by the input line, never by another rule. While Hermes
    redraws, a read can end on such a pair with no input box under it (caught live 2026-11-17 in
    Jarvis's window, 3 reads in 1,100 during a busy turn); that must not read as "voice off"."""
    rows = [r for r in tail.splitlines() if r.strip()]

    def dash_len(k: int) -> int:
        s = rows[k].strip() if 0 <= k < len(rows) else ""
        return len(s) if s and set(s) == {"─"} else 0

    def wrapped(i: int) -> bool:               # head (a shorter dash row follows) or tail (a longer one precedes)
        n = dash_len(i)
        return bool(n) and (0 < dash_len(i + 1) < n or dash_len(i - 1) > n)

    rules = [i for i, r in enumerate(rows) if _is_rule(r) and not wrapped(i)]
    if not rules:
        return rows[:-3], rows[-3:], None
    i = rules[-1]
    return rows[:i], rows[i + 1:], rows[i].strip()


def state_of(tail: Optional[str]) -> str:
    """recording | transcribing | conversation | busy | voice-idle | off | unclear | gone

    Hermes now renders a second footer row below the voice status bar (model/context/usage
    stats, e.g. " claude-fable-5-1 · 147K/1M · ..."), so the voice bar is not always the very
    last line. Scan the last few non-blank rows for the voice marker instead of trusting only
    rows[-1] (seen live 2026-11-06: an already-on pane was misread as "off" because the stats
    row had become the true last line).

    Only rows BELOW the input box's top rule count (the input line, the voice bar, the stats
    row). Above it is scrollback, and "Voice mode disabled." there must not read as voice on,
    or rearm() never turns voice back on after an end phrase (seen live 2026-11-09).

    busy: a turn is running (spinner in the top rule, or the busy hint on the input line). Never
    "off", whatever the bar shows (Kylie 2026-11-17: a busy turn was taken for an end phrase).
    unclear: no input box to judge by (no rule and no voice bar, or nothing under the rule, as in
    a half-drawn frame). Never "off" either: "off" needs a drawn input box with no voice bar."""
    if tail is None:
        return "gone"
    if not [r for r in tail.splitlines() if r.strip()]:
        return "gone"                          # an empty read is a herdr hiccup, not "voice off"
    _, footer, rule = _split(tail)
    footer = [r for r in footer if not r.strip().startswith("Voice mode")]   # Hermes' own messages
    for bar in reversed(footer):
        if "● REC" in bar:
            return "recording"
        if "◉ Transcribing" in bar or "◉ STT" in bar:
            return "transcribing"
    if (rule and _SPINNER_RULE.match(rule)) or any(h in r for r in footer for h in _BUSY_HINTS):
        return "busy"
    for bar in reversed(footer):
        if "Voice mode" in bar and "Continuous" in bar:
            return "conversation"
        if "Voice mode" in bar or bar.strip().startswith("🎤"):
            return "voice-idle"                # the narrow bar " 🎤 Ctrl+B" lands here too
    if rule is None or not footer:
        return "unclear"
    return "off"


def recording_confirmed(tail: Optional[str]) -> bool:
    """Is the window really recording? ● REC on the bar AND Hermes' own "● Recording..." line just
    above the input box, with no later sign that recording ended (silence, transcribing, failure)."""
    if state_of(tail) != "recording":
        return False
    above, _, _ = _split(tail or "")
    for row in reversed(above[-8:]):           # nothing is printed while it records: the line stays near
        s = row.strip()
        if s.startswith(_REC_CONFIRMED):
            return True
        if any(s.startswith(m) or m in s for m in _REC_ENDED):
            return False
    return False


def _look(pane: str, confirm_off: bool = True, lines: int = STATE_LINES) -> tuple[str, Optional[str]]:
    """(state, tail). A half-drawn frame is read again (up to three reads); "off" is believed only
    if a second read 0.3 s later says so too (a redraw lasts milliseconds, an end phrase stays)."""
    now, tail = "unclear", None
    for attempt in range(4):
        tail = screen_tail(pane, lines=lines)
        now = state_of(tail)
        if now == "unclear" and attempt < 2:
            time.sleep(0.15)
            continue
        if now == "off" and confirm_off:
            confirm_off = False
            time.sleep(0.3)
            continue
        break
    return now, tail


def state(pane: str, confirm_off: bool = True) -> str:
    return _look(pane, confirm_off)[0]


VERDICT_LINES = 32               # the stop-phrase line sits ~12 rows above the input box in a narrow window


def look(pane: str) -> tuple[str, Optional[str]]:
    """(state, tail) in one read, with rows enough for heard_verdict() (the switchboard's watch)."""
    return _look(pane, lines=VERDICT_LINES)


_STT_STARTED = ("Transcribing", "Preparing local STT")
_NOTHING_HEARD = ("No speech detected", "Transcription failed", "Voice processing error", "Recording cancelled",
                  "Voice recording failed")


def heard_verdict(tail: Optional[str]) -> str:
    """What did the window make of its latest recording?  end | nothing | pending

    end: Hermes transcribed it and it was a stop phrase: the newest event above the input box is
         "Stop phrase detected", printed right after "Transcribing..." (or the local model's
         "Preparing local STT ..."). Nothing newer, no later "● Recording...".
    nothing: no words came of it (no speech, a failed transcription, a cancelled recording).
    pending: no verdict on the screen (still transcribing, a normal turn, a new recording, unreadable).
    Agent text that merely quotes the line is not "end": the transcribing line must sit just above it."""
    if not tail:
        return "pending"
    above, _, _ = _split(tail)
    rows = [r.strip() for r in above]
    for i in range(len(rows) - 1, -1, -1):
        s = rows[i]
        if s.startswith("Stop phrase detected"):
            return "end" if any(r.startswith(_STT_STARTED) for r in rows[max(0, i - 6):i]) else "pending"
        if s.startswith(_REC_CONFIRMED):
            return "pending"                   # the newest event is a recording: its verdict is not out yet
        if s.startswith(_NOTHING_HEARD):
            return "nothing"
    return "pending"


def stopped_by_phrase(tail: Optional[str]) -> bool:
    """Did the window's voice mode just end because Peter said an end phrase (not /voice off)?
    Hermes prints "Stop phrase detected — ending voice chat." then "Voice mode disabled."; a typed
    /voice off prints only the second. Only the LAST "disabled" counts, so a later /voice off wins."""
    if not tail:
        return False
    rows = [r.strip() for r in tail.splitlines() if r.strip()]
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].startswith("Voice mode disabled"):
            return i > 0 and rows[i - 1].startswith(("Stop phrase detected", "Stop phrase typed"))
        if rows[i].startswith("Voice mode enabled"):
            return False
    return False


def _type_line(pane: str, text: str) -> None:
    """Type a command and press Enter. An Enter sent in the same instant as the text is dropped by
    the Hermes prompt (seen live 2026-10-21), so give the text a moment to land first."""
    _herdr("pane", "send-text", pane, text)
    time.sleep(0.3)
    _herdr("pane", "send-keys", pane, "enter")


def stash_draft(pane: str) -> bool:
    """Peter 2026-10-21: "make all of the profiles exactly the same as Jarvis and Kylie" - a
    half-typed message must not stop voice coming back. Ctrl+S is Hermes' own draft stash: it keeps
    the text (Ctrl+S on an empty line brings it back) and clears the line. Never deletes anything."""
    if not has_draft(screen_tail(pane, ansi=True)):
        return True
    _herdr("pane", "send-keys", pane, "ctrl+s")
    time.sleep(0.5)
    return not has_draft(screen_tail(pane, ansi=True))


def rearm(pane: str) -> bool:
    """Peter 2026-10-21: an end phrase should put the window back to 'voice on, waiting for Ctrl+B',
    not switch voice off. Hermes turns it off, so turn it straight back on (a draft is stashed first).
    Checked every 1.5 s for four windows, so the first look is a single read; the second, before
    typing anything, is confirmed (a half-drawn frame mid-turn must not get "/voice on" typed in)."""
    if state(pane, confirm_off=False) != "off" or not stopped_by_phrase(screen_tail(pane, lines=30)):
        return False
    if state(pane) != "off":
        return False
    if not stash_draft(pane):
        return False
    _type_line(pane, "/voice on")
    return True


def has_draft(ansi_tail: Optional[str]) -> bool:
    """Is there half-typed text on the input line? The input row is the one with the ❯ prompt;
    an empty line shows nothing after it, or the placeholder, which Hermes draws in italics
    (ESC[3m). Plain text after the prompt is Peter's own draft. Unreadable counts as a draft."""
    if ansi_tail is None:
        return True
    rows = [r for r in ansi_tail.splitlines() if "❯" in r]
    if not rows:
        return False
    after = rows[-1].split("❯", 1)[1]
    after = re.sub(r"^\s*(\x1b\[0m)?\s*", "", after)      # the prompt's own reset
    if not _ANSI.sub("", after).strip():
        return False
    return not after.startswith("\x1b[3m")               # italic = the placeholder, not typed text


def _await_rec(pane: str, wait_sec: float) -> str:
    """After a Ctrl+B: started | mic-stuck | busy | ignored."""
    deadline = time.monotonic() + wait_sec
    stuck_deadline = deadline + wait_sec          # ● REC is up: give the recorder a little longer to open
    while True:
        now, tail = _look(pane)
        if recording_confirmed(tail):
            return "started"
        if now in ("busy", "transcribing"):
            return "busy"                         # a turn is running: Hermes ignores Ctrl+B mid-turn
        t = time.monotonic()
        if now == "recording":
            if t > stuck_deadline:
                return "mic-stuck"
        elif t > deadline:
            return "ignored"
        time.sleep(0.2)


def start(pane: str, wait_sec: float = S.PANE_START_SEC) -> str:
    """Press Ctrl+B in the window (turning voice mode on first if it is off) and confirm it is
    recording. Returns started | busy | draft | no-voice | no-rec | mic-stuck | gone.

    "started" only when the window really records: ● REC on the bar and Hermes' own "● Recording..."
    line (Kylie 2026-11-17: Voice Desk twice said "Kylie is listening" while her mic never opened).
    Ctrl+B ignored -> pressed once more, then "no-rec". ● REC that never confirms -> "mic-stuck",
    without a second press (on a recording window Ctrl+B means stop)."""
    now, tail = _look(pane)
    if now == "gone":
        return "gone"
    if now == "recording":
        return "started" if recording_confirmed(tail) else "mic-stuck"   # already recording there (or stuck)
    if now in ("transcribing", "busy", "unclear"):
        return "busy"                          # mid-turn: the caller tries again when she is free
    if now == "off":
        if not stash_draft(pane):
            return "draft"                     # the stash did not take: never type into Peter's message
        _type_line(pane, "/voice on")
        deadline = time.monotonic() + wait_sec
        while _look(pane)[0] not in ("voice-idle", "conversation"):   # /voice on runs as a busy command
            if time.monotonic() > deadline:
                return "no-voice"
            time.sleep(0.2)
    for attempt in range(2):
        if attempt:
            again, _ = _look(pane)
            if again == "recording":           # the first press opened it late: wait for it, no second press
                got = _await_rec(pane, wait_sec)
                return "no-rec" if got == "ignored" else got
            if again not in ("voice-idle", "conversation"):
                return "busy"                  # something changed since the first press: do not toggle it
        _herdr("pane", "send-keys", pane, "ctrl+b")
        got = _await_rec(pane, wait_sec)
        if got != "ignored":
            return got
    return "no-rec"
