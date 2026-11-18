# Report: the full voice list for every agent (Phases A and B)

Written by Builder, 2026-11-18, card t_134ade35. Brief: `2026-11-18-acd-full-voice-list.md` (Peter: "yes" to
finishing the full voice list for every agent), with decisions 1-10 and 14 of `2026-11-13-voice-desk-command-center.md`.
Branch `builder/t_134ade35` (worktree `.worktrees/t_134ade35`), NOT merged, NOT pushed. Phase C (Hermes terminal
windows reread voice on/off) is the separate card t_9833bb53 and was not touched.

## In one paragraph

Every agent's voice menu in the Agent Control Desk now lists the whole voice bank: local Kokoro voices first,
Microsoft below, every English accent, "silent", and "other, type it". Simba sees it too. Nothing changed by
itself: every agent still has exactly the voice, effect, speed and on/off it had this morning (proved on a copy of
agents.toml, old code against new). A voice an agent's own script cannot play yet is shown with the words "not
playable yet" and cannot be picked or typed. Phase B switched the agents' own speaking scripts over so they can play
any bank voice through one shared speaker, and took the backup voices out of Claude's hook: a voice that fails is
now silent everywhere, never a second voice. The outside scripts are switched already (backed up, smoke-tested);
the shared speaker itself arrives with the merge, and at that moment "not playable yet" disappears by itself.

## What changed

Phase A (the list), in this project:
- `acd_edit.voice_options` / `acd.py`: the menu is the whole bank (it used to be Kokoro + American + British only,
  with unplayable voices hidden). Unplayable voices are shown, greyed, "not playable yet: <why>". A new last line
  "other, type it..." asks for a name and gives it the same check as a pick, then the usual yes / no question.
- A voice cell whose current voice cannot be played says so in red ("<voice>, not playable yet"). Today no agent is
  in that state; it only appears if a voice is set behind the desk's back.
- Simba: any English bank voice; a Microsoft voice line says "her sound effect does not apply to this voice"; the
  effect menu and cell say the effect works on Kokoro voices only, and the cell shows "(does not apply)" when her
  voice is a Microsoft one. Her current kitty voice (Fenrir, sample 16 pitch 1.4) is unchanged.
- `voicescripts.speaker_problem` decides "playable" by reading the agent's own script for a switch mark and
  checking that the shared speaker is in place. `voicepick`, `voiceedit` (typed names) and the cells all ask it.
- `voicecfg`: a Kokoro voice is written into Gemini's voice file only once Gemini's hook can play it.
- Builder is not added anywhere.

Phase B (the scripts):
- `speak_any.py`, the shared speaker: makes exactly one bank voice into a file (Kokoro by the shared Kokoro
  script, Microsoft by name, two tries of the same voice), or nothing. Never plays, never a second voice.
- `simba_tts.py`: British Kokoro voices (with British pronunciation and her effect) and Microsoft voices (plain,
  through the shared speaker) are accepted; still English only.
- Outside scripts, switched by `python -m voicescripts install` (anchored edits; backup; fake-driven smoke test;
  automatic put-back on failure):

  | Script | Before | Now |
  |---|---|---|
  | Gemini's hook `~/.gemini/config/hooks/speak-response.py` | Microsoft only; on failure the Mac voice (Samantha / Meijia) | Microsoft as before; a Kokoro voice through the shared speaker; on failure silent |
  | Jarvis `~/.hermes/profiles/orchestrator/scripts/tts_jarvis_local.sh` | a Microsoft name was turned into am_puck | his Kokoro voices as before; any other voice through the shared speaker at his speed; on failure silent |
  | Esther `~/.hermes/profiles/tax_angel_esther/scripts/esther_tts.py` | a voice not in her table was turned into alloy | her own voices (alloy, the blends) exactly as before; any other through the shared speaker; on failure silent |
  | Claude `~/.claude/hooks/speak-natural.py` | Microsoft failing -> Kokoro af_heart -> Mac voice | backup step removed only (no re-route): on failure the rest is silent |
  | Claude `~/.claude/hooks/kokoro_speak.py` | Kokoro failing -> Mac voice | silent, exit 3 |

