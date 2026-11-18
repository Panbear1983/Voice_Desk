# Voice Desk owns every agent's voice settings: plan

Status: PLAN ONLY. Nothing is built yet. Written 2026-10-22 at Peter's request:
"can all those that are like store inside of the Hermes profile like the voice type and all that
be engineered into the voice desk as well so that the voice desk has sort of holds more control
over the audio aspect of all the Hermes profiles and also just any voice control related features."

Goal: one file in the Agent_Control_Desk repo is the single place to change anything voice-related for the
four Hermes agents (Jarvis, Kylie, Esther, Wanna Buffet). Voice Desk writes those values into each
Hermes profile, so the agents, the listener and Telegram all stay in step.

---

## 1. Where voice settings live today (verified 2026-10-22)

| Setting | Lives in | Used by |
|---|---|---|
| Wake names and misheard spellings | `Agent_Control_Desk/settings.py` NAMES (line ~110) | listener |
| Greeting words (hi, hey, hello...) | `Agent_Control_Desk/brain.py` `_GREET` (uncommitted edit) | listener |
| Whisper hint words | `Agent_Control_Desk/settings.py` WHISPER_PROMPT | listener |
| Mic sensitivity (noise floor, min speech length) | `Agent_Control_Desk/settings.py` lines 20-35 | listener |
| Each agent's reply voice | `~/.hermes/profiles/<p>/config.yaml` `tts:` block | agent window AND its Telegram voice bubbles |
| End phrases (66 variants, copied 4 times) | each profile `voice.stop_phrases` | agent window; listener reads them to know the mic came back |
| Voice-mode knobs (auto_tts, silence_duration, silence_threshold, barge_in) | each profile `voice:` block | agent window |
| Voice Desk's own copy of the voice | `Agent_Control_Desk/voices.py` reads the profile at run time | listener's own spoken lines |
| Claude Code's voice | `~/GitHub/Claude_Voice_Defaults` | Claude only |

Current voices (from `voices.table()`): Jarvis en-US-JennyNeural, Kylie en-US-AvaNeural,
Wanna Buffet en-US-AndrewNeural, Esther "alloy" through her own Kokoro script
(`tax_angel_esther/scripts/esther_tts.py`). Voice Desk substitutes en-US-AriaNeural for Esther
because Kokoro is not an Edge voice.

Each profile's `tts.provider` points at a different script (jenny_only, natural/tts_ava.sh,
esther, natural/tts_wanna_buffet.sh). The scripts stay where they are. Only the values change.

## 2. Proposed design

1. New file `Agent_Control_Desk/agents.toml` (read with Python's built-in `tomllib`; the venv has no YAML
   library and must not need one). One block per agent:
   ```toml
   [agents.jarvis]
   profile      = "orchestrator"
   names        = ["jarvis", "javis", "jaarvis", "javas", "jervis", "jarvas", "orchestrator"]
   voice_en     = "en-US-JennyNeural"
   voice_zh     = "zh-TW-HsiaoChenNeural"
   speed        = 1.0
   silence_duration  = 1.5
   silence_threshold = 200
   barge_in     = false
   auto_tts     = true

   [agents.esther]
   profile      = "tax_angel_esther"
   voice_profile_native = "alloy"        # Kokoro voice written into her profile
   voice_en     = "en-US-AriaNeural"     # what Voice Desk itself speaks with
   ...

   [shared]
   greetings    = ["ok", "okay", "hey", "hay", "hi", "high", "hai", "hello", ...]
   end_phrases  = ["that's all", "that is all", "no further questions", "that's all the questions i have"]
   end_lead_ins = ["", "oh", "okay", "ok", "thanks", "thank you"]
   ```
   The 66 end-phrase variants are generated from the short list (base x lead-in x apostrophe forms),
   not typed out.
2. `settings.NAMES`, `brain._GREET`, `voices.py` and the listener read `agents.toml` instead of their
   own constants. Behavior must not change on day one: the first version of `agents.toml` holds
   exactly today's values.
