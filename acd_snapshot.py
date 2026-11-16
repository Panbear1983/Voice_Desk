"""Draw the Agent Control Desk without a window: the grid and one agent's tree, saved as pictures (SVG).

    .venv/bin/python acd_snapshot.py --out docs/acd_mockups            # the real files on this Mac, read-only
    .venv/bin/python acd_snapshot.py --out /tmp/x --agent 2 --size 190x58

    .venv/bin/python acd_snapshot.py --out docs/acd_mockups --v2       # version two: the pop-ups as well

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--agent", type=int, default=0, help="which row's tree to draw (0 = the first)")
    ap.add_argument("--size", default="190x58", help="columns x rows")
    ap.add_argument("--prefix", default="v1")
    ap.add_argument("--v2", action="store_true", help="draw version two's pictures (the pop-ups too); never armed")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.lower().split("x"))
    if a.v2:
        made = asyncio.run(render_v2(a.out, agent=a.agent, size=(w, h), prefix=a.prefix if a.prefix != "v1" else "v2"))
    else:
        made = asyncio.run(render(a.out, agent=a.agent, size=(w, h), prefix=a.prefix))
    for p in made:
        print(p)


if __name__ == "__main__":
    main()
