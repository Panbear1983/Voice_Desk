# Where every voice setting lives

One page, checked 2026-11-09 against the live Mac. The dashboard grid (`./dashboard.sh`) reads every
place below; it changes only the ones marked **edit** or **switch**. Everything stays where the agents already read
it: if the dashboard is closed, voices keep working exactly as last set.

| What | Where it lives | Who reads it, and when | Panel |
|---|---|---|---|
| Voice type (English / Mandarin), speed, end phrases, voice-mode knobs | `Voice_Desk/agents.toml` (`voice`, `voice_zh`, `speed`, `auto_tts`, …) | `voicecfg sync` copies it into each Hermes `config.yaml` and `~/.claude/voice-defaults.conf`; Voice Desk re-reads `agents.toml` before every spoken line | **edit** E / Z pick-list (backup, read-back, undo U) |
| The voice bank (what E / Z offer, 2026-11-13) | `voicebank.py` reads every installed Kokoro voice file (`~/.local/share/claude-voice/voices-v1.0.bin`, `~/Desktop/Peter_Tax/tax_engine/data/voice/voices-v1.0.bin`) and Microsoft's own voice list saved in `Voice_Desk/edge_voices.json` (`python -m voicebank refresh` fetches it again; the dashboard never goes online) | When a list opens. Everyone sees the whole bank for the language, local voices first; a voice the agent's own speaker cannot make yet is shown ◌ and cannot be picked, with the reason (stage 2 moves Gemini's, Esther's, Jarvis's and Simba's scripts onto the shared speaker). `python -m voicebank` lists it | read-only |
| Extra languages, language override | `agents.toml`: `voices_extra = { ja = "ja-JP-NanamiNeural" }`, `voice_override = "auto"` (`en`, `zh` or an extra code) | Stored now, nothing enforces them yet (Hermes has no language detection: one voice per agent) | **edit** L / O |
| Quiet hours | `agents.toml`: `quiet_hours = { tg_dm = "22:00-07:00" }` (places: `terminal`, `tg_dm`, `tg_group`, `mic`, `iphone`) | Stored and shown only; enforcement is a later step | by hand |
| Hermes terminal window: voice on | `~/.hermes/profiles/<profile>/config.yaml` → `voice.auto_enable` (local Hermes patch) and `voice.auto_tts` | Per window, in memory: starts OFF unless `auto_enable: true`; read when the window starts | read-only |
| Telegram direct chat: off / voice_only / all | `~/.hermes/gateway_voice_mode.json`, keys `<profile>:telegram:<chat id>` (Peter's chat id 7512954760). File does not exist until the panel (or `/voice` in Telegram) writes it; old orphaned copies sit in `profiles/orchestrator/` and `profiles/wanna-buffet/` and are NOT read (and never written). No saved mode → `voice.auto_tts` decides | Read at gateway start AND, with Peter's live-reload patch loaded (`hermes_patches.py`), re-read on the next message after the file changes | **switch** Enter: Off / Voice-back / Always (live once the gateway has restarted with the patch) |
| Telegram team group: voice notes | `~/.hermes/profiles/orchestrator/scripts/team_room/state/mirror_config.json` → `voice_notes.on` and `voice_notes.agents` (group chat id -5370236728) | Re-read on every line: live | **switch** Enter: Off / Always per agent (same rule and file layout as the team room's own `mirror voice on\|off <agent>`; turning on when the whole group was off also says who else comes on) |
| Claude | `~/.claude/settings.json` → `voice.enabled` (global, no per-project switch) · `~/.claude/voice-defaults.conf` (`NEURAL_VOICE_EN/ZH`, `VOICE_ALWAYS_ON`, `VOICE_MODE`; the SessionStart hook `voice-defaults.sh` forces it back on) · hooks `~/.claude/hooks/speak-response.sh` + `speak-natural.py` · per-session state in `~/.claude/hooks/state/` (`last-spoken-<session>` = it spoke) | Stop hook, every reply | voice type **edit**; on/off **switch** Enter: Off / Always. Off writes `voice.enabled` **and** the legacy `voiceEnabled` in settings.json (both hooks test `.voice.enabled // .voiceEnabled`, and jq's `//` skips a false), `VOICE_ALWAYS_ON=0` in the conf (else the SessionStart hook switches it back on) and `always_on` in agents.toml (else `voicecfg check` calls it drift). `~/.claude/hooks` is not touched by the panel (the hook itself plays through the speech queue since step 4) |
| Gemini | its own Stop hook `~/.gemini/config/hooks/speak-response.py` (speaks the Microsoft voices Voice Desk writes into `~/.gemini/config/hooks/voice-defaults.conf`; still switches to the Mac voice when that fails, until stage 2; plays through the speech queue as `gemini`, and stays silent while `~/.voice_desk/gemini_voice_off` exists, see `gemini-switch-file.md`); cache `~/.gemini/config/hooks/audio_cache/` | Stop hook, every reply | **switch** Enter: Off / Always (writes the file; the hook reads it on its next reply) |
| Codex | no speech hook of its own; speaks only through Voice Desk, with its own voice from `agents.toml` | — | read-only |
| Simba (since 2026-11-13 her row is her Hermes profile `simba`) | `agents.toml` `[agents.simba]`: `profile = "simba"`, `voice` (a Kokoro voice her script makes: American today), `effect` (a recipe: sample number 1-30 or `fenrir16` / `smoky` / `raspy` / `plain`, then words or knob=value), `english_only`, `command` = `simba_tts.py` run with the Kokoro venv. Her profile speaks through `~/.hermes/profiles/simba/scripts/tts_simba.sh`, which reads voice and effect from `agents.toml`; her old listener (panbear-daily `simba.py`, `simba.yaml voice_notes`) sends her Team_Room voice notes | Before every line | **edit** E / F (backup, history, undo U); her Telegram cells are shown, never switched: Voice Desk does not move her bot |
| Voice Desk listener | `~/.voice_desk/listener.json` (state, who is on the line), `muted` flag, `listener.inbox` orders, launchd `com.peter.voice-desk-listener` | Heartbeat every 0.5 s | M, H, N, X, R, K |
| Speech queue (one voice at a time) | `~/.voice_desk/speech/` — `play.lock`, `floor.json`, `gate.json`, `last.json`; off switch = a file named `off`; stand-in `~/bin/afplay` | Every Hermes window sentence | **switch** Enter on the mic cell (on = voices take turns; off = windows play straight) |
| iPhone door | `receiver.py` (launchd `com.peter.voice-desk-receiver`, port 8765, token in `~/.voice_desk/receiver_token`), 11 Siri shortcuts (`*.shortcut`, never committed). The Mac sends text; Siri reads it in Siri's own voice | Per request | read-only |
| Hermes local patches (can vanish on a Hermes update) | kept outside Hermes: `~/.hermes/backups/local-patches/manifest.json` + one `.diff` each (CLI windows: quiet while `~/.voice_desk/live_turn` is fresh, `voice.auto_enable`, stream-gap read-aloud, Ogg→WAV for afplay; gateway: live chat modes, Telegram voice note for MP3 replies) | Checked by `python3 hermes_patches.py check`; shown in the top strip as "Hermes fixes present (n/n)" (grey = in but the gateway has not restarted since) | `hermes_patches.py apply` puts back what fits |

## Where the evidence comes from (the Voice check)

Settings prove nothing, so the grid also reads what really happened. Read-only, silent, one shared
reader, re-read every 45 s (and on C), unchanged files are not read again.

- `~/.hermes/profiles/<profile>/logs/agent.log` and `gateway.log`:
  `gateway.run: response ready: platform=telegram chat=<id>` (a reply was made; a negative chat id is the
  group), `[Telegram] Sending response` (the text went out), `tools.tts_tool: Generating speech` and
  `TTS audio saved: <file> (… provider: x)` (a voice was made; `team_room_voice_*` = a team note,
  `hermes_voice/tts_reply_*` = a direct-chat reply), `Auto voice reply TTS failed` (a voice failed).
  **Hermes does not log whether Telegram accepted the voice message**, so "voice made" is the strongest
  evidence for a direct chat.
- Claude: newest `~/.claude/hooks/state/last-spoken-*` file. Gemini: newest file in its `audio_cache/`.
- Voice Desk: `~/.voice_desk/listener.json`, `speech/last.json`, `receiver.log` (the iPhone's own voice is
  Siri's and cannot be heard by the Mac: only the request log is evidence).

Colours: **green** spoke recently and it worked · **grey** idle, nothing to say lately (not a failure) ·
**yellow** worked last time but some of the last replies were text only · **red** mismatch or failure
(the setting says voice and the log says text only or an error; or it says Off and a voice was made).

## Facts worth knowing

- `tts_natural.sh` (Hermes' voice script) switches to its own Mandarin voices for mostly-Chinese text and
  ignores the voice name it is given, so a changed `voice_zh` reaches Claude's hook and Voice Desk's
  settings but not what that script speaks. The silent Voice check asks Edge for the named voice directly.
- The Voice check's silent test renders one sentence per language into a temp folder and verifies it with
  `ffprobe`; nothing is played and nothing is sent to Telegram.
- Panel changes write a backup of `agents.toml` to `~/.voice_desk/voice_history_backups/`, one backup of each
  changed Hermes/Claude file to `~/.hermes/profile-backups/<time>-voice-panel/`, and one line to
  `~/.voice_desk/voice_history.jsonl`. Undo runs the change backwards, so it never discards a hand edit
  made to the same file in between.

## Switching a cell on or off (step 2)

`./dashboard.sh`, ← → to a place, **Enter**. Live today: Telegram team group (Off / Always), Claude terminal
(Off / Always) and the Voice Desk mic window / phone column, which holds two global switches (each agent's own
voice vs the shared voice, speech queue on / off) because Voice Desk always answers aloud when Peter speaks.
Terminals and the team group have no Voice-back (nothing there knows whether Peter spoke). Hermes terminal
windows (per window, in memory) and the iPhone (Siri speaks) stay read-only; Enter on them says why.

Every switch: work out the exact edits, back each file up (`~/.hermes/profile-backups/<time>-voice-panel/`,
`agents.toml` in `~/.voice_desk/voice_history_backups/`; the two marker files have nothing to back up), write
atomically, read back the way the file's own reader reads it (json, bash `source`, `tomllib`, and `jq` for
the Claude hook's own test), put every file back at once if anything does not read back, then one line in
`~/.voice_desk/voice_history.jsonl`. **U** undoes the newest change or switch key by key, so a hand edit made
to the same file in between survives, and a name taken off the team-group list goes back to the same place.

**Step 3 additions.** *Telegram direct chat* (Off / Voice-back / Always) writes `~/.hermes/gateway_voice_mode.json`
(key `<profile>:telegram:<chat id>` → `off` / `voice_only` / `all`; the file is created if it is not there yet,
a file that cannot be read is never overwritten). It is live ONLY once the running gateway has Peter's
live-reload patch loaded (the gateway then re-reads the file on the next message, no timer): the cell note and
the message say which case applies. The stray copies in `profiles/*/gateway_voice_mode.json` are never read or
written. *Gemini's terminal* (Off / Always) creates / removes `~/.voice_desk/gemini_voice_off`; Gemini's hook
honours it since step 4 (see `docs/gemini-switch-file.md`; before step 4 the cell stayed red with "the Off switch
is set but the hook does not read it yet"). One trap until the gateway has the patch loaded: a `/voice` command typed
in Telegram makes the old gateway rewrite the file from its own memory, which would undo a panel change made since
its start.

**Step 4 additions.** The watcher (`watcher.py`, launchd job `com.peter.voice-desk-watcher` via `install_watcher.sh`)
re-applies what the panel last set; state in `~/.voice_desk/watcher.json` (last check, problems it could not repair),
`watcher_log.jsonl` (what it put back), pause = the file `~/.voice_desk/watcher_paused` (W in the dashboard). Claude's
and Gemini's Stop hooks play through the speech queue (`~/bin/afplay` stand-in, `VOICE_DESK_SPEAKER=claude|gemini`);
`speech/off` gives the old behaviour. See the README section "The watcher (step 4)".

## Hermes local patches (step 3)

Peter's patches to Hermes' own files (`~/.hermes/hermes-agent`) are kept OUTSIDE it, in
`~/.hermes/backups/local-patches/` (`manifest.json` + one diff each), so an update cannot silently lose them:

    python3 hermes_patches.py check    # which are still in; exit 1 when one is gone
    python3 hermes_patches.py apply    # put back the ones that fit (backs the files up first, never forces)

"In" means the manifest's marker text is found; "missing, fits" can be re-applied; "conflict" means the code around
it changed upstream and needs a person. For gateway patches `check` also says whether the running gateway has
them: a file on disk is not loaded code until the gateway restarts. The top strip's "Hermes fixes" light is the
same check (green all in and live · grey in but the gateway has not restarted · red something is gone).

