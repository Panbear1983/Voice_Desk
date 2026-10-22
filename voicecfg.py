"""voicecfg: keep every agent's voice settings in step with agents.toml.

    .venv/bin/python -m voicecfg check              # what differs (read-only); exit 1 on drift
    .venv/bin/python -m voicecfg sync --dry-run     # the exact line changes, nothing written
    .venv/bin/python -m voicecfg sync --apply       # back up, write, re-read with Hermes' own parser

Hermes agents: agents.toml values go into ~/.hermes/profiles/<profile>/config.yaml:
  voice      -> tts.providers.<active provider>.voice (or tts.<provider>.voice), and
                tts.edge.voice when the profile has that block and the voice is an Edge voice
  speed      -> tts.edge.speed
  end phrases-> voice.stop_phrases (generated from [shared.end_phrases])
  knobs      -> voice.auto_tts / silence_duration / silence_threshold / barge_in / barge_in_grace_seconds
Claude: [agents.claude] goes into ~/.claude/voice-defaults.conf (NEURAL_VOICE_EN, NEURAL_VOICE_ZH,
VOICE_ALWAYS_ON, VOICE_MODE), which ~/.claude/hooks/speak-response.sh already reads.

Edits are single-line replacements (or one inserted line when a key is missing), never a YAML
re-dump, so comments, anchors and every other setting stay byte-for-byte as they were. No key,
token or secret is ever read into the output: only the managed voice keys are printed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import agents

HOME = Path.home()
PROFILES = Path(os.environ.get("HERMES_PROFILES_DIR", HOME / ".hermes/profiles"))
CLAUDE_CONF = Path(os.environ.get("VOICE_DEFAULTS_CONF", HOME / ".claude/voice-defaults.conf"))
BACKUPS = HOME / ".hermes/profile-backups"
HERMES_AGENT = HOME / ".hermes/hermes-agent"            # Hermes' own YAML loader, for the parse check
HERMES_PY = HERMES_AGENT / "venv/bin/python"

_EDGE = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural$")
_CLAUDE_KEYS = (("voice", "NEURAL_VOICE_EN"), ("voice_zh", "NEURAL_VOICE_ZH"),
                ("always_on", "VOICE_ALWAYS_ON"), ("mode", "VOICE_MODE"))


# ---- reading YAML by lines --------------------------------------------------------------------
@dataclass
class Entry:
    line: int            # 0-based line index
    indent: int
    value: str           # the raw value text ("" for a block parent)
    head: str            # everything before the value: indent + key + ":" + spacing
    tail: str            # everything after the value: spacing + inline comment + newline


_KEYLINE = re.compile(r"^([ \t]*)([A-Za-z0-9_.\-]+|\"[^\"]*\"|'[^']*'):(?=\s|$)([ \t]*)(.*?)(\r?\n?)$")


def _split_comment(rest: str) -> tuple[str, str]:
    """'value   # note' -> ('value', '   # note'); a # inside quotes is not a comment."""
    q = None
    i = 0
    while i < len(rest):
        c = rest[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'" and (i == 0 or rest[i - 1] in " \t[,{"):
            q = c
        elif c == "#" and (i == 0 or rest[i - 1] in " \t"):
            j = i
            while j > 0 and rest[j - 1] in " \t":
                j -= 1
            return rest[:j], rest[j:]
        i += 1
    stripped = rest.rstrip(" \t")
    return stripped, rest[len(stripped):]


def scan(text: str) -> dict[str, Entry]:
    """Dotted key path -> where it sits, for every mapping key in a YAML file. Comment lines
    (at any column) and blank lines do not end a block, as in YAML itself; list items and
    block scalars are stepped over."""
    out: dict[str, Entry] = {}
    stack: list[tuple[int, str]] = []
    skip_deeper: Optional[int] = None
    for n, raw in enumerate(text.splitlines(keepends=True)):
        body = raw.strip()
        if not body or body.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if skip_deeper is not None:
            if indent > skip_deeper:
                continue
            skip_deeper = None
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if body.startswith("- ") or body == "-":
            skip_deeper = indent
            continue
        m = _KEYLINE.match(raw)
        if not m:
            continue
        key = m.group(2).strip("\"'")
        value, comment = _split_comment(m.group(4))
        path = ".".join([k for _, k in stack] + [key])
        out[path] = Entry(n, indent, value, raw[:m.start(4)], comment + m.group(5))
        stack.append((indent, key))
        if value[:1] in ("|", ">"):
            skip_deeper = indent
    return out


def parse_value(raw: str) -> Any:
    s = raw.strip()
    if s == "" or s in ("~", "null"):
        return None
    if s.startswith("["):
        try:
            return json.loads(s)
        except ValueError:
            return s
    if s.startswith('"'):
        try:
            return json.loads(s)
        except ValueError:
            return s
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        return s[1:-1].replace("''", "'")
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s


_BARE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]*")


