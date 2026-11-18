"""The agents' own speaking scripts outside this folder: which of them can play any voice in the bank, and the one
safe way to switch them over (Peter 2026-11-18: "yes" to the full voice list for every agent; brief
2026-11-18-acd-full-voice-list.md, Phase B; earlier decisions 3-6 of 2026-11-13-voice-desk-command-center.md).

    .venv/bin/python -m voicescripts status              which scripts are switched (read-only)
    .venv/bin/python -m voicescripts install --dry-run   the exact edits, nothing written
    .venv/bin/python -m voicescripts install [names]     back up, edit, smoke-test with fakes, put back on failure
    .venv/bin/python -m voicescripts restore <backup>    put every file of one install back as it was

The scripts and what the edit does (each edit is small and anchored on exact text; if the text around it changed,
nothing is written and the script is reported as "does not fit"):

    gemini          ~/.gemini/config/hooks/speak-response.py      a non-Microsoft voice (a Kokoro voice) goes to the
                                                                  shared speaker; the Mac-voice fallback is removed
    jarvis          ~/.hermes/profiles/orchestrator/scripts/tts_jarvis_local.sh
                                                                  a non-Kokoro voice goes to the shared speaker (it
                                                                  used to be turned into am_puck)
    esther          ~/.hermes/profiles/tax_angel_esther/scripts/esther_tts.py
                                                                  a voice that is not one of hers goes to the shared
                                                                  speaker (it used to be turned into alloy)
    claude-natural  ~/.claude/hooks/speak-natural.py              backup voices removed (Kokoro af_heart, Mac voice):
    claude-kokoro   ~/.claude/hooks/kokoro_speak.py               a failed voice is silent. No re-route (decision 6)

Simba's script (simba_tts.py) lives in this folder and is switched by the code itself, not by an install.

The shared speaker is speak_any.py in Voice Desk's folder (~/GitHub/Voice_Desk/speak_any.py, run with its .venv
Python). A switched script that finds it missing stays silent for a voice it does not make itself, so the pick-lists
offer the whole bank to an agent only when BOTH are true: its script carries the switch mark (MARK) and the shared
speaker is in place (`switched`). Until then the extra voices are shown "not playable yet".

Every install: one backup folder ~/.hermes/profile-backups/<time>-voice-scripts/ with a copy of each file before
the edit, manifest.json (fingerprints before and after) and RESTORE.md (how to undo by hand). After each edit a smoke
test runs the edited script with fakes (a fake shared speaker, a fake Kokoro, a fake `say`, a fake player, a throw-away
home folder): it must make the asked voice through the shared speaker, stay silent when that fails, and never call a
second voice. Nothing is played and no live window is touched. If the smoke test fails the file is put back at once
and the install stops there.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
MARK = "voice-desk: any bank voice (shared speaker)"          # a script that hands other voices to speak_any.py
NOFB_MARK = "voice-desk no-fallback edit"                      # a Claude hook file with its backup voice removed
SWITCHED, READY, NOFIT, MISSING = "switched", "ready", "does not fit", "missing"
STATE_WORDS = {SWITCHED: "switched: plays any bank voice / no backup voice", READY: "not switched yet (the edit fits)",
               NOFIT: "not switched, and the edit does not fit: the text around it changed (needs a person)",
               MISSING: "the file is not there"}


def home() -> Path:
    return Path(os.environ.get("VOICE_DESK_SCRIPTS_HOME") or Path.home())


def speak_any_path() -> Path:
    """Where the outside scripts look for the shared speaker (the same default they carry)."""
    return Path(os.environ.get("VOICE_DESK_SPEAK_ANY") or home() / "GitHub/Voice_Desk/speak_any.py")


def speak_any_python() -> Path:
    return Path(os.environ.get("VOICE_DESK_SPEAK_PY") or home() / "GitHub/Voice_Desk/.venv/bin/python")


def gemini_hook() -> Path:
    return Path(os.environ.get("VOICE_DESK_GEMINI_HOOK") or home() / ".gemini/config/hooks/speak-response.py")


# ---- which agent speaks through which file, and can that file play any bank voice ---------------------------------
_read_cache: dict[tuple[str, int, int], bool] = {}


def has_mark(path: Path, mark: str = MARK) -> bool:
    """The file carries the switch mark (cached by size and time; a missing file has none)."""
    try:
        st = Path(path).stat()
    except OSError:
        return False
    key = (f"{path}|{mark}", st.st_mtime_ns, st.st_size)
    if key not in _read_cache:
        try:
            _read_cache[key] = mark in Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            _read_cache[key] = False
    return _read_cache[key]


def shared_speaker_ready() -> bool:
    return speak_any_path().is_file() and os.access(speak_any_python(), os.X_OK)


def speaker_file(agent) -> Optional[Path]:
    """The file that makes this agent's English lines when it is not Voice Desk speaking for it: its own script
    (agents.toml `command`, last part) or Gemini's hook. None = no script of its own (Edge or the shared Kokoro
    script already make every bank voice for it)."""
    if agent.command:
        return Path(os.path.expanduser(agent.command[-1]))
    if agent.target == "gemini":
        return gemini_hook()
    return None


def speaker_kind(agent) -> str:
    """"simba" (simba_tts.py), "gemini" (its hook), "script" (Jarvis's, Esther's or another own script), or ""."""
    if any(Path(c).name == "simba_tts.py" for c in agent.command):
        return "simba"
    if agent.command:
        return "script"
    if agent.target == "gemini":
        return "gemini"
    return ""


def switched(agent) -> bool:
    """The agent's own speaker plays any bank voice: its file carries MARK and the shared speaker is in place."""
    path = speaker_file(agent)
    if path is None:
        return True
    return has_mark(path) and shared_speaker_ready()


def _engine(name: str) -> str:
    import agents
    import speak_any
    if speak_any.EDGE_NAME.match(name or ""):
        return "edge"
    return "kokoro" if agents.is_kokoro(name) else ""


