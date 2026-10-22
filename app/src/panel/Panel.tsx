import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";

import { countdown, DEFAULT_THRESHOLD, overThreshold, type Boot } from "../lib";
import { parseTarget, scopePrefix } from "./scope";

/** docs/RPC.md: `params` of an `event` notification IS the engine event dict. */
type EngineEvent = {
  type: string;
  rid?: number;
  n?: number;
  message?: string;
  url?: string;
  /** `human_required` only: the human-solve budget, in seconds. */
  timeout?: number;
};
type ScrapeResult = {
  written: number;
  out_dir: string;
  exit_code: number;
  cancelled: boolean;
};
type ScrapeDone = { rid: number; result?: ScrapeResult; error?: string };
type EstimateResult = {
  page_count: number | null;
  source: string | null;
  scope: string;
  elapsed_ms: number;
};
type MapNode = { pages: number; total: number; children: Record<string, MapNode> };
type MapResult = { tree: MapNode; scope: string };

type Section = { seg: string; total: number };
type MapLanded = { total: number; sections: Section[] };

/** Pre-Enter annotation. `gone` = we stopped waiting; the scrape never did. */
type Estimate =
  | { k: "none" }
  | { k: "estimating" }
  | { k: "landed"; pages: number | null }
  | { k: "gone" };

type Phase =
  | { k: "idle" }
  | { k: "running" }
  | { k: "done"; written: number; outDir: string }
  | { k: "stopped"; written: number; outDir: string }
  | { k: "failed"; message: string; outDir?: string };

type Run = { rid: number; url: string; scope: string; out: string };
/** A visible browser window is waiting on the user (docs/RPC.md `human_required`).
 *  Absolute deadline, not a remaining count, so the countdown cannot drift. */
type Solve = { deadline: number };

const DEBOUNCE_MS = 250;
/** Client-side abandon. docs/RPC.md: the server's own budget is 800ms. */
const ABANDON_MS = 1000;
/** Card gutter, kept in sync with --gutter in panel.css. */
const GUTTER = 10;

const reason = (e: unknown) => (e instanceof Error ? e.message : String(e));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Engine messages lead with an emoji and sometimes a newline. Strip to words. */
const tidy = (message: string) =>
  message.replace(/\s+/g, " ").replace(/^[^\p{L}\p{N}]+/u, "").trim().slice(0, 120);

const tail = (path: string) => `…/${path.split("/").slice(-2).join("/")}`;