3. New command `python -m voicecfg` in Agent_Control_Desk:
   - `check`: for each profile, shows what differs between `agents.toml` and its config.yaml (drift
     from hand edits). Read-only.
   - `sync --dry-run`: prints the exact line changes it would make to each config.yaml.
   - `sync --apply`: backs up each config.yaml to `~/.hermes/profile-backups/<date>-voicecfg/` and
     then writes the changes.
   Edits are targeted line replacements (the `voice:` line under the active tts provider, the
   `stop_phrases:` line and the four knob lines under top-level `voice:`), never a YAML re-dump, so
   comments, anchors and every other setting stay untouched. Same approach `voices.py` already uses
   to read the file without a YAML library.
4. Dashboard: one new read-only panel listing each agent's voice, and a warning when `check` finds
   drift. No editing from the dashboard in v1.

## 3. Steps (each one is small, tested, then committed)

1. Commit the two pending fixes first (greetings + short-utterance fix in `audio.py`, `brain.py`,
   `listener.py`, `tests/test_listener.py`) so this work starts from a clean tree.
2. Add `agents.toml` holding today's values exactly. Test: loading it gives the same names, greetings
   and voices as the current constants.
3. Switch `settings.py` NAMES and `brain.py` `_GREET` to read from it. Test: `tests/test_listener.py`
   and `tests/selftest.py --no-brain` stay ALL PASS.
4. Switch `voices.py` to read voices from `agents.toml` (the profile read stays as the `check`
   comparison). Test: `voices.table()` prints the same four voices as above.
5. Write the end-phrase generator. Test: it produces exactly the 66 strings now in the profiles.
6. Write `voicecfg check`. Test: on today's profiles it reports zero drift.
7. Write `voicecfg sync --dry-run` / `--apply` with backups. Test on copies of the four config.yaml
   files in the scratch dir: change Kylie's voice in a copy of agents.toml, run sync, confirm only
   that one line changed (`diff` against the backup), and that Hermes still parses the file
   (exact Hermes command to confirm from the hermes-agent skill before this step; not yet verified).
8. Live check, with Peter: change one voice, sync, open that agent's window, hear the new voice; send
   a Telegram message to that agent's bot and hear the same voice in the voice bubble.
9. Dashboard drift panel.
10. Update Agent_Control_Desk README and AGENTS.md; mempalace note under wing `voice_desk`.

## 4a. Peter's decisions (2026-10-22, by voice)

1. Telegram follows Voice Desk: "Yes indeed, so that it could be a bit more unified." One voice per
   agent everywhere.
2. Esther keeps her own Kokoro voice everywhere ("Yes it does"). Voice Desk speaks its own lines for
   her through her Kokoro script (`tax_angel_esther/scripts/esther_tts.py`), not Aria. This replaces
   the Aria fallback in `voices.py` (`_NON_EDGE_FALLBACK`).
3. Claude's voice moves into Voice Desk: "I hope it can actually be moved to voice desk as well."
   `agents.toml` gets a `[agents.claude]` block. `voicecfg sync` writes `~/.claude/voice-defaults.conf`
   (NEURAL_VOICE_EN, NEURAL_VOICE_ZH, VOICE_ALWAYS_ON, VOICE_MODE), which
   `~/.claude/hooks/speak-response.sh:64` already reads. The hooks keep working. The
   Claude_Voice_Defaults repo then holds only the hook scripts and their installer, and its README
   says the voices now come from Voice Desk.
4. Mic sensitivity moves into the same file ("Yes, yes please"). `[shared.mic]` holds settings.py
   lines 20-35 (PRE_ROLL_SEC, SPEECH_START_SEC, SILENCE_SEND_SEC, MIN_SPEECH_SEC,
   SPEECH_OVER_FLOOR, MIN_SPEECH_RMS, BARGE_IN_*). Values are unchanged at first.

Added steps: 4b, Esther's own lines through Kokoro (test: a wav is produced for a short line within
the timeout); 7b, sync writes `~/.claude/voice-defaults.conf` with a backup (test: a dry run of
speak-response.sh prints the voice from agents.toml).

## 4b. Next phase, requested 2026-10-22 ~15:20 (not built, not yet handed to the builder)

Peter: "I can see what's actually being written into the configuration into the dashboard, but I
cannot tweak it. I cannot edit based on each profile. So I need to have that feature in the
dashboard." And: "when I'm adding new profile in the future for Hermes, I also want to be able to
configure directly in here ... it has to be registered ... and waiting for [me] to type in the
activation message and the closing message as well."

