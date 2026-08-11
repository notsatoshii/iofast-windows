import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import { parseTarget, scopePrefix } from "../panel/scope";
import FirstRun from "./FirstRun";
import Settings from "./Settings";
import {
  overThreshold,
  plural,
  reason,
  tokens,
  when,
  DEFAULT_THRESHOLD,
  type Boot,
  type Run,
} from "../lib";

/** docs/RPC.md: `params` of an `event` notification IS the engine event dict. */
type EngineEvent = { type: string; rid?: number; n?: number; message?: string };
type ScrapeResult = { written: number; out_dir: string; exit_code: number; cancelled: boolean };
type ScrapeDone = { rid: number; result?: ScrapeResult; error?: string };
type Started = { rid: number; url: string; out: string; update: boolean };
type EstimateResult = { page_count: number | null };
/** A coding-agent CLI detected on this machine (store.rs `detect_agents`). */
type Agent = { id: string; path: string; terminal: boolean };

/** The run whose events the progress strip is showing — panel-started too. */
type Active = { rid: number; label: string; update: boolean };
/** A pending large-crawl confirmation. `go` is what Enter would have done. */
type Gate = { pages: number; go: () => void };
type Estimate =
  | { k: "none" }
  | { k: "estimating" }
  | { k: "landed"; pages: number | null }
  | { k: "gone" };

const DEBOUNCE_MS = 250;
/** Client-side abandon. docs/RPC.md: the server's own budget is 800ms. */
const ABANDON_MS = 1000;

/** Engine messages lead with an emoji and sometimes a newline. Strip to words. */
const tidy = (message: string) =>
  message.replace(/\s+/g, " ").replace(/^[^\p{L}\p{N}]+/u, "").trim().slice(0, 120);