## What is live now, and what waits for the merge

- Live since 12:10 today: the five outside script edits above. For every agent's CURRENT voice nothing sounds
  different (their own path is untouched and was rendered silently, below). What differs: a voice that fails is now
  silent instead of falling back to the Mac voice (Gemini, Claude) or to Kokoro af_heart (Claude).
- Waits for the merge to main: the new desk menus, Simba's wider script, and the shared speaker itself
  (`~/GitHub/Agent_Control_Desk/speak_any.py`). Until then a switched script asked for a voice it does not make stays
  silent, and the desk keeps those voices "not playable yet" (it checks that the shared speaker is in place), so the
  list is honest in between. After the merge: Gemini, Jarvis, Esther and Simba can take any bank voice.

## Proof

1. Tests (fakes only, nothing played): full suite `python -m unittest discover -s tests -t .`: 764 tests, all pass
   except `test_the_real_agents_toml_entry`, which fails only inside a worktree (it compares agents.toml's Simba
   path with the repo folder; same failure before this change); 3 skipped (two of them are the Gemini hook's old
   Mac-voice-fallback tests, which now run only on a hook that is not switched). New: `tests/test_voicescripts.py`
   (switches COPIES of the five real outside scripts as they were before the switch, smoke-tests them, restores every
   byte; checks that every smoke test catches the OLD scripts; re-runs the smoke test on the live switched scripts,
   read-only; install rules on stand-in files; the shared speaker with fake engines), and four new tests of the
   switched Gemini hook in `tests/test_speak_hooks.py`. Script-style suites run by
   hand: `tests/test_speechq.py` ALL PASS, `tests/test_listener.py` ALL PASS, `tests/test_voicecfg.py` 4 FAIL, the
   same 4 as on main before this change (Kylie's and Esther's live silence settings differ from the committed
   agents.toml; the other session's uncommitted agents.toml is probably about exactly that). Not caused here.
2. Nothing changes by default: `tools/acd_voice_list_proof.py --old <checkout of main>` on a copy of the live
   agents.toml: every agent's voice, Mandarin voice, effect, speed and on/off settings, the voice Voice Desk would
   speak with, and everything a sync would write: old code == new code. The copy and the live file are byte for
   byte unchanged after the headless desk ran. No agent's current voice shows "not playable yet".
3. Headless desk (no window, never armed): every voice menu opened and counted. Before the merge (now): Jarvis
   and Esther 46 Microsoft lines "not playable yet", Gemini 28 Kokoro lines, Simba 54; Kylie, Wanna Buffet, Claude,
   Codex 0. With the shared speaker in place (merge simulated): Jarvis, Esther, Gemini 0. Simba opens once the merge
   puts her new script in place. The typed-name line reaches its yes / no question (left unanswered).
4. Silent renders (`tools/silent_voice_render.py`, files only, ffprobe + the local whisper transcript; the sentence
   "Hello Peter, this is a quiet voice check for the full voice list." came back word for word every time):
   - today's voices: Jarvis am_puck (his switched script), Simba am_fenrir with her effect, Gemini Aria (its hook's own
     Microsoft call), Claude af_heart, Kylie Ava, Wanna Buffet am_santa, Codex Ryan: all PASS;
   - new voices through the switched scripts and the shared speaker: Jarvis Ava (at 1.2x), Esther am_adam and Ryan,
     Gemini bm_george, Simba Ava (plain) and bf_emma (with effect): all PASS;
   - a voice that does not exist (Jarvis, Esther): exit 1, nothing written: PASS (silent, no other voice).
5. Install log: all five outside scripts "switched and smoke-tested"; `voicescripts status` reads them as switched.

