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
| Docsify (docsify.js.org) | BROKEN, deferred | skeleton SPA shell, hash routes; raw `.md` + `_sidebar.md` fetchable but unused | needs a docsify adapter (handoff) |
| Redoc demo (redocly.github.io/redoc) | degraded, deferred | JS-rendered; browser tier rescues root; in-page anchors invent 404 routes | phantom-anchor guard (handoff) |

## Measured constants (source of the committed values)

- Index vs bundle bullet-line ratio — indexes: docs.readme.com leaf 0.444,
  mid 0.700, developers.cloudflare.com leaf 0.308; bundles: svelte.dev
  docs/{svelte,kit,ai}/llms.txt 0.003–0.007 → `INDEX_MIN_BULLET_RATIO = 0.1`.
- RTD sitemaps: sphinx-doc.org = exactly 1 `<loc>`; urllib3.readthedocs.io and
  docs.celeryq.dev list only version roots (`/en/latest`, `/en/stable`, …) →
  `SITEMAP_MIN_YIELD = 3` + `_VERSION_ROOT_RE`.
- docs.readme.com root llms.txt lists 29 nested indexes → `MAX_NESTED_LLMS = 50`.

## Before → after (live, same probe commands)

- sphinx-doc.org: 1 wrong page → 5 pages incl. the requested quickstart.
- docs.readme.com: 0 twin pages (all browser-tier HTML) → 3-4/5 via `.md` twins, no thin pages.
- developers.cloudflare.com/argo-smart-routing: 1/5 twins + duplicate page → 4/4 twins, no duplicate.
- svelte.dev: byte-identical behavior to main (regression guard — an early
  expansion draft polluted it with 404 seeds; the bundle discriminator fixed it).
