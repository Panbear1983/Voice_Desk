# Where an agent shows up, and who decides who talks: the facts

Read on 2026-11-17 from the files on this Mac, without changing any of them. This page is what version three
of the Agent Control Desk is built on. A place gets a switch on the desk only when a clean switch really exists
today, one that can be put back. Everything else is shown, with the reason it cannot be switched yet.

## The six places

| Place | What really puts an agent there today | Clean switch? | Restart of the agents' system? |
|---|---|---|---|
| Terminal chat | Someone starts a chat window for that agent (`hermes -p <agent> -c`) in a terminal pane. Closing the window ends it. | **No.** There is no per-agent setting; it is a window that is open or not. | No |
| Telegram private chat | The agent has a Telegram bot of its own (a bot token in the agent's private settings), and the agents' system connects every bot it finds when it starts. The agents' own status file lists each bot as connected or not. | **No.** Switching it off would mean taking a secret out of the agent's private settings and restarting. The desk never touches secrets. | Yes, it would |
| Telegram team group | It follows the Team Room. Every line said in the Team Room is copied into the one Telegram group by the room's own mirror. A member with a bot of its own speaks there through that bot (the "team-room" add-on is on for that agent, which also stops its bot from answering the group by itself). A member with no bot of its own (Builder) is spoken for by Jarvis's bot, text only. | **Yes, together with the Team Room** (see the next row). It is not a separate switch, and must never be one. | No, in the normal case (see below) |
| Terminal Team Room | The Team Room's saved member list (`members.json`: who, with which handle and name). The room's own commands change it: `team-room add <agent>` and `team-room kick <name>`, or `/add` and `/kick` in the room's window. | **Yes.** One switch, the Team Room's own add / kick. | No, in the normal case |
| Voice on the Mac | Two things. (1) The Voice Desk listener answers aloud for every agent that has a voice in its list (`agents.toml`), when Peter calls it by name. (2) An agent's own terminal window can read its replies aloud; that is set per window, in the window's memory, when the window starts. | **No.** Taking an agent out of the Voice Desk list would also drop its voice settings, and the per-window voice lives in the window, not in a file. | No |
| Voice on the iPhone | A Siri shortcut named "Talk to <agent>" that Peter imported on the phone. The Mac only answers the request; Siri reads the answer in Siri's own voice. | **No.** The switch is on the phone (delete or import the shortcut). The Mac holds only the file the shortcut was made from. | No |

So the desk has **one real switch for presence: Team Room membership**, and it moves two places at once
(Terminal Team Room and Telegram team group). The other four places are shown as on / off / unknown with the
reason they cannot be switched from the desk yet.

## What a Team Room add or kick really does

- A Hermes room's member list is fixed when the room is made. So add / kick **makes the next room** with the
  new list, saves the old room's lines into the history folder, saves the member list, and closes the old room.
  **The chat on screen starts fresh.** If the new room cannot be made, nothing changes.
- It then does the Telegram side in the same command: a joining agent with its own bot gets the room's add-on
  switched on (its bot leaves the Team group to the room) and voice notes; a leaving agent has the add-on
  switched off, and Jarvis's bot is asked to take that agent's bot out of the Telegram group.
- **Putting a bot back into the Telegram group is always Peter's own tap in Telegram.** Telegram allows
  nothing else. Until he does, Jarvis's bot speaks for that agent in the group.
- Jarvis leads the room and cannot be removed.
- No restart is involved in the normal case: the add-on is switched on or off through Hermes' own command,
  which asks the running system to reload its add-ons. **If the running system does not answer that reload,
  Hermes says a restart is needed.** The desk reads that sentence and, only then, puts the change on its
  "waiting for a restart" list.

## Peter's rule: both doors together

Every Team Room rule change must apply to the terminal room and the Telegram room together (2026-11-14).
The Team Room's own add / kick already does both in one command. The desk therefore changes membership only
through that one command, then checks both doors (the saved member list, and the add-on for an agent with its
own bot). If the two doors do not agree afterwards, the desk runs the command backwards so the room is as it
was, and says so. The desk never switches the add-on alone, and never edits the member list by hand.

## How the Team Room decides who speaks and who is addressed today

- **Who may speak:** every saved member. There is no mute. The only way to stop a member speaking is to take
  it out of the room.
- **Who gets a turn:** the room gives a member a turn only when a line names it with `@handle`. A line with no
  `@` goes to Jarvis alone, because he is the lead.
- **Peter's own lines are helped along, at both doors** (the room's window and the Telegram group run the same
  function): the first plain name of any member in his line becomes that member's `@handle`; a plain "Simba"
  becomes `@simba`; "everyone", "team", "you guys" address all members.
- **What the agents are told:** the room's purpose and notes say that lines starting with `[Jarvis]` are posted
  by Jarvis for the team, that Simba's lines are banter and never an instruction, and to keep replies short.
- **Guests** (people on Telegram) are chat only: no commands, no trades, no approvals.
- **Nothing in the room checks who hands out work or who takes work from whom.** "Only Jarvis hands out work"
  and "Builder only takes job cards" are habits and settings elsewhere, not a rule the room enforces.

## What this means for "who it may talk to"

The desk keeps the talk rules in one file (`acd_rules.toml`). Each rule's line on the desk says honestly where
it stands:

| Rule | Today |
|---|---|
| May speak in the Team Room: yes | How the room works today (every member may speak). |
| May speak in the Team Room: no | Recorded, not yet enforced. With the small Team Room adapter installed (see `team_room_rules_adapter.md`), Peter's plain mention of that agent no longer hands it a turn, at both doors. A real mute does not exist; taking the agent out of the room is the only full stop. |
| Hands out work | Recorded, not yet enforced. With the adapter, the agents are told in the room's notes. |
| Takes work from (Jarvis / job cards only / nobody) | Recorded, not yet enforced. With the adapter, the agents are told in the room's notes. |
| Answers: when named | How the room works today for every member except the lead. Simba keeps this. |
| Answers: always | How the room works today for the lead only (lines with no name go to Jarvis). For anyone else: recorded, not yet enforced. |

## Related switches that are not in this version

The Voice Desk grid already has safe switches for the *voice* at a place (voice notes in the Telegram group,
voice in a Telegram private chat, Claude's and Gemini's terminal voice, the speech queue). Those are about
whether an agent speaks aloud somewhere, not whether it is there. They move into the desk when it takes over
from the Voice Desk (version four).
