import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { DEFAULT_THRESHOLD, reason, type Config } from "../lib";

/**
 * First run. Nothing else in the app works without a library folder, so this
 * replaces the whole window rather than sitting behind a settings pane.
 */
export default function FirstRun({
  suggested,
  onChosen,
}: {
  suggested: string;
  onChosen: () => void;
}) {
  const [root, setRoot] = useState(suggested);
  const [error, setError] = useState("");

  const pick = async () => {
    try {
      const picked = await invoke<string | null>("pick_folder", { start: root });
      if (picked) setRoot(picked);
    } catch (e) {
      setError(reason(e));
    }
  };

  const save = async () => {
    const config: Config = { library_root: root, large_crawl_threshold: DEFAULT_THRESHOLD };
    try {
      await invoke("config_set", { config });
      onChosen();
    } catch (e) {
      setError(reason(e));
    }
  };

  return (
    <div className="first">
      <div className="first-card">
        <svg className="first-mark" viewBox="0 0 16 16" aria-hidden="true">
          <rect x="1.75" y="1.75" width="12.5" height="12.5" rx="3.25" />
          <path d="M8 4.75v5.5m0 0L5.9 8.2M8 10.25l2.1-2.05" />
        </svg>
        <h1>Where should the library live?</h1>
        <p>
          One folder per scrape, plain markdown inside. Spotlight and Alfred index it, and any
          editor can open it — iofast keeps no database of its own.
        </p>
        <div className="first-path" title={root}>
          {root}
        </div>
        <div className="first-acts">
          <button type="button" className="btn go" onClick={() => void save()}>
            Use this folder
          </button>
          <button type="button" className="btn" onClick={() => void pick()}>
            Choose…
          </button>
        </div>
        {error && <p className="first-error">{error}</p>}
      </div>
    </div>
  );
}
