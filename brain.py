"""The brains: separate Claude Code, Gemini and Codex conversations, and one headless thread
per Hermes profile.

`Brain` is the front door the window and the phone receiver talk to. It keeps a separate
conversation per target ("claude", "gemini", "codex", or "hermes:<profile>"), remembers which one you are
talking to (say "talk to Esther" / "talk to Claude"), and serializes turns with a file lock
so the Mac window and the phone take turns.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import settings as S
from codex_brain import CodexBrain
from live_session import HermesLiveBrain, LiveBrain

StatusCb = Callable[[str], None]
TextCb = Callable[[str], None]

_TOOL_STATUS = {
    "Read": "still reading files",
    "Grep": "still searching the code",
    "Glob": "still looking for files",
    "Bash": "still running a command",
    "Edit": "still editing a file",
    "Write": "still writing a file",
    "WebSearch": "still searching the web",
    "WebFetch": "still reading a web page",
    "Agent": "still waiting on a helper",
}


def _log(who: str, prompt: str, reply: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(S.TRANSCRIPT_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {S.USER_NAME.upper()}: {prompt}\n[{stamp}] {who.upper()}: {reply}\n\n")


def _status_ticker(stop: threading.Event, on_status: StatusCb, last_tool: Callable[[], str]) -> None:
    n = 0
    while not stop.wait(S.STATUS_EVERY_SEC):
        n += 1
        phrase = _TOOL_STATUS.get(last_tool(), "still working on it")
        if n % 3 == 0:
            phrase = "still here, this one is taking a while"
        on_status(phrase)


# =============================================================================================
class ClaudeBrain:
    """One headless Claude Code thread, resumed turn after turn."""

    name = "Claude"
    target = "claude"

    def __init__(self) -> None:
        self.session_id: Optional[str] = self._load_session()
        self.last_tool = ""
        self._proc: Optional[subprocess.Popen] = None

    # -- session bookkeeping ---------------------------------------------------------------
    def _load_session(self) -> Optional[str]:
        try:
            sid = S.SESSION_FILE.read_text().strip()
            return sid or None
        except OSError:
            return None

    def _save_session(self, sid: str) -> None:
        S.SESSION_FILE.write_text(sid)
        self.session_id = sid

    def new_thread(self) -> None:
        """Forget the running conversation (next turn starts fresh)."""
        try:
            S.SESSION_FILE.unlink()
        except OSError:
            pass
        self.session_id = None

    # -- model switching by voice ("use Opus", "switch to Sonnet", "which model") ------------
    MODELS = {
        "fable": "claude-fable-5-1", "opus": "claude-opus-5-5",
        "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5",
    }

    def model(self) -> str:
        try:
            return S.MODEL_FILE.read_text().strip() or S.DEFAULT_MODEL
        except OSError:
            return S.DEFAULT_MODEL

    # -- effort by voice: three presets ("quick", "normal", "deep") + "max" ------------------
    EFFORTS = {
        "quick": "low", "fast": "low", "low": "low",
        "normal": "medium", "medium": "medium",
        "deep": "high", "high": "high", "harder": "high", "careful": "high",
        "max": "max", "maximum": "max",
    }
    EFFORT_WORDS = {"low": "quick", "medium": "normal", "high": "deep", "xhigh": "deep", "max": "max"}

    def effort(self) -> str:
        try:
            return S.EFFORT_FILE.read_text().strip() or S.DEFAULT_EFFORT
        except OSError:
            return S.DEFAULT_EFFORT

    def command(self, text: str) -> Optional[str]:
        """Spoken settings commands Claude handles itself; None if the text is a real question."""
        norm = re.sub(r"[^\w\s]", "", text.lower())
        if re.search(r"\b(which|what) (effort|mode)\b", norm):
            return f"Effort is {self.EFFORT_WORDS.get(self.effort(), self.effort())}."
        m = re.search(r"\b(think|effort|mode)\b.*\b(quick|fast|low|normal|medium|deep|high|harder|careful|max|maximum)\b", norm) \
            or re.search(r"\b(quick|fast|normal|deep|careful|max|maximum)\s+(mode|answers|thinking|effort)\b", norm)
        if m:
            level = self.EFFORTS[m.group(2) if m.re.pattern.startswith(r"\b(think") else m.group(1)]
            S.EFFORT_FILE.write_text(level)
            return f"Okay, {self.EFFORT_WORDS[level]} effort from the next question on."
        if re.search(r"\b(which|what) model\b", norm):
            return f"I am using {self._model_name(self.model())}."
        m = re.search(r"\b(use|switch to|change to|swap to)\s+(the\s+)?(fable|opus|sonnet|haiku)\b", norm)
        if not m:
            return None
        chosen = self.MODELS[m.group(3)]
        S.MODEL_FILE.write_text(chosen)
        return f"Okay, switching to {self._model_name(chosen)} from the next question on."

    @staticmethod
    def _model_name(model_id: str) -> str:
        return model_id.replace("claude-", "").replace("-", " ").title()

    def ask(self, text: str, on_status: StatusCb = lambda s: None, on_text: TextCb = lambda s: None) -> str:
        handled = self.command(text)
        if handled:
            _log(self.name, text, handled)
            return handled
        return self._ask(text, on_status, on_text)

    def _ask(self, text: str, on_status: StatusCb, on_text: TextCb) -> str:
        cmd = [S.CLAUDE_BIN, "-p", text, "--output-format", "stream-json", "--verbose",
               "--model", self.model(), "--effort", self.effort(),
               "--permission-mode", "bypassPermissions",
               "--append-system-prompt", S.VOICE_SYSTEM_PROMPT]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        env = dict(os.environ, VOICE_DESK_LOOP="1")
        self.last_tool = ""
        self._proc = subprocess.Popen(cmd, cwd=str(S.CLAUDE_CWD), env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stop = threading.Event()
        threading.Thread(target=_status_ticker, args=(stop, on_status, lambda: self.last_tool), daemon=True).start()
        result = ""
        session_in_use = False
        pending = ""        # text is shown as progress only once a tool call follows it; the last text is the reply
        try:
            assert self._proc.stdout
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "system" and ev.get("session_id"):
                    self._save_session(ev["session_id"])
                elif t == "assistant":
                    for block in (ev.get("message") or {}).get("content", []):
                        if block.get("type") == "tool_use":
                            self.last_tool = block.get("name", "")
                            if pending:
                                on_text(pending)
                                pending = ""
                        elif block.get("type") == "text" and block.get("text"):
                            if pending:
                                on_text(pending)
                            pending = block["text"]
                elif t == "result":
                    result = ev.get("result") or ""
                    if ev.get("session_id"):
                        self._save_session(ev["session_id"])
                    if ev.get("is_error") and "in use" in result.lower():
                        session_in_use = True
            self._proc.wait(timeout=S.CLAUDE_TURN_TIMEOUT_SEC)
        finally:
            stop.set()
            err = ""
            if self._proc and self._proc.stderr:
                try:
                    err = self._proc.stderr.read()
                except Exception:
                    pass
            self._proc = None
        if session_in_use or (not result and "already in use" in err.lower()):
            # the thread is being driven elsewhere (e.g. a resumed interactive window); fork a fresh one
            self.new_thread()
            return self._ask(text, on_status, on_text)
        if not result:
            tail = err.strip().splitlines()[-1] if err.strip() else "no reply"
            return f"I did not get an answer back. The error was: {tail}"
        _log(self.name, text, result)
        return result

    def cancel(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.terminate()


# =============================================================================================
class HermesBrain:
    """One headless Hermes thread for one profile: `hermes -p <profile> -z <text> --resume <id>`.

    Hermes one-shot prints only the final answer and no session id, so after the first turn
    the new session is looked up in the profile's own session store."""

    def __init__(self, profile: str) -> None:
        self.profile = profile
        self.target = f"hermes:{profile}"
        self.name = hermes_display_name(profile)
        self.home = S.HERMES_PROFILES_DIR / profile
        self.session_file = S.HERMES_SESSION_DIR / profile
        self.session_id: Optional[str] = self._load_session()
        self.last_tool = ""
        self._proc: Optional[subprocess.Popen] = None

    def _load_session(self) -> Optional[str]:
        try:
            sid = self.session_file.read_text().strip()
            return sid or None
        except OSError:
            return None

    def _save_session(self, sid: str) -> None:
        S.HERMES_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(sid)
        self.session_id = sid

    def new_thread(self) -> None:
        try:
            self.session_file.unlink()
        except OSError:
            pass
        self.session_id = None

    def _newest_session(self, since: float) -> Optional[str]:
        """The one-shot session Hermes just wrote (started after `since`), from its session store."""
        db = self.home / "state.db"
        if not db.exists():
            return None
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                row = con.execute(
                    "SELECT id FROM sessions WHERE source = 'oneshot' AND started_at >= ? "
                    "ORDER BY started_at DESC LIMIT 1", (since - 5,)).fetchone()
            finally:
                con.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def ask(self, text: str, on_status: StatusCb = lambda s: None, on_text: TextCb = lambda s: None) -> str:
        return self._ask(text, on_status, retry=True)

    def _ask(self, text: str, on_status: StatusCb, retry: bool) -> str:
        prompt = text + "\n\n" + S.HERMES_VOICE_NOTE
        cmd = [S.HERMES_BIN, "-p", self.profile, "-z", prompt]
        if S.HERMES_VOICE_REASONING:                  # a spoken turn: think briefly (the profile's own level is for typed work)
            cmd += ["--reasoning", S.HERMES_VOICE_REASONING]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        env = dict(os.environ, HERMES_HOME=str(self.home), VOICE_DESK_LOOP="1")
        started = time.time()
        self.last_tool = ""
        self._proc = subprocess.Popen(cmd, cwd=str(S.CLAUDE_CWD), env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stop = threading.Event()
        threading.Thread(target=_status_ticker, args=(stop, on_status, lambda: ""), daemon=True).start()
        out = err = ""
        try:
            out, err = self._proc.communicate(timeout=S.HERMES_TURN_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            out, err = self._proc.communicate()
        finally:
            stop.set()
            self._proc = None
        result = out.strip()
        noise = [ln for ln in (out + "\n" + err).splitlines() if ln.strip() and "HERMES_HOME fallback" not in ln]
        if "session not found" in (out + err).lower():
            self.new_thread()                       # the profile's store lost it; start a fresh thread
            if retry:
                return self._ask(text, on_status, retry=False)
        if not result or result.lower().startswith("hermes -z: agent failed"):
            tail = noise[-1] if noise else "no reply"
            return f"I did not get an answer back from {self.name}. The error was: {tail}"
        if not self.session_id:
            sid = self._newest_session(started)
            if sid:
                self._save_session(sid)
        _log(self.name, text, result)
        return result

    def cancel(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.terminate()


# =============================================================================================
class GeminiBrain:
    """One Gemini conversation through Google's Antigravity command-line tool (`agy`), which is
    what Google now points individual Gemini subscribers at: `agy -p <text> --conversation <id>`.
    It answers in JSON with the conversation id, so resuming is exact."""

    name = "Gemini"
    target = "gemini"

    def __init__(self) -> None:
        self.session_id: Optional[str] = self._load_session()
        self.last_tool = ""
        self._proc: Optional[subprocess.Popen] = None

    def _load_session(self) -> Optional[str]:
        try:
            sid = S.GEMINI_SESSION_FILE.read_text().strip()
            return sid or None
        except OSError:
            return None

    def _save_session(self, sid: str) -> None:
        S.GEMINI_SESSION_FILE.write_text(sid)
        self.session_id = sid

    def new_thread(self) -> None:
        try:
            S.GEMINI_SESSION_FILE.unlink()
        except OSError:
            pass
        self.session_id = None

    def ask(self, text: str, on_status: StatusCb = lambda s: None, on_text: TextCb = lambda s: None) -> str:
        cmd = [S.GEMINI_BIN, "-p", text + "\n\n" + S.HERMES_VOICE_NOTE, "--output-format", "json",
               "--model", S.GEMINI_MODEL, "--dangerously-skip-permissions"]
        if self.session_id:
            cmd += ["--conversation", self.session_id]      # a lost id only warns; a fresh one comes back
        env = dict(os.environ, VOICE_DESK_LOOP="1")
        self.last_tool = ""
        self._proc = subprocess.Popen(cmd, cwd=str(S.CLAUDE_CWD), env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stop = threading.Event()
        threading.Thread(target=_status_ticker, args=(stop, on_status, lambda: ""), daemon=True).start()
        out = err = ""
        try:
            out, err = self._proc.communicate(timeout=S.GEMINI_TURN_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            out, err = self._proc.communicate()
        finally:
            stop.set()
            self._proc = None
        result = ""
        try:
            data = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
            result = (data.get("response") or "").strip()
            if data.get("conversation_id"):
                self._save_session(data["conversation_id"])
        except (ValueError, IndexError):
            result = out.strip()
        if not result:
            lines = [ln for ln in err.strip().splitlines() if ln.strip()]
            tail = lines[-1] if lines else "no reply"
            return f"I did not get an answer back from Gemini. The error was: {tail}"
        _log(self.name, text, result)
        return result

    def cancel(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.terminate()


# =============================================================================================
def hermes_profiles() -> list[str]:
    """Hermes profiles on this Mac that can hold a conversation (they have their own session store)."""
    try:
        return sorted(p.name for p in S.HERMES_PROFILES_DIR.iterdir()
                      if p.is_dir() and (p / "config.yaml").exists() and p.name not in S.HERMES_SKIP_PROFILES)
    except OSError:
        return []


def hermes_display_name(profile: str) -> str:
    return S.HERMES_DISPLAY_NAMES.get(profile) or profile.replace("_", " ").replace("-", " ").title()


def _plain(text: str) -> str:
    """lowercase, punctuation to spaces, single spaces (CJK kept)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").lower())).strip()


def name_aliases() -> list[tuple[str, str]]:
    """(spoken form, target) for every name on this Mac, longest first so 'tax angel esther'
    wins over 'tax angel'. Names in settings.NAMES first; any other Hermes profile answers to
    its profile name in words (e.g. 'builder')."""
    pairs: list[tuple[str, str]] = []
    have = {f"hermes:{p}" for p in hermes_profiles()} | {"claude"} | ({"gemini"} if Path(S.GEMINI_BIN).exists() else set())
    if shutil.which(S.CODEX_BIN):
        have.add("codex")
    for target, (display, spoken) in S.NAMES.items():
        if target in have:
            pairs += [(_plain(a), target) for a in (display, *spoken)]
    named = {t for _, t in pairs}
    for p in hermes_profiles():
        if f"hermes:{p}" not in named:
            pairs.append((_plain(p.replace("_", " ").replace("-", " ")), f"hermes:{p}"))
    return sorted(set(pairs), key=lambda x: len(x[0]), reverse=True)


# Greetings that may come before a name (agents.toml [shared] greetings). Peter 2026-10-22: "hi", "hey"
# and "hello" must all wake an agent; the extra spellings are how whisper writes a short greeting.
_GREET = "(?:" + "|".join(re.escape(g) for g in S.GREETINGS) + ")"
_LEAD = rf"(?:(?:{_GREET}|oh|um|so)\s+|(?:嘿|嗨|哈囉)\s*)*"
_SWITCH_CORE = (r"(?:please\s+)?(?:let me\s+|can i\s+|could i\s+|i want to\s+|i'd like to\s+)?"
                r"(?:talk|speak|switch|switch over|hand me over|put me through|go back)\s+(?:to|with)\s+(?:the\s+)?")
_SWITCH = rf"(?:{_SWITCH_CORE})?"
_TAIL = r"(?:\s+(?:please|now|instead)(?=$))?"     # only when nothing follows: "Esther, now that..." keeps "now"
# A hail is what wakes a SLEEPING listener (Peter, 2026-10-21: a bare "Jarvis" fires too easily in
# conversation): "hey Jarvis", "ok Kylie", "talk to Esther", "嘿 克勞德". Fillers alone ("um Jarvis") do not count.
_HAIL = rf"(?:(?:oh|um|so|uh)\s+)*(?:{_GREET}\s+|(?:嘿|嗨|哈囉)\s*)+"
_HAILED = rf"(?:{_HAIL}{_SWITCH}|{_LEAD}{_SWITCH_CORE})"


def _plain_map(text: str) -> tuple[str, list[int]]:
    """_plain(text) plus, for every character of it, the index it came from in text."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text or ""):
        for c in ch.lower():
            if re.match(r"[\w']", c):
                out.append(c); idx.append(i)
            elif out and out[-1] != " ":
                out.append(" "); idx.append(i)
    while out and out[-1] == " ":
        out.pop(); idx.pop()
    return "".join(out), idx


def addressed(text: str, hailed: bool = False) -> Optional[tuple[str, str]]:
    """Does this sentence START with a name? 'Jarvis, what time is it' ->
    ('hermes:orchestrator', 'what time is it'); 'hey Kylie' / 'talk to Esther' ->
    (target, ''); 'I told Jarvis yesterday' -> None. The rest keeps the speaker's own words.
    hailed=True (the listener asleep): the name alone is not enough, it needs a hail in front,
    'hey Jarvis' / 'ok Kylie' / 'talk to Esther'; a bare 'Jarvis, what time is it' -> None."""
    raw = (text or "").strip()
    plain, idx = _plain_map(raw)
    if not plain:
        return None
    front = _HAILED if hailed else f"{_LEAD}{_SWITCH}"
    for alias, target in name_aliases():
        # an English name must end at a word edge ("jarvis" is not "jarvisville"); 克勞德 needs no space
        edge = r"(?=\s|$)" if re.search(r"[a-z0-9]$", alias) else ""
        m = re.match(rf"^{front}{re.escape(alias)}{edge}{_TAIL}{edge}", plain)
        if not m:
            continue
        rest = raw[idx[m.end() - 1] + 1:]
        return target, rest.strip().lstrip(",.!?;:，。！？、 ").strip()
    return None


class Brain:
    """Front door: picks the thread ("claude" or "hermes:<profile>"), handles "talk to X"."""

    def __init__(self) -> None:
        self.claude = ClaudeBrain()
        self.live = {t: LiveBrain(t) for t in ("claude", "gemini", "codex")}   # his open herdr windows
        self.gemini = GeminiBrain()
        self.codex = CodexBrain()
        self._hermes: dict[str, HermesBrain] = {}
        self._current = self.claude

    # -- targets --------------------------------------------------------------------------
    def targets(self) -> list[str]:
        return (["claude"] + (["gemini"] if Path(S.GEMINI_BIN).exists() else [])
                + (["codex"] if shutil.which(S.CODEX_BIN) else [])
                + [f"hermes:{p}" for p in hermes_profiles()])

    def target(self) -> str:
        """The sticky target (what the Mac window talks to), set by voice."""
        try:
            t = S.BRAIN_FILE.read_text().strip()
        except OSError:
            t = ""
        return t if t in self.targets() else "claude"

    def set_target(self, target: str) -> None:
        S.BRAIN_FILE.write_text(target)

    def backend(self, target: Optional[str] = None):
        t = self.resolve(target) if target else self.target()
        if not t or t == "claude":
            return self.live["claude"] if self.live["claude"].available() else self.claude
        if t == "gemini":
            return self.live["gemini"] if self.live["gemini"].available() else self.gemini
        if t == "codex":
            return self.live["codex"] if self.live["codex"].available() else self.codex
        profile = t.split(":", 1)[1]
        live = HermesLiveBrain(profile, hermes_display_name(profile))   # his open Hermes window, if any
        if live.available():
            return live
        if profile not in self._hermes:
            self._hermes[profile] = HermesBrain(profile)
        return self._hermes[profile]

    def name(self, target: Optional[str] = None) -> str:
        return self.backend(target).name

    def resolve(self, spoken: str) -> Optional[str]:
        """'claude' / 'esther' / 'tax angel esther' / 'hermes:tax_angel_esther' -> a target id."""
        s = re.sub(r"[^\w\s:\-]", "", (spoken or "").lower()).strip()
        if not s:
            return None
        if s in self.targets():
            return s
        if s in ("claude", "claude code"):
            return "claude"
        if s in ("gemini", "google", "google gemini", "antigravity") and "gemini" in self.targets():
            return "gemini"
        s = re.sub(r"^(the|to)\s+", "", s)
        for alias, target in name_aliases():            # "jarvis", "kylie", "wanna buffet", ...
            if s == alias:
                return target
        hits = []
        for p in hermes_profiles():
            words = [w for w in re.split(r"[_\-\s]+", p.lower()) if w]
            full = " ".join(words)
            display = hermes_display_name(p).lower()
            if s in (p.lower(), full, display, f"hermes {full}", f"hermes {display}") or s in words:
                hits.append(f"hermes:{p}")
        return hits[0] if len(hits) == 1 else None

    # -- the "talk to X" command ------------------------------------------------------------
    def command(self, text: str) -> Optional[str]:
        norm = re.sub(r"[^\w\s]", "", text.lower()).strip()
        if re.search(r"\b(who am i talking to|who is this|which (brain|profile|agent)( is this)?)\b", norm):
            return f"You are talking to {self.name()}."
        if re.search(r"\b(who|what|which)\b.*\b(can i talk to|is available|profiles)\b", norm):
            names = [self.name(t) for t in self.targets()]
            return "You can talk to " + ", ".join(names[:-1]) + (f" or {names[-1]}." if len(names) > 1 else ".")
        m = re.search(r"^(?:ok(?:ay)?\s+)?(?:please\s+)?(?:let me\s+)?(?:talk|speak|switch|switch over|hand me over|put me through)"
                      r"\s+(?:to|with)\s+(.+?)(?:\s+(?:please|now|instead))?$", norm)
        if not m:
            return None
        wanted = m.group(1).strip()
        if wanted in ClaudeBrain.MODELS or len(wanted.split()) > 4 or wanted.startswith("me ") or " about " in f" {wanted} ":
            return None                             # "switch to Opus" is a model change; "talk to me about X" is a sentence
        target = self.resolve(wanted)
        if not target:
            if wanted in ("hermes", "a hermes profile"):
                return "Which Hermes profile? " + self.command("who can I talk to")
            return f"I do not know anyone called {wanted}. " + self.command("who can I talk to")
        self.set_target(target)
        who = self.name(target)
        return f"Okay, you are talking to {who} now." if target != "claude" else "Okay, back to Claude."

    # -- one turn -----------------------------------------------------------------------------
    def ask(self, text: str, on_status: StatusCb = lambda s: None, on_text: TextCb = lambda s: None,
            to: Optional[str] = None) -> str:
        """`to` (a target id or a spoken name) overrides the sticky target for this turn only;
        the phone Shortcuts use it so each one talks to its own thread."""
        handled = self.command(text)
        if handled:
            _log("voice", text, handled)
            return handled
        backend = self.backend(to)
        self._current = backend
        with open(S.TURN_LOCK_FILE, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)          # phone, window and listener take turns
            # another door may have moved this thread on (or started a new one) since we last looked
            backend.session_id = backend._load_session()
            return backend.ask(text, on_status, on_text)

    @property
    def last_tool(self) -> str:
        return self._current.last_tool

    def new_thread(self, to: Optional[str] = None) -> None:
        self.backend(to).new_thread()

    def cancel(self) -> None:
        self._current.cancel()
