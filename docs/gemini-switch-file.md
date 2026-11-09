# Gemini's voice switch file

Prepared in step 3, honoured by Gemini's hook since step 4 (2026-11-09; `~/.gemini/config/hooks/speak-response.py`, backup
`speak-response.py.bak-20261109-step4` next to it).

**Convention** (same as the speech queue's `speech/off`): a marker file.

    ~/.voice_desk/gemini_voice_off     exists  -> Gemini stays silent
                                       absent  -> Gemini speaks as it always did

The folder follows `VOICE_DESK_STATE` when that is set (the tests use it). The file's content is ignored; the panel
writes `off`.

**What the hook does** (at the top of the hook, before any audio is made; this is the check now in the hook):

```python
import os
state = os.environ.get("VOICE_DESK_STATE") or os.path.expanduser("~/.voice_desk")
if os.path.exists(os.path.join(state, "gemini_voice_off")):
    raise SystemExit(0)            # silent: Peter switched Gemini off in the Voice Desk grid
```

The text `gemini_voice_off` must appear in the hook's source: the grid looks for it to know whether the hook listens.

**Where the grid shows it.** Gemini row, Terminal window column. Enter → Off / Always. While the hook does not
read the file the cell stays "Always" and red ("the Off switch is set but Gemini's hook does not read it yet")
so a set switch never looks like a working one. U undoes the switch.
