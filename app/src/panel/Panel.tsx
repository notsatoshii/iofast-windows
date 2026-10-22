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

/** docs/RPC.md `detect`: synchronous URL → source routing. */
type DetectResult = {
  source: string;
  label: string;
  modes: string[];
  needs_signin: boolean;
};

type GrabResult = {
  written: number;
  out_dir: string;
  exit_code: number;
  cancelled: boolean;
  vault_filed?: boolean;
  vault_path?: string;
  source_note?: string;
};
type GrabDone = { rid: number; result?: GrabResult; error?: string };

type Phase =
  | { k: "idle" }
  | { k: "running" }
  | { k: "done"; written: number; outDir: string; vaultPath?: string; sourceNote?: string }
  | { k: "stopped"; written: number; outDir: string }
  | { k: "failed"; message: string; outDir?: string };

type Run = { rid: number; url: string; out: string };
/** A visible browser window is waiting on the user (docs/RPC.md `human_required`).
 *  Absolute deadline, not a remaining count, so the countdown cannot drift. */
type Solve = { deadline: number };

const DEBOUNCE_MS = 250;
/** Client-side abandon for detect. docs/RPC.md: detect is synchronous, fast. */
const ABANDON_MS = 1000;
/** Card gutter, kept in sync with --gutter in panel.css. */
const GUTTER = 10;

const reason = (e: unknown) => (e instanceof Error ? e.message : String(e));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Engine messages lead with an emoji and sometimes a newline. Strip to words. */
const tidy = (message: string) =>
  message.replace(/\s+/g, " ").replace(/^[^\p{L}\p{N}]+/u, "").trim().slice(0, 120);

const tail = (path: string) => `…/${path.split("/").slice(-2).join("/")}`;

/** Source badge label from detect result. */
const sourceLabel = (source: string): string => {
  switch (source) {
    case "web": return "Web";
    case "github": return "GitHub";
    case "youtube": return "YouTube";
    case "instagram": return "Instagram";
    case "x": return "X";
    default: return source;
  }
};

