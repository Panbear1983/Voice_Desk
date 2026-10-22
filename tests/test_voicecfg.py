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
# a fake Hermes profiles dir with the four profile names, so routing does not depend on this Mac
_profiles = Path(tempfile.mkdtemp(prefix="vd-cfg-profiles-"))
for _p in ("assistant", "orchestrator", "tax_angel_esther", "wanna-buffet"):
    (_profiles / _p).mkdir()
    (_profiles / _p / "config.yaml").write_text("model: x\n")
os.environ["HERMES_PROFILES_DIR"] = str(_profiles)

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


def _edited_copy(edit) -> Path:
    """A copy of agents.toml with `edit(text) -> text` applied, in a temp dir."""
    import agents
    p = Path(tempfile.mkdtemp(prefix="vd-agents-")) / "agents.toml"
    p.write_text(edit(agents.PATH.read_text(encoding="utf-8")), encoding="utf-8")
    return p


def _run_with(toml: Path, code: str) -> str:
    import subprocess
    env = dict(os.environ, VOICE_DESK_AGENTS=str(toml), VOICE_DESK_STATE=tempfile.mkdtemp(prefix="vd-cfg-sub-"))
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).strip()


# 3 ---------------------------------------------------------------------------------------------
def test_settings_read_agents_toml() -> None:
    import settings as S
    import brain as B
    report("settings: NAMES comes out exactly as before", S.NAMES == DAY_ONE_NAMES)
    report("settings: WHISPER_PROMPT comes out exactly as before", S.WHISPER_PROMPT == DAY_ONE_WHISPER_PROMPT)
    report("settings: mic constants come out exactly as before",
           all(getattr(S, k) == v for k, v in DAY_ONE_MIC.items()), str({k: getattr(S, k) for k in DAY_ONE_MIC}))
    report("brain: the greeting pattern is today's",
           B._GREET == r"(?:ok|okay|hey|hay|heya|hi|high|hai|hiya|hello|hallo|hullo|helo|yo)", B._GREET)
    toml = _edited_copy(lambda t: t.replace('"helo", "yo"]', '"helo", "yo", "howdy"]')
                        .replace('names = ["jarvis",', 'names = ["jarvie", "jarvis",')
                        .replace("silence_send_sec = 1.5", "silence_send_sec = 2.0")
                        .replace('whisper_prompt = "Jarvis,', 'whisper_prompt = "Jarvie, Jarvis,'))
    out = _run_with(toml, "import settings as S, brain as B; "
                          "print(B.addressed('howdy Jarvis, what time is it', hailed=True)); "
                          "print(B.addressed('hey jarvie', hailed=True)); print(S.SILENCE_SEND_SEC); print(S.WHISPER_PROMPT[:7])")
    report("settings: an edited agents.toml changes greetings, names, mic and hint words (no code edit)",
           out.splitlines() == ["('hermes:orchestrator', 'what time is it')", "('hermes:orchestrator', '')", "2.0", "Jarvie,"],
           out)


