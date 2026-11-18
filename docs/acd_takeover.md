# Taking over from the Voice Desk dashboard

Written with version four of the Agent Control Desk (branch `acd/v4`, 17 November 2026). It says what the old
Voice Desk dashboard did, what the desk now does in its place, what still lives only in the old one, what
points at the old dashboard today, and what has to change to make the desk the default.

Nothing in this note has been done. Version four only adds files beside the old dashboard.

**Update 18 November 2026:** the takeover is done. The desk gained the listener controls (`l`), the here / not
here marks and activation phrases (X / P), and, last, the other languages (L / O: the Chinese voice menu's
"other languages..." line) and quiet hours per place (shown on the "Speaks in ..." cells). `dashboard.py` and
`dashboard.sh` were removed from the project, the Monitors pane opens `./acd.sh --armed`, and the words listed
below (read-me, the two install scripts, `app.py`) now point at the desk. The rest of this note is kept as written
on 17 November.

## The short answer

- The desk now covers the old dashboard's **main screen**: every voice switch, the voice edits, the silent
  voice test, the health strip, the watcher's pause key, and what the logs say really happened.
- The old dashboard's **second screen**, the listener controls (mute, hush, new conversation, hand the mic
  back, restart, kill), is **not** in the desk. The desk deliberately keeps its hands off the listener.
- So the desk can become the default screen, but `dashboard.py` and `dashboard.sh` must stay in the project
  until the listener controls have a home.
- **No restart of the voice listener or the receiver is needed** for the takeover. See the last section.

## What the old dashboard did, and where it is now

| Old dashboard | In the desk? | Where, or why not |
|---|---|---|
| Grid: one row per agent, one column per place, each cell Off / Voice-back / Always | Yes | Each agent's tree, under Voice: "Spoken replies, place by place". Claude, Gemini and Codex: the A key on the grid. |
| Under each cell, what the logs say really happened (spoke, idle, mixed, mismatch) | Yes | The same lines, read with the old grid's own reader (`gridevidence.py`), shown beside each place with the same colours. |
| Switch: Telegram team group voice notes (Off / Always) | Yes | Enter on that place. The old switch code (`onoff.set_cell`) makes the change. |
| Switch: Telegram private chat voice (Off / Voice-back / Always) | Yes | Same. |
| Switch: Claude's own terminal voice (Off / Always) | Yes | A on the grid, then Claude. |
| Switch: Gemini's own terminal voice (Off / Always) | Yes | A on the grid, then Gemini. |
| Switch: the mic window's speech queue (on / off, one switch for all agents) | Yes | Enter on "Voice Desk mic window / phone" in any agent's tree. |
| Hermes windows and the iPhone: shown, never switched | Yes | Shown with the same reason. |
| E / Z: English and Mandarin voice pick-lists | Yes | Enter on English or Chinese in the tree (version two). |
| F: Simba's voice effect | Yes | Enter on "Effect" in Simba's tree. Only the named samples and starting points; a typed recipe ("23 but slower") is not offered. |
| T: silent voice test of one voice | Yes | T inside the voice pop-up: made to a file and checked, never played. |
| Shift-T: test everyone | **No** | Not carried over. Still in the old dashboard. |
| Top strip: voice engine, Hermes gateway, speech queue, Hermes fixes, watcher, Mac output | Yes | One line under the legend, read with the old strip's own code (`syshealth.py`). A red light also becomes a warning line. |
| W: pause / resume the Voice Desk watcher | Yes | P pauses the desk's own watcher and the Voice Desk's watcher together (armed desk only). |
| U: undo the last change | Yes | U. A voice switch made from the desk is undone with the old switch code's own undo. |
| Change history lines at the bottom | Partly | The desk keeps its own history (`acd_edit.py status` prints it). It is not drawn on the screen. |
| A: add an agent to the grid (writes a new block in the voice list) | **No** | A new Hermes agent shows up in the desk as a row by itself, without a voice. Giving it a voice block still needs the old dashboard. |
| D: remove an agent from the grid | **No** | Still in the old dashboard. |
| X: here / not here at a place | **No** | Stored-only wiring flags. Still in the old dashboard. |
| P: activation phrase of a place | **No** | Stored and shown only. Still in the old dashboard. (P in the desk is the watcher's pause key.) |
| L, O: extra languages, language override | **No** | Hidden in the old dashboard too: stored only, nothing enforces them. |
| Quiet hours | **No** | Stored and shown only in the old dashboard. |
| ? : what the colours mean | Yes | The legend line is always on the screen. |
| Detail pane for the selected cell | Partly | The tree shows the setting, the evidence line and why a place cannot be switched. The old pane's longer sentences are not copied. |
| Second screen: the listener (who is on the line, what was heard and said) | **No** | Not carried over, on purpose. |
| M mute, H hush | **No** | Listener controls. Still in the old dashboard. |
| N new conversation, X hand the mic back | **No** | Listener controls. Still in the old dashboard. |
| R restart the listener, K kill the listener | **No** | The desk never stops or restarts the listener. Still in the old dashboard. |

What the desk has that the old dashboard never had: the language model and its backups, sign-in and allowance
of each service, where an agent shows up, who it may talk to, the Team Room switch, the watcher for settings
changed behind the desk's back, and the dry-run rule (nothing is written unless the desk is armed).

## What points at the old dashboard today

Found by reading, nothing was changed.

- `~/.config/herdr/dashboards.manifest`, line 24:
  `Monitors/Ops      <desk folder> | wX:pG | <desk folder> | ./dashboard.sh`
  (the real line spelled the home folder out in full; it now opens `~/GitHub/Agent_Control_Desk` with
  `./acd.sh --armed`)
  This is the line that opens the old dashboard in the Monitors workspace after a restart.
- `~/.config/herdr/dashboards-desktop.manifest` has no Voice Desk line.
- `dashboard.sh` in the project runs `dashboard.py`.
- No launch job runs the dashboard. The two launch jobs of the project are the listener
  (`com.peter.voice-desk-listener`) and the receiver (`com.peter.voice-desk-receiver`); neither starts or
  needs a dashboard.
- Words only: `README.md` (the "small control panel" lines and the voice-grid section), `app.py` line 140 and
  `install_listener.sh` (both say "./dashboard.sh shows it"), `install_watcher.sh` ("W in ./dashboard.sh"),
  and the agents' own notes under `~/.hermes/profiles/orchestrator/skills/voice_desk/` and
  `.../hermes-config-ops/hermes-voice-mode-tuning/`.

## What must change to make the desk the default

1. Merge `acd/v4` into the project's main folder (the desk's files are all new files, plus nothing changed in
   the old ones).