export default function Panel() {
  const [url, setUrl] = useState("");
  const [showSeq, setShowSeq] = useState(0);
  const [phase, setPhase] = useState<Phase>({ k: "idle" });
  const [pages, setPages] = useState(0);
  const [note, setNote] = useState("");
  const [solve, setSolve] = useState<Solve | null>(null);
  const [left, setLeft] = useState(0);
  const [fileToVault, setFileToVault] = useState(false);
  const [detectSource, setDetectSource] = useState<string | null>(null);
  const [needsSignin, setNeedsSignin] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const runRef = useRef<Run | null>(null);
  const detectSeq = useRef(0);
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
      estimate_error: null,
    });
  };

  /** Call sidecar_detect on URL change to show source badge. */
  const runDetect = useCallback(async (href: string) => {
    const seq = ++detectSeq.current;
    setDetectSource(null);
    setNeedsSignin(false);
    const started = performance.now();
    const landed = await Promise.race([
      invoke<DetectResult>("sidecar_detect", { url: href }).catch(() => null),
      new Promise<null>((done) => setTimeout(() => done(null), ABANDON_MS)),
    ]);
    if (seq !== detectSeq.current) return;
    const elapsed = performance.now() - started;
    if (landed) {
      setDetectSource(landed.source);
      setNeedsSignin(landed.needs_signin);
      report("landed", elapsed);
    } else {
      report("abandoned", elapsed);
    }
  }, []);

  const startGrab = useCallback(async (href: string, out: string, file: boolean) => {
    setPages(0);
    setNote("");
    setPhase({ k: "running" });
    try {
      const rid = await invoke<number>("sidecar_grab_start", {
        url: href,
        out,
        mode: "page",
        limit: null,
        lang: null,
        file,
        vault: null,
      });
      runRef.current = { rid, url: href, out };
    } catch (e) {
      setPhase({ k: "failed", message: reason(e) });
    }
  }, []);

  // Debounced detect while typing, immediate on clipboard prefill.
  useEffect(() => {
    if (phase.k !== "idle") return;
    if (!target) {
      detectSeq.current++;
      setDetectSource(null);
      setNeedsSignin(false);
      return;
    }
    const delay = DEBOUNCE_MS;
    const href = target.href;
    const timer = setTimeout(() => void runDetect(href), delay);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, showSeq, phase.k, runDetect]);

  // Read file_to_vault config on mount.
  useEffect(() => {
    invoke<Boot>("config_get")
      .then((boot) => {
        if (boot.config) setFileToVault(boot.config.file_to_vault ?? false);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const shown = listen<{ clipboard: string | null; active_url?: string | null }>("panel-shown", (e) => {
      detectSeq.current++;
      runRef.current = null;
      setPhase({ k: "idle" });
      setPages(0);
      setNote("");
      setSolve(null);
      setDetectSource(null);
      setNeedsSignin(false);

      performance.mark("iofast:panel-shown");
      timing.current = { shown: performance.now(), interactive: 0, sent: false };
      const raw = e.payload.active_url || e.payload.clipboard || "";
      const pasted = parseTarget(raw);
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

    const done = listen<GrabDone>("grab-done", (e) => {
      const run = runRef.current;
      if (!run || e.payload.rid !== run.rid) return;
      runRef.current = null;
      setSolve(null);
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
        setPhase({
          k: "done",
          written: result.written,
          outDir: result.out_dir,
          vaultPath: result.vault_path,
          sourceNote: result.source_note,
        });
      }
    });

    const all = Promise.all([shown, events, done]);
    return () => {
      void all.then((fns) => fns.forEach((f) => f()));
    };
  }, [startGrab]);

  // Tick the human-solve countdown against an absolute deadline.
  useEffect(() => {
    if (!solve) return;
    const tick = () => setLeft(solve.deadline - Date.now());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [solve]);

  // Transparent window: track card height.
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
      void invoke("panel_hide");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = async () => {
    if (!target || !scope || phase.k === "running") return;
    let out: string;
    try {
      out = await invoke<string>("library_dir", { host: target.host, scope });
    } catch (e) {
      setPhase({ k: "failed", message: reason(e) });
      return;
    }
    await startGrab(target.href, out, fileToVault);
  };

  const cancel = () => {
    const run = runRef.current;
    if (!run) return;
    setNote("stopping…");
    void invoke("sidecar_cancel", { rid: run.rid }).catch(() => undefined);
  };

  const toggleFileToVault = () => {
    const next = !fileToVault;
    setFileToVault(next);
    void invoke("config_set", { patch: { file_to_vault: next } }).catch(() => undefined);
  };

  const openInObsidian = () => {
    if (phase.k !== "done" || !phase.vaultPath || !phase.sourceNote) return;
    // Extract vault name from vault_path (last segment).
    const vaultName = phase.vaultPath.split(/[\\/]/).pop() ?? "";
    const uri = `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(phase.sourceNote)}`;
    void invoke("open_path", { path: uri });
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
          aria-label="URL to grab"
          placeholder="Paste a URL"
          onChange={(e) => {
            if (phase.k !== "idle" && phase.k !== "running") setPhase({ k: "idle" });
            setUrl(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}
        />
        {detectSource && !running && (
          <span className="badge" title={detectSource}>{sourceLabel(detectSource)}</span>
        )}
        {target && !running && <kbd className="enter">↵</kbd>}
      </div>

      {needsSignin && !running && (
        <div className="banner warn" role="status">
          <span>This source requires sign-in first</span>
        </div>
      )}

      <label className="toggle">
        <input
          type="checkbox"
          checked={fileToVault}
          onChange={toggleFileToVault}
          disabled={running}
        />
        <span>File to vault</span>
      </label>

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
          ) : detectSource ? (
            <span className="note">{scope}</span>
          ) : (
            <span className="note pulse">detecting…</span>
          )}
        </div>
        <div className="meta-action">
          {running ? (
            <button type="button" className="btn" onClick={cancel}>
              Cancel
            </button>
          ) : phase.k === "done" ? (
            <>
              {phase.vaultPath && phase.sourceNote && (
                <button type="button" className="btn" onClick={openInObsidian}>
                  Open in Obsidian
                </button>
              )}
              <button
                type="button"
                className="btn"
                onClick={() => void invoke("open_path", { path: phase.outDir })}
              >
                Open folder
              </button>
            </>
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

      {solve && (
        <div className="banner solve" role="status">
          <span>
            Solve the challenge in the browser window
            <span className="solve-left" data-low={left <= 30_000 ? "1" : "0"}>
              {countdown(left)}
            </span>
          </span>
        </div>
      )}
    </div>
  );
}