def speaker_problem(agent, field_name: str, name: str) -> str:
    """Why this agent's own speaker cannot play that voice YET ("" = it can). Only English voices depend on the
    agent's script: a Mandarin line is always made with the Microsoft voice by name. The words say "not playable
    yet" in the pick-lists; the reason is the script, not the voice."""
    if field_name != "voice" or not name:
        return ""
    kind = speaker_kind(agent)
    if not kind or switched(agent):
        return ""
    engine = _engine(name)
    who = agent.display
    if kind == "simba":
        import simba_tts
        name = simba_tts.clean_voice(name)              # "Fenrir" is am_fenrir
        if _engine(name) == "edge":
            return "her script makes Kokoro voices only"
        if not (name[:3] in ("af_", "am_")):
            return "her script makes American Kokoro voices only"
        return ""
    if kind == "gemini" and engine != "edge":
        return "Gemini's hook makes Microsoft voices only"
    if kind == "script" and engine == "edge":
        # its own script makes its own (Kokoro) voices, including names only it knows (Esther's "alloy"); a Microsoft
        # voice is the one thing it cannot make before it is switched
        return f"{who}'s own script makes Kokoro voices only"
    return ""


# ---- the outside scripts and their edits ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Script:
    key: str
    who: str
    rel: str                                   # path under the home folder
    kind: str                                  # "bash" / "python"
    mark: str
    edits: tuple[tuple[str, str], ...]         # (exact old text, new text): every old must be there exactly once
    what: str                                  # one plain line: what the edit changes
    smoke: Callable[["Script", Path, Path, Optional[Path]], list[str]] = field(default=lambda s, p, t, b: [],
                                                                             compare=False)

    def path(self, base: Optional[Path] = None) -> Path:
        return (base or home()) / self.rel


def _sh_default(name: str, rel: str) -> str:
    return f'${{{name}:-$HOME/{rel}}}'


JARVIS_EDITS = (
    ('case "$voice" in am_*|af_*|bm_*|bf_*) ;; *) voice="am_puck";; esac   # a stale Edge name in config must not '
     'matter\n',
     f'# {MARK}, 2026-11-18 (Peter: "yes" to the full voice list for every agent). Jarvis\'s own Kokoro voices\n'
     '# (am_/af_/bm_/bf_) are made here as before; any other voice in the bank is made by Voice Desk\'s shared speaker\n'
     '# (speak_any.py), exactly that voice and nothing else. If it fails the line stays silent (exit 1): it is never\n'
     '# turned into am_puck or any other stand-in voice.\n'
     'native=1; case "$voice" in am_*|af_*|bm_*|bf_*) ;; *) native=0;; esac\n'),
    ('SPEED="${JARVIS_TTS_SPEED:-1.2}"\n',
     'SPEED="${JARVIS_TTS_SPEED:-1.2}"\n'
     'if [ "$native" = 0 ]; then\n'
     f'  SA="{_sh_default("VOICE_DESK_SPEAK_ANY", "GitHub/Voice_Desk/speak_any.py")}"\n'
     f'  SPY="{_sh_default("VOICE_DESK_SPEAK_PY", "GitHub/Voice_Desk/.venv/bin/python")}"\n'
     '  if [ -x "$SPY" ] && [ -f "$SA" ] && "$SPY" "$SA" "$in" "$out" "$voice" "$SPEED" 2>>"$LOG" && [ -s "$out" ]; then\n'
     '    echo "$(date +%T) shared speaker:$voice x$SPEED" >> "$LOG"; exit 0\n'
     '  fi\n'
     '  echo "$(date +%T) SHARED SPEAKER FAILED for $voice, line stays silent" >> "$LOG"; exit 1\n'
     'fi\n'),
)

ESTHER_EDITS = (
    ("import json\nimport subprocess\n", "import json\nimport os\nimport subprocess\n"),
    ('def main() -> int:\n'
     '    src, dst = Path(sys.argv[1]), Path(sys.argv[2])\n'
     '    voice = (sys.argv[3] if len(sys.argv) > 3 else "").strip()\n',
     f'# {MARK}, 2026-11-18 (Peter: "yes" to the full voice list for every agent). A voice that is one\n'
     '# of Esther\'s own (alloy, heart, the blends ...: tax_engine/telegram/voice.py VOICES) is made here exactly as\n'
     '# before. Any other voice in the bank (am_adam, bm_george, en-US-AvaNeural ...) is made by Voice Desk\'s shared\n'
     '# speaker (speak_any.py), exactly that voice: it is never turned into alloy. If it fails nothing is written and\n'
     '# this exits 1: the line stays silent.\n'
     'SPEAK_ANY = os.environ.get("VOICE_DESK_SPEAK_ANY") or str(Path.home() / "GitHub/Voice_Desk/speak_any.py")\n'
     'SPEAK_PY = os.environ.get("VOICE_DESK_SPEAK_PY") or str(Path.home() / "GitHub/Voice_Desk/.venv/bin/python")\n'
     '\n'
     '\n'
     'def wants_shared_speaker(voice: str) -> bool:\n'
     '    """True when the voice is not one of Esther\'s own (no voice at all means her default: hers)."""\n'
     '    if not voice:\n'
     '        return False\n'
     '    try:\n'
     '        if str(ENGINE) not in sys.path:\n'
     '            sys.path.insert(0, str(ENGINE))\n'
     '        from tax_engine.telegram.voice import resolve\n'
     '    except Exception:\n'
     '        return voice.endswith("Neural")          # her table cannot be read: a Microsoft name is surely not hers\n'
     '    return resolve(voice) is None\n'
     '\n'
     '\n'
     'def via_shared_speaker(src: Path, dst: Path, voice: str) -> int:\n'
     '    try:\n'
     '        r = subprocess.run([SPEAK_PY, SPEAK_ANY, str(src), str(dst), voice], capture_output=True, timeout=170)\n'
     '    except (OSError, subprocess.TimeoutExpired):\n'
     '        return 1\n'
     '    return 0 if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0 else 1\n'
     '\n'
     '\n'
     'def main() -> int:\n'
     '    src, dst = Path(sys.argv[1]), Path(sys.argv[2])\n'
     '    voice = (sys.argv[3] if len(sys.argv) > 3 else "").strip()\n'
     '    if wants_shared_speaker(voice):\n'
     '        return via_shared_speaker(src, dst, voice)\n'),
)