def format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return json.dumps(list(v), ensure_ascii=False)
    s = str(v)
    if _BARE.fullmatch(s) and s.lower() not in ("true", "false", "yes", "no", "on", "off", "null"):
        return s
    return json.dumps(s, ensure_ascii=False)


def same(key: str, have: Any, want: Any) -> bool:
    if key.endswith("stop_phrases"):
        norm = lambda xs: {str(x).strip().lower() for x in xs} if isinstance(xs, (list, tuple)) else None  # noqa: E731
        return norm(have) is not None and norm(have) == norm(want)
    if isinstance(have, bool) or isinstance(want, bool):
        return type(have) is type(want) and have == want
    return have == want


def dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


@functools.lru_cache(maxsize=64)
def _readback(path: str, _sig: tuple) -> Any:
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import hermes_yaml as y; "
            "print(json.dumps(y.safe_load(open(sys.argv[2], encoding='utf-8')), default=str))")
    r = subprocess.run([str(HERMES_PY), "-c", code, str(HERMES_AGENT), path], capture_output=True, text=True,
                       timeout=60, env={k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")})
    if r.returncode != 0:
        raise ValueError(f"Hermes cannot parse {path}: {r.stderr.strip().splitlines()[-1:]}")
    return json.loads(r.stdout)


def hermes_readback(path: Path) -> Any:
    """The file as Hermes' own loader sees it (raises ValueError if Hermes cannot parse it)."""
    st = Path(path).stat()
    return _readback(str(path), (st.st_mtime_ns, st.st_size, st.st_ino))


# ---- what should be where ---------------------------------------------------------------------
@dataclass
class Item:
    where: str                      # agents.toml agent key ("kylie", "claude")
    label: str                      # "Kylie (assistant)"
    path: Path
    kind: str                       # "yaml" or "conf"
    key: str                        # dotted YAML path, or the conf variable
    have: Any
    want: Any
    line: Optional[int] = None      # 0-based line holding it (None = missing)
    insert_after: Optional[int] = None
    insert_indent: int = 0
    problem: str = ""               # set when the key can be neither read nor placed


def _yaml_items(a: agents.Agent, cfg: dict, path: Path) -> list[Item]:
    label = f"{a.display} ({a.profile})"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Item(a.key, label, path, "yaml", "(file)", None, None, problem=f"cannot read: {exc.strerror}")]
    e = scan(text)
    want: list[tuple[str, Any]] = []
    provider = parse_value(e["tts.provider"].value) if "tts.provider" in e else "edge"
    if a.voice:
        vkey = f"tts.providers.{provider}.voice" if f"tts.providers.{provider}" in e else f"tts.{provider}.voice"
        want.append((vkey, a.voice))
        if _EDGE.match(a.voice) and "tts.edge" in e and vkey != "tts.edge.voice":
            want.append(("tts.edge.voice", a.voice))
    if a.speed is not None:
        want.append(("tts.edge.speed", a.speed))
    want.append(("voice.stop_phrases", agents.end_phrases(cfg)))
    want += [(f"voice.{k}", v) for k, v in a.knobs.items()]
    items = []
    for key, value in want:
        if key in e:
            items.append(Item(a.key, label, path, "yaml", key, parse_value(e[key].value), value, line=e[key].line))
            continue
        parent = key.rsplit(".", 1)[0]
        it = Item(a.key, label, path, "yaml", key, None, value)
        if parent in e and e[parent].value == "":
            kids = [x for k, x in e.items() if k.startswith(parent + ".") and k.count(".") == key.count(".")]
            it.insert_after = e[parent].line
            it.insert_indent = kids[0].indent if kids else e[parent].indent + 2
        else:
            it.problem = f"no `{parent}:` block to put it in"
        items.append(it)
    return items


def _conf_scan(text: str) -> dict[str, tuple[int, str, str, str]]:
    """KEY -> (line, value, quote, tail) for KEY=value lines of a shell conf file."""
    out = {}
    for n, raw in enumerate(text.splitlines(keepends=True)):
        m = re.match(r'^([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|\'([^\']*)\'|([^\s#]*))(.*?)(\r?\n?)$', raw)
        if m:
            quote = '"' if m.group(2) is not None else "'" if m.group(3) is not None else ""
            val = next(g for g in (m.group(2), m.group(3), m.group(4)) if g is not None)
            out[m.group(1)] = (n, val, quote, m.group(5) + m.group(6))
    return out


def _claude_items(a: agents.Agent, path: Path) -> list[Item]:
    label = f"{a.display} (~/.claude/voice-defaults.conf)"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Item(a.key, label, path, "conf", "(file)", None, None, problem=f"cannot read: {exc.strerror}")]
    found = _conf_scan(text)
    items = []
    for src, var in _CLAUDE_KEYS:
        if src not in a.raw:
            continue
        v = a.raw[src]
        want = ("1" if v else "0") if isinstance(v, bool) else str(v)
        if var in found:
            items.append(Item(a.key, label, path, "conf", var, found[var][1], want, line=found[var][0]))
        else:
            items.append(Item(a.key, label, path, "conf", var, None, want))
    return items


