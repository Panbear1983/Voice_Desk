"""Draw the Agent Control Desk without a window: the grid and one agent's tree, saved as pictures (SVG).

    .venv/bin/python acd_snapshot.py --out docs/acd_mockups            # the real files on this Mac, read-only
    .venv/bin/python acd_snapshot.py --out /tmp/x --agent 2 --size 190x58

Nothing appears on the screen and nothing is written except the two pictures. Used for checks and for the
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--agent", type=int, default=0, help="which row's tree to draw (0 = the first)")
    ap.add_argument("--size", default="190x58", help="columns x rows")
    ap.add_argument("--prefix", default="v1")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.lower().split("x"))
    for p in asyncio.run(render(a.out, agent=a.agent, size=(w, h), prefix=a.prefix)):
        print(p)


if __name__ == "__main__":
    main()
