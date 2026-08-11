import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { reason, type Config } from "../lib";

/** docs/RPC.md `scrape.env` allowlist — the only two secrets the app holds. */
const KEYS = [
  {
    name: "OPENROUTER_API_KEY",
    label: "OpenRouter API key",
    hint: "Arms the tier-3 rescue for sites no adapter handles.",
    secret: true,
  },
  {
    name: "IOFAST_RESCUE_MODEL",
    label: "Rescue model",
    hint: "An OpenRouter model id, for example anthropic/claude-sonnet-4.5.",
    secret: false,
  },
] as const;

export default function Settings({
  config,
  onChanged,
}: {
  config: Config;
  onChanged: () => void;
}) {
  const [limit, setLimit] = useState(String(config.large_crawl_threshold));
  const [stored, setStored] = useState<Record<string, boolean>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  const readKeys = () =>
    invoke<Record<string, boolean>>("keychain_status").then(setStored).catch(() => undefined);

  useEffect(() => {
    void readKeys();
  }, []);

  const write = async (next: Config) => {
    try {
      await invoke("config_set", { config: next });
      onChanged();
    } catch (e) {
      setError(reason(e));
    }
  };

  const changeRoot = async () => {
    try {
      const picked = await invoke<string | null>("pick_folder", { start: config.library_root });
      // Existing runs stay where they are; only new ones land in the new root.
      if (picked) await write({ ...config, library_root: picked });
    } catch (e) {
      setError(reason(e));
    }
  };

  const commitLimit = () => {
    const n = Math.round(Number(limit));
    if (!Number.isFinite(n) || n < 1) {
      setLimit(String(config.large_crawl_threshold));
      return;
    }
    setLimit(String(n));
    if (n !== config.large_crawl_threshold) void write({ ...config, large_crawl_threshold: n });
  };

  const saveKey = async (name: string) => {
    try {
      await invoke("keychain_set", { name, value: drafts[name] ?? "" });
      setDrafts((d) => ({ ...d, [name]: "" }));
      await readKeys();
    } catch (e) {
      setError(reason(e));
    }
  };

  const clearKey = async (name: string) => {
    try {
      await invoke("keychain_clear", { name });
      await readKeys();
    } catch (e) {
      setError(reason(e));
    }
  };

  return (
    <div className="pane">
      <h2>Library</h2>
      <div className="set">
        <div className="set-label">
          Library folder
          <span className="set-hint">One folder per scrape, plain markdown inside.</span>
        </div>
        <div className="set-control">
          <span className="path" title={config.library_root}>
            {config.library_root}
          </span>
          <button type="button" className="btn" onClick={() => void changeRoot()}>
            Change…
          </button>
        </div>
      </div>
      <div className="set">
        <div className="set-label">
          Confirm crawls above
          <span className="set-hint">Big sites ask first, in the panel and here.</span>
        </div>
        <div className="set-control">
          <input
            className="num-input"
            type="number"
            min={1}
            value={limit}
            aria-label="Large-crawl confirmation threshold, in pages"
            onChange={(e) => setLimit(e.target.value)}
            onBlur={commitLimit}
            onKeyDown={(e) => e.key === "Enter" && commitLimit()}
          />
          <span className="set-unit">pages</span>
        </div>
      </div>

      <h2>Quick panel</h2>
      <div className="set">
        <div className="set-label">
          Hotkey
          <span className="set-hint">Not editable yet.</span>
        </div>
        <div className="set-control">
          <kbd className="key">⌘</kbd>
          <kbd className="key">⇧</kbd>
          <kbd className="key">K</kbd>
        </div>
      </div>

      <h2>Rescue</h2>
      {KEYS.map((key) => (
        <div className="set" key={key.name}>
          <div className="set-label">
            {key.label}
            <span className="set-hint">{key.hint}</span>
          </div>
          <div className="set-control">
            {stored[key.name] ? (
              <>
                <span className="saved">Saved</span>
                <button type="button" className="btn" onClick={() => void clearKey(key.name)}>
                  Clear
                </button>
              </>
            ) : (
              <>
                <input
                  className="text-input"
                  type={key.secret ? "password" : "text"}
                  value={drafts[key.name] ?? ""}
                  spellCheck={false}
                  autoComplete="off"
                  aria-label={key.label}
                  onChange={(e) => setDrafts((d) => ({ ...d, [key.name]: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && void saveKey(key.name)}
                />
                <button
                  type="button"
                  className="btn"
                  disabled={!drafts[key.name]}
                  onClick={() => void saveKey(key.name)}
                >
                  Save
                </button>
              </>
            )}
          </div>
        </div>
      ))}
      <p className="foot">
        Both live in the macOS keychain, never in the config file, and reach the engine only
        inside the request for the run that needs them. Saved values are never read back into
        this window.
      </p>
      {error && <p className="foot bad">{error}</p>}
    </div>
  );
}
