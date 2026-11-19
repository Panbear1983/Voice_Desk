"""Adding and removing agents (Part 3) and the here / not-here toggle and activation phrases (Part 4).

Only agents.toml is written, a copy is kept first, the result is read back, U undoes exactly.

    .venv/bin/python -m unittest tests.test_gridedit -v
"""
import shutil
import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests  # noqa: E402,F401
from tests import gridfix as F  # noqa: E402

import agents  # noqa: E402
import gridedit as GE  # noqa: E402
import gridstate as G  # noqa: E402
import voiceedit as VE  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def world_with_builder():
    w = F.make_world()
    (w.edit.profiles / "builder").mkdir()
    (w.edit.profiles / "builder" / "config.yaml").write_text("model: x\n")
    (w.edit.profiles / "default").mkdir()                       # no config.yaml: not a profile that can talk
    return w


def files_under(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# The old dashboard's footer, its ? screen and its A / D / X / P / L / O / T keys were retired with it on 2026-11-18.
# The Agent Control Desk does each of them through the same writers: tests/test_acd_parity.py (add, remove, the
# voice test), tests/test_acd_two_keys.py (here / not here, the phrase), tests/test_acd_languages.py (L and O).


class AddingAnAgent(unittest.TestCase):
    def setUp(self):
        self.w = world_with_builder()

    def test_the_pick_list_is_profiles_on_disk_not_on_the_grid(self):
        self.assertEqual(GE.addable_profiles(agents.load(self.w.toml), self.w.edit.profiles), ["builder"])

    def test_simbas_block_counts_as_her_profile(self):
        (self.w.edit.profiles / "simba").mkdir()
        (self.w.edit.profiles / "simba" / "config.yaml").write_text("model: x\n")
        self.w.toml.write_text(F.AGENTS_TOML + '\n[agents.simba]\ntarget = "simba"\ndisplay = "Simba"\nnames = ["simba"]\n')
        self.assertEqual(GE.addable_profiles(agents.load(self.w.toml), self.w.edit.profiles), ["builder"])

    def test_add_a_hermes_profile_silent_then_u_takes_it_off_byte_for_byte(self):
        before_profiles = files_under(self.w.edit.profiles)
        res = GE.add_agent(self.w.edit, "Builder", "builder, the builder", None, profile="builder")
        self.assertTrue(res.ok, res.message)
        a = next(x for x in agents.agents(agents.load(self.w.toml)) if x.key == "builder")
        self.assertEqual((a.target, a.display, a.names, a.voice), ("hermes:builder", "Builder", ("builder", "the builder"), None))
        self.assertEqual(a.raw.get("kind"), "hermes", "the block says which connector reads it")
        self.assertIn("builder", [x.key for x in G.grid_agents(agents.load(self.w.toml))], "a row by itself")
        self.assertIn("silent", res.message)
        self.assertEqual(files_under(self.w.edit.profiles), before_profiles, "no profile file was touched")
        self.assertEqual(Path(res.entry["toml_backup"]).read_text(), F.AGENTS_TOML, "a copy from before")
        self.assertIn("Builder on the grid: no → added", VE.recent(self.w.edit, 1)[0])
        u = VE.undo(self.w.edit)
        self.assertTrue(u.ok, u.message)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        self.assertIn("undo: Builder on the grid: added → no", VE.recent(self.w.edit, 1)[0])

    def test_add_with_a_voice_checks_the_voice_name(self):
        bad = GE.add_agent(self.w.edit, "Builder", "", "zh-TW-HsiaoChenNeural", profile="builder")
        self.assertFalse(bad.ok)
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        res = GE.add_agent(self.w.edit, "Builder", "", "en-US-BrianNeural", profile="builder")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(agents.load(self.w.toml)["agents"]["builder"]["voice"], "en-US-BrianNeural")

    def test_other_cli_agent_gets_a_target_of_its_own_and_no_chat_id(self):
        res = GE.add_agent(self.w.edit, "Aider", "aider, ada", "af_heart")
        self.assertTrue(res.ok, res.message)
        raw = agents.load(self.w.toml)["agents"]["aider"]
        self.assertEqual(raw, {"target": "aider", "kind": "cli", "display": "Aider", "names": ["aider", "ada"],
                               "voice": "af_heart"})
        self.assertNotIn("chat", self.w.toml.read_text().split("[agents.aider]")[1])
        self.assertIn("cannot talk to it yet", res.message)

    def test_refuses_a_duplicate_a_builtin_and_an_empty_name(self):
        self.assertFalse(GE.add_agent(self.w.edit, "Kylie", "", None, profile="assistant").ok)
        self.assertFalse(GE.add_agent(self.w.edit, "Codex", "", None).ok)
        self.assertFalse(GE.add_agent(self.w.edit, "  ", "", None).ok)
        self.assertFalse(GE.add_agent(self.w.edit, "Ghost", "", None, profile="ghost").ok, "not on this Mac")
        self.assertEqual(self.w.toml.read_text(), F.AGENTS_TOML)
        self.assertEqual(VE.read_history(self.w.edit), [])


class RemovingAnAgent(unittest.TestCase):
    def test_remove_takes_only_the_block_and_u_puts_it_back_exactly(self):
        w = F.make_world()
        before_profiles = files_under(w.edit.profiles)
        res = GE.remove_agent(w.edit, "kylie")
        self.assertTrue(res.ok, res.message)
        text = w.toml.read_text()
        self.assertNotIn("[agents.kylie]", text)
        self.assertNotIn("Kylie's English voice", text)
        self.assertIn("[agents.esther]", text)
        self.assertEqual(files_under(w.edit.profiles), before_profiles, "her Hermes profile is untouched")
        self.assertTrue((w.edit.profiles / "assistant" / "config.yaml").exists())
        self.assertIn("files are untouched", res.message)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_the_real_agents_toml_simbas_comments_go_with_her_and_come_back(self):
        w = F.make_world()
        real = (REPO / "agents.toml").read_text()
        w.toml.write_text(real)
        res = GE.remove_agent(w.edit, "simba")
        self.assertTrue(res.ok, res.message)
        text = w.toml.read_text()
        self.assertNotIn("girl cat", text, "the comment lines above her header are hers")
        self.assertIn('[agents.codex]\ntarget = "codex"', text)
        self.assertIn('voice_zh = "zh-TW-YunJheNeural"\n\n[agents.jarvis]', text, "one blank line between blocks")
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), real)

    def test_the_last_block_in_the_file_leaves_no_stray_blank_line(self):
        w = F.make_world()
        self.assertTrue(GE.remove_agent(w.edit, "wanna_buffet").ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML.split("\n[agents.wanna_buffet]")[0])
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_d_then_u_is_byte_identical_for_every_block_of_the_real_agents_toml(self):
        # Review 2026-11-13: the block just before the last one (Esther) did not come back, because the last
        # block's span takes the blank line above it and the undo put her on the wrong side of that blank line.
        real = (REPO / "agents.toml").read_text()
        self.assertGreaterEqual(len(tomllib.loads(real)["agents"]), 8)
        for name, texts in (("real", real), ("fixture", F.AGENTS_TOML)):
            for key in tomllib.loads(texts)["agents"]:
                with self.subTest(key=key, file=name):
                    w = F.make_world()
                    w.toml.write_text(texts)
                    res = GE.remove_agent(w.edit, key)
                    self.assertTrue(res.ok, res.message)
                    self.assertNotIn(f"[agents.{key}]", w.toml.read_text())
                    u = VE.undo(w.edit)
                    self.assertTrue(u.ok, u.message)
                    self.assertEqual(w.toml.read_bytes(), texts.encode())

    def test_two_removals_in_a_row_come_back_with_u_u(self):
        real = (REPO / "agents.toml").read_text()
        w = F.make_world()
        w.toml.write_text(real)
        self.assertTrue(GE.remove_agent(w.edit, "esther").ok)
        self.assertTrue(GE.remove_agent(w.edit, "wanna_buffet").ok)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), real)

    def test_an_undo_whose_read_back_refuses_names_the_backup(self):
        from unittest import mock
        w = F.make_world()
        res = GE.remove_agent(w.edit, "kylie")
        self.assertTrue(res.ok, res.message)
        assert res.entry
        removed = w.toml.read_text()
        real_put = GE.put_block
        with mock.patch.object(GE, "put_block", lambda t, k, b, a=None: real_put(t, k, b and b.replace(
                "\n", "\n# hand-made\n", 1), a)):
            u = VE.undo(w.edit)
        self.assertFalse(u.ok)
        self.assertIn("did not come back as it was", u.message)
        self.assertIn(res.entry["toml_backup"], u.message)
        self.assertEqual(w.toml.read_text(), removed, "nothing written")

    def test_an_undo_refuses_when_the_block_was_edited_by_hand_since(self):
        w = F.make_world()
        self.assertTrue(GE.add_agent(w.edit, "Aider", "", None).ok)
        w.toml.write_text(w.toml.read_text().replace('display = "Aider"', 'display = "Aider!"'))
        u = VE.undo(w.edit)
        self.assertFalse(u.ok)
        self.assertIn("changed since", u.message)
        self.assertIn('display = "Aider!"', w.toml.read_text(), "nothing overwritten")