GEMINI_EDITS = (
    ('_EDGE_VOICE = re.compile(r"^(en|zh)-[A-Z]{2}-[A-Za-z]+Neural$")\n',
     '_EDGE_VOICE = re.compile(r"^(en|zh)-[A-Z]{2}-[A-Za-z]+Neural$")\n'
     f'# {MARK}, 2026-11-18 (Peter: "yes" to the full voice list for every agent). A Microsoft\n'
     '# voice is made below exactly as before. Any other English voice in the bank (a Kokoro voice such as af_heart or\n'
     '# bm_george) is made by Voice Desk\'s shared speaker (speak_any.py), exactly that voice. No fallback: if the voice\n'
     '# cannot be made the reply stays silent (the Mac voice is never used any more), and a voice line in\n'
     '# voice-defaults.conf that names no usable voice means silent, not the default above.\n'
     '_KOKORO_VOICE = re.compile(r"^[ab][fm]_[a-z]+$")\n'
     'SPEAK_ANY = os.environ.get("VOICE_DESK_SPEAK_ANY") or str(Path.home() / "GitHub/Voice_Desk/speak_any.py")\n'
     'SPEAK_PY = os.environ.get("VOICE_DESK_SPEAK_PY") or str(Path.home() / "GitHub/Voice_Desk/.venv/bin/python")\n'),
    ('        v = _EDGE_VOICE.match(m.group(2))\n'
     '        if m.group(1) == "NEURAL_VOICE_EN" and v and v.group(1) == "en":\n'
     '            en = m.group(2)\n'
     '        elif m.group(1) == "NEURAL_VOICE_ZH" and v and v.group(1) == "zh":\n'
     '            zh = m.group(2)\n',
     '        v = _EDGE_VOICE.match(m.group(2))\n'
     '        if m.group(1) == "NEURAL_VOICE_EN":\n'
     '            en = m.group(2) if (v and v.group(1) == "en") or _KOKORO_VOICE.match(m.group(2)) else ""\n'
     '        elif m.group(1) == "NEURAL_VOICE_ZH":\n'
     '            zh = m.group(2) if v and v.group(1) == "zh" else ""\n'),
    ('        log(f"Edge-TTS failed ({e}), falling back to native say")\n',
     '        log(f"Edge-TTS failed ({e}); the reply stays silent (no stand-in voice)")\n'),
    ('def play_audio(audio_path: Path) -> None:\n',
     'def shared_speaker(text: str, voice: str, out_path: Path) -> bool:\n'
     '    """Voice Desk\'s shared speaker makes exactly this voice into out_path (nothing is played here)."""\n'
     '    fd, tmp = tempfile.mkstemp(prefix="gemini-say-", suffix=".txt")\n'
     '    try:\n'
     '        with os.fdopen(fd, "w", encoding="utf-8") as f:\n'
     '            f.write(text)\n'
     '        r = subprocess.run([SPEAK_PY, SPEAK_ANY, tmp, str(out_path), voice], stdout=subprocess.DEVNULL,\n'
     '                           stderr=subprocess.PIPE, timeout=240)\n'
     '        if r.returncode != 0:\n'
     '            log(f"Shared speaker could not make {voice} (exit {r.returncode}): "\n'
     '                f"{(r.stderr or b\'\').decode(errors=\'replace\').strip()[-160:]}")\n'
     '        return r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0\n'
     '    except Exception as e:\n'
     '        log(f"Shared speaker error for {voice}: {e}")\n'
     '        return False\n'
     '    finally:\n'
     '        try:\n'
     '            os.unlink(tmp)\n'
     '        except OSError:\n'
     '            pass\n'
     '\n'
     '\n'
     'def play_audio(audio_path: Path) -> None:\n'),
    ('    success = False\n'
     '    if not audio_path.exists():\n'
     '        try:\n'
     '            success = asyncio.run(synthesize_edge_tts(text, voice, audio_path))\n'
     '        except Exception as e:\n'
     '            log(f"Asyncio TTS error: {e}")\n'
     '            success = False\n'
     '    else:\n'
     '        success = True\n'
     '\n'
     '    if success and audio_path.exists():\n'
     '        play_audio(audio_path)\n'
     '    else:\n'
     '        # Fallback to macOS native say\n'
     '        stop_prior_speech()\n'
     '        mac_voice = "Meijia" if is_zh else "Samantha"\n'
     '        if queue_player():\n'
     '            # `say` speaks at once, outside the queue. Render it to a file and play that file in turn instead.\n'
     '            fd, tmp = tempfile.mkstemp(prefix="gemini-say-", suffix=".aiff")\n'
     '            os.close(fd)\n'
     '            try:\n'
     '                made = subprocess.run(["say", "-v", mac_voice, "-o", tmp, text],\n'
     '                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0\n'
     '                if made and os.path.getsize(tmp) > 0:\n'
     '                    play_audio(Path(tmp))\n'
     '                    return\n'
     '            finally:\n'
     '                try:\n'
     '                    os.unlink(tmp)\n'
     '                except OSError:\n'
     '                    pass\n'
     '        subprocess.run(["say", "-v", mac_voice, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n',
     '    if not voice:\n'
     '        log("Skipping speech: no usable voice is set for this language (silent, no stand-in voice)")\n'
     '        return\n'
     '    success = False\n'
     '    if not audio_path.exists():\n'
     '        try:\n'
     '            if _EDGE_VOICE.match(voice):\n'
     '                success = asyncio.run(synthesize_edge_tts(text, voice, audio_path))\n'
     '            else:\n'
     '                success = shared_speaker(text, voice, audio_path)\n'
     '        except Exception as e:\n'
     '            log(f"TTS error: {e}")\n'
     '            success = False\n'
     '    else:\n'
     '        success = True\n'
     '\n'
     '    if success and audio_path.exists():\n'
     '        play_audio(audio_path)\n'
     '    else:\n'
     '        # No fallback (2026-11-18): the Mac\'s own voice is never used. The reply stays silent.\n'
     '        log(f"Voice {voice} could not be made: the reply stays silent (no stand-in voice)")\n'),
)

