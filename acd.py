"""Agent Control Desk: the screen. ONE table (Peter, 2026-11-17).

    ./acd.sh              every change is a dry run: shown in full, nothing written
    ./acd.sh --armed      changes are written, but only if the desk's own armed switch is also on (acd_edit.py)

Rows are settings, columns are agents: Jarvis, Kylie, Builder, Esther, Wanna Buffet, Simba, then Claude, Gemini
and Codex (those three are there for their voices and the names that wake them; other rows are blank for them).
Their "Mounted session" row picks the ONE open terminal session each of them talks to by voice (acd_mount.py):
green its window is open, yellow mounted but not open (voice then says so and starts nothing else), grey none.
Every cell starts with a light: green live everywhere, yellow saved but waiting on a restart or a fresh chat,
blue a temporary override, red failed or changed behind the desk's back, grey off or not applicable.

  arrows   move cell to cell (they step over the section headings and stop at the edges)
  Enter    on a cell that can be changed: a small menu opens under the cell with the choices, today's marked
           "now". Enter on a choice shows one line that says exactly what will change and whether a restart is
           needed; Enter again saves, Esc goes back.
           on a row's name (the first column): the same menu, applied to every agent the row can be changed
           for, after one confirmation that lists each agent's before and after.
           on a fact (who answered last ...): nothing opens; the strip under the table says why.
  s        the standard fallback ladder for every agent: shows each agent's before and after first, then asks
  u  undo the newest change      r  restart review    c  read again now    q  close
  p  pause / resume the watcher
  t  silent voice test (one agent or everyone; made to files and checked, never played)
  l  the listener: what the voice line is doing, mute / hush / new conversation / restart / stop
  n  add an agent to the Voice Desk list      d  remove one (shows exactly what goes, asks first)
  (plain lower-case letters, no Shift; the hint line on screen shows them the same way)

The strip under the table tells the whole story of the chosen cell: allowance numbers and reset times, why a
reply fell to a backup, what a change would need. It replaces the old tree.

THE FALLBACK LADDER has one fixed order for every agent: rung 1 the main model (the Claude subscription), rung 2
Antigravity (Claude there first, Gemini second), rung 3 ChatGPT (Codex), rung 4 NVIDIA Nemotron. A cell's menu
picks the model for that rung, or off; the order is never a choice. A list that is not in that order is shown
as it is, with a yellow light, and is never reordered just by reading it. A small arrow marks the rung that
answered each agent's last reply; a rung whose allowance is used up is red with the day it comes back.

In a narrow window the row names stay pinned on the left and the agent columns scroll sideways; a line under
the table names the columns (and sections) that are off screen.

It is an overseer, never a lifeline: it reads gently (ONE background read every 45 seconds for all agents),
every change goes through acd_edit.py (backup, atomic write, read back, auto-restore, history, undo), it never
restarts anything itself (r only leaves a request for the existing restart helper), and it never sends anything
to Telegram.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, OptionList, Static
from textual.widgets.option_list import Option

import agents as A
import gridedit
import acd_data as D
import acd_edit as E
import acd_health as H
import acd_mount as M
import acd_parity as P
import acd_watch as W

REFRESH_SEC = 45
LISTENER_SEC = 2                 # the voice line's small status file, for the status line
DRY_LABEL = "dry run: changes are shown in full, nothing is written"
ARMED_LABEL = "ARMED: changes are written (backup, read-back, undo)"
READ_ONLY_LABEL = DRY_LABEL                 # the old name, kept for callers
COLOUR = {D.GREEN: "#3fb950", D.YELLOW: "#d29922", D.BLUE: "#58a6ff", D.RED: "#f85149", D.GREY: "#8b949e"}
DIM = "#8b949e"
MARK = "■"                               # the small square that carries a colour
# Red has two meanings and the legend says both: something failed or ran out, or it was changed behind the desk's back.
LEGEND = ((D.GREEN, "live everywhere"), (D.YELLOW, "waiting on a restart or a fresh chat"),
          (D.BLUE, "temporary override"), (D.RED, "failed, used up, or changed behind the desk's back"),
          (D.GREY, "off or not applicable"))


def mark(status: str) -> Text:
    t = Text()
    t.append(MARK + " ", style=COLOUR.get(status, DIM))
    return t


def line(status: Optional[str], text: str, dim: bool = False, tint: bool = False) -> Text:
    """A coloured square, then the words. tint=True colours the words too (the warning strip)."""
    t = mark(status) if status else Text("  ")
    t.append(text, style=DIM if dim else (COLOUR.get(status, "") if tint and status else ""))
    return t


def effort_words(effort: str) -> str:
    return "extra thinking off" if effort == "none" else f"{effort} effort"


def backup_words(b: D.Backup, short: bool = True) -> str:
    """"Claude Sonnet 5.5 via Antigravity"; the service is left out when the model's name already says it."""
    model = D.model_name(b.model)
    service = D.service_name(b.service, short=short)
    if model.lower().startswith(service.lower().rstrip(".")):
        return model
    return f"{model} via {service}" if short else f"{model} through {service}"


def join(lines: list[Text]) -> Text:
    return Text("\n").join(lines)


def when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%-d %b %H:%M")


def where(platform: str) -> str:
    return D.PLATFORM_NAMES.get(platform, platform)


def legend_text(width: int = 10_000) -> Text:
    """What each light means, broken between two items (never inside one) when the window is narrow."""
    items = []
    for status, words in LEGEND:
        t = Text()
        t.append(MARK + " ", style=COLOUR[status])
        t.append(words, style=DIM)
        items.append(t)
    return flow(items, width)


def warnings_text(snap: D.Snapshot) -> Text:
    return join([line(w.status, w.text, tint=True) for w in snap.warnings])


def state_word(p: D.Presence) -> str:
    return {D.ON: "on", D.OFF: "off", D.UNKNOWN: "unknown"}[p.state]


# ---- the table: rows are settings, columns are agents ----------------------------------------------------
FACT = "a fact read from the logs and the settings, not a setting of its own"
ANSWERED = "▶"                           # on the ladder: the rung that answered the agent's last reply
SHORT_UNKNOWN = {"iphone_voice": "no shortcut"}        # a grey square and the reason, never a bare "?"
SHORT_ANSWER = {"yes, like everyone": "yes", "yes, like every member": "yes", "yes, the only one who does": "yes",
                "yes, by job card only": "by card", "only as job cards": "job cards only",
                "when named in the line": "when named"}
PLACE_ROW = {"terminal": "Has a terminal chat window", "tg_private": "Telegram private chat",
             "tg_group": "Telegram team group", "team_room": "Team Room", "mac_voice": "Mac voice line",
             "iphone_voice": "iPhone voice (Siri)"}
VPLACE_ROW = (("terminal", "Speaks in terminal window"), ("tg_dm", "Speaks in Telegram private"),
              ("tg_group", "Speaks in Telegram group"), ("mic", "Speaks on the mic line"),
              ("iphone", "Speaks on iPhone (Siri)"))
VOICE_ROW = dict(VPLACE_ROW)
# Kylie and Peter 2026-11-18: the WHERE IT SHOWS UP row of the terminal sat beside the real voice switch and was
# taken for it. Its status line says first that it is not the voice switch, and names the row that is.
NOT_SWITCH_HEAD = "This row is not the voice switch"
NOT_THE_VOICE_SWITCH = {"terminal": f"{NOT_SWITCH_HEAD}: it only says whether {{name}} has a terminal chat window. "
                                    f"Whether {{name}} speaks there is the row \"{VOICE_ROW['terminal']}\" "
                                    f"(in VOICE, above)"}
TALK_ROW = (("speaks", "Speaks in the Team Room"), ("hands_out", "Hands out work"),
            ("takes_from", "Takes work from"), ("answers", "Answers"))
SECTIONS = ("LANGUAGE MODEL", "FALLBACK LADDER (one order)", "VOICE", "WHERE IT SHOWS UP",
            "WHO IT MAY TALK TO")


@dataclass(frozen=True)
class Row:
    key: str                        # "model", "rung:codex", "vplace:tg_dm", "presence:team_room", "talk:answers" ...
    label: str
    section: bool = False


@dataclass
class Cell:
    status: Optional[str] = None    # the light. None = this row does not apply to this column: the cell is blank
    words: str = ""                 # the few words in the cell
    detail: list = field(default_factory=list)      # the full story, for the strip under the table
    setting: str = ""               # what Enter changes here ("" = nothing)
    fixed: str = ""                 # for a cell with no menu: why
    marker: bool = False            # the ladder rung that answered the last reply
    short: str = ""                 # fewer words for a column too narrow for `words` (instead of an ellipsis)

    @property
    def blank(self) -> bool:
        return self.status is None and not self.words


def is_agent(col: Any) -> bool:
    return isinstance(col, D.AgentView)


def col_id(col: Any) -> str:
    return col.profile if is_agent(col) else "other:" + col.key


def columns_of(snap: Optional[D.Snapshot]) -> list:
    """The table's columns after the row names: every agent, then Claude, Gemini and Codex."""
    return list(snap.agents) + list(snap.others) if snap else []


# ---- other languages: the old dashboard's L (extra languages) and O (language choice) ----------------------------
LANG_ROW = "Other languages"
LANG_MENU = "::languages"               # the Chinese voice menu's last line: on to the other languages


def lang_facts(col: Any) -> tuple[dict, str, bool]:
    """(extra languages, the language choice, English only) of a column, as the Voice Desk list says."""
    if is_agent(col):
        v = col.voice
        return (dict(v.extra), v.override, v.english_only) if v else ({}, P.AUTO, False)
    return dict(getattr(col, "extra", ())), getattr(col, "override", P.AUTO), getattr(col, "english_only", False)


def has_languages(col: Any) -> bool:
    extra, choice, _ = lang_facts(col)
    return bool(extra) or choice != P.AUTO


def languages_cell(name: str, col: Any) -> Cell:
    """Other languages: stored and shown only, so the light stays grey (nothing speaks them yet)."""
    extra, choice, english_only = lang_facts(col)
    codes = ", ".join(P.lang_name(c) for c in extra)
    detail = [f"Extra languages: {P.extra_words(extra)}.", f"Language choice: {P.choice_words(choice)}.",
              P.NEEDS_LANG]
    if english_only:
        return Cell(D.GREY, "English only" + ("" if choice == P.AUTO else f"; {P.choice_words(choice)}"),
                    [f"{name} speaks English only: no other language, and a Chinese line stays silent."]
                    + detail[1:], setting="languages")
    words = (P.choice_words(choice) + (f"; {codes}" if codes else "")) if choice != P.AUTO else (codes or "none")
    return Cell(D.GREY, words, detail, setting="languages")


def rows_of(snap: Optional[D.Snapshot]) -> list[Row]:
    """Every row of the table, top to bottom. Three rows appear only when some agent has something to show there:
    the extra backups that are not on the ladder, a voice effect, and other languages (the old dashboard's L and O:
    stored and shown only; the Chinese voice menu reaches them for everyone)."""
    agents = snap.agents if snap else ()
    ladders = [D.ladder(v) for v in agents if not v.error]
    rows = [Row("section:model", SECTIONS[0], True),
            Row("model", "Model"), Row("effort", "Thinking effort"), Row("signin", "Service and sign-in"),
            Row("last", "Who answered last"), Row("drops", "Drops to a backup today"),
            Row("section:ladder", SECTIONS[1], True), Row("rung:main", D.RUNG_LABEL[D.MAIN_RUNG])]
    rows += [Row(f"rung:{key}", label) for key, label, _ in D.LADDER]
    if any(lad.extras or lad.extras_off for lad in ladders):
        rows.append(Row("extras", D.RUNG_LABEL[D.EXTRA_RUNG]))
    rows += [Row("section:voice", SECTIONS[2], True), Row("voice", "English voice"), Row("voice_zh", "Chinese voice"),
             Row("speed", "Speed")]
    if any(v.effect for v in agents):
        rows.append(Row("effect", "Voice effect"))
    if any(has_languages(c) for c in columns_of(snap)):
        rows.append(Row("languages", LANG_ROW))
    rows += [Row(f"vplace:{key}", label) for key, label in VPLACE_ROW]
    rows.append(Row("names", "Answers to (call names)"))
    if any(getattr(o, "mount", None) is not None for o in (snap.others if snap else ())):
        rows.append(Row("mount", "Mounted session"))
    rows.append(Row("section:presence", SECTIONS[3], True))
    rows += [Row(f"presence:{key}", PLACE_ROW.get(key, name)) for key, name in D.PLACES]
    rows.append(Row("section:talk", SECTIONS[4], True))
    rows += [Row(f"talk:{key}", label) for key, label in TALK_ROW]
    return rows


def _health_lines(h: Optional[D.ServiceView]) -> list[str]:
    """A service's sign-in in one sentence, then its allowance in ONE sentence (the word is said once)."""
    if h is None:
        return []
    out = [h.words]
    if h.allowance:
        out.append("Allowance: " + "; ".join(str(ln).rstrip(".") for ln in h.allowance))
    elif h.allowance_note:
        out.append("Allowance: unknown (" + h.allowance_note + ")")
    return out


def _drift(v: D.AgentView, key: str) -> Optional[int]:
    return next((i for i, d in enumerate(v.drifts) if d.key == key), None)


def _drift_lines(v: D.AgentView, i: int) -> list[str]:
    d = v.drifts[i]
    return [d.sentence + (f" ({d.note})" if d.note else ""), "Enter: put it back, or accept it."]


def _short_trouble(trouble: str) -> str:
    return trouble.removeprefix("allowance ")


def _back_day(trouble: str) -> str:
    """"allowance used up until Fri 20 Nov" -> "20 Nov": the day a used-up rung comes back, when the log gave one."""
    m = re.search(r"until (?:[A-Za-z]{3} )?(\d{1,2} [A-Za-z]{3})", trouble or "")
    return m.group(1) if m else ""


def rung_words(model: Optional[str], trouble: str = "") -> str:
    """A ladder cell's few words. In trouble the cell is red and says the day it comes back ("Opus, back 20 Nov");
    the full name and the reason are in the strip."""
    if not trouble:
        return D.short_model(model)
    day = _back_day(trouble)
    if day:
        return f"{D.model_family(model)}, back {day}"
    return f"{D.model_family(model)}, {_short_trouble(trouble)}"


def tiny_backup(b: D.Backup) -> str:
    """"Sonnet 5.5 (Claude)": a backup that is not on the ladder, short enough for a cell."""
    return f"{D.short_model(b.model)} ({D.SERVICE_TINY.get(b.service, D.service_name(b.service, short=True))})"


def last_when(ts: float, now: Optional[float] = None) -> str:
    """The time of the last reply, short: the clock time today, the day before that. The strip has both."""
    then = dt.datetime.fromtimestamp(ts)
    today = dt.datetime.fromtimestamp(now).date() if now is not None else dt.date.today()
    return then.strftime("%H:%M") if then.date() == today else then.strftime("%-d %b")


