"""Source protocol and shared types for the multi-source grab layer."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass
class GrabOptions:
    """Tunables passed to every source. Each field is optional; a source
    ignores what doesn't apply (youtube ignores limit, web ignores lang)."""

    mode: str = "page"  # "page" | "site" (site only meaningful for source "web")
    limit: int | None = None  # max items for profiles/feeds (instagram, x)
    lang: str | None = None  # preferred transcript language (youtube)


@dataclass
class GrabResult:
    """Outcome of one grab call. Mirrors the ledger counters so the caller
    can aggregate across sources without reaching into run_dir."""

    source: str  # "web" | "youtube" | "github" | "instagram" | "x"
    run_dir: Path  # the run folder written, same layout as a docs scrape
    ok: int
    thin: int
    failed: int


class Source(Protocol):
    """One content source. detect() walks the registry and calls matches();
    the first hit wins, with WebSource as the universal fallback."""

    name: str

    def matches(self, url: str) -> bool: ...

    async def grab(
        self, url: str, out_root: Path, opts: GrabOptions, emit: Callable
    ) -> GrabResult: ...