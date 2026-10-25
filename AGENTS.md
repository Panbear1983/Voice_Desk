# Voice_Desk — agent rules

Memory wing: `voice_desk` (notes from before the 2026-10-15 rename sit under `claude_voice`) (shared mempalace, protocol at `~/.mempalace/MEMORY_PROTOCOL.md`).
Search that wing before planning; file one note under it when you finish.

What this is: a hands-free voice loop with Claude Code on Peter's Mac. Mic open -> a pause of
1.5 s sends -> whisper-server (small model, kept loaded) -> `claude -p --resume` headless thread
-> spoken reply via Hermes' voice chain (Jenny -> Piper -> Mac voice; Meijia for Mandarin) ->
mic reopens. Window = `app.py` (Textual 7.5). Phone door = `receiver.py` + an iOS Shortcut.
Renamed from Claude_Voice to Voice_Desk on 2026-10-15 (Peter's pick); state dir `~/.voice_desk`
(auto-moved from `~/.claude_voice`), env prefix `VOICE_DESK_`, hook guard `VOICE_DESK_LOOP=1`.
Since 2026-10-15 evening `brain.Brain` is a router: "claude" (ClaudeBrain, `claude -p --resume`),
"gemini" (GeminiBrain, `agy -p … --conversation <id>` JSON; Google's Antigravity CLI, the old gemini CLI is dead for individuals) or
"hermes:<profile>" (HermesBrain, `hermes -p <profile> -z … --resume <id>` with HERMES_HOME set; the new
session id is read from the profile's state.db because one-shot prints none). Sticky target in
`~/.voice_desk/brain` ("talk to Esther"); the phone passes `to` per request, one Shortcut per brain.
Codex is optional: `codex_brain.CodexBrain` runs `codex exec --json`, then resumes the exact
saved UUID with `codex exec resume`. Its ID is `~/.voice_desk/codex_session`, separate from
Claude/Gemini/Hermes and any interactive Codex window. Use the local user's Codex login and
configured model; never embed an author's credentials, paths or session UUID in the adapter.
Keep workspace-write sandboxing, bounded cancellation/timeouts and no automatic replay of
failed turns. `tests/test_codex.py` is offline and uses a fake executable; no model tokens.

Live windows (2026-10-25, Peter: "talk to Claude ... activate this particular session I'm talking
to you right now", then "do the same for Gemini and Codex"): `live_session.LiveBrain(target)` types
the words, prefixed `[voice] `, into the herdr pane running that agent (herdr reports agent +
session id per pane; a pin in `~/.voice_desk/<target>_live_session` picks between several, else the
only open window is used), waits 0.6 s before Enter (Codex swallows an Enter sent with the paste)
and presses Enter once more if nothing arrived after 5 s. It follows that agent's own transcript
(Claude ~/.claude/projects/*/<id>.jsonl, Gemini ~/.gemini/antigravity-cli/brain/<id>/.system_generated
/logs/transcript.jsonl, Codex ~/.codex/sessions/*/*/*/rollout-*-<id>.jsonl task_complete) and returns
the last text before any Stop-hook feedback. `Brain.backend()` prefers the live window, else the
background brain. `~/.voice_desk/live_turn` exists during the turn; Claude's speak-response.sh and
Gemini's ~/.gemini/config/hooks/speak-response.py stay silent on it (Gemini's also on
VOICE_DESK_LOOP=1). `tests/test_live_session.py` is offline (fake herdr + fake transcripts).
Hermes characters too (Peter: "yes, switch it on for all five"): `live_session.HermesLiveBrain(profile)`
finds the window by its title "Hermes — <profile>" (herdr's agent field is unreliable: the
Orchestrator reports as "agy"), types the same way, and reads the answer from
`~/.hermes/profiles/<profile>/state.db` messages (assistant row with no tool calls after Peter's
words). herdr's Hermes plugin is enabled in all five profiles (symlink in <profile>/plugins +
`hermes plugins enable herdr-agent-state`). Hermes windows read answers aloud in voice mode; a
LOCAL PATCH in ~/.hermes/hermes-agent/hermes_cli/cli_voice_mixin.py (`_voice_speak_response_async`)
skips that while ~/.voice_desk/live_turn is fresh. A Hermes update can overwrite it: re-apply.

Standing rules:
- Peter never types in the window and never presses record or play. Keep it that way.
- Settings are a handful of presets in `settings.py`, not a config page.
- The headless thread sets `VOICE_DESK_LOOP=1`; the speak-response and mempalace Stop hooks
  exit early on it. Do not remove that guard or every reply is spoken twice.
- Do not launch the window for Peter; he double-clicks `start.sh` himself.
- Never commit anything from `~/.voice_desk/` (session id, receiver token, transcript).
