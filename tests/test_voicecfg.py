"""Offline tests for agents.toml (the one file that owns every agent's voice settings) and
voicecfg (check / sync). No microphone, no speakers, no live file is written.

  .venv/bin/python tests/test_voicecfg.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VOICE_DESK_STATE", tempfile.mkdtemp(prefix="vd-cfg-test-"))

ok = True


def report(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f"  {detail}" if detail and not passed else ""))


# Day one: agents.toml must hold exactly what the code held on 2026-10-22 (commit f3cbe43).
DAY_ONE_NAMES = {
    "claude": ("Claude", ("claude", "claud", "clod", "claude code", "克勞德", "克劳德")),
    "gemini": ("Gemini", ("gemini", "jemini", "google gemini")),
    "hermes:orchestrator": ("Jarvis", ("jarvis", "javis", "jaarvis", "javas", "jervis", "jarvas", "orchestrator")),
    "hermes:assistant": ("Kylie", ("kylie", "kiley", "kylee", "kyley", "kailey", "kaylee", "kayley", "kaley",
                                   "kyli", "kylie's", "assistant")),
    "hermes:tax_angel_esther": ("Esther", ("esther", "ester", "tax angel esther", "tax angel")),
    "hermes:wanna-buffet": ("Wanna Buffet", ("wanna buffet", "wanna buffett", "wannabuffet", "wana buffet",
                                             "wanna buff it", "warren buffett", "warren buffet")),
}
DAY_ONE_GREETINGS = ["ok", "okay", "hey", "hay", "heya", "hi", "high", "hai", "hiya", "hello", "hallo", "hullo",
                     "helo", "yo"]
DAY_ONE_VOICES = {   # target -> the voice written in its Hermes profile today
    "hermes:orchestrator": "en-US-JennyNeural",
    "hermes:assistant": "en-US-AvaNeural",
    "hermes:tax_angel_esther": "alloy",
    "hermes:wanna-buffet": "en-US-AndrewNeural",
}
DAY_ONE_WHISPER_PROMPT = "Jarvis, Kylie, Esther, Wanna Buffet, Claude, Gemini. Mute, unmute."
DAY_ONE_MIC = {   # settings.py lines 25-34 on 2026-10-22
    "PRE_ROLL_SEC": 0.6, "SPEECH_START_SEC": 0.25, "SILENCE_SEND_SEC": 1.5, "MAX_UTTERANCE_SEC": 90,
    "MIN_SPEECH_SEC": 0.4, "NOISE_FLOOR_START": 150.0, "SPEECH_OVER_FLOOR": 3.0, "MIN_SPEECH_RMS": 350.0,
    "BARGE_IN_OVER_FLOOR": 4.0, "BARGE_IN_SEC": 0.5,
}


# 2 ---------------------------------------------------------------------------------------------
def test_agents_toml_day_one() -> None:
    import agents
    cfg = agents.load()
    report("agents.toml: names and spellings are today's, in today's order",
           agents.names(cfg) == DAY_ONE_NAMES, str(agents.names(cfg))[:300])
    report("agents.toml: greetings are today's", agents.greetings(cfg) == DAY_ONE_GREETINGS, str(agents.greetings(cfg)))
    got = {a.target: a.voice for a in agents.agents(cfg) if a.target.startswith("hermes:")}
    report("agents.toml: each Hermes agent's voice is the one in its profile today", got == DAY_ONE_VOICES, str(got))
    report("agents.toml: whisper hint words are today's", agents.whisper_prompt(cfg) == DAY_ONE_WHISPER_PROMPT)
    report("agents.toml: [shared.mic] holds today's mic sensitivity", agents.mic(cfg) == DAY_ONE_MIC, str(agents.mic(cfg)))


if __name__ == "__main__":
    test_agents_toml_day_one()
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
