# Build iteration plan (started 2026-11-11)

Items Peter asked to queue. Not built yet.

1. Voice pick-list on the dashboard. E / Z (and F for Simba) should open a short list of working voices with the
   current one marked, arrows + Enter to pick, plus an "other, type it" line at the bottom. Today the popup is a
   plain typing box (AskScreen in dashboard.py) and only validates what is typed. The on/off switches already use
   a pick-list (ChoiceScreen), so reuse it.

2. Remove Jenny from what the dashboard shows and from fallbacks (Peter: no Microsoft Jenny, "no fallback"
   policy of 2026-11-11). Where Jenny still lives:
   - agents.toml [shared] voice = en-US-JennyNeural: the shared voice for any agent with no voice of its own,
     and for everyone when the V key turns per-agent voices off.
   - voicecfg.py gemini_wants(): falls back to Jenny when no shared voice is set.
   - Dashboard text in gridevidence.py and gridstate.py still says Gemini is "Jenny / HsiaoChen (hard-coded)",
     but Gemini now reads its voice from agents.toml (Aria). Label is stale.
   - tts.py header comment and the Hermes script name tts_kokoro_then_jenny.sh (name only).
   Decide first: what replaces Jenny as the shared voice (a Kokoro voice), and whether Gemini and Codex, which
   speak Edge/Microsoft voices today (Aria, Ryan), should move to Kokoro or stay.

3. No fallback voice anywhere (Peter 2026-11-11, replaces the narrower Jenny item above). Rule: each agent has
   exactly one voice per language. If it fails, that line stays silent; nothing else switches on (no second Edge
   voice, no Piper, no Mac voice). Today only jarvis, kylie, esther, wanna_buffet and simba have
   no_fallback = true. To do: make this the only behaviour for every agent and the Hermes voice chain
   (tts.py, tts_kokoro_then_jenny.sh, tts_natural.sh), drop the `fallback` field from agents.toml and from
   the dashboard's E/Z/F edit screens, and make the Voice check show a failed voice as a failure, not a switch.
   Claude's own hook (speak-natural.py) and Gemini's hook have their own chains and need the same change.

3b. Empty voice = silent (Peter 2026-11-11). An agent with no voice in agents.toml stays silent. Today
   voices.voice_for() hands any voiceless agent the shared voice (Jenny). Remove that default, remove
   [shared] voice / voice_zh, and decide the V key (shared-voice mode): remove it, or give it a new job.
   Silent-by-design agents (e.g. builder) just have no voice.

4. Add a new agent from the dashboard (Peter 2026-11-11). Today the grid is built only from hand-written
   [agents.*] blocks in agents.toml (agents.agents()); there is no add button. Checked: Hermes profiles on disk
   (assistant, builder, orchestrator, default) are not in the grid unless someone writes a block.
   Proposed design: an "A" key opens a pick-list of Hermes profiles found on disk that are not on the grid yet,
   plus "other (Claude/Gemini/Codex-style CLI)". Picking one asks for a display name and spoken name(s), then
   a voice from the pick-list (item 1) or "silent", and writes a new [agents.<key>] block (backup, read-back,
   undo U, same as every other panel edit). Telegram chat ids are NOT how an agent is added: the profile
   already knows its own bot, and the grid cells for Telegram direct chat / team group are found from the
   profile's own config. Also needs: a remove-from-grid key (never deletes the profile), and the new row gets
   the same on/off switches as the others.

5. Grid as a wiring matrix (Peter 2026-11-11, idea, shape to confirm). Each cell (agent x place) gets a "here /
   not here" toggle, separate from the voice on/off switch. Not here = blank cell, the agent does not appear at
   that place. Here = shown, and its colour is the Voice check's honest answer: green only when it is really
   wired and working; a "here" cell that is not wired yet shows as unfinished, never green. Each "here" cell also
   holds its activation phrase (e.g. iPhone: the Siri shortcut name, "Talk to Jarvis"; mic: "hello Jarvis";
   Telegram: the bot or group). Activation phrases are designed later. Limits to design around: a toggle cannot
   create wiring by itself (a Siri shortcut must be imported by Peter; make_shortcut.py can generate it), and the
   list of places stays fixed in code unless a new place is built (separate, lower priority).

6. Footer buttons after the changes above (Peter 2026-11-11). Grid screen today: T voice test, A test all, C check
   now, E English voice, Z Mandarin voice, L extra language, O language override, F voice effect, U undo,
   W pause/resume watcher, S listener screen (M mute, H hush, Q close are on every screen). Listener screen:
   N new conversation, V voices own/shared, X hand the mic back, R restart, K kill, Esc back.
   Becomes useless or hidden:
   - V (own voices vs shared voice): the shared voice goes away (items 2-3b). Remove.
   - L extra language and O language override: stored only, nothing enforces them (Hermes has no language
     detection). Hide until enforcement exists.
   - F voice effect: only meaningful for Simba. Show only when her row is selected.
   Stay, with a new look: E and Z open the voice pick-list (item 1); A test all keeps working but its letter is
   needed for add agent, so test all moves to Shift-T.
   Missing, to add: A add agent, D remove agent from grid (never deletes the profile), a key (or Enter-menu line)
   for the "here / not here" toggle on a cell (item 5), a key to edit a cell's activation phrase, and a "?"
   help line that explains the colours. Footer shows only keys that apply to the selected row or cell.