CLAUDE_NATURAL_EDITS = (
    ('instead of after the whole answer has been rendered. If synthesis fails (no\n'
     'network, service down, edge-tts missing) this process replaces itself with\n'
     'macOS `say` using APPLE_VOICE ("" = system default) for whatever is left, so\n'
     'the recorded pid still means "this session\'s speech".\n',
     'instead of after the whole answer has been rendered. If synthesis fails (no\n'
     'network, service down, edge-tts missing) the rest of the answer stays silent:\n'
     f'no backup voice since 2026-11-18 ({NOFB_MARK}; Peter 2026-11-13: "no\n'
     'fallback voice for every claude session"). APPLE_VOICE only marks a Chinese answer.\n'),
    ('def fallback_say(remaining):\n'
     '    """Replace this process with Apple `say` for the remaining text."""\n'
     '    cleanup()\n'
     '    argv = ["say"] + (["-v", apple] if apple else []) + [remaining]\n'
     '    try:\n'
     '        os.execvp("say", argv)\n'
     '    except Exception:\n'
     '        os._exit(127)\n',
     'def fallback_say(remaining):\n'
     f'    """No backup voice ({NOFB_MARK}, 2026-11-18; brief decision 6: "no fallback voice for every\n'
     '    claude session"). This used to hand the rest to Apple `say`; now the rest of the answer stays silent and the\n'
     '    log says so. The name is kept so every caller below reads as before."""\n'
     '    log(f"voice failed: the remaining {len(remaining)} chars stay silent (no backup voice)")\n'
     '    cleanup()\n'
     '    os._exit(0)\n'),
    ('def fallback_local(remaining):\n'
     '    """Natural local voice (Kokoro, af_heart) for the rest of the answer when Jenny is unavailable;\n'
     '    the robotic `say` only if that fails too. Chinese answers keep the Taiwan Mandarin system voice."""\n'
     '    if apple:\n'
     '        fallback_say(remaining)\n'
     '    try:\n'
     '        tf = os.path.join(tmpdir, "rest.txt"); wav = os.path.join(tmpdir, "rest.wav")\n'
     '        with open(tf, "w", encoding="utf-8") as fh:\n'
     '            fh.write(remaining)\n'
     '        r = subprocess.run([KOKORO_PY, KOKORO_SAY, tf, wav], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, '
     'timeout=240)\n'
     '        if r.returncode == 0 and os.path.exists(wav) and os.path.getsize(wav) > 0:\n'
     '            log(f"Jenny unavailable: spoke {len(remaining)} chars with the local voice")\n'
     '            player = subprocess.Popen([AFPLAY, wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, '
     'env=PLAY_ENV)\n'
     '            children.append(player)\n'
     '            player.wait()\n'
     '            cleanup()\n'
     '            os._exit(0)\n'
     '        log(f"local voice failed: {(r.stderr or b\'\').decode(errors=\'replace\')[-200:]}")\n'
     '    except Exception as e:\n'
     '        log(f"local voice error: {e}")\n'
     '    fallback_say(remaining)\n',
     'def fallback_local(remaining):\n'
     f'    """No backup voice ({NOFB_MARK}, 2026-11-18): this used to speak the rest with Kokoro af_heart,\n'
     '    then Apple `say`. Now the rest stays silent; see fallback_say."""\n'
     '    fallback_say(remaining)\n'),
)

CLAUDE_KOKORO_EDITS = (
    ('Exit 0 = all spoken. On any failure the rest is handed to macOS `say` so the answer is never lost.\n',
     f'Exit 0 = all spoken. On any failure the rest stays silent and this exits 3 ({NOFB_MARK}, 2026-11-18:\n'
     'no backup voice for any Claude session; it used to hand the rest to macOS `say`).\n'),
    ('def say_rest(rest):\n'
     '    shutil.rmtree(tmpdir, ignore_errors=True)\n'
     '    if rest.strip():\n'
     '        os.execvp("say", ["say", rest])\n'
     '    os._exit(0)\n',
     'def say_rest(rest):\n'
     '    """No backup voice: the rest stays silent (exit 3 tells speak-natural.py that Kokoro failed)."""\n'
     '    shutil.rmtree(tmpdir, ignore_errors=True)\n'
     '    os._exit(3 if rest.strip() else 0)\n'),
)


# ---- smoke tests: the edited script, run with fakes ----------------------------------------------------------------
FAKE_SPEAK_ANY = '''import json, os, sys
with open(os.environ["FAKE_SPEAK_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_SPEAK_MODE", "ok") != "ok":
    sys.exit(1)
with open(sys.argv[2], "wb") as f:
    f.write(b"FAKEAUDIO" + bytes(64))
'''
FAKE_RECORDER = '#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n'
# A fake Kokoro script runner for Jarvis's script: "$KPY" "$KSAY" <in> <out> <voice>
FAKE_KPY = '#!/bin/sh\necho "$4" >> "{log}"\ncp "{wav}" "$3"\nexit 0\n'
FAKE_FAIL = '#!/bin/sh\nexit 1\n'
# A fake edge-tts: --voice V --text T --write-media P
FAKE_EDGE = ('#!/bin/sh\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = "--write-media" ] && out="$2"; shift; done\n'
             'echo "$out" >> "{log}"\n[ "$FAKE_EDGE_MODE" = ok ] || exit 1\ncp "{wav}" "$out"\n')


