"""Agent Control Desk: the three things only the old Voice Desk dashboard could do, so the desk can replace it.

    1. the voice test    one agent or everyone: a sentence is made in each voice to a throw-away file and
                         checked for real sound. Nothing is ever played. (the old T / Shift-T, voicetest.py)
    2. add / remove      an agent's block in the Voice Desk list (agents.toml), through the old dashboard's own
                         safe path (gridedit.py: a copy first, an atomic write, a read back, put back at once
                         when the read back is wrong). u undoes it. The same safe path also keeps the old
                         dashboard's marks (here / not here, the activation phrase: X and P) and an agent's other
                         languages (extra language voices and the language choice: L and O; stored and shown only).
    3. the listener      what it is doing, and mute / hush / new conversation / hand the microphone back /
                         restart / stop (service.py, the same orders the old second screen sent).

Every one of them goes through the desk's armed gate (acd_edit.armed): on a desk that is not armed nothing is
made, written, sent or restarted, and the result says exactly what WOULD happen. Stopping or restarting the
listener is refused while someone is speaking or an agent is mid-reply, and needs two separate confirmations.

Nothing here opens the microphone, and nothing here is called by the desk's 45 second read.
"""
from __future__ import annotations

import datetime as _dt
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import agents as A
import acd_data as D
import acd_edit as E
import gridedit
import voiceedit

WHO = E.WHO


def _dry(paths: E.EditPaths, what: str, lines: Optional[list] = None, needs: str = "") -> E.Result:
    return E.Result(True, f"DRY RUN, nothing written ({E.why_not_armed(paths)}). This would happen: {what}",
                    dry=True, lines=list(lines or []), needs=needs)