# 4 ---------------------------------------------------------------------------------------------
def test_voices_from_agents_toml() -> None:
    import agents
    import voices
    voices.set_per_profile(True)
    tbl = {name: v for name, v in voices.table()}
    want = {"Jarvis": ("en-US-JennyNeural", "zh-TW-HsiaoChenNeural"), "Kylie": ("en-US-AvaNeural", "zh-TW-HsiaoChenNeural"),
            "Esther": ("alloy", "zh-TW-HsiaoYuNeural"), "Wanna Buffet": ("en-US-AndrewNeural", "zh-TW-YunJheNeural")}
    report("voices: table() gives today's four voices, read from agents.toml",
           {n: (v.en, v.zh) for n, v in tbl.items()} == want and all(v.source == "agents" for v in tbl.values()),
           str(tbl))
    report("voices: the profile config.yaml no longer decides (fake profiles here carry no voice)",
           voices.voice_for("hermes:assistant").en == "en-US-AvaNeural")
    report("voices: Claude and Gemini keep the shared voice", voices.voice_for("claude") == voices.shared_voice()
           and voices.shared_voice().en == "en-US-JennyNeural" and voices.shared_voice().zh == "zh-TW-HsiaoChenNeural")
    real = agents.PATH
    agents.PATH = _edited_copy(lambda t: t.replace('voice = "en-US-AvaNeural"', 'voice = "en-US-EmmaNeural"'))
    try:
        got = voices.voice_for("hermes:assistant").en
    finally:
        agents.PATH = real
    report("voices: a voice changed in agents.toml is used for the next line, no restart", got == "en-US-EmmaNeural", got)
    voices.voice_for("hermes:assistant")                                                       # last good = the real file
    agents.PATH = _edited_copy(lambda t: t.replace('[agents.kylie]', '[agents.kylie\n'))   # a typo mid-edit
    try:
        got = voices.voice_for("hermes:assistant").en
    finally:
        agents.PATH = real
    report("voices: a broken agents.toml mid-edit keeps the last good voices (never silent)", got == "en-US-AvaNeural", got)


# 4b --------------------------------------------------------------------------------------------
def test_esther_speaks_through_kokoro() -> None:
    import tts
    import voices
    esther = voices.voice_for("hermes:tax_angel_esther")
    report("esther: her voice is Kokoro 'alloy' through her own script, Aria only as the fallback",
           esther.en == "alloy" and esther.command and esther.command[-1].endswith("tax_angel_esther/scripts/esther_tts.py")
           and esther.fallback == "en-US-AriaNeural", str(esther))
    d = Path(tempfile.mkdtemp(prefix="vd-kokoro-"))
    good = d / "good.sh"
    good.write_text('#!/bin/sh\nprintf "RIFF%s" "$3" > "$2"\n')       # writes the voice it was asked for
    bad = d / "bad.sh"
    bad.write_text("#!/bin/sh\nexit 1\n")
    for f in (good, bad):
        f.chmod(0o755)
    chained: list = []
    real_chain = tts._chain
    tts._chain = lambda txt, out, voice: (chained.append(voice), out.write_bytes(b"RIFFedge"), out)[2]
    try:
        p = tts.synthesize("Your return is filed.", esther._replace(command=(str(good),)))
        report("esther: an English line is made by her Kokoro script, with her voice name",
               p is not None and p.read_bytes() == b"RIFFalloy" and p.suffix == ".wav" and chained == [],
               f"{p} {p.read_bytes() if p else None} {chained}")
        p = tts.synthesize("Your return is filed.", esther._replace(command=(str(bad),)))
        report("esther: Kokoro failing -> the Aria fallback speaks, never silence",
               p is not None and chained == ["en-US-AriaNeural"], str(chained))
        chained.clear()
        p = tts.synthesize("你的報稅已經送出了。", esther._replace(command=(str(good),)))
        report("esther: a Mandarin line uses her Mandarin Edge voice (Kokoro is English only)",
               p is not None and chained == ["zh-TW-HsiaoYuNeural"], str(chained))
    finally:
        tts._chain = real_chain


# 5 ---------------------------------------------------------------------------------------------
# voice.stop_phrases as written in all four profiles on 2026-10-22 (identical in each)
_S = ["that's", "that is", "that’s"]
_LEADS = ["", "oh, ", "okay, ", "ok, ", "thanks, ", "thank you, "]
DAY_ONE_STOP = (["stop"] + [f"{l}{s} all" for l in _LEADS for s in _S]
                + [f"{l}{s} all the questions i have" for l in _LEADS for s in _S]
                + [f"{l}{s} all the question i have" for l in _LEADS for s in _S]
                + [f"{l}no further questions" for l in _LEADS] + [f"{l}no further question" for l in _LEADS])


