"""Draw the Agent Control Desk without a window: the grid and one agent's tree, saved as pictures (SVG).

    .venv/bin/python acd_snapshot.py --out docs/acd_mockups            # the real files on this Mac, read-only
    .venv/bin/python acd_snapshot.py --out /tmp/x --agent 2 --size 190x58

    .venv/bin/python acd_snapshot.py --out docs/acd_mockups --v2       # version two: the pop-ups as well
    .venv/bin/python acd_snapshot.py --out docs/acd_mockups --v3       # version three: Team Room switch, talk rules
    .venv/bin/python acd_snapshot.py --out docs/acd_mockups --v4       # version four: sign-in and allowance, the
                                                                       # assistants' voices, a voice switch, and a
                                                                       # drift pop-up drawn from a throw-away world

Nothing appears on the screen and nothing is written except the pictures (the desk is never armed here, so a
save inside a picture run is a dry run). Used for checks and for the
read-me's screenshots, so no test ever needs a real window.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Callable, Optional

import acd
import acd_data as D


async def render(out: Path, collector: Optional[Callable[[], D.Snapshot]] = None, agent: int = 0,
                 size: tuple[int, int] = (190, 58), prefix: str = "v1") -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    if collector is None:
        paths = D.Paths.default()
        collector = lambda: D.collect(paths)                     # noqa: E731
    snap = await asyncio.to_thread(collector)
    app = acd.Desk(collector=lambda: snap, autostart=False)
    grid, tree = out / f"{prefix}_grid.svg", out / f"{prefix}_tree.svg"
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        app.apply_snapshot(snap)
        await pilot.pause()
        grid.write_text(app.export_screenshot(title="Agent Control Desk: the grid"), encoding="utf-8")
        app.open_tree(agent)
        await pilot.pause()
        tree.write_text(app.export_screenshot(title="Agent Control Desk: one agent's tree"), encoding="utf-8")
    return grid, tree


async def render_v2(out: Path, collector: Optional[Callable[[], D.Snapshot]] = None, agent: int = 0,
                    size: tuple[int, int] = (190, 58), prefix: str = "v2", desk_kwargs: Optional[dict] = None) -> list[Path]:
    """Version two's pictures: the grid, a narrow grid, the tree, and the pop-ups (model, backups, voice, restart
    review) plus what a dry-run save shows. The desk is NOT armed here, so nothing can be written."""
    out.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    kwargs = dict(desk_kwargs or {})
    kwargs.pop("launcher_armed", None)                            # a picture run is never armed

    async def one(name: str, steps, shot_size=size) -> None:
        app = acd.Desk(collector=collector, autostart=False, **kwargs) if collector else acd.Desk(autostart=False, **kwargs)
        snap = await asyncio.to_thread(app.collector)
        async with app.run_test(size=shot_size) as pilot:
            await pilot.pause()
            app.apply_snapshot(snap)
            await pilot.pause()
            await steps(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            path = out / f"{prefix}_{name}.svg"
            path.write_text(app.export_screenshot(title=f"Agent Control Desk: {name.replace('_', ' ')}"), encoding="utf-8")
            made.append(path)

    async def setting(app, pilot, name: str) -> None:
        app.open_tree(agent)
        await pilot.pause()
        tree = app.screen.query_one("#tree")
        tree.highlighted = app.screen.settings.index(name)
        await pilot.press("enter")
        await pilot.pause()

    async def grid(app, pilot):
        pass

    async def tree(app, pilot):
        app.open_tree(agent)
        await pilot.pause()

    async def model(app, pilot):
        await setting(app, pilot, "model")
        await pilot.press("down")
        await pilot.press("enter")

    async def model_confirm(app, pilot):
        await model(app, pilot)
        await pilot.press("s")

    async def dry_result(app, pilot):
        await model_confirm(app, pilot)
        await pilot.press("enter")

    async def backups(app, pilot):
        await setting(app, pilot, "backups")
        await pilot.press("space")

    async def voice(app, pilot):
        await setting(app, pilot, "voice")

    async def restart(app, pilot):
        await pilot.press("r")

    await one("grid", grid)
    await one("grid_narrow", grid, (128, 50))
    await one("tree", tree)
    await one("model_popup", model)
    await one("model_confirm", model_confirm)
    await one("dry_run_result", dry_result)
    await one("backups_popup", backups)
    await one("voice_popup", voice)
    await one("restart_popup", restart)
    return made


async def render_v3(out: Path, collector: Optional[Callable[[], D.Snapshot]] = None, agent: int = 1,
                    size: tuple[int, int] = (190, 70), prefix: str = "v3", desk_kwargs: Optional[dict] = None) -> list[Path]:
    """Version three's pictures: one agent's tree (where it shows up, who it may talk to), the Team Room switch,
    the talk-rules pop-up and the restart review. Never armed, so nothing can be written or run."""
    out.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    kwargs = dict(desk_kwargs or {})
    kwargs.pop("launcher_armed", None)

    async def one(name: str, steps) -> None:
        app = acd.Desk(collector=collector, autostart=False, **kwargs) if collector else acd.Desk(autostart=False, **kwargs)
        snap = await asyncio.to_thread(app.collector)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app.apply_snapshot(snap)
            await pilot.pause()
            await steps(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            path = out / f"{prefix}_{name}.svg"
            path.write_text(app.export_screenshot(title=f"Agent Control Desk: {name.replace('_', ' ')}"), encoding="utf-8")
            made.append(path)

    async def setting(app, pilot, name: str) -> bool:
        app.open_tree(agent)
        await pilot.pause()
        if name not in app.screen.settings:
            return False
        app.screen.query_one("#tree").highlighted = app.screen.settings.index(name)
        await pilot.press("enter")
        await pilot.pause()
        return True

    async def tree(app, pilot):
        app.open_tree(agent)
        await pilot.pause()
        if "team_room" in app.screen.settings:
            app.screen.query_one("#tree").highlighted = app.screen.settings.index("team_room")
            await pilot.pause()

    async def presence(app, pilot):
        if await setting(app, pilot, "team_room"):
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.press("s")

    async def talk(app, pilot):
        await setting(app, pilot, "talk")

    async def restart(app, pilot):
        await pilot.press("r")

    await one("tree", tree)
    await one("presence_popup", presence)
    await one("talk_popup", talk)
    await one("restart_popup", restart)
    return made


async def _shots(out: Path, prefix: str, size: tuple[int, int], make_app, steps_by_name: dict) -> list[Path]:
    made: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)
    for name, steps in steps_by_name.items():
        app = make_app()
        snap = await asyncio.to_thread(app.collector)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app.apply_snapshot(snap)
            await pilot.pause()
            await steps(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            path = out / f"{prefix}_{name}.svg"
            path.write_text(app.export_screenshot(title=f"Agent Control Desk: {name.replace('_', ' ')}"), encoding="utf-8")
            made.append(path)
    return made


async def _setting(app, pilot, agent: int, name: str) -> bool:
    app.open_tree(agent)
    await pilot.pause()
    if name not in app.screen.settings:
        return False
    app.screen.query_one("#tree").highlighted = app.screen.settings.index(name)
    await pilot.press("enter")
    await pilot.pause()
    return True


async def render_v4(out: Path, agent: int = 0, size: tuple[int, int] = (200, 78), prefix: str = "v4",
                    usage: bool = True) -> list[Path]:
    """Version four's pictures from the REAL files, read-only and never armed: the grid with login health, one
    agent's tree with the sign-in and allowance rows, the assistants' section, and one voice-switch pop-up.
    With usage=True Hermes' own usage check is run ONCE per service first (a status check, no model call)."""
    import acd_edit as E
    import acd_health as H
    import acd_watch as W
    paths = D.Paths.default()
    reader = D.LogReader(paths.profiles)
    edit = E.EditPaths.default(launcher_armed=False)
    meter = H.UsageReader() if usage else None
    if meter is not None:
        first = await asyncio.to_thread(lambda: D.collect(paths, reader, desk=E.record(edit)))
        services: dict = {}
        for v in first.agents:
            for h in v.health:
                services.setdefault(h.service, v.profile)
        await asyncio.to_thread(meter.refresh, services)

    def collector() -> D.Snapshot:
        return D.collect(paths, reader, desk=E.record(edit), usage=(meter.cached() or None) if meter else None,
                         watch=W.view(edit))

    def make_app():
        return acd.Desk(collector=collector, autostart=False, edit=edit)

    async def grid(app, pilot):
        pass

    async def tree(app, pilot):
        app.open_tree(agent)
        await pilot.pause()

    async def assistants(app, pilot):
        await pilot.press("a")
        await pilot.pause()

    async def place(app, pilot):
        await _setting(app, pilot, 1, "vplace:tg_group")

    return await _shots(out, prefix, size, make_app, {"grid": grid, "tree": tree, "assistants": assistants,
                                                      "voice_place_popup": place})


