/** Shapes, formatting and the large-crawl predicate, shared by both windows. */

/** Mirrors `Config` in src-tauri/src/store.rs. */
export type Config = {
  library_root: string;
  large_crawl_threshold: number;
  interactive: boolean;
  file_to_vault?: boolean;
  vault_path?: string;
  whisper_model?: string;
  vault_check_schedule?: string;
  digest_schedule?: string;
};
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
  /** `manifest.quality.verdict`. `clean` asks whether every URL landed; this asks
   *  whether what landed is real prose. "" on runs written before it existed. */
  quality: string;
  cancelled: boolean;
  modified_ms: number;
  /** A stopped crawl left `.crawl/checkpoint.pkl` — the Resume action keys on it. */
  resumable: boolean;
};

/** One row of schedules.json (schedule_get): the run's auto re-scrape cadence.
 *  `proxy` is the per-run proxy toggle; absent = on (pre-toggle schedules). */
export type Schedule = { every: "daily" | "weekly"; last_ms: number; proxy?: boolean };

/** docs/RPC.md `browser_status`: out-of-band browser-component state. */
export type BrowserStatus = {
  installed: boolean;
  drivers: boolean;
  chromium: boolean;
  path: string;
};

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

/**
 * Remaining human-solve time as m:ss, for the CAPTCHA banner's countdown.
 * Clamped at zero: the engine stops waiting at its own deadline, and a banner
 * counting into negatives would claim the window is still live when it is not.
 * Rounds up, so the last visible second is 0:01 rather than a premature 0:00.
 * Checked by scripts/lib.check.ts.
 */
export function countdown(msLeft: number): string {
  const secs = Math.max(0, Math.ceil(msLeft / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
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
