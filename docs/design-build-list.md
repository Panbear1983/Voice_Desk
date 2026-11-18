# Brief: Voice Desk dashboard iteration (no fallback, pick-list, add agent, wiring grid, footer)

Written by Claude Code 2026-11-11. THIS FILE IS THE ONLY SOURCE; cards carry a short pointer to it.
HOLD (Peter 2026-11-11): nothing here is built until the weekly Claude limit refreshes
(Fri 2026-11-13 about 17:00 Taipei) and Jarvis has checked the quota (weekly was 6% left on
2026-11-11). Do not use the Antigravity fallback to get around it. Release automatically then.

Peter's instruction: "build it ... to see it. And do it, corrections later." So build all five
parts in order, show him the result, and expect corrections after. Repo: ~/GitHub/Agent_Control_Desk
(Textual TUI, `./dashboard.sh`). Raw notes behind this: `docs/build-iteration-plan.md`.

## Facts already checked (do not redo)

- The grid is built only from hand-written `[agents.*]` blocks in `agents.toml` (`agents.agents()`).
  Hermes profiles on disk (assistant, builder, orchestrator, default) are not on the grid.
- The places (columns) are a fixed list in `gridstate.PLACES`: terminal, tg_dm, tg_group, mic, iphone.
- The E / Z voice popup is `AskScreen` in `dashboard.py` (plain typing box, only validates). A pick-list
  already exists: `ChoiceScreen`, used for the on/off switches. Reuse it.
- `voices.voice_for()` gives any agent with no voice the shared voice (`[shared] voice`, Jenny).
  `voicecfg.gemini_wants()` also falls back to Jenny. Only jarvis, kylie, esther, wanna_buffet and simba
  have `no_fallback = true` today.
- Gemini's own hook speaks Edge (Microsoft) voices only; Codex has no hook, speaks through Voice Desk.
- Footer keys today, grid: T test, A test all, C check now, E English, Z Mandarin, L extra language,
  O language override, F effect, U undo, W watcher, S listener screen. Listener screen: N, V, X, R, K, Esc.

## Part 1. One voice, no fallback, empty = silent

Peter: "If the primary voice ... did not work ... it's just gonna stay silent, so that there is no 2nd
voice automatically switched on." And: agents with no voice "can just be empty ... such as builder."
- Every agent has exactly one voice per language. If it fails, that line is silent. No second Edge voice,
  no Piper, no Mac voice, anywhere (Voice Desk, `tts.py`, the Hermes voice scripts, Claude's and Gemini's
  hooks where Voice Desk writes their config).
- No voice assigned = silent. Remove the "voiceless agent gets the shared voice" rule.
- Remove `[shared] voice` / `voice_zh` and the `fallback` field from `agents.toml` and from the E/Z/F screens.
  Remove Jenny defaults from `voicecfg.py`. Fix the stale dashboard label "Jenny / HsiaoChen (hard-coded)".
- The V key (shared-voice mode) goes away with the shared voice.
- A failed voice shows on the Voice check as a failure (red), not as a quiet switch to another voice.

## Part 2. Voice pick-list

E and Z (and F on Simba) open a short list of working voices, current one marked, arrows + Enter.
Last line: "other, type it" (the old typing box). Mandarin list is Taiwan voices; Simba's list is American
Kokoro voices only (her `english_only` rule stays). Same backup, read-back and undo U as every edit.

## Part 3. Add / remove an agent from the grid

- A key: pick-list of Hermes profiles found on disk not yet on the grid, plus "other CLI agent". Ask display
  name, spoken name(s), voice (pick-list) or "silent". Write a new `[agents.<key>]` block with backup,
  read-back, undo. Telegram chat ids are NOT used to add an agent; the profile knows its own bot.
- D key: remove an agent from the grid only. Never delete the profile or any file but the settings block
  (and that is backed up and undoable).
- Test all moves from A to Shift-T.

## Part 4. Grid as a wiring matrix

Peter: columns "can also be toggle on or off, which means certain agent ... does not have to be up here at
certain channel." Each cell (agent x place) gets a here / not-here toggle, separate from the voice on/off
switch. Not here = blank cell. Here = shown, and its colour is the Voice check's honest answer: green only
when really wired and working; switched on but not wired = shown as unfinished, never green. Each "here"
cell holds an activation phrase (iPhone: Siri shortcut name; mic: "hello Jarvis"; Telegram: bot/group),
editable by a key. A toggle cannot create wiring (a Siri shortcut must be imported by Peter;
`make_shortcut.py` can generate the file): say so on the cell. The list of places stays fixed in code.
Store the here/not-here flags and phrases in `agents.toml` per agent so they survive a restart.

## Part 5. Footer keys

- Remove: V (shared voice gone). Hide: L and O (stored only, nothing enforces them). F only on Simba's row.
- Keep: T, C, U, W, S, M, H, Q (and E / Z with the pick-list).
- Add: A add agent, D remove from grid, the here/not-here toggle, the activation-phrase edit, "?" help line
  explaining the colours. Footer shows only keys that apply to the selected row or cell.

## Done means

1. Dry runs with fakes (nothing played, nothing sent): a failing primary voice stays silent; a voiceless
   agent is silent; the V key and shared voice are gone; Jenny appears nowhere in the grid or defaults.
2. Pick-list, add agent, remove agent, here/not-here toggle and footer all work in the real TUI, driven
   headless (Textual pilot / screenshot), and every change undoes with U.
3. `tests/` pass, including the script-style `test_voicecfg.py` (discover skips it; run it by hand).
4. A screenshot of the grid and of each new popup for Peter, and a plain-English note of what changed.

## Must not

- Overwrite the uncommitted live edits in `agents.py`, `agents.toml`, `panes.py`, `tts.py`, `voices.py`
  (today's no-fallback work, Peter's). Build on top; keep them.
- Stop, unload or restart the voice-desk listener or the Hermes gateway without Peter's OK.
- Let any test touch a live window (package-level fakes only).
- Delete any profile, key, voice file or user data. Commit anything from `~/.voice_desk/`.
- Push to GitHub by hand (the daily routine publishes commits).
