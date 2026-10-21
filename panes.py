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
#   " ● REC  Ctrl+B to stop "                         recording
#   " ◉ Transcribing... " / " ◉ STT "                  turning speech into text
#   " 🎤 Voice mode | TTS on | Continuous — Ctrl+B …"  a voice chat is on (thinking or speaking too)
#   " 🎤 Voice mode | TTS on — Ctrl+B to record"       voice mode on, nobody talking
#   (no voice segment)                                 voice mode off (an end phrase turns it off)
ACTIVE = ("recording", "transcribing", "conversation")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


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
    """The bottom of the window, or None if herdr cannot read it (pane closed)."""
    args = ["pane", "read", pane, "--source", "visible", "--lines", str(lines)] + (["--format", "ansi"] if ansi else [])
    try:
        r = _herdr(*args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def state_of(tail: Optional[str]) -> str:
    """recording | transcribing | conversation | voice-idle | off | gone  (from the status bar)"""
    if tail is None:
        return "gone"
    rows = [r for r in tail.splitlines() if r.strip()]
    if not rows:
        return "gone"                          # an empty read is a herdr hiccup, not "voice off"
    bar = rows[-1] if rows else ""
    if "● REC" in bar:
        return "recording"
    if "◉ Transcribing" in bar or "◉ STT" in bar:
        return "transcribing"
    if "Voice mode" in bar and "Continuous" in bar:
        return "conversation"
    if "Voice mode" in bar or bar.strip().startswith("🎤"):
        return "voice-idle"
    return "off"


def state(pane: str) -> str:
    return state_of(screen_tail(pane))


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
    not switch voice off. Hermes turns it off, so turn it straight back on (a draft is stashed first)."""
    if state(pane) != "off" or not stopped_by_phrase(screen_tail(pane, lines=30)):
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


def start(pane: str, wait_sec: float = S.PANE_START_SEC) -> str:
    """Press Ctrl+B in the window (turning voice mode on first if it is off) and confirm it is
    recording. Returns started | busy | draft | no-voice | gone."""
    now = state(pane)
    if now == "gone":
        return "gone"
    if now in ACTIVE:
        return "started"                       # already talking there
    if now == "off":
        if not stash_draft(pane):
            return "draft"                     # the stash did not take: never type into Peter's message
        _type_line(pane, "/voice on")
        deadline = time.monotonic() + wait_sec
        while state(pane) == "off":
            if time.monotonic() > deadline:
                return "no-voice"
            time.sleep(0.2)
    _herdr("pane", "send-keys", pane, "ctrl+b")
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if state(pane) in ACTIVE:
            return "started"
        time.sleep(0.2)
    return "busy"                               # Hermes ignores Ctrl+B while it is mid-answer