def agent_cell(row: Row, v: D.AgentView, speed_why: Optional[dict] = None, now: Optional[float] = None) -> Cell:
    """One agent's cell on one row: the light, the few words, the full story and what Enter does there."""
    key = row.key
    if v.error:
        if key == "model":
            return Cell(D.RED, "cannot be read", [v.error, "Nothing else can be shown for this agent until the "
                                                  "settings file reads again."], fixed="the settings file cannot be read")
        return Cell(D.GREY, "unknown", [v.error], fixed="the settings file cannot be read")
    name = v.name
    main = next((h for h in v.health if h.main), None)

    if key == "model":
        if not v.model:
            return Cell(D.GREY, "unknown", ["The model could not be read."], fixed="the model could not be read")
        ms = v.model
        detail = [f"{name}'s model is set to {D.model_name(ms.model)}, on {D.service_name(ms.service)}, "
                  f"{effort_words(ms.effort)}."]
        if v.model_note:
            detail.append(f"Note: {v.model_note}.")
        status = v.model_status
        drift = _drift(v, "model")
        if drift is not None:
            return Cell(D.RED, D.model_name(ms.model), detail + _drift_lines(v, drift), setting=f"drift:{drift}")
        if v.model_behind:
            detail.append(v.model_behind)
        if v.model_pending:
            detail.append("Not live everywhere yet: " + v.model_pending)
        if v.old_chats:
            detail.append(f"{v.old_chats} open chat{'s are' if v.old_chats != 1 else ' is'} still on an older "
                          "setting; a fresh chat would use the current one.")
            status = D.YELLOW if status == D.GREEN else status
        if v.override:
            o = v.override
            detail.append(f"Temporary choice: {D.model_name(o.model)} through {D.service_name(o.service)} "
                          f"({where(o.where)}, {when(o.when)}). The desk leaves it alone.")
            status = D.BLUE if status == D.GREEN else status
        detail += _health_lines(main)
        if v.model_locked:
            return Cell(status, D.model_name(ms.model) + "  locked", detail,
                        fixed=f"{name}'s model is locked: it is shown, never changed from the desk")
        return Cell(status, D.model_name(ms.model), detail + [E.NEEDS["model"]], setting="model")

    if key == "effort":
        if not v.model:
            return Cell(D.GREY, "unknown", fixed="the model could not be read")
        effort = v.model.effort
        words = "extra thinking off" if effort == "none" else effort
        detail = [f"{name} thinks with {effort_words(effort)}."]
        drift = _drift(v, "effort")
        if drift is not None:
            return Cell(D.RED, words, detail + _drift_lines(v, drift), setting=f"drift:{drift}")
        if v.model_locked:
            return Cell(D.GREY if effort == "none" else D.GREEN, words, detail,
                        fixed=f"{name}'s thinking effort is locked with its model")
        return Cell(D.GREEN, words, detail + [E.NEEDS["effort"]], setting="effort")

    if key == "signin":
        if main is None:
            if v.signin_failed and v.model:
                return Cell(D.RED, "could not sign in",
                            [f"{D.service_name(v.model.service)} could not sign in during the last day "
                             f"({D.plain_reason(v.signin_failed)})."], fixed=FACT)
            return Cell(D.GREY, "not checked", [f"{D.service_name(v.model.service) if v.model else 'The service'}: "
                                                "the sign-in has not been checked yet."], fixed=FACT)
        # a green light beside the service's name IS "signed in"; anything else names the trouble instead
        words = {H.SIGNIN_DEAD: "sign-in dead", H.ALLOWANCE_OUT: "used up"}.get(
            main.kind, D.service_name(v.model.service, short=True) if main.state == H.GOOD and v.model
            else "signed in" if main.state == H.GOOD else "sign-in unknown")
        status = main.status if main.state != H.UNKNOWN else D.GREY
        detail = _health_lines(main)
        if main.needs_peter:
            detail.append("Only Peter signing in again fixes this.")
        return Cell(status, words, detail, fixed=FACT)

    if key == "last":
        if not v.last:
            return Cell(D.GREY, "no reply in the log", ["No reply is in the current log."], fixed=FACT)
        r = v.last
        return Cell(r.status, f"{D.short_model(r.answered_model)}, {last_when(r.when, now)}",
                    [f"{r.note} ({where(r.where)}, {when(r.when)}).",
                     f"It was answered by {D.model_name(r.answered_model)} on {D.service_name(r.answered_service)}."],
                    fixed=FACT)

    if key == "drops":
        n = v.drops_today
        status = D.GREEN if n == 0 else D.YELLOW if n < 10 else D.RED
        return Cell(status, str(n), [f"Today {name} fell to a backup {n} time{'s' if n != 1 else ''}: the main model "
                                     "failed and a lower rung answered." if n else
                                     f"Today {name} has not fallen to a backup."], fixed=FACT)

    if key == "rung:main":
        if not v.model:
            return Cell(D.GREY, "unknown", fixed="the model could not be read")
        lad = D.ladder(v)
        failing = main is not None and main.state == H.FAILING
        status = D.RED if failing else v.model_status
        words = D.short_model(v.model.model) + (", " + {H.SIGNIN_DEAD: "sign-in dead",
                                                        H.ALLOWANCE_OUT: "used up"}.get(main.kind, "failing")
                                                if failing else "")
        detail = [f"Rung 1 is {name}'s main model: {D.model_name(v.model.model)} on "
                  f"{D.service_name(v.model.service)}. Every reply starts here."]
        if lad.answered == D.MAIN_RUNG:
            detail.append("The last reply was answered from this rung.")
        elif lad.below:
            detail.append(f"The last reply was answered from {lad.below}.")
        detail += _health_lines(main)
        if v.model_locked:
            return Cell(status, words, detail, fixed=f"{name}'s model is locked", marker=lad.answered == D.MAIN_RUNG)
        return Cell(status, words, detail + ["Enter changes the main model (the same choice as the Model row)."],
                    setting="model", marker=lad.answered == D.MAIN_RUNG)

    if key.startswith("rung:"):
        rung = key.split(":", 1)[1]
        if v.backups_locked:
            return Cell(D.GREY, "none", [f"{name} has no backups by design, and the desk does not change that."],
                        fixed=f"{name} has no backups by design")
        lad = D.ladder(v)
        r = lad.rung(rung)
        if r is None or r.backup is None:
            return Cell(D.GREY, "off", [f"{name} has nothing on {E.RUNG_NAME[rung]}: when the rungs above it fail, "
                                        "this one is skipped.", E.NEEDS["ladder"]],
                        setting=key, marker=bool(r and r.answered))
        b = r.backup
        if not b.on:
            return Cell(D.GREY, f"off ({D.short_model(b.model)})",
                        [f"{backup_words(b, short=False)} is switched off for {name}"
                         + (f" ({b.note})" if b.note else "") + ". The desk remembers it and can switch it on again.",
                         E.NEEDS["ladder"]], setting=key, marker=r.answered)
        drift = _drift(v, f"backup|{b.service}|{b.model}")
        words = rung_words(b.model, r.trouble)
        detail = [f"{name}'s {E.RUNG_NAME[rung]}: {backup_words(b, short=False)}. It answers only when every rung "
                  "above it has failed."]
        if r.trouble:
            detail.append(f"It is on, but {r.trouble}: it cannot answer"
                          + (" until then." if "until" in r.trouble else "."))
        if r.answered:
            detail.append("The last reply was answered from this rung.")
        if lad.order_note:
            detail.append(lad.order_note[0].upper() + lad.order_note[1:] + ". A change on any rung writes the list "
                          "in the standard order.")
        detail += _health_lines(next((h for h in v.health if h.service == b.service), None))
        if drift is not None:
            return Cell(D.RED, words, detail + _drift_lines(v, drift), setting=f"drift:{drift}", marker=r.answered)
        return Cell(r.status, words, detail + [E.NEEDS["ladder"]], setting=key, marker=r.answered)

    if key == "extras":
        if v.backups_locked:
            return Cell(D.GREY, "none", [f"{name} has no backups by design."], fixed=f"{name} has no backups by design")
        lad = D.ladder(v)
        if not lad.extras and not lad.extras_off:
            return Cell(D.GREY, "none", [f"Every backup of {name} is on the ladder."],
                        fixed="there is no extra backup here")
        if not lad.extras:
            b = lad.extras_off[0]
            return Cell(D.GREY, f"off ({tiny_backup(b)})",
                        [f"{backup_words(x, short=False)} is switched off for {name}; it is not on the standard "
                         "ladder. The desk remembers it." for x in lad.extras_off], setting="extras")
        first = lad.extras[0]
        trouble = D.backup_trouble(v.health, first)
        words = tiny_backup(first) + (f" +{len(lad.extras) - 1}" if len(lad.extras) > 1 else "")
        detail = [f"{backup_words(x, short=False)} is on {name}'s list but is not on the standard ladder "
                  "(Claude subscription, Antigravity, ChatGPT, NVIDIA)." for x in lad.extras]
        if lad.answered == D.EXTRA_RUNG:
            detail.append("The last reply was answered by it.")
        if trouble:
            detail.append(f"It is on, but {trouble}.")
        return Cell(D.RED if trouble else D.YELLOW, words, detail + [E.NEEDS["backup"]], setting="extras",
                    marker=lad.answered == D.EXTRA_RUNG)

    if key == "languages":
        return languages_cell(name, v) if v.voice else Cell()

    if key in ("voice", "voice_zh", "speed", "effect"):
        if not v.voice:
            if key == "effect":
                return Cell()
            return Cell(D.GREY, "no voice", [f"{name} has no voice in the Voice Desk list, so there is nothing to "
                                             "change. n adds an agent to that list."],
                        fixed=f"{name} has no voice in the Voice Desk list")
        if key == "voice":
            if v.voice.not_playable:
                return Cell(D.RED, f"{D.voice_label(v.voice.english)}, not playable yet",
                            [f"{name}'s English voice is {D.voice_label(v.voice.english)}, but it is not playable yet: "
                             f"{v.voice.not_playable}. Until then {name}'s English lines from its own script stay "
                             "silent (never another voice).", E.NEEDS["voice"]], setting="voice")
            return Cell(D.GREEN, D.voice_label(v.voice.english),
                        [f"{name} speaks English as {D.voice_label(v.voice.english)}. One voice: if it fails, "
                         f"{name} stays silent, no stand-in voice.", E.NEEDS["voice"]], setting="voice")
        if key == "voice_zh":
            if v.voice.english_only:
                return Cell(D.GREY, "English only", [f"{name} is English only: a Chinese line stays silent.",
                                                     E.NEEDS["voice"]], setting="voice_zh")
            if not v.voice.chinese:
                return Cell(D.GREY, "silent", [f"{name} has no Chinese voice: a Chinese line stays silent.",
                                               E.NEEDS["voice"]], setting="voice_zh")
            return Cell(D.GREEN, D.voice_label(v.voice.chinese),
                        [f"{name} speaks Chinese as {D.voice_label(v.voice.chinese)}.", E.NEEDS["voice"]],
                        setting="voice_zh")
        if key == "speed":
            why = (speed_why or {}).get(v.voice_key, "")
            speed = v.voice.speed
            word = E.speed_word(speed)
            words = "not set" if speed is None else word if word == f"{speed:g}" else f"{word} ({speed:g})"
            if why:
                return Cell(D.GREY, "fixed by this voice" if speed is None else words,
                            [f"{name}'s speaking speed: {words}.", why[0].upper() + why[1:] + "."], fixed=why)
            return Cell(D.GREY if speed is None else D.GREEN, words,
                        [f"{name}'s speaking speed: {words}.", E.NEEDS["voice"]], setting="speed")
        if not v.effect:
            return Cell()
        if not E.effect_applies(None, v.voice.english):
            return Cell(D.GREY, f"{v.effect} (does not apply)",
                        [f"{name}'s voice effect: {v.effect}. It works on Kokoro voices only, and "
                         f"{D.voice_label(v.voice.english)} is a Microsoft voice: it is spoken plain. The effect stays "
                         "stored and comes back with a Kokoro voice.", E.NEEDS["voice"]], setting="effect")
        return Cell(D.GREEN, str(v.effect), [f"{name}'s voice effect: {v.effect}. It works on Kokoro voices only.",
                                             E.NEEDS["voice"]], setting="effect")

    if key.startswith("vplace:"):
        return place_cell(name, key, v.voice_places, has_voice=bool(v.voice))

    if key == "names":
        return Cell()

    if key.startswith("presence:"):
        place = key.split(":", 1)[1]
        p = next((x for x in v.presence if x.key == place), None)
        if p is None:
            return Cell(D.GREY, "unknown", fixed=FACT)
        label = PLACE_ROW["terminal"] if place == "terminal" else D.PLACE_NAME.get(place, place)
        detail = [f"{label}: {state_word(p)}: {p.evidence}."]
        if place in NOT_THE_VOICE_SWITCH:
            detail.insert(0, NOT_THE_VOICE_SWITCH[place].format(name=name))
        if p.why_not and not p.can_switch:
            detail.append(p.why_not[0].upper() + p.why_not[1:] + ".")
        if p.state == D.ON:
            words = "in" + (", leads it" if v.room_lead else "") if place == "team_room" else "on"
            status = p.status
        elif p.state == D.OFF:
            words, status = ("out" if place == "team_room" else "off"), (D.RED if p.status == D.RED else D.GREY)
        else:
            words, status = SHORT_UNKNOWN.get(place, "unknown"), D.GREY
        if p.can_switch:
            return Cell(status, words, detail + [E.NEEDS["team_room"]], setting="team_room")
        wire = wire_at(v.wiring, D.WIRE_OF.get(place, ""))
        if wire is not None and v.voice_key:
            return wired_cell(name, status, words, detail, wire)
        return Cell(status, words, detail, fixed=p.why_not or "there is no clean switch for this place")

    if key.startswith("talk:"):
        rule = key.split(":", 1)[1]
        r = next((x for x in v.talk_rules if x.key == rule), None)
        if r is None:
            return Cell(D.GREY, "unknown", ["The rules file could not be read."], fixed="the rules file could not be read")
        words = SHORT_ANSWER.get(r.answer, r.answer)
        no = r.token in ("no", "nobody") or r.answer.lower().startswith(("no", "nobody"))
        detail = [f"{r.label}: {r.answer}.", "Where this rule stands: " + r.standing + "."]
        if r.locked:
            detail.append(r.locked[0].upper() + r.locked[1:] + ".")
        return Cell(D.GREY if no else D.GREEN, words, detail + [E.NEEDS["rule"]], setting=key)

    return Cell()


WIRE_ALL = "*"                           # the call-names cell of Claude, Gemini and Codex: every place in one menu
MARK_ONLY = "a mark the desk shows: it switches nothing on or off"


def wire_at(wiring: tuple, place: str) -> Optional[D.Wire]:
    return next((w for w in wiring if w.place == place), None)


def phrase_words(w: D.Wire) -> str:
    """"hello Jarvis (stored)", "hello Jarvis (today's, none stored)", "none"."""
    if not w.phrase:
        return "none"
    return w.phrase + (" (stored)" if w.phrase_stored else " (today's, none stored)")


def wired_cell(name: str, status: Optional[str], words: str, detail: list, w: D.Wire) -> Cell:
    """A WHERE IT SHOWS UP cell that also carries the Voice Desk list's own mark for that place: here or not here,
    and the activation phrase. The fact read from the logs stays; the mark and the phrase are what Enter changes."""
    place = P.wire_place_name(w.place)
    if not w.here and not w.here_stored:        # nothing is wired there and the list holds no mark: the fact stands
        return Cell(status, words, detail, setting=f"wire:{w.place}")
    if not w.here:
        first = detail[:1] if detail and detail[0].startswith(NOT_SWITCH_HEAD) else []
        return Cell(D.RED if status == D.RED else D.GREY, "not here",
                    first + [f"{name} is marked not here at {place} in the Voice Desk list ({MARK_ONLY})."]
                    + detail[len(first):], setting=f"wire:{w.place}")
    more = [f"Activation phrase: {phrase_words(w)}."]
    if not w.wired and w.here_stored:
        more.append(f"Marked here, but nothing makes it speak there yet: {w.hint}.")
    return Cell(status, words, detail + more, setting=f"wire:{w.place}")


# a Hermes agent's terminal voice: set by the desk's own switch (terminal_voice.json), or nothing set (the default)
SET_BY = {"desk": "set by the desk", "default": "default"}
SET_BY_SHORT = {"desk": "by desk", "default": "default"}           # for a narrow column
SET_BY_SAID = {"desk": "set by the desk's terminal voice switch",
               "default": "the default, since the desk's terminal voice switch is not set"}
QUIET_WORDS = "Quiet hours:"
QUIET_NOTE = "stored and shown only: nothing keeps it quiet then yet; set by hand in the Voice Desk list"


def place_cell(name: str, key: str, places: tuple, has_voice: bool = True) -> Cell:
    """Spoken replies at one place: the setting, then what the logs say really happened."""
    place = key.split(":", 1)[1]
    p = next((x for x in places if x.key == place), None)
    if p is None:
        if not has_voice:
            return Cell(D.GREY, "no voice", [f"{name} has no voice, so nothing is spoken here."],
                        fixed=f"{name} has no voice in the Voice Desk list")
        return Cell()
    unknown = p.value.strip() in ("?", "—", "-", "")
    # the setting in a word; what the logs say really happened is in the strip. Where the desk cannot read the
    # setting itself, the first thing known about it stands in ("Voice-back"), never a bare "?".
    words = ((p.evidence or "").split(",")[0].strip() or "not known") if unknown else p.value
    short = ""
    said = "not known" if unknown else p.value
    if not unknown and p.set_by in SET_BY:      # Kylie 2026-11-18: "Always" read the same set or not set
        words, short = f"{p.value} ({SET_BY[p.set_by]})", f"{p.value} ({SET_BY_SHORT[p.set_by]})"
        said = f"{p.value}, {SET_BY_SAID[p.set_by]}"
    detail = [f"Spoken replies, {D.place_words(place)}: " + said
              + (f" ({p.evidence})" if p.evidence else "") + "."]
    if p.problem:
        detail.append(p.problem)
    if p.quiet:                                 # the old dashboard's quiet-hours line, per place
        detail.append(f"{QUIET_WORDS} {p.quiet} ({QUIET_NOTE}).")
        short = short or words                  # a narrow column keeps the setting; the strip has the hours
        words = f"{words}, quiet {p.quiet}"
    status = D.RED if p.problem else p.status
    if p.can_switch:
        return Cell(status, words, detail + [E.NEEDS["voice_place"]], setting=key, short=short)
    why = D.plain_why(p.why_not) if p.why_not else "there is no switch for this place"
    return Cell(status, words, detail + [why[0].upper() + why[1:] + "."], fixed=why, short=short)


def other_cell(row: Row, o: D.OtherAssistant) -> Cell:
    """Claude, Gemini and Codex: called by name through the Voice Desk. Voice and call names only; every other
    row is blank for them."""
    key = row.key
    lands = f" Where a change lands: {o.takes_effect}." if o.takes_effect else ""
    if key == "voice":
        if o.english and o.not_playable:
            return Cell(D.RED, f"{D.voice_label(o.english)}, not playable yet",
                        [f"{o.name}'s English voice is {D.voice_label(o.english)}, but it is not playable yet: "
                         f"{o.not_playable}. Until then its own spoken replies stay silent (never another voice)."
                         + lands, E.NEEDS["voice"]], setting="voice")
        return Cell(D.GREEN if o.english else D.GREY, D.voice_label(o.english) if o.english else "silent",
                    [f"{o.name} speaks English as {D.voice_label(o.english)}." + lands, E.NEEDS["voice"]],
                    setting="voice")
    if key == "voice_zh":
        return Cell(D.GREEN if o.chinese else D.GREY, D.voice_label(o.chinese) if o.chinese else "silent",
                    [f"{o.name} speaks Chinese as {D.voice_label(o.chinese)}." + lands, E.NEEDS["voice"]],
                    setting="voice_zh")
    if key == "languages":
        return languages_cell(o.name, o) if o.key else Cell()
    if key.startswith("vplace:"):
        return place_cell(o.name, key, o.voice_places)
    if key == "names":
        more = len(o.call_names) - 1
        words = (o.call_names[0] + (f" +{more} more" if more > 0 else "")) if o.call_names else "none"
        detail = [f"The voice line wakes {o.name} when it hears: " + ", ".join(o.call_names) + "."]
        if o.wiring and o.key:
            kept = [f"{P.wire_place_name(w.place)}: {w.phrase}" for w in o.wiring if w.here and w.phrase_stored]
            away = [P.wire_place_name(w.place) for w in o.wiring if not w.here and w.here_stored]
            if kept:
                detail.append("Stored activation phrases: " + "; ".join(kept) + ".")
            if away:
                detail.append("Marked not here at: " + ", ".join(away) + ".")
            detail.append("The call names themselves are not changed from the desk. Enter marks a place here or "
                          "not here, or stores an activation phrase.")
            return Cell(D.GREEN, words, detail, setting=f"wire:{WIRE_ALL}")
        return Cell(D.GREEN, words, detail,
                    fixed="the call names are kept in the Voice Desk list; the desk shows them and does not change them")
    if key == "mount":
        return mount_cell(o)
    return Cell()