def test_end_phrases() -> None:
    import agents
    got = agents.end_phrases(agents.load())
    report("end phrases: 67 of them (66 variants + 'stop'), no duplicates", len(got) == 67 == len(set(got)), str(len(got)))
    report("end phrases: exactly the strings in today's profiles", set(got) == set(DAY_ONE_STOP),
           f"missing {set(DAY_ONE_STOP) - set(got)} extra {set(got) - set(DAY_ONE_STOP)}")
    report("end phrases: the curly phone apostrophe is there", "thank you, that’s all" in got)
    report("end phrases: 'stop' first", got[0] == "stop")


# 6 ---------------------------------------------------------------------------------------------
import json as _json  # noqa: E402

_STOP = _json.dumps(DAY_ONE_STOP, ensure_ascii=False)
# The shapes of the four live config.yaml files on 2026-10-22 (voice parts only, no secrets),
# including the awkward bits: column-0 comments INSIDE a block, a sub-block after a blank line,
# several providers of which only one is active.
FIXTURES = {
    "orchestrator": f"""model:
  default: x
display:
  skin: orchestrator   # per-profile skin
voice:
  # Peter 2026-10-21: end phrases hand the mic back to Voice Desk (exact sentence; listed variants).
  stop_phrases: {_STOP}
  auto_tts: true
  name: natural
  silence_duration: 1.5
  silence_threshold: 200
  barge_in: false
# a comment between blocks
moa:
  presets:
    default:
      enabled: false
tts:
  # To go back to plain Edge: set provider back to edge.
  provider: jenny_only
  edge:
    voice: en-US-JennyNeural
    speed: 1.0
  providers:
    natural:   # shared script for every profile
      type: command
      voice: en-US-JennyNeural
      model: edge google mac
    jenny_only:
      type: command
      command: /x/tts_jenny_only.sh {{input_path}} {{output_path}} {{voice}}
      voice: en-US-JennyNeural
      output_format: mp3
      timeout: 90
mcp_servers:
  playwright:
    command: node
""",
    "assistant": f"""agent:
  reasoning_effort: high
tts:
  # comment
  provider: natural
  providers:
    natural:
      type: command
      voice: en-US-AvaNeural
      model: edge google mac
      voice_compatible: true   # Telegram voice bubbles need Ogg/Opus

# ── Security ──────────────────────────────────────────────────────────
# security:
#   redact_secrets: true
  edge:
    voice: en-US-AvaNeural
    speed: 1.0
voice:
  # Peter 2026-10-21: end phrases hand the mic back to Voice Desk (exact sentence; listed variants).
  stop_phrases: {_STOP}
  auto_tts: true
  silence_duration: 1.5
  silence_threshold: 200
  barge_in: false
  name: natural
cron:
  wrap_response: false
""",
    "tax_angel_esther": f"""onboarding:
  seen:
    tool_progress_prompt: true
tts:
  providers:
    esther:
      type: command
      command: /x/python /x/esther_tts.py {{input_path}} {{output_path}} {{voice}}
      voice: alloy
      output_format: ogg
      # long replies come back in short pieces
      max_text_length: 700
      timeout: 300
  provider: esther
  # Spoken pieces of at least ~2 sentences.
  streaming:
    min_len: 120
voice:
  # Peter 2026-10-21: end phrases hand the mic back to Voice Desk (exact sentence; listed variants).
  stop_phrases: {_STOP}
  auto_tts: true
  barge_in: false
  barge_in_grace_seconds: 0.5
  silence_duration: 1.5
mcp_servers:
  mempalace:
    enabled: true
""",
    "wanna-buffet": f"""agent:
  reasoning_effort: high
tts:
  provider: natural
  providers:
    natural:
      type: command
      # male voice for Wanna Buffet
      voice: en-US-AndrewNeural
      voice_compatible: true   # true makes .ogg
      timeout: 60
  edge:
    voice: en-US-AndrewNeural
    speed: 0.92
  openai:
    model: gpt-4o-mini-tts
    voice: onyx
voice:
  # Peter 2026-10-21: end phrases hand the mic back to Voice Desk (exact sentence; listed variants).
  stop_phrases: {_STOP}
  auto_tts: true
  barge_in: false
# 2026-09-23 lock-down: a column-0 comment in the middle of the voice block
# (Hermes still reads the next line as voice.silence_threshold).
  silence_threshold: 500
command_allowlist:
  - delete in root path
""",
}
CLAUDE_CONF = """# Peter's spoken-reply defaults (chosen by ear 2026-09-17). THE place to change voices.
NEURAL_VOICE_EN="en-US-JennyNeural"        # English: Jenny
NEURAL_VOICE_ZH="zh-TW-HsiaoChenNeural"    # Chinese: HsiaoChen, Taiwan Mandarin
VOICE_ALWAYS_ON=1                          # 1 = every Claude Code session starts with voice on
VOICE_MODE="hold"                          # push-to-talk mode restored if missing
"""
LIVE_PROFILES = Path.home() / ".hermes/profiles"
LIVE_CLAUDE_CONF = Path.home() / ".claude/voice-defaults.conf"
HERMES_PY = Path.home() / ".hermes/hermes-agent/venv/bin/python"


