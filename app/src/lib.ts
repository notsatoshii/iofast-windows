/** Shapes, formatting and the large-crawl predicate, shared by both windows. */

/** Mirrors `Config` in src-tauri/src/store.rs. */
export type Config = { library_root: string; large_crawl_threshold: number };
/** `config_get`: a null config means first run. */
export type Boot = { config: Config | null; suggested_root: string };

/** One library row, read from that run's manifest.json by `library_list`. */
export type Run = {
  dir: string;
  host: string;
  scope: string;
  url: string;
  pages: number;
  tokens: number;
  clean: boolean;
  cancelled: boolean;
  modified_ms: number;
  /** A stopped crawl left `.crawl/checkpoint.pkl` — the Resume action keys on it. */
  resumable: boolean;
};

/** One row of schedules.json (schedule_get): the run's auto re-scrape cadence. */
export type Schedule = { every: "daily" | "weekly"; last_ms: number };

export const reason = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Used until `config_get` lands, and by the panel if settings were never opened. */
export const DEFAULT_THRESHOLD = 300;

/**
 * The large-crawl gate, shared so the panel and the main window cannot drift.
 * A count we do not have (`null` — the estimate was abandoned, or the site
 * publishes no sitemap) never blocks a run: the gate exists to warn, not to
 * stand between the user and a site we failed to size. Strictly greater, so a
 * threshold of 300 lets exactly 300 pages through.
 * Checked by scripts/lib.check.ts.
 */
export const overThreshold = (count: number | null, limit: number): boolean =>
  count !== null && limit > 0 && count > limit;

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Corpus size: the number that decides whether it fits in a context window. */
export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/** Coarse on purpose — the useful question is "is this stale", not "when". */
export function when(ms: number, now = Date.now()): string {
  const mins = Math.floor((now - ms) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 48 * 60) return `${Math.floor(mins / 60)}h ago`;
  if (mins < 30 * 24 * 60) return `${Math.floor(mins / (24 * 60))}d ago`;
  return new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