## How to undo the outside edits

    cd ~/GitHub/Agent_Control_Desk && .venv/bin/python -m voicescripts restore ~/.hermes/profile-backups/20261118-121053-voice-scripts

(also written in that folder's RESTORE.md, with one `cp -p` line per file). It puts a file back only if it is still
exactly what the install wrote; a file changed since is named and left alone. The backups are the files as they
were at 12:10 today.

## What Peter would hear differently (for the one listener-restart question)

Nothing for any agent's current voice. The listener does not use any changed module (it runs Simba's script fresh
for each line), so this phase needs no listener restart; the Agent Control Desk shows the new menus the next time it
is opened from main. The one audible difference is already live: when a voice fails, Gemini and Claude are now
silent instead of speaking in the Mac's own voice (or Claude in Kokoro Heart).

## Not verified / open

- Kylie's hands-on look (skill kylie-ui-test) has not happened: the headless pictures are attached to the card.
- Esther's own voice (alloy) was not rendered: her script starts her warm voice service in the background when it is
  not running, which is a live process. Her own path is unchanged code (the edit only adds a check in front of it,
  and the smoke test proves alloy, heart and her blends stay on it).
- Gemini's own Microsoft call depends on the edge-tts in the user's Python folder; it worked in the render.
- After the merge, re-run `tools/acd_voice_list_proof.py` from main: it should show 0 "not playable yet" for
  Jarvis, Esther, Gemini and Simba.
- Pre-existing, not changed here: `docs/where-every-voice-setting-lives.md` still names the Telegram team group's
  chat number in its team-group row (public repo). Flagged for whoever owns the Telegram docs.
- The smoke test once imported the Gemini backup copy, which left a `__pycache__` folder inside the backup folder
  (harmless; later runs do not write it).

## Commits (branch builder/t_134ade35)

See `git log main..builder/t_134ade35`.

# Phase C: Hermes terminal windows switch voice on and off from the desk

Written by Builder, 2026-11-18, card t_9833bb53. Branch `builder/t_9833bb53` (worktree `.worktrees/t_9833bb53`),
NOT merged, NOT pushed.

## In one paragraph

The "Speaks in terminal window" cell of every Hermes agent (Jarvis, Kylie, Esther, Wanna Buffet, Simba) can now be
switched Off or Always from the desk, like Claude's and Gemini's. The desk writes one small file in that agent's
own profile folder; every open window of that agent looks at it before each reply and acts only when it changed.
Off: the window stops reading replies aloud (its mic and Ctrl+B are left alone, so Voice Desk can still hand it
spoken words). Always: the window turns voice on and reads replies aloud. Nothing changes by default: no such file
exists today, and without it every window does exactly what it did before. No gateway restart is needed, ever: this
runs in the windows, not in the gateway, so no cell says "waiting for gateway restart". A window that was already
open before 12:48 today still runs the old code and follows the switch only after it is closed and opened again.

## What changed

In Hermes (a local patch, live on disk since 12:48; backup first in
`~/.hermes/backups/local-patches/pre-terminal-voice-switch-20261118-124613/`):
- new `hermes_cli/terminal_voice_switch.py`: reads `<profile home>/terminal_voice.json` (`{"voice": "on"}` or
  `{"voice": "off"}`); acts only on a change; file removed = back to what the window started with
  (`voice.auto_enable`); unreadable file or another value = nothing happens; headless runs (job cards, one-shot,
  pipes) never act; every error is swallowed, so it can never break a turn. A typed `/voice off` or `/voice tts` in
  the window still wins until the desk's value changes again.
- two hook lines: at window start (`cli_tui_mixin._tui_wake_startup`, after `voice.auto_enable`) and at the start of
  every turn (`cli_chat_turn_mixin._chat_setup_turn_audio`, before the speech decision).
