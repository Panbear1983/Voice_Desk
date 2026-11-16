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
answers = "when_named"
answers_locked = true
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


# ---- a world whose settings files are real YAML text, for the tests that CHANGE things (version two) ---------
def edit_world(armed: bool = False, launcher: bool = True):
    """gridfix's fake world (agents.toml, four profiles with a voice block, Claude and Gemini voice files), with
    every profile's settings file rewritten as full YAML text, plus an acd_edit.EditPaths that points only into
    the throw-away folder. armed=True switches the desk's own armed file on."""
    import acd_edit as E
    import voicecfg
    from tests import gridfix as G

    g = G.make_world()
    profiles = g.edit.profiles
    spec = {"orchestrator": ("claude-sonnet-5-5", "anthropic", "medium", "en-US-JennyNeural"),
            "assistant": ("claude-opus-5-5", "anthropic", "medium", "en-US-AvaNeural"),
            "tax_angel_esther": ("claude-opus-5-5", "anthropic", "medium", "alloy"),
            "wanna-buffet": ("claude-opus-5-5", "anthropic", "medium", "en-US-AndrewNeural")}
    for prof, (model, service, effort, voice) in spec.items():
        tts = G.profile_yaml(voice, speed=0.92 if prof == "wanna-buffet" else 1.0).split("\n", 1)[1]
        (profiles / prof / "config.yaml").write_text(config_yaml(model, service, effort) + tts, encoding="utf-8")
    (profiles / "simba" / "logs").mkdir(parents=True)
    (profiles / "simba" / "config.yaml").write_text(
        "model:\n  provider: openrouter\n  default: x-ai/grok-4.20\nfallback_providers: []\n"
        "agent:\n  reasoning_effort: none\nplugins:\n  enabled:\n    - team-room-telegram\n", encoding="utf-8")
    rules = g.root / "rules.toml"
    rules.write_text(RULES_TOML.replace('order = ["orchestrator", "assistant", "builder", "simba"]',
                                        'order = ["orchestrator", "assistant", "tax_angel_esther", "wanna-buffet", "simba"]')
                     + '\n[agents.tax_angel_esther]\nrole = "tax"\nmodel_note = "meant to stay fixed on Claude Opus 5.5"\n',
                     encoding="utf-8")
    (g.root / "members.json").write_text(json.dumps({
        "generation": 4, "room_id": "team-4", "lead": "orchestrator", "saved": "2026-11-14T17:15:14+08:00",
        "members": [{"profile": p, "handle": ROOM_HANDLE.get(p, p), "name": ROOM_NAME.get(p, p.title())}
                    for p in list(spec) + ["simba"]]}, indent=1), encoding="utf-8")
    (g.root / "gateway_state.json").write_text(json.dumps({"platforms": {
        "assistant:telegram": {"state": "connected"}, "orchestrator:telegram": {"state": "connected"}}}), encoding="utf-8")
    paths = D.Paths(profiles=profiles, agents_toml=g.toml, members=g.root / "members.json", rules=rules,
                    shortcuts=g.root / "repo", gateway_state=g.root / "gateway_state.json")
    edit = E.EditPaths(state=g.root / "acd-state", profiles=profiles, rules=rules,
                       restart_request=g.root / "helper" / "gateway_restart.request",
                       config_backups=g.root / "config-backups", voice=g.edit, launcher_armed=launcher,
                       members=g.root / "members.json", team_room_cmd=g.root / "no-such-team-room-command",
                       gateway_state=g.root / "gateway_state.json")
    if armed:
        E.set_armed(edit, True)
    return SimpleNamespace(root=g.root, paths=paths, edit=edit, now=NOW, profiles=profiles, toml=g.toml)


ROOM_HANDLE = {"orchestrator": "jarvis", "assistant": "kylie", "tax_angel_esther": "esther"}
ROOM_NAME = {"orchestrator": "Jarvis", "assistant": "Kylie", "tax_angel_esther": "Esther", "wanna-buffet": "Wanna Buffet",
             "simba": "Simba"}
