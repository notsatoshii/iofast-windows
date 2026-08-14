# Doc-site shape audit — 2026-08-13

11 live doc sites probed with `engine/tools/scrape_quality.py` (5-page bounded
scrapes) to hunt the #29 failure class: tiny mangled fragments where the true
content never reaches the extractor. The `*.quality.json` files here are the
harness reports backing the constants committed on this branch.

## Matrix result

| Framework (site) | Verdict | Defect found | Fix |
|---|---|---|---|
| Docusaurus (docusaurus.io, jestjs.io) | healthy | — | — |
| VitePress (vitepress.dev) | healthy | already rides #29 `.md` twins | — |
| MkDocs Material (squidfunk.github.io) | healthy | — | — |
| Fern (buildwithfern.com) | healthy | already rides twins | — |
| Redocly (redocly.com) | healthy | already rides twins | — |
| GitBook (gitbook.com/docs) | healthy | already rides twins | — |
| Sphinx / RTD (sphinx-doc.org, requests.readthedocs.io) | BROKEN → fixed | sitemap lists 1 version root; crawl collapsed to one wrong page | `SITEMAP_MIN_YIELD` crawl fallback + version-root discriminator + start URL always seeded |
| ReadMe.io (docs.readme.com) | DEGRADED → fixed | nested llms.txt (root → section indexes → `.md` leaves) never expanded; browser tier burned per page | nested expansion + bullet-ratio index/bundle discriminator |
| Cloudflare (developers.cloudflare.com) | DEGRADED → fixed | `<path>/index.md` twin convention; re-derived `<path>.md` 404'd + duplicate page | `md_twin_map` exact twins + `/index` canonicalization |
| Docsify (docsify.js.org) | BROKEN → fixed | skeleton SPA shell, hash routes; raw `.md` + `_sidebar.md` fetchable but unused | api-mode `docsify` adapter: shell for config, `_sidebar.md` for the page list, raw `.md` per page |
| Redoc demo (redocly.github.io/redoc) | degraded, deferred | JS-rendered; browser tier rescues root; the crawl budget is spent on client-side routes the server does not serve — **not** invented links, see below | crawl-budget fix, still open (handoff) |

## Measured constants (source of the committed values)

- Index vs bundle bullet-line ratio — indexes: docs.readme.com leaf 0.444,
  mid 0.700, developers.cloudflare.com leaf 0.308; bundles: svelte.dev
  docs/{svelte,kit,ai}/llms.txt 0.003–0.007 → `INDEX_MIN_BULLET_RATIO = 0.1`.
- RTD sitemaps: sphinx-doc.org = exactly 1 `<loc>`; urllib3.readthedocs.io and
  docs.celeryq.dev list only version roots (`/en/latest`, `/en/stable`, …) →
  `SITEMAP_MIN_YIELD = 3` + `_VERSION_ROOT_RE`.
- docs.readme.com root llms.txt lists 29 nested indexes → `MAX_NESTED_LLMS = 50`.
- Docsify detection (2026-08-14, 4 live sites): `window.$docsify` appears on all
  four (docsify.js.org, adrianbj.github.io/TracyDebugger, libocca.org,
  adrien2p.github.io/medusa-extender); the docsify script src varies wildly
  (`npm/docsify@5/dist/docsify.min.js`, `gh/…/docsify-nightly/lib/docsify.min.js`,
  `unpkg.com/docsify@4/lib/…`, bare `npm/docsify@4`) → the detector needs both
  the config object AND any script src containing `docsify`, so a page that only
  *writes about* the config is not mistaken for a site built with it.

## Before → after (live, same probe commands)

- sphinx-doc.org: 1 wrong page → 5 pages incl. the requested quickstart.
- docs.readme.com: 0 twin pages (all browser-tier HTML) → 3-4/5 via `.md` twins, no thin pages.
- developers.cloudflare.com/argo-smart-routing: 1/5 twins + duplicate page → 4/4 twins, no duplicate.
- svelte.dev: byte-identical behavior to main (regression guard — an early
  expansion draft polluted it with 404 seeds; the bundle discriminator fixed it).
- docsify.js.org: 1 shell page, 0 prose chars → 5/5 markdown pages (1067–4296
  chars, quality verdict OK). adrianbj.github.io/TracyDebugger: 5/5 pages
  (2215–40681 chars) with the configured `introduction.md` homepage.
  apache.github.io/incubator-weex-ui (docsify with NO `_sidebar.md`): homepage
  only, 5645 chars, ledger reconciles — the sidebar-less shape degrades, never fails.

## Redoc: the "phantom anchor" reading was wrong (2026-08-14)

The original row blamed link extraction for inventing `/redoc/3.x/{orders,products,
authorization}`. It does not. The rendered DOM of `https://redocly.github.io/redoc/3.x`
carries 17 anchors, and those three URLs are literally among them:

    '/redoc/3.x/authorization'   '/redoc/3.x/products'   '/redoc/3.x/orders'
    '/redoc/3.x/statistics'      '/redoc/3.x/tools'      '/redoc/3.x/resources'

(headless chromium, `els.map(e => e.getAttribute('href'))`). They are client-side
routes: the SPA resolves them in the browser, and the static host answers 404 for
every one. The engine followed real links and reported honest failures.

The remaining defect is therefore about **budget, not extraction**: `spider.py`
stops enqueueing at `len(ledger.enqueued) >= max_pages`, so failures count against
the page budget. A 5-page probe here spends 3 of its 5 slots on 404s and exits 1.
Any history-mode SPA on a static host has the same shape, so a fix belongs in the
crawl budget (bound *written* pages, with a separate ceiling on total attempts so a
broken site still cannot spin) — not in a Redoc-specific anchor filter. Deliberately
not attempted in the same pass as the docsify adapter: it changes the crawler core,
which resume, update and cancel all read through `ledger.enqueued`.
