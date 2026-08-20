/**
 * Rendered-contrast sweep for the app window. Not wired into the build: it
 * needs a live dev server and a browser. Run it after any change to
 * theme.css, app.css, or a variant sheet:
 *
 *   cd app && pnpm exec vite --port 14320          # terminal 1
 *   npx -y agent-browser set media dark            # or light
 *   npx -y agent-browser open "http://localhost:14320/?scene=busy"
 *   npx -y agent-browser eval "($(grep -v '^//' scripts/contrast-walker.js | sed '1,/^ \*\//d'))()"
 *
 * Sweep both themes over: ?scene=busy, the plain library, ?scene=firstrun,
 * and the Settings/About tabs. Expect {failures: []}; each failure row names
 * the element, its computed ratio, and the 4.5/3.0 floor it missed.
 * (2026-08-20: a 40-combo sweep of base + 3 revamp variants passed clean.)
 */
() => {
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const parse = (s) => {
    const m = s.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    return m ? { c: [+m[1], +m[2], +m[3]], a: m[4] === undefined ? 1 : +m[4] } : null;
  };
  const mix = (top, alpha, under) => top.map((c, i) => c * alpha + under[i] * (1 - alpha));
  const effBg = (el) => {
    let bg = null; // composite from the element upward until opaque
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const p = parse(getComputedStyle(n).backgroundColor);
      if (p && p.a > 0) { layers.push(p); if (p.a >= 1) break; }
    }
    bg = [255, 255, 255];
    const rootDark = matchMedia("(prefers-color-scheme: dark)").matches;
    if (layers.length === 0 || layers[layers.length - 1].a < 1) bg = rootDark ? [28, 28, 31] : [251, 251, 252];
    for (let i = layers.length - 1; i >= 0; i--) bg = mix(layers[i].c, layers[i].a, bg);
    return bg;
  };
  const bad = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode;
    if (!t.textContent.trim()) continue;
    const el = t.parentElement;
    if (!el || seen.has(el)) continue;
    seen.add(el);
    if (el.closest('[aria-hidden="true"], [disabled], :disabled')) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || +cs.opacity === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const fgP = parse(cs.color);
    if (!fgP) continue;
    const bg = effBg(el);
    const fg = fgP.a < 1 ? mix(fgP.c, fgP.a, bg) : fgP.c;
    const px = parseFloat(cs.fontSize);
    const bold = +cs.fontWeight >= 700;
    const need = px >= 24 || (px >= 18.66 && bold) ? 3 : 4.5;
    const rr = ratio(fg, bg);
    if (rr < need) {
      bad.push({
        text: t.textContent.trim().slice(0, 40),
        sel: el.className ? `${el.tagName.toLowerCase()}.${String(el.className).split(" ")[0]}` : el.tagName.toLowerCase(),
        ratio: +rr.toFixed(2),
        need,
        px,
      });
    }
  }
  return { checked: seen.size, failures: bad };
}
