"""Offline tests: Telegram group rows, added by hand (the brief of 2026-11-19, card 2).

Peter: "Manually adding additional row of telegram group ... click individual cell ... type in the telegram ID ...
press enter ... sends the scan to the telegram and check if those particular agents are actually being added to
that group." One agent in three groups = three group rows for everyone; the others are blank in the extra rows.

    g                 a group row: its name and its ID typed once; a group the logs have seen is offered (never
                      added by itself). Enter asks Telegram (getChatMember, each agent's OWN bot) who is in it.
    a cell            green member and connected, red not a member / not confirmed (the reason, which bot to add,
                      then c), blank marked "not in this group"; plus the agent's voice mode in that group
                      (Off / Voice-back / Always), which is separate from the light
    the row's name    check again, mark it as the Team Room's group (the old row moves into it, every setting kept),
                      take it off the desk (no bot leaves any group)
    u                 every change goes back exactly

Nothing real is contacted: Telegram is a fake handed to the desk, urllib's urlopen is replaced by a tripwire for the
whole module, and every token is made up (and checked never to show up anywhere).

    .venv/bin/python -m unittest tests.test_acd_groups -v
"""
import dataclasses
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import acd_edit as E  # noqa: E402
import acd_groups as TG  # noqa: E402
import gridstate as G  # noqa: E402
from tests.test_acd_table import make_app, menu_list, pick, settle, show  # noqa: E402
from textual.widgets import Input, OptionList  # noqa: E402

WHEN = dt.datetime(2026, 11, 19, 12, 0, 0)
FAMILY = "-1001000000001"          # made-up group IDs: this repository is public
WORK = "-1001000000002"
TEAM = "-1001000000003"
BOT = {"orchestrator": "1000001", "assistant": "1000002", "wanna-buffet": "1000004", "simba": "1000005"}
TOKENS = {p: f"{n}:fake-{p}-not-a-real-token" for p, n in BOT.items()}       # tax_angel_esther has no bot token
MEMBER = {"ok": True, "result": {"status": "member", "user": {"id": 1, "is_bot": True, "username": "fake_bot"}}}
NOT_FOUND = {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}


def tripwire(*_a, **_k):
    raise AssertionError("a test tried to reach the real Telegram (urllib.request.urlopen)")


_PATCH = mock.patch.object(urllib.request, "urlopen", tripwire)


def setUpModule():
    _PATCH.start()


def tearDownModule():
    _PATCH.stop()


class FakeTelegram:
    """Telegram, as the desk's asker sees it. answers: (group ID, bot ID) -> an answer dict, or an exception to raise.
    Anything not listed: "chat not found". It records what was asked, never the token."""

    def __init__(self, answers: dict | None = None) -> None:
        self.answers = dict(answers or {})
        self.calls: list = []
        self.tokens_seen: list = []

    def __call__(self, token: str, method: str, params: dict) -> dict:
        assert method in TG.ALLOWED, method
        self.calls.append((method, dict(params)))
        self.tokens_seen.append(token)
        got = self.answers.get((params["chat_id"], params["user_id"]), NOT_FOUND)
        if isinstance(got, Exception):
            raise got
        return got


def member(username: str = "fake_bot", status: str = "member", **more) -> dict:
    return {"ok": True, "result": {"status": status, "user": {"id": 1, "is_bot": True, "username": username}, **more}}


def world(armed: bool = True):
    """The desk's usual fake world with the voice grid, a made-up bot token for four agents (Esther has none), and
    the desk told where its own group list lives."""
    w = F.with_grid(F.edit_world(armed=armed))
    for p, tok in TOKENS.items():
        (w.profiles / p).mkdir(parents=True, exist_ok=True)
        (w.profiles / p / ".env").write_text("\n".join(["# made up for a test", "TELEGRAM_BOT_TOKEN=" + tok]) + "\n")
    w.paths = dataclasses.replace(w.paths, groups=TG.groups_file(w.edit))
    return w


def collect(w) -> D.Snapshot:
    return F.edit_collect(w)


def no_token_anywhere(test, *texts):
    for text in texts:
        for tok in TOKENS.values():
            test.assertNotIn(tok, text)
            test.assertNotIn(tok.split(":", 1)[1], text)


