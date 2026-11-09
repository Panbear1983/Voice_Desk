"""Shared fixtures for the voice-grid tests: a whole fake world in a temp folder. Nothing here touches
Peter's real files, windows or logs.

    world = make_world()        # .paths (gridstate.Paths), .edit (voiceedit.EditPaths), .toml, .root
    log(world, "assistant", "agent.log", ts, "gateway.run: response ready: platform=telegram chat=7512954760 ...")
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import tests  # noqa: F401  (the fake no-window herdr, also when a test file is run directly)

import gridstate as G
import voiceedit

AGENTS_TOML = '''\
[shared]
greetings = ["hello"]
whisper_prompt = "Jarvis, Kylie."
voice = "en-US-JennyNeural"
voice_zh = "zh-TW-HsiaoChenNeural"

[shared.end_phrases]
always = ["stop"]

[agents.claude]
target = "claude"
display = "Claude"
names = ["claude"]
voice = "en-US-JennyNeural"          # Claude Code's spoken replies (NEURAL_VOICE_EN)
voice_zh = "zh-TW-HsiaoChenNeural"   # NEURAL_VOICE_ZH
always_on = true
mode = "tap"

[agents.gemini]
target = "gemini"
display = "Gemini"
names = ["gemini"]

[agents.codex]
target = "codex"
display = "Codex"
names = ["codex"]

[agents.jarvis]
profile = "orchestrator"
display = "Jarvis"
names = ["jarvis"]
voice = "en-US-JennyNeural"
voice_zh = "zh-TW-HsiaoChenNeural"
speed = 1.0
auto_tts = true
silence_duration = 1.5
silence_threshold = 200
barge_in = false

[agents.kylie]
profile = "assistant"
display = "Kylie"
names = ["kylie"]
voice = "en-US-AvaNeural"            # Kylie's English voice
voice_zh = "zh-TW-HsiaoChenNeural"
speed = 1.0
auto_tts = true
silence_duration = 1.5
silence_threshold = 200
barge_in = false
quiet_hours = { tg_dm = "22:00-07:00" }

[agents.esther]
profile = "tax_angel_esther"
display = "Esther"
names = ["esther"]
voice = "alloy"                      # her Kokoro voice
voice_zh = "zh-TW-HsiaoYuNeural"
command = ["/nonexistent/python",
           "/nonexistent/esther_tts.py"]
fallback = "en-US-AriaNeural"
auto_tts = true
silence_duration = 1.5
barge_in = false

[agents.wanna_buffet]
profile = "wanna-buffet"
display = "Wanna Buffet"
names = ["wanna buffet"]
voice = "en-US-AndrewNeural"
voice_zh = "zh-TW-YunJheNeural"
speed = 0.92
auto_tts = true
silence_threshold = 500
barge_in = false
'''


def profile_yaml(voice: str, *, auto_enable: bool = True, auto_tts: bool = True, threshold: int = 200,
                 speed: float | None = 1.0) -> str:
    return (f"model: x\ntts:\n  provider: natural\n  providers:\n    natural:\n      type: command\n"
            f"      voice: {voice}\n      model: edge google mac\n"
            + (f"  edge:\n    voice: {voice}\n    speed: {speed}\n" if speed is not None else "")
            + f"voice:\n  auto_tts: {'true' if auto_tts else 'false'}\n"
            + (f"  auto_enable: true\n" if auto_enable else "")
            + f"  silence_duration: 1.5\n  silence_threshold: {threshold}\n  barge_in: false\n"
            + '  stop_phrases: ["stop"]\n')


def make_world(*, group_on: bool = True, dm_modes: dict | None = None) -> SimpleNamespace:
    root = Path(tempfile.mkdtemp(prefix="vd-grid-"))
    profiles = root / "hermes/profiles"
    for prof, voice, thr in (("orchestrator", "en-US-JennyNeural", 200), ("assistant", "en-US-AvaNeural", 300),
                             ("tax_angel_esther", "alloy", 200), ("wanna-buffet", "en-US-AndrewNeural", 500)):
        (profiles / prof / "logs").mkdir(parents=True)
        (profiles / prof / "config.yaml").write_text(profile_yaml(voice, threshold=thr, speed=0.92 if prof == "wanna-buffet" else 1.0))
    mirror = profiles / "orchestrator/scripts/team_room/state/mirror_config.json"
    mirror.parent.mkdir(parents=True)
    mirror.write_text(json.dumps({"chat_id": "-1", "enabled": True, "voice_notes": {
        "on": group_on, "agents": ["orchestrator", "assistant", "tax_angel_esther", "wanna-buffet"]}}))
    if dm_modes is not None:
        (root / "hermes/gateway_voice_mode.json").write_text(json.dumps(dm_modes))
    claude = root / "claude"
    (claude / "hooks/state").mkdir(parents=True)
    (claude / "hooks/speak-response.sh").write_text("#!/bin/bash\n")
    (claude / "settings.json").write_text(json.dumps({"voice": {"enabled": True, "mode": "tap"}}))
    (claude / "voice-defaults.conf").write_text(
        'NEURAL_VOICE_EN="en-US-JennyNeural"        # English\nNEURAL_VOICE_ZH="zh-TW-HsiaoChenNeural"\n'
        'VOICE_ALWAYS_ON=1                          # on\nVOICE_MODE="tap"\n')
    gemini = root / "gemini"
    (gemini / "config/hooks/audio_cache").mkdir(parents=True)
    (gemini / "config/hooks/speak-response.py").write_text("# hook\n")
    state = root / "state"
    (state / "speech").mkdir(parents=True)
    repo = root / "repo"
    repo.mkdir()
    for name in ("Jarvis", "Kylie", "Esther", "Wanna Buffet", "Claude", "Gemini", "Codex"):
        (repo / f"Talk to {name}.shortcut").write_text("")           # only the NAME is ever looked at
    toml = root / "agents.toml"
    toml.write_text(AGENTS_TOML, encoding="utf-8")
    paths = G.Paths(hermes_home=root / "hermes", profiles=profiles, claude_dir=claude, gemini_dir=gemini, state=state,
                    mirror_config=mirror, repo=repo, receiver_port=1)           # port 1: nothing listens
    edit = voiceedit.EditPaths(toml=toml, history=state / "voice_history.jsonl", toml_backups=state / "voice_history_backups",
                               hermes_backups=root / "profile-backups", profiles=profiles, claude_conf=claude / "voice-defaults.conf")
    return SimpleNamespace(root=root, paths=paths, edit=edit, toml=toml)


def stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) + ",123"


def log(world, profile: str, name: str, ts: float, text: str) -> None:
    with open(world.paths.profiles / profile / "logs" / name, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp(ts)} {text}\n")


CHAT = "7512954760"


def dm_reply(world, profile: str, ts: float, chat: str = CHAT) -> None:
    for name in ("gateway.log", "agent.log"):
        log(world, profile, name, ts, f"INFO gateway.run: response ready: platform=telegram chat={chat} "
            f"session=agent:{profile}:telegram:dm:{chat} time=9.0s api_calls=2 response=300 chars")
    log(world, profile, "gateway.log", ts + 4, f"INFO gateway.platforms.base: [Telegram] Sending response (300 chars) to {chat}")


def dm_voice(world, profile: str, ts: float, provider: str = "natural") -> None:
    log(world, profile, "agent.log", ts, "INFO tools.tts_tool: Generating speech with command TTS provider 'natural'...")
    log(world, profile, "agent.log", ts + 2, f"INFO tools.tts_tool: TTS audio saved: /var/T/hermes_voice/tts_reply_abc123.ogg "
        f"(154,521 bytes, provider: {provider})")


def team_note(world, profile: str, ts: float) -> None:
    log(world, profile, "agent.log", ts, "INFO tools.tts_tool: Generating speech with command TTS provider 'natural'...")
    log(world, profile, "agent.log", ts + 2, f"INFO tools.tts_tool: TTS audio saved: {world.root}/cache/audio/team_room_voice_9f.ogg "
        "(18,000 bytes, provider: natural)")
