"""Run manifest. Schema is versioned: downstream phases pin on it."""

SCHEMA = 1


def build_manifest(info: dict, ledger, reconciled: bool) -> dict:
    # Corpus size estimated at 4 chars/token over the written pages.
    tokens = sum(r["chars"] for r in ledger.records.values() if r.get("file")) // 4
    return {
        "schema": SCHEMA,
        "info": info,
        "counts": ledger.counts(),
        "excluded": ledger.excluded,
        "reconciled": reconciled,
        "token_estimate": tokens,
        # Site order (first enqueue), not URL order: `iofast combine` reads it.
        "pages": sorted(ledger.records.values(), key=lambda r: ledger.order[r["url"]]),
    }