def make_fixtures() -> tuple[Path, Path]:
    """A profiles dir and a Claude conf in a temp dir, shaped like today's live files."""
    d = Path(tempfile.mkdtemp(prefix="vd-cfg-fix-"))
    for p, text in FIXTURES.items():
        (d / "profiles" / p).mkdir(parents=True)
        (d / "profiles" / p / "config.yaml").write_text(text, encoding="utf-8")
        (d / "profiles" / p / "config.yaml").chmod(0o600)
    (d / "voice-defaults.conf").write_text(CLAUDE_CONF, encoding="utf-8")
    return d / "profiles", d / "voice-defaults.conf"


def test_check() -> None:
    import agents
    import voicecfg as V
    cfg = agents.load()
    profiles, conf = make_fixtures()
    items = V.collect(cfg, profiles, conf)
    keys = {(i.where, i.key) for i in items}
    report("check: covers each agent's active voice, speed, end phrases and knobs, and Claude's file",
           {("kylie", "tts.providers.natural.voice"), ("jarvis", "tts.providers.jenny_only.voice"),
            ("esther", "tts.providers.esther.voice"), ("wanna_buffet", "tts.edge.speed"),
            ("wanna_buffet", "voice.silence_threshold"), ("esther", "voice.barge_in_grace_seconds"),
            ("jarvis", "voice.stop_phrases"), ("claude", "NEURAL_VOICE_EN"), ("claude", "VOICE_MODE")} <= keys,
           str(sorted(keys)))
    report("check: today's shapes -> zero drift", V.drift(items) == [], str(V.drift(items)))
    wb = next(i for i in items if (i.where, i.key) == ("wanna_buffet", "voice.silence_threshold"))
    report("check: reads a key that sits after a column-0 comment inside its block (as Hermes does)",
           wb.have == 500 and wb.line is not None, str(wb))
    ky = next(i for i in items if (i.where, i.key) == ("kylie", "tts.providers.natural.voice"))
    report("check: reads the active provider's voice, not a commented or inactive one",
           ky.have == "en-US-AvaNeural" and FIXTURES["assistant"].splitlines()[ky.line].strip() == "voice: en-US-AvaNeural")

    a = profiles / "assistant" / "config.yaml"
    a.write_text(a.read_text().replace("silence_threshold: 200", "silence_threshold: 300"))
    e = profiles / "tax_angel_esther" / "config.yaml"
    e.write_text(e.read_text().replace(', "thank you, no further question"]', "]"))
    j = profiles / "orchestrator" / "config.yaml"
    j.write_text(j.read_text().replace('stop_phrases: ["stop", "that\'s all", ', 'stop_phrases: ["that\'s all", "stop", '))
    conf.write_text(conf.read_text().replace('VOICE_MODE="hold"', 'VOICE_MODE="tap"'))
    d = {(i.where, i.key): (i.have, i.want) for i in V.drift(V.collect(cfg, profiles, conf))}
    report("check: a hand edit shows up as drift, with both values",
           d.get(("kylie", "voice.silence_threshold")) == (300, 200), str(d))
    report("check: a missing end phrase shows up", ("esther", "voice.stop_phrases") in d)
    report("check: end phrases in another order are not drift (Hermes matches them as a set)",
           ("jarvis", "voice.stop_phrases") not in d)
    report("check: Claude's voice file is checked too", d.get(("claude", "VOICE_MODE")) == ("tap", "hold"), str(d))
    report("check: nothing else is reported", len(d) == 3, str(sorted(d)))
    out = _cli(["check", "--profiles", str(profiles), "--claude-conf", str(conf)])
    report("check: the command prints each difference and exits 1 on drift",
           out[0] == 1 and "silence_threshold" in out[1] and "3 differences" in out[1], out[1][-400:])
    report("check: it changed nothing", "silence_threshold: 300" in a.read_text())

    if LIVE_PROFILES.is_dir() and LIVE_CLAUDE_CONF.exists():
        live = V.drift(V.collect(cfg, LIVE_PROFILES, LIVE_CLAUDE_CONF))
        report("check: today's LIVE profiles and Claude file match agents.toml (read-only)", live == [], str(live))
    if HERMES_PY.exists() and LIVE_PROFILES.is_dir():
        ok_all, detail = True, []
        for i in V.collect(cfg, LIVE_PROFILES, LIVE_CLAUDE_CONF):
            if i.where == "claude":
                continue
            seen = V.hermes_readback(i.path)
            hv = V.dig(seen, i.key)
            same = V.same(i.key, hv, i.have)
            ok_all &= same
            if not same:
                detail.append((i.where, i.key, hv, i.have))
        report("check: voicecfg's line reader agrees with Hermes' own YAML loader on every live key", ok_all, str(detail))


