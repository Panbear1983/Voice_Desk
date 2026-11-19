"""Offline tests: a new Hermes agent's column is fully filled (the brief of 2026-11-19, card 1).

Peter: "for the 1st phase ... a manually added feature for a new agent ... only Hermes dedicated feature ... type the
agent name that we named when we 1st created Hermes, and then start to configure all the features."

    n                 adds a Hermes profile to the Voice Desk list with kind = "hermes" (kind = "cli" for another
                      terminal assistant); then the desk reads the profile's OWN files and fills its column:
                      model, thinking effort, service and sign-in, the fallback ladder, the Telegram bot (is a token
                      there, is the bot connected), the terminal window, the Team Room
    anything missing  red, "not set up", with the reason in plain words; never a green light that is not earned
    every row         editable through the same pick-lists as the other agents (the full voice list: Kokoro first,
                      Microsoft below, silent, other); empty voice = silent
    t, c, d, u        the voice test and the read again work on the new column; d takes it out of the Voice Desk
                      list only, and u puts it back

Throw-away folders only: fake profile directories with real-looking files (config.yaml, auth.json, .env), a fake
herdr, a fake gateway status. Nothing real is read or contacted, and no token is real.

    .venv/bin/python -m unittest tests.test_acd_new_agent -v
"""
import dataclasses
import datetime as dt
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_parity as P  # noqa: E402
import acd_setup as S  # noqa: E402
import agents as A  # noqa: E402
from tests.test_acd_table import make_app, menu_list, open_cell, settle, show  # noqa: E402
from textual.widgets import Input, OptionList  # noqa: E402

WHEN = dt.datetime(2026, 11, 19, 12, 0, 0)
FAKE_TOKEN = "123456789:FAKE-token-for-tests-only-0000"      # not a real token: the tests check it never leaks
AGY = S.AGY

OWL_CONFIG = """\
model:
  provider: anthropic
  default: claude-sonnet-5-5
  base_url: ''
fallback_providers:
  - provider: antigravity-subscription-directsdk
    model: claude-sonnet-5-5
  - provider: openai-codex
    model: gpt-5.4-mini
agent:
  reasoning_effort: medium
plugins:
  enabled:
    - herdr-agent-state
"""
BARE_CONFIG = """\
model:
  provider: anthropic
  default: claude-sonnet-5-5
fallback_providers: []
plugins:
  enabled: []
"""


def auth_json(*statuses: str, service: str = "anthropic") -> str:
    """auth.json as Hermes writes it, with fake sign-ins whose last status is each word given."""
    return json.dumps({"version": 1, "providers": {}, "credential_pool": {service: [
        {"id": f"fake{i}", "label": "fake", "auth_type": "oauth", "priority": i, "source": "manual:hermes_pkce",
         "access_token": "FAKE-ACCESS", "refresh_token": "FAKE-REFRESH", "last_status": s or None}
        for i, s in enumerate(statuses)]}, "updated_at": "2026-11-19T00:00:00Z"})


def make_profile(profiles: Path, name: str, config: str, *, auth: str = "", env: str = "") -> Path:
    folder = profiles / name
    (folder / "logs").mkdir(parents=True, exist_ok=True)
    (folder / "config.yaml").write_text(config, encoding="utf-8")
    if auth:
        (folder / "auth.json").write_text(auth, encoding="utf-8")
    if env:
        (folder / ".env").write_text(env, encoding="utf-8")
    return folder


