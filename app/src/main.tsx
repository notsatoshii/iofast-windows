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
const isPanel = getCurrentWindow().label === "panel";
document.body.classList.add(isPanel ? "panel-window" : "app-window");

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>{isPanel ? <Panel /> : <App />}</React.StrictMode>,
);
