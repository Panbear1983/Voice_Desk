"""How the voice grid looks: turns the rows from gridevidence into coloured text. No widgets here, so it
is easy to test without a screen."""
from __future__ import annotations

import re
import time
from typing import Optional

from rich.text import Text

import gridevidence as E
import gridstate as G
import onoff
import syshealth
import voiceedit
import voicetest

COLOUR = {"ok": "#2ecc71", "idle": "#7f8c8d", "warn": "#f1c40f", "bad": "#e74c3c", "na": "#444444",
          "unfinished": "#e67e22"}
MEANING = {"ok": "green: spoke recently and it worked", "idle": "grey: idle, nothing to say lately (not a failure)",
           "warn": "yellow: worked last time, but some recent replies were text only",
           "bad": "red: mismatch or failure",
           "unfinished": "orange: switched here, but nothing is wired there yet (never green)"}
COLUMN_WIDTH = 17


def voice_short(v: str) -> str:
    m = re.match(r"^[a-z]{2,3}-[A-Z]{2}-([A-Za-z]+?)(Multilingual)?Neural$", v or "")
    return m.group(1) if m else (v or "?")


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def cell_text(cell: G.Cell, ev: E.Evidence, here: Optional[bool] = None, place: str = "") -> Text:
    """Part 4, the wiring matrix: not here = a blank cell. Here = two lines, the setting (Off / Voice-back / Always)
    and what the evidence says, in the evidence's colour; here but not wired = "not wired yet" in orange, never
    green. here=None: the default (here wherever something is wired)."""
    here = cell.value != G.NA if here is None else here
    if not here:
        return Text("")
    if G.unwired(cell, place) if place else cell.value == G.NA:
        t = Text()
        t.append("here", style="bold white")
        t.append("\n")
        t.append("◌ not wired yet", style=COLOUR["unfinished"])
        return t
    status = "bad" if cell.problem else ev.status
    head = _clip(ev.headline or cell.problem, COLUMN_WIDTH - 2)
    t = Text()
    t.append(cell.label, style="bold white" if cell.value != G.OFF else "bold #aaaaaa")
    t.append("\n")
    t.append("● ", style=COLOUR.get(status, COLOUR["idle"]))
    t.append(head, style=COLOUR.get(status, COLOUR["idle"]))
    return t


def test_text(results: Optional[list[voicetest.TestResult]], busy: bool, silent: bool = False) -> Text:
    if busy:
        return Text("testing…", style="#3498db")
    if silent:
        return Text("silent\n(no voice set)", style="#7f8c8d")
    if not results:
        return Text("not tested\n(T tests)", style="#7f8c8d")
    t = Text()
    for i, r in enumerate(results[:2]):
        if i:
            t.append("\n")
        t.append(f"{r.lang} ✓ {r.seconds:.1f}s" if r.ok else f"{r.lang} ✗ FAILED · silent", style=COLOUR["ok"] if r.ok else COLOUR["bad"])
    return t


def voice_text(row: E.Row) -> Text:
    v = row.voice
    t = Text()
    if v.silent:
        t.append("silent", style="bold #aaaaaa")
        t.append("\nno voice set", style="#7f8c8d")
        return t
    t.append(f"{voice_short(v.en) if v.en else 'EN silent'} · {voice_short(v.zh) if v.zh else 'English only'}")
    extras = f" +{len(v.extra)}" if v.extra else ""
    effect = f"effect {v.effect}" if v.effect else ""
    t.append(f"\none voice, no backup{extras}" if not effect else f"\n{effect}{extras}", style="#7f8c8d")
    return t


def header_labels() -> list[str]:
    return ["Agent", "Voice (EN · ZH)", *[name for _, name in G.PLACES], "Voice check"]


def health_text(lights: list[syshealth.Light]) -> Text:
    t = Text()
    for i, light in enumerate(lights):
        if i:
            t.append("   ")
        colour = "#7f8c8d" if light.ok is None else COLOUR["ok"] if light.ok else COLOUR["bad"]
        t.append("● ", style=colour)
        t.append(light.label, style=colour if light.ok is False else "white")
    return t


