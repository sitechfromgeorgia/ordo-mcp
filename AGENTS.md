# AGENTS.md — Georgia Legal Ecosystem MCP (Matsne + Courts + Parliament)

## What this is

Pure stdlib Python wrappers for Georgia's legal ecosystem, plus a single MCP server exposing
everything as tools/resources for AI agents:

- **`matsne_api.py`** — matsne.gov.ge legislation (საკანონმდებლო მაცნე)
- **`court_api.py`** — court practice: `EcdCourtAPI` (ecd.court.ge common courts) +
  `SupremeCourtAPI` (supremecourt.ge cassation + Grand Chamber)
- **`higher_courts_api.py`** — apex courts: `ConstitutionalCourtAPI` (constcourt.ge) +
  `EchrAPI` (HUDOC — European Court of Human Rights)
- **`parliament_api.py`** — `ParliamentAPI` (info.parliament.ge): bills and their legislative history
- **`research.py`** — `LegalResearch`: cross-source citation verification + article practice search
  (takes the wrapper instances; no HTTP of its own)

**Files:** `matsne_api.py`, `court_api.py`, `higher_courts_api.py`, `parliament_api.py`,
`research.py`, `SKILL.md` (reference docs),
`mcp_server.py` (MCP server), `pyproject.toml`, `AGENTS.md`, `README.md`, `LICENSE`

## Key facts

- **API wrappers: zero dependencies** — stdlib only (`json`, `re`, `time`, `urllib`, `html`, `dataclasses`, `typing`). Keep it that way; don't add deps to the wrappers.
- **MCP server: single dependency** — `mcp>=1.0.0,<2.0.0` in `pyproject.toml`. The `<2.0.0` pin is required: mcp 2.x removed `mcp.server.fastmcp`.
- **No tests, no CI/CD** — verify changes by running each wrapper's `__main__` live test (hits the real internet; HUDOC rate-limits if run repeatedly).
- **Git:** remote `https://github.com/sitechfromgeorgia/matsne-gov-ge-mcp.git`, branch `master`. Author identity `Sitech From Georgia <sitechfromgeorgia@example.com>` is set in repo-local config. `docs/` and `index.html` are unrelated untracked scratch files — stage files explicitly, never `git add -A`.

## Commands

```sh
python matsne_api.py                        # live test: matsne legislation
python court_api.py                         # live test: ecd + supreme + grand chamber
python higher_courts_api.py                 # live test: constcourt + echr (HUDOC)
python parliament_api.py                    # live test: parliament bills
python research.py                          # live test: verify_citation + research_article
uv run mcp_server.py                        # start MCP server (stdio transport)
uv run python -c "from mcp_server import *" # verify imports
```

On Windows the `__main__` tests print Georgian: `court_api.py` and `higher_courts_api.py`
reconfigure stdout to UTF-8, but `matsne_api.py` does not — if it raises `UnicodeEncodeError`,
run `$env:PYTHONIOENCODING="utf-8"; python matsne_api.py`.

### Claude Desktop config

```json
{
  "mcpServers": {
    "matsne-ge": {
      "command": "uv",
      "args": ["--directory", "PATH_TO_REPO", "run", "mcp_server.py"]
    }
  }
}
```

## Architecture

### API Wrapper (`matsne_api.py`)

- `MatsneAPI` class — wraps 12+ undocumented JSON endpoints reverse-engineered from Drupal JS
- `DocumentMeta` dataclass — structured container for document metadata
- Rate limit: 0.3s between requests (configurable), exponential backoff on retries (2 retries)
- Cookie tracking for session persistence across requests

### Court Practice Wrapper (`court_api.py`)

- `_BaseAPI` — shared rate-limit (0.3s), cookie tracking, retry, UTF-8 JSON/HTML handling.
  **This is the shared core** — `higher_courts_api.py` imports `_BaseAPI` + `_html_to_text`
  from here, so don't rename/move them without updating that import.
- `EcdCourtAPI` — ecd.court.ge (ASP.NET): POST+JSON endpoints for classifiers, decision search,
  full-text, court-act verification (barcode)
- `SupremeCourtAPI` — supremecourt.ge (Laravel-style): GET+HTML endpoints for cassation search,
  full text, download, and Grand Chamber (PDF lists)
