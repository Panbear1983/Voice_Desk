#!/usr/bin/env python3
"""Permission pop-up watcher: tells Peter on Telegram when macOS is waiting for him to click Allow.

A plain script, no agent and no model. launchd runs it every 30 seconds (`--once`). Each pass:

  1. reads the on-screen window list (quartz_windows.py, read-only);
  2. picks out macOS permission prompts (`find_prompts`, a pure function over that list);
  3. for a prompt it has not reported yet: takes a picture of just that window
     (`screencapture -x -o -l <window id>`) and sends it to Peter with Telegram sendPhoto, captioned
     "Permission needed: <app> -> <access>. Remote in to click Allow."; without the Screen Recording
     grant it sends the same words as text and says why there is no picture;
  4. one reminder if the same prompt is still open 10 minutes after the first message;
  5. forgets a prompt as soon as its window is gone (no message on close).

It NEVER clicks, types or scripts any window: no AppleScript, no System Events, no accessibility calls.

The bot token is read at run time from Jarvis's Hermes profile file (~/.hermes/profiles/orchestrator/.env,
TELEGRAM_BOT_TOKEN), the same place the board-picture script sends from; Peter's chat number from
~/.voice_desk/tg_dm_chat, the same file the Agent Control Desk uses. Neither is ever printed, logged or
written into this folder. The token goes to curl on its standard input, so it is not in the process list.
curl, not Python's urllib: urllib fails on this Mac's self-signed certificate chain.

Remembered prompts: ~/.local/state/permission_watch/state.json (survives restarts).

  python permission_watch.py --once              one pass (what launchd runs)
  python permission_watch.py --once --dry-run    one pass, print what it would send; sends and saves nothing
  python permission_watch.py                     keep checking every 30 s in this window
  python permission_watch.py --test-send x.png   send x.png to Peter captioned TEST (proof the route works)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOME = Path.home()
STATE_DIR = Path(os.environ.get("PERMISSION_WATCH_STATE", HOME / ".local/state/permission_watch"))
TOKEN_FILE = Path(os.environ.get("PERMISSION_WATCH_TOKEN_FILE", HOME / ".hermes/profiles/orchestrator/.env"))
TOKEN_KEY = os.environ.get("PERMISSION_WATCH_TOKEN_KEY", "TELEGRAM_BOT_TOKEN")
CHAT_FILE = Path(os.environ.get("VOICE_DESK_STATE", HOME / ".voice_desk")) / "tg_dm_chat"
INTERVAL = 30            # seconds between passes when not run with --once
REMIND_AFTER = 600       # one reminder when a prompt is still open 10 minutes after the first message

# Processes that only ever draw permission / authorisation dialogs (lower-case). The known real one:
# owner universalAccessAuthWarn, title "Screen Recording".
PROMPT_OWNERS = {"universalaccessauthwarn", "usernotificationcenter", "coreservicesuiagent", "securityagent",
                 "tccd", "authorizationhost"}
# Words only a permission question uses: these match whoever draws the window.
PROMPT_PHRASES = ("would like to access", "wants access", "wants to access", "would like to control",
                  "would like to record", "would like to use", "would like to find")
# Kinds of access. A title naming one counts as a prompt only on a floating window (layer above 0) drawn by
# something other than the menu bar / Dock / Notification Center, so the System Settings "Accessibility" page,
# the Photos app or a browser tab about Full Disk Access (all ordinary layer-0 windows) never set it off.
ACCESS_TRIGGERS = ("Screen Recording", "Accessibility", "Full Disk Access", "Photos")
ACCESS_NAMES = ("Full Disk Access", "Screen & System Audio Recording", "Screen Recording", "Accessibility",
                "Input Monitoring", "Photos", "Camera", "Microphone", "Contacts", "Calendars", "Reminders",
                "Automation", "Bluetooth", "Location", "Local Network", "Desktop folder", "Documents folder",
                "Downloads folder", "removable volumes", "network volumes", "App Management")
SHELL_OWNERS = {"window server", "dock", "control center", "controlcenter", "systemuiserver", "notification center",
                "spotlight", "windowmanager", "textinputmenuagent"}
MIN_SIDE = 40            # smaller than this (or fully transparent) is a helper window nobody can see


# ---------------------------------------------------------------- the detector (pure)

def _text(w: Dict[str, Any], key: str) -> str:
    v = w.get(key)
    return v.strip() if isinstance(v, str) else ""


def _visible(w: Dict[str, Any]) -> bool:
    if w.get("kCGWindowIsOnscreen") is False:
        return False
    if float(w.get("kCGWindowAlpha", 1) or 0) <= 0:
        return False
    b = w.get("kCGWindowBounds") or {}
    return float(b.get("Width", MIN_SIDE) or 0) >= MIN_SIDE and float(b.get("Height", MIN_SIDE) or 0) >= MIN_SIDE


def is_prompt(w: Dict[str, Any]) -> bool:
    """True when this window (a Quartz window-list dict) is a macOS permission prompt waiting for a click."""
    if not _visible(w):
        return False
    owner, title = _text(w, "kCGWindowOwnerName"), _text(w, "kCGWindowName")
    if owner.lower() in PROMPT_OWNERS:
        return True
    words = f"{owner} {title}".lower()
    if any(p in words for p in PROMPT_PHRASES):
        return True
    layer = int(w.get("kCGWindowLayer", 0) or 0)
    return (layer > 0 and owner.lower() not in SHELL_OWNERS
            and any(t.lower() in title.lower() for t in ACCESS_TRIGGERS))


def describe(w: Dict[str, Any]) -> Dict[str, Any]:
    """The facts an alert needs: window id, who drew it, its title, the asking app and the kind of access."""
    owner, title = _text(w, "kCGWindowOwnerName"), _text(w, "kCGWindowName")
    app = ""
    m = re.search(r"[\"“”']([^\"“”']{1,60})[\"“”']\s+(?:would like|wants)", title)
    if not m:
        m = re.match(r"\s*(.{1,60}?)\s+(?:would like|wants)\b", title)
    if m:
        app = m.group(1).strip()
    access = next((a for a in ACCESS_NAMES if a.lower() in title.lower()), "")
    if not access and title:
        access = title[:80]
    return {"id": int(w.get("kCGWindowNumber", 0) or 0), "owner": owner or "?", "title": title,
            "app": app or "an app", "access": access or "a permission (title hidden from the watcher)"}


def find_prompts(windows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every permission prompt in a window list, described. Pure: same list in, same answer out."""
    return [describe(w) for w in windows if is_prompt(w)]


