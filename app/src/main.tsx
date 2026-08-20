/// <reference types="vite/client" />
// TEMP: the reference above exists for import.meta.env below — remove at collapse.
import React from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";

import App from "./main/App";
import Panel from "./panel/Panel";
import "./theme.css";
import "./panel/panel.css";
import "./main/app.css";

// Both windows load index.html; the label picks the surface. The panel is the
// fast path, `main` is the library, run progress and settings.
function mount() {
  const isPanel = getCurrentWindow().label === "panel";
  document.body.classList.add(isPanel ? "panel-window" : "app-window");

  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>{isPanel ? <Panel /> : <App />}</React.StrictMode>,
  );
}

// TEMP: bold-revamp preview scaffold — remove at collapse, restoring the
// synchronous mount above as the whole module body. The Tauri shell takes the
// else branch, which is byte-for-byte the old startup path; only a plain DEV
// browser pays the async hop, and the variant sheets load only there too.
if (import.meta.env.DEV && !("__TAURI_INTERNALS__" in window)) {
  void (async () => {
    await import("./main/devmock");
    // TEMP: variant sheets — the winner folds into app.css at collapse.
    await Promise.all([
      import("./main/variants/a.css"),
      import("./main/variants/b.css"),
      import("./main/variants/c.css"),
    ]);
    // TEMP: variant picker — ?variant=a|b|c scopes the candidate token sets.
    const params = new URLSearchParams(window.location.search);
    const variant = params.get("variant");
    if (variant) document.documentElement.dataset.variant = variant;
    const bar = document.createElement("div");
    bar.style.cssText =
      "position:fixed;bottom:10px;right:10px;z-index:99;display:flex;gap:6px;font:11px -apple-system,sans-serif;opacity:0.85";
    for (const v of ["base", "a", "b", "c"]) {
      const link = document.createElement("a");
      link.textContent = v.toUpperCase();
      const next = new URLSearchParams(window.location.search);
      if (v === "base") next.delete("variant");
      else next.set("variant", v);
      link.href = `?${next.toString()}`;
      const on = (variant ?? "base") === v;
      link.style.cssText = `padding:3px 8px;border-radius:6px;text-decoration:none;color:${on ? "#000" : "#999"};background:${on ? "#f0a55a" : "rgba(128,128,128,0.25)"}`;
      bar.appendChild(link);
    }
    document.body.appendChild(bar);
    mount();
  })();
} else {
  mount();
}
