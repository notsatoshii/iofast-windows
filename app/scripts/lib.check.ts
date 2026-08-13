/**
 * Self-check for src/lib.ts — the large-crawl gate decides whether a 4,000-page
 * crawl starts without asking, so the must-FIRE cases come first and the
 * must-NOT-fire cases follow. Outside src/ on purpose, same as scope.check.ts.
 *
 *   node --experimental-strip-types scripts/lib.check.ts && echo ok
 */
import assert from "node:assert/strict";
import { countdown, DEFAULT_THRESHOLD, overThreshold, tokens, when } from "../src/lib.ts";

// Must fire: over the limit, by one page or by thousands.
assert.equal(overThreshold(301, 300), true);
assert.equal(overThreshold(4000, 300), true);
assert.equal(overThreshold(2, 1), true);

// Must NOT fire: at the limit, under it, or with no count at all. A null count
// is the abandoned-estimate case — it must never block a run.
assert.equal(overThreshold(300, 300), false);
assert.equal(overThreshold(299, 300), false);
assert.equal(overThreshold(0, 300), false);
assert.equal(overThreshold(null, 300), false);
assert.equal(overThreshold(null, 1), false);
// A nonsensical threshold disables the gate rather than blocking everything.
assert.equal(overThreshold(5, 0), false);
assert.equal(overThreshold(5, -1), false);

assert.equal(DEFAULT_THRESHOLD, 300);

assert.equal(tokens(0), "0");
assert.equal(tokens(999), "999");
assert.equal(tokens(1000), "1.0k");
assert.equal(tokens(31_240), "31k");
assert.equal(tokens(2_400_000), "2.4M");

const now = Date.parse("2026-08-11T12:00:00Z");
const ago = (mins: number) => when(now - mins * 60_000, now);
assert.equal(ago(0), "just now");
assert.equal(ago(5), "5m ago");
assert.equal(ago(90), "1h ago");
assert.equal(ago(60 * 47), "47h ago");
assert.equal(ago(60 * 49), "2d ago");

// The CAPTCHA banner's countdown. Zero-pads the seconds, rounds up so the last
// visible tick is 0:01, and never runs negative — a banner counting past the
// engine's own deadline would claim a browser window is still waiting when the
// engine has already given up on it.
assert.equal(countdown(180_000), "3:00");
assert.equal(countdown(125_000), "2:05");
assert.equal(countdown(60_000), "1:00");
assert.equal(countdown(59_400), "1:00");
assert.equal(countdown(9_000), "0:09");
assert.equal(countdown(1), "0:01");
assert.equal(countdown(0), "0:00");
assert.equal(countdown(-5_000), "0:00");