A. Edit per profile in the dashboard: wake names (and misheard spellings), greetings, end phrases,
   voice. Every edit writes agents.toml, shows the `voicecfg sync --dry-run` diff, applies only on
   confirm (with backups), then offers the listener restart (Peter's permission rule stays).
B. New Hermes profile onboarding: the dashboard lists Hermes profiles under ~/.hermes/profiles that
   are not in agents.toml as "unregistered". Registering one asks for its wake name(s), end
   phrase(s) and voice, adds an [agents.<name>] block, and syncs. Open question: does a new agent
   also need its herdr window set up for the hand-off (panes.find)? Check before building.
Depends on the agents.toml / voicecfg build (t_9dddcaa5) being merged first.

## 4c. Phase 3, requested 2026-10-22 ~15:35 (discussion only, not built; start after phase 2 merges)

Peter: "while one agent is in the process of responding, I should be able to activate another agent
by saying hey ... whoever ... and then continue to have another conversation ... to prevent any
response in overlap, these response will line up one after another, make sure that all the
responses are being all spoken out. Before the next response comes in, it's kind of like train
coming arriving to the train station."

Today: once handed to an agent's window the listener is deaf (listener.py deaf(), handed), and each
Hermes window speaks through its own voice mode, so there is no shared queue across agents. The
only lock is listener.py:119 _speak_lock (within the listener). ~/.voice_desk/allow_overlap exists
but no Python code reads it (check what does before reusing the name).

Needed:
A. While an agent is thinking (not speaking, not listening), the listener listens again for
   greeting + name only, and hands Peter to the second agent.
B. One shared "platform" for speech: every agent's reply waits for the speaker to be free and plays
   whole, in the order replies arrive. Probably a lock/queue file every speaker (Hermes voice mode,
   Voice Desk tts, Claude's speak-response.sh) takes before playing.
Open: can Hermes voice mode be told to wait for a shared lock (Hermes code, not this repo)? Does
the agent's mic stay open while it thinks (then both would hear the second "Hey <name>")?

Peter's decisions (2026-10-22 ~15:45, approved for build: "Perfect, all right, let's make that into the build"):
C. A queued reply plays only in a pause (never cuts into Peter talking or another reply), opens
   with the agent's name ("Kylie here"), and plays whole.
D. The mic goes to whoever spoke last. Earlier agents keep their whole conversation in their own
   windows; Peter goes back by saying the name again ("Hey Kylie"). ~20 s of silence returns the
   listener to waiting for a name, as today.

## 4. Decisions needed from Peter before building (answered in 4a)

1. Telegram: should a voice changed in Voice Desk also change that agent's Telegram voice bubbles?
   (Default in this plan: yes, one voice everywhere, since the profile setting serves both. Peter
   asked on 2026-10-21 for agents to sound "exactly as on Telegram".) The alternative is a separate
   Voice-Desk-only voice, which means the two can sound different.
2. Esther: keep her Kokoro voice in her windows and Telegram, and Aria only for Voice Desk's own
   lines? Or move her to an Edge voice so she sounds the same everywhere?
3. Claude Code's voice (Claude_Voice_Defaults repo): fold it into `agents.toml` too, or leave it
   separate? (Default: leave it separate in v1.)
4. Mic sensitivity settings: move them into `agents.toml` `[shared]` too, so one file covers
   "any voice control related features"? (Default: yes, values unchanged.)

## 5. Risks

- A bad write to a profile config.yaml could break that agent. Mitigations: dry-run first, a backup
  before every write, line-level edits only, and a parse check after writing.
- Hermes may read voice settings only when a session starts. After a sync the agent's window, and
  for Telegram the gateway, may need a restart. Verify in step 8. A gateway restart is done from a
  separate terminal, never from inside the gateway session.
- Restarting the listener (needed after steps 3-4) needs Peter's explicit permission each time,
  per his standing rule.
- Hand edits to a profile after sync would quietly diverge. `check` and the dashboard warning
  exist to catch that.

## 6. Out of scope

Picking new voices; changing which TTS scripts run; anything in the Hermes_Telegram_Bridge repo;
any non-voice profile setting (voicecfg reads and writes no keys, tokens or secrets).