@dataclass
class Sandbox:
    root: Path
    home: Path
    bin: Path
    speak_log: Path
    say_log: Path
    player_log: Path
    kokoro_log: Path
    edge_log: Path
    fake_speak_any: Path
    player: Path
    wav: Path

    def env(self, **extra: str) -> dict:
        env = dict(os.environ)
        for k in ("VOICE_DESK_LOOP",):
            env.pop(k, None)
        env.update(HOME=str(self.home), PATH=f"{self.bin}:{env.get('PATH', '/usr/bin:/bin')}",
                   VOICE_DESK_SPEAK_ANY=str(self.fake_speak_any), VOICE_DESK_SPEAK_PY=sys.executable,
                   FAKE_SPEAK_LOG=str(self.speak_log), VOICE_DESK_STATE=str(self.root / "state"),
                   VOICE_DESK_AFPLAY_STANDIN=str(self.player))
        env.update(extra)
        return env

    @staticmethod
    def lines(p: Path) -> list[str]:
        return p.read_text().splitlines() if p.exists() else []

    def speak_calls(self) -> list[list[str]]:
        return [json.loads(x) for x in self.lines(self.speak_log)]


def _exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def sandbox(root: Path) -> Sandbox:
    """A throw-away home with fakes: nothing in it can speak, play or reach a live window."""
    root.mkdir(parents=True, exist_ok=True)
    bin_ = root / "bin"
    logs = {n: root / f"{n}.log" for n in ("speak", "say", "player", "kokoro", "edge")}
    wav = root / "tiny.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(4800))                      # 0.1 s of silence
    for name in ("say", "afplay", "killall"):          # nothing on PATH may speak or stop someone else's sound
        _exe(bin_ / name, FAKE_RECORDER.format(log=logs["say"] if name == "say" else root / f"{name}.log"))
    sb = Sandbox(root, root / "home", bin_, logs["speak"], logs["say"], logs["player"], logs["kokoro"], logs["edge"],
                 root / "fake_speak_any.py", _exe(root / "fake_player", FAKE_RECORDER.format(log=logs["player"])),
                 wav)
    sb.home.mkdir(exist_ok=True)
    sb.fake_speak_any.write_text(FAKE_SPEAK_ANY)
    return sb


def _run(cmd: list, env: dict, timeout: float = 120, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)