def _cli(args: list[str]) -> tuple[int, str]:
    import subprocess
    r = subprocess.run([sys.executable, "-m", "voicecfg", *args], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, VOICE_DESK_STATE=tempfile.mkdtemp(prefix="vd-cli-")))
    return r.returncode, r.stdout + r.stderr


# 7 ---------------------------------------------------------------------------------------------
def _changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    import difflib
    a, b = before.splitlines(), after.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append(("\n".join(a[i1:i2]), "\n".join(b[j1:j2])))
    return out


def _sync_case(profiles: Path, conf: Path, toml: Path, label: str) -> None:
    import voicecfg as V
    import agents
    kylie = profiles / "assistant" / "config.yaml"
    before = {p: (p / "config.yaml").read_text() for p in profiles.iterdir()}
    bdir = Path(tempfile.mkdtemp(prefix="vd-bak-"))
    rc, out = _cli(["sync", "--dry-run", "--agents", str(toml), "--profiles", str(profiles), "--claude-conf", str(conf)])
    report(f"sync {label}: --dry-run prints the line changes for Kylie only",
           rc == 0 and "-      voice: en-US-AvaNeural" in out and "+      voice: en-US-EmmaNeural" in out
           and "+    voice: en-US-EmmaNeural" in out and "1 files would change" in out, out[-600:])
    report(f"sync {label}: --dry-run wrote nothing", all((p / "config.yaml").read_text() == t for p, t in before.items()))
    rc, out = _cli(["sync", "--apply", "--agents", str(toml), "--profiles", str(profiles), "--claude-conf", str(conf),
                    "--backup-dir", str(bdir)])
    bak = bdir / "assistant-config.yaml"
    report(f"sync {label}: --apply backs up first, then writes", rc == 0 and bak.exists()
           and bak.read_text() == before[kylie.parent], out[-400:])
    changed = _changed_lines(bak.read_text(), kylie.read_text())
    report(f"sync {label}: only Kylie's voice lines changed (active provider + tts.edge), nothing else",
           changed == [("      voice: en-US-AvaNeural", "      voice: en-US-EmmaNeural"),
                       ("    voice: en-US-AvaNeural", "    voice: en-US-EmmaNeural")], str(changed))
    report(f"sync {label}: the other three profiles are untouched",
           all((p / "config.yaml").read_text() == t for p, t in before.items() if p.name != "assistant"))
    report(f"sync {label}: file permissions kept (0600)", (kylie.stat().st_mode & 0o777) == 0o600, oct(kylie.stat().st_mode))
    if HERMES_PY.exists():
        seen = V.hermes_readback(kylie)
        report(f"sync {label}: Hermes' own loader parses it and reads the new voice",
               V.dig(seen, "tts.providers.natural.voice") == "en-US-EmmaNeural" and V.dig(seen, "tts.edge.voice") == "en-US-EmmaNeural"
               and len(V.dig(seen, "voice.stop_phrases")) == 67)
    report(f"sync {label}: check afterwards -> zero drift",
           V.drift(V.collect(agents.load(toml), profiles, conf)) == [])
    rc, out = _cli(["sync", "--dry-run", "--agents", str(toml), "--profiles", str(profiles), "--claude-conf", str(conf)])
    report(f"sync {label}: a second run has nothing to do", rc == 0 and out.startswith("No changes"), out)


