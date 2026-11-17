"""Tests for the permission pop-up watcher. Offline: fixture window lists, a fake curl, no screen, no Telegram.

  uv run --no-project --python .venv/bin/python --with pytest python -m pytest tools/permission_watch -q
  .venv/bin/python tools/permission_watch/test_permission_watch.py      (same tests, no pytest needed)
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import permission_watch as pw  # noqa: E402


def win(wid, owner, title=None, layer=0, w=420, h=300, alpha=1.0):
    d = {"kCGWindowNumber": wid, "kCGWindowOwnerName": owner, "kCGWindowLayer": layer, "kCGWindowAlpha": alpha,
         "kCGWindowIsOnscreen": True, "kCGWindowBounds": {"X": 500, "Y": 300, "Width": w, "Height": h}}
    if title is not None:
        d["kCGWindowName"] = title
    return d


# Shapes copied from the live list on 2026-11-17 (macOS 27.0.1, titles hidden without the grant) plus
# ordinary app windows whose titles name a kind of access.
NORMAL = [
    win(572, "Window Server", "StatusIndicator", layer=2147483630, w=28, h=28),
    win(22, "Window Server", "Menubar", layer=24, w=1440, h=30),
    win(318, "FluidVoice", None, layer=3, w=340, h=114, alpha=0.0),
    win(103, "Terminal", None, w=1437, h=833),
    win(230, "System Settings", "Accessibility", w=771, h=625),
    win(231, "System Settings", "Screen & System Audio Recording", w=771, h=625),
    win(400, "Photos", "Photos", w=1200, h=800),
    win(336, "Safari", "Full Disk Access on macOS - Google Search", w=1440, h=869),
    win(171, "Notification Center", None, layer=-2147483601, w=360, h=180),
    win(670, "LINE", None, w=1, h=1),
    win(800, "Control Center", "Accessibility Shortcuts", layer=25, w=40, h=30),
]


def test_known_real_sample_matches():
    got = pw.find_prompts([win(901, "universalAccessAuthWarn", "Screen Recording", layer=8)])
    assert len(got) == 1
    p = got[0]
    assert p["id"] == 901 and p["owner"] == "universalAccessAuthWarn" and p["access"] == "Screen Recording"
    assert pw.caption("alert", p) == ("Permission needed: an app -> Screen Recording. Remote in to click Allow. "
                                      "(pop-up from universalAccessAuthWarn)")


def test_prompt_owners_match_even_with_title_hidden():
    for owner in ("universalAccessAuthWarn", "UserNotificationCenter", "CoreServicesUIAgent", "SecurityAgent"):
        got = pw.find_prompts([win(10, owner)])          # no kCGWindowName: watcher not yet granted
        assert [p["owner"] for p in got] == [owner], owner
        assert "title hidden" in got[0]["access"]


def test_phrases_match_any_owner_and_name_the_app():
    got = pw.find_prompts([win(11, "SomeHelper", '"Terminal" would like to access files in your Desktop folder.')])
    assert len(got) == 1 and got[0]["app"] == "Terminal" and got[0]["access"] == "Desktop folder"
    got = pw.find_prompts([win(12, "x", "Zoom wants access to control this computer using accessibility features")])
    assert got[0]["app"] == "Zoom" and got[0]["access"] == "Accessibility"


def test_access_words_match_only_on_floating_windows():
    assert pw.find_prompts([win(13, "SomeTCCHelper", "Full Disk Access", layer=8)])
    assert pw.find_prompts([win(14, "SomeTCCHelper", "Photos", layer=3)])
    assert not pw.find_prompts([win(15, "SomeTCCHelper", "Full Disk Access", layer=0)])


def test_normal_windows_do_not_match():
    assert pw.find_prompts(NORMAL) == []


def test_invisible_prompt_owner_windows_do_not_match():
    assert not pw.find_prompts([win(16, "UserNotificationCenter", alpha=0.0)])
    assert not pw.find_prompts([win(17, "CoreServicesUIAgent", w=1, h=1)])


def test_mixed_list_finds_only_the_prompt():
    got = pw.find_prompts(NORMAL + [win(901, "universalAccessAuthWarn", "Screen Recording", layer=8)])
    assert [p["id"] for p in got] == [901]


# ---------------------------------------------------------------- dedupe

P = pw.describe(win(901, "universalAccessAuthWarn", "Screen Recording", layer=8))


def test_new_prompt_alerts_once_then_quiet():
    acts, st = pw.plan([P], {"open": {}}, now=1000)
    assert [k for k, _ in acts] == ["alert"]
    for t in (1030, 1060, 1500, 1599):
        acts, st = pw.plan([P], st, now=t)
        assert acts == [], t


def test_one_reminder_at_ten_minutes_and_never_again():
    _, st = pw.plan([P], {"open": {}}, now=1000)
    acts, st = pw.plan([P], st, now=1600)
    assert [k for k, _ in acts] == ["reminder"]
    assert pw.caption("reminder", P).startswith("Reminder, still waiting after 10 minutes.")
    for t in (1630, 2200, 9000):
        acts, st = pw.plan([P], st, now=t)
        assert acts == [], t


def test_closed_is_forgotten_silently_and_reopened_alerts_again():
    _, st = pw.plan([P], {"open": {}}, now=1000)
    acts, st = pw.plan([], st, now=1030)
    assert acts == [] and st == {"open": {}}
    acts, st = pw.plan([P], st, now=1060)
    assert [k for k, _ in acts] == ["alert"]


def test_new_window_id_or_new_title_is_a_new_prompt():
    _, st = pw.plan([P], {"open": {}}, now=1000)
    other = dict(P, id=902)
    acts, _ = pw.plan([P, other], st, now=1030)
    assert [(k, p["id"]) for k, p in acts] == [("alert", 902)]
    retitled = dict(P, title="Accessibility")
    acts, _ = pw.plan([retitled], st, now=1030)
    assert [k for k, _ in acts] == ["alert"]


def test_state_survives_a_restart(tmp_path=None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    path = tmp / "sub" / "state.json"
    _, st = pw.plan([P], {"open": {}}, now=1000)
    pw.save_state(path, st)
    acts, _ = pw.plan([P], pw.load_state(path), now=1030)
    assert acts == []
    path.write_text("not json")
    assert pw.load_state(path) == {"open": {}}
    assert pw.load_state(tmp / "missing.json") == {"open": {}}


# ---------------------------------------------------------------- one pass, with fakes

def _fake_quartz(windows, grant):
    return types.SimpleNamespace(list_windows=lambda: windows, can_capture_screen=lambda: grant)


def _setup(tmp, windows, grant, results):
    """Point the watcher at a temp state dir, a fake window list and a fake Telegram; returns the sent log."""
    sent = []
    sys.modules["quartz_windows"] = _fake_quartz(windows, grant)
    pw.STATE_DIR = Path(tmp)
    pw.read_token = lambda: "123:SECRET"
    pw.read_chat = lambda: "1000000001"

    def fake_photo(png, text, chat, token):
        sent.append(("photo", text))
        return results.pop(0) if results else (True, "sent")

    def fake_text(text, chat, token):
        sent.append(("text", text))
        return results.pop(0) if results else (True, "sent")

    pw.send_photo, pw.send_text = fake_photo, fake_text
    return sent


def test_run_once_without_grant_sends_text_saying_why_then_is_quiet():
    saved = (pw.STATE_DIR, pw.read_token, pw.read_chat, pw.send_photo, pw.send_text)
    try:
        tmp = tempfile.mkdtemp()
        sent = _setup(tmp, NORMAL + [win(901, "universalAccessAuthWarn", "Screen Recording", layer=8)],
                      grant=False, results=[])
        assert pw.run_once(now=1000) == 0
        assert len(sent) == 1 and sent[0][0] == "text"
        assert "Permission needed" in sent[0][1] and "no Screen Recording permission" in sent[0][1]
        assert pw.run_once(now=1030) == 0 and len(sent) == 1          # same prompt: quiet
        assert pw.run_once(now=1600) == 0 and len(sent) == 2 and sent[1][1].startswith("Reminder")
    finally:
        pw.STATE_DIR, pw.read_token, pw.read_chat, pw.send_photo, pw.send_text = saved
        sys.modules.pop("quartz_windows", None)


def test_failed_send_is_retried_next_pass():
    saved = (pw.STATE_DIR, pw.read_token, pw.read_chat, pw.send_photo, pw.send_text)
    try:
        tmp = tempfile.mkdtemp()
        sent = _setup(tmp, [win(901, "universalAccessAuthWarn", "Screen Recording", layer=8)],
                      grant=False, results=[(False, "network down")])
        pw.run_once(now=1000)
        assert json.loads((Path(tmp) / "state.json").read_text()) == {"open": {}}
        pw.run_once(now=1030)
        assert len(sent) == 2 and "901|Screen Recording" in json.loads((Path(tmp) / "state.json").read_text())["open"]
    finally:
        pw.STATE_DIR, pw.read_token, pw.read_chat, pw.send_photo, pw.send_text = saved
        sys.modules.pop("quartz_windows", None)


def test_dry_run_sends_and_saves_nothing(capsys=None):
    saved = (pw.STATE_DIR, pw.read_token, pw.read_chat, pw.send_photo, pw.send_text)
    try:
        tmp = tempfile.mkdtemp()
        sent = _setup(tmp, [win(901, "universalAccessAuthWarn", "Screen Recording", layer=8)], grant=False,
                      results=[])
        assert pw.run_once(dry_run=True, now=1000) == 0
        assert sent == [] and not (Path(tmp) / "state.json").exists()
    finally:
        pw.STATE_DIR, pw.read_token, pw.read_chat, pw.send_photo, pw.send_text = saved
        sys.modules.pop("quartz_windows", None)


# ---------------------------------------------------------------- curl: the token never reaches argv

def test_curl_gets_the_token_on_stdin_not_in_its_arguments():
    tmp = Path(tempfile.mkdtemp())
    seen = tmp / "seen"
    fake = tmp / "curl"
    fake.write_text(f"#!/bin/sh\necho \"ARGS: $*\" > {seen}\ncat >> {seen}\n"
                    "echo '{\"ok\": true, \"result\": {\"message_id\": 42}}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    old = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp}:{old}"
    try:
        ok, note = pw.send_text("hello", "1000000001", "123:SECRET")
    finally:
        os.environ["PATH"] = old
    assert ok and note == "sent (message 42)"
    args, cfg = seen.read_text().split("\n", 1)
    assert "SECRET" not in args and "chat_id=1000000001" in args
    assert 'url = "https://api.telegram.org/bot123:SECRET/sendMessage"' in cfg


def test_telegram_error_note_never_carries_the_token():
    tmp = Path(tempfile.mkdtemp())
    fake = tmp / "curl"
    fake.write_text("#!/bin/sh\necho 'curl: (6) could not reach https://api.telegram.org/bot123:SECRET/x' >&2\n"
                    "exit 6\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    old = os.environ["PATH"]
    os.environ["PATH"] = f"{tmp}:{old}"
    try:
        ok, note = pw.send_text("hello", "1", "123:SECRET")
    finally:
        os.environ["PATH"] = old
    assert not ok and "SECRET" not in note and "<token>" in note


def test_never_scripts_the_ui():
    src = (HERE / "permission_watch.py").read_text() + (HERE / "quartz_windows.py").read_text()
    for banned in ("osascript", "tell application", "CGEventPost", "CGEventCreate", "AXUIElement", "cliclick"):
        assert banned not in src, banned


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("ok  ", name)
        except Exception as e:  # noqa: BLE001
            bad += 1
            print("FAIL", name, type(e).__name__, e)
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
