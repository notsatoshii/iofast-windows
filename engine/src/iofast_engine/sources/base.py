"""Source protocol and shared types for non-docs captures (instagram, x, …).

Another worker creates this file on src/web-yt-gh; this copy matches the
contract in docs/WINDOWS-PLAN.md byte-for-byte so the merge is trivial.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass
class GrabOptions:
    mode: str = "page"          # "page" | "site"  (site only meaningful for source "web")
    limit: int | None = None    # max items for profiles/feeds (instagram, x)
    lang: str | None = None     # preferred transcript language (youtube)


@dataclass
class GrabResult:
    source: str                 # "web" | "youtube" | "github" | "instagram" | "x"
    run_dir: Path               # the run folder written, same layout as a docs scrape
    ok: int
    thin: int
    failed: int


class Source(Protocol):
    name: str

    def matches(self, url: str) -> bool: ...

    async def grab(
        self,
        url: str,
        out_root: Path,
        opts: GrabOptions,
        emit: Callable[[dict], None],
    ) -> GrabResult: ...