"""Outcome ledger: every enqueued URL ends in exactly one outcome class."""

OUTCOMES = ("ok", "thin_escalated_ok", "skipped_no_content", "failed_fetch", "failed_exception")
FAILURE_OUTCOMES = ("skipped_no_content", "failed_fetch", "failed_exception")


class Ledger:
    """Every enqueued URL ends in exactly one outcome class. No silent failures."""

    def __init__(self):
        self.enqueued = set()
        self.records = {}
        self.excluded = {}

    def enqueue(self, url):
        self.enqueued.add(url)

    def exclude(self, reason):
        self.excluded[reason] = self.excluded.get(reason, 0) + 1

    def record(
        self,
        url,
        outcome,
        reason="",
        tier="static",
        chars=0,
        file="",
        status=None,
        escalated=False,
        elapsed_ms=None,
    ):
        """Outcome is always supplied by the caller — never inferred from the tier."""
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {outcome}")
        self.enqueued.add(url)
        self.records[url] = {
            "url": url,
            "outcome": outcome,
            "reason": reason,
            "tier": tier,
            "escalated": bool(escalated),
            "status": status,
            "chars": chars,
            "file": file,
            "elapsed_ms": elapsed_ms,
            "platform": None,
        }

    def reconcile(self):
        """Anything enqueued but never recorded was dropped by the engine — say so."""
        for url in self.enqueued - set(self.records):
            self.record(url, "failed_fetch", "no response recorded (dropped after retries)")
        return len(self.records) == len(self.enqueued)

    def counts(self):
        counts = dict.fromkeys(OUTCOMES, 0)
        for record in self.records.values():
            counts[record["outcome"]] += 1
        return counts

    def failures(self):
        return [r for r in self.records.values() if r["outcome"] in FAILURE_OUTCOMES]


def report_md(ledger: Ledger, info: dict) -> str:
    counts = ledger.counts()
    lines = [
        "# Scrape report",
        "",
        *(f"- **{k}**: {v}" for k, v in info.items()),
        f"- **pages enqueued**: {len(ledger.enqueued)}",
        "",
        "## Outcomes",
        "",
        "| Class | Pages |",
        "|---|---|",
        *(f"| {name} | {count} |" for name, count in counts.items()),
        f"| **total** | **{sum(counts.values())}** |",
        "",
        f"Ledger reconciles: {sum(counts.values())} recorded of "
        f"{len(ledger.enqueued)} enqueued "
        f"({'OK — zero silent failures' if ledger.reconcile() else 'MISMATCH'}).",
        "",
        "## Excluded before fetch (count only)",
        "",
        "| Reason | URLs |",
        "|---|---|",
        *(f"| {reason} | {count} |" for reason, count in sorted(ledger.excluded.items())),
        "",
        "## Failures",
        "",
    ]
    failures = ledger.failures()
    if not failures:
        lines.append("None.")
    else:
        lines += ["| URL | Class | Tier | Reason |", "|---|---|---|---|"]
        lines += [
            f"| {r['url']} | {r['outcome']} | {r['tier']} | {r['reason']} |" for r in failures
        ]
    return "\n".join(lines) + "\n"