async def render_v4_drift(out: Path, size: tuple[int, int] = (190, 64), prefix: str = "v4") -> list[Path]:
    """The drift pop-up, drawn from a THROW-AWAY world (nothing has drifted on the real system, and the desk has
    never been armed there). The world lives in a temp folder; nothing real is read or written."""
    import datetime as dt
    import acd_edit as E
    import acd_watch as W
    from tests import acdfix as F
    w = F.edit_world(armed=True)
    when = dt.datetime(2026, 11, 17, 10, 0, 0)
    E.change_model("assistant", "claude-sonnet-5-5", w.edit, loader=F.lines_loader, now=when)
    cfg = w.profiles / "assistant" / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("default: claude-sonnet-5-5", "default: claude-haiku-5-5", 1),
                   encoding="utf-8")
    now = when.timestamp() + 900

    def collector() -> D.Snapshot:
        return D.collect(w.paths, yaml_loader=F.lines_loader, now=now, desk=E.record(w.edit),
                         watch=W.view(w.edit, now=now))

    def make_app():
        return acd.Desk(collector=collector, autostart=False, edit=w.edit, loader=F.lines_loader, tester=None,
                        watching=False)

    async def drift(app, pilot):
        await _setting(app, pilot, 1, "drift:0")

    async def drift_tree(app, pilot):
        app.open_tree(1)
        await pilot.pause()

    return await _shots(out, prefix, size, make_app, {"drift_popup_demo": drift, "drift_tree_demo": drift_tree})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--agent", type=int, default=0, help="which row's tree to draw (0 = the first)")
    ap.add_argument("--size", default="190x58", help="columns x rows")
    ap.add_argument("--prefix", default="v1")
    ap.add_argument("--v2", action="store_true", help="draw version two's pictures (the pop-ups too); never armed")
    ap.add_argument("--v3", action="store_true", help="draw version three's pictures (Team Room switch, talk rules)")
    ap.add_argument("--v4", action="store_true", help="draw version four's pictures (sign-in, allowance, drift, voices)")
    ap.add_argument("--no-usage", action="store_true", help="with --v4: do not run Hermes' usage check")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.lower().split("x"))
    if a.v4:
        size = (w, h) if a.size != "190x58" else (200, 78)
        made = asyncio.run(render_v4(a.out, agent=a.agent, size=size, prefix=a.prefix if a.prefix != "v1" else "v4",
                                     usage=not a.no_usage))
        made += asyncio.run(render_v4_drift(a.out, prefix=a.prefix if a.prefix != "v1" else "v4"))
    elif a.v3:
        made = asyncio.run(render_v3(a.out, agent=a.agent or 1, size=(w, h),
                                     prefix=a.prefix if a.prefix != "v1" else "v3"))
    elif a.v2:
        made = asyncio.run(render_v2(a.out, agent=a.agent, size=(w, h), prefix=a.prefix if a.prefix != "v1" else "v2"))
    else:
        made = asyncio.run(render(a.out, agent=a.agent, size=(w, h), prefix=a.prefix))
    for p in made:
        print(p)


if __name__ == "__main__":
    main()