def mount_cell(o: D.OtherAssistant) -> Cell:
    """The one terminal session voice goes to: green its window is open, yellow mounted but its window is not
    open (voice then starts nothing else), grey nothing mounted (as before: the only open window, else a
    separate background session)."""
    m = o.mount
    if m is None:
        return Cell()
    if m.state == "open":
        w = m.window
        detail = [f"Voice {o.name} goes to the mounted session {m.code} ({M.where_words(w)})."]
        if m.bound:
            detail.append(m.why[:1].upper() + m.why[1:] + ".")
        return Cell(D.GREEN, f"{M.short(m.code)} open",
                    detail + ["Its answer is read aloud as the window shows it; the typed words start with [voice].",
                              "No other window and no background session is used."], setting="mount")
    if m.state == "closed":
        return Cell(D.YELLOW, f"{M.short(m.code)} not open",
                    [f"The mounted {o.name} session is not open: "
                     + (f"{m.error}, so the window of {m.code} cannot be found" if m.error
                        else f"{m.code} has no window in herdr") + "."]
                    + ([f"The {o.name} window with no session code is not used for it: {m.why}."] if m.why else [])
                    + [f"A voice turn to {o.name} says so and starts no other session.",
                       "Enter: mount an open session, or unmount."], setting="mount")
    n = sum(1 for s in m.sessions if s.code)
    blind = [s for s in m.sessions if not s.code]
    detail = [f"Nothing is mounted for {o.name}: voice uses its only open window when exactly one is open "
              f"({n} open now{', ' + m.error if m.error else ''}), otherwise a separate background session."]
    if blind:
        names = ", ".join(f"\"{s.label}\" ({s.pane})" if s.label else s.pane for s in blind)
        detail.append(f"herdr shows no session code for {names}, so voice cannot use "
                      + ("that window" if len(blind) == 1 else "those windows")
                      + (" until its session code is mounted (and only while it is the one "
                         f"{o.name} window open)" if len(m.sessions) == 1 else "")
                      + ("" if n == 1 else f": voice {o.name} goes to its separate background session") + ".")
    detail.append("Enter: mount one open session, so voice always goes to that one.")
    return Cell(D.GREY, "none mounted", detail, setting="mount")


ROOM_HEADING = "The Team Room (all agents)"      # the note after it is about the room, not the chosen agent


def cell_of(row: Row, col: Any, speed_why: Optional[dict] = None, room_note: str = "",
            now: Optional[float] = None) -> Cell:
    if row.section:
        return Cell()
    if row.key == "mount" and is_agent(col):        # Hermes agents have their own windows; this row is the three's
        return Cell()
    cell = agent_cell(row, col, speed_why, now) if is_agent(col) else other_cell(row, col)
    if room_note and row.key == "presence:team_room" and not cell.blank:
        cell.detail.append(f"{ROOM_HEADING}: {room_note}")
    return cell


def cell_at(key: str, col: Any, speed_why: Optional[dict] = None) -> Cell:
    """One cell by its row's key ("model", "rung:codex", "vplace:tg_dm" ...), for checks and tests."""
    return cell_of(Row(key, key), col, speed_why)


def column_story(snap: D.Snapshot, col: Any, speed_why: Optional[dict] = None) -> str:
    """Everything the table and its strip say about one column, as plain text: each row's cell, then the strip's
    whole story for it. (Checks and tests read this; the screen draws the same cells.)"""
    out = [col.name.upper()]
    for row in rows_of(snap):
        if row.section:
            out.append(row.label)
            continue
        cell = cell_of(row, col, speed_why, snap.room_note, snap.read_at)
        if cell.blank:
            continue
        out.append(f"{row.label}: {cell_text(cell).plain}")
        out.append("    " + strip_text(row, col, cell, 10_000).plain)
    return "\n".join(out)


def cut_words(words: str, room: int) -> str:
    """Cut to `room` characters with an ellipsis, BETWEEN two words whenever there is a space to cut at: a cell
    never ends in half a word."""
    if len(words) <= room:
        return words
    keep = words[:max(1, room - 1)]
    if " " in keep and not words[len(keep):len(keep) + 1].isspace():
        keep = keep[:keep.rindex(" ")]
    return keep.rstrip(" ,;:(") + "…"


def cell_text(cell: Cell, width: int = 0, bright: bool = False) -> Text:
    """What the table draws: the light, then the words (cut with an ellipsis when the column is narrower)."""
    if cell.blank:
        return Text("")
    t = Text()
    t.append(MARK + " ", style=COLOUR.get(cell.status, DIM))
    words = (ANSWERED + " " if cell.marker else "") + cell.words
    if width and len(words) + 2 > width and cell.short:
        words = (ANSWERED + " " if cell.marker else "") + cell.short
    if width and len(words) + 2 > width:
        words = cut_words(words, width - 2)
    colour = COLOUR[D.RED] if cell.status == D.RED else DIM if cell.status == D.GREY and not bright else ""
    t.append(words, style=colour)
    return t


def _sentence(words: str) -> str:
    words = words.strip()
    return words if not words or words[-1] in ".!?:" else words + "."


STRIP_LINES = 3                          # the strip never runs past three lines: the most important facts first


def strip_text(row: Row, col: Any, cell: Cell, width: int = 120, armed: bool = False, nudge: bool = False) -> Text:
    """The strip under the table: the story of the chosen cell, most important first, each fact said ONCE, in at
    most three lines; then what Enter does there. nudge=True (Enter was pressed on a cell with no menu) lights
    that last sentence up; it is not said a second time."""
    t = Text()
    if row.section:
        t.append(row.label, style=f"bold {COLOUR[D.BLUE]}")
        return t
    if col is None:
        t.append(row.label + ", every agent: ", style=f"bold {COLOUR[D.BLUE]}")
        t.append("Enter opens the same choices and applies one of them to every agent it applies to, after one "
                 "confirmation that lists each agent's before and after.")
        return t
    head = f"{col.name}, {row.label.strip()}: "
    if cell.blank:
        t.append(head, style=f"bold {COLOUR[D.BLUE]}")
        t.append("Nothing to change here: this row does not apply to " + col.name + "." if nudge else
                 "this row does not apply here.", style=COLOUR[D.YELLOW] if nudge else DIM)
        return t
    facts: list[str] = []
    for x in cell.detail:
        fact = _sentence(str(x)) if x else ""
        if fact and fact not in facts:
            facts.append(fact)
    tail = "Enter opens the choices." if cell.setting else \
        _sentence("Nothing to change here: " + cell.fixed) if cell.fixed else ""
    width = max(30, width)

    def wrapped(some: list[str]) -> list[str]:
        return textwrap.wrap(head + " ".join(some + ([tail] if tail else [])), width) or [head]

    lines = wrapped(facts)
    while len(lines) > STRIP_LINES and len(facts) > 1:      # the least important fact is the last one: it goes first
        facts.pop()
        lines = wrapped(facts)
    if len(lines) > STRIP_LINES:                            # one fact alone is too long: it is cut between two words
        lines = lines[:STRIP_LINES]
        lines[-1] = cut_words(lines[-1] + " …", width)
    at = 0
    tail_from = len(head + " ".join(facts)) + (1 if facts else 0) if tail else None
    for i, ln in enumerate(lines):
        if i:
            t.append("\n")
        for j, ch in enumerate(ln):
            pos = at + j
            style = f"bold {COLOUR[D.BLUE]}" if pos < len(head) else \
                (COLOUR[D.YELLOW] if nudge and not cell.setting else DIM) if tail_from is not None and pos >= tail_from else ""
            t.append(ch, style=style)
        at += len(ln) + 1                                   # the space the wrap swallowed
    return t


def listener_item(view: "P.ListenerView") -> Text:
    """The voice line in a few words, for the status line: what the listener is doing and who is on the line."""
    colour = COLOUR[D.RED] if not view.running or view.state == "error" else \
        COLOUR[D.YELLOW] if view.state == "muted" else COLOUR[D.GREEN]
    t = Text()
    t.append("● ", style=colour)
    t.append("voice line: " + view.words + (f" ({view.on_line} on the line)" if view.on_line and view.running else ""),
             style=colour if not view.running else DIM)
    return t


def systems_text(snap: Optional[D.Snapshot], width: int = 10_000, listener: Optional["P.ListenerView"] = None) -> Text:
    """One line: the voice line, the watcher's mode, then the Voice Desk's own health strip (voice engine,
    gateway, queue ...). A light that is failing is not repeated here: it is already a red line above."""
    if snap is None:
        return Text()
    items: list[Text] = []
    if listener is not None:
        items.append(listener_item(listener))
    if snap.watch_mode:
        t = Text()
        t.append("watcher: ", style=DIM)
        t.append(snap.watch_mode, style=COLOUR[D.YELLOW] if snap.watch_paused else DIM)
        items.append(t)
    vc = snap.voice_check
    if vc is not None and vc.ok:                        # a difference or a failed check is a line in the strip above
        t = Text()
        t.append("● ", style=COLOUR[D.GREEN])
        t.append(f"voice settings match agents.toml ({vc.checked} in {vc.files} files)", style=DIM)
        items.append(t)
    for ok, label, _detail in snap.lights:
        if ok is False:                                 # already a red line in the warning strip: said once
            continue
        if label.lower().startswith("watcher"):         # the Voice Desk's own watcher (voice switches), not the desk's
            label = "Voice Desk " + label.replace("(W resumes)", "").strip()
            label = label.replace("WATCHER PAUSED", "watcher paused")
        colour = DIM if ok is None else COLOUR[D.GREEN] if ok else COLOUR[D.RED]
        t = Text()
        t.append("● ", style=colour)
        t.append(label, style=colour if ok is False else DIM)
        items.append(t)
    return flow(items, width)


def flow(items: list[Text], width: int, gap: str = "   ") -> Text:
    """Items side by side, broken into lines BETWEEN items only: no item is ever split across two lines
    (unless one item alone is wider than the window)."""
    rows: list[Text] = []
    row = Text()
    for item in items:
        if row.plain and len(row.plain) + len(gap) + len(item.plain) > width:
            rows.append(row)
            row = Text()
        if row.plain:
            row.append(gap)
        row.append_text(item)
    if row.plain:
        rows.append(row)
    return join(rows)


# ---- the pop-ups -------------------------------------------------------------------------------------------
@dataclass
class Opt:
    token: str
    label: str
    now: bool = False               # today's choice
    disabled: bool = False
    note: str = ""
    goes_on: bool = False           # Enter goes straight on to a next step (a line to type); nothing is saved here


@dataclass
class Spec:
    """Everything one cell's menu needs: the choices, what each would change, and the jobs that change it."""
    title: str
    options: list[Opt] = field(default_factory=list)
    describe: Callable[[str], list[str]] = lambda token: []
    jobs: Callable[[str], list] = lambda token: []
    note: str = ""
    locked: str = ""                # why nothing can be chosen here
    needs: str = ""                 # what it takes for a change to be live everywhere
    testable: str = ""              # "en" / "zh": t makes the highlighted voice to a file and checks it
    agent: Any = None               # the Voice Desk agent behind a voice menu (for the test)

    @property
    def current(self) -> Optional[Opt]:
        return next((o for o in self.options if o.now), None)


_POPUP_CSS = """
{name} {{ align: center middle; background: #0d1117 60%; }}
#box {{ width: 104; max-width: 96%; height: auto; max-height: 92%; border: round #58a6ff; background: #161b22;
       padding: 1 2; }}
#box-title {{ text-style: bold; }}
#box-note {{ color: #8b949e; height: auto; }}
#box-list {{ height: auto; max-height: 22; background: #161b22; border: none; padding: 0; }}
#box-list > .option-list--option-disabled {{ color: #8b949e; }}
#box-status {{ height: auto; color: #d29922; }}
#box-confirm {{ height: auto; }}
#box-keys {{ color: #8b949e; height: auto; }}
"""
MENU_WIDTH = 64
END_WORDS = {1: "that is the last choice: nothing is below it", -1: "that is the first choice: nothing is above it"}


