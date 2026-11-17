# Permission pop-up watcher

Tells Peter on Telegram when macOS is waiting for someone to click Allow on a permission pop-up
(Screen Recording, Accessibility, Full Disk Access, Photos, "... would like to access ..."), so he can
remote in and click it. It **never clicks, types or scripts anything** on screen.

A plain script, no agent and no model. launchd (`com.peter.permission-watch`) runs one pass every 30 s:

1. reads the on-screen window list (`quartz_windows.py`, ctypes into CoreGraphics; macOS 27 dropped
   `CGWindowListCopyInfo`, so it uses `CGWindowListCreate` + `CGWindowListCreateDescriptionFromArray`);
2. picks out permission prompts (`find_prompts`, a pure function):
   - drawn by `universalAccessAuthWarn`, `UserNotificationCenter`, `CoreServicesUIAgent`, `SecurityAgent`
     (also `tccd`, `AuthorizationHost`), or
   - a title saying "would like to access", "wants access", ..., or
   - a title naming Screen Recording / Accessibility / Full Disk Access / Photos on a floating window
     (layer above 0). Ordinary windows (System Settings' Accessibility page, the Photos app, a browser tab)
     never count;
3. a new prompt (key = window id + title): a picture of just that window (`screencapture -x -o -l <id>`)
   sent with Telegram sendPhoto, caption "Permission needed: <app> -> <access>. Remote in to click Allow.";
   without the Screen Recording grant it sends the same words as text and says why there is no picture;
4. one reminder if it is still open 10 minutes after the first message; nothing on close, and a prompt
   that closes and comes back is new again.

Remembered prompts: `~/.local/state/permission_watch/state.json`. Log: `watch.log` next to it (a line
per message sent; quiet otherwise).

Sends through Jarvis's bot: the token is read at run time from `~/.hermes/profiles/orchestrator/.env`
(`TELEGRAM_BOT_TOKEN`) and Peter's chat number from `~/.voice_desk/tg_dm_chat`. Neither is printed,
logged or stored here; the token reaches curl on stdin, not on its command line. curl, because Python's
urllib fails on this Mac's self-signed certificate chain.

## Commands

```
.venv/bin/python tools/permission_watch/permission_watch.py --once --dry-run   # what it sees now; sends nothing
.venv/bin/python tools/permission_watch/permission_watch.py --once             # one real pass
.venv/bin/python tools/permission_watch/permission_watch.py --test-send x.png  # x.png to Peter, captioned TEST
tools/permission_watch/install.sh [--print | --remove]                         # load / show / remove the job
```

Tests (offline, fixture window lists, fake curl):

```
uv run --no-project --python .venv/bin/python --with pytest python -m pytest tools/permission_watch -q
.venv/bin/python tools/permission_watch/test_permission_watch.py              # same, without pytest
```

## The one grant Peter gives

For the pictures (and window titles), System Settings > Privacy & Security > Screen & System Audio
Recording must allow the program launchd runs. That is the repo's `.venv/bin/python`, a link to
`~/.local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3.11` (2026-11-17; `./install.sh`
prints the exact path).
Add that file with "+" (Cmd-Shift-G to type the path). If uv later replaces that Python, the grant has
to be given again. Without it the watcher still works and sends text alerts.
