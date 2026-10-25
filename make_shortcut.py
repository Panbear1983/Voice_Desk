"""Build the iPhone side: signed "Talk to ..." Shortcut files, one per thread.

The shortcut loops: dictate (stops on a pause) -> POST to the Mac receiver -> speak what
came back -> check back every ~8 s and speak the answer when it is ready -> repeat. Open the produced file once on the Mac (Shortcuts asks "Add Shortcut");
iCloud then syncs it to the iPhone.

  .venv/bin/python make_shortcut.py                      # 'Talk to Claude.shortcut' here + ~/Desktop/Misc
  .venv/bin/python make_shortcut.py --to esther          # 'Talk to Esther.shortcut', pinned to that Hermes profile
  .venv/bin/python make_shortcut.py --to codex           # 'Talk to Codex.shortcut', pinned to its own conversation
  .venv/bin/python make_shortcut.py --all                # one file per installed brain and Hermes profile
  .venv/bin/python make_shortcut.py --to orchestrator --name "Talk to the Boss"
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import settings as S
from brain import Brain

REPEATS = 300          # one tap gives up to 300 exchanges
TAILSCALE = shutil.which("tailscale") or "/opt/homebrew/bin/tailscale"
TAILSCALE_IP = os.environ.get("VOICE_DESK_MAC_IP") or (
    subprocess.run([TAILSCALE, "ip", "-4"], capture_output=True, text=True).stdout.strip().splitlines() or [""])[0]
if not TAILSCALE_IP:
    sys.exit("No Tailscale address found. Install Tailscale and sign in, or set VOICE_DESK_MAC_IP.")
URL_ASK = f"http://{TAILSCALE_IP}:{S.RECEIVER_PORT}/ask"
URL_REPLY = f"http://{TAILSCALE_IP}:{S.RECEIVER_PORT}/reply"
TOKEN = S.RECEIVER_TOKEN_FILE.read_text().strip()


def text(value: str) -> dict:
    return {"Value": {"string": value, "attachmentsByRange": {}}, "WFSerializationType": "WFTextTokenString"}


def ref(output_uuid: str, output_name: str) -> dict:
    return {"Value": {"string": "￼",
                      "attachmentsByRange": {"{0, 1}": {"OutputUUID": output_uuid, "OutputName": output_name,
                                                        "Type": "ActionOutput"}}},
            "WFSerializationType": "WFTextTokenString"}


def dictionary(items: list[tuple[str, dict]]) -> dict:
    return {"Value": {"WFDictionaryFieldValueItems": [
        {"WFItemType": 0, "WFKey": text(k), "WFValue": v} for k, v in items]},
        "WFSerializationType": "WFDictionaryFieldValue"}


POLLS = 12             # after each question: up to 12 check-backs of ~8 s = ~100 s for a long answer;
                       # once the answer is spoken the remaining check-backs return instantly


def url_action(uid: str, method: str, body: dict | None = None, to: str = "") -> dict:
    url = URL_ASK if method == "POST" else (f"{URL_REPLY}?to={to}" if to else URL_REPLY)
    params = {"UUID": uid, "WFURL": url, "WFHTTPMethod": method,
              "ShowHeaders": True, "WFHTTPHeaders": dictionary([("X-Token", text(TOKEN))])}
    if body:
        params["WFHTTPBodyType"] = "JSON"
        params["WFJSONValues"] = dictionary(list(body.items()))
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl", "WFWorkflowActionParameters": params}


def speak_reply(url_uuid: str) -> list[dict]:
    """Get Dictionary Value 'reply' from the last URL result, then Speak Text."""
    value_uuid = str(uuid.uuid4())
    return [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
         "WFWorkflowActionParameters": {"UUID": value_uuid, "WFGetDictionaryValueType": "Value",
                                        "WFDictionaryKey": "reply",
                                        "WFInput": {"Value": {"OutputUUID": url_uuid, "OutputName": "Contents of URL",
                                                              "Type": "ActionOutput"},
                                                    "WFSerializationType": "WFTextTokenAttachment"}}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
         "WFWorkflowActionParameters": {"WFSpeakTextWait": True,
                                        "WFText": ref(value_uuid, "Dictionary Value")}},
    ]


def build(to: str = "") -> dict:
    """One round = dictate -> POST /ask -> speak -> (GET /reply -> speak) x POLLS.
    `to` pins every request to one thread ("claude" or "hermes:<profile>").

    Siri drops any single request that takes much longer than ten seconds, so the Mac
    answers /ask at once and the phone checks back; every check-back speaks whatever came
    back ("." is silent), so no conditional logic is needed in the Shortcut."""
    group = str(uuid.uuid4())
    dictate_uuid = str(uuid.uuid4())
    ask_uuid = str(uuid.uuid4())
    actions = [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.repeat.count",
         "WFWorkflowActionParameters": {"GroupingIdentifier": group, "WFControlFlowMode": 0, "WFRepeatCount": REPEATS}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.dictatetext",
         "WFWorkflowActionParameters": {"UUID": dictate_uuid, "WFDictateTextStopListening": "After Pause"}},
        url_action(ask_uuid, "POST", {"text": ref(dictate_uuid, "Dictated Text"), **({"to": text(to)} if to else {})}),
        *speak_reply(ask_uuid),
    ]
    for _ in range(POLLS):
        poll_uuid = str(uuid.uuid4())
        actions.append(url_action(poll_uuid, "GET", to=to))
        actions.extend(speak_reply(poll_uuid))
    actions.append({"WFWorkflowActionIdentifier": "is.workflow.actions.repeat.count",
                    "WFWorkflowActionParameters": {"GroupingIdentifier": group, "WFControlFlowMode": 2}})
    return {
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowTypes": ["NCWidget", "WatchKit"],
        "WFWorkflowActions": actions,
    }


def make(name: str, to: str) -> Path:
    here = Path(__file__).resolve().parent
    raw = here / f"{name}.unsigned.shortcut"
    signed = here / f"{name}.shortcut"
    with open(raw, "wb") as fh:
        plistlib.dump(build(to), fh)
    r = None
    for attempt in range(4):
        r = subprocess.run(["/usr/bin/shortcuts", "sign", "--mode", "anyone", "--input", str(raw), "--output", str(signed)],
                           capture_output=True, text=True)
        if r.returncode == 0 and signed.exists():
            break
        if attempt < 3:
            time.sleep(1.5)
    raw.unlink(missing_ok=True)
    if not signed.exists():
        err = (r.stderr.strip() or r.stdout.strip()) if r else "unknown error"
        print("signing failed:", err)
        sys.exit(1)
    misc = Path.home() / "Desktop/Misc"
    if misc.is_dir():
        shutil.copy(signed, misc / signed.name)
    print(f"built {signed}  -> {to or 'claude'}  (receiver {URL_ASK}, {POLLS} check-backs per question)")
    return signed


def main() -> None:
    ap = argparse.ArgumentParser(description="Build signed 'Talk to ...' Shortcuts for the iPhone.")
    ap.add_argument("--to", default="", help="thread: 'claude', 'gemini', 'codex' or a Hermes profile")
    ap.add_argument("--name", default="", help="Shortcut name (what you say to Siri); default 'Talk to <name>'")
    ap.add_argument("--all", action="store_true", help="one Shortcut per installed brain and Hermes profile")
    args = ap.parse_args()
    brain = Brain()
    if args.all:
        for target in brain.targets():
            make(f"Talk to {brain.name(target)}", target)
        return
    target = brain.resolve(args.to) if args.to else "claude"
    if not target:
        sys.exit(f"unknown thread {args.to!r}; choose from: " + ", ".join(brain.targets()))
    # Peter 2026-10-25: every Shortcut names its thread, Claude included; an unpinned "Talk to Claude"
    # followed whoever was last picked by voice (Gemini, for days).
    make(args.name or f"Talk to {brain.name(target)}", target)


if __name__ == "__main__":
    main()
