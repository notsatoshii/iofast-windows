import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import { reason, type BrowserStatus, type Config } from "../lib";

/** docs/RPC.md `scrape.env` allowlist — the secrets the app holds. */
const RESCUE_KEYS = [
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

const NETWORK_KEYS = [
  {
    name: "IOFAST_PROXY",
    label: "Proxy",
    hint: "Routes every fetch tier for sites that block by IP. Format: http://user:pass@host:port. Enter several, comma-separated, to rotate across requests.",
    secret: true,
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
  const [browsers, setBrowsers] = useState<BrowserStatus | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installMsg, setInstallMsg] = useState("");
  const [atLogin, setAtLogin] = useState<boolean | null>(null);
  const [atLoginError, setAtLoginError] = useState("");

  const readKeys = () =>
    invoke<Record<string, boolean>>("keychain_status").then(setStored).catch(() => undefined);

  useEffect(() => {
    void readKeys();
    void invoke<BrowserStatus>("sidecar_browser_status").then(setBrowsers).catch(() => undefined);
    void invoke<boolean>("autostart_get").then(setAtLogin).catch(() => setAtLogin(false));
    // Install progress rides the shared engine-event stream (docs/RPC.md).
    const events = listen<{ type: string; message?: string }>("engine-event", (e) => {
      if (e.payload.type === "browser_install" && e.payload.message) {
        setInstallMsg(e.payload.message);
      }
    });
    return () => {
      void events.then((un) => un());
    };
  }, []);

  const installBrowsers = async () => {
    setConfirming(false);
    setInstalling(true);
    setError("");
    try {
      setBrowsers(await invoke<BrowserStatus>("sidecar_install_browsers"));
    } catch (e) {
      setError(reason(e));
    } finally {
      setInstalling(false);
      setInstallMsg("");
    }
  };

  // A refusal (dev build, app still on the disk image) is shown in the row, not
  // in the page-foot error line — that sits below the whole Browser-tiers block,
  // where a user who just clicked the toggle would never see it.
  const setAutostart = async (next: boolean) => {
    setAtLoginError("");
    try {
      setAtLogin(await invoke<boolean>("autostart_set", { enabled: next }));
    } catch (e) {
      setAtLoginError(reason(e));
    }
  };

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

  const keyRow = (key: { name: string; label: string; hint: string; secret: boolean }) => (
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
  );

  return (
    <div className="pane" role="tabpanel" id="panel-settings" aria-labelledby="tab-settings" tabIndex={0}>
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
          <span aria-hidden="true">
            <kbd className="key">⌘</kbd>
            <kbd className="key">⇧</kbd>
            <kbd className="key">K</kbd>
          </span>
          <span className="visually-hidden">Command Shift K</span>
        </div>
      </div>

      <h2>Startup</h2>
      <div className="set">
        <div className="set-label">
          Start at login
          <span className="set-hint">
            Keeps iofast in the menubar from login on, so a run set to Auto re-scrapes without
            you opening the app first. Takes effect at your next login.
          </span>
        </div>
        <div className="set-control">
          {atLogin === null ? (
            <span className="set-hint">…</span>
          ) : (
            <>
              {atLoginError && (
                <span className="set-hint bad" role="alert">
                  {atLoginError}
                </span>
              )}
              {atLogin && <span className="saved">On</span>}
              <button type="button" className="btn" onClick={() => void setAutostart(!atLogin)}>
                {atLogin ? "Turn off" : "Turn on"}
              </button>
            </>
          )}
        </div>
      </div>

      <h2>Browser tiers</h2>
      <div className="set">
        <div className="set-label">
          Browser components
          <span className="set-hint">
            Enable the dynamic and stealth tiers for JavaScript-heavy and bot-guarded sites.
            Until installed, every scrape runs static-only.
          </span>
        </div>
        <div className="set-control">
          {browsers === null ? (
            <span className="set-hint">…</span>
          ) : installing ? (
            <span className="set-hint" aria-live="polite">
              {installMsg || "installing…"}
            </span>
          ) : browsers.installed ? (
            <span className="saved">Installed</span>
          ) : confirming ? (
            <>
              <span className="set-hint">
                Downloads ~770&nbsp;MB (pinned drivers + Chromium) into app support.
              </span>
              <button type="button" className="btn" onClick={() => void installBrowsers()}>
                Download
              </button>
              <button type="button" className="btn" onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <span className="set-hint">
                {browsers.drivers ? "Drivers only — Chromium missing" : "Not installed"}
              </span>
              <button type="button" className="btn" onClick={() => setConfirming(true)}>
                Install…
              </button>
            </>
          )}
        </div>
      </div>

      <h2>Challenges</h2>
      <div className="set">
        <div className="set-label">
          Solve challenges by hand
          <span className="set-hint">
            When a manual scrape hits a Cloudflare challenge the stealth tier can't clear, open a
            browser window so you can solve it, then continue in the background. Needs the browser
            tiers above. Scheduled re-scrapes never open a window.
          </span>
        </div>
        <div className="set-control">
          {config.interactive && <span className="saved">On</span>}
          <button
            type="button"
            className="btn"
            onClick={() => void write({ ...config, interactive: !config.interactive })}
          >
            {config.interactive ? "Turn off" : "Turn on"}
          </button>
        </div>
      </div>

      <h2>Rescue</h2>
      {RESCUE_KEYS.map(keyRow)}

      <h2>Network</h2>
      {NETWORK_KEYS.map(keyRow)}
      <p className="foot">
        These live in the macOS keychain, never in the config file, and reach the engine only
        inside the request for the run that needs them. Saved values are never read back into
        this window.
      </p>
      {error && (
        <p className="foot bad" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
