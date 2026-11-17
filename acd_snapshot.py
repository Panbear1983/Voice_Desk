"""Draw the Agent Control Desk without a window: the one table and its menus, saved as pictures (SVG).

    .venv/bin/python acd_snapshot.py --out docs/acd_mockups            # the real files on this Mac, read-only
    .venv/bin/python acd_snapshot.py --out /tmp/x --size 200x62 --narrow 120x50 --no-usage

It draws: the table, the table in a narrow window (the row names pinned, the agent columns scrolling sideways),
a cell's menu open, the confirm line after a choice, a row-wide change with each agent's before and after, the
standard-ladder before and after, and the listener pop-up.

Nothing appears on the screen and nothing is written except the pictures: the desk is never armed here, no
confirm step is ever answered with yes, and the standard-ladder plan only reads. Used for checks and for the
read-me's screenshots, so no test ever needs a real window.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Callable, Optional

import acd
import acd_data as D


async def _ready(app, pilot, snap: D.Snapshot) -> None:
    await pilot.pause()
    await asyncio.to_thread(app.learn_speeds, snap)
    app.apply_snapshot(snap)
    await pilot.pause()


async def render(out: Path, collector: Optional[Callable[[], D.Snapshot]] = None, size: tuple[int, int] = (200, 62),
                 prefix: str = "table", desk_kwargs: Optional[dict] = None) -> Path:
    """One picture of the table. With a collector (a test's fake world) the desk reaches nothing real."""
    out.mkdir(parents=True, exist_ok=True)
    kwargs = dict(desk_kwargs or {})
    kwargs.pop("launcher_armed", None)                            # a picture run is never armed
    if collector is None:
        paths = D.Paths.default()
        collector = lambda: D.collect(paths)                     # noqa: E731
    snap = await asyncio.to_thread(collector)
    app = acd.Desk(collector=lambda: snap, autostart=False, **kwargs)
    path = out / f"{prefix}.svg"
    async with app.run_test(size=size) as pilot:
        await _ready(app, pilot, snap)
        path.write_text(app.export_screenshot(title="Agent Control Desk"), encoding="utf-8")
    return path


async def _shots(out: Path, prefix: str, make_app, steps_by_name: dict) -> list[Path]:
    made: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)
    for name, (size, steps) in steps_by_name.items():
        app = make_app()
        snap = await asyncio.to_thread(app.collector)
        async with app.run_test(size=size) as pilot:
            await _ready(app, pilot, snap)
            await steps(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            path = out / f"{prefix}_{name}.svg"
            path.write_text(app.export_screenshot(title=f"Agent Control Desk: {name.replace('_', ' ')}"),
                            encoding="utf-8")
            made.append(path)
    return made


async def render_table(out: Path, collector: Optional[Callable[[], D.Snapshot]] = None,
                       size: tuple[int, int] = (200, 62), narrow: tuple[int, int] = (120, 50), prefix: str = "table",
                       desk_kwargs: Optional[dict] = None, agent: str = "Kylie", usage: bool = True) -> list[Path]:
    """Every picture of the table build. Without a collector the REAL files are read (read-only, never armed);
    with usage=True Hermes' own usage check is run ONCE per service first (a status check, no model call)."""
    kwargs = dict(desk_kwargs or {})
    kwargs.pop("launcher_armed", None)
    if collector is None:
        import acd_edit as E
        import acd_health as H
        import acd_parity as P
        import acd_watch as W
        paths = D.Paths.default()
        reader = D.LogReader(paths.profiles)
        edit = E.EditPaths.default(launcher_armed=False)
        meter = H.UsageReader() if usage else None              # no keep file: a picture run writes only pictures
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

        kwargs = {"edit": edit, "listener": P.RealListener(), "watching": False, **kwargs}

    def make_app():
        return acd.Desk(collector=collector, autostart=False, **kwargs)

    def col(app) -> str:
        names = [c.name for c in app.screen.cols]
        return agent if agent in names else (names[1] if len(names) > 1 else names[0])

    async def table(app, pilot):
        pass

    async def menu(app, pilot):
        app.screen.go("model", col(app))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    async def confirm(app, pilot):
        await menu(app, pilot)
        await pilot.press("down")
        await pilot.press("enter")                               # the confirm line, left unanswered
        await pilot.pause()

    async def ladder_menu(app, pilot):
        app.screen.go("rung:codex", col(app))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    async def row_wide(app, pilot):
        app.screen.go("effort", 0)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        if isinstance(app.screen, acd.CellMenu):
            lst = app.screen.query_one("#menu-list")
            first = next((i for i, o in enumerate(app.screen.spec.options) if not o.now and not o.disabled), 0)
            lst.highlighted = first
            await pilot.press("enter")                           # each agent's before and after, left unanswered
            await pilot.pause()

    async def standard(app, pilot):
        await pilot.press("s")                                   # the plan only reads; the question is left unanswered
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def listener(app, pilot):
        await pilot.press("l")
        await pilot.pause()

    return await _shots(out, prefix, make_app, {
        "full": (size, table), "narrow": (narrow, table), "cell_menu": (size, menu), "confirm_line": (size, confirm),
        "ladder_menu": (size, ladder_menu), "row_wide_confirm": (size, row_wide),
        "standard_ladder": (size, standard), "listener_popup": (size, listener)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--size", default="200x62", help="columns x rows")
    ap.add_argument("--narrow", default="120x50", help="the narrow window's columns x rows")
    ap.add_argument("--prefix", default="table")
    ap.add_argument("--agent", default="Kylie", help="whose column the menus are drawn on")
    ap.add_argument("--no-usage", action="store_true", help="do not run Hermes' usage check first")
    a = ap.parse_args()
    size = tuple(int(x) for x in a.size.lower().split("x"))
    narrow = tuple(int(x) for x in a.narrow.lower().split("x"))
    for p in asyncio.run(render_table(a.out, size=size, narrow=narrow, prefix=a.prefix, agent=a.agent,
                                      usage=not a.no_usage)):
        print(p)


if __name__ == "__main__":
    main()