2. Change the one manifest line so the Monitors pane starts `./acd.sh` instead of `./dashboard.sh`. Keep a
   dated copy of the manifest first.
3. Decide where the listener controls live. Until then, keep `dashboard.sh` and say in the read-me that it is
   now "the listener screen". Two ways out, neither built: add a listener screen to the desk, or cut the old
   dashboard down to its second screen.
4. Decide about arming. The desk opens in dry run. To let it write: `.venv/bin/python acd_edit.py arm`, and the
   pane's line becomes `./acd.sh --armed`. Automatic put-back is a third, separate switch
   (`acd_watch.py auto on`).
5. Update the words listed above (read-me, the two install scripts, `app.py` line 140, the agents' notes).
6. Optional: the Voice Desk watcher's own check job is not loaded on this Mac today (the health strip says
   "watcher not running"). `./install_watcher.sh` loads it. That is separate from the desk and was not touched.

## Does any of this need the voice listener or the receiver restarted?

**No.**

- Version four adds new files only: `acd_health.py`, `acd_watch.py`, a new test file and this note, plus
  changes to the desk's own files from versions one to three. No file the listener or the receiver loads was
  changed (`listener.py`, `receiver.py`, `engine.py`, `audio.py`, `tts.py`, `voices.py`, `settings.py`,
  `agents.py` and the rest are byte for byte what they were).
- The desk imports the old modules (`onoff`, `gridstate`, `gridevidence`, `syshealth`, `watcher`,
  `voicecfg`, `voiceedit`) in its own process. That does not touch the running listener.
- The voice switches write the same small files the old dashboard wrote, and those are re-read before every
  spoken line, so they never needed a listener restart.
- Changing the manifest line only changes which program the Monitors pane starts.

One thing that does need a restart of the agents' system, and only in one case: a Telegram private chat's voice
switch is live at once only when the running agents' system has the live-reload fix loaded. The switch's own
result says which case it is, as the old dashboard did.

## What was not proven

- Nothing here has run in a real window. Every check was drawn without one.
- The armed path of the voice switches was run only against a throw-away copy of the files.
- The allowance comes from Hermes' own `hermes usage --json`, run at most once per service every ten minutes.
  That is a status check, not a model call, but it does go out to the service.
