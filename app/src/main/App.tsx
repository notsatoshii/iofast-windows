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
type EngineEvent = { type: string; rid?: number; n?: number; message?: string; delta?: string };
type ScrapeResult = { written: number; out_dir: string; exit_code: number; cancelled: boolean };
type ScrapeDone = { rid: number; result?: ScrapeResult; error?: string };
type Started = { rid: number; url: string; out: string; update: boolean };
type EstimateResult = { page_count: number | null };
/** A coding-agent CLI detected on this machine (store.rs `detect_agents`). */
type Agent = { id: string; path: string; terminal: boolean };
/** Result of indexing a run into docs-mcp (store.rs `mcp_index`). */
type McpIndexed = { library: string; config_json: string };
/** Result of an API push over a run's corpus (engine `push`). */
type PushResult = { digest: string; model: string; files: number; chars: number; truncated: boolean };

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
  const [node, setNode] = useState<string | null>(null);
  const [mcpBusy, setMcpBusy] = useState<string | null>(null);
  const [mcpConfig, setMcpConfig] = useState<McpIndexed | null>(null);
  const [copied, setCopied] = useState(false);
  const [hasKey, setHasKey] = useState(false);
  const [ask, setAsk] = useState<{ dir: string; host: string } | null>(null);
  const [askPrompt, setAskPrompt] = useState("");
  const [askModel, setAskModel] = useState("");
  const [pushBusy, setPushBusy] = useState(false);
  const [digest, setDigest] = useState<(PushResult & { host: string }) | null>(null);
  const [stream, setStream] = useState("");

  const activeRef = useRef<Active | null>(null);
  const estSeq = useRef(0);
  // Only one push runs at a time (engine BUSY guard), so any `push` delta event
  // belongs to the in-flight one — no rid correlation needed. The ref keeps the
  // event listener from appending after the call has landed.
  const streamingRef = useRef(false);

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

  // Whether Node is present, for the row's "MCP search" action. Absent = hide it
  // (the FastMCP no-Node fallback is not built yet).
  useEffect(() => {
    invoke<string | null>("detect_node").then(setNode).catch(() => undefined);
  }, []);

  // Whether an OpenRouter key is stored, for the row's "Ask AI" action. Re-read
  // on focus too — the user may add or clear it in Settings between visits.
  const recheckKey = useCallback(() => {
    invoke<Record<string, boolean>>("keychain_status")
      .then((s) => setHasKey(!!s.OPENROUTER_API_KEY))
      .catch(() => undefined);
  }, []);
  useEffect(recheckKey, [recheckKey]);

  // No index file to keep in sync: the library IS the folders, so re-read them
  // whenever the window comes forward.
  useEffect(() => {
    const onFocus = () => {
      if (boot?.config) refresh();
      recheckKey();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [boot, refresh, recheckKey]);

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

  // API-push answer streams in as `push` delta events; append them live while a
  // push is in flight. The final digest arrives on the awaited sidecar_push call.
  useEffect(() => {
    const chunks = listen<EngineEvent>("engine-event", (e) => {
      if (streamingRef.current && e.payload.type === "push" && typeof e.payload.delta === "string") {
        setStream((s) => s + e.payload.delta);
      }
    });
    return () => {
      void chunks.then((f) => f());
    };
  }, []);

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

  // Index a run into the shared docs-mcp store, then reveal the paste config.
  const indexMcp = async (dir: string) => {
    setError("");
    setMcpConfig(null);
    setMcpBusy(dir);
    try {
      setMcpConfig(await invoke<McpIndexed>("mcp_index", { dir }));
    } catch (e) {
      setError(reason(e));
    } finally {
      setMcpBusy(null);
    }
  };

  // Ask the model the typed prompt over this run's corpus, then show the answer.
  const runPush = async () => {
    if (!ask || !askPrompt.trim()) return;
    setError("");
    setStream("");
    streamingRef.current = true;
    setPushBusy(true);
    try {
      const r = await invoke<PushResult>("sidecar_push", {
        dir: ask.dir,
        prompt: askPrompt,
        model: askModel.trim() || null,
      });
      setDigest({ ...r, host: ask.host });
      setAsk(null);
      setAskPrompt("");
    } catch (e) {
      setError(reason(e));
    } finally {
      streamingRef.current = false;
      setPushBusy(false);
      setStream("");
    }
  };

  const copyMcp = (text: string) => {
    // No clipboard-write command in the shell (see main.rs clipboard_text); the
    // webview API works from this click, and the config stays visible to select
    // by hand if a wkwebview build ever blocks it.
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => undefined);
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

          {mcpConfig && (
            <div className="gate mcp">
              <span>
                Indexed <strong>{mcpConfig.library}</strong> for MCP search. Add this to your
                MCP client (Claude Desktop, Cursor), then restart it. The server runs
                read-only — search only, over untrusted docs.
              </span>
              <pre className="mcp-config">{mcpConfig.config_json}</pre>
              <div className="acts">
                <button type="button" className="btn go" onClick={() => copyMcp(mcpConfig.config_json)}>
                  {copied ? "Copied" : "Copy"}
                </button>
                <button type="button" className="btn" onClick={() => setMcpConfig(null)}>
                  Close
                </button>
              </div>
            </div>
          )}

          {ask && (
            <div className="gate mcp ask">
              <span>
                Ask AI about <strong>{ask.host}</strong>. The scraped pages go to the model as
                untrusted reference — it is told to treat them as data, never as instructions.
              </span>
              <textarea
                className="ask-prompt"
                rows={3}
                value={askPrompt}
                spellCheck={false}
                disabled={pushBusy}
                autoFocus
                placeholder="e.g. How do I authenticate a request?"
                onChange={(e) => setAskPrompt(e.target.value)}
              />
              <input
                className="text-input ask-model"
                type="text"
                value={askModel}
                spellCheck={false}
                autoComplete="off"
                disabled={pushBusy}
                placeholder="model (optional — defaults to your rescue model)"
                onChange={(e) => setAskModel(e.target.value)}
              />
              {pushBusy && (
                <pre className="mcp-config digest" aria-live="polite">
                  {stream || "…"}
                </pre>
              )}
              <div className="acts">
                <button
                  type="button"
                  className="btn go"
                  disabled={pushBusy || !askPrompt.trim()}
                  onClick={() => void runPush()}
                >
                  {pushBusy ? "Asking…" : "Ask"}
                </button>
                <button type="button" className="btn" disabled={pushBusy} onClick={() => setAsk(null)}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {digest && (
            <div className="gate mcp">
              <span>
                Answer about <strong>{digest.host}</strong> — {digest.model}, over{" "}
                {plural(digest.files, "file")}
                {digest.truncated ? " (corpus truncated to fit)" : ""}.
              </span>
              <pre className="mcp-config digest">{digest.digest}</pre>
              <div className="acts">
                <button type="button" className="btn go" onClick={() => copyMcp(digest.digest)}>
                  {copied ? "Copied" : "Copy"}
                </button>
                <button type="button" className="btn" onClick={() => setDigest(null)}>
                  Close
                </button>
              </div>
            </div>
          )}

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
                      {node && (
                        <button
                          type="button"
                          className="link"
                          disabled={mcpBusy === run.dir}
                          onClick={() => void indexMcp(run.dir)}
                        >
                          {mcpBusy === run.dir ? "Indexing…" : "MCP search"}
                        </button>
                      )}
                      {hasKey && (
                        <button
                          type="button"
                          className="link"
                          onClick={() => {
                            setDigest(null);
                            setError("");
                            setAskPrompt("");
                            setAsk({ dir: run.dir, host: run.host });
                          }}
                        >
                          Ask AI
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
