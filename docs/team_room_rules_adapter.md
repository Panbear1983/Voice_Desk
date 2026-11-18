# The Team Room rules adapter

A small, additive change to the Team Room so that it reads the Agent Control Desk's rules file
(`acd_rules.toml`). It is **not installed**. It is delivered as a patch, `team_room_rules_adapter.patch`,
and was applied and tested only on a throw-away copy of the Team Room (2026-11-17). The live Team Room was
read, never changed.

## What it changes

One file, `team_room.py`, about 70 added lines and nothing removed:

1. **A reader.** `desk_rules()` reads the desk's rules file (the `[defaults]` part with each agent's own lines
   on top). It looks at the file again only when the file changes. `may_speak(profile)` answers one question
   from it.
2. **Who Peter's plain words reach.** Today the first plain name of a member in one of Peter's lines becomes
   that member's `@handle`, at both doors. With the adapter, an agent whose rule says it may **not** speak in
   the Team Room is skipped: Peter's plain mention no longer hands it a turn. An explicit `@handle` typed by
   Peter still works; that is his deliberate override.
3. **What the agents are told.** The room's notes gain one short paragraph built from the rules, for example:
   "Team rules from the Agent Control Desk: Only Jarvis hands out work. Builder takes work only as job cards."
   Nothing checks these; the agents are told, not policed.

Both doors share these: the terminal room and the Telegram relay already send Peter's lines through the same
function and use the same notes, so a rule lands on both together. That is Peter's rule for the Team Room.

## What it does not do

- It cannot mute a member. The room has no mute; `@everyone` and another agent's `@name` still reach an agent
  whose rule says "may not speak". Taking the agent out of the room is the only full stop.
- It does not enforce who hands out work, who takes work from whom, or "answers: always". Those rules are
  written into the notes at most. The desk shows them as "told to the agents in the room's notes, not
  enforced" or "recorded, not yet enforced".

## When the rules file is missing or unreadable

Every function answers exactly as the room behaved before: everyone may speak, and the notes are word for word
what they were. Checked on the copy: the Team Room's own 525 tests pass with no rules file, and again with the
desk's real rules file in place.

With today's rules file (everyone may speak), the only visible difference after installing is the one extra
paragraph in the room's notes.

## Installing it (not done; needs Peter's yes)

In the Team Room's folder: `patch -p1 --dry-run < team_room_rules_adapter.patch`, then the same without
`--dry-run`. The rules file is found at `~/GitHub/Agent_Control_Desk/acd_rules.toml`, or wherever the setting
`TEAM_ROOM_DESK_RULES` points; that path has to follow the project when it is renamed.

**Does the room need a reload? Yes, once, to load the new code:**

- the Team Room's window has to be closed and started again (it holds the old code in memory), and
- the Telegram relay has to be reloaded with the room's own `reload_mirror_plugin.py` (a few seconds; no
  restart of the agents' system).

After that, a change to the rules file is picked up by itself for point 2 (who Peter's words reach), at both
doors, with no reload. The notes paragraph (point 3) is applied the next time the room's context is set:
`team-room setup` (safe to repeat) or the next add / kick.

## How the desk knows

The adapter carries the marker word `ACD_RULES_ADAPTER`. The desk looks for that word in the Team Room's
program (read only). Found: rule lines say "enforced by the Team Room" or "told to the agents". Not found:
they say "recorded, not yet enforced".