def screen_text(app) -> str:
    out = []
    for screen in app.screen_stack:
        for wdg in screen.query("Static"):
            out.append(str(wdg.render()))
    return "\n".join(out)


# ---- asking Telegram ------------------------------------------------------------------------------------------------
class AskingTelegram(unittest.TestCase):
    def test_each_answer_has_its_own_colour_word_and_reason(self):
        tok = TOKENS["orchestrator"]
        cases = {
            "member": (member(), TG.MEMBER, "member"),
            "admin": (member(status="administrator"), TG.MEMBER, "member"),
            "restricted but in": (member(status="restricted", is_member=True), TG.MEMBER, "member"),
            "left": (member(status="left"), TG.NOT_MEMBER, "not a member"),
            "kicked": (member(status="kicked"), TG.NOT_MEMBER, "not a member"),
            "chat not found": (NOT_FOUND, TG.NOT_MEMBER, "not a member"),
            "forbidden": ({"ok": False, "error_code": 403, "description": "Forbidden: bot is not a member of the "
                                                                          "supergroup chat"}, TG.NOT_MEMBER, "not a member"),
            "unauthorized": ({"ok": False, "error_code": 401, "description": "Unauthorized"}, TG.ERROR, "not confirmed"),
            "slow down": ({"ok": False, "error_code": 429, "description": "Too Many Requests"}, TG.ERROR, "not confirmed"),
            "odd": ({"ok": False, "error_code": 500, "description": f"Internal error at bot{tok}/getChatMember"},
                    TG.ERROR, "not confirmed"),
        }
        for label, (answer, state, words) in cases.items():
            fake = FakeTelegram({(FAMILY, "1000001"): answer})
            c = TG.check_one(tok, FAMILY, fake)
            self.assertEqual((c.state, c.words), (state, words), label)
            self.assertEqual(fake.calls, [("getChatMember", {"chat_id": FAMILY, "user_id": "1000001"})], label)
            no_token_anywhere(self, c.why, c.words)
        self.assertIn("chat not found", TG.check_one(tok, FAMILY, FakeTelegram()).why)
        self.assertEqual(TG.check_one(tok, FAMILY, FakeTelegram({(FAMILY, "1000001"): member("jarvis_fake_bot")})).bot,
                         "jarvis_fake_bot")

    def test_no_token_no_network_and_no_asker(self):
        down = FakeTelegram({(FAMILY, "1000001"): TG.TgUnreachable("could not reach Telegram (timed out)")})
        c = TG.check_one(TOKENS["orchestrator"], FAMILY, down)
        self.assertEqual((c.state, c.words), (TG.ERROR, "not confirmed"))
        self.assertIn("could not reach Telegram", c.why)
        boom = FakeTelegram({(FAMILY, "1000001"): RuntimeError(f"bad {TOKENS['orchestrator']}")})
        c = TG.check_one(TOKENS["orchestrator"], FAMILY, boom)
        self.assertEqual(c.state, TG.ERROR)
        no_token_anywhere(self, c.why)
        none = FakeTelegram()
        self.assertEqual(TG.check_one("", FAMILY, none).state, TG.NO_TOKEN)
        self.assertEqual(TG.check_one(None, FAMILY, none).state, TG.ERROR)
        self.assertEqual(TG.check_one("not-a-token", FAMILY, none).state, TG.ERROR)
        self.assertEqual(none.calls, [], "no question is asked without a real-looking token")
        self.assertEqual(TG.check_one(TOKENS["orchestrator"], FAMILY, None).state, TG.NO_TELEGRAM)

    def test_the_asker_only_ever_asks_getchatmember_or_getchat_and_scrubs_the_token(self):
        tok = TOKENS["orchestrator"]
        for method in ("getUpdates", "sendMessage", "leaveChat", "deleteWebhook"):
            with self.assertRaises(ValueError):
                TG.telegram_ask(tok, method, {})
        seen = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            seen.append((req.full_url.rsplit("/", 1)[1], req.get_method(), req.data.decode(), timeout))
            return Answer(json.dumps(MEMBER).encode())

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            got = TG.telegram_ask(tok, "getChatMember", {"chat_id": FAMILY, "user_id": "1000001"})
        self.assertTrue(got["ok"])
        self.assertEqual(seen, [("getChatMember", "POST", f"chat_id={FAMILY}&user_id=1000001", TG.TIMEOUT)])

        def refused(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                         io.BytesIO(json.dumps(NOT_FOUND).encode()))
        with mock.patch.object(urllib.request, "urlopen", refused):
            self.assertEqual(TG.telegram_ask(tok, "getChatMember", {"chat_id": FAMILY, "user_id": "1"}), NOT_FOUND)

        def offline(req, timeout=0):
            raise urllib.error.URLError(f"no route for {req.full_url}")
        with mock.patch.object(urllib.request, "urlopen", offline):
            with self.assertRaises(TG.TgUnreachable) as got_err:
                TG.telegram_ask(tok, "getChatMember", {"chat_id": FAMILY, "user_id": "1"})
        no_token_anywhere(self, str(got_err.exception))

    def test_a_token_is_read_only_for_the_call(self):
        w = world()
        self.assertEqual(TG.bot_token(w.profiles, "orchestrator"), TOKENS["orchestrator"])
        self.assertEqual(TG.bot_token(w.profiles, "tax_angel_esther"), "")
        got = TG.check_group(FAMILY, ["orchestrator", "tax_angel_esther"], w.profiles,
                             FakeTelegram({(FAMILY, "1000001"): MEMBER}))
        self.assertEqual({p: c.state for p, c in got.items()},
                         {"orchestrator": TG.MEMBER, "tax_angel_esther": TG.NO_TOKEN})
        no_token_anywhere(self, json.dumps({p: dataclasses.asdict(c) for p, c in got.items()}))


