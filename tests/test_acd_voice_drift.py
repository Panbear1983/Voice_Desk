"""Offline tests for the voice-drift warning on the Agent Control Desk (2026-11-18).

The retired voice dashboard warned when an agent's own voice settings (its Hermes config.yaml, Claude's or
Gemini's voice-defaults.conf) no longer matched agents.toml. The desk now runs the same check, read-only, on
every read: voicecfg's own collect + drift, exactly what `.venv/bin/python -m voicecfg check` compares.

    nothing differs     the status line under the table says "voice settings match agents.toml (N in M files)"
    something differs   a red line in the top strip names the agents, the first difference in voicecfg's own
                        words, and the command that lists them all
    the check fails     a yellow line says it could not compare; the desk still opens

Throw-away folders only: no live settings file, no window, no listener, no sound.

    .venv/bin/python -m unittest tests.test_acd_voice_drift -v
"""
import contextlib
import dataclasses
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import acdfix as F  # noqa: E402
from tests.test_acd_table import make_app, show, text_of  # noqa: E402

import acd  # noqa: E402
import acd_data as D  # noqa: E402
import agents as A  # noqa: E402
import voicecfg  # noqa: E402

KYLIE = "assistant"


def clean_world():
    """The desk's fake world with every voice setting in step with its agents.toml. (The fixture writes every
    profile with the same mic threshold; Wanna Buffet's agents.toml block says 500, so that line is set by hand.)"""
    w = F.edit_world()
    cfg = w.profiles / "wanna-buffet" / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("silence_threshold: 200", "silence_threshold: 500"),
                   encoding="utf-8")
    v = w.edit.voice
    w.paths = dataclasses.replace(w.paths, voice_check=lambda: D.voice_check(A.load(v.toml), v.profiles,
                                                                              v.claude_conf, v.gemini_conf))
    return w


def hand_edit_kylie(w, to: str = "en-GB-SoniaNeural") -> None:
    """Someone changes Kylie's voice in her own settings file, behind agents.toml's back."""
    cfg = w.profiles / KYLIE / "config.yaml"
    text = cfg.read_text(encoding="utf-8")
    assert "en-US-AvaNeural" in text
    cfg.write_text(text.replace("en-US-AvaNeural", to), encoding="utf-8")


def cli_check(w) -> tuple[int, str]:
    """What `python -m voicecfg check` itself says about the same throw-away files."""
    v = w.edit.voice
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = voicecfg.main(["check", "--agents", str(v.toml), "--profiles", str(v.profiles),
                              "--claude-conf", str(v.claude_conf), "--gemini-conf", str(v.gemini_conf)])
    return code, out.getvalue()


def strip(snap: D.Snapshot) -> list:
    return [(x.status, x.text) for x in snap.warnings]


class TheCheck(unittest.TestCase):
    def test_a_world_in_step_matches_and_says_how_much_was_compared(self):
        w = clean_world()
        vc = w.paths.voice_check()
        self.assertIs(vc.ok, True, vc.lines)
        code, said = cli_check(w)
        self.assertEqual(code, 0, said)
        self.assertIn(f"No drift: {vc.checked} voice settings in {vc.files} files match agents.toml.", said)

    def test_a_hand_edited_voice_is_found_in_voicecfgs_own_words(self):
        w = clean_world()
        hand_edit_kylie(w)
        vc = w.paths.voice_check()
        self.assertIs(vc.ok, False)
        self.assertEqual(vc.who, ("Kylie",))
        self.assertTrue(vc.lines and all("en-GB-SoniaNeural" in x and "en-US-AvaNeural" in x for x in vc.lines),
                        vc.lines)
        code, said = cli_check(w)                       # the desk and the command agree, line for line
        self.assertEqual(code, 1)
        cli_lines = [" ".join(x.split()) for x in said.splitlines()]
        for line in vc.lines:
            self.assertIn(line, cli_lines)
        self.assertIn(f"{len(vc.lines)} differences.", said)

    def test_the_default_desk_reads_exactly_the_files_voicecfg_check_reads(self):
        seen = []
        with mock.patch.object(D.A, "load", return_value={"agents": {}}) as load, \
                mock.patch.object(D, "voice_check", side_effect=lambda *a: seen.append(a) or D.VoiceCheck(True)):
            read = D._voice_check_default()
            self.assertIsNotNone(read)
            read()
        load.assert_called_once_with(None)              # agents.toml as the command finds it (VOICE_DESK_AGENTS)
        self.assertEqual(seen, [({"agents": {}}, voicecfg.PROFILES, voicecfg.CLAUDE_CONF, voicecfg.GEMINI_CONF)])


