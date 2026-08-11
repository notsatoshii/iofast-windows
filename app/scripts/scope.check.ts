/**
 * Self-check for the scope_prefix port (src/panel/scope.ts), which decides what
 * a scrape crawls. Outside src/ on purpose: it is not part of the bundle and
 * needs no @types/node in the app's tsconfig.
 *
 *   node --experimental-strip-types scripts/scope.check.ts && echo ok
 *
 * Cases mirror engine/src/iofast_engine/urls.py scope_prefix, plus the parse
 * boundary Enter depends on.
 */
import assert from "node:assert/strict";
import { parseTarget, scopePrefix } from "../src/panel/scope.ts";

const scope = (u: string) => scopePrefix(new URL(u));

assert.equal(scope("https://x.com/docs/intro"), "/docs/");
assert.equal(scope("https://x.com/docs/"), "/docs/");
assert.equal(scope("https://x.com/docs"), "/");
assert.equal(scope("https://x.com/"), "/");
assert.equal(scope("https://x.com"), "/");
assert.equal(scope("https://x.com/a/b/c"), "/a/b/");
assert.equal(scope("https://x.com/docs/intro?q=1#f"), "/docs/");

assert.equal(parseTarget("  https://x.com/docs  ")?.href, "https://x.com/docs");
assert.equal(parseTarget("http://x.com")?.protocol, "http:");
assert.equal(parseTarget("x.com/docs"), null);
assert.equal(parseTarget("file:///etc/passwd"), null);
assert.equal(parseTarget(""), null);