def _syntax(path: Path, kind: str) -> list[str]:
    if kind == "bash":
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        return [] if r.returncode == 0 else [f"bash finds a syntax error: {r.stderr.strip()[-200:]}"]
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")      # syntax only: no .pyc is written
    except SyntaxError as exc:
        return [f"Python finds a syntax error: {exc}"]
    # the hooks run under whatever python3 the caller has (macOS's own is older): it must read the file too
    probs = []
    for py in ("/usr/bin/python3", "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
               os.path.expanduser("~/Desktop/Peter_Tax/tax_engine/venv/bin/python"),
               os.path.expanduser("~/.local/share/claude-voice/venv/bin/python")):
        if not os.access(py, os.X_OK):
            continue
        r = subprocess.run([py, "-c", "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], "
                                      "'exec')", str(path)], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            probs.append(f"{py} finds a syntax error: {r.stderr.strip()[-200:]}")
    return probs


def _no_say(sb: Sandbox, what: str) -> list[str]:
    said = sb.lines(sb.say_log)
    return [f"{what}: the Mac voice was called ({said[-1][:60]!r})"] if said else []


def smoke_jarvis(s: Script, path: Path, tmp: Path, before: Optional[Path]) -> list[str]:
    sb = sandbox(tmp)
    probs = _syntax(path, "bash")
    if probs:
        return probs
    _exe(sb.home / ".local/share/claude-voice/venv/bin/python", FAKE_KPY.format(log=sb.kokoro_log, wav=sb.wav))
    txt = tmp / "say.txt"
    txt.write_text("Hello Peter, the build is green.", encoding="utf-8")
    zh = tmp / "zh.txt"
    zh.write_text("今天的建置全部通過了，沒有任何錯誤。", encoding="utf-8")
    out = tmp / "out.wav"
    r = _run(["bash", path, txt, out, "en-US-AvaNeural"], sb.env(FAKE_SPEAK_MODE="ok"))
    calls = sb.speak_calls()
    if r.returncode != 0 or not out.exists() or not out.read_bytes().startswith(b"FAKEAUDIO"):
        probs.append(f"a Microsoft voice did not come from the shared speaker (exit {r.returncode})")
    elif not calls or calls[-1][2:] != ["en-US-AvaNeural", "1.2"]:
        probs.append(f"the shared speaker was not asked for exactly en-US-AvaNeural at 1.2x: {calls[-1:]}")
    out.unlink(missing_ok=True)
    r = _run(["bash", path, txt, out, "en-GB-RyanNeural"], sb.env(FAKE_SPEAK_MODE="fail"))
    if r.returncode == 0 or (out.exists() and out.stat().st_size):
        probs.append("a failed shared speaker still wrote a line (it must stay silent)")
    if sb.lines(sb.kokoro_log):
        probs.append(f"a failed Microsoft voice was handed to Kokoro ({sb.lines(sb.kokoro_log)[-1]})")
    n = len(sb.speak_calls())
    r = _run(["bash", path, txt, out, "am_puck"], sb.env(FAKE_SPEAK_MODE="fail"))
    if r.returncode != 0 or sb.lines(sb.kokoro_log) != ["am_puck"] or len(sb.speak_calls()) != n:
        probs.append(f"his own Kokoro voice am_puck is not made the old way any more (exit {r.returncode}, "
                     f"kokoro {sb.lines(sb.kokoro_log)}, shared speaker calls {len(sb.speak_calls()) - n})")
    out.unlink(missing_ok=True)
    r = _run(["bash", path, zh, out, "en-US-AvaNeural"], sb.env(FAKE_SPEAK_MODE="ok"))
    if r.returncode == 0 or len(sb.speak_calls()) != n:
        probs.append("a Chinese line was spoken (Jarvis is English only: it must stay silent)")
    return probs + _no_say(sb, "Jarvis")


def _python_for(path: Path, wanted: str) -> str:
    p = Path(os.path.expanduser(wanted))
    return str(p) if os.access(p, os.X_OK) else sys.executable


ESTHER_DECISIONS = {"alloy": False, "af_heart": False, "heart": False, "": False, "deepalloy": False,
                    "am_adam": True, "bm_george": True, "en-US-AvaNeural": True}


def smoke_esther(s: Script, path: Path, tmp: Path, before: Optional[Path]) -> list[str]:
    sb = sandbox(tmp)
    probs = _syntax(path, "python")
    if probs:
        return probs
    py = _python_for(path, "~/Desktop/Peter_Tax/tax_engine/venv/bin/python")
    txt = tmp / "say.txt"
    txt.write_text("Your quarterly estimate is ready.", encoding="utf-8")
    out = tmp / "out.ogg"
    r = _run([py, path, txt, out, "en-US-AvaNeural"], sb.env(FAKE_SPEAK_MODE="ok"))
    calls = sb.speak_calls()
    if r.returncode != 0 or not out.exists() or not out.read_bytes().startswith(b"FAKEAUDIO"):
        probs.append(f"a Microsoft voice did not come from the shared speaker (exit {r.returncode}: "
                     f"{r.stderr.strip()[-200:]})")
    elif not calls or calls[-1][2:] != ["en-US-AvaNeural"]:
        probs.append(f"the shared speaker was not asked for exactly en-US-AvaNeural: {calls[-1:]}")
    out.unlink(missing_ok=True)
    r = _run([py, path, txt, out, "bm_george"], sb.env(FAKE_SPEAK_MODE="fail"))
    if r.returncode == 0 or (out.exists() and out.stat().st_size):
        probs.append("a failed shared speaker still wrote a line (it must stay silent, never alloy)")
    probe = ("import importlib.util, json, sys\n"
             "spec = importlib.util.spec_from_file_location('esther_tts_smoke', sys.argv[1])\n"
             "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
             "print(json.dumps({v: m.wants_shared_speaker(v) for v in json.loads(sys.argv[2])}))\n")
    r = _run([py, "-c", probe, path, json.dumps(list(ESTHER_DECISIONS))], sb.env())
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = None
    if got != ESTHER_DECISIONS:
        probs.append(f"which voices are hers is not read right: {got} (wanted {ESTHER_DECISIONS}; "
                     f"{r.stderr.strip()[-200:]})")
    return probs + _no_say(sb, "Esther")


GEMINI_PROBE = r'''import asyncio, importlib.util, json, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("gemini_hook_smoke", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
played = []
m.play_audio = lambda p: played.append(Path(p).read_bytes()[:9].decode(errors="replace"))
m.stop_prior_speech = lambda: None
out = {}
conf = m.VOICE_CONF
conf.parent.mkdir(parents=True, exist_ok=True)
for name, text in (("kokoro_conf", 'NEURAL_VOICE_EN="af_heart"\nNEURAL_VOICE_ZH="zh-TW-HsiaoYuNeural"\n'),
                   ("edge_conf", 'NEURAL_VOICE_EN="en-US-AriaNeural"\nNEURAL_VOICE_ZH="zh-TW-HsiaoYuNeural"\n'),
                   ("bad_conf", 'NEURAL_VOICE_EN="nonsense"\n')):
    conf.write_text(text)
    out[name] = list(m.gemini_voices())
os.environ["FAKE_SPEAK_MODE"] = "ok"
m.speak_worker("Hello from the Gemini smoke test.", False, "af_heart")
out["ok_played"] = list(played); played.clear()
os.environ["FAKE_SPEAK_MODE"] = "fail"
m.speak_worker("Hello again from the Gemini smoke test.", False, "bm_george")
out["fail_played"] = list(played); played.clear()
async def no_edge(text, voice, path):
    return False
m.synthesize_edge_tts = no_edge
m.speak_worker("Microsoft is down in this test.", False, "en-US-AriaNeural")
out["edge_fail_played"] = list(played)
print(json.dumps(out))
'''
GEMINI_WANT = {"kokoro_conf": ["af_heart", "zh-TW-HsiaoYuNeural"], "edge_conf": ["en-US-AriaNeural", "zh-TW-HsiaoYuNeural"],
               "bad_conf": ["", "zh-TW-HsiaoChenNeural"], "ok_played": ["FAKEAUDIO"], "fail_played": [],
               "edge_fail_played": []}
VOICES_PROBE = ("import importlib.util, json, sys\n"
                "spec = importlib.util.spec_from_file_location('gemini_voices_probe', sys.argv[1])\n"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                "print(json.dumps(list(m.gemini_voices())))\n")


def smoke_gemini(s: Script, path: Path, tmp: Path, before: Optional[Path]) -> list[str]:
    sb = sandbox(tmp)
    probs = _syntax(path, "python")
    if probs:
        return probs
    py = _python_for(path, "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3")
    r = _run([py, "-c", GEMINI_PROBE, path], sb.env())
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = None
    if got != GEMINI_WANT:
        probs.append(f"the hook does not behave as it should: {got} (wanted {GEMINI_WANT}; {r.stderr.strip()[-300:]})")
    calls = sb.speak_calls()
    if [c[2:] for c in calls] != [["af_heart"], ["bm_george"]]:
        probs.append(f"the shared speaker was not asked for exactly af_heart, then bm_george: {calls}")
    if 'subprocess.run(["say"' in path.read_text(encoding="utf-8"):
        probs.append("a call to the Mac voice is still in the hook")
    if before is not None and before.exists():
        # nothing changes by default: Peter's own voice-defaults.conf gives the same voices before and after
        env = dict(os.environ)
        old = _run([py, "-c", VOICES_PROBE, before], env)
        new = _run([py, "-c", VOICES_PROBE, path], env)
        if old.returncode or new.returncode or old.stdout.strip() != new.stdout.strip():
            probs.append(f"Gemini's voices from its voice-defaults.conf changed: before {old.stdout.strip()}, "
                         f"after {new.stdout.strip()}")
    return probs + _no_say(sb, "Gemini")


def smoke_claude_natural(s: Script, path: Path, tmp: Path, before: Optional[Path]) -> list[str]:
    sb = sandbox(tmp)
    probs = _syntax(path, "python")
    if probs:
        return probs
    if 'os.execvp("say"' in path.read_text(encoding="utf-8"):
        probs.append("a call to the Mac voice is still in speak-natural.py")
    edge = _exe(tmp / "fake-edge-tts", FAKE_EDGE.format(log=sb.edge_log, wav=sb.wav))
    _exe(sb.home / ".local/share/claude-voice/venv/bin/python", FAKE_FAIL)      # Kokoro that always fails
    conf = tmp / "voice-defaults.conf"
    txt = tmp / "say.txt"
    cases = (("Chinese answer, Microsoft down", "edge", "fail", "今天的建置全部通過了。", ["zh-TW-HsiaoChenNeural", "Meijia"], 0),
             ("English answer, Kokoro fails", "kokoro", "fail", "The build is green.", ["", ""], 0),
             ("English answer, Microsoft down", "edge", "fail", "The build is green.", ["en-US-AvaNeural", ""], 0),
             ("English answer, Microsoft works", "edge", "ok", "The build is green.", ["en-US-AvaNeural", ""], 1))
    for what, engine, mode, text, args, plays in cases:
        conf.write_text(f'ENGLISH_ENGINE="{engine}"\nKOKORO_VOICE_EN="af_heart"\n')
        txt.write_text(text, encoding="utf-8")
        before_plays, before_says = len(sb.lines(sb.player_log)), len(sb.lines(sb.say_log))
        try:
            _run([sys.executable, path, txt, *args],
                 sb.env(EDGE_TTS=str(edge), FAKE_EDGE_MODE=mode, VOICE_DEFAULTS_CONF=str(conf)), timeout=60)
        except subprocess.TimeoutExpired:
            probs.append(f"{what}: it did not finish within a minute")
            continue
        played = len(sb.lines(sb.player_log)) - before_plays
        if played != plays:
            probs.append(f"{what}: {played} piece(s) played, wanted {plays}")
        said = sb.lines(sb.say_log)[before_says:]
        if said:
            probs.append(f"{what}: the Mac voice was called ({said[-1][:60]!r})")
    return probs


def smoke_claude_kokoro(s: Script, path: Path, tmp: Path, before: Optional[Path]) -> list[str]:
    sb = sandbox(tmp)
    probs = _syntax(path, "python")
    if probs:
        return probs
    if 'os.execvp("say"' in path.read_text(encoding="utf-8"):
        probs.append("a call to the Mac voice is still in kokoro_speak.py")
    py = _python_for(path, "~/.local/share/claude-voice/venv/bin/python")
    txt = tmp / "say.txt"
    txt.write_text("The build is green. Nothing else to report.", encoding="utf-8")
    r = _run([py, path, txt, sb.player, "af_heart"], sb.env(CLAUDE_VOICE_HOME=str(tmp / "no-model")))
    if r.returncode != 3:
        probs.append(f"a Kokoro failure did not end silent with exit 3 (exit {r.returncode})")
    if sb.lines(sb.player_log):
        probs.append("something was played although Kokoro had no model")
    return probs + _no_say(sb, "Claude's Kokoro player")


SCRIPTS = (
    Script("gemini", "Gemini", ".gemini/config/hooks/speak-response.py", "python", MARK, GEMINI_EDITS,
           "a Kokoro voice is made by the shared speaker; the Mac-voice fallback is gone", smoke_gemini),
    Script("jarvis", "Jarvis", ".hermes/profiles/orchestrator/scripts/tts_jarvis_local.sh", "bash", MARK, JARVIS_EDITS,
           "a Microsoft voice is made by the shared speaker (it used to be turned into am_puck)", smoke_jarvis),
    Script("esther", "Esther", ".hermes/profiles/tax_angel_esther/scripts/esther_tts.py", "python", MARK, ESTHER_EDITS,
           "a voice that is not one of hers is made by the shared speaker (it used to be turned into alloy)",
           smoke_esther),
    Script("claude-natural", "Claude", ".claude/hooks/speak-natural.py", "python", NOFB_MARK, CLAUDE_NATURAL_EDITS,
           "the backup voices (Kokoro af_heart, then the Mac voice) are gone: a failed voice is silent",
           smoke_claude_natural),
    Script("claude-kokoro", "Claude", ".claude/hooks/kokoro_speak.py", "python", NOFB_MARK, CLAUDE_KOKORO_EDITS,
           "the Mac-voice backup after a Kokoro failure is gone: the rest stays silent", smoke_claude_kokoro),
)


def by_key(keys: Optional[list[str]] = None) -> list[Script]:
    if not keys:
        return list(SCRIPTS)
    known = {s.key: s for s in SCRIPTS}
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise SystemExit(f"unknown script(s): {', '.join(unknown)}; known: {', '.join(known)}")
    return [known[k] for k in keys]


def state(s: Script, base: Optional[Path] = None) -> str:
    p = s.path(base)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return MISSING
    if s.mark in text:
        return SWITCHED
    return READY if all(text.count(old) == 1 for old, _ in s.edits) else NOFIT


def edited(s: Script, text: str) -> str:
    for old, new in s.edits:
        if text.count(old) != 1:
            raise ValueError(f"{s.key}: the text to replace is there {text.count(old)} times, not once")
        text = text.replace(old, new)
    return text


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.voice-desk-tmp")
    tmp.write_bytes(data)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


@dataclass
class Outcome:
    key: str
    ok: bool
    message: str
    problems: list[str] = field(default_factory=list)


RESTORE_NOTE = """# Restore note: Voice Desk voice-script install {stamp}

Written by `python -m voicescripts install` (Voice_Desk/voicescripts.py). Each file below was copied here BEFORE it
was edited. To put everything back exactly as it was:

    cd ~/GitHub/Voice_Desk && .venv/bin/python -m voicescripts restore {folder}

That puts a file back only if it still is exactly what the install wrote (fingerprints in manifest.json); a file
changed by hand since is left alone and named, so nothing newer is lost. By hand, one file at a time:

{lines}
"""


def install(keys: Optional[list[str]] = None, *, base: Optional[Path] = None, backups: Optional[Path] = None,
            now: Optional[_dt.datetime] = None, dry_run: bool = False,
            out: Callable[[str], None] = print) -> list[Outcome]:
    """Switch the scripts over, one at a time: backup, edit, smoke test, put back on failure. Stops at the first
    failure. dry_run shows the edits and writes nothing."""
    now = now or _dt.datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    backups = Path(backups or Path.home() / ".hermes/profile-backups")
    folder = backups / f"{stamp}-voice-scripts"
    manifest: list[dict] = []
    results: list[Outcome] = []
    for s in by_key(keys):
        p = s.path(base)
        st = state(s, base)
        if st != READY:
            results.append(Outcome(s.key, st == SWITCHED, f"{s.key}: {STATE_WORDS[st]}; nothing written"))
            out(results[-1].message)
            continue
        old = p.read_bytes()
        new = edited(s, old.decode("utf-8")).encode("utf-8")
        if dry_run:
            diff = difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
                                        str(p), f"{p} (edited)")
            out("".join(diff))
            results.append(Outcome(s.key, True, f"{s.key}: DRY RUN, nothing written ({s.what})"))
            out(results[-1].message)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        bak = folder / f"{s.key}--{p.name}"
        shutil.copy2(p, bak)
        entry: dict = {"key": s.key, "path": str(p), "backup": str(bak), "before": _sha(old), "after": _sha(new),
                 "what": s.what}
        manifest.append(entry)
        _write_manifest(folder, manifest, stamp)
        _atomic_write(p, new)
        with tempfile.TemporaryDirectory(prefix=f"voicescripts-{s.key}-") as tmp:
            try:
                problems = s.smoke(s, p, Path(tmp), bak)
            except Exception as exc:                            # noqa: BLE001  a crashed smoke test is a failure
                problems = [f"the smoke test itself failed: {type(exc).__name__}: {exc}"]
        if problems:
            _atomic_write(p, old)
            back = _sha(p.read_bytes()) == entry["before"]
            entry["restored"] = back
            _write_manifest(folder, manifest, stamp)
            results.append(Outcome(s.key, False, f"{s.key}: smoke test FAILED, the file was put back "
                                                 f"{'as it was' if back else 'BUT DOES NOT MATCH ITS BACKUP: ' + str(bak)}"
                                                 "; the install stops here", problems))
            out(results[-1].message)
            for x in problems:
                out(f"    - {x}")
            break
        results.append(Outcome(s.key, True, f"{s.key}: switched and smoke-tested ({s.what}); backup {bak}"))
        out(results[-1].message)
    return results


