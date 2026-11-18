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
  (`~/GitHub/Voice_Desk/speak_any.py`). Until then a switched script asked for a voice it does not make stays
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

    cd ~/GitHub/Voice_Desk && .venv/bin/python -m voicescripts restore ~/.hermes/profile-backups/20261118-121053-voice-scripts

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