def collect(cfg: dict, profiles: Path = PROFILES, claude_conf: Path = CLAUDE_CONF) -> list[Item]:
    """Every managed setting: where it lives, what it is now, what agents.toml says."""
    items: list[Item] = []
    for a in agents.agents(cfg):
        if a.profile:
            items += _yaml_items(a, cfg, Path(profiles) / a.profile / "config.yaml")
        elif a.target == "claude" and any(src in a.raw for src, _ in _CLAUDE_KEYS):
            items += _claude_items(a, Path(claude_conf))
    return items


def drift(items: list[Item]) -> list[Item]:
    return [i for i in items if i.problem or not same(i.key, i.have, i.want)]


# ---- writing ------------------------------------------------------------------------------------
def _new_text(path: Path, items: list[Item]) -> tuple[str, str]:
    """(old text, new text) for one file with every drifted item fixed."""
    old = path.read_text(encoding="utf-8")
    lines = old.splitlines(keepends=True)
    inserts: list[tuple[int, str]] = []
    if items and items[0].kind == "yaml":
        e = scan(old)
        for i in items:
            if i.key in e:
                x = e[i.key]
                lines[x.line] = x.head + format_value(i.want) + x.tail
            elif i.insert_after is not None:
                inserts.append((i.insert_after, " " * i.insert_indent + f"{i.key.rsplit('.', 1)[1]}: {format_value(i.want)}\n"))
    else:
        found = _conf_scan(old)
        for i in items:
            if i.key in found:
                n, _, quote, tail = found[i.key]
                lines[n] = f"{i.key}={quote}{i.want}{quote}{tail}"
            else:
                quote = "" if i.key == "VOICE_ALWAYS_ON" else '"'
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                inserts.append((len(lines) - 1, f"{i.key}={quote}{i.want}{quote}\n"))
    for after, text in sorted(inserts, key=lambda t: t[0], reverse=True):
        lines.insert(after + 1, text)
    return old, "".join(lines)


def plan(items: list[Item]) -> dict[Path, tuple[str, str]]:
    by_file: dict[Path, list[Item]] = {}
    for i in drift(items):
        if not i.problem:
            by_file.setdefault(i.path, []).append(i)
    return {p: _new_text(p, its) for p, its in by_file.items()}


def _backup_name(path: Path, kind: str) -> str:
    return "claude-voice-defaults.conf" if kind == "conf" else f"{path.parent.name}-config.yaml"


def _atomic_write(path: Path, text: str) -> None:
    real = path.resolve()
    mode = real.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=f".{real.name}.", suffix=".voicecfg", dir=real.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _verify(path: Path, kind: str, items: list[Item]) -> list[str]:
    """Re-read the written file the way its reader does; [] = every managed value is right."""
    bad = []
    if kind == "yaml":
        if HERMES_PY.exists():
            try:
                data = hermes_readback(path)
            except (ValueError, subprocess.SubprocessError) as exc:
                return [str(exc)]
            for i in items:
                if not same(i.key, dig(data, i.key), i.want):
                    bad.append(f"Hermes reads {i.key} as {dig(data, i.key)!r}, wanted {i.want!r}")
        e = scan(path.read_text(encoding="utf-8"))
        for i in items:
            if i.key not in e or not same(i.key, parse_value(e[i.key].value), i.want):
                bad.append(f"{i.key} did not land")
    else:
        names = [i.key for i in items]
        r = subprocess.run(["/bin/bash", "-c", '. "$1" && for k in "${@:2}"; do printf "%s=%s\\n" "$k" "${!k}"; done',
                            "_", str(path), *names], capture_output=True, text=True, timeout=10)
        got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        for i in items:
            if got.get(i.key) != i.want:
                bad.append(f"bash reads {i.key} as {got.get(i.key)!r}, wanted {i.want!r}")
    return bad


