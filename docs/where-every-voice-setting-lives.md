# Where every voice setting lives

One page, checked 2026-11-09 against the live Mac. The dashboard grid (`./dashboard.sh`) reads every
place below; it changes only the ones marked **edit** or **switch**. Everything stays where the agents already read
it: if the dashboard is closed, voices keep working exactly as last set.

| What | Where it lives | Who reads it, and when | Panel |
|---|---|---|---|
| Voice type (English / Mandarin), speed, end phrases, voice-mode knobs | `Voice_Desk/agents.toml` (`voice`, `voice_zh`, `speed`, `auto_tts`, …) | `voicecfg sync` copies it into each Hermes `config.yaml` and `~/.claude/voice-defaults.conf`; Voice Desk re-reads `agents.toml` before every spoken line | **edit** E / Z (backup, read-back, undo U) |
| Extra languages, language override | `agents.toml`: `voices_extra = { ja = "ja-JP-NanamiNeural" }`, `voice_override = "auto"` (`en`, `zh` or an extra code) | Stored now, nothing enforces them yet (Hermes has no language detection: one voice per agent) | **edit** L / O |
| Quiet hours | `agents.toml`: `quiet_hours = { tg_dm = "22:00-07:00" }` (places: `terminal`, `tg_dm`, `tg_group`, `mic`, `iphone`) | Stored and shown only; enforcement is a later step | by hand |
| Hermes terminal window: voice on | `~/.hermes/profiles/<profile>/config.yaml` → `voice.auto_enable` (local Hermes patch) and `voice.auto_tts` | Per window, in memory: starts OFF unless `auto_enable: true`; read when the window starts | read-only |
| Telegram direct chat: off / voice_only / all | `~/.hermes/gateway_voice_mode.json`, keys `<profile>:telegram:<chat id>` (Peter's chat id 7512954760). File does not exist now; old orphaned copies sit in `profiles/orchestrator/` and `profiles/wanna-buffet/` and are NOT read. No saved mode → `voice.auto_tts` decides | Read only when the gateway starts (`gateway/run_runtime_init.py:220`): not live-changeable yet | read-only |
| Telegram team group: voice notes | `~/.hermes/profiles/orchestrator/scripts/team_room/state/mirror_config.json` → `voice_notes.on` and `voice_notes.agents` (group chat id -5370236728) | Re-read on every line: live | **switch** Enter: Off / Always per agent (same rule and file layout as the team room's own `mirror voice on\|off <agent>`; turning on when the whole group was off also says who else comes on) |
| Claude | `~/.claude/settings.json` → `voice.enabled` (global, no per-project switch) · `~/.claude/voice-defaults.conf` (`NEURAL_VOICE_EN/ZH`, `VOICE_ALWAYS_ON`, `VOICE_MODE`; the SessionStart hook `voice-defaults.sh` forces it back on) · hooks `~/.claude/hooks/speak-response.sh` + `speak-natural.py` · per-session state in `~/.claude/hooks/state/` (`last-spoken-<session>` = it spoke) | Stop hook, every reply | voice type **edit**; on/off **switch** Enter: Off / Always. Off writes `voice.enabled` **and** the legacy `voiceEnabled` in settings.json (both hooks test `.voice.enabled // .voiceEnabled`, and jq's `//` skips a false), `VOICE_ALWAYS_ON=0` in the conf (else the SessionStart hook switches it back on) and `always_on` in agents.toml (else `voicecfg check` calls it drift). `~/.claude/hooks` is not touched |
| Gemini | its own Stop hook `~/.gemini/config/hooks/speak-response.py` (Jenny / HsiaoChen hard-coded, no off switch); cache `~/.gemini/config/hooks/audio_cache/` | Stop hook, every reply | read-only (not editable yet) |
| Codex | no speech hook of its own; speaks only through Voice Desk with the shared voice | — | read-only |
| Voice Desk: own voice per agent or shared | `~/.voice_desk/voices_per_profile` (exists = everyone uses the shared Jenny voice; V key on the listener screen) | Before every line | **switch** Enter on the mic cell, or V on the listener screen |
| Voice Desk listener | `~/.voice_desk/listener.json` (state, who is on the line), `muted` flag, `listener.inbox` orders, launchd `com.peter.voice-desk-listener` | Heartbeat every 0.5 s | M, H, N, X, R, K |
| Speech queue (one voice at a time) | `~/.voice_desk/speech/` — `play.lock`, `floor.json`, `gate.json`, `last.json`; off switch = a file named `off`; stand-in `~/bin/afplay` | Every Hermes window sentence | **switch** Enter on the mic cell (on = voices take turns; off = windows play straight) |
| iPhone door | `receiver.py` (launchd `com.peter.voice-desk-receiver`, port 8765, token in `~/.voice_desk/receiver_token`), 11 Siri shortcuts (`*.shortcut`, never committed). The Mac sends text; Siri reads it in Siri's own voice | Per request | read-only |
| Hermes local patches (can vanish on a Hermes update) | `hermes_cli/cli_voice_mixin.py` (quiet while `~/.voice_desk/live_turn` is fresh), `hermes_cli/cli_tui_mixin.py` (`_maybe_auto_enable_voice`) | Shown in the top strip as "Hermes fixes present (n/2)" | read-only |

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
Terminals and the team group have no Voice-back (nothing there knows whether Peter spoke). Telegram direct
chat (read only when the gateway starts), Hermes terminal windows (per window, in memory), Gemini (no off
switch yet) and the iPhone (Siri speaks) stay read-only; Enter on them says why.

Every switch: work out the exact edits, back each file up (`~/.hermes/profile-backups/<time>-voice-panel/`,
`agents.toml` in `~/.voice_desk/voice_history_backups/`; the two marker files have nothing to back up), write
atomically, read back the way the file's own reader reads it (json, bash `source`, `tomllib`, and `jq` for
the Claude hook's own test), put every file back at once if anything does not read back, then one line in
`~/.voice_desk/voice_history.jsonl`. **U** undoes the newest change or switch key by key, so a hand edit made
to the same file in between survives, and a name taken off the team-group list goes back to the same place.
