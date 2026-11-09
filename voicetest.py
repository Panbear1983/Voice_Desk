"""Silent synthetic voice test: does each agent's configured voice really produce audio?

For one agent this renders one short sentence per language with the voice agents.toml names, into
a throw-away folder, and checks the file with ffprobe (a real audio stream, not empty, not a blip).
Nothing is played, nothing reaches Telegram, no agent is asked anything. It is the "without
testing it physically" half of the Voice check: the log evidence says what happened lately, this
says whether the voice would work right now.

An Edge voice is tried through Hermes' own TTS chain in strict mode: if the named voice cannot be
made, the test says so instead of quietly letting another voice (Gemini's female voice, the Mac's
voice) answer in its place, which is exactly the failure that hides in real use.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

import agents
import gridstate as G
import settings as S
import tts

SENTENCES = {"en": "This is a Voice Desk test for {name}.", "zh": "這是{name}的語音測試。"}
MIN_SECONDS = 0.5
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


@dataclass
class TestResult:
    __test__ = False                   # not a pytest/unittest test class
    lang: str
    voice: str
    ok: bool
    seconds: float = 0.0
    took: float = 0.0
    detail: str = ""
    at: float = 0.0
    via: str = ""                      # which path made it: the voice itself or the backup

    def short(self) -> str:
        return (f"{self.lang} ✓ {self.seconds:.1f}s" if self.ok else f"{self.lang} ✗ {self.detail}")


def probe(path: Path) -> tuple[bool, float, str]:
    """(is real audio, duration in seconds, what ffprobe said) for a file."""
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=20)
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, 0.0, f"ffprobe could not read it ({exc})"
    if r.returncode != 0:
        return False, 0.0, "ffprobe says this is not audio: " + (r.stderr.strip().splitlines() or ["?"])[-1]
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    try:
        dur = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        dur = 0.0
    if not streams:
        return False, dur, "no audio stream in the file"
    if dur < MIN_SECONDS:
        return False, dur, f"only {dur:.2f}s of audio"
    return True, dur, streams[0].get("codec_name", "")


Synth = Callable[[Path, Path, str, "agents.Agent", str], Optional[str]]


_EDGE_PYTHONS = ("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3", "/usr/local/bin/python3")
_edge_python: Optional[str] = None
NEWHOST = Path.home() / ".local/bin/edge_tts_newhost.py"      # Edge through Microsoft's current address


def edge_python() -> Optional[str]:
    """A Python that has edge-tts (the same search Hermes' own voice script does), found once."""
    global _edge_python
    if _edge_python is None:
        import glob
        found = ""
        for c in [*_EDGE_PYTHONS, *sorted(glob.glob(str(Path.home() / ".hermes/installs/*/environments/*/venv/bin/python")),
                                          key=lambda f: -Path(f).stat().st_mtime)]:
            if Path(c).exists() and subprocess.run([c, "-c", "import edge_tts"], capture_output=True).returncode == 0:
                found = c
                break
        _edge_python = found
    return _edge_python or None


def _edge_direct(txt: Path, out: Path, voice: str) -> bool:
    """Ask Edge for exactly this voice and nothing else: no Google, no Mac voice. Two tries, like the chain."""
    py = edge_python()
    if not py or not NEWHOST.exists():
        return False
    for _ in range(2):
        try:
            r = subprocess.run([py, str(NEWHOST), "--voice", voice, "--file", str(txt), "--write-media", str(out)],
                               capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return True
    return False


def _real_synth(txt: Path, out: Path, voice: str, agent: agents.Agent, lang: str) -> Optional[str]:
    """Make the file; return None on failure, else which path made it ('voice' or 'backup')."""
    if lang == "en" and agent.command and not tts.voices.is_edge(voice):          # Esther / Simba: their own Kokoro script
        if tts._script(agent.command, txt, out, voice, effect=agent.effect):
            return "voice"
        return "backup" if agent.fallback and _edge_direct(txt, out, agent.fallback) else None
    return "voice" if _edge_direct(txt, out, voice) else None


def voices_to_test(agent: agents.Agent, cfg: dict) -> list[tuple[str, str]]:
    """[(language, voice name)] exactly as the agent would speak: its own, else the shared Voice Desk voice."""
    info = G.voice_info(agent, cfg)
    if agent.english_only:                                  # Simba: English only, nothing else is tested or spoken
        return [("en", info.en)]
    out = [("en", info.en), ("zh", info.zh)]
    out += [(code, v) for code, v in info.extra.items()]
    return out


def run(agent: agents.Agent, cfg: dict, synth: Synth = _real_synth, probe_fn=probe,
        sentences: Optional[dict] = None) -> list[TestResult]:
    sentences = sentences or SENTENCES
    results = []
    tmp = Path(tempfile.mkdtemp(prefix="vd-voicecheck-"))
    try:
        for lang, voice in voices_to_test(agent, cfg):
            t0 = time.time()
            text = sentences.get(lang, sentences["en"]).format(name=agent.display)
            txt, out = tmp / f"{lang}.txt", tmp / f"{lang}.mp3"
            txt.write_text(text, encoding="utf-8")
            try:
                via = synth(txt, out, voice, agent, lang)
            except Exception as exc:                  # a broken script must read as a failed test, not crash the panel
                results.append(TestResult(lang, voice, False, 0, time.time() - t0, f"{type(exc).__name__}: {exc}", time.time()))
                continue
            took = time.time() - t0
            if not via or not out.exists():
                results.append(TestResult(lang, voice, False, 0, took, "the voice could not be made", time.time()))
                continue
            ok, dur, what = probe_fn(out)
            detail = (f"{what}, {dur:.1f}s" + ("" if via == "voice" else f" (made with the backup voice {agent.fallback})")) if ok else what
            results.append(TestResult(lang, voice, ok, dur, took, detail, time.time(), via))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


# -- remembering the last results so the panel shows them after a restart ----------------------------
def results_file() -> Path:
    return S.STATE_DIR / "voice_check.json"


def save(all_results: dict[str, list[TestResult]], path: Optional[Path] = None) -> None:
    path = path or results_file()
    try:
        path.write_text(json.dumps({k: [asdict(r) for r in v] for k, v in all_results.items()}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        pass


def load(path: Optional[Path] = None) -> dict[str, list[TestResult]]:
    try:
        raw = json.loads((path or results_file()).read_text(encoding="utf-8"))
        return {k: [TestResult(**r) for r in v] for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        return {}