const labelOf = (href: string) => {
  const target = parseTarget(href);
  return target ? `${target.host}${scopePrefix(target)}` : href;
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function App() {
  const [boot, setBoot] = useState<Boot | null>(null);
  const [tab, setTab] = useState<"library" | "settings">("library");
  const [runs, setRuns] = useState<Run[]>([]);
  const [url, setUrl] = useState("");
  const [estimate, setEstimate] = useState<Estimate>({ k: "none" });
  const [gate, setGate] = useState<Gate | null>(null);
  const [active, setActive] = useState<Active | null>(null);
  const [pages, setPages] = useState(0);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [doomed, setDoomed] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);

  const activeRef = useRef<Active | null>(null);
  const estSeq = useRef(0);

  const target = parseTarget(url);
  const scope = target ? scopePrefix(target) : null;
  const config = boot?.config ?? null;

  const refresh = useCallback(() => {
    invoke<Run[]>("library_list")
      .then(setRuns)
      .catch(() => undefined);
  }, []);

  const reboot = useCallback(() => {
    invoke<Boot>("config_get")
      .then((next) => {
        setBoot(next);
        if (next.config) refresh();
      })
      .catch((e: unknown) => setError(reason(e)));
  }, [refresh]);

  useEffect(reboot, [reboot]);

  // Which coding-agent CLIs are installed — read once; the row's "Open in…"
  // action offers only the ones actually present.
  useEffect(() => {
    invoke<Agent[]>("detect_agents").then(setAgents).catch(() => undefined);
  }, []);

  // No index file to keep in sync: the library IS the folders, so re-read them
  // whenever the window comes forward.
  useEffect(() => {
    const onFocus = () => {
      if (boot?.config) refresh();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [boot, refresh]);

  useEffect(() => {
    const started = listen<Started>("scrape-started", (e) => {
      const next = {
        rid: e.payload.rid,
        label: labelOf(e.payload.url),
        update: e.payload.update,
      };
      activeRef.current = next;
      setActive(next);
      setPages(0);
      setNote("");
    });

    const events = listen<EngineEvent>("engine-event", (e) => {
      if (e.payload.rid !== activeRef.current?.rid) return;
      if (typeof e.payload.n === "number") setPages(e.payload.n);
      if (typeof e.payload.message === "string") setNote(tidy(e.payload.message));
    });

    const done = listen<ScrapeDone>("scrape-done", (e) => {
      if (e.payload.rid !== activeRef.current?.rid) return;
      activeRef.current = null;
      setActive(null);
      const { result, error: failure } = e.payload;
      if (failure || !result) setError(failure ?? "the sidecar returned nothing");
      else if (result.cancelled) setNote(`Stopped — ${plural(result.written, "page")} kept`);
      else if (result.exit_code !== 0)
        setError(`engine exit ${result.exit_code} after ${plural(result.written, "page")}`);
      refresh();
    });

    const all = Promise.all([started, events, done]);
    return () => {
      void all.then((fns) => fns.forEach((f) => f()));
    };
  }, [refresh]);

  // Same pre-Enter pass as the quick panel, and just as non-blocking: it only
  // ever writes the annotation line and the gate's page count.
  useEffect(() => {
    if (!target) {
      estSeq.current++;
      setEstimate({ k: "none" });
      return;
    }
    const href = target.href;
    const timer = setTimeout(async () => {
      const seq = ++estSeq.current;
      setEstimate({ k: "estimating" });
      const landed = await Promise.race([
        invoke<EstimateResult>("sidecar_estimate", { url: href }).catch(() => null),
        new Promise<null>((stop) => setTimeout(() => stop(null), ABANDON_MS)),
      ]);
      if (seq === estSeq.current) {
        setEstimate(landed ? { k: "landed", pages: landed.page_count } : { k: "gone" });
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // target is derived from url; deriving it again in here would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  const start = async (href: string, at: string, out: string, update: boolean) => {
    setGate(null);
    setError("");
    setNote("");
    try {
      // The rid comes back on the `scrape-started` event, which the panel's
      // runs raise too — one listener covers both origins.
      await invoke("sidecar_scrape_start", {
        url: href,
        out,
        scope: at,
        depth: null,
        maxPages: null,
        update,
      });
    } catch (e) {
      setError(reason(e));
    }
  };

  const guard = (count: number | null, go: () => void) => {
    const limit = config?.large_crawl_threshold ?? DEFAULT_THRESHOLD;
    if (count !== null && overThreshold(count, limit)) setGate({ pages: count, go });
    else go();
  };

  const submit = () => {
    if (!target || !scope || active) return;
    guard(estimate.k === "landed" ? estimate.pages : null, () => {
      void (async () => {
        try {
          const out = await invoke<string>("library_dir", { host: target.host, scope });
          await start(target.href, scope, out, false);
          setUrl("");
        } catch (e) {
          setError(reason(e));
        }
      })();
    });
  };

  /** Re-scrape: conditional GETs against this run's own manifest, same folder. */
  const rescrape = (run: Run) =>
    guard(run.pages, () => void start(run.url, run.scope, run.dir, true));

  const remove = async (run: Run) => {
    setDoomed(null);
    try {
      await invoke("library_delete", { path: run.dir });
    } catch (e) {
      setError(reason(e));
    }
    refresh();
  };

  const openIn = async (agentId: string, dir: string) => {
    setOpening(null);
    setError("");
    try {
      await invoke("open_in_agent", { agent: agentId, dir });
    } catch (e) {
      setError(reason(e));
    }
  };

  if (!boot) return <div className="app" />;
  if (!config) {
    return (
      <div className="app">
        <FirstRun suggested={boot.suggested_root} onChosen={reboot} />
      </div>
    );
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <svg className="mark" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="1.75" y="1.75" width="12.5" height="12.5" rx="3.25" />
            <path d="M8 4.75v5.5m0 0L5.9 8.2M8 10.25l2.1-2.05" />
          </svg>
          iofast
        </div>
        <nav className="tabs">
          {(["library", "settings"] as const).map((name) => (
            <button
              key={name}
              type="button"
              className="tab"
              aria-current={tab === name}
              onClick={() => setTab(name)}
            >
              {name === "library" ? "Library" : "Settings"}
            </button>
          ))}
        </nav>
      </header>

      {active && (
        <div className="strip">
          <span className="strip-what">
            {active.update ? "Updating" : "Scraping"} {active.label}
          </span>
          <span className="strip-count">{plural(pages, "page")}</span>
          <span className="strip-note">{note}</span>
          <button
            type="button"
            className="btn"
            onClick={() => void invoke("sidecar_cancel", { rid: active.rid }).catch(() => undefined)}
          >
            Stop
          </button>
        </div>
      )}

      {tab === "settings" ? (
        <Settings config={config} onChanged={reboot} />
      ) : (
        <div className="pane">
          <div className="add">
            <input
              className="add-url"
              type="text"
              value={url}
              spellCheck={false}
              autoComplete="off"
              autoCorrect="off"
              aria-label="Documentation URL"
              placeholder="Paste a docs URL to add it to the library"
              onChange={(e) => {
                setGate(null);
                setUrl(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <span className="add-hint" aria-live="polite">
              {!target ? (
                url.trim() === "" ? (
                  ""
                ) : (
                  "not a URL yet"
                )
              ) : estimate.k === "estimating" ? (
                <span className="pulse">sizing {scope}</span>
              ) : estimate.k === "landed" && estimate.pages !== null ? (
                <>
                  <span className="approx">≈</span> {plural(estimate.pages, "page")} · {scope}
                </>
              ) : (
                scope
              )}
            </span>
            <button type="button" className="btn" disabled={!target || !!active} onClick={submit}>
              Scrape
            </button>
          </div>

          {gate && (
            <div className="gate">
              <span>This site maps to ≈{plural(gate.pages, "page")} — scrape anyway?</span>
              <div className="acts">
                <button type="button" className="btn go" onClick={gate.go}>
                  Scrape {gate.pages}
                </button>
                <button type="button" className="btn" onClick={() => setGate(null)}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {error && <div className="alert">{error}</div>}

          {runs.length === 0 ? (
            <p className="empty">
              Nothing here yet. Paste a URL above, or press <kbd className="key">⌘</kbd>
              <kbd className="key">⇧</kbd>
              <kbd className="key">K</kbd> anywhere.
            </p>
          ) : (
            <ul className="runs">
              {runs.map((run) => (
                <li className="run" key={run.dir}>
                  <span
                    className="dot"
                    data-clean={run.clean ? "1" : "0"}
                    title={run.clean ? "clean run" : "run has gaps — see report.md"}
                  />
                  <span className="run-host">{run.host}</span>
                  <span className="run-scope">{run.scope}</span>
                  <span className="run-when">{when(run.modified_ms)}</span>
                  <span className="run-facts">
                    {plural(run.pages, "page")} · {tokens(run.tokens)} tokens
                    {run.cancelled ? " · stopped early" : ""}
                  </span>
                  {doomed === run.dir ? (
                    <span className="run-acts asking">
                      <span className="ask">Move to Trash?</span>
                      <button type="button" className="link bad" onClick={() => void remove(run)}>
                        Delete
                      </button>
                      <button type="button" className="link" onClick={() => setDoomed(null)}>
                        Keep
                      </button>
                    </span>
                  ) : opening === run.dir ? (
                    <span className="run-acts asking">
                      <span className="ask">Open in:</span>
                      {agents.map((agent) => (
                        <button
                          key={agent.id}
                          type="button"
                          className="link"
                          onClick={() => void openIn(agent.id, run.dir)}
                        >
                          {cap(agent.id)}
                        </button>
                      ))}
                      <button type="button" className="link" onClick={() => setOpening(null)}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <span className="run-acts">
                      <button
                        type="button"
                        className="link"
                        onClick={() => void invoke("open_path", { path: run.dir })}
                      >
                        Open folder
                      </button>
                      {agents.length > 0 && (
                        <button
                          type="button"
                          className="link"
                          onClick={() => setOpening(run.dir)}
                        >
                          Open in…
                        </button>
                      )}
                      <button
                        type="button"
                        className="link"
                        disabled={!!active}
                        onClick={() => rescrape(run)}
                      >
                        Re-scrape
                      </button>
                      <button
                        type="button"
                        className="link"
                        onClick={() => setDoomed(run.dir)}
                      >
                        Delete
                      </button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
