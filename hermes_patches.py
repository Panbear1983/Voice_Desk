"""Peter's local patches to Hermes' own program files: keep them reproducible and checkable.

A Hermes update can overwrite or conflict with the voice fixes Peter relies on (it stashes local edits and
puts them back, and drops them when the code around them moved). So every local patch is kept OUTSIDE the
Hermes folder, as a plain diff plus one line in a manifest:

    ~/.hermes/backups/local-patches/manifest.json     the list, in the order to apply
    ~/.hermes/backups/local-patches/<name>.diff       one `git diff` per patch (new test files included)

    python3 hermes_patches.py check     which patches are in (the dashboard's "Hermes fixes" light uses this)
    python3 hermes_patches.py apply     put back the missing ones: backup first, `git apply` only when it
                                        fits cleanly, read back, nothing forced, nothing half-done

"In" is decided by marker text from the manifest, not by an exact diff match, so a harmless change around a
patch after an update does not make it look lost. "Missing, fits" can be re-applied; "missing, conflict"
means the code around it changed upstream and needs a person (the diff is the recipe).

Code on disk is not loaded code: a patch for the gateway is live only after the gateway restarted since the
file changed. `loaded` says so (None = gateway not running / unknown).

Read-only except `apply`; no process is started except `git apply`; nothing here restarts anything.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PRESENT, MISSING, PARTIAL, CONFLICT, NODIFF = "present", "missing", "partial", "conflict", "no diff"
STATE_WORDS = {
    PRESENT: "in",
    MISSING: "missing, fits (can be put back)",
    PARTIAL: "partly there, does not fit (needs a person)",
    CONFLICT: "missing, conflicts with the new Hermes code (needs a person)",
    NODIFF: "missing, and its saved diff is gone",
}


@dataclass(frozen=True)
class Locations:
    root: Path          # the Hermes program folder (a git checkout)
    patches: Path       # manifest.json + the diffs
    hermes_home: Path   # gateway.pid lives here

    @classmethod
    def for_home(cls, home: Path) -> "Locations":
        """The standard layout under a Hermes home (the grid passes its own `hermes_home`)."""
        return cls(root=Path(home) / "hermes-agent", patches=Path(home) / "backups/local-patches", hermes_home=Path(home))

    @classmethod
    def default(cls) -> "Locations":
        home = Path(os.environ.get("VOICE_DESK_HERMES_HOME", Path.home() / ".hermes"))
        std = cls.for_home(home)
        return cls(root=Path(os.environ.get("VOICE_DESK_HERMES_AGENT", std.root)),
                   patches=Path(os.environ.get("VOICE_DESK_HERMES_PATCHES", std.patches)), hermes_home=home)


@dataclass(frozen=True)
class Patch:
    id: str
    diff: str
    summary: str
    runs_in: str                                  # "cli" (windows) or "gateway"
    markers: tuple[tuple[str, str], ...]          # (file under root, text that proves the patch is in)


@dataclass
class PatchState:
    patch: Patch
    state: str
    missing: list[str] = field(default_factory=list)    # marker files whose text is gone
    detail: str = ""
    loaded: Optional[bool] = None                 # gateway patches: running code includes it? None = unknown

    @property
    def word(self) -> str:
        return STATE_WORDS[self.state]


class ManifestError(ValueError):
    pass


def load_manifest(loc: Locations) -> list[Patch]:
    path = loc.patches / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out = []
        for p in data["patches"]:
            out.append(Patch(str(p["id"]), str(p["diff"]), str(p.get("summary", "")), str(p.get("runs_in", "cli")),
                             tuple((str(a), str(b)) for a, b in p["markers"])))
        if not out:
            raise ManifestError("the manifest lists no patches")
        return out
    except OSError as exc:
        raise ManifestError(f"cannot read {path} ({exc.strerror})") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ManifestError(f"{path} is not a valid patch manifest ({exc})") from exc


# ---- git, only for `apply` and the "does it still fit" question ------------------------------------------------
def _git_apply(loc: Locations, diff: Path, *, check: bool) -> tuple[bool, str]:
    cmd = ["git", "-C", str(loc.root), "apply", "--whitespace=nowarn"] + (["--check"] if check else []) + [str(diff)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[0] if (r.stderr or r.stdout).strip() else ""


def diff_paths(diff: Path) -> tuple[list[str], list[str]]:
    """(files the diff changes that already exist, files it creates)."""
    old: list[str] = []
    new: list[str] = []
    lines = Path(diff).read_text(encoding="utf-8", errors="replace").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("+++ b/"):
            (new if i > 0 and lines[i - 1].startswith("--- /dev/null") else old).append(ln[6:])
    return old, new


def _marker_state(loc: Locations, patch: Patch) -> tuple[int, list[str]]:
    found, missing = 0, []
    for rel, text in patch.markers:
        try:
            ok = text in (loc.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            ok = False
        if ok:
            found += 1
        else:
            missing.append(rel)
    return found, missing


# ---- is the gateway running code that includes the patch? ---------------------------------------------------------
def gateway_started(loc: Locations) -> Optional[float]:
    """When the running gateway started (epoch seconds), None when it is not running. Reads gateway.pid only."""
    try:
        data = json.loads((loc.hermes_home / "gateway.pid").read_text(encoding="utf-8"))
        pid, started = int(data["pid"]), float(data["start_time"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass
    except OSError:
        return None
    return started / 100 if started > 1e11 else started     # Hermes stores centiseconds


def _newest_marker_file(loc: Locations, patch: Patch) -> float:
    times = []
    for rel, _ in patch.markers:
        try:
            times.append((loc.root / rel).stat().st_mtime)
        except OSError:
            pass
    return max(times) if times else 0.0


def status(loc: Optional[Locations] = None) -> list[PatchState]:
    loc = loc or Locations.default()
    started = gateway_started(loc)
    out: list[PatchState] = []
    for p in load_manifest(loc):
        found, missing = _marker_state(loc, p)
        if found == len(p.markers):
            st = PatchState(p, PRESENT)
            if p.runs_in == "gateway" and started is not None:
                st.loaded = started >= _newest_marker_file(loc, p)
            out.append(st)
            continue
        diff = loc.patches / p.diff
        if not diff.is_file():
            out.append(PatchState(p, NODIFF, missing, f"{p.diff} is not in {loc.patches}"))
            continue
        fits, why = _git_apply(loc, diff, check=True)
        if fits:
            out.append(PatchState(p, MISSING, missing, "git apply --check says it fits"))
        else:
            out.append(PatchState(p, PARTIAL if found else CONFLICT, missing, why))
    return out


# ---- what the dashboard and the switches need to know ----------------------------------------------------------------
def summary(states: list[PatchState]) -> tuple[Optional[bool], str, str]:
    """(light, label, detail): True green / False red / None grey (everything in, but the gateway has not
    restarted since a gateway patch changed, so the running code does not have it)."""
    n = len(states)
    ok = [s for s in states if s.state == PRESENT]
    if len(ok) < n:
        bad = [s for s in states if s.state != PRESENT]
        return (False, f"Hermes fixes missing ({len(ok)}/{n})",
                "; ".join(f"{s.patch.summary or s.patch.id}: {s.word}" for s in bad)
                + ". `python3 hermes_patches.py apply` puts back the ones that fit")
    stale = [s for s in states if s.loaded is False]
    if stale:
        return (None, f"Hermes fixes on disk ({n}/{n}), gateway not restarted yet",
                "not live until the Hermes gateway restarts: " + "; ".join(s.patch.summary or s.patch.id for s in stale))
    return True, f"Hermes fixes present ({n}/{n})", ""


LIVE, PENDING, MISSING_STATE = "live", "pending_restart", "missing"
LIVE_MODE_ID = "gateway-live-voice-mode"


def live_voice_mode(loc: Optional[Locations] = None) -> str:
    """Can a Telegram chat mode be switched live right now? `live` = the running gateway has the re-read patch
    (or it is not running, so the next start has it); `pending_restart` = on disk only; `missing` = not there.
    Cheap (a few small file reads, no git): the grid asks it for every Hermes row."""
    loc = loc or Locations.default()
    try:
        patch = next((p for p in load_manifest(loc) if p.id == LIVE_MODE_ID), None)
    except ManifestError:
        return MISSING_STATE
    if patch is None or _marker_state(loc, patch)[0] != len(patch.markers):
        return MISSING_STATE
    started = gateway_started(loc)
    return PENDING if started is not None and started < _newest_marker_file(loc, patch) else LIVE


# ---- putting missing patches back ----------------------------------------------------------------------------------------
@dataclass
class ApplyResult:
    id: str
    ok: bool
    message: str


def apply_missing(loc: Optional[Locations] = None, *, backup_base: Optional[Path] = None,
                  now: Optional[_dt.datetime] = None) -> list[ApplyResult]:
    """Apply, in manifest order, every patch that is missing and fits. Each one: back up the files it changes,
    `git apply`, check the markers, and put the files back if anything is off. Patches that are in are left
    alone; patches that do not fit are reported, never forced."""
    loc = loc or Locations.default()
    now = now or _dt.datetime.now()
    backup_base = backup_base or loc.patches
    folder = backup_base / f"pre-apply-{now.strftime('%Y%m%d-%H%M%S')}"
    results: list[ApplyResult] = []
    for p in load_manifest(loc):
        found, _ = _marker_state(loc, p)
        if found == len(p.markers):
            results.append(ApplyResult(p.id, True, "already in"))
            continue
        diff = loc.patches / p.diff
        if not diff.is_file():
            results.append(ApplyResult(p.id, False, f"saved diff {p.diff} is missing"))
            continue
        fits, why = _git_apply(loc, diff, check=True)            # re-checked now: an earlier patch may have changed the file
        if not fits:
            results.append(ApplyResult(p.id, False, f"does not fit the current Hermes code ({why}); nothing changed"))
            continue
        changed, created = diff_paths(diff)
        saved: list[str] = []
        try:
            for rel in changed:
                src = loc.root / rel
                if src.is_file():
                    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, folder / rel)
                    saved.append(rel)
            done, why = _git_apply(loc, diff, check=False)
            found, missing = _marker_state(loc, p)
            if not done or found != len(p.markers):
                raise RuntimeError(why or "markers not found after applying: " + ", ".join(missing))
            results.append(ApplyResult(p.id, True, f"put back (files saved first in {folder})"))
        except Exception as exc:
            for rel in saved:
                shutil.copy2(folder / rel, loc.root / rel)
            for rel in created:
                if (loc.root / rel).exists() and rel not in changed:
                    try:
                        (loc.root / rel).unlink()
                    except OSError:
                        pass
            results.append(ApplyResult(p.id, False, f"not applied ({exc}); files put back as they were"))
    return results


# ---- command line --------------------------------------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    loc = Locations.default()
    try:
        if cmd == "check":
            states = status(loc)
            for s in states:
                tail = "" if s.loaded is None or s.state != PRESENT else (
                    " — live in the running gateway" if s.loaded else " — ON DISK ONLY: the gateway has not restarted since")
                print(f"{s.patch.id:28} {s.word}{tail}" + (f"  [{s.detail}]" if s.detail and s.state != PRESENT else ""))
            light, label, detail = summary(states)
            print(label + (f" — {detail}" if detail else ""))
            return 0 if light is not False else 1
        if cmd == "apply":
            res = apply_missing(loc)
            for r in res:
                print(f"{r.id:28} {'ok  ' if r.ok else 'FAIL'} {r.message}")
            return 0 if all(r.ok for r in res) else 1
    except ManifestError as exc:
        print(f"cannot read the patch list: {exc}")
        return 2
    print("usage: hermes_patches.py check | apply")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