def test_sync() -> None:
    import shutil
    import voicecfg as V
    import agents
    emma = _edited_copy(lambda t: t.replace('voice = "en-US-AvaNeural"', 'voice = "en-US-EmmaNeural"'))
    profiles, conf = make_fixtures()
    _sync_case(profiles, conf, emma, "(fixtures)")
    if LIVE_PROFILES.is_dir():
        # copies of today's live config.yaml files, in a temp dir; the live files are only read
        d = Path(tempfile.mkdtemp(prefix="vd-live-copy-"))
        for p in ("orchestrator", "assistant", "tax_angel_esther", "wanna-buffet"):
            (d / "profiles" / p).mkdir(parents=True)
            shutil.copy2(LIVE_PROFILES / p / "config.yaml", d / "profiles" / p / "config.yaml")
        shutil.copy2(LIVE_CLAUDE_CONF, d / "voice-defaults.conf")
        try:
            _sync_case(d / "profiles", d / "voice-defaults.conf", emma, "(copies of the live files)")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # a knob a profile does not have yet is added under its block, at the block's indent
    profiles, conf = make_fixtures()
    toml = _edited_copy(lambda t: t.replace("silence_threshold = 500\n", "silence_threshold = 500\nsilence_duration = 2.0\n"))
    wb = profiles / "wanna-buffet" / "config.yaml"
    before = wb.read_text()
    rc, out = _cli(["sync", "--apply", "--agents", str(toml), "--profiles", str(profiles), "--claude-conf", str(conf),
                    "--backup-dir", tempfile.mkdtemp(prefix="vd-bak-")])
    report("sync: a missing knob is inserted as one line under voice:", rc == 0
           and _changed_lines(before, wb.read_text()) == [("", "  silence_duration: 2.0")], str(_changed_lines(before, wb.read_text())))
    if HERMES_PY.exists():
        seen = V.hermes_readback(wb)
        report("sync: ... and Hermes reads it, with the column-0 comment's key still in voice:",
               V.dig(seen, "voice.silence_duration") == 2.0 and V.dig(seen, "voice.silence_threshold") == 500)

    # end phrases: a new phrase in agents.toml rewrites the one stop_phrases line in every profile
    profiles, conf = make_fixtures()
    toml = _edited_copy(lambda t: t.replace('"no further question"]', '"no further question", "over and out"]'))
    rc, out = _cli(["sync", "--apply", "--agents", str(toml), "--profiles", str(profiles), "--claude-conf", str(conf),
                    "--backup-dir", tempfile.mkdtemp(prefix="vd-bak-")])
    got = [V.parse_value(V.scan((profiles / p / "config.yaml").read_text())["voice.stop_phrases"].value) for p in FIXTURES]
    report("sync: a new end phrase reaches all four profiles (73 phrases, with lead-ins)",
           rc == 0 and all(len(g) == 73 and "thanks, over and out" in g for g in got), out[-300:])
    report("sync: ... one line per profile", all(len(_changed_lines(FIXTURES[p], (profiles / p / "config.yaml").read_text())) == 1
                                                  for p in FIXTURES))

    # a written file that does not read back right is put back at once
    profiles, conf = make_fixtures()
    real = V.hermes_readback
    V.hermes_readback = lambda path: (_ for _ in ()).throw(ValueError("Hermes cannot parse it"))
    try:
        done, errors = V.apply(V.collect(agents.load(emma), profiles, conf), Path(tempfile.mkdtemp(prefix="vd-bak-")))
    finally:
        V.hermes_readback = real
    report("sync: if Hermes cannot parse the result, the file is restored and the error reported",
           not done and errors and "restored" in errors[0]
           and (profiles / "assistant" / "config.yaml").read_text() == FIXTURES["assistant"], str(errors))


