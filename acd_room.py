"""Agent Control Desk: the ONE place that starts another program, and the only program it starts is the Team
Room's own command (`team-room add ...` / `team-room kick ...`).

Why it exists: Peter's rule is that every Team Room change applies to the terminal room and the Telegram group
together. The Team Room's own add / kick already does both in one step (it makes the next room, saves the member
list, and switches the Telegram side). So the desk never edits the member list and never switches the add-on by
itself; it asks the Team Room to do it, here, and acd_edit.py checks both doors afterwards.

Nothing in this file is reached unless the desk is armed (acd_edit.set_team_room returns its dry run first).
It restarts nothing and sends nothing.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

Runner = Callable[[list], tuple]        # (the Team Room command's words) -> (exit code, what it printed)
ALLOWED = ("add", "kick")               # the only two things the desk may ask the Team Room to do

# Hermes says one of these when the running system did not reload its add-ons after an add-on was switched.
_RESTART_SAID = re.compile(r"restart (it|the gateway)|restart_required|hermes gateway restart", re.I)


def says_restart(text: str) -> bool:
    """True when what the Team Room printed says the Telegram side still needs a restart to take effect."""
    text = str(text or "")
    return bool(_RESTART_SAID.search(text)) and "no restart" not in text.lower()


def default_runner(command: Optional[Path]) -> Runner:
    def run(words: list) -> tuple:
        if not words or words[0] not in ALLOWED:
            return 2, "the desk only asks the Team Room to add or kick; nothing was run"
        if not command or not Path(command).exists():
            return 127, "the Team Room's own command was not found; nothing was run"
        try:
            done = subprocess.run([str(command), *[str(w) for w in words]], capture_output=True, text=True,
                                  timeout=240, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            return 126, f"the Team Room's own command could not run ({str(exc)[:120]})"
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    return run
