"""Adapter types. Types only — no platform adapters, no fetch stack imports."""

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class Fingerprint:
    """What a platform detector is allowed to look at."""

    url: str
    status: int
    headers: dict  # lowercased keys
    html: str


@dataclass(frozen=True)
class Doc:
    url: str
    markdown: str


@dataclass(frozen=True)
class Ctx:
    """Everything an adapter's map_site/harvest needs from the run."""

    origin: str
    scope: str
    max_depth: int
    get: Callable[[str], object]
    ledger: object
    emit: Callable[[dict], None]


@dataclass(frozen=True)
class Adapter:
    name: str
    detect: Callable[[Fingerprint], bool]
    fetch_mode: str = "html"
    fetch_strategy: str = "static"
    content_selector: Optional[str] = None
    nav_selector: Optional[str] = None
    known_fail: Optional[str] = None  # reason string
    map_site: Optional[Callable[[Ctx], Optional[list]]] = field(default=None)
    harvest: Optional[Callable[[Ctx], object]] = field(default=None)