def fake_herdr(*titles: str) -> str:
    """A herdr that lists panes with these window titles (and does nothing else)."""
    d = Path(tempfile.mkdtemp(prefix="acd-herdr-"))
    panes = [{"pane_id": f"wT:p{i}", "terminal_title": t, "terminal_title_stripped": t} for i, t in enumerate(titles)]
    script = d / "herdr"
    script.write_text("#!/bin/sh\ncase \"$1 $2\" in\n  \"pane list\") cat <<'EOF'\n"
                      + json.dumps({"result": {"panes": panes}}) + "\nEOF\n;;\nesac\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def owl_world(armed: bool = True, *, full: bool = True):
    """The desk's usual fake world, plus a Hermes profile night_owl that is on this Mac but not in the Voice Desk
    list. full=True: everything set up (sign-in, backups, Telegram token and connected bot, window, Team Room);
    full=False: a bare profile with almost nothing."""
    w = F.with_grid(F.edit_world(armed=armed))
    if full:
        make_profile(w.profiles, "night_owl", OWL_CONFIG, auth=auth_json("ok", "dead"),
                     env="\n".join(["# Night Owl's own settings", "NVIDIA_API_KEY=", "TELEGRAM_BOT_TOKEN=" + FAKE_TOKEN])
                     + "\n")
        gw = json.loads((w.root / "gateway_state.json").read_text())
        gw["platforms"]["night_owl:telegram"] = {"state": "connected"}
        (w.root / "gateway_state.json").write_text(json.dumps(gw))
        room = json.loads((w.root / "members.json").read_text())
        room["members"].append({"profile": "night_owl", "handle": "owl", "name": "Night Owl"})
        (w.root / "members.json").write_text(json.dumps(room))
        (w.root / "repo" / "Talk to Night Owl.shortcut").write_text("fake")
        herdr = fake_herdr("Hermes — night_owl", "Hermes — orchestrator")
    else:
        make_profile(w.profiles, "night_owl", BARE_CONFIG, env="# nothing here yet\n")
        herdr = fake_herdr("Hermes — orchestrator")
    w.paths = dataclasses.replace(w.paths, windows=S.windows_reader(herdr))
    w.edit = dataclasses.replace(w.edit, herdr=herdr)
    return w


def add_owl(w, voice=None):
    r = P.add_agent(w.edit, "Night Owl", "night owl, owl", voice, profile="night_owl", now=WHEN)
    assert r.ok and not r.dry, r.message
    return r


def owl(snap: D.Snapshot) -> D.AgentView:
    return next(v for v in snap.agents if v.profile == "night_owl")


def cell(key: str, v) -> acd.Cell:
    return acd.cell_at(key, v)


# ---- the connector on its own -------------------------------------------------------------------------------------
class TheConnector(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="acd-setup-"))
        self.profiles = self.root / "profiles"

    def setup_of(self, cfg, **kw) -> S.Setup:
        return S.hermes_setup("night_owl", "Night Owl", cfg, self.profiles, **kw)

    def test_a_fully_set_up_profile_is_green_piece_by_piece(self):
        make_profile(self.profiles, "night_owl", OWL_CONFIG, auth=auth_json("ok"), env=f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n")
        (self.root / "shortcuts").mkdir()
        (self.root / "shortcuts" / "Talk to Night Owl.shortcut").write_text("x")
        s = self.setup_of(F.lines_loader(self.profiles / "night_owl" / "config.yaml"),
                          platforms={"night_owl:telegram": {"state": "connected"}},
                          members={"lead": "orchestrator", "members": [{"profile": "night_owl"}]},
                          windows={"night_owl"}, shortcuts=self.root / "shortcuts")
        self.assertEqual([p.key for p in s.pieces],
                         ["model", "effort", "signin", "ladder", "telegram", "window", "team_room", "iphone"])
        for p in s.pieces:
            self.assertEqual((p.status, p.found), (S.GREEN, True), p)
        self.assertEqual(s.missing, [])
        self.assertEqual(s.piece("ladder").words, "2 backups")

    def test_each_missing_piece_is_red_not_set_up_with_its_plain_reason(self):
        make_profile(self.profiles, "night_owl", BARE_CONFIG)
        s = self.setup_of({"model": {"default": "claude-sonnet-5-5", "provider": "anthropic"}},
                          platforms={}, members={"lead": "orchestrator", "members": []}, windows=set(),
                          shortcuts=self.root)
        why = {p.key: p.why for p in s.missing}
        self.assertEqual(set(why), {"effort", "signin", "ladder", "telegram", "window", "team_room", "iphone"})
        for p in s.missing:
            self.assertEqual(p.status, S.RED, p)
            self.assertNotEqual(p.status, S.GREEN)
        self.assertEqual(why["telegram"], "this profile has no Telegram bot token")
        self.assertIn("no saved sign-in for the Claude subscription", why["signin"])
        self.assertIn("no backups", why["ladder"])
        self.assertIn("no thinking effort", why["effort"])
        self.assertIn('no terminal window titled "Hermes — night_owl" is open', why["window"])
        self.assertIn("not a member of the Team Room", why["team_room"])
        self.assertEqual(s.piece("telegram").words, S.NOT_SET_UP)

    def test_no_model_and_no_settings_file(self):
        s = self.setup_of({"model": {}}, windows=set())
        self.assertEqual((s.piece("model").status, s.piece("model").words), (S.RED, S.NOT_SET_UP))
        self.assertIn("no main model", s.piece("model").why)
        bad = self.setup_of(ValueError("cannot parse config.yaml"))
        self.assertEqual((bad.pieces, bad.error), ((), "cannot parse config.yaml"))
        self.assertIn("could not be read", S.lines(bad)[0])

    def test_sign_in_says_only_what_the_files_show(self):
        cfg = {"model": {"default": "claude-sonnet-5-5", "provider": "anthropic"}}
        cases = {("ok", "dead"): (S.GREEN, "signed in"), ("dead",): (S.RED, "sign-in dead"),
                 ("exhausted",): (S.RED, "used up"), ("",): (S.GREY, "saved, not used yet")}
        for statuses, (colour, words) in cases.items():
            p = S.signin_piece(cfg, json.loads(auth_json(*statuses)), None)
            self.assertEqual((p.status, p.words), (colour, words), statuses)
        shared = S.signin_piece(cfg, None, {"providers": {"anthropic": {"x": 1}}})
        self.assertEqual((shared.status, shared.words), (S.GREY, "shared sign-in"), "found, but not confirmed for it")
        agy = {"model": {"default": "gemini-4-argon", "provider": AGY}}
        self.assertEqual(S.signin_piece({**agy, "plugins": {"enabled": [AGY]}}, None, None).status, S.GREY)
        off = S.signin_piece(agy, None, None)
        self.assertEqual((off.status, off.found), (S.RED, False))
        self.assertIn("add-on is not switched on", off.why)

    def test_the_telegram_bot_token_present_and_bot_reachable(self):
        env = self.root / ".env"
        self.assertEqual(S.telegram_piece("night_owl", env, {}).why, "this profile has no Telegram bot token")
        for text, has in ((f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n", True), (f"export TELEGRAM_BOT_TOKEN='{FAKE_TOKEN}'\n", True),
                          ("TELEGRAM_BOT_TOKEN=\n", False), ("# TELEGRAM_BOT_TOKEN=abc\n", False),
                          ("TELEGRAM_BOT_TOKEN=  # none yet\n", False), ("OTHER_TELEGRAM_BOT_TOKEN=abc\n", False)):
            env.write_text(text)
            self.assertIs(S.env_has(env, "TELEGRAM_BOT_TOKEN"), has, text)
        env.write_text(f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n")
        on = S.telegram_piece("night_owl", env, {"night_owl:telegram": {"state": "connected"}})
        self.assertEqual((on.status, on.words), (S.GREEN, "connected"))
        down = S.telegram_piece("night_owl", env, {"night_owl:telegram": {"state": "disconnected"}})
        self.assertEqual((down.status, down.words), (S.RED, "bot disconnected"))
        unseen = S.telegram_piece("night_owl", env, {"assistant:telegram": {"state": "connected"}})
        self.assertEqual((unseen.status, unseen.words), (S.RED, "token, not connected"))
        self.assertIn("not reachable yet", unseen.why)
        blind = S.telegram_piece("night_owl", env, None)
        self.assertEqual(blind.status, S.GREY, "no gateway status: never green")
        for p in (on, down, unseen, blind):
            self.assertNotIn(FAKE_TOKEN, repr(p))
            self.assertNotIn("FAKE-token", repr(p))

    def test_open_windows_reads_the_window_titles_from_herdr(self):
        got = S.open_windows(fake_herdr("Hermes — night_owl", "Hermes - wanna-buffet", "zsh", "Codex (Dolly)"))
        self.assertEqual(got, {"night_owl", "wanna-buffet"})
        self.assertEqual(S.open_windows(fake_herdr()), set())
        self.assertIsInstance(S.open_windows(str(self.root / "no-such-herdr")), str, "herdr missing: a reason")
        self.assertIsInstance(S.open_windows(None), str)
        p = S.window_piece("night_owl", "herdr could not be asked (FileNotFoundError)")
        self.assertEqual((p.status, p.found), (S.GREY, False), "could not be checked is never green")

    def test_kind(self):
        self.assertEqual(S.kind_of({"profile": "x"}), "hermes")
        self.assertEqual(S.kind_of({"target": "aider"}), "cli")
        self.assertEqual(S.kind_of({"profile": "x", "kind": "CLI"}), "cli")
        self.assertTrue(S.checked({"profile": "x", "kind": "hermes"}))
        self.assertFalse(S.checked({"profile": "x"}), "a block from before has no kind line: it looks as it did")
        self.assertFalse(S.checked({"target": "aider", "kind": "hermes"}), "hermes without a profile")


# ---- the column on the desk ---------------------------------------------------------------------------------------
class TheColumn(unittest.TestCase):
    def snap(self, w) -> D.Snapshot:
        return D.collect(w.paths, yaml_loader=F.lines_loader, now=w.now, desk=E.record(w.edit))

    def test_n_writes_kind_hermes_and_reads_nothing_else_into_the_list(self):
        w = owl_world()
        profile_before = F.tree_hashes(w.profiles)
        add_owl(w)
        raw = A.load(w.edit.voice.toml)["agents"]["night_owl"]
        self.assertEqual(raw, {"profile": "night_owl", "kind": "hermes", "display": "Night Owl",
                               "names": ["night owl", "owl"]})
        self.assertEqual(F.tree_hashes(w.profiles), profile_before, "nothing in any profile was changed")

    def test_the_new_column_fills_from_real_looking_profile_files(self):
        w = owl_world()
        add_owl(w)
        v = owl(self.snap(w))
        self.assertEqual((v.kind, v.voice_key, v.name), ("hermes", "night_owl", "Night Owl"))
        self.assertIsNotNone(v.setup)
        self.assertEqual(v.setup.missing, [])
        self.assertEqual(cell("model", v).words, "Claude Sonnet 5.5")
        self.assertEqual((cell("effort", v).status, cell("effort", v).words), (D.GREEN, "medium"))
        signin = cell("signin", v)
        self.assertEqual((signin.status, signin.words), (D.GREEN, "signed in"))
        self.assertIn("own files", " ".join(signin.detail))
        self.assertEqual(cell("rung:ag_claude", v).status, D.GREEN)
        self.assertEqual(cell("rung:codex", v).status, D.GREEN)
        nv = cell("rung:nvidia", v)
        self.assertEqual(nv.status, D.GREY, "an empty rung of a filled ladder stays a plain grey off")
        self.assertTrue(nv.words.startswith("off"), nv.words)
        tg = cell("presence:tg_private", v)
        self.assertEqual((tg.status, tg.words), (D.GREEN, "connected"))
        term = cell("presence:terminal", v)
        self.assertEqual((term.status, term.words), (D.GREEN, "open"))
        self.assertTrue(term.detail[0].startswith(acd.NOT_SWITCH_HEAD), "still says it is not the voice switch")
        self.assertEqual(cell("presence:team_room", v).words, "in")
        self.assertEqual(cell("presence:iphone_voice", v).status, D.GREEN)
        names = cell("names", v)
        self.assertEqual((names.words, names.setting), ("night owl +1 more", "wire:*"))
        story = acd.column_story(self.snap(w), v)
        self.assertNotIn(FAKE_TOKEN, story)
        self.assertNotIn("FAKE-ACCESS", story)

    def test_a_bare_profile_shows_red_not_set_up_with_the_reason_and_no_unearned_green(self):
        w = owl_world(full=False)
        add_owl(w)
        snap = self.snap(w)
        v = owl(snap)
        expect = {"effort": "no thinking effort", "signin": "no saved sign-in for the Claude subscription",
                  "rung:ag_claude": "no backups", "rung:nvidia": "no backups",
                  "presence:tg_private": "this profile has no Telegram bot token",
                  "presence:terminal": "no terminal window titled", "presence:iphone_voice": "no Siri shortcut"}
        for key, words in expect.items():
            c = cell(key, v)
            self.assertEqual(c.status, D.RED, key)
            self.assertIn(words, " ".join(c.detail), key)
        self.assertEqual(cell("presence:tg_private", v).words, S.NOT_SET_UP)
        self.assertEqual(cell("presence:terminal", v).words, "no window open")
        out = cell("presence:team_room", v)
        self.assertEqual((out.status, out.words, out.setting), (D.RED, "out", "team_room"),
                         "red, and the same one switch as everyone's")
        self.assertIn("not a member of the Team Room", " ".join(out.detail))
        for key in ("effort", "rung:codex", "voice", "voice_zh", "speed"):
            self.assertTrue(cell(key, v).setting, f"{key} can still be changed from the desk")
        story = acd.column_story(snap, v)
        self.assertIn("Telegram bot: not set up: this profile has no Telegram bot token.", story)
        self.assertIn("nothing in them was changed", story)

    def test_a_silent_agent_in_the_list_has_open_voice_cells(self):
        w = owl_world()
        add_owl(w, voice=None)
        v = owl(self.snap(w))
        self.assertIsNone(v.voice)
        for key in ("voice", "voice_zh"):
            c = cell(key, v)
            self.assertEqual((c.status, c.words, c.setting), (D.GREY, "silent", key))
        self.assertEqual(cell("speed", v).setting, "speed")

    def test_the_window_row_says_what_herdr_shows_even_when_the_log_had_a_terminal_reply(self):
        w = owl_world()
        w.paths = dataclasses.replace(w.paths, windows=lambda: {"orchestrator"})
        F.log(w, "night_owl", F.turn(w.now - 600, "s1", "claude-sonnet-5-5", "anthropic"))
        add_owl(w)
        c = cell("presence:terminal", owl(self.snap(w)))
        self.assertEqual((c.status, c.words), (D.RED, "no window open"))
        self.assertIn("a terminal reply was logged", " ".join(c.detail), "the log's line stays, below it")
        w.paths = dataclasses.replace(w.paths, windows=lambda: "herdr could not be asked (TimeoutExpired)")
        c = cell("presence:terminal", owl(self.snap(w)))
        self.assertEqual((c.status, c.words), (D.GREEN, "on"), "herdr not asked: the log's answer stands")

    def test_only_a_block_that_says_kind_hermes_gets_the_check(self):
        w = owl_world()
        add_owl(w)
        snap = self.snap(w)
        for v in snap.agents:
            if v.profile != "night_owl":
                self.assertIsNone(v.setup, v.profile)
                self.assertEqual(cell("names", v).words, "", "the other columns look as before")
        jarvis = next(v for v in snap.agents if v.profile == "orchestrator")
        self.assertEqual(jarvis.kind, "hermes", "read from its profile line, no kind line needed")

    def test_the_windows_are_asked_once_and_only_when_a_column_needs_them(self):
        w = owl_world()
        asked = []
        w.paths = dataclasses.replace(w.paths, windows=lambda: asked.append(1) or {"night_owl"})
        self.snap(w)
        self.assertEqual(asked, [], "no kind = hermes column: herdr is not asked")
        add_owl(w)
        make_profile(w.profiles, "lark", BARE_CONFIG)
        self.assertTrue(P.add_agent(w.edit, "Lark", "", None, profile="lark", now=WHEN).ok)
        snap = self.snap(w)
        self.assertEqual(asked, [1], "one look for both new columns")
        self.assertEqual(cell("presence:terminal", next(v for v in snap.agents if v.profile == "lark")).status, D.RED)

    def test_the_report_after_n_lists_what_was_found_and_what_is_missing(self):
        w = owl_world(full=False)
        r = P.setup_report(w.edit, "night_owl", "Night Owl", loader=F.lines_loader)
        self.assertTrue(r.ok)
        self.assertIn("nothing in it was changed", r.message)
        self.assertIn("not set up or not working yet:", r.message)
        self.assertIn("telegram bot", r.message)
        text = "\n".join(r.lines)
        self.assertIn("- Telegram bot: not set up: this profile has no Telegram bot token", text)
        self.assertIn("+ Model: claude-sonnet-5-5", text)
        full = owl_world()
        r2 = P.setup_report(full.edit, "night_owl", "Night Owl", loader=F.lines_loader)
        self.assertIn("nothing the desk checks is missing", r2.message)
        self.assertNotIn(FAKE_TOKEN, r2.message + "\n".join(r2.lines))


# ---- the real desk, driven headless -------------------------------------------------------------------------------
def body(app) -> str:
    out = []
    for name in ("box-title", "box-note", "box-text", "box-body", "box-confirm", "box-keys"):
        for wdg in app.screen.query(f"#{name}"):
            out.append(str(wdg.render()))
    return "\n".join(out)


class OnTheDesk(unittest.IsolatedAsyncioTestCase):
    async def walk_n(self, app, pilot, voice_token=None, typed=None):
        await pilot.press("n")
        await pilot.pause()
        tokens = [tok for tok, _ in app.screen.rows]
        app.screen.query_one("#box-list", OptionList).highlighted = tokens.index("night_owl")
        await pilot.press("enter")
        await pilot.pause()
        self.assertEqual(app.screen.query_one("#box-input", Input).value, "Night Owl", "its Hermes name is offered")
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#box-input", Input).value = "night owl, owl"
        await pilot.press("enter")
        await pilot.pause()
        rows = [tok for tok, _ in app.screen.rows]
        if voice_token is not None:
            app.screen.query_one("#box-list", OptionList).highlighted = rows.index(voice_token)
        await pilot.press("enter")
        await pilot.pause()
        if typed is not None:
            self.assertIsInstance(app.screen, acd.AskPopup)
            app.screen.query_one("#box-input", Input).value = typed
            await pilot.press("enter")
            await pilot.pause()
        return rows

    async def test_n_fills_the_column_then_t_c_d_and_u_work_on_it(self):
        w = owl_world(full=False)
        app = make_app(w)
        async with app.run_test(size=(220, 60)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            self.assertIsNone(owl(app.snapshot).setup, "before n: a profile column as it always was")
            rows = await self.walk_n(app, pilot, voice_token=acd.SILENT)
            kokoro = [i for i, t in enumerate(rows) if A.is_kokoro(t)]
            edge = [i for i, t in enumerate(rows) if t.endswith("Neural")]
            self.assertTrue(kokoro and edge and max(kokoro) < min(edge), "Kokoro first, Microsoft below")
            self.assertEqual(rows[-2:], [acd.SILENT, E.OTHER_VOICE], "then silent, then other, type it")
            words = body(app)
            self.assertIn("Add Night Owl?", words)
            self.assertIn("kind            hermes", words)
            self.assertIn("fills its column", words)
            await pilot.press("y")
            await settle(app, pilot)
            added, report = app.last_results
            self.assertTrue(added.ok and not added.dry, added.message)
            self.assertIn("not set up or not working yet", report.message)
            self.assertIn("- Telegram bot: not set up: this profile has no Telegram bot token", body(app))
            await pilot.press("escape")
            await pilot.pause()
            await show(app, pilot)
            v = owl(app.snapshot)
            self.assertIsNotNone(v.setup)
            screen = app.screen
            self.assertTrue(screen.go("presence:tg_private", "Night Owl"))
            await pilot.pause()
            row, col, c = screen.at()
            self.assertEqual((c.status, c.words), (D.RED, S.NOT_SET_UP))
            strip = str(screen.query_one("#strip").render())
            self.assertIn("this profile has no Telegram bot token", strip)
            self.assertIn("not set up", acd.cell_text(c).plain)

            # the voice cell opens the same full pick-list as everyone's
            await open_cell(app, pilot, "voice", "Night Owl")
            self.assertIsInstance(app.screen, acd.CellMenu)
            listed = menu_list(app)
            self.assertIn("silent", listed)
            self.assertIn("other, type it", listed)
            await pilot.press("escape")
            await pilot.pause()

            # t: the voice test offers the new agent by itself
            self.assertTrue(app.screen.go("voice", "Night Owl"))
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            self.assertIn(("night_owl", "only Night Owl"), app.screen.rows)
            await pilot.press("escape")
            await pilot.pause()

            # c: read again, the column is still filled
            app.action_reread()
            await settle(app, pilot)
            self.assertIsNotNone(owl(app.snapshot).setup)

            # d takes it out of the Voice Desk list only; u puts it back
            toml = Path(w.edit.voice.toml)
            with_owl = toml.read_text(encoding="utf-8")
            profile_before = F.tree_hashes(w.profiles / "night_owl")
            self.assertTrue(app.screen.go("model", "Night Owl"))
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            self.assertEqual(app.screen.rows[0][0], "night_owl", "the chosen column is offered first")
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("Its Hermes profile night_owl", body(app))
            await pilot.press("y")
            await settle(app, pilot)
            self.assertNotIn("[agents.night_owl]", toml.read_text(encoding="utf-8"))
            self.assertEqual(F.tree_hashes(w.profiles / "night_owl"), profile_before, "the profile is untouched")
            await pilot.press("escape")
            await pilot.pause()
            await show(app, pilot)
            self.assertIsNone(owl(app.snapshot).setup, "out of the list: no set-up check")
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
            self.assertEqual(toml.read_text(encoding="utf-8"), with_owl, "u put the block back byte for byte")
            await pilot.press("escape")
            await pilot.pause()
            await show(app, pilot)
            self.assertIsNotNone(owl(app.snapshot).setup)

    async def test_other_type_it_takes_a_voice_name_checked_like_every_other_edit(self):
        w = owl_world()
        app = make_app(w)
        async with app.run_test(size=(220, 60)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await self.walk_n(app, pilot, voice_token=E.OTHER_VOICE, typed="zh-TW-HsiaoChenNeural")
            self.assertIn("Not that voice", body(app), "a Chinese voice is refused for English")
            await pilot.press("escape")
            await pilot.pause()
            await self.walk_n(app, pilot, voice_token=E.OTHER_VOICE, typed="en-US-AvaNeural")
            self.assertIn("Add Night Owl?", body(app))
            await pilot.press("y")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].ok, app.last_results[0].message)
            raw = A.load(w.edit.voice.toml)["agents"]["night_owl"]
            self.assertEqual((raw["voice"], raw["kind"]), ("en-US-AvaNeural", "hermes"))

    async def test_a_dry_run_n_writes_nothing_but_still_reports_what_it_found(self):
        w = owl_world(armed=False, full=False)
        before = F.tree_hashes(w.root)
        app = make_app(w)
        async with app.run_test(size=(220, 60)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await self.walk_n(app, pilot, voice_token=acd.SILENT)
            await pilot.press("y")
            await settle(app, pilot)
            added, report = app.last_results
            self.assertTrue(added.ok and added.dry, added.message)
            self.assertIn('+ kind = "hermes"', "\n".join(added.lines))
            self.assertIn("this profile has no Telegram bot token", "\n".join(report.lines))
        self.assertEqual(F.tree_hashes(w.root), before)


if __name__ == "__main__":
    unittest.main()