def _stamp(now: _dt.datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


# ---- 1. the voice test -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Spoken:
    """One sentence the test would make: who, in which language, in which voice, and the words."""
    key: str
    name: str
    lang: str
    voice: str
    sentence: str


LANG_WORD = {"en": "English", "zh": "Chinese"}


def _listed(paths: E.EditPaths, keys: Optional[list] = None) -> tuple[list, dict]:
    cfg = A.load(paths.voice.toml)
    everyone = A.agents(cfg)
    if keys is None:
        return everyone, cfg
    by_key = {a.key: a for a in everyone}
    missing = [k for k in keys if k not in by_key]
    if missing:
        raise E.EditError(f"{', '.join(missing)} is not in the Voice Desk list; nothing tested")
    return [by_key[k] for k in keys], cfg


def test_plan(paths: E.EditPaths, keys: Optional[list] = None) -> tuple[list[Spoken], list[str]]:
    """(every sentence the test would make, the names of the agents with no voice at all). keys None = everyone."""
    import voicetest
    who, cfg = _listed(paths, keys)
    out: list[Spoken] = []
    silent: list[str] = []
    for a in who:
        voices = voicetest.voices_to_test(a, cfg)
        if not voices:
            silent.append(a.display)
        for lang, voice in voices:
            words = voicetest.SENTENCES.get(lang, voicetest.SENTENCES["en"]).format(name=a.display)
            out.append(Spoken(a.key, a.display, lang, voice, words))
    return out, silent


def plan_lines(plan: list[Spoken], silent: list[str]) -> list[str]:
    out = [f"{s.name}, {LANG_WORD.get(s.lang, s.lang)}, voice {D.voice_label(s.voice)}: \"{s.sentence}\"" for s in plan]
    out += [f"{name}: no voice is set, so there is nothing to test (it stays silent)" for name in silent]
    return out


def test_voices(paths: E.EditPaths, keys: Optional[list] = None, *, synth: Optional[Callable] = None,
                probe: Optional[Callable] = None, now: Optional[_dt.datetime] = None) -> E.Result:
    """The old dashboard's silent voice test, for one agent, some, or everyone (keys None). Each sentence is made
    to a throw-away file with the agent's own speaker and checked for real sound; NOTHING IS PLAYED. Not armed =
    nothing is made: the result lists exactly what would be said and in which voice."""
    import voicetest
    now = now or _dt.datetime.now()
    try:
        plan, silent = test_plan(paths, keys)
        who, cfg = _listed(paths, keys)
    except (E.EditError, OSError, ValueError) as exc:
        return E.Result(False, f"Not tested. {exc}")
    names = ", ".join(a.display for a in who) or "nobody"
    if not plan:
        return E.Result(True, f"Nothing to test for {names}: no voice is set.", lines=plan_lines(plan, silent))
    what = (f"{len(plan)} test sentence{'s are' if len(plan) != 1 else ' is'} made to a throw-away file for {names} "
            "and checked for real sound. Nothing is played out loud.")
    if not E.armed(paths):
        return _dry(paths, what, ["+ " + ln for ln in plan_lines(plan, silent)])
    kwargs: dict = {}
    if synth is not None:
        kwargs["synth"] = synth
    if probe is not None:
        kwargs["probe_fn"] = probe
    lines: list[str] = []
    failed: list[str] = []
    saved = voicetest.load(paths.state / "voice_tests.json")
    for a in who:
        results = voicetest.run(a, cfg, **kwargs)
        saved[a.key] = results
        for r in results:
            label = f"{a.display}, {LANG_WORD.get(r.lang, r.lang)}, voice {D.voice_label(r.voice)}"
            if r.ok:
                lines.append(f"+ passed: {label}: {r.detail}")
            else:
                lines.append(f"- FAILED: {label}: {r.detail}")
                failed.append(f"{a.display} ({LANG_WORD.get(r.lang, r.lang)})")
    lines += [name + ": no voice is set, so nothing was tested (it stays silent)" for name in silent]
    try:
        paths.state.mkdir(parents=True, exist_ok=True)
        voicetest.save(saved, paths.state / "voice_tests.json")
    except OSError:
        pass
    entry = {"id": f"{_stamp(now)}-voice-test", "ts": now.timestamp(), "who": WHO, "kind": "voice_test",
             "what": f"voice test for {names}: " + ("all passed" if not failed else "FAILED for " + ", ".join(failed)),
             "ok": True}
    E._append_history(paths, entry)
    if failed:
        return E.Result(False, f"Voice test FAILED for {', '.join(failed)}: that line would be silent. "
                               "Nothing was played.", lines=lines, entry=entry)
    return E.Result(True, f"Voice test passed for {names}: every voice made real sound. Nothing was played.",
                    lines=lines, entry=entry)


# ---- 2. add / remove an agent in the Voice Desk list ---------------------------------------------------------
CLI = "::cli"           # "another assistant that runs in a terminal", not a Hermes profile


def addable(paths: E.EditPaths) -> list[tuple[str, str]]:
    """(profile, its display name) for every Hermes profile on this Mac that is not in the Voice Desk list yet."""
    import brain
    try:
        cfg = A.load(paths.voice.toml)
    except (OSError, ValueError):
        return []
    return [(p, brain.hermes_display_name(p)) for p in gridedit.addable_profiles(cfg, paths.voice.profiles)]


def draft_agent(display: str, profile: Optional[str]) -> A.Agent:
    """The agent as it will be, for the voice pick-list, before anything is written."""
    return gridedit.draft(gridedit.key_for(profile or display), display, profile=profile)


def _in_sandbox(paths: E.EditPaths, run: Callable[[voiceedit.EditPaths], voiceedit.Result]) -> tuple[Any, list[str]]:
    """Run a change on throw-away copies of every file it could touch and return (its result, the line changes)."""
    root, box, pairs = E._sandbox(paths.voice)
    try:
        before = {live: copy.read_text(encoding="utf-8") for live, copy in pairs.items()}
        res = run(box)
        lines: list[str] = []
        for live, copy in pairs.items():
            after = copy.read_text(encoding="utf-8") if copy.exists() else ""
            if after != before[live]:
                lines.append(f"in {E._file_words(live)}:")
                lines += ["  " + ln for ln in E.diff_lines(before[live], after)]
        return res, lines
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _plain(message: str) -> str:
    """The old dashboard's own sentence, with its key names taken out (the desk's undo key is u)."""
    return (message.replace(" U takes it off again.", "").replace(" U puts it back.", "")
            .replace("the grid", "the Voice Desk list").replace("agents.toml", "the Voice Desk list file"))


def _record(paths: E.EditPaths, res: voiceedit.Result, now: _dt.datetime, did: str, what: str,
            undoes: Optional[str] = None, needs: str = "") -> E.Result:
    needs = needs or NEEDS_AGENT
    grid = dict(res.entry or {})
    entry = {"id": f"{_stamp(now)}-{grid.get('agent')}-agent-{did}", "ts": now.timestamp(), "who": WHO,
             "kind": "agent", "agent": grid.get("agent"), "did": did, "what": what, "ok": True, "grid": grid}
    if undoes:
        entry["undoes"] = undoes
    E._append_history(paths, entry)
    return E.Result(True, ("Undone: " if undoes else "Saved: ") + what, needs=needs, entry=entry)


NEEDS_AGENT = ("Only the Voice Desk list changed. The voice line answers to a new name, or stops answering to a "
               "removed one, after the listener's next restart. No agent's own files were touched.")


def setup_report(paths: E.EditPaths, profile: str, display: str, *, loader: Optional[Callable] = None,
                 windows: Any = None) -> E.Result:
    """What the new agent's column will show: the Hermes connector's look at the profile's own files (acd_setup.py).
    Only reads, on an armed desk and a dry run alike; never fails the add it follows."""
    import acd_setup
    folder = Path(paths.profiles) / profile
    try:
        cfg = (loader or D.hermes_yaml)(folder / "config.yaml")
    except Exception as exc:                            # noqa: BLE001  a look must never take the desk down
        cfg = exc
    if windows is None:
        windows = acd_setup.open_windows(paths.herdr)
    found = acd_setup.hermes_setup(profile, display, cfg, paths.profiles,
                                   platforms=D.read_gateway_platforms(paths.gateway_state),
                                   members=D.read_members(paths.members) if paths.members else None, windows=windows)
    gaps = [p.label.lower() for p in found.pieces if p.status == D.RED]
    blind = [p.label.lower() for p in found.missing if p.status != D.RED]
    head = (f"What {display}'s column shows, read from the Hermes profile {profile} (nothing in it was changed): "
            + (f"not set up or not working yet: {', '.join(gaps)}" if gaps else "nothing the desk checks is missing")
            + (f"; could not be checked: {', '.join(blind)}." if blind else "."))
    if found.error:
        head = f"{display}'s settings file could not be read, so its column cannot be filled ({found.error})."
    return E.Result(True, head, lines=acd_setup.lines(found))


def add_agent(paths: E.EditPaths, display: str, names: str, voice: Optional[str], *, profile: Optional[str] = None,
              now: Optional[_dt.datetime] = None) -> E.Result:
    """A new block in the Voice Desk list for a Hermes profile (profile=...) or another terminal assistant."""
    now = now or _dt.datetime.now()
    display = (display or "").strip()
    sound = f"speaks as {D.voice_label(voice)}" if voice else "silent (no voice)"
    kind = f"the Hermes agent {profile}" if profile else "another terminal assistant"
    what = f"{display or 'the new agent'} is added to the Voice Desk list ({kind}; {sound})"
    try:
        if not E.armed(paths):
            res, lines = _in_sandbox(paths, lambda box: gridedit.add_agent(box, display, names, voice or None,
                                                                           profile=profile, now=now))
            if not res.ok:
                return E.Result(False, "Not added. " + _plain(res.message))
            return _dry(paths, what + ".", lines, NEEDS_AGENT)
        res = gridedit.add_agent(paths.voice, display, names, voice or None, profile=profile, now=now)
    except (voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"Not added. {exc}")
    if not res.ok:
        return E.Result(False, "Not added. " + _plain(res.message))
    return _record(paths, res, now, "added", what)


@dataclass(frozen=True)
class Removal:
    """Exactly what taking an agent out of the Voice Desk list removes, and what stays."""
    key: str
    name: str
    block: tuple        # the lines that go, as they are in the file
    stays: str


def removal_plan(paths: E.EditPaths, key: str) -> Removal:
    text = Path(paths.voice.toml).read_text(encoding="utf-8")
    agent = voiceedit._find_agent(A.load(paths.voice.toml), key)
    block = gridedit.block_text(text, key) or ""
    stays = ((f"Its Hermes profile {agent.profile}, its settings, its chats and its Telegram bot all stay. "
              if agent.profile else "Every other file stays. ")
             + "Only these lines of the Voice Desk list go, and a copy of the list is kept first.")
    return Removal(key, agent.display, tuple(ln for ln in block.splitlines() if ln.strip()), stays)


def remove_agent(paths: E.EditPaths, key: str, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Take an agent's block out of the Voice Desk list. Nothing else is touched; u puts it back."""
    now = now or _dt.datetime.now()
    try:
        plan = removal_plan(paths, key)
        what = f"{plan.name} is taken out of the Voice Desk list ({len(plan.block)} lines; nothing else is touched)"
        if not E.armed(paths):
            res, lines = _in_sandbox(paths, lambda box: gridedit.remove_agent(box, key, now=now))
            if not res.ok:
                return E.Result(False, "Not removed. " + _plain(res.message))
            return _dry(paths, what + ".", lines, NEEDS_AGENT)
        res = gridedit.remove_agent(paths.voice, key, now=now)
    except (voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"Not removed. {exc}")
    if not res.ok:
        return E.Result(False, "Not removed. " + _plain(res.message))
    return _record(paths, res, now, "removed", what)


def undo_agent(last: dict, paths: E.EditPaths, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Run an add or a remove backwards with the old dashboard's own undo (refused, and the copy named, when the
    block was changed by hand since)."""
    now = now or _dt.datetime.now()
    what = "undo of: " + str(last.get("what"))
    grid = dict(last.get("grid") or {})
    needs = {"grid:here": NEEDS_HERE, "grid:phrase": NEEDS_PHRASE}.get(str(grid.get("field")), NEEDS_AGENT)
    try:
        if not E.armed(paths):
            res, lines = _in_sandbox(paths, lambda box: gridedit.undo(grid, box))
            if not res.ok:
                return E.Result(False, "Not undone. " + _plain(res.message))
            return _dry(paths, what + ".", lines, needs)
        res = gridedit.undo(grid, paths.voice)
    except (voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"Not undone. {exc}")
    if not res.ok:
        return E.Result(False, "Not undone. " + _plain(res.message))
    return _record(paths, res, now, "undone", str(last.get("what")), undoes=last["id"], needs=needs)


# ---- 2b. here / not here, and the activation phrase (the old dashboard's X and P keys) -----------------------
NEEDS_HERE = ("Only the Voice Desk list changed. It is a mark the desk shows: it switches nothing on or off, "
              "restarts nothing, and no agent's own files were touched.")
NEEDS_PHRASE = ("Stored and shown only: it does not rename a Siri shortcut or change the wake words yet. Only the "
                "Voice Desk list changed, and nothing is restarted.")
PHRASE_HINT = {
    "terminal": "What starts it in its window, for example the command name.",
    "tg_dm": "The bot Peter writes to, for example @JarvisBot.",
    "tg_group": "The group, for example Team_Room.",
    "mic": "The words that wake it at the Mac's microphone, for example hello Jarvis.",
    "iphone": "The Siri shortcut's name, for example Talk to Jarvis.",
}
PHRASE_MAX = 80


def wire_place_name(place: str) -> str:
    import gridstate as G
    return G.PLACE_NAME.get(place, place)


def _wire_ready(paths: E.EditPaths, key: str, place: str):
    """(the agent, its grid cells) or an EditError in plain words. Only reads."""
    import gridstate as G
    if paths.grid is None:
        raise E.EditError("The Voice Desk's own files are not known to the desk.")
    if place not in G.PLACE_KEYS:
        raise E.EditError(f"{place} is not a place the Voice Desk list knows.")
    agent = voiceedit._find_agent(A.load(paths.voice.toml), key)
    return agent, G.cells_for(agent, paths.grid)


def set_here(paths: E.EditPaths, key: str, place: str, here: bool, *,
             now: Optional[_dt.datetime] = None) -> E.Result:
    """Mark an agent here, or not here, at one place, with the old dashboard's own writer (gridedit.set_here: a
    copy first, an atomic write, a read back, put back at once when the read back is wrong). u undoes it."""
    import gridstate as G
    now = now or _dt.datetime.now()
    word = "here" if here else "not here"
    try:
        agent, cells = _wire_ready(paths, key, place)
        name = wire_place_name(place)
        what = f"{agent.display} is marked {word} at {name}"
        if G.here_map(agent, cells)[place] == here:
            return E.Result(True, f"{agent.display} is already marked {word} at {name}; nothing to change")
        if here and G.unwired(cells[place], place):
            what += f" (nothing makes it speak there yet: {G.wiring_hint(agent, place)})"
        if not E.armed(paths):
            res, lines = _in_sandbox(paths, lambda box: gridedit.set_here(box, key, place, here, cells, now=now))
            if not res.ok:
                return E.Result(False, "Not changed. " + _plain(res.message))
            return _dry(paths, what + ".", lines, NEEDS_HERE)
        res = gridedit.set_here(paths.voice, key, place, here, cells, now=now)
    except (E.EditError, voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    if not res.ok or res.entry is None:
        return E.Result(False, "Not changed. " + _plain(res.message))
    return _record(paths, res, now, "here" if here else "nothere", what, needs=NEEDS_HERE)


def phrase_now(paths: E.EditPaths, key: str, place: str) -> tuple[str, bool, str]:
    """(the phrase shown today, is it stored, today's default when none is stored). Only reads."""
    import gridstate as G
    agent = voiceedit._find_agent(A.load(paths.voice.toml), key)
    default = G.default_phrase(agent, place)
    stored = G.phrase_map(agent).get(place)
    return (stored if stored is not None else default), stored is not None, default


def set_phrase(paths: E.EditPaths, key: str, place: str, text: Optional[str], *,
               now: Optional[_dt.datetime] = None) -> E.Result:
    """Store the activation phrase of one place (empty = back to today's default), with the old dashboard's own
    writer (gridedit.set_phrase). Stored and shown only: it renames nothing. u undoes it."""
    import gridstate as G
    now = now or _dt.datetime.now()
    text = " ".join((text or "").split())
    todays = " (today's)"
    try:
        agent, cells = _wire_ready(paths, key, place)
        name = wire_place_name(place)
        if not G.here_map(agent, cells)[place]:
            return E.Result(False, f"Not changed. {agent.display} is marked not here at {name}: mark it here first.")
        if len(text) > PHRASE_MAX:
            return E.Result(False, f"Not changed. An activation phrase of at most {PHRASE_MAX} characters, please.")
        have, stored, default = phrase_now(paths, key, place)
        if (text and stored and text == have) or (not text and not stored):
            return E.Result(True, f"{agent.display} at {name}: already "
                                  f"{have or 'no phrase'}{'' if stored else todays}; nothing to change")
        new = text or (f"today's ({default})" if default else "none")
        what = (f"{agent.display} at {name}: the activation phrase changes from "
                f"{have or 'none'}{'' if stored or not have else todays} to {new}")
        if not E.armed(paths):
            res, lines = _in_sandbox(paths, lambda box: gridedit.set_phrase(box, key, place, text, now=now))
            if not res.ok:
                return E.Result(False, "Not changed. " + _plain(res.message))
            return _dry(paths, what + ".", lines, NEEDS_PHRASE)
        res = gridedit.set_phrase(paths.voice, key, place, text, now=now)
    except (E.EditError, voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    if not res.ok or res.entry is None:
        return E.Result(False, "Not changed. " + _plain(res.message))
    return _record(paths, res, now, "phrase", what, needs=NEEDS_PHRASE)


# ---- 2c. other languages: extra language voices and the language choice (the old dashboard's L and O keys) -----
NEEDS_LANG = ("Stored and shown only: nothing speaks an extra language or follows the language choice yet. Only one "
              "line of the Voice Desk list changes, and nothing is restarted.")
EXTRA_HINT = ("Type code=voice, separated by commas, for example ja=ja-JP-NanamiNeural, fr=fr-FR-DeniseNeural "
              "(a Microsoft voice of that language). English and Chinese have their own rows. Leave it empty to "
              "remove them all.")
LANG_NAME = {"en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German",
             "es": "Spanish", "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "th": "Thai", "vi": "Vietnamese",
             "id": "Indonesian", "ms": "Malay", "ru": "Russian", "ar": "Arabic", "hi": "Hindi"}
AUTO = "auto"


def lang_name(code: str) -> str:
    return LANG_NAME.get(code, code)


@dataclass(frozen=True)
class Languages:
    """An agent's languages beyond English and Chinese, and which language it speaks in, as the list says now."""
    key: str
    name: str
    extra: dict                     # {"ja": "ja-JP-NanamiNeural"}
    override: str                   # "auto" (follow the reply's language), "en", "zh" or an extra code
    english_only: bool


def languages_now(paths: E.EditPaths, key: str) -> Languages:
    """Only reads."""
    agent = voiceedit._find_agent(A.load(paths.voice.toml), key)
    return Languages(key, agent.display, dict(agent.voices_extra), str(agent.voice_override or AUTO).lower(),
                     agent.english_only)


def extra_words(extra: dict) -> str:
    """"Japanese (Nanami), French (Denise)", or "none"."""
    return ", ".join(f"{lang_name(c)} ({D.voice_label(v)})" for c, v in extra.items()) or "none"


def choice_words(code: str) -> str:
    return "follow the reply's language" if code == AUTO else f"always {lang_name(code)}"


def choice_codes(lang: Languages) -> list[str]:
    """The language choices this agent can take: an English-only agent only follows the reply or speaks English."""
    return [AUTO, "en"] if lang.english_only else [AUTO, "en", "zh", *lang.extra]


def _lang_write(paths: E.EditPaths, key: str, field_name: str, value: Any, what: str, now: _dt.datetime,
                undoes: Optional[str] = None, restore: Optional[tuple] = None) -> E.Result:
    """One line of the Voice Desk list through the old dashboard's own writer (voiceedit.change: a copy first, an
    atomic write, a read back, put back at once when it does not read back), behind the desk's armed gate."""
    kind = "undo" if undoes else "change"

    def run(vp: voiceedit.EditPaths) -> voiceedit.Result:
        return voiceedit.change(key, field_name, value, vp, now=now, kind=kind, undoes=undoes, restore=restore)

    try:
        if not E.armed(paths):
            res, lines = _in_sandbox(paths, run)
            if not res.ok:
                return E.Result(False, ("Not undone. " if undoes else "Not changed. ") + _plain(res.message))
            if res.entry is None:
                return E.Result(True, _plain(res.message))
            return _dry(paths, ("undo of: " if undoes else "") + what + ".", lines, NEEDS_LANG)
        res = run(paths.voice)
    except (E.EditError, voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"{'Not undone' if undoes else 'Not changed'}. {exc}")
    if not res.ok:
        return E.Result(False, ("Not undone. " if undoes else "Not changed. ") + _plain(res.message))
    if res.entry is None:
        return E.Result(True, _plain(res.message))
    entry = {"id": f"{_stamp(now)}-{key}-{field_name}", "ts": now.timestamp(), "who": WHO, "kind": "language",
             "agent": key, "field": field_name, "what": what, "before": res.entry.get("old"),
             "after": res.entry.get("new"), "ok": True}
    if res.entry.get("removed"):
        entry["removed"] = list(res.entry["removed"])     # the line's own text and place, so u puts it back verbatim
    if undoes:
        entry["undoes"] = undoes
    E._append_history(paths, entry)
    return E.Result(True, ("Undone: " if undoes else "Saved: ") + what, needs=NEEDS_LANG, entry=entry)


def check_extra(paths: E.EditPaths, key: str, text: Optional[str]) -> tuple[dict, str]:
    """(the typed extra languages, cleaned, "") or ({}, why not), with the old dashboard's own check. Only reads."""
    try:
        agent = voiceedit._find_agent(A.load(paths.voice.toml), key)
        typed = voiceedit.parse_extra(text or "")
        return (voiceedit.check_voice("voices_extra", typed, agent) if typed else {}), ""
    except (voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return {}, str(exc)


def set_extra(paths: E.EditPaths, key: str, text: Optional[str], *, now: Optional[_dt.datetime] = None) -> E.Result:
    """The extra languages (the old L key): "ja=ja-JP-NanamiNeural, fr=..." ; empty removes them all. Each voice
    must be a Microsoft voice of that language. Stored and shown only. u undoes it."""
    now = now or _dt.datetime.now()
    try:
        lang = languages_now(paths, key)
    except (E.EditError, voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    clean, why = check_extra(paths, key, text)
    if why:
        return E.Result(False, f"Not changed. {why}")
    if clean == lang.extra:
        return E.Result(True, f"{lang.name}'s extra languages are already {extra_words(clean)}; nothing to change")
    if lang.override in lang.extra and lang.override not in clean:
        return E.Result(False, f"Not changed. {lang.name}'s language choice is {choice_words(lang.override)}: set it "
                               "back to follow the reply's language first, then take that language away.")
    what = f"{lang.name}'s extra languages change from {extra_words(lang.extra)} to {extra_words(clean)}"
    return _lang_write(paths, key, "voices_extra", clean or None, what, now)


def set_override(paths: E.EditPaths, key: str, code: str, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """The language choice (the old O key): follow the reply's language ("auto", the default: the line goes), or
    always English, Chinese or one of the extra languages. Stored and shown only. u undoes it."""
    now = now or _dt.datetime.now()
    code = str(code or "").strip().lower() or AUTO
    try:
        lang = languages_now(paths, key)
    except (E.EditError, voiceedit.EditError, OSError, ValueError, KeyError) as exc:
        return E.Result(False, f"Not changed. {exc}")
    allowed = choice_codes(lang)
    if code not in allowed:
        return E.Result(False, f"Not changed. {lang.name} can take only: "
                               + ", ".join(choice_words(c) for c in allowed) + ".")
    if code == lang.override:
        return E.Result(True, f"{lang.name}'s language choice is already: {choice_words(code)}; nothing to change")
    what = f"{lang.name}'s language choice changes from {choice_words(lang.override)} to {choice_words(code)}"
    return _lang_write(paths, key, "voice_override", None if code == AUTO else code, what, now)


def undo_language(last: dict, paths: E.EditPaths, *, now: Optional[_dt.datetime] = None) -> E.Result:
    """Run a language change backwards with the old dashboard's own undo (the removed line goes back verbatim)."""
    now = now or _dt.datetime.now()
    removed = last.get("removed")
    restore = tuple(removed) if isinstance(removed, list) and len(removed) == 2 else None
    return _lang_write(paths, str(last.get("agent")), str(last.get("field")), last.get("before"),
                       str(last.get("what")), now, undoes=str(last.get("id")), restore=restore)


# ---- 3. the listener (the voice line) --------------------------------------------------------------------------
class RealListener:
    """The listener's own plumbing (service.py), exactly what the old dashboard's second screen used."""

    def read_status(self) -> dict:
        import service
        return service.read_status()

    def is_fresh(self, data: dict) -> bool:
        import service
        return service.is_fresh(data)

    def is_muted(self) -> bool:
        import service
        return service.is_muted()

    def set_muted(self, on: bool) -> None:
        import service
        service.set_muted(on)

    def send(self, order: str) -> None:
        import service
        service.send(order)

    def loaded(self) -> Optional[bool]:
        import service
        try:
            return service.service_loaded()
        except Exception:                               # noqa: BLE001  a look must never take the desk down
            return None

    def restart(self) -> str:
        import service
        return service.service_restart()

    def stop(self) -> str:
        import service
        return service.service_stop()

    def live_turn(self) -> bool:
        """True while an agent is answering a spoken turn in its own window (the flag is at most 15 minutes old)."""
        import settings as S
        try:
            return time.time() - S.LIVE_TURN_FLAG.stat().st_mtime < 900
        except OSError:
            return False


STATE_WORDS = {
    "starting": "starting", "asleep": "asleep, waiting for a name", "listening": "listening",
    "hearing": "hearing someone speak", "thinking": "an agent is working on a reply",
    "speaking": "speaking a reply", "handed": "a conversation is open in an agent's window",
    "muted": "muted", "standby": "standing by: an agent's window has the microphone", "error": "error",
    "off": "off: the voice line is not running",
}
BUSY_STATES = {"hearing": "someone is speaking to it right now",
               "thinking": "an agent is in the middle of a reply",
               "speaking": "a reply is being spoken right now",
               "handed": "a conversation is open in an agent's window"}


@dataclass(frozen=True)
class ListenerView:
    state: str                      # one of STATE_WORDS
    words: str
    on_line: str = ""               # who is on the line
    heard: str = ""
    reply: str = ""
    note: str = ""
    since: Optional[float] = None
    muted: bool = False
    loaded: Optional[bool] = None   # the Mac has the listener's job loaded (None = could not tell)
    turns: int = 0
    busy: str = ""                  # why it must not be stopped or restarted right now ("" = it is quiet)

    @property
    def running(self) -> bool:
        return self.state != "off"


def listener_view(svc: Any = None, ask_loaded: bool = True) -> ListenerView:
    """What the listener says about itself, read from its own status file. Read-only."""
    svc = svc or RealListener()
    try:
        data = svc.read_status() or {}
    except Exception:                                   # noqa: BLE001
        data = {}
    try:
        muted = bool(svc.is_muted())
    except Exception:                                   # noqa: BLE001
        muted = False
    if not svc.is_fresh(data):
        state = "off"
    else:
        state = str(data.get("state") or "")
        if state in ("asleep", "listening") and (data.get("muted") or muted):
            state = "muted"
        if state not in STATE_WORDS:
            state = "starting"
    busy = BUSY_STATES.get(state, "")
    if not busy:
        try:
            if svc.live_turn():
                busy = "an agent is answering a spoken turn in its own window"
        except Exception:                               # noqa: BLE001
            pass
    loaded = None
    if ask_loaded:
        try:
            loaded = svc.loaded()
        except Exception:                               # noqa: BLE001
            loaded = None
    return ListenerView(state, STATE_WORDS[state], str(data.get("name") or ""), str(data.get("heard") or ""),
                        str(data.get("reply") or "").strip()[:400], str(data.get("note") or ""),
                        float(data["since"]) if data.get("since") and state != "off" else None, muted, loaded,
                        int(data.get("turns") or 0), busy)


STOPS_THE_LINE = "this stops the voice line"
ACTIONS = {
    "mute": "the listener is muted: it ignores everything it hears until it is unmuted",
    "unmute": "the listener is unmuted: say a name to start",
    "hush": "the listener is told to stop talking (the reply being spoken is cut off)",
    "new_thread": "the next spoken turn starts a fresh conversation with whoever is on the line",
    "release": "the listener takes the microphone back from the agent's window and waits for a name",
    "restart": f"the listener is restarted: {STOPS_THE_LINE} for a few seconds, then it starts again",
    "stop": f"the listener is stopped: {STOPS_THE_LINE}, and it stays off until it is started again or the Mac "
            "is logged in again",
}
NEEDS_TWO = ("restart", "stop")


def listener_do(action: str, paths: E.EditPaths, *, svc: Any = None, confirmed: int = 0,
                now: Optional[_dt.datetime] = None) -> E.Result:
    """One order to the listener. Stop and restart are refused while it is busy, and need two confirmations.
    Not armed = nothing is sent or restarted: the result says what would happen."""
    now = now or _dt.datetime.now()
    svc = svc or RealListener()
    if action not in ACTIONS:
        return E.Result(False, f"{action} is not something the desk does to the listener; nothing done")
    view = listener_view(svc, ask_loaded=False)
    what = ACTIONS[action]
    if action in NEEDS_TWO:
        if view.busy:
            return E.Result(False, f"Not done: {view.busy}. The desk never stops or restarts the voice line in the "
                                   "middle of that; try again when it is quiet.")
        if action == "stop" and not view.running:
            return E.Result(True, "The listener is already off; nothing to stop.")
        if confirmed < 2:
            return E.Result(False, f"Not done: {STOPS_THE_LINE}, so the desk asks twice, and it was confirmed "
                                   f"{confirmed} time{'s' if confirmed != 1 else ''}.")
        if action == "restart" and not view.running:
            what = "the listener is started (it is off now)"
    if not E.armed(paths):
        return _dry(paths, what + ".")
    try:
        if action == "mute":
            svc.set_muted(True)
        elif action == "unmute":
            svc.set_muted(False)
        elif action in ("hush", "new_thread", "release"):
            svc.send(action)
        elif action == "restart":
            said = svc.restart()
            if said not in ("restarted", "started"):
                return E.Result(False, f"The listener was not restarted: {said}")
        else:
            said = svc.stop()
            if said not in ("stopped", "already off"):
                return E.Result(False, f"The listener was not stopped: {said}")
    except Exception as exc:                            # noqa: BLE001  an order that fails must never take the desk down
        return E.Result(False, f"Not done. {type(exc).__name__}: {exc}")
    entry = {"id": f"{_stamp(now)}-listener-{action}", "ts": now.timestamp(), "who": WHO, "kind": "listener",
             "action": action, "what": "listener: " + what, "ok": True}
    E._append_history(paths, entry)
    return E.Result(True, "Done: " + what + ".", entry=entry)


def _for(since: float) -> str:
    secs = max(0, int(time.time() - since))
    if secs < 90:
        return f"{secs} seconds"
    n, unit = (secs // 60, "minute") if secs < 5400 else (secs // 3600, "hour")
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def listener_text_lines(view: ListenerView) -> list[tuple[str, str]]:
    """(label, words) lines for the listener pop-up: at least what the old second screen showed."""
    job = {True: "loaded on this Mac", False: "NOT loaded on this Mac (r starts it)", None: "could not be checked"}
    out = [("State", view.words + (f" (for {_for(view.since)})" if view.since else "")),
           ("On the line", view.on_line or "nobody"),
           ("Muted", "yes: it ignores everything until unmuted" if view.muted else "no"),
           ("Its job", job[view.loaded]),
           ("Heard last", view.heard or "nothing yet"),
           ("Last reply", view.reply or "none yet")]
    if view.note:
        out.append(("Note", view.note))
    out.append(("Turns", str(view.turns)))
    out.append(("Safe to stop", "no: " + view.busy if view.busy else
                "yes, nobody is speaking and no agent is mid-reply" if view.running else "it is already off"))
    return out