def _write_manifest(folder: Path, manifest: list[dict], stamp: str) -> None:
    (folder / "manifest.json").write_text(json.dumps({"stamp": stamp, "files": manifest}, indent=1) + "\n")
    lines = "\n".join(f"    cp -p '{e['backup']}' '{e['path']}'      # {e['key']}: {e['what']}" for e in manifest)
    (folder / "RESTORE.md").write_text(RESTORE_NOTE.format(stamp=stamp, folder=folder, lines=lines))


def restore(folder: Path, out: Callable[[str], None] = print) -> bool:
    """Put back every file of one install that is still exactly what the install wrote."""
    data = json.loads((Path(folder) / "manifest.json").read_text())
    ok = True
    for e in reversed(data["files"]):
        p, bak = Path(e["path"]), Path(e["backup"])
        try:
            now = _sha(p.read_bytes())
        except OSError:
            now = ""
        if now == e["before"]:
            out(f"{e['key']}: already as it was before the install")
        elif now == e["after"]:
            _atomic_write(p, bak.read_bytes())
            good = _sha(p.read_bytes()) == e["before"]
            ok &= good
            out(f"{e['key']}: put back from {bak}" + ("" if good else " BUT IT DOES NOT READ BACK: check by hand"))
        else:
            ok = False
            out(f"{e['key']}: changed since the install, left alone. Its backup: {bak}")
    return ok


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="voicescripts", description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    i = sub.add_parser("install")
    i.add_argument("names", nargs="*")
    i.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("folder")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        for s in SCRIPTS:
            print(f"{s.key:15} {STATE_WORDS[state(s)]}   ({s.path()})")
        print(f"shared speaker  {'in place' if shared_speaker_ready() else 'NOT in place'}   "
              f"({speak_any_path()}, run with {speak_any_python()})")
        return 0
    if args.cmd == "install":
        results = install(args.names, dry_run=args.dry_run)
        return 0 if all(x.ok for x in results) else 1
    return 0 if restore(Path(args.folder)) else 1


if __name__ == "__main__":
    sys.exit(main())
