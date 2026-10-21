"""Platform adapter registry. Detection is data, not dispatch — never subclass Adapter."""

from .base import Adapter, Ctx, Doc, Fingerprint
from .confluence import ADAPTER as CONFLUENCE
from .document360 import ADAPTER as DOCUMENT360
from .docsify import ADAPTER as DOCSIFY
from .docusaurus import ADAPTER as DOCUSAURUS
from .gitbook import ADAPTER as GITBOOK
from .gravitee import ADAPTER as GRAVITEE
from .mintlify import ADAPTER as MINTLIFY
from .notion import ADAPTER as NOTION
from .readme import ADAPTER as README
from .readthedocs import ADAPTER as READTHEDOCS
from .redocly import ADAPTER as REDOCLY
from .swaggerhub import ADAPTER as SWAGGERHUB

__all__ = ["ADAPTERS", "Adapter", "Ctx", "Doc", "Fingerprint", "detect_platform"]

ADAPTERS: tuple[Adapter, ...] = (
    GITBOOK,
    MINTLIFY,
    DOCUSAURUS,
    README,
    REDOCLY,
    DOCSIFY,
    DOCUMENT360,
    READTHEDOCS,
    SWAGGERHUB,
    CONFLUENCE,
    NOTION,
    GRAVITEE,
)


def detect_platform(fp: Fingerprint, emit=None):
    """First adapter whose detect() matches, or None. >1 match is a registry bug."""
    emit = emit or (lambda event: None)
    matched = []
    for adapter in ADAPTERS:
        try:
            if adapter.detect(fp):
                matched.append(adapter)
        except Exception:
            continue
    if len(matched) > 1:
        emit(
            {
                "type": "warning",
                "warning": "ambiguous_platform",
                "url": fp.url,
                "adapters": [a.name for a in matched],
                "message": (
                    f"⚠️  ambiguous_platform: {', '.join(a.name for a in matched)} "
                    f"both match {fp.url}"
                ),
            }
        )
    return matched[0] if matched else None