- **`authority_level`** on every result: 5=Grand Chamber (binding), 4=Supreme cassation,
  3=appeals, 2=first instance — agents weigh precedent by this

### Higher Courts Wrapper (`higher_courts_api.py`)

- `ConstitutionalCourtAPI` — constcourt.ge (Laravel + DataTables): GET+HTML search of judicial
  acts (`/ka/judicial-acts`), results link to `?legal={id}` detail pages
- `EchrAPI` — HUDOC (hudoc.echr.coe.int): GET+JSON metadata search (`/app/query/results`) +
  full text (`/app/conversion/docx/html/body?library=ECHR&id={itemid}`); rate-limits hard, so
  default delay is 0.5s
- **`authority_level`**: 7=ECHR (supranational, binding on Georgia), 6=Constitutional Court
  (can strike down laws) — above all the domestic court levels in `court_api.py`

### Parliament Wrapper (`parliament_api.py`)

- `ParliamentAPI(_BaseAPI)` — info.parliament.ge is an ExtJS app; its REST endpoints
  (`/law/1/bill`, `/law/1/bill/{id}/billReview`, `/law/1/billReview/{id}/billReviewContent`,
  `/file/1/BillReviewContent/{file_id}`) return JSON/files to plain urllib (no Cloudflare block)
- `search_bills()` newest first, `get_bill()` = stages + votes + committees + file URLs

### Research (`research.py`)

- `verify_quote(text, quote)` — normalized exact match, else sliding-window `difflib` fuzzy match;
  verdict `exact` / `close` (≥0.85) / `partial` (≥0.6) / `not_found`
- `LegalResearch.verify_citation()` — fetches the cited source (matsne article/document/version,
  ecd, supreme, constitutional, echr) and runs `verify_quote`
- `LegalResearch.research_article()` — article text + Constitutional Court / Supreme Court /
  ecd full-text hits for `"<law genitive> <N>-ე მუხლ"` (`article_phrase()` builds the Georgian
  ordinal: `პირველი`, `მე-2`…`მე-20`, `მე-40`, `21-ე`, `361-ე`, `151¹`); `LAW_CONTEXT` maps core
  codes to their genitive name and Supreme Court chamber

### MCP Server (`mcp_server.py`)

- Imports `MatsneAPI`, `EcdCourtAPI`, `SupremeCourtAPI`, `ConstitutionalCourtAPI`, `EchrAPI`,
  `ParliamentAPI`, `LegalResearch` — wrappers untouched, MCP is a pure transport layer
- 14 `matsne_*` + 4 `court_*` + 4 `supreme_*` + 2 `constitutional_*` + 2 `echr_*` +
  2 `parliament_*` + `verify_citation` + `research_article` = 30 tools
- 3 prompts: `legal_memo`, `case_analysis` (IRAC), `law_change_review` — they share `_RULES`
  (sources with URLs, as_of_date, check_status, authority order, verify every quote)
- 6 resources: `matsne://document/{id}`, `matsne://today`, `court://decision/{id}/{instance}`,
  `supreme://case/{id}/{palata}`, `constitutional://act/{legal_id}`, `echr://case/{itemid}`
- Tools prefixed by source to avoid namespace conflicts

## Gotchas

- **`DocumentPart`** in tree response can be a **single object `{}` OR an array `[]`** — always type-check
- **HTML page fetch** (`/ka/document/view/{id}`) is **intermittently blocked** by bot detection — `get_document()` auto-falls back to `get_text_via_linked()` which always works via API
- **`linked/{id}/document`** returns the whole current document text via the API — the bot-proof fallback for `get_text_via_linked()` and `get_article()`
- **Tree anchors are stale** — `part_N` from `/document/tree` follow an older numbering than the live page; never fetch a specific article by a tree anchor (`get_article()` splits the page/linked HTML instead)
- **Historical versions** (`?publication=N`, 0 = original) exist only on the view page; a version's date is the amending act's *adoption* date, not entry into force — `version_for_date()` returns the amending act + a caveat
- **Article keys**: `"361"`, `"1507^1"` (input also `1507¹`); core codes resolve offline via `MatsneAPI.CORE_LAWS`
- **English translations** (`/en/document/view/{id}`) have no `part_N` anchors — articles are split on
  `Article N –` headings (`_ARTICLE_HEAD`); PDF at `/en/document/download/{id}/{publication}/en/pdf`.
  Untranslated docs serve the Georgian text on the `/en/` URL — `get_article(lang="en")` reports that