# 7b --------------------------------------------------------------------------------------------
CLAUDE_HOOK = Path.home() / ".claude/hooks/speak-response.sh"


def test_sync_claude() -> None:
    import subprocess
    profiles, conf = make_fixtures()
    toml = _edited_copy(lambda t: t.replace('voice = "en-US-JennyNeural"          # Claude Code',
                                            'voice = "en-US-AriaNeural"          # Claude Code'))
    rc, out = _cli(["sync", "--dry-run", "--agents", str(toml), "--profiles", str(profiles), "--claude-conf", str(conf)])
    report("claude: --dry-run shows the one NEURAL_VOICE_EN line", rc == 0 and '+NEURAL_VOICE_EN="en-US-AriaNeural"' in out
           and "1 files would change" in out, out[-400:])
    bdir = Path(tempfile.mkdtemp(prefix="vd-bak-"))
    rc, out = _cli(["sync", "--apply", "--agents", str(toml), "--profiles", str(profiles), "--claude-conf", str(conf),
                    "--backup-dir", str(bdir)])
    changed = _changed_lines(CLAUDE_CONF, conf.read_text())
    report("claude: --apply backs up voice-defaults.conf and changes only that line, comment kept",
           rc == 0 and (bdir / "claude-voice-defaults.conf").read_text() == CLAUDE_CONF
           and changed == [('NEURAL_VOICE_EN="en-US-JennyNeural"        # English: Jenny',
                            'NEURAL_VOICE_EN="en-US-AriaNeural"        # English: Jenny')], str(changed))
    report("claude: the Hermes profiles are untouched",
           all((profiles / p / "config.yaml").read_text() == t for p, t in FIXTURES.items()))
    if not CLAUDE_HOOK.exists():
        report("claude: speak-response.sh not on this Mac (dry-run skipped)", True)
        return
    # the real Stop hook, dry run, in a throwaway HOME: it must pick the voice from the synced file
    home = Path(tempfile.mkdtemp(prefix="vd-home-"))
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text('{"voice": {"enabled": true}}')
    transcript = home / "t.jsonl"
    transcript.write_text("")
    payload = _json.dumps({"transcript_path": str(transcript), "session_id": "voicecfg-test",
                           "last_assistant_message": "The build passed.", "prompt_id": "p1"})
    env = {k: v for k, v in os.environ.items() if k != "VOICE_DESK_LOOP"}
    env.update(HOME=str(home), SPEAK_DRY_RUN="1", SPEAK_STATE_DIR=str(home / "state"), VOICE_DEFAULTS_CONF=str(conf))
    r = subprocess.run(["/bin/bash", str(CLAUDE_HOOK)], input=payload, capture_output=True, text=True, env=env, timeout=60)
    report("claude: a dry run of speak-response.sh speaks with the voice from agents.toml",
           "neural=en-US-AriaNeural" in r.stdout and "The build passed." in r.stdout, (r.stdout + r.stderr)[-300:])


if __name__ == "__main__":
    test_agents_toml_day_one()
    test_settings_read_agents_toml()
    test_voices_from_agents_toml()
    test_esther_speaks_through_kokoro()
    test_end_phrases()
    test_check()
    test_sync()
    test_sync_claude()
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