- new test `tests/hermes_cli/test_terminal_voice_switch.py` (17 checks).
- the patch manifest lists it as `terminal-voice-switch` (runs in: windows), diff
  `terminal-voice-switch-20261118.diff` (applies cleanly on top of the earlier patches; checked on a copy).
  `hermes_patches.py check` says 10/10 in; `apply` would put it back after a Hermes update.

In this project:
- `gridstate.terminal_cell`: a Hermes agent's cell reads the switch file first (Off / Always, red when the file is
  unreadable or when the Hermes fix is missing), else the old `voice.auto_enable` reading.
- `onoff_hermes`: the Off / Always menu and the plan (the same backup, read-back, history and U-undo as every other
  switch; the first switch makes the file, its undo removes it again; a file that cannot be read is never
  overwritten). `onoff`, `acd_edit` and `watcher` use it, so the desk, the old grid and the watcher agree.
- `hermes_patches.terminal_voice_switch`: is the fix in, and since when.
- `tools/acd_voice_list_proof.py` now also reads every agent's terminal cell and opens its menu (never picks).

## Proof

1. Real program, silent: a real `hermes -p builder` window (Builder's own profile, never a live window) in a
   pseudo-terminal, with stand-in `afplay` / `ffplay` first on its PATH that write to a log instead of playing.
   Switch "on" before start: the window turned voice on and said so. Switch flipped to "off", one short message:
   the window said spoken replies are off, the reply came, the stand-in player was called 0 times. Switch flipped
   back to "on", one short message: the window said spoken replies are on, and the reply's audio went to the
   stand-in player once. Nothing reached the speakers. The switch file was removed afterwards.
2. Tests: Hermes `test_terminal_voice_switch.py` 17 pass; with the other voice test files 199 pass, 2 fail and 4
   error in `test_voice_mic_recovery.py` / `test_voice_mode.py` (a test guard that refuses the real Hermes home when
   that file imports the CLI alone, a timing test, and a beep test; they test `tools/voice_mode.py`, which this
   patch does not touch). Desk: full suite 775 tests, all pass except `test_the_real_agents_toml_entry` (fails only
   inside a worktree, as before), 3 skipped; `tests/test_voicecfg.py` by hand: the same 4 FAIL lines as a clean copy
   of main (live silence settings, not this change); `tests/test_listener.py` and `tests/test_speechq.py` ALL PASS.
   The five tests that said "a Hermes window cannot be switched" now say it can; new tests in
   `tests/test_onoff_hermes.py` (HermesTerminalSwitch) and `test_acd_v4`.
3. Nothing changes by default: `tools/acd_voice_list_proof.py --old <main>` on a copy of the live agents.toml:
   old == new, the copy byte for byte unchanged. Every agent's five "speaks in" cells read by the old and the new
   code: the same value and the same warning for every agent and place; only the Hermes terminal note's wording
   differs. No `terminal_voice.json` exists in any profile.
4. Headless desk on the copy: every Hermes agent's terminal cell offers Off / Always (Builder's column has no voice
   and no switch); picture `menu_kylie_terminal.svg`.

## How to undo the Hermes patch

    cd ~/.hermes/hermes-agent && git apply -R ~/.hermes/backups/local-patches/terminal-voice-switch-20261118.diff
    cp -p ~/.hermes/backups/local-patches/manifest.json.bak-20261118-terminal-voice-switch \
          ~/.hermes/backups/local-patches/manifest.json

(`git apply -R` takes out the two hook lines and removes the two new files.) Any switch made from the desk is undone
with U, which removes the file again.

## Not verified / open

- Not tried on Peter's open windows (that would touch a live window). They follow the switch only after they are
  reopened once; no window or service was restarted.
- Kylie's hands-on look (skill kylie-ui-test) has not happened.
- The first real-window run met Hermes' one-time "help improve Hermes?" question in Builder's profile; the run's
  Enter chose the default "No thanks" (shared metrics off) in Builder's own settings.