class TheWiringMatrix(unittest.TestCase):
    """Part 4: here / not here per cell (separate from the voice switch), unfinished never green, phrases."""

    def rows(self, w):
        import gridevidence as E
        return {r.agent.key: r for r in E.build(agents.load(w.toml), w.paths, E.LogReader(w.paths.profiles))}

    def test_not_here_is_a_blank_cell_and_here_but_unwired_is_orange_never_green(self):
        import gridevidence as E
        import gridview as V
        na = G.Cell(G.NA, "nothing speaks there")
        self.assertEqual(V.cell_text(na, E.NA_EVIDENCE, False, "tg_dm").plain, "")
        t = V.cell_text(na, E.NA_EVIDENCE, True, "tg_dm")
        self.assertEqual(t.plain, "here\n◌ not wired yet")
        self.assertNotIn(V.COLOUR["ok"], {str(s.style) for s in t.spans})
        ok = E.Evidence("ok", "spoke 2m ago")
        t = V.cell_text(G.Cell(G.UNKNOWN, "no Siri shortcut"), ok, True, "iphone")
        self.assertIn("not wired", t.plain, "an iPhone with no shortcut is unfinished even when the door is open")
        self.assertEqual(V.cell_text(G.Cell(G.ALWAYS), E.Evidence("ok", "spoke 2m ago"), False, "tg_group").plain, "",
                         "not here hides even a working cell")

    def test_by_default_an_agent_is_here_where_it_is_wired(self):
        w = F.make_world()
        r = self.rows(w)
        self.assertFalse(r["codex"].is_here("tg_dm"), "Codex has no Telegram bot")
        self.assertTrue(r["codex"].is_here("mic"))
        self.assertTrue(r["kylie"].is_here("tg_dm"))
        self.assertEqual(r["kylie"].phrase("mic"), ("hello Kylie", False))

    def test_x_puts_codex_here_on_telegram_unfinished_and_u_takes_it_back(self):
        w = F.make_world()
        cells = self.rows(w)["codex"].cells
        res = GE.set_here(w.edit, "codex", "tg_dm", True, cells)
        self.assertTrue(res.ok, res.message)
        self.assertIn("not wired there yet", res.message)
        r = self.rows(w)["codex"]
        self.assertTrue(r.is_here("tg_dm"))
        self.assertTrue(G.unwired(r.cells["tg_dm"], "tg_dm"))
        self.assertIn("Codex at Telegram direct: not here → here", VE.recent(w.edit, 1)[0])
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_x_takes_kylie_off_a_place_without_touching_her_voice_switch(self):
        w = F.make_world()
        before = files_under(w.root)
        r = self.rows(w)["kylie"]
        res = GE.set_here(w.edit, "kylie", "tg_dm", False, r.cells)
        self.assertTrue(res.ok, res.message)
        self.assertIn("voice switch there is unchanged", res.message)
        after = files_under(w.root)
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        self.assertTrue(all(k.endswith("agents.toml") or "voice_history" in k for k in changed), changed)
        raw = agents.load(w.toml)["agents"]["kylie"]["here"]
        self.assertNotIn("tg_dm", raw)
        self.assertIn("mic", raw)
        self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_p_stores_phrases_per_place_empty_goes_back_to_default_and_u_undoes_each(self):
        w = F.make_world()
        self.assertTrue(GE.set_phrase(w.edit, "jarvis", "mic", "  hello   Jarvis  ").ok)
        self.assertTrue(GE.set_phrase(w.edit, "jarvis", "iphone", "Talk to Jarvis now").ok)
        self.assertEqual(agents.load(w.toml)["agents"]["jarvis"]["phrases"],
                         {"mic": "hello Jarvis", "iphone": "Talk to Jarvis now"})
        self.assertIn("Jarvis phrase at iPhone (Siri): “default (Talk to Jarvis)” → “Talk to Jarvis now”",
                      VE.recent(w.edit, 1)[0])
        self.assertTrue(GE.set_phrase(w.edit, "jarvis", "mic", "").ok)
        self.assertEqual(agents.load(w.toml)["agents"]["jarvis"]["phrases"], {"iphone": "Talk to Jarvis now"})
        for _ in range(3):
            self.assertTrue(VE.undo(w.edit).ok)
        self.assertEqual(w.toml.read_text(), F.AGENTS_TOML)

    def test_the_flags_survive_a_restart_because_they_live_in_agents_toml(self):
        w = F.make_world()
        GE.set_here(w.edit, "codex", "iphone", False, self.rows(w)["codex"].cells)
        GE.set_phrase(w.edit, "codex", "mic", "hello Codex")
        a = next(x for x in agents.agents(agents.load(w.toml)) if x.key == "codex")
        self.assertEqual(G.phrase_map(a), {"mic": "hello Codex"})
        self.assertFalse(G.here_map(a, self.rows(w)["codex"].cells)["iphone"])


if __name__ == "__main__":
    unittest.main()
