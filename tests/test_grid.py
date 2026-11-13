"""Offline tests for the voice grid: what each cell reads (gridstate), what the logs prove (gridevidence),
and how it is drawn (gridview). Fakes only: a throw-away folder with the same file shapes as Peter's
Mac, never his real settings, logs, windows or speakers.

    .venv/bin/python -m unittest tests.test_grid -v
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import gridevidence as E  # noqa: E402
import gridstate as G  # noqa: E402
import gridview as V  # noqa: E402

NOW = time.time()
H = 3600


def rows_by_key(world, now=NOW):
    cfg = agents.load(world.toml)
    reader = E.LogReader(world.paths.profiles)
    return {r.agent.key: r for r in E.build(cfg, world.paths, reader, now)}


class TheRows(unittest.TestCase):
    def test_every_agent_shows_exactly_its_own_voice_and_a_voiceless_one_is_silent(self):
        # Peter 2026-11-11: one voice, no fallback, empty = silent. No shared voice fills a gap any more.
        cfg = agents.load(F.make_world().toml)
        info = {a.key: G.voice_info(a, cfg) for a in agents.agents(cfg)}
        self.assertEqual((info["kylie"].en, info["kylie"].zh), ("en-US-AvaNeural", "zh-TW-HsiaoChenNeural"))
        self.assertEqual((info["codex"].en, info["codex"].zh), ("", ""), "no voice in agents.toml: silent")
        self.assertTrue(info["codex"].silent)
        self.assertFalse(info["kylie"].silent)
        self.assertFalse(hasattr(info["kylie"], "backup"), "no backup voice is shown any more")

    def test_rows_are_the_agents_in_peters_order_and_builder_only_once_added(self):
        # 2026-11-13: the rows are exactly the [agents.*] blocks; Builder has none until Peter adds him (A)
        w = F.make_world()
        order = [a.key for a in G.grid_agents(agents.load(w.toml))]
        self.assertEqual(order, ["jarvis", "kylie", "esther", "wanna_buffet", "claude", "gemini", "codex"])
        w.toml.write_text(F.AGENTS_TOML + '\n[agents.builder]\nprofile = "builder"\ndisplay = "Builder"\nnames = ["builder"]\n')
        self.assertIn("builder", [a.key for a in G.grid_agents(agents.load(w.toml))])

    def test_a_new_agent_in_agents_toml_becomes_a_row_by_itself(self):
        w = F.make_world()
        w.toml.write_text(F.AGENTS_TOML + '\n[agents.atlas]\nprofile = "atlas"\ndisplay = "Atlas"\nnames = ["atlas"]\n'
                          'voice = "en-US-GuyNeural"\n')
        keys = [a.key for a in G.grid_agents(agents.load(w.toml))]
        self.assertEqual(keys[4], "atlas", "a new Hermes agent sits with the other Hermes agents")
        rows = rows_by_key(w)
        self.assertIn("atlas", rows)
        self.assertEqual(rows["atlas"].cells["terminal"].value, G.UNKNOWN, "no config.yaml: shown as unknown, not a crash")


class TheCells(unittest.TestCase):
    def test_terminal_window_follows_auto_enable(self):
        w = F.make_world()
        self.assertEqual(rows_by_key(w)["kylie"].cells["terminal"].value, G.ALWAYS)
        cfg = w.paths.profiles / "assistant/config.yaml"
        cfg.write_text(cfg.read_text().replace("  auto_enable: true\n", ""))
        self.assertEqual(rows_by_key(w)["kylie"].cells["terminal"].value, G.OFF)

    def test_claude_gemini_codex_terminal(self):
        w = F.make_world()
        rows = rows_by_key(w)
        self.assertEqual(rows["claude"].cells["terminal"].value, G.ALWAYS)
        self.assertEqual(rows["gemini"].cells["terminal"].value, G.ALWAYS)
        self.assertEqual(rows["codex"].cells["terminal"].value, G.NA, "Codex has no speech of its own")
        (w.paths.claude_dir / "settings.json").write_text(json.dumps({"voice": {"enabled": False}}))
        (w.paths.claude_dir / "hooks/speak-response.sh").unlink()
        rows = rows_by_key(w)
        self.assertEqual(rows["claude"].cells["terminal"].value, G.OFF)
        self.assertIn("missing", rows["claude"].cells["terminal"].problem)
        self.assertEqual(V.cell_text(rows["claude"].cells["terminal"], rows["claude"].evidence["terminal"]).spans[-1].style,
                         V.COLOUR["bad"])

    def test_telegram_direct_mode_file_wins_then_auto_tts(self):
        w = F.make_world(dm_modes={"assistant:telegram:7512954760": "off", "wanna-buffet:telegram:7512954760": "voice_only",
                                   "orchestrator:telegram:7512954760": "all"})
        rows = rows_by_key(w)
        self.assertEqual(rows["kylie"].cells["tg_dm"].value, G.OFF)
        self.assertEqual(rows["wanna_buffet"].cells["tg_dm"].value, G.VOICE_BACK)
        self.assertEqual(rows["jarvis"].cells["tg_dm"].value, G.ALWAYS)
        self.assertEqual(rows["esther"].cells["tg_dm"].value, G.ALWAYS, "no saved mode: voice.auto_tts true decides")
        self.assertIn("gateway starts", rows["kylie"].cells["tg_dm"].note)

    def test_no_telegram_for_claude_gemini_codex(self):
        rows = rows_by_key(F.make_world())
        for k in ("claude", "gemini", "codex"):
            self.assertEqual(rows[k].cells["tg_dm"].value, G.NA)
            self.assertEqual(rows[k].cells["tg_group"].value, G.NA)
            self.assertEqual(rows[k].evidence["tg_dm"].status, "na")

    def test_team_group_follows_mirror_config(self):
        self.assertEqual(rows_by_key(F.make_world(group_on=False))["jarvis"].cells["tg_group"].value, G.OFF)
        self.assertEqual(rows_by_key(F.make_world(group_on=True))["jarvis"].cells["tg_group"].value, G.ALWAYS)

    def test_mic_and_iphone_are_voice_back_for_everyone(self):
        for r in rows_by_key(F.make_world()).values():
            self.assertEqual(r.cells["mic"].value, G.VOICE_BACK)
            self.assertEqual(r.cells["iphone"].value, G.VOICE_BACK)

    def test_quiet_hours_and_extra_languages_are_stored_and_shown(self):
        w = F.make_world()
        text = F.AGENTS_TOML.replace(
            'voice_zh = "zh-TW-HsiaoChenNeural"\nspeed = 1.0\nauto_tts = true\nsilence_duration = 1.5\nsilence_threshold = 200\n'
            'barge_in = false\nquiet_hours',
            'voice_zh = "zh-TW-HsiaoChenNeural"\nvoices_extra = { ja = "ja-JP-NanamiNeural" }\nvoice_override = "zh"\n'
            'speed = 1.0\nauto_tts = true\nsilence_duration = 1.5\nsilence_threshold = 200\nbarge_in = false\nquiet_hours')
        self.assertNotEqual(text, F.AGENTS_TOML)
        w.toml.write_text(text)
        k = rows_by_key(w)["kylie"]
        self.assertEqual(k.quiet, {"tg_dm": "22:00-07:00"})
        self.assertEqual(k.voice.extra, {"ja": "ja-JP-NanamiNeural"})
        self.assertEqual(k.voice.override, "zh")
        shown = V.detail_text(k, None).plain
        self.assertIn("quiet hours 22:00-07:00", shown)
        self.assertIn("not enforced", shown)
        self.assertIn("ja: ja-JP-NanamiNeural", shown)


class TheVoiceCheck(unittest.TestCase):
    """Peter's real case, in the log's own wording: a Telegram direct chat that answers in text only."""

    def test_a_failed_voice_on_the_line_is_red_never_a_quiet_switch(self):
        # Peter 2026-11-11: a failed voice shows as a failure on the Voice check, the line stayed silent
        import service
        from unittest import mock
        a = next(x for x in agents.agents(agents.load(F.make_world().toml)) if x.key == "kylie")
        now = time.time()
        live = {"pid": os.getpid(), "heartbeat": now, "target": a.target, "voice": "Ava · HsiaoChen", "state": "listening",
                "voice_failed": now - 5}
        with mock.patch.object(service, "is_fresh", return_value=True):
            ev = E.judge_mic(a, G.Cell(G.VOICE_BACK), live, now, "en-US-AvaNeural")
            self.assertEqual((ev.status, ev.headline), ("bad", "voice failed"))
            self.assertIn("silent", ev.detail)
            live["voice_failed"] = None
            self.assertEqual(E.judge_mic(a, G.Cell(G.VOICE_BACK), live, now, "en-US-AvaNeural").status, "ok")

    def test_the_voice_column_says_silent_for_an_agent_with_no_voice_and_never_backup(self):
        rows = rows_by_key(F.make_world())
        self.assertIn("silent", V.voice_text(rows["codex"]).plain)
        self.assertNotIn("backup ", V.voice_text(rows["kylie"]).plain)
        self.assertIn("silent", V.test_text(None, False, rows["codex"].voice.silent).plain)
        self.assertNotIn("Jenny", V.detail_text(rows["gemini"], None).plain)

    def test_always_but_text_only_replies_is_red(self):
        w = F.make_world()
        for i in range(5):
            F.dm_reply(w, "assistant", NOW - (5 - i) * 86400)
        ev = rows_by_key(w)["kylie"].evidence["tg_dm"]
        self.assertEqual(ev.status, "bad")
        self.assertIn("text only", ev.headline)
        self.assertIn("no voice", ev.detail)
        self.assertIn("5 days", ev.detail)

    def test_always_with_voice_made_after_every_reply_is_green(self):
        w = F.make_world()
        for i in range(3):
            t = NOW - (3 - i) * H
            F.dm_reply(w, "assistant", t)
            F.dm_voice(w, "assistant", t + 1)
        ev = rows_by_key(w)["kylie"].evidence["tg_dm"]
        self.assertEqual(ev.status, "ok", ev)
        self.assertIn("does not log whether Telegram accepted", ev.detail)
        self.assertEqual(ev.voice, "en-US-AvaNeural via natural")

    def test_latest_text_only_after_earlier_voices_is_still_red(self):
        w = F.make_world()
        for i, voiced in enumerate((True, True, False)):
            t = NOW - (3 - i) * H
            F.dm_reply(w, "assistant", t)
            if voiced:
                F.dm_voice(w, "assistant", t + 1)
        ev = rows_by_key(w)["kylie"].evidence["tg_dm"]
        self.assertEqual(ev.status, "bad")
        self.assertIn("latest reply", ev.detail)

    def test_latest_voiced_but_earlier_text_only_is_yellow(self):
        w = F.make_world()
        for i, voiced in enumerate((False, False, True)):
            t = NOW - (3 - i) * H
            F.dm_reply(w, "assistant", t)
            if voiced:
                F.dm_voice(w, "assistant", t + 1)
        self.assertEqual(rows_by_key(w)["kylie"].evidence["tg_dm"].status, "warn")

    def test_a_failed_voice_is_red_with_the_reason(self):
        w = F.make_world()
        F.dm_reply(w, "tax_angel_esther", NOW - H)
        F.log(w, "tax_angel_esther", "gateway.log", NOW - H + 5,
              "WARNING gateway.run: Auto voice reply TTS failed: TTS provider 'esther' timed out after 300s")
        ev = rows_by_key(w)["esther"].evidence["tg_dm"]
        self.assertEqual(ev.status, "bad")
        self.assertEqual(ev.headline, "voice failed")
        self.assertIn("timed out after 300s", ev.detail)

    def test_a_voice_started_but_never_finished_is_a_failure(self):
        w = F.make_world()
        F.dm_reply(w, "assistant", NOW - H)
        F.log(w, "assistant", "agent.log", NOW - H + 1, "INFO tools.tts_tool: Generating speech with command TTS provider 'natural'...")
        self.assertEqual(rows_by_key(w)["kylie"].evidence["tg_dm"].status, "bad")

    def test_no_replies_lately_is_grey_not_red(self):
        w = F.make_world()
        F.dm_reply(w, "assistant", NOW - 20 * 86400)         # older than the 7-day look-back
        self.assertEqual(rows_by_key(w)["kylie"].evidence["tg_dm"].status, "idle")

    def test_off_but_a_voice_was_made_is_a_mismatch(self):
        w = F.make_world(dm_modes={"assistant:telegram:7512954760": "off"})
        F.dm_reply(w, "assistant", NOW - H)
        F.dm_voice(w, "assistant", NOW - H + 1)
        self.assertEqual(rows_by_key(w)["kylie"].evidence["tg_dm"].status, "bad")
        w2 = F.make_world(dm_modes={"assistant:telegram:7512954760": "off"})
        F.dm_reply(w2, "assistant", NOW - H)
        self.assertEqual(rows_by_key(w2)["kylie"].evidence["tg_dm"].status, "idle", "Off and text only is as set")

    def test_voice_back_without_a_voice_message_is_grey(self):
        w = F.make_world(dm_modes={"assistant:telegram:7512954760": "voice_only"})
        F.dm_reply(w, "assistant", NOW - H)
        self.assertEqual(rows_by_key(w)["kylie"].evidence["tg_dm"].status, "idle")

    def test_group_replies_do_not_count_as_direct_chat(self):
        w = F.make_world()
        F.dm_reply(w, "assistant", NOW - H, chat="-5370236728")
        self.assertEqual(rows_by_key(w)["kylie"].evidence["tg_dm"].status, "idle")

    def test_team_group_notes_green_recent_grey_old_red_when_off(self):
        w = F.make_world()
        F.team_note(w, "assistant", NOW - H)
        F.team_note(w, "orchestrator", NOW - 3 * 86400)
        rows = rows_by_key(w)
        self.assertEqual(rows["kylie"].evidence["tg_group"].status, "ok")
        self.assertEqual(rows["jarvis"].evidence["tg_group"].status, "idle")
        self.assertEqual(rows["esther"].evidence["tg_group"].status, "idle")
        w2 = F.make_world(group_on=False)
        F.team_note(w2, "assistant", NOW - H)
        self.assertEqual(rows_by_key(w2)["kylie"].evidence["tg_group"].status, "bad")

    def test_team_notes_do_not_pass_for_direct_chat_voice(self):
        w = F.make_world()
        F.dm_reply(w, "assistant", NOW - H)
        F.team_note(w, "assistant", NOW - H + 1)             # a team note right after the reply is not the reply's voice
        self.assertEqual(rows_by_key(w)["kylie"].evidence["tg_dm"].status, "bad")

    def test_claude_and_gemini_evidence_comes_from_the_files_their_hooks_leave(self):
        w = F.make_world()
        spoke = w.paths.claude_state / "last-spoken-abc"
        spoke.write_text("")
        os.utime(spoke, (NOW - 600, NOW - 600))
        rows = rows_by_key(w)
        self.assertEqual(rows["claude"].evidence["terminal"].status, "ok")
        self.assertEqual(rows["gemini"].evidence["terminal"].status, "idle", "no audio_cache files: nothing said lately")

    def test_listener_off_shows_red_in_the_mic_column(self):
        ev = rows_by_key(F.make_world())["kylie"].evidence["mic"]
        self.assertEqual(ev.status, "bad")
        self.assertIn("not running", ev.detail)

    def test_listener_running_marks_only_the_agent_on_the_line(self):
        w = F.make_world()
        (w.paths.state / "listener.json").write_text(json.dumps(
            {"state": "asleep", "updated": NOW, "since": NOW - 5, "target": "hermes:assistant", "name": "Kylie",
             "voice": "en-US-AvaNeural"}))
        rows = rows_by_key(w)
        self.assertEqual(rows["kylie"].evidence["mic"].status, "ok")
        self.assertEqual(rows["jarvis"].evidence["mic"].status, "idle")

    def test_phone_door_closed_is_red(self):
        self.assertEqual(rows_by_key(F.make_world())["kylie"].evidence["iphone"].status, "bad")   # port 1: nothing listens


class TheReaderIsGentle(unittest.TestCase):
    def test_one_pass_reads_each_log_once_and_a_second_pass_reads_nothing(self):
        w = F.make_world()
        F.dm_reply(w, "assistant", NOW - H)
        cfg = agents.load(w.toml)
        reader = E.LogReader(w.paths.profiles)
        E.build(cfg, w.paths, reader, NOW)
        first = reader.reads
        self.assertLessEqual(first, 8, "at most agent.log + gateway.log for each of the four profiles")
        E.build(cfg, w.paths, reader, NOW)
        self.assertEqual(reader.reads, first, "nothing changed on disk, so nothing is read again")
        F.dm_reply(w, "assistant", NOW - 60)
        E.build(cfg, w.paths, reader, NOW)
        self.assertEqual(reader.reads, first + 2, "only the two changed files are read again")

    def test_only_the_tail_of_a_huge_log_is_read(self):
        w = F.make_world()
        big = w.paths.profiles / "assistant/logs/agent.log"
        big.write_text("2026-01-01 00:00:00,000 INFO x: filler line\n" * 5000)
        F.dm_reply(w, "assistant", NOW - H)
        reader = E.LogReader(w.paths.profiles, tail_bytes=20_000)
        events, _ = reader.events("assistant")
        self.assertEqual(len([e for e in events if e.kind == "dm_reply"]), 1)

    def test_a_missing_log_is_not_a_crash(self):
        events, since = E.LogReader(F.make_world().paths.profiles).events("nobody")
        self.assertEqual(events, [])
        self.assertIsNone(since)


class TheLooks(unittest.TestCase):
    def test_a_cell_shows_the_setting_and_the_evidence_in_the_right_colour(self):
        w = F.make_world()
        F.dm_reply(w, "assistant", NOW - H)
        r = rows_by_key(w)["kylie"]
        t = V.cell_text(r.cells["tg_dm"], r.evidence["tg_dm"])
        self.assertTrue(t.plain.startswith("Always\n"))
        self.assertIn("text only", t.plain)
        self.assertIn(V.COLOUR["bad"], {str(s.style) for s in t.spans})

    def test_not_applicable_and_not_here_is_a_blank_cell(self):
        # Part 4 (Peter 2026-11-11): not here = a blank cell (it was a dash)
        r = rows_by_key(F.make_world())["codex"]
        self.assertEqual(V.cell_text(r.cells["tg_dm"], r.evidence["tg_dm"]).plain, "")
        self.assertEqual(V.cell_text(r.cells["tg_dm"], r.evidence["tg_dm"], r.is_here("tg_dm"), "tg_dm").plain, "")


if __name__ == "__main__":
    unittest.main()
