"""The top strip of the voice grid: is the machinery under the voices healthy?

    voice engine      the shared text-to-speech script is there and Edge is not benched
    Hermes gateway    the process Telegram replies come through is alive and its bots are connected
    speech queue      on / off (the file ~/.voice_desk/speech/off is the off switch)
    Hermes fixes      Peter's local patches in Hermes' own program files are still there (a Hermes
                      update can overwrite them)
    Mac output        volume, mute and which speaker/headphones are the output right now

Read-only and gentle: a handful of small file reads and one osascript call, done once per refresh
by the caller, never per row.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import gridstate as G
import settings as S

# (file under hermes-agent, text that proves the patch is in, what it does)
FIX_MARKERS = (
    ("hermes_cli/cli_voice_mixin.py", "voice_desk/live_turn", "windows stay quiet while Voice Desk is answering"),
    ("hermes_cli/cli_tui_mixin.py", "_maybe_auto_enable_voice", "voice.auto_enable turns window voice on at start"),
)


@dataclass(frozen=True)
class Light:
    ok: Optional[bool]                 # True green, False red, None = unknown (grey)
    label: str
    detail: str = ""


def voice_engine(paths: G.Paths) -> Light:
    chain = S.TTS_CHAIN
    if not chain.exists():
        return Light(False, "voice engine missing", f"{chain} is not there")
    if not os.access(chain, os.X_OK):
        return Light(False, "voice engine not runnable", f"{chain} is not executable")
    if (S.TTS_FALLBACK_STATE / "edge.down").exists():
        return Light(False, "Edge voices benched", "the chain marked Edge as down for 10 minutes; Voice Desk's own lines retry it")
    return Light(True, "voice engine OK", "text-to-speech script present, Edge not benched (a real render is the T test)")


def gateway(paths: G.Paths) -> Light:
    pid_file = paths.hermes_home / "gateway.pid"
    state = G._read_json(paths.hermes_home / "gateway_state.json") or {}
    try:
        pid = int((G._read_json(pid_file) or {}).get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except PermissionError:
            alive = True
        except OSError:
            alive = False
    if not alive:
        return Light(False, "Hermes gateway down", "no running gateway process (gateway.pid)")
    plats = state.get("platforms") or {}
    bad = [k for k, v in plats.items() if isinstance(v, dict) and v.get("state") != "connected"]
    if bad:
        return Light(False, "Hermes gateway up, bot trouble", "not connected: " + ", ".join(sorted(bad)))
    return Light(True, "Hermes gateway up", f"{len(plats)} Telegram bot(s) connected" if plats else "running")


def speech_queue(paths: G.Paths) -> Light:
    off = (paths.speech_dir / "off").exists()
    stand_in = (Path.home() / "bin" / "afplay").exists()
    if off:
        return Light(False, "speech queue off", "the file speech/off exists: Hermes windows speak straight through, overlapping allowed")
    return Light(True, "speech queue on" if stand_in else "speech queue on (stand-in missing)",
                 "agents take turns, one voice at a time" if stand_in else "~/bin/afplay is missing, so lines are not queued")


def hermes_fixes(paths: Optional[G.Paths] = None, hermes_agent: Optional[Path] = None) -> Light:
    """Peter's local Hermes patches (hermes_patches.py: manifest + saved diffs in ~/.hermes/backups/local-patches).
    Green = all in and live; grey = all in, but the gateway has not restarted since a gateway patch changed
    (on disk is not loaded); red = something is gone. Falls back to the old marker check when no manifest exists."""
    import hermes_patches
    loc = hermes_patches.Locations.for_home(paths.hermes_home) if paths else hermes_patches.Locations.default()
    if hermes_agent is not None:
        loc = hermes_patches.Locations(hermes_agent, loc.patches, loc.hermes_home)
    try:
        ok, label, detail = hermes_patches.summary(hermes_patches.status(loc))
        return Light(ok, label, detail)
    except hermes_patches.ManifestError:
        pass
    root = loc.root
    missing = []
    for rel, marker, what in FIX_MARKERS:
        try:
            ok = marker in (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            ok = False
        if not ok:
            missing.append(what)
    if missing:
        return Light(False, f"Hermes fixes missing ({len(FIX_MARKERS) - len(missing)}/{len(FIX_MARKERS)})",
                     "gone after an update: " + "; ".join(missing))
    return Light(True, f"Hermes fixes present ({len(FIX_MARKERS)}/{len(FIX_MARKERS)})")


def mac_output() -> Light:
    vol = muted = None
    try:
        r = subprocess.run(["/usr/bin/osascript", "-e", "get volume settings"], capture_output=True, text=True, timeout=5)
        m = re.search(r"output volume:(\d+|missing value).*output muted:(true|false|missing value)", r.stdout)
        if m:
            vol = None if m.group(1) == "missing value" else int(m.group(1))
            muted = m.group(2) == "true"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        import coreaudio
        device = coreaudio.default_output_name()
    except Exception:
        device = None
    if vol is None and device is None:
        return Light(None, "Mac output unknown")
    bits = [f"volume {vol}%" if vol is not None else "volume ?", "MUTED" if muted else "not muted", device or "device ?"]
    return Light(False if muted or vol == 0 else True, "Mac output: " + ", ".join(bits))


def collect(paths: G.Paths) -> list[Light]:
    return [voice_engine(paths), gateway(paths), speech_queue(paths), hermes_fixes(paths), mac_output()]
