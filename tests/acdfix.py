"""A whole fake world for the Agent Control Desk tests, in a throw-away folder. Nothing here touches Peter's
real settings, logs, windows or the network.

    w = make_world()                       # w.paths (acd_data.Paths), w.root, w.now
    add_agent(w, "assistant", model="claude-opus-5-5", service="anthropic", backups=[("gemini-3.8-flash", "agy")])
    log(w, "assistant", turn(w.now - 60, "s1", "claude-opus-5-5", "anthropic"))

The fake settings files are written as JSON (which is also valid YAML) and read by `loader`, so the tests need
neither a YAML library nor Hermes on the machine.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import tests  # noqa: F401  (the fake no-window herdr and throw-away state folder)

import acd_data as D

NOW = dt.datetime(2026, 11, 16, 12, 0, 0).timestamp()
AGY = "antigravity-subscription-directsdk"

AGENTS_TOML = '''\
[shared]
greetings = ["hello"]
whisper_prompt = "test"

[agents.claude]
target = "claude"
display = "Claude"
names = ["claude", "clod"]
voice = "af_heart"
voice_zh = "zh-TW-HsiaoChenNeural"

[agents.jarvis]
profile = "orchestrator"
display = "Jarvis"
names = ["jarvis"]
voice = "am_puck"
voice_zh = "zh-TW-HsiaoChenNeural"
english_only = true
speed = 1.2

[agents.kylie]
profile = "assistant"
display = "Kylie"
names = ["kylie"]
voice = "en-US-AvaNeural"
voice_zh = "zh-TW-HsiaoChenNeural"
speed = 1.0

[agents.simba]
profile = "simba"
display = "Simba"
names = ["simba"]
voice = "am_fenrir"
english_only = true
'''

RULES_TOML = '''\
ignore = ["default"]
order = ["orchestrator", "assistant", "builder", "simba"]

[defaults]
speaks_in_team_room = true
hands_out_work = false
takes_work_from = "orchestrator"
only_job_cards = false
answers_when_named = false
model_locked = false

[agents.orchestrator]
role = "the manager"
hands_out_work = true
takes_work_from = ""

[agents.builder]
role = "the builder"
only_job_cards = true

[agents.simba]
answers_when_named = true
model_locked = true
takes_work_from = ""
'''


def loader(path: Path):
    """The tests' stand-in for Hermes' own settings reader: the fake files are JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"cannot parse {Path(path).name}: {e}") from e


def make_world(members=("orchestrator", "assistant", "builder", "simba"), lead="orchestrator") -> SimpleNamespace:
    root = Path(tempfile.mkdtemp(prefix="acd-test-"))
    profiles = root / "profiles"
    profiles.mkdir()
    (root / "agents.toml").write_text(AGENTS_TOML, encoding="utf-8")
    (root / "rules.toml").write_text(RULES_TOML, encoding="utf-8")
    (root / "shortcuts").mkdir()
    if members is not None:
        (root / "members.json").write_text(json.dumps({
            "lead": lead, "saved": "2026-11-14T17:15:14+08:00",
            "members": [{"profile": p, "name": p.title()} for p in members]}), encoding="utf-8")
    paths = D.Paths(profiles=profiles, agents_toml=root / "agents.toml", members=root / "members.json",
                    rules=root / "rules.toml", shortcuts=root / "shortcuts")
    return SimpleNamespace(root=root, paths=paths, now=NOW)


def add_agent(w, profile: str, model: str = "claude-opus-5-5", service: str = "anthropic", effort="medium",
              backups=(), addons=("team-room-telegram",), raw: str = "") -> Path:
    folder = w.paths.profiles / profile
    (folder / "logs").mkdir(parents=True, exist_ok=True)
    cfg = {"model": {"default": model, "provider": service}, "agent": {"reasoning_effort": effort},
           "fallback_providers": [{"model": m, "provider": s} for m, s in backups],
           "plugins": {"enabled": list(addons)}}
    (folder / "config.yaml").write_text(raw or json.dumps(cfg), encoding="utf-8")
    return folder


def stamp(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") + ",000"


def turn(ts: float, session: str, model: str, service: str, platform: str = "cli", history: int = 0) -> str:
    return (f"{stamp(ts)} INFO [{session}] agent.turn_context: conversation turn: session={session} model={model} "
            f"provider={service} platform={platform} history={history} msg='hello'")


def failed(ts: float, session: str, model: str, service: str, summary: str) -> str:
    return (f"{stamp(ts)} WARNING [{session}] agent.conversation_loop: API call failed (attempt 1/3) "
            f"error_type=RuntimeError thread=t provider={service} base_url=x model={model} summary={summary}")


def fallback(ts: float, session: str, old: str, new: str, service: str) -> str:
    return (f"{stamp(ts)} INFO [{session}] agent.chat_completion_helpers: Fallback activated: "
            f"{old} → {new} ({service})")


def signin_failed(ts: float, to: str = f"{AGY}/gemini-3.8-flash") -> str:
    return (f"{stamp(ts)} WARNING hermes_cli.runtime_provider: Primary provider auth failed (No Anthropic "
            f"credentials found. Run 'hermes auth add anthropic' to sign in.). Falling back to {to}")


def log(w, profile: str, *lines: str) -> None:
    path = w.paths.profiles / profile / "logs" / "agent.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("".join(line + "\n" for line in lines))


def collect(w, reader=None) -> D.Snapshot:
    return D.collect(w.paths, reader=reader, yaml_loader=loader, now=w.now)


def agent(snap: D.Snapshot, profile: str) -> D.AgentView:
    return next(v for v in snap.agents if v.profile == profile)