class EndsList(OptionList):
    """A list of choices whose arrow keys STOP at the first and the last choice (a plain list jumps round to the
    other end without a word). It says so through an Edge message."""

    class Edge(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    def step(self, direction: int) -> None:
        n = self.option_count
        at = self.highlighted if self.highlighted is not None else (-1 if direction > 0 else n)
        new = at + direction
        while 0 <= new < n and self.get_option_at_index(new).disabled:
            new += direction
        if 0 <= new < n:
            self.highlighted = new
        else:
            self.post_message(self.Edge(direction))

    def action_cursor_down(self) -> None:
        self.step(1)

    def action_cursor_up(self) -> None:
        self.step(-1)


class MenuBox(Vertical):
    """The floating menu's box. It says when its size changes, so the menu can place it again: a box that grew
    under its cell (the yes / no line came) is pulled back inside the screen instead of running off its bottom."""

    class Resized(Message):
        pass

    def on_resize(self, event: events.Resize) -> None:
        self.post_message(self.Resized())


class CellMenu(ModalScreen[Optional[str]]):
    """The floating menu under a cell: the choices for that one setting, today's marked "now". Enter on a choice
    shows one line that says exactly what will change and whether a restart is needed; Enter again saves, Esc
    goes back. Returns the chosen token, or None."""
    CSS = """
    CellMenu { align: left top; background: #0d1117 25%; }
    #menu { width: 64; max-width: 100%; height: auto; max-height: 100%; border: round #1f6feb; background: #1c2330;
            padding: 0 1; }
    #menu-title { text-style: bold; height: auto; }
    #menu-note { color: #8b949e; height: auto; }
    #menu-list { height: auto; max-height: 14; background: #1c2330; border: none; padding: 0; }
    #menu-list > .option-list--option-disabled { color: #8b949e; }
    #menu-list > .option-list--option-highlighted { background: #1f6feb; }
    #menu-status { height: auto; color: #d29922; }
    #menu-confirm-box { height: auto; max-height: 20; display: none; scrollbar-size-vertical: 1; }
    #menu-confirm { height: auto; }
    #menu-keys { color: #8b949e; height: auto; }
    """
    BINDINGS = [Binding("escape", "cancel", "cancel"), Binding("enter", "enter", "choose", show=False),
                Binding("t", "test", "test", show=False),
                Binding("down", "scroll_confirm(1)", show=False), Binding("up", "scroll_confirm(-1)", show=False)]

    def __init__(self, spec: Spec, anchor: Optional[tuple[int, int]] = None, dry: bool = True,
                 tester: Optional[Callable[[str, str], tuple[bool, str]]] = None) -> None:
        super().__init__()
        self.spec, self.anchor, self.dry, self.tester = spec, anchor, dry, tester
        self.confirming: Optional[str] = None           # the token waiting for its second Enter
        self.overflow = False                           # the yes / no answer is longer than the box: it scrolls

    def row_text(self, o: Opt) -> Text:
        t = Text()
        t.append("(o) " if o.now else "( ) ", style=COLOUR[D.GREEN] if o.now else DIM)
        t.append(o.label, style=DIM if o.disabled else "")
        if o.now:
            t.append("   now", style=COLOUR[D.BLUE])
        if o.note:
            t.append(f"   ({o.note})", style=DIM)
        return t

    def compose(self) -> ComposeResult:
        with MenuBox(id="menu"):
            yield Static(self.spec.title, id="menu-title")
            yield Static(self.spec.locked or self.spec.note, id="menu-note")
            yield EndsList(*([] if self.spec.locked else
                             [Option(self.row_text(o), disabled=o.disabled) for o in self.spec.options]),
                           id="menu-list")
            yield Static("", id="menu-status")
            with VerticalScroll(id="menu-confirm-box", can_focus=False):
                yield Static("", id="menu-confirm")
            yield Static("", id="menu-keys")

    def on_mount(self) -> None:
        lst = self.query_one("#menu-list", OptionList)
        note = self.query_one("#menu-note", Static)
        note.display = bool(self.spec.locked or self.spec.note)
        lst.display = not self.spec.locked
        if self.spec.locked:
            lst.disabled = True
            self.set_focus(None)
        else:
            lst.focus()
            opts = self.spec.options
            lst.highlighted = next((i for i, o in enumerate(opts) if o.now and not o.disabled),
                                   next((i for i, o in enumerate(opts) if not o.disabled), None))
        self.show_keys()
        self.call_after_refresh(self.place)

    def place(self) -> None:
        """Put the menu right under its cell; when it would run off the screen, pull it back in (or above the cell)."""
        try:
            box = self.query_one("#menu", Vertical)
        except Exception:                               # noqa: BLE001  the menu is closing
            return
        width, height = box.outer_size.width or MENU_WIDTH, box.outer_size.height or 8
        sw, sh = self.app.size.width, self.app.size.height
        if self.anchor is None:
            x, y = max(0, (sw - width) // 2), max(0, (sh - height) // 2)
        else:
            x, y = self.anchor
            x = max(0, min(x, sw - width))
            if y + height > sh:
                y = max(0, y - height - 1) if y - height - 1 >= 0 else max(0, sh - height)
        box.styles.offset = (x, y)

    def on_menu_box_resized(self, event: "MenuBox.Resized") -> None:
        """The box grew or shrank (the yes / no line came or went): place it again, inside the screen."""
        event.stop()
        self.place()

    def show_keys(self) -> None:
        if self.spec.locked:
            words = "Esc close"
        elif self.confirming is not None:
            words = "Enter = yes, do exactly that" + ("   up / down: read the rest" if self.overflow else "") + \
                "\nEsc back to choices; Esc again closes the menu"
        else:
            words = "up / down move   Enter choose   Esc cancel"
            if self.tester and self.spec.testable:
                words += "   t test this voice (made to a file, never played)"
        self.query_one("#menu-keys", Static).update(words)

    def highlighted(self) -> Optional[Opt]:
        i = self.query_one("#menu-list", OptionList).highlighted
        return self.spec.options[i] if i is not None and 0 <= i < len(self.spec.options) else None

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.action_enter()

    def on_ends_list_edge(self, event: EndsList.Edge) -> None:
        event.stop()
        self.query_one("#menu-status", Static).update(END_WORDS[event.direction] + ".")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self.confirming is None:
            self.query_one("#menu-status", Static).update("")

    def action_enter(self) -> None:
        if self.spec.locked:
            self.dismiss(None)
            return
        if self.confirming is not None:
            self.dismiss(self.confirming)
            return
        o = self.highlighted()
        if o is None or o.disabled:
            return
        if o.now:
            self.query_one("#menu-status", Static).update("That is what it is now, so there is nothing to change.")
            return
        if o.goes_on:
            self.dismiss(o.token)
            return
        lines = self.spec.describe(o.token)
        if not lines:
            self.query_one("#menu-status", Static).update("Nothing would change.")
            return
        self.confirming = o.token
        head = ("DRY RUN: the desk will show exactly what would change and write nothing."
                if self.dry else "This will be WRITTEN now (backup first, read back, u undoes it).")
        wrap = max(24, (self.query_one("#menu", Vertical).size.width or MENU_WIDTH) - 5)     # one for a scroll bar
        body = [Text(part, style=COLOUR[D.YELLOW] if self.dry else COLOUR[D.RED]) for part in textwrap.wrap(head, wrap)]
        for s in lines:
            parts = textwrap.wrap(s, wrap) or [""]
            body += [Text(("  " if i == 0 else "    ") + part) for i, part in enumerate(parts)]
        if self.spec.needs:
            body += [Text("  " + part, style=DIM) for part in textwrap.wrap(self.spec.needs, wrap)]
        self.query_one("#menu-status", Static).update("")
        self.query_one("#menu-confirm", Static).update(join(body))
        lst = self.query_one("#menu-list", OptionList)
        lst.disabled = True
        # the yes / no line must stay on the screen (Kylie 2026-11-18, a 30-row pane): while it asks, the choices
        # and the note step aside (the lines above say exactly what was chosen), and a long answer scrolls
        # inside the box instead of pushing the keys off the bottom edge
        lst.display = False
        self.query_one("#menu-note", Static).display = False
        self.query_one("#menu-status", Static).display = False
        box = self.query_one("#menu-confirm-box", VerticalScroll)
        title_lines = len(textwrap.wrap(self.spec.title, wrap)) or 1
        room = max(3, self.app.size.height - 2 - title_lines - 2)       # the border, the title, the two key lines
        box.styles.max_height = room
        box.display = True
        box.scroll_home(animate=False)
        self.overflow = len(body) > room
        self.set_focus(None)
        self.show_keys()
        self.call_after_refresh(self.place)

    def action_scroll_confirm(self, by: int) -> None:
        """Up / down while the yes / no line asks: read the rest of a long answer (the choices are put away)."""
        if self.confirming is None:
            return
        box = self.query_one("#menu-confirm-box", VerticalScroll)
        box.scroll_to(y=max(0, min(box.max_scroll_y, box.scroll_y + by)), animate=False, immediate=True)

    def action_cancel(self) -> None:
        if self.confirming is not None:
            self.confirming = None
            self.overflow = False
            self.query_one("#menu-confirm", Static).update("")
            self.query_one("#menu-confirm-box", VerticalScroll).display = False
            self.query_one("#menu-note", Static).display = bool(self.spec.locked or self.spec.note)
            self.query_one("#menu-status", Static).display = True
            lst = self.query_one("#menu-list", OptionList)
            lst.display = True
            lst.disabled = False
            lst.focus()
            self.show_keys()
            self.call_after_refresh(self.place)
        else:
            self.dismiss(None)

    def action_test(self) -> None:
        o = self.highlighted()
        if self.spec.locked or self.confirming is not None or not self.tester or o is None or not self.spec.testable:
            return
        if not o.token:
            self.query_one("#menu-status", Static).update("Silent has nothing to test.")
            return
        if o.goes_on:                                   # "other, type it..." / "other languages...": not a voice
            self.query_one("#menu-status", Static).update("That line opens a next step: there is no voice to test.")
            return
        self.query_one("#menu-status", Static).update(f"Making {o.label} to a file (nothing is played) ...")
        self.run_voice_test(self.spec.testable, o.token, o.label)

    @work(thread=True, exclusive=True, group="acd-voice-test")
    def run_voice_test(self, lang: str, voice: str, label: str) -> None:
        try:
            ok, detail = self.tester(lang, voice)
        except Exception as e:                           # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        words = f"{'Test passed' if ok else 'Test FAILED'} for {label}: {detail}"
        self.app.call_from_thread(self.query_one("#menu-status", Static).update, words)


class NoticePopup(ModalScreen[bool]):
    """A message, or a yes / no question. Y (or Enter on a plain message) answers yes; Esc closes."""
    CSS = _POPUP_CSS.format(name="NoticePopup") + "#box-body { height: auto; max-height: 30; }"
    BINDINGS = [Binding("escape", "no", "close"), Binding("y", "yes", "yes"), Binding("enter", "enter", "ok")]

    def __init__(self, title: str, body: Text, ask: str = "") -> None:
        super().__init__()
        self.title_words, self.body, self.ask = title, body, ask

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            with VerticalScroll(id="box-body"):
                yield Static(self.body, id="box-text")
            yield Static(f"Y = {self.ask}     Esc = no, close" if self.ask else "Enter or Esc closes", id="box-keys")

    def action_yes(self) -> None:
        if self.ask:
            self.dismiss(True)

    def action_enter(self) -> None:
        if not self.ask:
            self.dismiss(False)

    def action_no(self) -> None:
        self.dismiss(False)


RESULT_KEYS = "Esc closes     any other key closes this and goes on to the table (Enter opens the chosen cell)"


class ResultPopup(ModalScreen[Optional[tuple]]):
    """What a change did ("Saved", "Dry run", "Not changed"). It only tells, it asks nothing, so it never eats a key:
    Esc closes it, and any other key closes it AND goes on to the table, so one Enter opens the chosen cell's
    choices (Kylie 2026-11-18: after a Saved box her first Enter only closed the box). Returns (key, character) of
    that key, or None for Esc."""
    CSS = _POPUP_CSS.format(name="ResultPopup") + "#box-body { height: auto; max-height: 30; }"

    def __init__(self, title: str, body: Text) -> None:
        super().__init__()
        self.title_words, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            with VerticalScroll(id="box-body", can_focus=False):
                yield Static(self.body, id="box-text")
            yield Static(RESULT_KEYS, id="box-keys")

    def on_mount(self) -> None:
        self.set_focus(None)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if self.is_current:
            self.dismiss(None if event.key == "escape" else (event.key, event.character))


RUNG_CODE = {"ag_claude": "2a", "ag_gemini": "2b", "codex": "3", "nvidia": "4"}
LADDER_ORDER = "1 the main model   2a Antigravity: Claude   2b Antigravity: Gemini   3 ChatGPT   4 NVIDIA Nemotron"
EXTRAS_WORDS = {"keep": "keep extras after rung 4", "drop": "drop extras"}


def ladder_words(pairs: list) -> str:
    """A backups list on one line, each entry with its rung: "2a Opus 5.5 · 2b Gemini Flash · 4 Nemotron 3.5"."""
    out = []
    for service, model in pairs:
        rung = D.rung_of(service, model)
        out.append(f"{RUNG_CODE[rung]} {D.short_model(model)}" if rung in RUNG_CODE else
                   f"extra {D.short_model(model)} ({D.SERVICE_TINY.get(service, D.service_name(service, short=True))})")
    return " · ".join(out) if out else "no backups"


def ladder_body(plans: list, mode: str, left_out: list[str], dry: Text, width: int = 108) -> Text:
    """The standard-ladder box: the order, the keep / drop choice with today's pick marked, then every agent's
    before and after on one line each, and who is left out and why."""
    body = [Text("The standard ladder, the same order for every agent:", style="bold"), Text("  " + LADDER_ORDER)]
    pick = Text("  Backups that are not on the ladder:  ")
    for key, token in (("k", "keep"), ("x", "drop")):
        on = mode == token
        pick.append(f"[{key}] {EXTRAS_WORDS[token]}" + ("  (selected)" if on else ""),
                    style=f"bold {COLOUR[D.BLUE]}" if on else DIM)
        pick.append("     ")
    body += [pick, Text("")]
    for plan in plans:
        body.append(Text(plan.name, style="bold"))
        if plan.error:
            body.append(Text(f"  could not be read: {plan.error}", style=COLOUR[D.RED]))
            continue
        if not plan.changes:
            body.append(Text("  already on the standard ladder: nothing changes", style=DIM))
            continue
        body.append(Text("  - before: " + ladder_words(plan.before), style=COLOUR[D.RED]))
        body.append(Text("  + after:  " + ladder_words(plan.after), style=COLOUR[D.GREEN]))
        for part in textwrap.wrap("; ".join(plan.notes), max(40, width - 4)):
            body.append(Text("    " + part, style=DIM))
    body.append(Text(""))
    for name in left_out:
        body.append(Text(f"{name} is left out: no backups by design, and the desk does not change that.", style=DIM))
    body += [Text("u undoes the newest of these changes, one agent at a time.", style=DIM), dry]
    return join(body)


class LadderPopup(NoticePopup):
    """The standard ladder for every agent: each before and after, read-only, with one choice inside the box:
    k keeps the backups that are not on the ladder (after rung 4), x drops them. The before and after follow the
    choice. Y sets it (returns "keep" or "drop"); Esc closes (returns None). When the list is longer than the box
    a line says there is more below."""
    CSS = _POPUP_CSS.format(name="LadderPopup") + """
    #box { width: 116; }
    #box-body { height: auto; max-height: 100%; }
    #box-more { height: auto; color: #d29922; display: none; }
    """
    BINDINGS = [Binding("escape", "no", "close"), Binding("y", "yes", "yes"), Binding("enter", "enter", "ok"),
                Binding("k", "mode('keep')", "keep extras"), Binding("x", "mode('drop')", "drop extras"),
                Binding("down", "scroll(1)", "down", show=False), Binding("up", "scroll(-1)", "up", show=False)]

    def __init__(self, plans: dict, left_out: list[str], dry: Text, mode: str = "keep") -> None:
        super().__init__("Put every agent on the standard ladder?", Text(""), ask="yes")
        self.plans, self.left_out, self.dry, self.mode = plans, left_out, dry, mode

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            with VerticalScroll(id="box-body"):
                yield Static("", id="box-text")
            yield Static("", id="box-more")
            yield Static("", id="box-keys")

    def on_mount(self) -> None:
        self.show()
        body = self.query_one("#box-body", VerticalScroll)
        self.watch(body, "scroll_y", lambda *_: self.more_hint(), init=False)      # however it was scrolled

    def on_resize(self) -> None:
        self.call_after_refresh(self.more_hint)

    def todo(self) -> int:
        return sum(1 for p in self.plans[self.mode] if p.changes)

    def show(self) -> None:
        self.query_one("#box-text", Static).update(ladder_body(self.plans[self.mode], self.mode, self.left_out, self.dry))
        n = self.todo()
        yes = f"y = yes, set it for {n} agent{'s' if n != 1 else ''}" if n else "nothing to change with this choice"
        self.query_one("#box-keys", Static).update(
            f"{yes}     k keep extras   x drop extras     up / down scroll     Esc = no, close")
        self.call_after_refresh(self.more_hint)

    def more_hint(self) -> None:
        try:
            body, more = self.query_one("#box-body", VerticalScroll), self.query_one("#box-more", Static)
        except Exception:                               # noqa: BLE001  the box is closing
            return
        below = body.max_scroll_y - body.scroll_y
        more.display = below > 0
        if below > 0:
            more.update(f"more below: {int(below)} more line{'s' if below != 1 else ''} (the down arrow scrolls)")

    def action_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.show()

    def action_scroll(self, by: int) -> None:
        body = self.query_one("#box-body", VerticalScroll)
        body.scroll_to(y=max(0, min(body.max_scroll_y, body.scroll_y + by)), animate=False, immediate=True)
        self.more_hint()

    def action_yes(self) -> None:
        if self.todo():
            self.dismiss(self.mode)

    def action_enter(self) -> None:
        pass

    def action_no(self) -> None:
        self.dismiss(None)


class PickPopup(ModalScreen[Optional[str]]):
    """A short list to pick one line from. Enter picks, Esc closes. Returns the line's token, or None."""
    CSS = _POPUP_CSS.format(name="PickPopup")
    BINDINGS = [Binding("escape", "cancel", "close")]

    def __init__(self, title: str, rows: list[tuple[str, str]], note: str = "") -> None:
        super().__init__()
        self.title_words, self.rows, self.note = title, rows, note

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            yield Static(self.note, id="box-note")
            yield EndsList(*[Option(label, disabled=not token) for token, label in self.rows], id="box-list")
            yield Static("Up / Down move   Enter open   Esc close", id="box-keys")

    def on_mount(self) -> None:
        lst = self.query_one("#box-list", OptionList)
        lst.focus()
        lst.highlighted = next((i for i, (token, _) in enumerate(self.rows) if token), None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        token = self.rows[event.option_index][0]
        if token:
            self.dismiss(token)

    def action_cancel(self) -> None:
        self.dismiss(None)


SILENT = "::silent"             # the pick-list's token for "no voice"


class AskPopup(ModalScreen[Optional[str]]):
    """One line to type (a name). Enter answers, Esc cancels. Returns the text, or None."""
    CSS = _POPUP_CSS.format(name="AskPopup") + "#box-input { margin: 1 0; }"
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, title: str, note: str, value: str = "") -> None:
        super().__init__()
        self.title_words, self.note, self.value = title, note, value

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_words, id="box-title")
            yield Static(self.note, id="box-note")
            yield Input(value=self.value, id="box-input")
            yield Static("Enter goes on     Esc cancels, nothing is changed", id="box-keys")

    def on_mount(self) -> None:
        self.query_one("#box-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


LISTENER_KEYS = ("m mute / unmute   h hush   n new conversation   x hand the microphone back   "
                 "r restart   k stop   Esc close")


class ListenerPopup(ModalScreen[None]):
    """The voice line: what the listener is doing, and its controls (the old dashboard's second screen). It never
    opens the microphone. It reads the listener's own small status file every two seconds while it is open."""
    CSS = _POPUP_CSS.format(name="ListenerPopup") + "#box-body { height: auto; }"
    BINDINGS = [Binding("escape", "close", "close"), Binding("m", "order('mute')", "mute"),
                Binding("h", "order('hush')", "hush"), Binding("n", "order('new_thread')", "new"),
                Binding("x", "order('release')", "hand back"), Binding("r", "order('restart')", "restart"),
                Binding("k", "order('stop')", "stop")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("The voice line (the listener)", id="box-title")
            yield Static("", id="box-note")
            yield Static("", id="box-body")
            yield Static(LISTENER_KEYS, id="box-keys")

    def on_mount(self) -> None:
        self.loaded: Optional[bool] = None
        self.show()
        self.timer = self.set_interval(2, self.show)
        self.ask_loaded()

    @work(thread=True, exclusive=True, group="acd-listener-job")
    def ask_loaded(self) -> None:
        svc = self.app.listener_svc
        try:
            got = svc.loaded() if svc is not None else None
        except Exception:                                # noqa: BLE001
            got = None
        self.app.call_from_thread(self.set_loaded, got)

    def set_loaded(self, got: Optional[bool]) -> None:
        self.loaded = got
        self.show()

    def view(self) -> Optional["P.ListenerView"]:
        svc = self.app.listener_svc
        if svc is None:
            return None
        return dataclasses.replace(P.listener_view(svc, ask_loaded=False), loaded=self.loaded)

    def show(self) -> None:
        v = self.view()
        try:
            note, body = self.query_one("#box-note", Static), self.query_one("#box-body", Static)
        except Exception:                                # noqa: BLE001  the pop-up is closing
            return
        if v is None:
            note.update("This desk was not given the listener, so there is nothing to show or control here.")
            return
        armed = self.app.is_armed
        note.update(Text(("ARMED: an order is really sent." if armed else
                          "Dry run: a key shows what would happen, nothing is sent.")
                         + " Stop and restart stop the voice line, so the desk asks twice, and refuses while "
                           "someone is speaking or an agent is mid-reply.",
                         style=COLOUR[D.RED] if armed else COLOUR[D.YELLOW]))
        out = []
        for label, words in P.listener_text_lines(v):
            t = Text()
            t.append(label.ljust(14), style=DIM)
            t.append(words, style=COLOUR[D.RED] if (label == "State" and not v.running)
                     or (label == "Safe to stop" and v.busy) else "")
            out.append(t)
        body.update(join(out))

    def action_order(self, action: str) -> None:
        v = self.view()
        if v is None:
            return
        if action == "mute" and v.muted:
            action = "unmute"
        self.app.listener_order(action)

    def action_close(self) -> None:
        self.dismiss(None)


def results_text(results: list[E.Result]) -> tuple[str, Text]:
    """(title, body) for what a save did, or would do."""
    dry = any(r.dry for r in results)
    failed = [r for r in results if not r.ok]
    title = ("Not changed" if failed and len(failed) == len(results) else
             "Dry run: nothing was written" if dry else "Saved")
    out: list[Text] = []
    for r in results:
        out.append(line(D.RED if not r.ok else D.YELLOW if r.dry else D.GREEN, r.message))
        for ln in r.lines[:16]:
            out.append(Text("      " + ln, style=COLOUR[D.RED] if ln.lstrip().startswith("- ") else
                            COLOUR[D.GREEN] if ln.lstrip().startswith("+ ") else DIM))
        if len(r.lines) > 16:
            out.append(Text(f"      ... and {len(r.lines) - 16} more lines", style=DIM))
        if r.needs and r.ok:
            for part in textwrap.wrap(r.needs, 92):
                out.append(Text("    " + part, style=DIM))
        out.append(Text(""))
    return title, join(out[:-1] if out else out)


# ---- the screen --------------------------------------------------------------------------------------------
_TOP_CSS = """
#title { height: 1; background: #161b22; padding: 0 1; }
#warnings { height: auto; padding: 0 1; }
#legend { height: auto; padding: 0 1; background: #161b22; }
#systems { height: auto; padding: 0 1; }
#keys { height: auto; padding: 0 1; background: #161b22; color: #8b949e; dock: bottom; }
"""
_SHARED_KEYS = [Binding("t", "app.voice_test", "voice test", show=False),
                Binding("l", "app.listener", "listener", show=False),
                Binding("n", "app.add_agent", "add an agent", show=False),
                Binding("d", "app.remove_agent", "remove an agent", show=False),
                Binding("s", "app.standard_ladder", "the standard ladder", show=False),
                Binding("c", "app.reread", "read again", show=False), Binding("q", "app.quit", "close", show=False),
                Binding("u", "app.undo", "undo", show=False), Binding("r", "app.restart_review", "restart", show=False),
                Binding("p", "app.pause_watcher", "pause the watcher", show=False)]

# The letters are shown the way they are typed: plain lower-case keys, no Shift. Shown item by item, so a narrow
# window breaks the line between two keys and never cuts one off.
TABLE_KEYS = ("arrows move cell to cell", "Enter open the choices (on a row's name: for every agent)",
              "s standard ladder", "t voice test", "l listener", "n add", "d remove", "u undo", "r restart review",
              "p pause watcher", "c read again", "q close")
EDGE_WORDS = {"down": "that is the last row: nothing is below it", "up": "that is the first row: nothing is above it",
              "left": "that is the first column: the row names", "right": "that is the last column"}
LABEL_WIDTH = 28
COL_MIN, COL_MAX = 18, 34


def label_width(seen: int, width: int, columns: int) -> int:
    """How wide the pinned row-names column is. When not every column fits beside it, it takes up the few
    characters left over, so that what scrolls is a whole number of columns: at either end of the sideways scroll
    no half column sticks out from under the row names."""
    room, each = seen - (LABEL_WIDTH + 2), width + 2
    if room <= 0 or columns * each <= room:
        return LABEL_WIDTH
    return LABEL_WIDTH + room % each


def keys_text(width: int, note: str = "") -> Text:
    items = [Text(k, style=DIM) for k in TABLE_KEYS]
    if note:
        items.append(Text(note, style=COLOUR[D.YELLOW]))
    return flow(items, width, gap="   ")


def column_width(snap: Optional[D.Snapshot], window: int, speed_why: Optional[dict] = None) -> int:
    """One width for every agent column: as wide as the longest cell needs, but never wider than what lets all
    the agents fit beside the row names. Whatever does not fit (the three assistants, in a narrow window) scrolls
    sideways; a cell that is cut ends in an ellipsis and the strip has the whole story."""
    cols, rows = columns_of(snap), rows_of(snap)
    if not cols:
        return COL_MIN
    need = max((len(cell_text(cell_of(r, c, speed_why, now=snap.read_at)).plain) for r in rows for c in cols),
               default=COL_MIN)
    need = max(need, max(len(c.name) for c in cols), COL_MIN)
    room = max(40, window) - (LABEL_WIDTH + 2) - 2
    fit = room // len(cols) - 2                                 # every column pads one on each side
    if fit < COL_MIN + 4:                                       # not all nine: the agents fit, the assistants scroll
        fit = room // max(1, len(snap.agents) if snap else 1) - 2
    return max(COL_MIN, min(COL_MAX, need, fit))


class DeskTable(DataTable):
    """The desk's one table. Three differences from a plain table: no faint band follows the mouse pointer (with
    the chosen cell also highlighted it looked like a second choice); the arrow keys step over the section
    headings; and they STOP at the first and the last row and column, and say so."""

    class Edge(Message):
        def __init__(self, direction: str) -> None:
            super().__init__()
            self.direction = direction

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.headings: set[int] = set()

    def _set_hover_cursor(self, active: bool) -> None:
        super()._set_hover_cursor(False)

    def on_resize(self, event: events.Resize) -> None:
        """The strip under the table grew a line and the table got shorter (a 30-row pane): the chosen row is
        scrolled back into sight instead of being left just under the bottom edge."""
        if self.row_count:
            self.call_after_refresh(self.keep_cursor_in_sight)

    def keep_cursor_in_sight(self) -> None:
        if self.row_count:
            r, c = self.cursor_coordinate
            self.move_cursor(row=r, column=c, scroll=True)

    def step_rows(self, direction: int) -> None:
        row, col = self.cursor_coordinate
        new = row + direction
        while 0 <= new < self.row_count and new in self.headings:
            new += direction
        if not 0 <= new < self.row_count:
            self.post_message(self.Edge("down" if direction > 0 else "up"))
            return
        self.move_cursor(row=new, column=col)

    def action_cursor_down(self) -> None:
        self.step_rows(1)

    def action_cursor_up(self) -> None:
        self.step_rows(-1)

    def action_cursor_right(self) -> None:
        if self.cursor_coordinate.column >= len(self.columns) - 1:
            self.post_message(self.Edge("right"))
            return
        self.move_cursor(column=self.cursor_coordinate.column + 1, animate=False)

    def action_cursor_left(self) -> None:
        if self.cursor_coordinate.column <= 0:
            self.post_message(self.Edge("left"))
            return
        self.move_cursor(column=self.cursor_coordinate.column - 1, animate=False)

    def snap_target(self) -> Optional[int]:
        """Where the sideways scroll must stand so that the first agent column beside the pinned row names is a
        WHOLE column (half a column sticking out from under the names looked broken), and the chosen column is
        fully in view. None when nothing scrolls sideways."""
        try:
            widths = [col.get_render_width(self) for col in self.ordered_columns]
        except Exception:                               # noqa: BLE001
            return None
        fixed, rest = sum(widths[:self.fixed_columns]), widths[self.fixed_columns:]
        seen = (self.scrollable_content_region.width or self.size.width) - fixed
        if not rest or seen <= 0 or sum(rest) <= seen:
            return 0
        starts = [sum(rest[:i]) for i in range(len(rest))]
        j = max(0, self.cursor_coordinate.column - self.fixed_columns)        # the chosen column among the scrolling ones
        k = max((i for i, x in enumerate(starts) if x <= self.scroll_x), default=0)     # the column now at the left edge
        k = min(k, j)                                                          # the chosen one is never left of the edge
        while k < j and starts[j] + rest[j] - starts[k] > seen:                # ... nor cut off on the right
            k += 1
        return starts[k]

    def snap_columns(self) -> None:
        target = self.snap_target()
        if target is not None and target != self.scroll_x:
            self.scroll_to(x=target, animate=False, force=True, immediate=True)

    def _scroll_cursor_into_view(self, animate: bool = False) -> None:
        """Up and down the plain table's way; sideways by WHOLE columns (see snap_target)."""
        try:
            region = self._get_cell_region(self.cursor_coordinate)
            self.scroll_to_region(region, animate=False, spacing=self._get_fixed_offset(), force=True,
                                  x_axis=False, immediate=True)
        except Exception:                               # noqa: BLE001  an empty table has no cell to show
            return
        self.snap_columns()


CURSOR_BG = "#1f4b82"             # the chosen cell: a clear blue block, the same on a filled cell and a blank one
PAGE_BG = "#0d1117"


class TableScreen(Screen):
    """The whole desk: one table. Rows are settings, columns are agents; the strip under it tells the full story
    of the chosen cell."""
    CSS = _TOP_CSS + """
    #table { height: 1fr; }
    #table > .datatable--cursor { background: %s; text-style: bold; }
    #table > .datatable--header { background: #161b22; color: #e6edf3; text-style: bold; }
    #table > .datatable--fixed { background: #0d1117; color: #8b949e; }
    #table > .datatable--fixed-cursor { background: %s; color: #e6edf3; }
    #more { height: auto; padding: 0 1; color: #d29922; display: none; }
    #strip { height: auto; max-height: 9; padding: 0 1; background: #161b22; border-top: solid #30363d; }
    """ % (CURSOR_BG, CURSOR_BG)
    BINDINGS = [*_SHARED_KEYS]

    def compose(self) -> ComposeResult:
        yield Static("", id="title")
        yield Static("reading every agent's settings and log ...", id="warnings")
        yield Static(legend_text(), id="legend")
        yield Static("", id="systems")
        yield DeskTable(id="table", cursor_type="cell", zebra_stripes=False, fixed_columns=1, header_height=2,
                        cursor_foreground_priority="renderable")      # the chosen cell keeps its green and red
        yield Static("", id="more")
        yield Static("", id="strip")
        yield Static("", id="keys")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Row] = []
        self.cols: list = []
        self.cells: dict = {}
        self.shape: tuple = ()
        self.note = ""

    def on_mount(self) -> None:
        self.query_one("#table", DataTable).focus()
        self.show(self.app.snapshot)

    def on_resize(self) -> None:
        self.show(self.app.snapshot)

    # -- drawing
    def show(self, snap: Optional[D.Snapshot]) -> None:
        app = self.app
        self.query_one("#title", Static).update(app.title_text())
        self.query_one("#legend", Static).update(legend_text(app.text_width()))
        self.query_one("#keys", Static).update(keys_text(app.text_width(), self.note))
        if snap is None:
            return
        self.query_one("#warnings", Static).update(warnings_text(snap))
        self.query_one("#systems", Static).update(systems_text(snap, app.text_width(), app.listener_now))
        table = self.query_one("#table", DeskTable)
        rows, cols = rows_of(snap), columns_of(snap)
        width = column_width(snap, self.size.width or app.size.width, app.speed_why)
        keep_row = self.rows[table.cursor_coordinate.row].key if self.rows and table.row_count else ""
        keep_col = table.cursor_coordinate.column if table.row_count else 1
        keep_scroll = (table.scroll_x, table.scroll_y)
        names = self.names_width(table, width, len(cols))
        shape = (tuple(r.key for r in rows), tuple(col_id(c) for c in cols), width, names)
        self.rows, self.cols = rows, cols
        self.cells = {(r.key, col_id(c)): cell_of(r, c, app.speed_why, snap.room_note, snap.read_at)
                      for r in rows for c in cols}
        fresh = shape != self.shape
        if fresh:
            table.clear(columns=True)
            table.add_column(Text("\nSetting", style=DIM), key="setting", width=names)
            for c in cols:
                head = Text(c.name, style="bold")
                head.append("\n" + (getattr(c, "role", "") or ("called by name" if not is_agent(c) else "")), style=DIM)
                table.add_column(head, key=col_id(c), width=width)
            self.shape = shape
        else:
            table.clear()
        table.headings = {i for i, r in enumerate(rows) if r.section}
        for r in rows:
            if r.section:
                table.add_row(Text(r.label, style=f"bold {COLOUR[D.BLUE]}"), *[Text("") for _ in cols], key=r.key)
            else:
                table.add_row(Text("  " + r.label), *[cell_text(self.cells[(r.key, col_id(c))], width) for c in cols],
                              key=r.key)
        if rows:
            at = next((i for i, r in enumerate(rows) if r.key == keep_row), None)
            if at is None or rows[at].section:
                at = next((i for i, r in enumerate(rows) if not r.section), 0)
            had = bool(keep_row)
            table.move_cursor(row=at, column=min(max(keep_col, 0), len(cols)) if had else 1, scroll=not had)
            if had:
                table.scroll_to(x=keep_scroll[0], y=keep_scroll[1], animate=False)
        self.show_strip()
        self.call_after_refresh(self.snap_then_hint)

    def names_width(self, table: DataTable, width: int, columns: int) -> int:
        seen = table.scrollable_content_region.width or self.size.width or self.app.size.width
        return label_width(seen, width, columns)

    def go(self, row_key: str, column: Any = None) -> bool:
        """Put the cursor on a row (by its key) and a column: a number (0 = the row names), an agent's name, or
        None to stay in the same column. False when there is no such row or column."""
        table = self.query_one("#table", DeskTable)
        r = next((i for i, row in enumerate(self.rows) if row.key == row_key), None)
        if r is None:
            return False
        if column is None:
            c = table.cursor_coordinate.column
        elif isinstance(column, int):
            c = column
        else:
            c = next((i + 1 for i, col in enumerate(self.cols) if col.name == column or col_id(col) == column), -1)
        if not 0 <= c <= len(self.cols):
            return False
        table.move_cursor(row=r, column=c)
        return True

    def at(self) -> tuple[Optional[Row], Any, Optional[Cell]]:
        """(the row, the column's agent or None for the row names, the cell) under the cursor."""
        table = self.query_one("#table", DeskTable)
        if not self.rows or not table.row_count:
            return None, None, None
        r, c = table.cursor_coordinate
        if not 0 <= r < len(self.rows):
            return None, None, None
        row = self.rows[r]
        if c <= 0 or c > len(self.cols):
            return row, None, None
        col = self.cols[c - 1]
        return row, col, self.cells.get((row.key, col_id(col)))

    def show_strip(self, extra: str = "", nudge: bool = False) -> None:
        row, col, cell = self.at()
        strip = self.query_one("#strip", Static)
        if row is None:
            strip.update("")
            return
        t = strip_text(row, col, cell or Cell(), self.app.text_width(), self.app.is_armed, nudge=nudge)
        if extra:
            t.append("\n" + extra, style=COLOUR[D.YELLOW])
        strip.update(t)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        self.show_strip()
        self.call_after_refresh(self.snap_then_hint)

    def snap_then_hint(self) -> None:
        try:
            table = self.query_one("#table", DeskTable)
        except Exception:                               # noqa: BLE001  the screen is closing
            return
        if self.shape and self.names_width(table, self.shape[2], len(self.cols)) != self.shape[3]:
            self.show(self.app.snapshot)                 # the table's real width is known now (a scroll bar came or went)
            return
        table.snap_columns()
        self.call_after_refresh(self.more_hint)

    def on_desk_table_edge(self, event: DeskTable.Edge) -> None:
        self.show_strip(EDGE_WORDS[event.direction] + ".")

    def more_hint(self) -> None:
        """One line under the table that NAMES whatever is off screen: agent columns to the left or right, and
        sections below. Nothing is cut off in silence."""
        try:
            table, more = self.query_one("#table", DeskTable), self.query_one("#more", Static)
        except Exception:                               # noqa: BLE001  the screen is closing
            return
        parts = []
        left, right = hidden_columns(table, self.cols)
        if right:
            parts.append("more columns to the right (right arrow): " + ", ".join(right))
        if left:
            parts.append("more columns to the left (left arrow): " + ", ".join(left))
        below = hidden_sections(table, self.rows)
        if below:
            parts.append("more rows below (down arrow): " + ", ".join(below))
        more.display = bool(parts)
        if parts:
            more.update(flow([Text(p) for p in parts], self.app.text_width()))

    def cell_anchor(self) -> Optional[tuple[int, int]]:
        """Where on the screen the chosen cell's bottom-left corner is: the floating menu opens right there."""
        table = self.query_one("#table", DeskTable)
        try:
            r, c = table.cursor_coordinate
            widths = [col.get_render_width(table) for col in table.ordered_columns]
            x = sum(widths[:c]) - (0 if c == 0 else int(table.scroll_x))
            y = table.header_height + r - int(table.scroll_y)
            return table.region.x + max(0, x), table.region.y + y + 1
        except Exception:                               # noqa: BLE001  the menu then opens in the middle
            return None

    # -- Enter
    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        event.stop()
        row, col, cell = self.at()
        if row is None or row.section:
            return
        if col is None:
            self.app.open_row(row, self.cell_anchor())
        elif cell is None or cell.blank or not cell.setting:
            self.show_strip(nudge=True)                  # the strip's own last sentence lights up: said once
        else:
            self.app.open_cell(row, col, cell, self.cell_anchor())


def hidden_columns(table: DataTable, cols: list) -> tuple[list[str], list[str]]:
    """(names of the columns cut off on the left, on the right) in a window too narrow for all of them."""
    try:
        widths = [col.get_render_width(table) for col in table.ordered_columns]
    except Exception:                                   # noqa: BLE001
        return [], []
    seen = table.scrollable_content_region.width or table.size.width
    if len(widths) != len(cols) + 1 or not seen:
        return [], []
    first, edge = widths[0], seen
    left, right, x = [], [], first
    for c, w in zip(cols, widths[1:]):
        start, end = x - table.scroll_x, x + w - table.scroll_x
        if start < first:
            left.append(c.name)
        elif end > edge:
            right.append(c.name)
        x += w
    return left, right


def hidden_sections(table: DataTable, rows: list) -> list[str]:
    """The sections that start below the table's bottom edge."""
    seen = table.scrollable_content_region.height or table.size.height
    if not seen:
        return []
    bottom = table.scroll_y + seen - table.header_height
    return [r.label.split(" (")[0] for i, r in enumerate(rows) if r.section and i >= bottom]


class Desk(App):
    """The Agent Control Desk. `collector` returns a Snapshot; tests pass their own, plus their own EditPaths."""
    TITLE = "Agent Control Desk"
    CSS = "Screen { background: #0d1117; color: #e6edf3; }"
    BINDINGS = [Binding("q", "quit", "close"), Binding("c", "reread", "read again")]

    def __init__(self, collector: Optional[Callable[[], D.Snapshot]] = None, autostart: bool = True,
                 edit: Optional[E.EditPaths] = None, loader: Optional[E.Loader] = None,
                 tester: Optional[Callable[[A.Agent, str, str], tuple[bool, str]]] = E.real_voice_test,
                 launcher_armed: bool = False, runner: Optional[Callable[[list], tuple]] = None,
                 usage: Optional[H.UsageReader] = None, watching: bool = True, listener: Any = None,
                 synth: Optional[Callable] = None, probe: Optional[Callable] = None) -> None:
        super().__init__()
        # the voice line's own plumbing. A desk built with its own collector (a test, a picture) gets none unless
        # it is handed one, so it can never reach the real listener by accident.
        self.listener_svc = listener if listener is not None else (P.RealListener() if collector is None else None)
        self.listener_now: Optional[P.ListenerView] = None
        self.synth, self.probe = synth, probe                    # tests pass a fake voice maker
        self.runner = runner                                     # tests pass a fake Team Room command
        self.usage = usage                                       # the ONE shared allowance reader (None = not asked)
        self.watching = watching                                 # False = the watcher never acts from this desk
        self.snapshot: Optional[D.Snapshot] = None
        self.autostart = autostart
        self.edit = edit or E.EditPaths.default(launcher_armed=launcher_armed)
        self.loader, self.tester = loader, tester
        self.last_results: list[E.Result] = []
        self.speed_why: dict = {}                                # Voice Desk key -> why its speed cannot be changed
        self.speed_stamp: Optional[int] = None
        if collector is None:
            paths = D.Paths.default()
            reader = D.LogReader(paths.profiles)                 # ONE reader, kept for the life of the desk
            edit_paths = self.edit
            if self.usage is None and os.environ.get("ACD_USAGE", "on") != "off":
                self.usage = H.UsageReader(keep=self.edit.state / "allowance_last_known.json")
            usage_reader = self.usage

            def collector() -> D.Snapshot:
                got = usage_reader.cached() if usage_reader is not None else None
                return D.collect(paths, reader, desk=E.record(edit_paths), usage=got or None,
                                 watch=W.view(edit_paths))
        self.collector = collector

    def on_mount(self) -> None:
        self.push_screen(TableScreen())
        self.look_at_listener()
        if self.autostart:
            self.action_reread()
            self.set_interval(REFRESH_SEC, self.action_reread)
            if self.listener_svc is not None:
                self.set_interval(LISTENER_SEC, self.look_at_listener)

    def look_at_listener(self) -> None:
        """The voice line's state for the status line: ONE small file read, never the microphone, never a command."""
        if self.listener_svc is None:
            return
        try:
            now = P.listener_view(self.listener_svc, ask_loaded=False)
        except Exception:                                # noqa: BLE001  a failed look must never take the desk down
            return
        if now == self.listener_now:
            return
        self.listener_now = now
        if self.snapshot is None:
            return
        for screen in self.screen_stack:
            if isinstance(screen, TableScreen):
                try:
                    screen.query_one("#systems", Static).update(
                        systems_text(self.snapshot, self.text_width(), now))
                except Exception:                        # noqa: BLE001
                    pass

    @property
    def is_armed(self) -> bool:
        return E.armed(self.edit)

    def text_width(self) -> int:
        """How many characters fit on one line of the strips at the top (they pad one column on each side)."""
        return max(20, self.size.width - 2)

    def title_text(self) -> Text:
        t = Text("AGENT CONTROL DESK", style="bold")
        if self.is_armed:
            t.append("     " + ARMED_LABEL, style=COLOUR[D.RED])
        else:
            t.append("     " + DRY_LABEL, style=COLOUR[D.YELLOW])
        if self.snapshot:
            t.append("     read " + dt.datetime.fromtimestamp(self.snapshot.read_at).strftime("%a %-d %b %H:%M:%S"),
                     style=DIM)
        return t

    def action_reread(self) -> None:
        self.read_in_background()

    @work(thread=True, exclusive=True, group="acd-read")
    def read_in_background(self) -> None:
        self.watch_step()
        try:
            snap = self.collector()
        except Exception as e:                       # noqa: BLE001  a failed read must never take the desk down
            self.call_from_thread(self.read_failed, f"{type(e).__name__}: {e}")
            return
        try:
            self.learn_speeds(snap)
        except Exception:                            # noqa: BLE001
            pass
        self.call_from_thread(self.apply_snapshot, snap)

    def watch_step(self) -> None:
        """The watcher's own look, on the same slow timer as the read. It runs only on an ARMED desk (an unarmed
        desk writes nothing at all), and it changes a setting only when automatic put-back is switched on too."""
        if not self.watching or not self.is_armed:
            return
        snap = self.snapshot
        overrides = {v.profile: v.override.model for v in snap.agents if v.override} if snap else {}
        try:
            self.last_watch = W.run_once(self.edit, act=True, loader=self.loader or E.default_loader(),
                                         overrides=overrides)
        except Exception:                            # noqa: BLE001  the watcher must never take the desk down
            self.last_watch = None

    def usage_services(self) -> dict:
        """service -> one agent that uses it (whose sign-in the usage check is asked as)."""
        out: dict = {}
        for v in (self.snapshot.agents if self.snapshot else ()):
            for h in v.health:
                out.setdefault(h.service, v.profile)
        return out

    @work(thread=True, exclusive=True, group="acd-usage")
    def read_usage_in_background(self) -> None:
        """Hermes' own usage check, at most once per service every ten minutes, never on the screen's thread."""
        try:
            ran = self.usage.refresh(self.usage_services()) if self.usage is not None else 0
        except Exception:                            # noqa: BLE001
            ran = 0
        if ran:
            self.call_from_thread(self.action_reread)

    def read_failed(self, why: str) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, TableScreen):
                screen.query_one("#warnings", Static).update(line(D.RED, f"this read failed ({why[:160]}); "
                                                                         "showing the last good one"))

    def apply_snapshot(self, snap: D.Snapshot) -> None:
        self.snapshot = snap
        for screen in self.screen_stack:
            if isinstance(screen, TableScreen):
                screen.show(snap)
        if self.usage is not None and self.autostart and self.usage.due(self.usage_services()):
            self.read_usage_in_background()

    # -- changing a setting ------------------------------------------------------------------------------------
    def voice_agent(self, v) -> tuple[Optional[A.Agent], dict]:
        try:
            cfg = A.load(self.edit.voice.toml)
        except (OSError, ValueError):
            return None, {}
        key = getattr(v, "voice_key", "") or getattr(v, "key", "")
        return next((a for a in A.agents(cfg) if a.key == key), None), cfg

    def learn_speeds(self, snap: Optional[D.Snapshot]) -> None:
        """Which agents' speaking speed cannot be changed (their voice has no speed setting). Asked once per
        version of the Voice Desk list, by running the real change on throw-away copies; never on the live files."""
        if snap is None:
            return
        try:
            stamp = os.stat(self.edit.voice.toml).st_mtime_ns
        except OSError:
            return
        if stamp == self.speed_stamp:
            return
        why: dict = {}
        for v in snap.agents:
            if v.voice and v.voice_key:
                try:
                    why[v.voice_key] = E.speed_fixed(v.voice_key, self.edit)
                except Exception:                        # noqa: BLE001  a failed look must never take the desk down
                    why[v.voice_key] = ""
        self.speed_why, self.speed_stamp = why, stamp

    def spec_for(self, col: Any, setting: str) -> Optional[Spec]:
        """The menu for one cell: its choices, what each would change, and the jobs that change it."""
        if setting.startswith("drift:"):
            return self.spec_drift(col, setting)
        if setting.startswith("vplace:"):
            return self.spec_place(col, setting)
        if setting.startswith("rung:"):
            return self.spec_rung(col, setting.split(":", 1)[1])
        if setting.startswith("talk:"):
            return self.spec_talk(col, setting.split(":", 1)[1])
        if setting.startswith("wire:"):
            return self.spec_wire(col, setting.split(":", 1)[1])
        if setting == "mount":
            return self.spec_mount(col)
        if setting == "languages":
            return self.spec_languages(col)
        build = {"model": self.spec_model, "effort": self.spec_effort, "extras": self.spec_extras,
                 "team_room": self.spec_team_room}.get(setting)
        if build:
            return build(col)
        if setting in ("voice", "voice_zh", "speed", "effect"):
            return self.spec_voice(col, setting)
        return None

    def open_cell(self, row: Row, col: Any, cell: Cell, anchor: Optional[tuple[int, int]] = None) -> None:
        spec = self.spec_for(col, cell.setting)
        if spec is None:
            return
        tester = None
        if spec.testable and spec.agent is not None and self.tester:
            agent = spec.agent
            tester = lambda lang, voice: self.tester(agent, lang, voice)        # noqa: E731
        def chosen(token: Optional[str]) -> None:
            if token is None:
                return
            if cell.setting.startswith("wire:") and token.startswith("phrase|"):
                self.ask_phrase(col, token.split("|", 1)[1])
                return
            if cell.setting in ("voice", "voice_zh") and token == E.OTHER_VOICE:
                self.ask_voice_name(col, cell.setting)
                return
            if token == LANG_MENU:                      # the Chinese voice menu's last line: the other languages
                self.open_cell(row, col, Cell(setting="languages"), anchor)
                return
            if cell.setting == "languages" and token == "extra":
                self.ask_extra(col)
                return
            if cell.setting == "mount" and token == M.TYPE:
                self.ask_mount_code(col)
                return
            self.run_jobs(spec.jobs(token))

        self.push_screen(CellMenu(spec, anchor, dry=not self.is_armed, tester=tester), chosen)

    def spec_wire(self, col: Any, which: str) -> Spec:
        """Here or not here at a place, and the activation phrase there: the Voice Desk list's own marks (the old
        dashboard's X and P keys). which = one place, or WIRE_ALL for every place in one menu."""
        edit = self.edit
        key = getattr(col, "voice_key", "") or getattr(col, "key", "")
        wires = [w for w in getattr(col, "wiring", ()) if which in (WIRE_ALL, w.place)]
        one = which != WIRE_ALL
        title = (f"{col.name}: {P.wire_place_name(which)}, here or not here" if one
                 else f"{col.name}: where it is marked here, and its activation phrases")
        if not key or not wires:
            return Spec(title, locked=f"{col.name} is not in the Voice Desk list, so there is nothing to mark.")
        by = {w.place: w for w in wires}
        options: list[Opt] = []
        for w in wires:
            place = P.wire_place_name(w.place)
            if one:
                options += [Opt(f"here|{w.place}", "here", w.here), Opt(f"nothere|{w.place}", "not here", not w.here)]
            else:
                options.append(Opt(f"nothere|{w.place}", f"{place}: mark it not here") if w.here
                               else Opt(f"here|{w.place}", f"{place}: mark it here"))
            if w.here:
                options.append(Opt(f"phrase|{w.place}", ("" if one else f"{place}: ") + "change the phrase... (now: "
                                   + phrase_words(w) + ")", goes_on=True))

        def describe(tok: str) -> list[str]:
            did, place = tok.split("|", 1)
            w, name = by[place], P.wire_place_name(place)
            if did == "phrase":
                return ["Next you type the phrase. Nothing is saved until you say yes to it."]
            out = [f"{col.name} is marked {'here' if did == 'here' else 'not here'} at {name} in the Voice Desk list.",
                   "Spoken replies there keep their own switch"         # the needs line below says it is only a mark
                   + (f": the row \"{VOICE_ROW['terminal']}\"." if place == "terminal" else ".")]
            if did == "here" and not w.wired:
                out.append(f"Nothing makes it speak there yet: {w.hint}.")
            if did == "nothere" and w.phrase_stored:
                out.append("Its stored activation phrase there is kept.")
            return out

        def jobs(tok: str) -> list:
            did, place = tok.split("|", 1)
            return [] if did == "phrase" else [lambda: P.set_here(edit, key, place, did == "here")]

        note = ("These are marks kept in the Voice Desk list, the same ones the old dashboard had. A phrase is stored "
                "and shown only: it does not rename a Siri shortcut or change the wake words yet.")
        if one and which == "terminal":
            note = (f"Not the voice switch: whether {col.name} speaks in its terminal window is the row "
                    f"\"{VOICE_ROW['terminal']}\". " + note)
        return Spec(title, options, describe, jobs, note=note, needs=P.NEEDS_HERE)

    def ask_phrase(self, col: Any, place: str) -> None:
        """A line to type the activation phrase of one place, then one yes / no line with the old and the new."""
        edit = self.edit
        key = getattr(col, "voice_key", "") or getattr(col, "key", "")
        name = P.wire_place_name(place)
        title = f"{col.name} at {name}: activation phrase"
        try:
            have, stored, default = P.phrase_now(edit, key, place)
        except Exception as exc:                         # noqa: BLE001
            self.push_screen(NoticePopup(title, Text(f"The Voice Desk list could not be read ({type(exc).__name__}). "
                                                     "Nothing was changed.")))
            return
        was = (have + ("" if stored else " (today's, none stored)")) if have else "none"
        note = (P.PHRASE_HINT.get(place, "") + " "
                + (f"Leave it empty to go back to today's: {default}. " if default else "Leave it empty for none. ")
                + P.NEEDS_PHRASE)

        def answered(text: Optional[str]) -> None:
            if text is None:
                return
            text = " ".join(text.split())
            if len(text) > P.PHRASE_MAX:
                self.push_screen(NoticePopup(title, Text(f"That is {len(text)} characters; a phrase of at most "
                                                         f"{P.PHRASE_MAX}, please. Nothing was changed.")))
                return
            if (text and stored and text == have) or (not text and not stored):
                self.push_screen(NoticePopup(title, Text("That is what it is now, so there is nothing to change.")))
                return
            new = text or (f"today's ({default})" if default else "none")
            body = [Text(f"  now    {was}"), Text(f"  new    {new}"), Text(""), Text(P.NEEDS_PHRASE, style=DIM),
                    Text(""), self.dry_line()]
            self.push_screen(NoticePopup(f"Store this phrase for {col.name} at {name}?", join(body),
                                         ask="yes, store it"),
                             lambda yes: self.run_jobs([lambda: P.set_phrase(edit, key, place, text)]) if yes else None)

        self.push_screen(AskPopup(title, note, have if stored else ""), answered)

    def ask_voice_name(self, col: Any, setting: str) -> None:
        """"other, type it…": a line to type any voice name in the bank, the same check as every pick (the bank, and
        whether this agent's own script can play it), then one yes / no line with the old and the new."""
        edit = self.edit
        agent, _cfg = self.voice_agent(col)
        lang = "English" if setting == "voice" else "Chinese"
        title = f"{col.name}: {lang} voice, typed"
        if agent is None:
            self.push_screen(NoticePopup(title, Text(f"{col.name} has no voice in the Voice Desk list. Nothing was "
                                                     "changed.")))
            return
        before = agent.voice if setting == "voice" else agent.voice_zh
        note = ("A Kokoro name (af_heart, bm_george ...) or a Microsoft name (en-US-AvaNeural ...)." if setting == "voice"
                else "A Taiwan Mandarin Microsoft name (zh-TW-HsiaoYuNeural ...).")

        def answered(text: Optional[str]) -> None:
            if text is None:
                return
            text = text.strip()
            if not text:
                return
            name, why = E.check_typed_voice(agent, setting, text)
            if why:
                self.push_screen(NoticePopup(title, Text(f"{why} Nothing was changed.")))
                return
            if name == before:
                self.push_screen(NoticePopup(title, Text("That is what it is now, so there is nothing to change.")))
                return
            body = [Text(f"  now    {D.voice_label(before) if before else 'silent'}"),
                    Text(f"  new    {D.voice_label(name)}")]
            if setting == "voice" and agent.effect and not E.effect_applies(agent, name):
                body.append(Text(f"  Her sound effect ({agent.effect}) does not apply to this voice: it is spoken plain.",
                                 style=DIM))
            body += [Text(""), Text(E.NEEDS["voice"], style=DIM), Text(""), self.dry_line()]
            self.push_screen(NoticePopup(f"{col.name}: change the {lang} voice?", join(body), ask="yes, change it"),
                             lambda yes: self.run_jobs([lambda: E.change_voice(agent.key, setting, name, edit)])
                             if yes else None)

        self.push_screen(AskPopup(title, note, ""), answered)

    def spec_languages(self, col: Any) -> Spec:
        """Other languages (the old dashboard's L and O keys): which language it speaks in, and the extra language
        voices. Stored and shown only: nothing speaks them yet, and the menu says so."""
        edit = self.edit
        key = getattr(col, "voice_key", "") or getattr(col, "key", "")
        title = f"{col.name}: other languages"
        try:
            lang = P.languages_now(edit, key) if key else None
        except Exception:                               # noqa: BLE001
            lang = None
        if lang is None:
            return Spec(title, locked=f"{col.name} is not in the Voice Desk list, so there is nothing to change.")
        options = [Opt(f"override|{c}", P.choice_words(c) + (" (the default)" if c == P.AUTO else ""),
                       c == lang.override) for c in P.choice_codes(lang)]
        if not lang.english_only:
            options.append(Opt("extra", f"extra languages: change... (now: {P.extra_words(lang.extra)})",
                               goes_on=True))

        def describe(tok: str) -> list[str]:
            if tok == "extra":
                return ["Next you type the extra languages. Nothing is saved until you say yes to it."]
            code = tok.split("|", 1)[1]
            return [f"{col.name}'s language choice changes from {P.choice_words(lang.override)} to "
                    f"{P.choice_words(code)}.",
                    "Nothing speaks by this choice yet: it is kept in the Voice Desk list for when something does."]

        def jobs(tok: str) -> list:
            if not tok.startswith("override|"):
                return []
            code = tok.split("|", 1)[1]
            return [lambda: P.set_override(edit, key, code)]

        note = ("Which language it speaks in, and voices for languages beyond English and Chinese (the old "
                "dashboard's L and O keys). " + ("English only: it follows the reply or speaks English. "
                                                 if lang.english_only else "") + "Stored and shown only.")
        return Spec(title, options, describe, jobs, note=note, needs=P.NEEDS_LANG)

    def ask_extra(self, col: Any) -> None:
        """A line to type the extra languages (code=voice, ...), then one yes / no line with the old and the new."""
        edit = self.edit
        key = getattr(col, "voice_key", "") or getattr(col, "key", "")
        title = f"{col.name}: extra languages"
        try:
            lang = P.languages_now(edit, key)
        except Exception as exc:                         # noqa: BLE001
            self.push_screen(NoticePopup(title, Text(f"The Voice Desk list could not be read ({type(exc).__name__}). "
                                                     "Nothing was changed.")))
            return

        def answered(text: Optional[str]) -> None:
            if text is None:
                return
            clean, why = P.check_extra(edit, key, text)
            if why:
                self.push_screen(NoticePopup(title, Text(f"{why} Nothing was changed.")))
                return
            if clean == lang.extra:
                self.push_screen(NoticePopup(title, Text("That is what it is now, so there is nothing to change.")))
                return
            body = [Text(f"  now    {P.extra_words(lang.extra)}"), Text(f"  new    {P.extra_words(clean)}"),
                    Text(""), Text(P.NEEDS_LANG, style=DIM), Text(""), self.dry_line()]
            self.push_screen(NoticePopup(f"Change {col.name}'s extra languages?", join(body), ask="yes, change it"),
                             lambda yes: self.run_jobs([lambda: P.set_extra(edit, key, text)]) if yes else None)

        self.push_screen(AskPopup(title, P.EXTRA_HINT, ", ".join(f"{c}={v}" for c, v in lang.extra.items())),
                         answered)

    # -- the one terminal session voice goes to (Claude, Gemini, Codex) -------------------------------------------
    def mount_view(self, col: Any) -> Optional["M.MountView"]:
        """The column's mount, read fresh (one read-only herdr call) so a window opened a moment ago is listed; the
        table's last read when the desk was not told where the pins and herdr are."""
        m = getattr(col, "mount", None)
        if m is None:
            return None
        if self.edit.pins is None or not self.edit.herdr:
            return m
        try:
            return M.view(self.edit.pins, self.edit.herdr, m.target)
        except Exception:                                # noqa: BLE001  a failed look must never take the desk down
            return m

    def spec_mount(self, col: Any) -> Spec:
        """Mount ONE open terminal session of this service onto voice: pick one of the open windows herdr lists,
        or type / paste a code; or unmount. Only the voice line's pin changes (backup, read back, u undoes it)."""
        edit = self.edit
        name = col.name
        title = f"{name}: the one session voice goes to"
        m = self.mount_view(col)
        if m is None or edit.pins is None:
            return Spec(title, locked=f"The desk does not know where the voice line keeps {name}'s mounted session, "
                                      "so nothing can be mounted from here.")
        target = m.target
        options: list[Opt] = []
        by: dict = {}
        if m.code and m.window is None:
            options.append(Opt(f"mount|{m.code}", f"{M.short(m.code)}  mounted, its window is not open", now=True))
        for s in m.sessions:
            if not s.code:
                if m.bound and m.window == s:          # the mounted session, found in the one window with no code
                    options.append(Opt(f"mount|{m.code}", M.no_code_words(s, name, m), now=True))
                else:
                    options.append(Opt(f"nocode|{s.pane}", M.no_code_words(s, name, m), disabled=True))
                continue
            by[s.code] = s
            options.append(Opt(f"mount|{s.code}", M.session_label(s), now=s.code == m.code))
        if m.bound and m.window is not None:
            by[m.code] = m.window
        if not m.sessions:
            options.append(Opt("none", f"no {name} window is open in herdr"
                                       + (f" ({m.error})" if m.error else ""), disabled=True))
        options.append(Opt(M.TYPE, "type or paste a session code...", goes_on=True))
        options.append(Opt(M.UNMOUNT, "unmount: nothing mounted", now=not m.code))
        was = f"session {m.code}" if m.code else "nothing mounted"

        def describe(tok: str) -> list[str]:
            if tok == M.UNMOUNT:
                return [f"Nothing is mounted for {name} any more (now: {was}).",
                        f"Voice {name} then uses its only open window when exactly one is open, otherwise a "
                        "separate background session, as before mounting."]
            code = tok.split("|", 1)[1]
            s = by.get(code)
            return [f"Voice {name} is mounted on session {code}" + (f" ({M.where_words(s)})." if s else "."),
                    f"Only this session gets the words. If its window is closed, a voice turn says the mounted "
                    f"{name} session is not open and starts nothing else.",
                    f"Before: {was}. u puts it back exactly."]

        def jobs(tok: str) -> list:
            if tok == M.UNMOUNT:
                return [lambda: M.unmount(edit, target)]
            if tok.startswith("mount|"):
                code = tok.split("|", 1)[1]
                return [lambda: M.mount(edit, target, code)]
            return []

        note = (f"The {name} windows open in herdr, newest activity first: code, window, folder, last activity. "
                "One mounted session at a time: voice types only into it, and says so when it is not open. "
                + M.APP_NOTE)
        return Spec(title, options, describe, jobs, note=note, needs=M.NEEDS_MOUNT)

    def ask_mount_code(self, col: Any) -> None:
        """"type or paste a session code...": a line to type the code, then one yes / no line with the old and the
        new, and whether that session's window is open now."""
        edit = self.edit
        m = self.mount_view(col)
        if m is None or edit.pins is None:
            return
        name, target = col.name, m.target
        title = f"{name}: mount a session by its code"
        note = (f"The {name} session's code: the long id its conversation is saved under (the list before this one "
                "shows the codes of the open windows). A code whose window is not open is mounted all the same: the "
                f"light stays yellow and voice says the mounted {name} session is not open until its window opens. "
                + M.APP_NOTE)

        def answered(text: Optional[str]) -> None:
            if text is None:
                return
            code = text.strip()
            if not code:
                return
            if not M.CODE_OK.match(code):
                self.push_screen(NoticePopup(title, Text("That does not look like a session code (letters, digits, "
                                                         "dots and dashes, 4 to 128 of them). Nothing was changed.")))
                return
            if code == m.code:
                self.push_screen(NoticePopup(title, Text("That is what it is now, so there is nothing to change.")))
                return
            s = next((x for x in m.sessions if x.code == code), None)
            body = [Text(f"  now    {('session ' + m.code) if m.code else 'nothing mounted'}"),
                    Text(f"  new    session {code}"), Text("")]
            blind, why = M.would_bind(target, code, m.sessions)
            if s:
                body.append(Text(f"Its window is open: {M.where_words(s)}.", style=COLOUR[D.GREEN]))
            elif blind:
                body.append(Text(f"No window shows this code, but voice will use {M.where_words(blind)}: {why}.",
                                 style=COLOUR[D.GREEN]))
            else:
                body.append(Text(f"No open {name} window has this code right now. It is mounted anyway: the light "
                                 f"stays yellow and voice says the mounted {name} session is not open until its "
                                 "window opens.", style=COLOUR[D.YELLOW]))
                if why:
                    body.append(Text(f"The {name} window that shows no session code is not used for it: {why}.",
                                     style=COLOUR[D.YELLOW]))
                if not M.known(target, code):
                    body.append(Text(f"No {name} conversation with this code was found on this Mac either: check "
                                     "the code.", style=COLOUR[D.YELLOW]))
            body += [Text(""), Text(M.NEEDS_MOUNT, style=DIM), Text(""), self.dry_line()]
            self.push_screen(NoticePopup(f"Mount this {name} session?", join(body), ask="yes, mount it"),
                             lambda yes: self.run_jobs([lambda: M.mount(edit, target, code)]) if yes else None)

        self.push_screen(AskPopup(title, note, ""), answered)

    def open_row(self, row: Row, anchor: Optional[tuple[int, int]] = None) -> None:
        """Enter on a row's name: the same choices, applied to every agent the row can be changed for, after one
        confirmation that lists each agent's before and after."""
        screen = self.table_screen()
        if screen is None:
            return
        specs: list[tuple[Any, Spec]] = []
        skipped: list[tuple[str, str]] = []
        for col in screen.cols:
            cell = screen.cells.get((row.key, col_id(col)))
            if cell is None or cell.blank:
                continue
            same = (cell.setting == row.key or (row.key == "rung:main" and cell.setting == "model")
                    or (row.key.startswith("presence:") and cell.setting == "team_room"))
            if not cell.setting or not same:
                skipped.append((col.name, cell.fixed or "changed behind the desk's back: answer that on its own cell"))
                continue
            spec = self.spec_for(col, cell.setting)
            if spec is None or spec.locked or not spec.options:
                skipped.append((col.name, (spec.locked if spec else "") or "nothing to choose here"))
                continue
            specs.append((col, spec))
        if not specs or row.key == "extras":
            self.push_screen(NoticePopup(row.label.strip() + ": every agent", Text(
                "This row cannot be changed for every agent at once: change it cell by cell."
                if row.key == "extras" else "No agent can be changed on this row from the desk.")))
            return
        tokens: list[str] = []
        labels: dict = {}
        for _col, spec in specs:
            for o in spec.options:
                if not o.disabled and not o.goes_on and o.token not in labels:
                    labels[o.token] = o.label
                    tokens.append(o.token)
        options = [Opt(tok, labels[tok], now=all(any(o.token == tok and o.now for o in spec.options)
                                                 for _c, spec in specs)) for tok in tokens]

        def plan(token: str) -> tuple[list[str], list]:
            lines, jobs = [], []
            for col, spec in specs:
                cur = spec.current
                was = cur.label if cur else "not set"
                pick = next((o for o in spec.options if o.token == token), None)
                if pick is None or pick.disabled:
                    lines.append(f"{col.name}: stays {was} ({'not a choice for this agent' if pick is None else pick.note or 'not a choice here'})")
                elif pick.now:
                    lines.append(f"{col.name}: stays {was} (already)")
                else:
                    lines.append(f"{col.name}: {was} -> {pick.label}")
                    jobs += spec.jobs(token)
            lines += [f"{name}: not changed ({why})" for name, why in skipped]
            return lines, jobs

        def describe(token: str) -> list[str]:
            lines, jobs = plan(token)
            return lines if jobs else []

        combined = Spec(f"{row.label.strip()}: every agent", options, describe, lambda token: plan(token)[1],
                        note="One choice for every agent this row can be changed for. The next step lists each "
                             "agent's before and after.", needs=specs[0][1].needs)
        self.push_screen(CellMenu(combined, anchor, dry=not self.is_armed),
                         lambda token: self.run_jobs(combined.jobs(token)) if token is not None else None)

    def spec_model(self, v: D.AgentView) -> Spec:
        edit, loader = self.edit, self.loader
        title = f"{v.name}: language model"
        if v.model_locked or not v.model:
            return Spec(title, locked=(f"{v.name}'s model is locked: it is shown here, never changed from the desk."
                                       if v.model else "The model could not be read, so it cannot be changed from here."))
        ms = v.model
        note = f"On {D.service_name(ms.service)}." + (f" Note for {v.name}: {v.model_note}." if v.model_note else "")
        return Spec(title, [Opt(m, D.model_name(m), m == ms.model) for m in E.model_choices(ms.service, ms.model)],
                    lambda m: [f"{v.name}'s model changes from {D.model_name(ms.model)} to {D.model_name(m)}."],
                    lambda m: [lambda: E.change_model(v.profile, m, edit, loader=loader)],
                    note=note, needs=E.NEEDS["model"])

    def spec_effort(self, v: D.AgentView) -> Spec:
        edit, loader = self.edit, self.loader
        title = f"{v.name}: thinking effort"
        if v.model_locked or not v.model:
            return Spec(title, locked=f"{v.name}'s thinking effort is locked with its model.")
        now = v.model.effort
        efforts = list(E.EFFORTS) if now in dict(E.EFFORTS) else [(now, effort_words(now))] + list(E.EFFORTS)
        return Spec(title, [Opt(e, words, e == now) for e, words in efforts],
                    lambda e: [f"{v.name}'s thinking effort changes from {now} to {e}."],
                    lambda e: [lambda: E.change_effort(v.profile, e, edit, loader=loader)],
                    note="How hard it thinks before it answers.", needs=E.NEEDS["effort"])

    def spec_rung(self, v: D.AgentView, rung: str) -> Spec:
        """One rung of the fallback ladder: the model this agent uses there, or off. The order is never a choice."""
        edit, loader = self.edit, self.loader
        if rung == D.MAIN_RUNG:
            return self.spec_model(v)
        title = f"{v.name}: {E.RUNG_NAME[rung]}"
        if v.backups_locked:
            return Spec(title, locked=f"{v.name} has no backups by design, and the desk does not change that.")
        lad = D.ladder(v)
        r = lad.rung(rung)
        b = r.backup if r else None
        on = bool(b and b.on)
        have = b.model if b else None
        options = [Opt(m, D.model_name(m), on and m == have) for m in E.rung_choices(rung, have)]
        options.append(Opt("", "off: skip this rung", not on))

        def describe(m: str) -> list[str]:
            was = D.model_name(have) if on else "off"
            out = [f"{v.name}'s {E.RUNG_NAME[rung]} changes from {was} to {D.model_name(m) if m else 'off'}."]
            if not m:
                out.append("The desk remembers it, so it can be switched on again.")
            if not lad.in_order:
                out.append("The same change writes the list in the standard order (it is not in it now).")
            return out

        note = ("The order of the ladder is fixed: Claude subscription, Antigravity (Claude, then Gemini), ChatGPT, "
                "NVIDIA. Here you only pick the model for this rung, or off.")
        return Spec(title, options, describe,
                    lambda m: [lambda: E.set_rung(v.profile, rung, m or None, edit, loader=loader)],
                    note=note, needs=E.NEEDS["ladder"])

    def spec_extras(self, v: D.AgentView) -> Spec:
        """Backups that are not on the ladder: take one off the list, or put a remembered one back."""
        edit, loader = self.edit, self.loader
        title = f"{v.name}: extra backups, not on the ladder"
        if v.backups_locked:
            return Spec(title, locked=f"{v.name} has no backups by design, and the desk does not change that.")
        lad = D.ladder(v)
        options = [Opt("", "leave it as it is", True)]
        by: dict = {}
        for b in lad.extras:
            tok = f"off|{b.service}|{b.model}"
            by[tok] = b
            options.append(Opt(tok, f"take {backup_words(b, short=False)} off the list"))
        for b in lad.extras_off:
            tok = f"on|{b.service}|{b.model}"
            by[tok] = b
            options.append(Opt(tok, f"put {backup_words(b, short=False)} back on the list", note=b.note))

        def describe(tok: str) -> list[str]:
            b = by[tok]
            on = tok.startswith("on|")
            return [f"{v.name}'s backup {backup_words(b, short=False)} is switched {'on' if on else 'off'}"
                    + ("." if on else " (the desk remembers it, so it can be switched on again).")]

        return Spec(title, options, describe,
                    lambda tok: [lambda: E.set_backup(v.profile, by[tok].service, by[tok].model,
                                                      tok.startswith("on|"), edit, loader=loader)],
                    note="These are on the list but not on the standard ladder.", needs=E.NEEDS["backup"])

    def spec_drift(self, v: D.AgentView, setting: str) -> Spec:
        """Something changed a setting behind the desk's back: put it back, or accept it."""
        edit, loader = self.edit, self.loader
        i = int(setting.split(":", 1)[1])
        if i >= len(v.drifts):
            return Spec(f"{v.name}: changed", locked="That change is no longer there.")
        d = v.drifts[i]

        def describe(tok: str) -> list[str]:
            if tok == "put":
                return [f"{v.name}'s {d.what} is put back to {d.words(d.want)}, through the desk's own safe path "
                        "(backup, read back, u undoes it)."]
            return [f"{v.name}'s {d.what} stays {d.words(d.have)}. Only the desk's own record changes; "
                    "no agent's settings file is touched."]

        return Spec(f"{v.name}: {d.what} was changed behind the desk's back",
                    [Opt("put", f"put it back: {d.words(d.want)} again (what the desk set)"),
                     Opt("accept", f"accept it: keep {d.words(d.have)}, and the desk expects that from now on")],
                    describe,
                    lambda tok: [lambda: W.put_back(d, edit, loader=loader)] if tok == "put"
                    else [lambda: W.accept(d, edit)], note=d.sentence)

    def spec_place(self, v, setting: str) -> Spec:
        """Spoken replies at one place: the old Voice Desk grid's own switch (its code does the change)."""
        edit = self.edit
        place = setting.split(":", 1)[1]
        key = getattr(v, "voice_key", "") or getattr(v, "key", "")
        name = D.place_words(place)
        title = f"{v.name}: spoken replies, {name}"
        try:
            _title, options, why = E.voice_place_menu(key, place, edit)
        except Exception as e:                           # noqa: BLE001
            options, why = [], f"the switch could not be read ({type(e).__name__})"
        if not options:
            why = why or "nothing to switch here"
            return Spec(title, locked=why[:1].upper() + why[1:])
        labels = {tok: label for tok, label, _ in options}
        note = "This is the same switch the Voice Desk dashboard had."
        here = next((p for p in getattr(v, "voice_places", ()) if p.key == place), None)
        if here is not None and here.set_by in SET_BY_SAID:
            note = f"Now: {here.value}, {SET_BY_SAID[here.set_by]}. " + note
        return Spec(title, [Opt(tok, label, cur) for tok, label, cur in options],
                    lambda tok: [f"{v.name}: spoken replies, {name}: {labels[tok]}.",
                                 "The Voice Desk's own switch code makes the change: every file is backed up, "
                                 "written, read back and put back at once if it does not read back."],
                    lambda tok: [lambda: E.set_voice_place(key, place, tok, edit)],
                    note=note, needs=E.NEEDS["voice_place"])

    def spec_voice(self, v, setting: str) -> Spec:
        edit = self.edit
        agent, cfg = self.voice_agent(v)
        title = f"{v.name}: " + {"voice": "English voice", "voice_zh": "Chinese voice", "speed": "speaking speed",
                                 "effect": "voice effect"}[setting]
        if agent is None:
            return Spec(title, locked=f"{v.name} has no voice in the Voice Desk list, so there is nothing to change.")
        speeds = dict(E.SPEEDS)
        note = ("The whole voice bank: local Kokoro voices first, Microsoft below. A voice marked \"not playable yet\" "
                "cannot be picked until this agent's own script can play it. One voice, silent if it fails.")
        lands = getattr(v, "takes_effect", "")
        if lands:
            note += f" Where it lands: {lands}."
        testable = ""
        if setting == "effect":
            options = [Opt(tok, label, cur, off) for tok, label, cur, off in E.voice_options(agent, "effect", cfg)]
            note = (f"The effect on {D.voice_label(agent.voice)}. It works on Kokoro voices only: with a Microsoft "
                    "voice it does not apply (the voice is spoken plain).")
        elif setting == "speed":
            if agent.key not in self.speed_why:
                self.speed_why[agent.key] = E.speed_fixed(agent.key, edit)
            why = self.speed_why[agent.key]
            if why:
                return Spec(title, locked=why[0].upper() + why[1:] + ".")
            now = E.speed_word(agent.speed)
            options = [Opt(word, f"{word} ({value:g})", word == now) for word, value in E.SPEEDS]
            note = "How fast it speaks." + ("" if any(o.now for o in options) else f" Now: {now}.")
        else:
            options = [Opt(tok, label, cur, off, goes_on=tok == E.OTHER_VOICE)
                       for tok, label, cur, off in E.voice_options(agent, setting, cfg)]
            testable = "en" if setting == "voice" else "zh"
            if setting == "voice_zh" and not agent.english_only:     # the old dashboard's L and O, one line away
                extra, choice = dict(agent.voices_extra), str(agent.voice_override or P.AUTO).lower()
                now_words = P.extra_words(extra) if choice == P.AUTO else P.choice_words(choice)
                options.append(Opt(LANG_MENU, f"other languages... (stored only; now: {now_words})", goes_on=True))

        def describe(tok: str) -> list[str]:
            if setting == "speed":
                return [f"{v.name}'s speaking speed changes from {E.speed_word(agent.speed)} to {tok}."]
            if setting == "effect":
                return [f"{v.name}'s voice effect changes from {agent.effect or 'none'} to {tok}."]
            before = agent.voice if setting == "voice" else agent.voice_zh
            lang = "English" if setting == "voice" else "Chinese"
            out = [f"{v.name}'s {lang} voice changes from {D.voice_label(before)} to "
                   f"{D.voice_label(tok) if tok else 'silent'}."]
            if setting == "voice" and tok and agent.effect and not E.effect_applies(agent, tok):
                out.append(f"{v.name}'s sound effect ({agent.effect}) does not apply to this voice: it is spoken "
                           "plain. The effect stays stored and comes back with a Kokoro voice.")
            return out

        return Spec(title, options, describe,
                    lambda tok: [lambda: E.change_voice(agent.key, setting,
                                                        speeds[tok] if setting == "speed" else (tok or None), edit)],
                    note=note, needs=E.NEEDS["voice"], testable=testable, agent=agent)

    def spec_team_room(self, v: D.AgentView) -> Spec:
        """In or out of the Team Room: ONE switch for the terminal room and the Telegram group together."""
        edit, loader, runner = self.edit, self.loader, self.runner
        title = f"{v.name}: Team Room and Telegram team group"
        if v.room_lead or v.in_room is None:
            return Spec(title, locked=(f"{v.name} leads the Team Room and cannot be taken out of it." if v.room_lead
                                       else "The Team Room members file could not be read, so the desk will not change it."))
        has_bot = any(p.key == "tg_private" and p.state == D.ON for p in v.presence)

        def describe(tok: str) -> list[str]:
            joining = tok == "in"
            out = [f"{v.name} {'joins' if joining else 'leaves'} the Team Room: the terminal room and the Telegram "
                   "group change together, or not at all.",
                   "The room is rebuilt, so the chat on screen starts fresh. The old lines stay in the saved history."]
            if has_bot:
                out.append("Putting its bot into the Telegram group is Peter's own tap in Telegram; until then the "
                           "lead's bot speaks for it." if joining else
                           "Its bot is taken out of the Telegram group. Putting it back later is Peter's own tap in Telegram.")
            return out

        return Spec(title, [Opt("in", "in the room", bool(v.in_room)), Opt("out", "out of the room", not v.in_room)],
                    describe,
                    lambda tok: [lambda: E.set_team_room(v.profile, tok == "in", edit, loader=loader, runner=runner)],
                    note="One switch moves both places: the Team Room in the terminal and the Telegram team group.",
                    needs=E.NEEDS["team_room"])

    def spec_talk(self, v: D.AgentView, rule: str) -> Spec:
        """Who it may talk to: a few plain choices, kept in the one rules file the Team Room can read."""
        edit = self.edit
        rules = {r.key: r for r in v.talk_rules}
        title = f"{v.name}: " + dict(TALK_ROW).get(rule, rule).lower()
        if rule not in rules:
            return Spec(title, locked="The rules file could not be read, so there is nothing to change.")
        lead = next((a for a in (self.snapshot.agents if self.snapshot else ()) if a.room_lead), None)
        lead_name, lead_profile = (lead.name, lead.profile) if lead else ("the lead", "orchestrator")
        r = rules[rule]
        if rule == "speaks":
            options = [Opt("yes", "yes", r.token == "yes"),
                       Opt("no", "no", r.token == "no", disabled=bool(r.locked), note=r.locked)]
        elif rule == "hands_out":
            options = [Opt("yes", "yes", r.token == "yes"), Opt("no", "no", r.token == "no")]
        elif rule == "takes_from":
            options = [Opt("lead", lead_name, r.token == "lead", disabled=v.room_lead,
                           note="this is the lead" if v.room_lead else ""),
                       Opt("cards", "job cards only", r.token == "cards"),
                       Opt("nobody", "nobody (Peter only)", r.token == "nobody")]
        else:
            options = [Opt("always", "always", r.token == "always", disabled=bool(r.locked), note=r.locked),
                       Opt("when_named", "when named in the line", r.token == "when_named", disabled=bool(r.locked),
                           note=r.locked)]
        takes_words = {"lead": f"takes work from {lead_name}", "cards": "takes work only as job cards",
                       "nobody": "takes work from nobody (Peter only)"}
        follows = bool(self.snapshot and self.snapshot.room_follows_rules)

        def describe(tok: str) -> list[str]:
            if rule == "speaks":
                first = f"{v.name} {'may' if tok == 'yes' else 'may not'} speak in the Team Room."
            elif rule == "hands_out":
                first = f"{v.name} {'hands' if tok == 'yes' else 'does not hand'} out work."
            elif rule == "takes_from":
                first = f"{v.name} {takes_words[tok]}."
            else:
                first = f"{v.name} answers {'every line' if tok == 'always' else 'when named in the line'}."
            return [first, "The rule is written into the desk's rules file. " + (
                "The Team Room reads that file." if follows else
                "The Team Room does not read that file yet: recorded, not yet enforced.")]

        def changes_for(tok: str) -> dict:
            if rule == "speaks":
                return {"speaks_in_team_room": tok == "yes"}
            if rule == "hands_out":
                return {"hands_out_work": tok == "yes"}
            if rule == "takes_from":
                return {"only_job_cards": tok == "cards", "takes_work_from": "" if tok == "nobody" else lead_profile}
            return {"answers": tok}

        return Spec(title, options, describe,
                    lambda tok: [lambda: E.change_rules(v.profile, changes_for(tok), edit)],
                    note="Where the rule stands today: " + r.standing + ".", needs=E.NEEDS["rule"])

    @work(thread=True, group="acd-change")
    def run_jobs(self, jobs: list) -> None:
        results = []
        for job in jobs:
            try:
                got = job()
                results += got if isinstance(got, list) else [got]
            except Exception as e:                       # noqa: BLE001  a failed change must never take the desk down
                results.append(E.Result(False, f"Not changed. {type(e).__name__}: {e}"))
        self.call_from_thread(self.show_results, results)

    def show_results(self, results: list[E.Result]) -> None:
        self.last_results = results
        if results:
            title, body = results_text(results)
            self.push_screen(ResultPopup(title, body), self.key_goes_on)
        self.action_reread()

    def key_goes_on(self, pressed: Optional[tuple]) -> None:
        """The key that closed a result box goes on to the table as if the box had not been there."""
        if pressed:
            key, character = pressed
            self.post_message(events.Key(key, character))

    # -- the standard ladder, for every agent at once ------------------------------------------------------------
    def action_standard_ladder(self) -> None:
        """s: show what putting every agent on the standard ladder would change (read-only), then ask."""
        self.plan_ladder_in_background()

    @work(thread=True, exclusive=True, group="acd-ladder-plan")
    def plan_ladder_in_background(self) -> None:
        try:                                             # both plans only READ: nothing is written by showing them
            plans = {mode: E.plan_standard_ladder(self.edit, extras=mode, loader=self.loader)
                     for mode in ("keep", "drop")}
        except Exception as e:                           # noqa: BLE001
            self.call_from_thread(self.push_screen, NoticePopup(
                "The standard ladder", Text(f"The plan could not be made ({type(e).__name__}: {e}).")))
            return
        self.call_from_thread(self.ask_standard_ladder, plans)

    def ask_standard_ladder(self, plans: dict, mode: str = "keep") -> None:
        edit, loader = self.edit, self.loader
        left_out = [v.name for v in (self.snapshot.agents if self.snapshot else ()) if v.backups_locked]
        if not any(p.changes for some in plans.values() for p in some):
            body = ladder_body(plans[mode], mode, left_out, Text(""))
            body.append("\nEvery agent is already on the standard ladder: nothing to change.")
            self.push_screen(NoticePopup("The standard ladder", body))
            return
        self.push_screen(
            LadderPopup(plans, left_out, self.dry_line(), mode),
            lambda picked: self.run_jobs([lambda: E.apply_standard_ladder(edit, extras=picked, loader=loader)])
            if picked else None)

    # -- what only the old dashboard could do: the voice test, add / remove an agent, the listener ------------------
    def table_screen(self) -> Optional["TableScreen"]:
        return next((s for s in reversed(self.screen_stack) if isinstance(s, TableScreen)), None)

    def chosen(self) -> Any:
        """The agent (or assistant) whose column the cursor is in; None on the row names."""
        screen = self.table_screen()
        if screen is None or self.snapshot is None:
            return None
        return screen.at()[1]

    def dry_line(self) -> Text:
        if self.is_armed:
            return Text("The desk is ARMED: this is really done.", style=COLOUR[D.RED])
        return Text(f"DRY RUN ({E.why_not_armed(self.edit)}): the desk only shows what would happen.",
                    style=COLOUR[D.YELLOW])

    def action_voice_test(self) -> None:
        """t: the silent voice test, for the chosen agent or for everyone. Nothing is ever played."""
        edit = self.edit
        v = self.chosen()
        rows: list[tuple[str, str]] = []
        if v is not None and v.voice_key:
            rows.append((v.voice_key, f"only {v.name}"))
        rows.append(("*", "everyone in the Voice Desk list"))

        def picked(token: Optional[str]) -> None:
            if not token:
                return
            keys = None if token == "*" else [token]
            try:
                plan, silent = P.test_plan(edit, keys)
            except (E.EditError, OSError, ValueError) as exc:
                self.push_screen(NoticePopup("Voice test", Text(f"The Voice Desk list could not be read ({exc}).")))
                return
            body = [Text("One sentence is made in each voice to a throw-away file and checked for real sound."),
                    Text("NOTHING IS PLAYED: no sound comes out of the Mac.", style="bold"), Text(""),
                    Text("What would be said, and in which voice:", style=DIM)]
            body += [Text("  " + ln) for ln in P.plan_lines(plan, silent)]
            body += [Text(""), self.dry_line()]
            self.push_screen(
                NoticePopup("Voice test", join(body), ask="yes, run the silent test"),
                lambda yes: self.run_jobs([lambda: P.test_voices(edit, keys, synth=self.synth, probe=self.probe)])
                if yes else None)

        self.push_screen(PickPopup("Voice test: who?", rows,
                                   note="A silent test: the voices are made to files and checked, never played."),
                         picked)

    def action_add_agent(self) -> None:
        """n: a new agent in the Voice Desk list: which one, its name, what it answers to, its voice, then one
        confirm line that says exactly what is added."""
        edit = self.edit
        rows = [(prof, f"{name}: the Hermes agent \"{prof}\" on this Mac") for prof, name in P.addable(edit)]
        rows.append((P.CLI, "another assistant that runs in a terminal (like Claude, Gemini or Codex)"))

        def confirm(profile: Optional[str], display: str, names: str, voice: str) -> None:
            body = [Text("This is added to the Voice Desk list, and nothing else is touched:"),
                    Text(f"  name            {display}"),
                    Text(f"  answers to      {', '.join(gridedit.parse_names(names, display))}"),
                    Text(f"  voice           {D.voice_label(voice) if voice else 'silent: no voice'}"),
                    Text("  it is           " + (f"the Hermes agent {profile}" if profile
                                                 else "another terminal assistant")),
                    Text(""), Text(P.NEEDS_AGENT, style=DIM), Text(""), self.dry_line()]
            self.push_screen(
                NoticePopup(f"Add {display}?", join(body), ask=f"yes, add {display}"),
                lambda yes: self.run_jobs([lambda: P.add_agent(edit, display, names, voice or None, profile=profile)])
                if yes else None)

        def ask_voice(profile: Optional[str], display: str, names: str) -> None:
            try:
                cfg = A.load(edit.voice.toml)
                options = E.voice_options(P.draft_agent(display, profile), "voice", cfg)
            except Exception as exc:                     # noqa: BLE001
                self.push_screen(NoticePopup("Add an agent", Text(f"The voices could not be listed ({exc}).")))
                return
            voices = [(tok or SILENT, label) for tok, label, _cur, off in options if not off and tok != E.OTHER_VOICE]
            if not any(tok == SILENT for tok, _ in voices):
                voices.append((SILENT, "silent: no voice"))
            self.push_screen(PickPopup(f"{display}: its English voice", voices,
                                       note="One voice, silent if it fails. A Chinese voice can be added later in its column."),
                             lambda tok: confirm(profile, display, names, "" if tok == SILENT else tok) if tok else None)

        def ask_names(profile: Optional[str], display: str) -> None:
            self.push_screen(AskPopup(f"{display}: what it answers to",
                                      "Every way the listener may hear the name, separated by commas. The name "
                                      "itself is always one of them.", display.lower()),
                             lambda text: ask_voice(profile, display, text) if text is not None else None)

        def ask_name(token: Optional[str]) -> None:
            if not token:
                return
            profile = None if token == P.CLI else token
            start = next((name for prof, name in P.addable(edit) if prof == profile), "")

            def named(text: Optional[str]) -> None:
                if text is None:
                    return
                if not text:
                    self.push_screen(NoticePopup("Add an agent", Text("A new agent needs a name. Nothing was added.")))
                    return
                ask_names(profile, text)

            self.push_screen(AskPopup("New agent: its name", "The name shown on the desk and spoken back.", start), named)

        self.push_screen(PickPopup("Add an agent to the Voice Desk list", rows,
                                   note="Only the Voice Desk list gets a new block. No agent's own files change."),
                         ask_name)

    def action_remove_agent(self) -> None:
        """d: take an agent out of the Voice Desk list, after a confirm line that shows exactly what goes."""
        edit = self.edit
        try:
            listed = A.agents(A.load(edit.voice.toml))
        except (OSError, ValueError) as exc:
            self.push_screen(NoticePopup("Remove an agent", Text(f"The Voice Desk list could not be read ({exc}).")))
            return
        v = self.chosen()
        first = v.voice_key if v is not None else ""
        rows = sorted(((a.key, a.display) for a in listed), key=lambda r: r[0] != first)

        def picked(key: Optional[str]) -> None:
            if not key:
                return
            try:
                plan = P.removal_plan(edit, key)
            except Exception as exc:                     # noqa: BLE001
                self.push_screen(NoticePopup("Remove an agent", Text(f"Nothing was removed ({exc}).")))
                return
            body = [Text(f"Exactly these {len(plan.block)} lines are removed from the Voice Desk list:")]
            body += [Text("  - " + ln, style=COLOUR[D.RED]) for ln in plan.block]
            body += [Text("")] + [Text(part) for part in textwrap.wrap(plan.stays, 92)]
            body += [Text("u puts it back.", style=DIM), Text(""), self.dry_line()]
            self.push_screen(
                NoticePopup(f"Remove {plan.name} from the Voice Desk list?", join(body),
                            ask=f"yes, remove {plan.name}"),
                lambda yes: self.run_jobs([lambda: P.remove_agent(edit, key)]) if yes else None)

        self.push_screen(PickPopup("Remove which agent from the Voice Desk list?", rows,
                                   note="The next step shows exactly what would be removed, and asks first."), picked)

    def action_listener(self) -> None:
        """l: the voice line: what the listener is doing, and its controls."""
        self.push_screen(ListenerPopup())

    def listener_order(self, action: str) -> None:
        edit, svc = self.edit, self.listener_svc
        if svc is None:
            return
        if action not in P.NEEDS_TWO:
            self.run_jobs([lambda: P.listener_do(action, edit, svc=svc)])
            return
        view = P.listener_view(svc, ask_loaded=False)
        verb = "Restart" if action == "restart" else "Stop"
        if view.busy:
            self.push_screen(NoticePopup(f"{verb} the listener", join([
                Text(f"Not now: {view.busy}.", style=COLOUR[D.RED]),
                Text("The desk never stops or restarts the voice line in the middle of that. Try again when it is quiet.")])))
            return
        first = [Text(f"{verb} the listener?"), Text(P.STOPS_THE_LINE[0].upper() + P.STOPS_THE_LINE[1:] + ".",
                                                      style=COLOUR[D.RED]),
                 Text(P.ACTIONS[action][0].upper() + P.ACTIONS[action][1:] + "."), Text(""),
                 Text("This is the first of two questions.", style=DIM)]
        second = [Text("Second and last question.", style="bold"),
                  Text(P.STOPS_THE_LINE[0].upper() + P.STOPS_THE_LINE[1:]
                       + ": nobody can talk to an agent by voice until it is running again.", style=COLOUR[D.RED]),
                  Text(""), self.dry_line()]

        def again(yes: bool) -> None:
            if yes:
                self.push_screen(
                    NoticePopup(f"{verb} the listener: are you sure?", join(second),
                                ask=f"yes, {action} the listener now"),
                    lambda sure: self.run_jobs([lambda: P.listener_do(action, edit, svc=svc, confirmed=2)])
                    if sure else None)

        self.push_screen(NoticePopup(f"{verb} the listener", join(first), ask="yes, go on"), again)

    def action_pause_watcher(self) -> None:
        """P: pause or resume the watcher. While paused it reports nothing and puts nothing back. The pause is the
        desk's own small file, so, like every other change, it is written only on an armed desk."""
        edit = self.edit
        paused = not W.is_paused(edit)
        word = "paused" if paused else "resumed"
        if not self.is_armed:
            self.push_screen(NoticePopup("Watcher", join([
                Text(f"DRY RUN, nothing written ({E.why_not_armed(edit)}). This would happen: the watcher is {word}"
                     + (": it reports nothing and puts nothing back until P is pressed again." if paused else ".")),
                Text("On a desk that is not armed the watcher only reports, and never changes a setting.", style=DIM)])))
            return
        try:
            W.set_paused(edit, paused)
        except OSError as e:
            self.push_screen(NoticePopup("Watcher", Text(f"The pause could not be saved ({e.strerror or e}).")))
            return
        out = [Text("The watcher is PAUSED: it reports nothing and puts nothing back. p resumes it." if paused else
                    "The watcher is running again: it reports what was changed behind the desk's back.")]
        if edit.grid is not None:
            try:
                import watcher as voice_watcher
                voice_watcher.set_paused(edit.grid, paused)
                out.append(Text(f"The Voice Desk's own watcher (the voice switches) was {word} as well.", style=DIM))
            except Exception as e:                       # noqa: BLE001
                out.append(Text(f"The Voice Desk's own watcher could not be {word} ({type(e).__name__}).", style=DIM))
        self.push_screen(NoticePopup("Watcher", join(out)))
        self.action_reread()

    def action_undo(self) -> None:
        edit, loader, runner = self.edit, self.loader, self.runner
        last = E.last_undoable(edit)
        if last is None:
            self.push_screen(NoticePopup("Undo", Text("There is no change from the desk to undo.")))
            return
        body = join([Text("The newest change from the desk:"), Text("  " + str(last.get("what"))), Text(""),
                     Text("Undo runs it backwards through the same safe path (backup, read back).", style=DIM)])
        self.push_screen(NoticePopup("Undo the newest change?", body, ask="yes, undo it"),
                         lambda yes: self.run_jobs([lambda: E.undo(edit, loader=loader, runner=runner)]) if yes else None)

    def action_restart_review(self) -> None:
        edit = self.edit
        waiting = list(self.snapshot.waiting) if self.snapshot else []
        out = []
        if waiting:
            out.append(Text(f"{len(waiting)} saved change{'s are' if len(waiting) != 1 else ' is'} waiting for a restart:",
                            style=COLOUR[D.YELLOW]))
            out += [Text("  - " + w) for w in waiting]
        else:
            out.append(Text("No saved change is waiting for a restart."))
            out.append(Text("Model, backup and voice changes need none: new chats pick them up; an open terminal chat "
                            "keeps its old setting until a fresh chat is started.", style=DIM))
            out.append(Text("A Team Room change needs one only when the agents' system did not reload its add-ons; "
                            "the desk lists it here when that happens.", style=DIM))
        out.append(Text(""))
        out += [Text(part) for part in textwrap.wrap(E.RESTART_WORDS, 92)]
        if edit.restart_request.exists():
            out.append(Text("A restart request is already waiting for a quiet moment.", style=COLOUR[D.YELLOW]))
        if not self.is_armed:
            out.append(Text(f"The desk is in dry run ({E.why_not_armed(edit)}): Y only shows the request, it is not written.",
                            style=COLOUR[D.YELLOW]))
        reason = "; ".join(waiting)[:180] if waiting else "asked for from the Agent Control Desk"
        self.push_screen(NoticePopup("Restart review", join(out), ask="leave a restart request for the helper"),
                         lambda yes: self.run_jobs([lambda: E.request_restart(edit, reason)]) if yes else None)


if __name__ == "__main__":
    Desk(launcher_armed="--armed" in sys.argv[1:]).run()