# ---- the rows: the desk's own file, the safe path, undo -------------------------------------------------------------
class TheRows(unittest.TestCase):
    def test_ids_and_names_are_checked_before_anything(self):
        self.assertEqual(TG.check_id(" -100 1000 000001 "), (FAMILY, ""))
        self.assertIn("a person's chat", TG.check_id("1000000001")[1])
        self.assertIn("not a Telegram group ID", TG.check_id("family")[1])
        self.assertEqual(TG.check_id("")[1], "No ID was typed.")
        self.assertIn("at most", TG.check_name("x" * 41)[1])

    def test_add_mark_team_remove_and_undo_each_through_the_safe_path(self):
        w = world()
        path = TG.groups_file(w.edit)
        r = TG.add_group(w.edit, FAMILY, "Family", {"orchestrator": True, "assistant": False}, now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(r.entry["kind"], "tg_groups")
        one = path.read_bytes()
        self.assertEqual(TG.read_groups(path)[0], [TG.Group(FAMILY, "Family", False,
                                                            {"orchestrator": True, "assistant": False})])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600, "the group list is Peter's own")
        self.assertFalse(TG.add_group(w.edit, FAMILY, "Again", {}, now=WHEN).ok, "one row per group")
        self.assertTrue(TG.add_group(w.edit, WORK, "Work", {"assistant": True}, now=WHEN).ok)
        two = path.read_bytes()
        self.assertTrue(TG.set_mark(w.edit, WORK, "orchestrator", True, now=WHEN).ok)
        self.assertTrue(TG.set_team(w.edit, WORK, True, now=WHEN).ok)
        refused = TG.set_team(w.edit, FAMILY, True, now=WHEN)
        self.assertFalse(refused.ok)
        self.assertIn("already the Team Room's group", refused.message)
        self.assertTrue(TG.remove_group(w.edit, FAMILY, now=WHEN).ok)
        self.assertEqual([g.id for g in TG.read_groups(path)[0]], [WORK])
        backups = list((w.edit.state / "group-backups").iterdir())
        self.assertTrue(backups, "a backup is kept before every change")
        for _ in range(3):                               # remove, team, mark: each undone in turn
            self.assertTrue(E.undo(w.edit).ok)
        self.assertEqual(path.read_bytes(), two, "u puts the rows back byte for byte")
        self.assertTrue(E.undo(w.edit).ok)
        self.assertEqual(path.read_bytes(), one)
        self.assertTrue(E.undo(w.edit).ok)
        self.assertFalse(path.exists(), "undoing the first row takes the file away again")

    def test_a_dry_run_writes_nothing(self):
        w = world(armed=False)
        before = F.tree_hashes(w.root)
        r = TG.add_group(w.edit, FAMILY, "Family", {"orchestrator": True}, now=WHEN)
        self.assertTrue(r.ok and r.dry)
        self.assertIn("+ Family (" + FAMILY + "): in it: orchestrator", r.lines)
        self.assertEqual(F.tree_hashes(w.root), before)

    def test_undo_never_writes_over_a_change_it_did_not_make(self):
        w = world()
        TG.add_group(w.edit, FAMILY, "Family", {"orchestrator": True}, now=WHEN)
        path = TG.groups_file(w.edit)
        path.write_text(path.read_text().replace("Family", "Changed by hand"))
        got = E.undo(w.edit)
        self.assertFalse(got.ok)
        self.assertIn("changed since", got.message)
        self.assertIn("Changed by hand", path.read_text())

    def test_a_broken_list_is_said_and_never_written_over(self):
        w = world()
        path = TG.groups_file(w.edit)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json")
        self.assertFalse(TG.add_group(w.edit, FAMILY, "Family", {}, now=WHEN).ok)
        self.assertEqual(path.read_text(), "{ not json")
        snap = collect(w)
        self.assertIn("the desk's Telegram group list is not readable JSON", [x.text for x in snap.warnings])

    def test_the_repository_ships_made_up_examples_only(self):
        example = Path(__file__).resolve().parents[1] / "docs" / "tg_groups.example.json"
        groups = TG.parse(json.loads(example.read_text(encoding="utf-8")))
        self.assertTrue(groups)
        for g in groups:
            self.assertRegex(g.id, r"^-100100000000\d$", "a made-up ID")

    def test_group_ids_the_logs_have_seen_are_offered_never_added(self):
        w = world()
        F.log(w, "orchestrator",
              F.stamp(w.now - 300) + f" INFO gateway.run: inbound message: platform=telegram user=x chat={WORK} msg='hi'",
              F.stamp(w.now - 200) + " INFO gateway.run: inbound message: platform=telegram user=x chat=1000000001 msg='dm'",
              F.stamp(w.now - 100) + f" INFO gateway.run: pre_gateway_dispatch skip: reason=x platform=telegram "
                                     f"chat={FAMILY}")
        seen = TG.seen_groups(w.profiles, ["orchestrator", "assistant"], set())
        self.assertEqual([s[0] for s in seen], [FAMILY, WORK], "newest first, private chats left out")
        self.assertEqual(TG.seen_groups(w.profiles, ["orchestrator"], {FAMILY}), [seen[1]])
        self.assertFalse(TG.groups_file(w.edit).exists(), "nothing is added by itself")