class TheDesk(unittest.TestCase):
    def test_in_step_it_is_one_green_item_on_the_status_line_and_no_warning(self):
        w = clean_world()
        snap = F.edit_collect(w)
        self.assertIs(snap.voice_check.ok, True)
        self.assertFalse([t for _s, t in strip(snap) if "agents.toml" in t], strip(snap))
        status = acd.systems_text(snap).plain
        self.assertIn(f"● voice settings match agents.toml ({snap.voice_check.checked} in "
                      f"{snap.voice_check.files} files)", status)

    def test_a_difference_is_a_red_line_that_names_the_agent_and_the_first_difference(self):
        w = clean_world()
        hand_edit_kylie(w)
        before = F.tree_hashes(w.root)
        snap = F.edit_collect(w)
        self.assertEqual(F.tree_hashes(w.root), before)          # read-only: nothing was put back or written
        lines = [(s, t) for s, t in strip(snap) if "agents.toml" in t]
        self.assertEqual(len(lines), 1, strip(snap))
        status, text = lines[0]
        self.assertEqual(status, D.RED)
        n = len(snap.voice_check.lines)
        self.assertTrue(text.startswith(f"{n} voice settings differ from agents.toml (Kylie): "), text)
        self.assertIn(snap.voice_check.lines[0], text)
        self.assertIn("python -m voicecfg check", text)
        self.assertNotIn("voice settings match", acd.systems_text(snap).plain)
        self.assertNotIn("nothing needs attention", " ".join(t for _s, t in strip(snap)))

    def test_one_difference_is_said_in_the_singular(self):
        w = clean_world()
        cfg = w.profiles / "wanna-buffet" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("silence_threshold: 500", "silence_threshold: 200"),
                       encoding="utf-8")
        text = next(t for _s, t in strip(F.edit_collect(w)) if "agents.toml" in t)
        self.assertTrue(text.startswith("1 voice setting differs from agents.toml (Wanna Buffet): "), text)
        self.assertIn("voice.silence_threshold", text)
        self.assertIn("is 200, agents.toml says 500", text)
        self.assertNotIn(" …", text)

    def test_a_check_that_fails_is_a_yellow_line_and_the_desk_still_opens(self):
        w = F.edit_world()
        w.paths = dataclasses.replace(w.paths, voice_check=mock.Mock(side_effect=ValueError("agents.toml is broken")))
        snap = F.edit_collect(w)
        self.assertTrue(snap.agents)
        self.assertIs(snap.voice_check.ok, None)
        self.assertIn((D.YELLOW, "the voice settings could not be compared with agents.toml (voicecfg check: "
                                 "agents.toml is broken)"), strip(snap))
        self.assertNotIn("voice settings match", acd.systems_text(snap).plain)

    def test_a_desk_not_given_the_check_says_nothing_about_it(self):
        snap = F.edit_collect(F.edit_world())
        self.assertIsNone(snap.voice_check)
        self.assertFalse([t for _s, t in strip(snap) if "agents.toml" in t])
        self.assertNotIn("voice settings match", acd.systems_text(snap).plain)


class OnTheScreen(unittest.IsolatedAsyncioTestCase):
    async def test_the_red_line_is_in_the_top_strip_of_the_open_desk(self):
        w = clean_world()
        hand_edit_kylie(w)
        app = make_app(w)
        async with app.run_test(size=(220, 66)) as pilot:
            await pilot.pause()
            await show(app, pilot)
            top = " ".join(text_of(app, "#warnings").split())
            self.assertIn("voice settings differ from agents.toml (Kylie)", top)
            self.assertIn("en-GB-SoniaNeural", top)


if __name__ == "__main__":
    unittest.main()
