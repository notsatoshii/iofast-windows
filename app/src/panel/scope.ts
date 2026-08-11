/** URL shaping the panel does locally, so Enter never waits on the sidecar. */

/** An absolute http(s) URL, or null. Anything else is not submittable. */
export function parseTarget(text: string): URL | null {
  let url: URL;
  try {
    url = new URL(text.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return url.hostname ? url : null;
}

/**
 * Port of `scope_prefix` in engine/src/iofast_engine/urls.py — the default
 * crawl scope. /docs/intro -> /docs/ , /docs/ -> /docs/ , / -> /.
 * Must stay byte-identical in behaviour: the engine rejects a scope that is
 * not a path prefix of the URL (-32602).
 */
export function scopePrefix(url: URL): string {
  const path = url.pathname;
  if (!path || path === "/") return "/";
  if (path.endsWith("/")) return path;
  return path.slice(0, path.lastIndexOf("/")) + "/";
}