ADDON_LINE = "    - team-room-telegram\n"


class FakeTeamRoom:
    """A stand-in for the Team Room's own command, working only on the throw-away world. It does what the real
    add / kick does to the two doors: the saved member list (the terminal room) and, for an agent with its own
    bot, the team-room add-on in that agent's settings (the Telegram side).

        wire=False     the member list changes but the Telegram side does not follow (the two doors disagree)
        fail=True      the room cannot be made: nothing changes at all
        says="..."     extra words the command prints (for example Hermes' "restart" sentence)
    """

    def __init__(self, w, wire: bool = True, fail: bool = False, says: str = "") -> None:
        self.w, self.wire, self.fail, self.says = w, wire, fail, says
        self.calls: list[list] = []

    def has_bot(self, profile: str) -> bool:
        plats = json.loads(self.w.edit.gateway_state.read_text(encoding="utf-8"))["platforms"]
        return f"{profile}:telegram" in plats

    def __call__(self, words: list) -> tuple:
        self.calls.append(list(words))
        if self.fail:
            return 4, "team-room: the gateway refused: the new room could not be made"
        path = self.w.edit.members
        data = json.loads(path.read_text(encoding="utf-8"))
        if words[0] == "add":
            profile, handle, name = words[1], words[2], words[3]
            data["members"].append({"profile": profile, "handle": handle, "name": name})
            joining = True
        else:
            hit = next(m for m in data["members"] if m["handle"] == words[1])
            profile = hit["profile"]
            data["members"].remove(hit)
            joining = False
        data["generation"] += 1
        data["room_id"] = f"team-{data['generation']}"
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        if self.wire and self.has_bot(profile):
            cfg = self.w.profiles / profile / "config.yaml"
            text = cfg.read_text(encoding="utf-8")
            if joining and ADDON_LINE not in text:
                text = text.replace("  enabled:\n", "  enabled:\n" + ADDON_LINE, 1)
            elif not joining:
                text = text.replace(ADDON_LINE, "", 1)
            cfg.write_text(text, encoding="utf-8")
        return 0, f"done: {'add' if joining else 'remove'} {profile}. New room {data['room_id']}. {self.says}"


def config_yaml(model: str, service: str, effort: str) -> str:
    return (f"model:\n  provider: {service}\n  default: {model}   # the main model\n  base_url: ''\n"
            "# a comment that must stay\n"
            "fallback_providers:\n"
            "  # first choice\n"
            "  - provider: antigravity-subscription-directsdk\n    model: claude-opus-5-5\n"
            "  - provider: antigravity-subscription-directsdk\n    model: gemini-3.8-flash\n"
            "  - provider: nvidia\n    model: nvidia/nemotron-3.5-lightning-30b-a3b   # last resort\n"
            f"agent:\n  reasoning_effort: {effort}\n"
            "plugins:\n  enabled:\n    - team-room-telegram\n")


def lines_loader(path: Path):
    """The tests' stand-in for Hermes' own reader on YAML text: the three managed things plus the add-ons,
    read by lines. A file with the word BROKEN in it cannot be parsed."""
    import acd_edit as E
    import voicecfg
    text = Path(path).read_text(encoding="utf-8")
    if "BROKEN" in text:
        raise ValueError(f"cannot parse {Path(path).name}")
    data = E.managed(text)
    e = voicecfg.scan(text)
    enabled = voicecfg._block_list(text, e["plugins.enabled"]) if "plugins.enabled" in e else []
    data["plugins"] = {"enabled": enabled or []}
    return data


def edit_collect(w, reader=None):
    import acd_edit as E
    return D.collect(w.paths, reader=reader, yaml_loader=lines_loader, now=w.now, desk=E.record(w.edit))


def tree_hashes(root: Path) -> dict:
    """A fingerprint of every file under a folder (to prove that a dry run wrote nothing)."""
    import hashlib
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}