- **Voice server** uses `XZCookie` + `document.ZXKey` anti-bot — not bypassable without browser JS
- Search with `query` param returns different HTML structure than plain search (panel-based vs link-based)
- Titles may contain `<b>` tags from keyword highlighting — strip HTML when parsing
- `is-ajax=1` param converts search results from HTML to JSON
- All date params use `DD/MM/YYYY` format

### Parliament gotchas

- **`limit` > 50 returns an empty body** — `PAGE_MAX = 50`
- **`billTypeId` is ignored when `searchItem` is set** — `search_bills()` then filters client-side
  over the newest ≤500 text matches (`_search_filtered`) and reports `scanned`
- **`searchItem` also matches the package name**, so sibling bills of a package show up —
  `name_match` flags bills whose own name contains the query (sorted first when filtering)
- **Sort by `billPackage.registrationDate`** — sorting by `id` is string-ordered server-side
- Codes that are **organic laws** (e.g. Labour Code since 2013) are amended by `bill_type_id=11`, not 1

### Court practice gotchas

- **ecd.court.ge** is ASP.NET POST+JSON — always send `Content-Type: application/json; charset=utf-8`; dates are `YYYY-MM-DD` (or `null`); decision dates come back as C# `/Date(ms)/` (use `_parse_csharp_date`)
- **supremecourt.ge** is GET+HTML — Georgian full-text must be URL-encoded (`urllib.parse.urlencode`); dates are `YYYY/MM/DD`; `supreme_api_token` is decorative (ignored)
- **Grand Chamber decisions are NOT in `/ka/cases` search** — they live on static `/decisions-grand-chamber/...` pages as PDFs; `grand_chamber()` scrapes their titles + PDF URLs
- **Grand Chamber 'norms' category** (`didi-palatis-gadatsyvetilebebshi-gamoyenebul-normata-ganmartebebi`) is a single PDF of compiled binding norm interpretations — the highest authority below the Constitution/law
- **`court_verify_act` requires BOTH barcode and case number** — one alone returns an error page

### Higher-courts gotchas

- **constcourt.ge** renders results server-side only when a search is performed — a bare
  `/ka/judicial-acts` returns "record not found"; pass `fullsearch`/`intext`/`number`
- **constcourt full-text search** needs `intext` + `intextsearch=intextsearch` (the wrapper adds it)
- **constcourt detail pages** use `?legal={id}` (not a path segment); act text lives in
  `id="printablePageContent"` (extract with balanced-div walk)
- **HUDOC rate-limits hard** — bursty requests return empty/404; `EchrAPI` defaults to 0.5s delay
- **HUDOC query** must include `contentsitename:ECHR` and exclude `doctype:PR` (press releases) —
  the wrapper builds this; itemids look like `001-57574`
- **HUDOC full text** is an HTML body (numeric entities) — run through `_html_to_text` (unescape)
- **HUDOC is behind Cloudflare bot management** — a browser UA *without* the matching
  `sec-ch-ua*` client hints, or a non-browser UA such as `Python-urllib/3.x`, is answered with a
  managed challenge (HTTP 403 + `Cf-Mitigated: challenge`) even though `curl` gets 200 on the same
  URL. `EchrAPI` sends a browser-consistent header set and retries once as a plain
  `curl/<version>` client if challenged anyway. When a 403 appears, check the `Cf-Mitigated`
  response header before blaming the endpoint.

## PRO access

- **Internal JSON API** bypasses Matsne.gov.ge's PRO/subscription paywall for **most** documents — you can retrieve full text and metadata for PRO-only docs via the API wrapper even when the website blocks them
- **Exception:** Some PRO documents returned by `suggest`/`search` return `Access Denied` from the internal API too (e.g., 6382847, 6427001) — typically recent internal government reports or administrative orders
- The `suggest` endpoint (keyword search) finds PRO documents by title/ID even when their content is inaccessible
- **Strategy:** if `get_document()` fails with `Access Denied`, the doc still exists and can remain in results with metadata only (no text available)

## Reference

Full endpoint documentation in `SKILL.md` — read it before modifying API methods.