def prompt_key(p: Dict[str, Any]) -> str:
    return f"{p['id']}|{p['title']}"


# ---------------------------------------------------------------- what to send (pure)

def plan(prompts: List[Dict[str, Any]], state: Dict[str, Any], now: float,
         remind_after: float = REMIND_AFTER) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, Any]]:
    """Decide the messages for this pass and the state to keep.

    New prompt -> ("alert", p). Still open `remind_after` seconds after its alert and not yet reminded ->
    ("reminder", p), once. Already told -> nothing. A remembered prompt that is no longer on screen is
    dropped silently, so the same window coming back later counts as new."""
    known = state.get("open", {})
    keep: Dict[str, Any] = {}
    actions: List[Tuple[str, Dict[str, Any]]] = []
    for p in prompts:
        key = prompt_key(p)
        if key in keep:                      # the same window listed twice: one message
            continue
        rec = known.get(key)
        if rec is None:
            actions.append(("alert", p))
            rec = {"alerted_at": now, "reminded": False, "owner": p["owner"], "title": p["title"]}
        elif not rec.get("reminded") and now - float(rec.get("alerted_at", now)) >= remind_after:
            actions.append(("reminder", p))
            rec = dict(rec, reminded=True)
        keep[key] = rec
    return actions, {"open": keep}


def caption(kind: str, p: Dict[str, Any], test: bool = False) -> str:
    head = "Reminder, still waiting after 10 minutes. " if kind == "reminder" else ""
    text = f"{head}Permission needed: {p['app']} -> {p['access']}. Remote in to click Allow."
    if p["owner"] and p["owner"] != p["app"]:
        text += f" (pop-up from {p['owner']})"
    return f"TEST: {text}" if test else text


# ---------------------------------------------------------------- state file

def load_state(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data.get("open"), dict) else {"open": {}}
    except (OSError, ValueError, AttributeError):
        return {"open": {}}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- Telegram (curl)

def read_token(path: Path = TOKEN_FILE, key: str = TOKEN_KEY) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def read_chat() -> str:
    got = os.environ.get("PERMISSION_WATCH_CHAT", "") or os.environ.get("VOICE_DESK_TG_DM_CHAT", "")
    if not got:
        try:
            got = CHAT_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            got = ""
    return got.strip()