# ---- what the desk shows ---------------------------------------------------------------------------------------------
class TheCells(unittest.TestCase):
    def setUp(self):
        self.w = world()
        TG.add_group(self.w.edit, FAMILY, "Family", {"orchestrator": True, "assistant": True, "wanna-buffet": True,
                                                     "tax_angel_esther": True, "simba": False}, now=WHEN)
        (self.w.grid.hermes_home / "gateway_voice_mode.json").write_text(json.dumps(
            {f"orchestrator:telegram:{FAMILY}": "all", f"assistant:telegram:{FAMILY}": "voice_only"}))
        self.snap = collect(self.w)

    def cells(self, checks: dict) -> dict:
        g = dataclasses.replace(self.snap.groups[0], checks=checks)
        row = next(r for r in acd.rows_of(dataclasses.replace(self.snap, groups=(g,)))
                   if r.key == acd.group_key(FAMILY))
        return {v.profile: acd.cell_of(row, v) for v in self.snap.agents} | \
            {o.name: acd.cell_of(row, o) for o in self.snap.others}

    def test_the_row_sits_under_the_team_group_row_with_each_agents_mode(self):
        keys = [r.key for r in acd.rows_of(self.snap)]
        self.assertEqual(keys[keys.index("presence:tg_group") + 1], acd.group_key(FAMILY))
        g = self.snap.groups[0]
        self.assertEqual((g.name, g.team), ("Family", False))
        self.assertEqual({p: c.value for p, c in g.modes.items() if p in ("orchestrator", "assistant", "simba")},
                         {"orchestrator": G.ALWAYS, "assistant": G.VOICE_BACK, "simba": G.OFF})
        self.assertEqual((g.connected["orchestrator"], g.connected["wanna-buffet"]), (True, False))

    def test_every_colour(self):
        now = WHEN.timestamp()
        c = self.cells({"orchestrator": TG.Check(TG.MEMBER, "member", "Telegram says its bot is a member", now),
                        "assistant": TG.Check(TG.NOT_MEMBER, "not a member", "Telegram says this bot cannot see that "
                                              "group (chat not found)", now, "kylie_fake_bot"),
                        "wanna-buffet": TG.Check(TG.MEMBER, "member", "Telegram says its bot is a member", now),
                        "tax_angel_esther": TG.Check(TG.NO_TOKEN, "no bot", "this agent has no Telegram bot token",
                                                     now)})
        self.assertEqual((c["orchestrator"].status, c["orchestrator"].words), (D.GREEN, "member · Always"))
        self.assertEqual((c["assistant"].status, c["assistant"].words), (D.RED, "not a member · Voice-back"))
        hint = " ".join(c["assistant"].detail)
        self.assertIn("add Kylie's bot @kylie_fake_bot", hint)
        self.assertIn("Then press c to check again", hint)
        self.assertEqual((c["wanna-buffet"].status, c["wanna-buffet"].words[:21]), (D.RED, "member, not connected"),
                         "in the group, but the gateway has not connected its bot: not earned green")
        self.assertEqual(c["tax_angel_esther"].status, D.RED)
        self.assertIn("needs a Telegram bot of its own", " ".join(c["tax_angel_esther"].detail))
        simba = c["simba"]
        self.assertEqual((simba.status, simba.words, simba.setting), (None, "", acd.group_key(FAMILY)),
                         "marked not in this group: blank, and Enter still opens it")
        self.assertEqual(acd.cell_text(simba).plain, "")
        for name in ("Claude", "Gemini", "Codex"):
            self.assertTrue(c[name].blank and not c[name].setting, "no Telegram bot of their own")
        unchecked = self.cells({})
        self.assertEqual((unchecked["orchestrator"].status, unchecked["orchestrator"].short), (D.GREY, "not checked"))
        err = self.cells({"orchestrator": TG.Check(TG.ERROR, "not confirmed", "not confirmed: could not reach "
                                                   "Telegram (timed out)", now)})
        self.assertEqual(err["orchestrator"].status, D.RED)
        self.assertIn("Press c to check again", " ".join(err["orchestrator"].detail))
        greens = [p for p, cell in c.items() if cell.status == D.GREEN]
        self.assertEqual(greens, ["orchestrator"], "green only where Telegram AND the gateway both said so")

    def test_the_mode_switch_writes_the_gateways_own_per_group_mode_and_u_puts_it_back(self):
        mode_file = self.w.grid.hermes_home / "gateway_voice_mode.json"
        before = mode_file.read_bytes()
        r = TG.set_mode(self.w.edit, FAMILY, "orchestrator", G.OFF, name="Jarvis", group="Family", now=WHEN)
        self.assertTrue(r.ok and not r.dry, r.message)
        self.assertEqual(json.loads(mode_file.read_text())[f"orchestrator:telegram:{FAMILY}"], "off")
        self.assertEqual(json.loads(mode_file.read_text())[f"assistant:telegram:{FAMILY}"], "voice_only")
        self.assertTrue(E.undo(self.w.edit).ok)
        self.assertEqual(json.loads(mode_file.read_bytes()), json.loads(before))
        refused = TG.set_mode(self.w.edit, FAMILY, "simba", G.ALWAYS, now=WHEN)
        self.assertFalse(refused.ok, "Simba's bot is her own: shown, never switched")

    def test_a_first_mode_creates_the_file_and_its_undo_takes_it_away(self):
        mode_file = self.w.grid.hermes_home / "gateway_voice_mode.json"
        mode_file.unlink()
        self.assertTrue(TG.set_mode(self.w.edit, WORK, "assistant", G.ALWAYS, now=WHEN).ok)
        self.assertEqual(json.loads(mode_file.read_text()), {f"assistant:telegram:{WORK}": "all"})
        self.assertTrue(E.undo(self.w.edit).ok)
        self.assertFalse(mode_file.exists())

    def test_converting_the_team_group_keeps_every_setting(self):
        mirror = Path(self.w.grid.mirror_config)
        before = mirror.read_bytes()
        rows = [r.key for r in acd.rows_of(self.snap)]
        self.assertIn("vplace:tg_group", rows, "the old row keeps working until it is converted")
        old = {v.profile: acd.cell_of(acd.Row("vplace:tg_group", "x"), v).words for v in self.snap.agents}
        self.assertTrue(TG.set_team(self.w.edit, FAMILY, True, now=WHEN).ok)
        snap = collect(self.w)
        self.assertNotIn("vplace:tg_group", [r.key for r in acd.rows_of(snap)], "it moved into the group row")
        row = next(r for r in acd.rows_of(snap) if r.key == acd.group_key(FAMILY))
        self.assertEqual(row.label, "Telegram group: Family (Team Room)")
        for v in snap.agents:
            if snap.groups[0].marks.get(v.profile) and old[v.profile]:
                self.assertTrue(acd.cell_of(row, v).words.endswith(acd.MODE_SPLIT + old[v.profile].split(",")[0]),
                                (v.profile, acd.cell_of(row, v).words, old[v.profile]))
        self.assertEqual(mirror.read_bytes(), before, "conversion writes only the desk's own mark")
        self.assertTrue(E.undo(self.w.edit).ok)
        self.assertIn("vplace:tg_group", [r.key for r in acd.rows_of(collect(self.w))])


