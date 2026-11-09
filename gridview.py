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

COLOUR = {"ok": "#2ecc71", "idle": "#7f8c8d", "warn": "#f1c40f", "bad": "#e74c3c", "na": "#444444"}
MEANING = {"ok": "green: spoke recently and it worked", "idle": "grey: idle, nothing to say lately (not a failure)",
           "warn": "yellow: worked last time, but some recent replies were text only",
           "bad": "red: mismatch or failure"}
COLUMN_WIDTH = 17


def voice_short(v: str) -> str:
    m = re.match(r"^[a-z]{2,3}-[A-Z]{2}-([A-Za-z]+?)(Multilingual)?Neural$", v or "")
    return m.group(1) if m else (v or "?")


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def cell_text(cell: G.Cell, ev: E.Evidence) -> Text:
    """Two lines: the setting (Off / Voice-back / Always) and what the evidence says, in the evidence's colour."""
    if cell.value == G.NA:
        return Text("—", style=COLOUR["na"], justify="center")
    status = "bad" if cell.problem else ev.status
    head = _clip(ev.headline or cell.problem, COLUMN_WIDTH - 2)
    t = Text()
    t.append(cell.label, style="bold white" if cell.value != G.OFF else "bold #aaaaaa")
    t.append("\n")
    t.append("● ", style=COLOUR.get(status, COLOUR["idle"]))
    t.append(head, style=COLOUR.get(status, COLOUR["idle"]))
    return t


def test_text(results: Optional[list[voicetest.TestResult]], busy: bool) -> Text:
    if busy:
        return Text("testing…", style="#3498db")
    if not results:
        return Text("not tested\n(T tests)", style="#7f8c8d")
    t = Text()
    for i, r in enumerate(results[:2]):
        if i:
            t.append("\n")
        t.append(f"{r.lang} ✓ {r.seconds:.1f}s" if r.ok else f"{r.lang} ✗ failed", style=COLOUR["ok"] if r.ok else COLOUR["bad"])
    return t


def voice_text(row: E.Row) -> Text:
    v = row.voice
    t = Text()
    t.append(f"{voice_short(v.en)} · {voice_short(v.zh)}")
    extras = f" +{len(v.extra)}" if v.extra else ""
    effect = f" · {v.effect}" if v.effect else ""
    t.append(f"\nbackup {voice_short(v.backup)}{extras}{effect}", style="#7f8c8d")
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
    t.append(f"  English {v.en}  ·  Mandarin {v.zh}  ·  backup {v.backup}  ·  language: {v.override}")
    if v.effect:
        t.append(f"  ·  effect: {v.effect} (F changes it)")
    if v.extra:
        t.append("  ·  also " + ", ".join(f"{k}: {x}" for k, x in v.extra.items()))
    if v.using_shared:
        t.append("\nThe mic window speaks with the shared voice, not this one (V key on the listener screen is off).", style="#e67e22")
    if row.kind == G.HERMES:
        t.append("\nMandarin lines go through Hermes' voice script, which picks its own Mandarin voice; this agent's "
                 "Mandarin setting is kept for Voice Desk and Claude.", style="#7f8c8d")
    for place, name in G.PLACES:
        cell, ev = row.cells[place], row.evidence[place]
        if cell.value == G.NA:
            continue
        colour = COLOUR["bad" if cell.problem else ev.status]
        t.append("\n")
        t.append("● ", style=colour)
        t.append(f"{name}: ", style="bold")
        t.append(f"{cell.label}", style="bold white")
        t.append(f" — {ev.detail or cell.problem}", style=colour if ev.status in ("bad", "warn") else "default")
        t.append(f"  [{cell.note}]", style="#555555")
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
    t.append(" (U undoes the newest)  " if lines else "  none yet — Enter on a cell switches it; E / Z / L / O change a voice; all with a backup\n", style="#7f8c8d")
    if lines:
        t.append("\n" + "\n".join(lines), style="#bbbbbb")
    if watcher_lines:
        t.append("\nPut back by the watcher (W pauses it)\n", style="bold #9b59b6")
        t.append("\n".join(watcher_lines), style="#bbbbbb")
    return t