def _redact(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


def telegram(method: str, fields: List[str], token: str) -> Tuple[bool, str]:
    """Call one Telegram Bot API method with curl. The URL (which holds the token) goes in on stdin as a curl
    config line, so it never shows in `ps`. Returns (ok, short note without the token)."""
    cfg = f'url = "https://api.telegram.org/bot{token}/{method}"\n'
    try:
        r = subprocess.run(["curl", "-sS", "--max-time", "40", "-K", "-", *fields], input=cfg,
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"curl did not run: {type(e).__name__}"
    try:
        body = json.loads(r.stdout or "{}")
    except ValueError:
        body = {}
    if body.get("ok") is True:
        res = body.get("result")
        mid = res.get("message_id") if isinstance(res, dict) else None
        return True, f"sent (message {mid})" if mid else "sent"
    note = body.get("description") or (r.stderr or r.stdout or f"curl exit {r.returncode}").strip()
    return False, _redact(note, token)[:200]


def send_photo(png: str, text: str, chat: str, token: str) -> Tuple[bool, str]:
    return telegram("sendPhoto", ["--form-string", f"chat_id={chat}", "--form-string", f"caption={text}",
                                  "-F", f"photo=@{png};type=image/png"], token)


def send_text(text: str, chat: str, token: str) -> Tuple[bool, str]:
    return telegram("sendMessage", ["--form-string", f"chat_id={chat}", "--form-string", f"text={text}"], token)


# ---------------------------------------------------------------- the picture

def capture(window_id: int, can_capture: bool) -> Tuple[Optional[str], str]:
    """A PNG of just this window, or (None, why not). Never asks macOS for the grant (no pop-up of our own)."""
    if not can_capture:
        return None, (f"no screenshot: the watcher's Python ({os.path.realpath(sys.executable)}) "
                      "has no Screen Recording permission")
    fd, png = tempfile.mkstemp(prefix="permission_watch_", suffix=".png")
    os.close(fd)
    try:
        r = subprocess.run(["/usr/sbin/screencapture", "-x", "-o", "-l", str(window_id), png],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and os.path.getsize(png) > 0:
            return png, ""
        why = (r.stderr or f"exit {r.returncode}").strip()[:120]
    except (OSError, subprocess.TimeoutExpired) as e:
        why = type(e).__name__
    try:
        os.unlink(png)
    except OSError:
        pass
    return None, f"no screenshot: screencapture failed ({why})"


def deliver(kind: str, p: Dict[str, Any], chat: str, token: str, can_capture: bool) -> Tuple[bool, str]:
    text = caption(kind, p)
    png, why = capture(p["id"], can_capture)
    if png:
        try:
            ok, note = send_photo(png, text, chat, token)
        finally:
            try:
                os.unlink(png)
            except OSError:
                pass
        if ok:
            return True, "photo sent"
        why = f"no screenshot: the photo did not go through ({note})"
    ok, note = send_text(f"{text}\n({why}.)", chat, token)
    return ok, f"text sent ({why})" if ok else f"not sent: {note}"


# ---------------------------------------------------------------- one pass

def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def run_once(dry_run: bool = False, now: Optional[float] = None) -> int:
    import quartz_windows  # macOS only; kept out of the pure part so the tests need no screen
    now = time.time() if now is None else now
    windows = quartz_windows.list_windows()
    prompts = find_prompts(windows)
    state_path = STATE_DIR / "state.json"
    state = load_state(state_path)
    actions, new_state = plan(prompts, state, now)
    if dry_run:
        print(f"dry run: {len(windows)} windows on screen, {len(prompts)} permission prompt(s), "
              f"Screen Recording grant: {'yes' if quartz_windows.can_capture_screen() else 'no'}")
        for p in prompts:
            print(f"  prompt: window {p['id']} owner={p['owner']!r} title={p['title']!r}")
        for kind, p in actions:
            print(f"  would send ({kind}): {caption(kind, p)}")
        if not actions:
            print("  nothing to send")
        return 0
    if actions:
        token, chat = read_token(), read_chat()
        if not token or not chat:
            log(f"cannot send: {'no bot token' if not token else 'no chat number'} found; will retry next pass")
            return 1
        can_capture = quartz_windows.can_capture_screen()
        for kind, p in actions:
            ok, note = deliver(kind, p, chat, token, can_capture)
            log(f"{kind} for window {p['id']} ({p['owner']}: {p['title'] or 'no title'}): {note}")
            key = prompt_key(p)
            if not ok:                       # try again on the next pass
                if kind == "alert":
                    new_state["open"].pop(key, None)
                else:
                    new_state["open"][key]["reminded"] = False
    if actions or new_state != state:
        save_state(state_path, new_state)
    return 0


def test_send(png: str) -> int:
    token, chat = read_token(), read_chat()
    if not token or not chat:
        print("no bot token or chat number found", file=sys.stderr)
        return 1
    sample = {"id": 0, "owner": "universalAccessAuthWarn", "title": "Screen Recording",
              "app": "an app", "access": "Screen Recording"}
    ok, note = send_photo(png, caption("alert", sample, test=True) + " This is only a test of the route.",
                          chat, token)
    print(f"test photo: {note}")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Tell Peter on Telegram when a macOS permission pop-up waits. "
                                             "Never clicks anything.")
    ap.add_argument("--once", action="store_true", help="one pass, then exit (what launchd runs)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; send and save nothing")
    ap.add_argument("--test-send", metavar="PNG", help="send this picture to Peter captioned TEST, then exit")
    a = ap.parse_args(argv)
    if a.test_send:
        return test_send(a.test_send)
    if a.once:
        return run_once(dry_run=a.dry_run)
    while True:
        try:
            run_once(dry_run=a.dry_run)
        except Exception as e:  # keep watching; launchd mode (--once) lets it surface instead
            log(f"pass failed: {type(e).__name__}: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