# ---- the real screen, driven headless --------------------------------------------------------------------------------
class OnTheDesk(unittest.IsolatedAsyncioTestCase):
    async def type_in(self, app, pilot, text: str) -> None:
        self.assertIsInstance(app.screen, acd.AskPopup)
        app.screen.query_one("#box-input", Input).value = text
        await pilot.press("enter")
        await pilot.pause()

    async def test_g_adds_a_row_telegram_is_asked_the_cells_light_and_u_takes_it_away(self):
        w = world()
        fake = FakeTelegram({(FAMILY, BOT["orchestrator"]): member("jarvis_fake_bot"),
                             (FAMILY, BOT["simba"]): member("simba_fake_bot")})
        app = make_app(w, telegram=fake)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("g")
            await pilot.pause()
            await self.type_in(app, pilot, "Family")
            await self.type_in(app, pilot, "1000000001")
            self.assertIn("a person's chat", screen_text(app), "a private chat is refused, plainly")
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            await self.type_in(app, pilot, "Family")
            await self.type_in(app, pilot, FAMILY)
            await settle(app, pilot)
            self.assertIsInstance(app.screen, acd.PickPopup)
            said = screen_text(app)
            self.assertIn("Jarvis: member", said)
            self.assertIn("Kylie: not a member", said)
            self.assertIn("Esther: no bot", said)
            self.assertIn("ARMED", said)
            self.assertEqual({c[1]["user_id"] for c in fake.calls}, set(BOT.values()), "each agent's OWN bot")
            self.assertEqual({c[0] for c in fake.calls}, {"getChatMember"})
            app.screen.query_one("#box-list", OptionList).highlighted = 0           # the agents found in it
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].ok and not app.last_results[0].dry, app.last_results[0].message)
            await pilot.press("escape")
            await pilot.pause()
            await show(app, pilot)
            key = acd.group_key(FAMILY)
            screen = app.screen
            self.assertIn(key, [r.key for r in screen.rows])
            self.assertTrue(screen.go(key, "Jarvis"))
            await pilot.pause()
            self.assertEqual(screen.at()[2].status, D.GREEN)
            self.assertTrue(screen.go(key, "Simba"))
            await pilot.pause()
            self.assertEqual(screen.at()[2].status, D.RED, "her bot is in, but the gateway has not connected it")
            self.assertTrue(screen.go(key, "Kylie"))
            await pilot.pause()
            self.assertTrue(screen.at()[2].blank, "not found: blank in the extra row")

            # Enter on Kylie's blank cell marks her in; the desk asks Telegram about her bot: red, with what to do
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.CellMenu)
            self.assertIn("mark it in this group", menu_list(app))
            pick(app, "mark|in")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await settle(app, pilot)
            await pilot.press("escape")
            await pilot.pause()
            await show(app, pilot)
            screen = app.screen
            self.assertTrue(screen.go(key, "Kylie"))
            await pilot.pause()
            self.assertEqual(screen.at()[2].status, D.RED)
            strip = str(screen.query_one("#strip").render())
            self.assertIn("not a member", acd.cell_text(screen.at()[2]).plain)
            self.assertIn("add Kylie's bot", strip)

            # c asks again; Kylie's bot was added in Telegram meanwhile: green
            fake.answers[(FAMILY, BOT["assistant"])] = member("kylie_fake_bot")
            asked = len(fake.calls)
            await pilot.press("c")
            await settle(app, pilot)
            await show(app, pilot)
            self.assertGreater(len(fake.calls), asked)
            screen = app.screen
            self.assertTrue(screen.go(key, "Kylie"))
            await pilot.pause()
            self.assertEqual(screen.at()[2].status, D.GREEN)

            # the row's name: its own menu; remove takes it off the desk only; u puts it back, u again takes it away
            self.assertTrue(screen.go(key, 0))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("remove this row from the desk", menu_list(app))
            pick(app, "remove")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await settle(app, pilot)
            await pilot.press("escape")
            await pilot.pause()
            await show(app, pilot)
            self.assertNotIn(key, [r.key for r in app.screen.rows])
            for _ in range(2):
                await pilot.press("u")
                await pilot.pause()
                await pilot.press("y")
                await settle(app, pilot)
                await pilot.press("escape")
                await pilot.pause()
            await show(app, pilot)
            self.assertIn(key, [r.key for r in app.screen.rows], "u put the row back")
            g = app.snapshot.groups[0]
            self.assertFalse(g.marks["assistant"], "and the mark before it is undone too")
            for _ in range(2):                               # Kylie's mark, then the row itself
                await pilot.press("u")
                await pilot.pause()
                await pilot.press("y")
                await settle(app, pilot)
                await pilot.press("escape")
                await pilot.pause()
            await show(app, pilot)
            self.assertNotIn(key, [r.key for r in app.screen.rows])
            self.assertFalse(TG.groups_file(w.edit).exists())
            history = w.edit.history.read_text(encoding="utf-8")
            no_token_anywhere(self, history, screen_text(app))

    async def test_a_seen_group_is_offered_first_and_a_dry_run_adds_nothing(self):
        w = world(armed=False)
        F.log(w, "orchestrator", F.stamp(w.now - 60) + " INFO gateway.run: inbound message: platform=telegram "
                                                      f"user=x chat={WORK} msg='hi'")
        before = F.tree_hashes(w.root)
        fake = FakeTelegram()
        app = make_app(w, telegram=fake)
        async with app.run_test(size=(220, 62)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("g")
            await pilot.pause()
            self.assertIsInstance(app.screen, acd.PickPopup)
            self.assertEqual(app.screen.rows[0][0], WORK)
            self.assertIn("new group seen, add it?", app.screen.rows[0][1])
            await pilot.press("enter")
            await pilot.pause()
            await self.type_in(app, pilot, "Work")
            self.assertEqual(app.screen.query_one("#box-input", Input).value, WORK, "the seen ID is filled in")
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertIn("DRY RUN", screen_text(app))
            app.screen.query_one("#box-list", OptionList).highlighted = 1           # every agent marked in
            await pilot.press("enter")
            await settle(app, pilot)
            self.assertTrue(app.last_results[0].dry)
        self.assertEqual(F.tree_hashes(w.root), before, "a dry run writes nothing")

    async def test_a_desk_with_its_own_collector_never_reaches_telegram(self):
        w = world()
        TG.add_group(w.edit, FAMILY, "Family", {"orchestrator": True}, now=WHEN)
        app = make_app(w)
        self.assertIsNone(app.telegram)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            await pilot.press("c")
            await settle(app, pilot)
            cell = acd.cell_of(next(r for r in acd.rows_of(app.snapshot) if r.key == acd.group_key(FAMILY)),
                               F.agent(app.snapshot, "orchestrator"))
            self.assertEqual((cell.status, cell.short), (D.GREY, "not checked"))


if __name__ == "__main__":
    unittest.main()