def apply(items: list[Item], backup_dir: Optional[Path] = None) -> tuple[list[str], list[str]]:
    """Back up then write every file that drifts. Returns (done, errors). A file that does not
    read back right is restored from its backup at once."""
    stamp = _dt.datetime.now()
    backup_dir = Path(backup_dir or BACKUPS / f"{stamp:%Y-%m-%d}-voicecfg")
    done, errors = [], []
    wanted = {}
    for i in drift(items):
        if i.problem:
            errors.append(f"{i.label} {i.key}: {i.problem}")
        else:
            wanted.setdefault(i.path, []).append(i)
    for path, (old, new) in plan(items).items():
        its = wanted[path]
        if path.read_text(encoding="utf-8") != old:
            errors.append(f"{path}: changed while syncing, left alone (run sync again)")
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        bak = backup_dir / _backup_name(path, its[0].kind)
        if bak.exists():
            bak = bak.with_name(f"{bak.stem}.{stamp:%H%M%S}{bak.suffix}")
        shutil.copy2(path.resolve(), bak)
        _atomic_write(path, new)
        bad = _verify(path, its[0].kind, its)
        if bad:
            _atomic_write(path, old)
            errors.append(f"{path}: restored from backup, the written file did not read back right: {'; '.join(bad)}")
            continue
        done.append(f"{path}  (backup: {bak})")
    return done, errors


# ---- the command ------------------------------------------------------------------------------
def _show(v: Any) -> str:
    if isinstance(v, list):
        return f"{len(v)} phrases"
    return "missing" if v is None else repr(v)


def describe(i: Item) -> str:
    where = f"line {i.line + 1}" if i.line is not None else "not set"
    if i.problem:
        return f"  {i.label}  {i.key}: {i.problem}"
    if i.key.endswith("stop_phrases") and isinstance(i.have, list):
        have = {str(x).strip().lower() for x in i.have}
        want = {str(x).strip().lower() for x in i.want}
        return (f"  {i.label}  {i.key} ({where}): missing {sorted(want - have)[:5]}"
                f"{' …' if len(want - have) > 5 else ''}, extra {sorted(have - want)[:5]}")
    return f"  {i.label}  {i.key} ({where}): is {_show(i.have)}, agents.toml says {_show(i.want)}"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m voicecfg",
                                 description="Keep every agent's voice settings in step with agents.toml.")
    ap.add_argument("command", choices=("check", "sync"))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="sync: print the line changes only (default)")
    mode.add_argument("--apply", action="store_true", help="sync: back up, then write")
    ap.add_argument("--agents", type=Path, default=None, help="agents.toml to use (default: the repo's)")
    ap.add_argument("--profiles", type=Path, default=PROFILES, help="Hermes profiles folder")
    ap.add_argument("--claude-conf", type=Path, default=CLAUDE_CONF, help="Claude's voice-defaults.conf")
    ap.add_argument("--backup-dir", type=Path, default=None, help="sync --apply: where backups go")
    args = ap.parse_args(argv)

    cfg = agents.load(args.agents)
    items = collect(cfg, args.profiles, args.claude_conf)
    files = sorted({str(i.path) for i in items})
    off = drift(items)

    if args.command == "check":
        if not off:
            print(f"No drift: {len(items)} voice settings in {len(files)} files match agents.toml.")
            return 0
        print("Differences between agents.toml and the live settings:")
        for i in off:
            print(describe(i))
        print(f"{len(off)} differences. `python -m voicecfg sync --dry-run` shows the fix.")
        return 1

    changes = plan(items)
    problems = [i for i in off if i.problem]
    if not args.apply:
        if not changes and not problems:
            print(f"No changes: {len(items)} voice settings in {len(files)} files already match agents.toml.")
            return 0
        for path, (old, new) in changes.items():
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                                       str(path), f"{path} (after sync)", n=1))
        for i in problems:
            print("cannot sync:" + describe(i))
        print(f"{len(changes)} files would change. Run `python -m voicecfg sync --apply` to write them (backups first).")
        return 0 if not problems else 1
    done, errors = apply(items, args.backup_dir)
    for d in done:
        print(f"wrote {d}")
    for e in errors:
        print(f"ERROR {e}")
    if not done and not errors:
        print("No changes: everything already matches agents.toml.")
    if done:
        print("Hermes reads voice settings when a session starts: reopen the agent's window (and restart "
              "the Telegram gateway from a separate terminal) to hear a changed voice.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