def detail_text(row: E.Row, results: Optional[list[voicetest.TestResult]], now: Optional[float] = None) -> Text:
    now = now or time.time()
    v = row.voice
    t = Text()
    t.append(f"{row.agent.display}", style="bold #9b59b6")
    if v.silent:
        t.append("  silent: no voice set, so nothing speaks for it (E gives it one)")
    else:
        english = f"English {v.en}" if v.en else "English silent (no voice)"
        mandarin = f"Mandarin {v.zh}" if v.zh else "English only (a Chinese line stays silent)"
        t.append(f"  {english}  ·  {mandarin}  ·  one voice each, no backup: if it fails the line is silent  ·  "
                 f"language: {v.override}")
    if v.effect:
        import simba_tts
        t.append(f"  ·  effect: {v.effect} = {simba_tts.describe(v.effect)} (F changes it)")
    if v.extra:
        t.append("  ·  also " + ", ".join(f"{k}: {x}" for k, x in v.extra.items()))
    if row.kind == G.HERMES:
        t.append("\nMandarin lines go through Hermes' voice script, which picks its own Mandarin voice; this agent's "
                 "Mandarin setting is kept for Voice Desk and Claude.", style="#7f8c8d")
    for place, name in G.PLACES:
        cell, ev = row.cells[place], row.evidence[place]
        t.append("\n")
        if not row.is_here(place):
            t.append("  ", style="default")
            t.append(f"{name}: not here", style="#7f8c8d")
            t.append(f"  (X puts {row.agent.display} here)", style="#555555")
            continue
        phrase, stored = row.phrase(place)
        wake = (f"  wakes with “{phrase}”" + ("" if stored else " (today's)") if phrase else "  no activation phrase")
        if G.unwired(cell, place):
            t.append("◌ ", style=COLOUR["unfinished"])
            t.append(f"{name}: ", style="bold")
            t.append(f"here, not wired yet — {G.wiring_hint(row.agent, place)}", style=COLOUR["unfinished"])
            t.append(wake, style="#9b59b6")
            continue
        colour = COLOUR["bad" if cell.problem else ev.status]
        t.append("● ", style=colour)
        t.append(f"{name}: ", style="bold")
        t.append(f"{cell.label}", style="bold white")
        t.append(f" — {ev.detail or cell.problem}", style=colour if ev.status in ("bad", "warn") else "default")
        t.append(f"  [{cell.note}]", style="#555555")
        t.append(wake, style="#9b59b6")
        if onoff.can_switch(row.agent, place):
            t.append("  ⏎ Enter changes it", style="#9b59b6")
        if row.quiet.get(place):
            t.append(f"  quiet hours {row.quiet[place]} (stored, not enforced yet)", style="#7f8c8d")
    if results:
        when = time.strftime("%H:%M", time.localtime(results[0].at)) if results[0].at else ""
        t.append(f"\nVoice check {when}: ", style="bold")
        for i, r in enumerate(results):
            t.append(("  " if i else "") + f"{r.lang} {r.voice}: ", style="default")
            t.append(r.detail if r.ok else f"FAILED, {r.detail}", style=COLOUR["ok"] if r.ok else COLOUR["bad"])
    return t


def history_text(paths: voiceedit.EditPaths, n: int = 4, watcher_lines: Optional[list[str]] = None) -> Text:
    lines = voiceedit.recent(paths, n)
    t = Text()
    t.append("Changes", style="bold #9b59b6")
    t.append(" (U undoes the newest)  " if lines else "  none yet — Enter on a cell switches it; E / Z change a voice; A / D / X / P change the grid; all with a backup (? explains the colours)\n", style="#7f8c8d")
    if lines:
        t.append("\n" + "\n".join(lines), style="#bbbbbb")
    if watcher_lines:
        t.append("\nPut back by the watcher (W pauses it)\n", style="bold #9b59b6")
        t.append("\n".join(watcher_lines), style="#bbbbbb")
    return t
