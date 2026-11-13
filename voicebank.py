"""The voice bank: every voice that is really on this Mac (Peter 2026-11-13: "no voices currently on the system should
not be removed"; brief decision 2: the pick-list reads what is really installed, not a fixed list).

    Kokoro (local)    the voices inside every Kokoro voice file that is installed (voices-v1.0.bin, a zip with one
                      entry per voice). Read straight from the files, so a new voice pack shows up by itself.
                      Homes looked at: ~/.local/share/claude-voice (the shared Kokoro script, Claude, Jarvis, any
                      agent with a Kokoro voice) and ~/Desktop/Peter_Tax/tax_engine/data/voice (Esther's and
                      Simba's scripts). Override: VOICE_DESK_KOKORO_BANKS (folders, ":"-separated).
    Microsoft Edge    online voices. Nothing per voice is installed: what exists is Microsoft's own list, kept in
                      edge_voices.json next to this file (the real list as Microsoft sent it, with the date).
                      `python -m voicebank refresh` asks Microsoft again (needs the internet; the dashboard never
                      does, so opening a list stays instant and offline). Override: VOICE_DESK_EDGE_LIST.

Nothing here writes, plays or sends anything except `refresh`, which rewrites edge_voices.json. A voice in the bank is
not automatically one an agent can speak with: voicepick.py offers each agent the bank voices its own speaker can
really make (English Kokoro voices for the English scripts, Taiwan Mandarin for Z, and so on).

    python -m voicebank            # what is in the bank (read-only)
    python -m voicebank refresh    # fetch Microsoft's current list into edge_voices.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

HERE = Path(__file__).resolve().parent
KOKORO_HOMES = tuple(Path(p).expanduser() for p in os.environ.get(
    "VOICE_DESK_KOKORO_BANKS", "~/.local/share/claude-voice:~/Desktop/Peter_Tax/tax_engine/data/voice").split(":") if p)
EDGE_LIST = Path(os.environ.get("VOICE_DESK_EDGE_LIST", HERE / "edge_voices.json"))

# Kokoro's first letter says the language, the second the gender (af_heart = American English, female).
_KOKORO_LANG = {"a": ("en", "American"), "b": ("en", "British"), "e": ("es", "Spanish"), "f": ("fr", "French"),
                "h": ("hi", "Hindi"), "i": ("it", "Italian"), "j": ("ja", "Japanese"), "p": ("pt", "Brazilian"),
                "z": ("zh", "Mainland Mandarin")}
_GENDER = {"f": "female", "m": "male"}
# Edge locales in plain words (the rest are shown by their code).
_LOCALE = {"en-US": "American", "en-GB": "British", "en-AU": "Australian", "en-CA": "Canadian", "en-IE": "Irish",
           "en-IN": "Indian", "en-NZ": "New Zealand", "en-ZA": "South African", "en-SG": "Singapore",
           "en-HK": "Hong Kong", "en-KE": "Kenyan", "en-NG": "Nigerian", "en-PH": "Philippine", "en-TZ": "Tanzanian",
           "zh-TW": "Taiwan Mandarin", "zh-CN": "Mainland Mandarin", "zh-HK": "Cantonese"}
_EDGE_FIRST = ("en-US", "en-GB")            # Peter's usual accents first, then every other English accent A-Z


@dataclass(frozen=True)
class BankVoice:
    name: str          # what agents.toml stores: "af_heart", "en-US-AvaNeural"
    engine: str        # "kokoro" (local) or "edge" (Microsoft, online)
    lang: str          # "en", "zh", "ja", ...
    locale: str        # "en-US", "en-GB", "zh-TW", ... (Kokoro: the accent its first letter stands for)
    gender: str        # "female" / "male" / ""
    who: str           # the plain name: "Heart", "Ava", "Andrew (multilingual)"

    @property
    def label(self) -> str:
        accent = _LOCALE.get(self.locale, self.locale)
        where = "Kokoro, local" if self.engine == "kokoro" else "Microsoft Edge, online"
        return f"{self.who}, {accent} {self.gender}".rstrip() + f" · {where}"


# ---- Kokoro: read the installed voice files ------------------------------------------------------------------------
_kokoro_cache: dict[tuple[str, int, int], tuple[str, ...]] = {}


def _names_in(binfile: Path) -> tuple[str, ...]:
    """The voice names inside one Kokoro voice file (a zip of <name>.npy), cached by size and time."""
    try:
        st = binfile.stat()
    except OSError:
        return ()
    key = (str(binfile), st.st_mtime_ns, st.st_size)
    if key not in _kokoro_cache:
        try:
            with zipfile.ZipFile(binfile) as z:
                _kokoro_cache[key] = tuple(sorted(Path(n).stem for n in z.namelist() if n.endswith(".npy")))
        except (OSError, zipfile.BadZipFile):
            _kokoro_cache[key] = ()
    return _kokoro_cache[key]


def kokoro_files(homes: Optional[Iterable[Path]] = None) -> list[Path]:
    out = []
    for home in (KOKORO_HOMES if homes is None else homes):
        out += sorted(Path(home).glob("voices*.bin"))
    return out


def _kokoro_voice(name: str) -> Optional[BankVoice]:
    if len(name) < 4 or name[2] != "_" or name[0] not in _KOKORO_LANG or name[1] not in _GENDER:
        return None
    lang, _ = _KOKORO_LANG[name[0]]
    locale = {"a": "en-US", "b": "en-GB", "z": "zh-CN"}.get(name[0], lang)
    return BankVoice(name, "kokoro", lang, locale, _GENDER[name[1]], name[3:].capitalize())


def kokoro(homes: Optional[Iterable[Path]] = None) -> list[BankVoice]:
    """Every Kokoro voice in every installed voice file, each once, American then British then the rest, A-Z."""
    seen: dict[str, BankVoice] = {}
    for f in kokoro_files(homes):
        for n in _names_in(f):
            v = _kokoro_voice(n)
            if v and n not in seen:
                seen[n] = v
    order = "abezfhijp"
    return sorted(seen.values(), key=lambda v: (order.find(v.name[0]) % len(order), v.name))


# ---- Microsoft Edge: Microsoft's own list, saved -------------------------------------------------------------------
_edge_cache: dict[tuple[str, int, int], tuple[dict, list[BankVoice]]] = {}


def _edge_voice(row: dict) -> Optional[BankVoice]:
    name = str(row.get("ShortName") or row.get("name") or "")
    locale = str(row.get("Locale") or row.get("locale") or "")
    if not name.endswith("Neural") or not locale or not name.startswith(locale + "-"):
        return None
    core = name[len(locale) + 1:-len("Neural")]
    multi = core.endswith("Multilingual")
    core = core[:-len("Multilingual")] if multi else core
    who = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", core) + (" (multilingual)" if multi else "")    # NeerjaExpressive
    return BankVoice(name, "edge", locale.split("-")[0], locale, str(row.get("Gender") or row.get("gender") or "").lower(),
                     who)


def edge_list(path: Optional[Path] = None) -> tuple[dict, list[BankVoice]]:
    """(the file's header: fetched_at / source, the voices) from edge_voices.json; ({}, []) when it is missing."""
    p = Path(path or EDGE_LIST)
    try:
        st = p.stat()
    except OSError:
        return {}, []
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in _edge_cache:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        rows = data.get("voices") if isinstance(data, dict) else None
        voices = [v for v in (_edge_voice(r) for r in (rows or []) if isinstance(r, dict)) if v]
        head = {k: v for k, v in (data.items() if isinstance(data, dict) else ()) if k != "voices"}
        _edge_cache[key] = (head, voices)
    return _edge_cache[key]


def edge(path: Optional[Path] = None) -> list[BankVoice]:
    return edge_list(path)[1]


def _edge_order(v: BankVoice) -> tuple:
    first = _EDGE_FIRST.index(v.locale) if v.locale in _EDGE_FIRST else len(_EDGE_FIRST)
    return (first, v.locale, v.who.lower())


# ---- what the pick-lists ask for -----------------------------------------------------------------------------------
def english(homes: Optional[Iterable[Path]] = None, edge_path: Optional[Path] = None) -> list[BankVoice]:
    """Every English voice in the bank: local Kokoro first, then Microsoft (American, British, then other accents)."""
    return ([v for v in kokoro(homes) if v.lang == "en"]
            + sorted((v for v in edge(edge_path) if v.lang == "en"), key=_edge_order))


def mandarin_taiwan(edge_path: Optional[Path] = None) -> list[BankVoice]:
    """Taiwan Mandarin only (Peter 2026-11-09): the zh-TW voices Microsoft has. Kokoro's Mandarin voices are Mainland."""
    return sorted((v for v in edge(edge_path) if v.locale == "zh-TW"), key=lambda v: v.name)


def everything(homes: Optional[Iterable[Path]] = None, edge_path: Optional[Path] = None) -> list[BankVoice]:
    return kokoro(homes) + sorted(edge(edge_path), key=_edge_order)


# ---- refresh: ask Microsoft for its current list (the only network use, never from the dashboard) -------------------
_FETCH = """
import asyncio, json, sys
import edge_tts
rows = asyncio.run(edge_tts.list_voices())
json.dump([{k: r.get(k) for k in ("ShortName", "Locale", "Gender")} for r in rows], sys.stdout)
"""


def refresh(path: Optional[Path] = None, python: Optional[str] = None, run=subprocess.run) -> tuple[bool, str]:
    """Fetch Microsoft's voice list with the Python that has edge-tts and save it (atomic). The old file stays when
    anything fails."""
    if python is None:
        import tts
        python = tts.edge_python()
    if not python:
        return False, "no Python with edge-tts found, so Microsoft's list cannot be fetched (the saved list stays)"
    try:
        r = run([python, "-"], input=_FETCH, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"fetching Microsoft's list failed ({exc}); the saved list stays"
    try:
        rows = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        rows = None
    if not isinstance(rows, list) or not rows:
        return False, f"Microsoft sent no list (exit {r.returncode}: {(r.stderr or '').strip()[-200:]}); the saved list stays"
    import time
    p = Path(path or EDGE_LIST)
    data = {"fetched_at": time.strftime("%Y-%m-%d %H:%M %z"), "source": "edge_tts.list_voices() (Microsoft's own list)",
            "voices": sorted(rows, key=lambda x: str(x.get("ShortName")))}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return True, f"saved {len(rows)} Microsoft voices to {p.name}"


def main(argv: list[str]) -> int:
    if argv[:1] == ["refresh"]:
        ok, msg = refresh()
        print(msg)
        return 0 if ok else 1
    files = kokoro_files()
    ko = kokoro()
    head, ed = edge_list()
    print(f"Kokoro (local): {len(ko)} voices in {', '.join(str(f) for f in files) or 'no voice file found'}")
    for lang in sorted({v.lang for v in ko}):
        print(f"  {lang}: " + ", ".join(v.name for v in ko if v.lang == lang))
    print(f"Microsoft Edge (online): {len(ed)} voices, list from {head.get('fetched_at', 'nowhere (no edge_voices.json)')}")
    print(f"  English: {len([v for v in ed if v.lang == 'en'])}, Taiwan Mandarin: "
          + ", ".join(v.name for v in ed if v.locale == "zh-TW"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