export default function Panel() {
  const [url, setUrl] = useState("");
  const [showSeq, setShowSeq] = useState(0);
  const [estimate, setEstimate] = useState<Estimate>({ k: "none" });
  const [phase, setPhase] = useState<Phase>({ k: "idle" });
  const [pages, setPages] = useState(0);
  const [note, setNote] = useState("");
  const [map, setMap] = useState<MapLanded | null>(null);
  const [narrowing, setNarrowing] = useState(false);
  /** Set when Enter hit the large-crawl threshold; cleared by either answer. */
  const [gate, setGate] = useState<number | null>(null);
  const [solve, setSolve] = useState<Solve | null>(null);
  const [left, setLeft] = useState(0);
  const threshold = useRef(DEFAULT_THRESHOLD);

  const inputRef = useRef<HTMLInputElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const runRef = useRef<Run | null>(null);
  /** Scope to restart with once the cancelled run lands (Narrow). */
  const restartRef = useRef<string | null>(null);
  const estSeq = useRef(0);
  const lastEstimateError = useRef<string | null>(null);
  const immediate = useRef(false);
  const timing = useRef({ shown: 0, interactive: 0, sent: true });

  const target = parseTarget(url);
  const scope = target ? scopePrefix(target) : null;

  /** One `panel-timing` per show; Rust logs it. The 1s budget needs a number. */
  const report = (outcome: "landed" | "abandoned" | "skipped", ms: number | null) => {
    if (timing.current.sent) return;
    timing.current.sent = true;
    void emit("panel-timing", {
      show_to_interactive_ms: Math.round(timing.current.interactive),
      estimate_ms: ms === null ? null : Math.round(ms),
      estimate_outcome: outcome,
      estimate_error: lastEstimateError.current,
    });
  };

  const runEstimate = useCallback(async (href: string) => {
    const seq = ++estSeq.current;
    setEstimate({ k: "estimating" });
    performance.mark("iofast:estimate-fired");
    const started = performance.now();
    const landed = await Promise.race([
      invoke<EstimateResult>("sidecar_estimate", { url: href }).catch((error) => {
        lastEstimateError.current = String(error);
        return null;
      }),
      new Promise<null>((done) => setTimeout(() => done(null), ABANDON_MS)),
    ]);
    if (seq !== estSeq.current) return; // superseded by a keystroke or a new show
    const elapsed = performance.now() - started;
    performance.measure(
      landed ? "iofast:estimate-landed" : "iofast:estimate-abandoned",
      "iofast:estimate-fired",
    );
    setEstimate(landed ? { k: "landed", pages: landed.page_count } : { k: "gone" });
    report(landed ? "landed" : "abandoned", elapsed);
  }, []);

  const startScrape = useCallback(async (href: string, at: string, out: string) => {
    setPages(0);
    setNote("");
    setPhase({ k: "running" });
    try {
      const rid = await invoke<number>("sidecar_scrape_start", {
        url: href,
        out,
        scope: at,
        depth: null,
        maxPages: null,
      });
      runRef.current = { rid, url: href, scope: at, out };
    } catch (e) {
      setPhase({ k: "failed", message: reason(e) });
    }
  }, []);

  // Speed 1. Debounced while typing, immediate on a clipboard prefill. Never
  // gates Enter — the estimate only ever writes the annotation line.
  useEffect(() => {
    if (phase.k !== "idle") return; // a finished run owns the annotation line
    if (!target) {
      estSeq.current++;
      setEstimate({ k: "none" });
      return;
    }
    const delay = immediate.current ? 0 : DEBOUNCE_MS;
    immediate.current = false;
    const href = target.href;
    const timer = setTimeout(() => void runEstimate(href), delay);
    return () => clearTimeout(timer);
    // target is derived from url; showSeq re-fires when the same URL is re-shown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, showSeq, phase.k, runEstimate]);

  // The gate threshold is a setting; the panel reads it once and never writes it.
  useEffect(() => {
    invoke<Boot>("config_get")
      .then((boot) => {
        if (boot.config) threshold.current = boot.config.large_crawl_threshold;
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const shown = listen<{ clipboard: string | null; active_url?: string | null }>("panel-shown", (e) => {
      estSeq.current++;
      runRef.current = null;
      restartRef.current = null;
      setPhase({ k: "idle" });
      setPages(0);
      setNote("");
      setMap(null);
      setNarrowing(false);
      setGate(null);
      setEstimate({ k: "none" });

      performance.mark("iofast:panel-shown");
      timing.current = { shown: performance.now(), interactive: 0, sent: false };
      // Prefer the active browser URL (from UI Automation on Windows) over
      // clipboard text — it's what the user is actually looking at.
      const raw = e.payload.active_url || e.payload.clipboard || "";
      const pasted = parseTarget(raw);
      immediate.current = pasted !== null;
      setUrl(pasted ? pasted.href : "");
      setShowSeq((n) => n + 1);
      inputRef.current?.focus();
      inputRef.current?.select();
      requestAnimationFrame(() => {
        timing.current.interactive = performance.now() - timing.current.shown;
        performance.mark("iofast:panel-interactive");
        performance.measure(
          "iofast:show-to-interactive",
          "iofast:panel-shown",
          "iofast:panel-interactive",
        );
        if (!pasted) report("skipped", null);
      });
    });

    const events = listen<EngineEvent>("engine-event", (e) => {
      const run = runRef.current;
      if (!run || e.payload.rid !== run.rid) return;
      if (e.payload.type === "human_required") {
        setSolve({ deadline: Date.now() + (e.payload.timeout ?? 0) * 1000 });
        return;
      }
      if (e.payload.type === "human_done") {
        setSolve(null);
        return;
      }
      if (typeof e.payload.n === "number") setPages(e.payload.n);
      if (typeof e.payload.message === "string") setNote(tidy(e.payload.message));
    });

    const done = listen<ScrapeDone>("scrape-done", (e) => {
      const run = runRef.current;
      if (!run || e.payload.rid !== run.rid) return;
      const restart = restartRef.current;
      restartRef.current = null;
      runRef.current = null;
      setSolve(null);  // a sidecar that dies mid-solve emits no human_done
      if (restart) {
        void startScrape(run.url, restart, run.out);
        return;
      }
      const { result, error } = e.payload;
      if (error || !result) {
        setPhase({ k: "failed", message: error ?? "the sidecar returned nothing" });
      } else if (result.cancelled) {
        setPhase({ k: "stopped", written: result.written, outDir: result.out_dir });
      } else if (result.exit_code !== 0) {
        setPhase({
          k: "failed",
          message: `engine exit ${result.exit_code} after ${plural(result.written, "page")}`,
          outDir: result.written > 0 ? result.out_dir : undefined,
        });
      } else {
        setPhase({ k: "done", written: result.written, outDir: result.out_dir });
      }
    });

    const all = Promise.all([shown, events, done]);
    return () => {
      void all.then((fns) => fns.forEach((f) => f()));
    };
  }, [startScrape]);

  // Tick the human-solve countdown against an absolute deadline, so a slow
  // paint never reads as more time than the engine actually allows.
  useEffect(() => {
    if (!solve) return;
    const tick = () => setLeft(solve.deadline - Date.now());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [solve]);

  // Transparent window: an oversized one would eat clicks meant for what is
  // behind it, so the window tracks the card instead of the other way round.
  useLayoutEffect(() => {
    const card = cardRef.current;
    if (!card) return;
    const observer = new ResizeObserver(() => {
      const height = Math.ceil(card.getBoundingClientRect().height) + GUTTER * 2;
      void invoke("panel_resize", { height });
    });
    observer.observe(card);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (gate !== null || narrowing) {
        setGate(null);
        setNarrowing(false);
        inputRef.current?.focus();
        return;
      }
      void invoke("panel_hide");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [narrowing, gate]);

  // Speed 2. Scope is derived locally, so this never waits on the estimate.
  const submit = async (confirmed = false) => {
    if (!target || !scope || phase.k === "running") return;
    // Large-crawl gate: only an estimate that actually landed can trip it, so
    // Enter still commits immediately whenever the pre-Enter pass came back empty.
    if (
      !confirmed &&
      estimate.k === "landed" &&
      estimate.pages !== null &&
      overThreshold(estimate.pages, threshold.current)
    ) {
      setGate(estimate.pages);
      return;
    }
    setGate(null);
    estSeq.current++;
    setMap(null);
    setNarrowing(false);
    let out: string;
    try {
      out = await invoke<string>("library_dir", { host: target.host, scope });
    } catch (e) {
      setPhase({ k: "failed", message: reason(e) });
      return;
    }
    await startScrape(target.href, scope, out);
    // Concurrent by contract (docs/RPC.md `map`): an offer, never a gate.
    invoke<MapResult>("sidecar_map", { url: target.href, scope })
      .then((r) =>
        setMap({
          total: r.tree.total,
          sections: Object.entries(r.tree.children)
            .map(([seg, node]) => ({ seg, total: node.total }))
            .sort((a, b) => b.total - a.total),
        }),
      )
      .catch(() => undefined);
  };

  const cancel = () => {
    const run = runRef.current;
    if (!run) return;
    setNote("stopping…");
    void invoke("sidecar_cancel", { rid: run.rid }).catch(() => undefined);
  };

  /** Cancel the running scrape, then restart it under the chosen section. */
  const narrowTo = (seg: string) => {
    const run = runRef.current;
    if (!run) return;
    restartRef.current = `${run.scope}${seg}/`;
    setNarrowing(false);
    setMap(null); // the tree described the old scope
    setNote("switching scope…");
    void invoke("sidecar_cancel", { rid: run.rid }).catch(() => undefined);
  };

  const running = phase.k === "running";
  const outDir =
    phase.k === "done" || phase.k === "stopped" ? phase.outDir : phase.k === "failed" ? phase.outDir : undefined;

  return (
    <div className="panel" ref={cardRef} data-running={running ? "1" : "0"}>
      <div className="field">
        <svg className="mark" viewBox="0 0 16 16" aria-hidden="true">
          <rect x="1.75" y="1.75" width="12.5" height="12.5" rx="3.25" />
          <path d="M8 4.75v5.5m0 0L5.9 8.2M8 10.25l2.1-2.05" />
        </svg>
        <input
          ref={inputRef}
          className="url"
          type="text"
          value={url}
          spellCheck={false}
          autoComplete="off"
          autoCorrect="off"
          aria-label="Documentation URL"
          placeholder="Paste a docs URL"
          onChange={(e) => {
            if (phase.k !== "idle" && phase.k !== "running") setPhase({ k: "idle" });
            setGate(null);
            setUrl(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit(gate !== null);
            }
          }}
        />
        {target && !running && <kbd className="enter">↵</kbd>}
      </div>

      <div className="meta">
        <div className="meta-text" aria-live="polite">
          {running ? (
            <>
              <span className="count">{plural(pages, "page")}</span>
              <span className="note">{note}</span>
            </>
          ) : phase.k === "done" ? (
            <>
              <span className="count">{plural(phase.written, "page")}</span>
              <span className="note" title={phase.outDir}>
                {tail(phase.outDir)}
              </span>
            </>
          ) : phase.k === "stopped" ? (
            <span className="note strong">Stopped — {plural(phase.written, "page")} kept</span>
          ) : phase.k === "failed" ? (
            <span className="note bad">{phase.message}</span>
          ) : url.trim() === "" ? null : !target ? (
            <span className="note">not a URL yet</span>
          ) : estimate.k === "estimating" ? (
            <span className="note pulse">sizing {scope}</span>
          ) : estimate.k === "landed" && estimate.pages !== null ? (
            <span className="note">
              <span className="approx">≈</span> {plural(estimate.pages, "page")} · {scope}
            </span>
          ) : estimate.k === "none" ? (
            <span className="note">{scope}</span>
          ) : (
            <span className="note">no estimate · {scope}</span>
          )}
        </div>
        <div className="meta-action">
          {running ? (
            <button type="button" className="btn" onClick={cancel}>
              Cancel
            </button>
          ) : outDir ? (
            <button
              type="button"
              className="btn"
              onClick={() => void invoke("open_path", { path: outDir })}
            >
              Open folder
            </button>
          ) : null}
        </div>
      </div>

      {gate !== null && (
        <div className="gate">
          <span>This site maps to ≈{plural(gate, "page")} — scrape anyway?</span>
          <div className="acts">
            <button type="button" className="btn go" onClick={() => void submit(true)}>
              Scrape {gate}
            </button>
            <button type="button" className="btn" onClick={() => setGate(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {solve && (
        <div className="banner solve" role="status">
          <span>
            Solve the challenge in the browser window
            <span className="solve-left" data-low={left <= 30_000 ? "1" : "0"}>
              {countdown(left)}
            </span>
          </span>
          {/* No Stop here: the panel's own Cancel is two rows up and does the
              same thing (one cancel channel per run — see App.tsx). */}
        </div>
      )}

      {running && map && map.sections.length > 0 && (
        <div className="banner">
          <span>
            {plural(map.total, "page")} in {plural(map.sections.length, "section")}
          </span>
          <button type="button" className="link" onClick={() => setNarrowing((v) => !v)}>
            {narrowing ? "Keep all" : "Narrow"}
          </button>
        </div>
      )}

      {running && narrowing && map && (
        <ul className="sections">
          {map.sections.map((section) => (
            <li key={section.seg}>
              <button type="button" onClick={() => narrowTo(section.seg)}>
                <span className="seg">{section.seg}/</span>
                <span className="n">{section.total}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
