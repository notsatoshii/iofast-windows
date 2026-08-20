/* TEMP: bold-revamp preview scaffold — remove at collapse.
 * Lets a plain browser (no Tauri shell) render the main window with fixture
 * data, so variants can be reviewed headlessly. Loaded only when DEV and no
 * __TAURI_INTERNALS__ (see main.tsx TEMP boot). Never ships: the import sits
 * behind `import.meta.env.DEV` and vite dead-code-eliminates it in build.
 */
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";

mockWindows("main");

const now = Date.now();
const HOUR = 3_600_000;

const config = { library_root: "/Users/you/Docs Library", large_crawl_threshold: 300, interactive: true };

const runs = [
  { dir: "/lib/stripe.com/docs", host: "docs.stripe.com", scope: "/api/", url: "https://docs.stripe.com/api", pages: 412, tokens: 1_284_000, clean: true, quality: "", cancelled: false, modified_ms: now - 2 * HOUR, resumable: false },
  { dir: "/lib/react.dev", host: "react.dev", scope: "/reference/", url: "https://react.dev/reference", pages: 187, tokens: 640_200, clean: true, quality: "", cancelled: false, modified_ms: now - 26 * HOUR, resumable: false },
  { dir: "/lib/tailwindcss.com", host: "tailwindcss.com", scope: "/docs/", url: "https://tailwindcss.com/docs", pages: 96, tokens: 210_500, clean: false, quality: "SUSPECT", cancelled: false, modified_ms: now - 3 * 24 * HOUR, resumable: false },
  { dir: "/lib/docs.rs/tokio", host: "docs.rs", scope: "/tokio/", url: "https://docs.rs/tokio", pages: 58, tokens: 150_000, clean: false, quality: "", cancelled: true, modified_ms: now - 9 * 24 * HOUR, resumable: true },
  { dir: "/lib/kubernetes.io", host: "kubernetes.io", scope: "/docs/", url: "https://kubernetes.io/docs", pages: 1041, tokens: 3_902_000, clean: true, quality: "", cancelled: false, modified_ms: now - 30 * 24 * HOUR, resumable: false },
];

const schedules: Record<string, unknown> = {
  "/lib/stripe.com/docs": { every: "daily", last_ms: now - 2 * HOUR },
  "/lib/kubernetes.io": { every: "weekly", last_ms: now - 6 * 24 * HOUR, proxy: false },
};

// listen() registrations, so scenes can fire events back at the app.
const listeners = new Map<string, number[]>();
const emitMock = (event: string, payload: unknown) => {
  for (const id of listeners.get(event) ?? []) {
    (window as unknown as { __TAURI_INTERNALS__: { runCallback: (id: number, arg: unknown) => void } })
      .__TAURI_INTERNALS__.runCallback(id, { event, id: 0, payload });
  }
};

const params = new URLSearchParams(window.location.search);
const firstRun = params.get("scene") === "firstrun";

mockIPC((cmd, args) => {
  const a = args as Record<string, unknown>;
  switch (cmd) {
    case "plugin:event|listen": {
      const ev = a.event as string;
      listeners.set(ev, [...(listeners.get(ev) ?? []), a.handler as number]);
      // The resolved value is the eventId unlisten sends back; using the
      // handler id makes unlisten exact — StrictMode double-mounts rely on it.
      return a.handler as number;
    }
    case "plugin:event|unlisten": {
      const ev = a.event as string;
      listeners.set(ev, (listeners.get(ev) ?? []).filter((id) => id !== (a.eventId as number)));
      return;
    }
    case "config_get":
      return { config: firstRun ? null : config, suggested_root: "/Users/you/Docs Library" };
    case "config_set":
      return;
    case "library_list":
      return runs;
    case "schedule_get":
      return schedules;
    case "schedule_set":
      return;
    case "detect_agents":
      return [
        { id: "claude", path: "/usr/local/bin/claude", terminal: true },
        { id: "cursor", path: "/usr/local/bin/cursor", terminal: false },
      ];
    case "detect_node":
      return "/usr/local/bin/node";
    case "keychain_status":
      return { OPENROUTER_API_KEY: true, IOFAST_RESCUE_MODEL: false, IOFAST_PROXY: false };
    case "keychain_set":
    case "keychain_clear":
      return;
    case "about_info":
      return {
        app_version: "0.1.0",
        engine_version: "0.1.0",
        engine_build: "9094931aaaa0",
        app_build: "9094931aaaa0",
        notices: "Third-party notices\n===================\n\nreqwest — MIT/Apache-2.0\ntokio — MIT\nplaywright — Apache-2.0\n(fixture text for preview)",
      };
    case "sidecar_estimate":
      return { page_count: 4200 };
    case "sidecar_scrape_start":
      return;
    case "sidecar_cancel":
      emitMock("scrape-done", { rid: 1, result: { written: 214, out_dir: "/lib/x", exit_code: 0, cancelled: true } });
      return;
    case "sidecar_browser_status":
      return { installed: true, drivers: true, chromium: true, path: "/support/browsers" };
    case "sidecar_install_browsers":
      return { installed: true, drivers: true, chromium: true, path: "/support/browsers" };
    case "autostart_get":
      return true;
    case "autostart_set":
      return a.enabled;
    case "mcp_index":
      return {
        library: "docs-stripe-com",
        config_json: '{\n  "mcpServers": {\n    "iofast-docs": {\n      "command": "npx",\n      "args": ["docs-mcp", "--store", "/lib"]\n    }\n  }\n}',
      };
    case "sidecar_push":
      // Stream two deltas before resolving, so the busy/streaming state is
      // actually reviewable in the preview (the real engine streams).
      return new Promise((resolve) => {
        window.setTimeout(() => emitMock("engine-event", { type: "push", delta: "Use a secret key in the Authorization header:\n\n" }), 250);
        window.setTimeout(() => emitMock("engine-event", { type: "push", delta: "  curl https://api.stripe.com/v1/charges \\\n    -u sk_test_xxx:\n" }), 600);
        window.setTimeout(() => resolve({ digest: "Use a secret key in the Authorization header:\n\n  curl https://api.stripe.com/v1/charges \\\n    -u sk_test_xxx:\n\nEvery request must be TLS. (fixture answer)", model: "anthropic/claude-sonnet-5", files: 412, chars: 92_000, truncated: true }), 1200);
      });
    case "open_in_agent":
    case "open_path":
    case "library_delete":
      return;
    case "library_dir":
      return "/lib/new-run";
    case "pick_folder":
      return "/Users/you/Picked Folder";
    default:
      return;
  }
});

// Scenes: state the mock can only reach by firing events after mount.
if (params.get("scene") === "busy") {
  window.setTimeout(() => {
    emitMock("scrape-started", { rid: 1, url: "https://docs.stripe.com/api", out: "/lib/x", update: false });
    emitMock("engine-event", { type: "progress", rid: 1, n: 214, message: "⚡ fetched /api/charges/object" });
    emitMock("engine-event", { type: "human_required", rid: 1, url: "https://docs.stripe.com/api/auth", timeout: 165 });
    emitMock("engine-stale", { app: "9094931aaaa0", engine: "dev" });
  }, 400);
}
