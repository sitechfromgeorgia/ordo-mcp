# AGENTS.md — Ordo (Georgian legal research MCP)

## What this is

Python wrappers for Georgia's legal ecosystem, plus a single MCP server exposing everything as
tools/resources/prompts for AI agents that assist lawyers (the server is built for *other people's*
agents — keep tool descriptions self-explanatory and outputs citeable):

- **`matsne_api.py`** — matsne.gov.ge legislation (საკანონმდებლო მაცნე)
- **`court_api.py`** — court practice: `EcdCourtAPI` (ecd.court.ge common courts) +
  `SupremeCourtAPI` (supremecourt.ge cassation + Grand Chamber)
- **`higher_courts_api.py`** — apex courts: `ConstitutionalCourtAPI` (constcourt.ge) +
  `EchrAPI` (HUDOC — European Court of Human Rights)
- **`parliament_api.py`** — `ParliamentAPI` (info.parliament.ge): bills and their legislative history
- **`research.py`** — `LegalResearch`: cross-source citation verification + article practice search
  (takes the wrapper instances; no HTTP of its own)
- **`supreme_practice_api.py`** — `SupremePracticeAPI`: Supreme Court analytics (generalizations,
  recommendations, explanations, decision collections, ECHR translations, journal)
- **`agencies_api.py`** — administrative bodies: MoF tax dispute council, Revenue Service disputes
  + guidance (InfoHub API), competition agency, Public Defender, procurement disputes, Bar ethics
- **`deadlines.py`** — `DeadlineCalculator`: procedural deadlines computed from the LIVE norms
- **`state_fee.py`** — `FeeCalculator`: court state fees (სახელმწიფო ბაჟი) from the LIVE CPC art. 39
- **`registries_api.py`** — public registries (anonymous endpoints only): companies, real estate,
  liens, movable pledges, lawyers, notaries, procurement supplier lists, plus companyinfo.ge
  (Transparency International's company/people graph) and the Geostat business register
- **`pdf_text.py`** — PDF text extraction/search with legacy Georgian font repair

**Files:** the modules above, `SKILL.md` (reference docs),
`mcp_server.py` (MCP server), `pyproject.toml`, `AGENTS.md`, `README.md`, `LICENSE`

## Key facts

- **Dependencies only where they buy real capability.** The site wrappers are stdlib-only and
  should stay so (they are plain HTTP + parsing). `pdf_text.py` needs `pypdfium2` (Apache/BSD,
  prebuilt wheels) — imported lazily, so every other module imports without it. Adding a
  dependency is fine when a feature needs it; prefer permissive licenses (the project is MIT —
  AGPL libraries such as pymupdf are out).
- **MCP:** `mcp>=1.0.0,<2.0.0` — the `<2.0.0` pin is required: mcp 2.x removed `mcp.server.fastmcp`.
- **No tests, no CI/CD** — verify changes by running each wrapper's `__main__` live test (hits the real internet; HUDOC rate-limits if run repeatedly).
- **Git:** remote `https://github.com/sitechfromgeorgia/ordo-mcp.git`, branch `master`. Author identity `Sitech From Georgia <sitechfromgeorgia@example.com>` is set in repo-local config. `docs/` and `index.html` are unrelated untracked scratch files — stage files explicitly, never `git add -A`.

## Commands

```sh
python matsne_api.py                        # live test: matsne legislation
python court_api.py                         # live test: ecd + supreme + grand chamber
python higher_courts_api.py                 # live test: constcourt + echr (HUDOC)
python parliament_api.py                    # live test: parliament bills
python research.py                          # live test: verify_citation + research_article
python supreme_practice_api.py              # live test: Supreme Court analytics (~30 requests)
python agencies_api.py                      # live test: all administrative bodies
python deadlines.py                         # live test: deadline calculations
python state_fee.py                         # live test: state-fee calculations
python registries_api.py                    # live test: all public registries
uv run python pdf_text.py                   # live test: PDF extraction (needs pypdfium2)
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
    "ordo": {
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

### Supreme Court analytics (`supreme_practice_api.py`)

- New site lists load via `POST /get-analitics` (`cat=<id>`, CSRF `_token` from the page's inline
  JS + session cookie); cat ids 29, 30, 32, 10, 12, 13, 23 carry data
- Decision collections: static `/decisions/{civil|criminal|admin|disciplinary|qualification}` pages
- `old.supremecourt.ge` returns empty bodies; its recommendations / explanations / publications
  indexes are embedded as `LEGACY_CATALOG` (from Wayback); the PDFs themselves are still live
- Authority: analytical guidance / explanations / collections = 4 with `binding: False` (Grand
  Chamber 5 prevails); ECHR translations = 7 (unofficial translation); research/journal = 1

### Administrative bodies (`agencies_api.py`)

- `authority_level = 1` for all (reviewable by courts), `kind` = administrative / advisory /
  self-regulatory body, `binding` note per item
- InfoHub (`infohubapi.rs.ge`, header `languageCode: ka` mandatory, `take` < 99): type 16 MoF
  council, 15 RS disputes, 2/4/17 guidance — full text + PDF export
- gcca.gov.ge (competition.ge is parked), ombudsman.ge, tenders.procurement.gov.ge dispute engine
  (PHP session: POST filter then GET pages), gba.ge ethics PDFs. pdps.ge was unreachable (timeout)

### Deadlines (`deadlines.py`)

- **No durations stored.** `PROCEDURES` maps a procedure to (doc_id, article, part, `near` selector);
  the period is extracted at runtime from the article text (version in force on the start date)
  and returned with the quoted sentence. Ambiguous → `not_computed`, never a guess
- Counting rules (CPC 60–61, CrPC 86, GAC 15, CC 121–127) and holidays (Labour Code "უქმე დღეები"
  article, found by heading; Orthodox Easter computed) are also read live and regex-checked
- Every result carries `warning` (ka/en + agent instruction) that the agent must relay

### State fee (`state_fee.py`)

- **No amounts stored.** `FEE_TYPES` maps a fee type to a CPC art. 39(1) sub-point letter + a
  keyword that must appear in it (renumbering tripwire); percentage/minimum/fixed amounts, the
  per-instance ceilings for individuals/legal entities (39(3)), the magistrate halving (39(2)) and
  the settlement reductions (49(1)–(2)) are read live and regex-checked. Superscript sub-points
  (`ა³`, `ე¹`) are keyed as `ა3`, `ე1`; zero-width spaces are stripped
- Reuses `DeadlineCalculator._article` (shared page cache); exemptions (CPC 46–48, APC 9) are
  returned as `related_norms`, never applied automatically. Warning on every result

### Public registries (`registries_api.py`)

- `_RegistryBase(_AgencyBase)` + `_post_json`; every result has `warning` (personal data, reference
  only, official extract prevails). Inputs are validated: `_ident()` = 9-digit company code or
  11-digit personal number, `_cadastral()` = dotted cadastral code. No person-name search by design
- `CompanyRegistryAPI` — enreg.reestri.gov.ge `/_dea/main.php` (form POST, PHP session: GET
  `?m=new_index` first; pages = re-POST `p=N` after the filter): `find_legal_persons`,
  `find_participations2`, `search_app` (`s_app_tr_type` ids in `APP_TYPES`)
- `PropertyRegistryAPI` — maps.gov.ge `/map/portal/search` → `/lr/bo/mg/getinfo.alpha?lbl=…`
  (parcel card: owners + document list with `bs.napr.gov.ge/GetBlob` PDF extracts, readable by
  pdf_text); naprweb.reestri.gov.ge `/api/search` JSON (dates `DD-MM-YYYY`)
- `LienRegistryAPI` — naprlmr.reestri.gov.ge `/api/searchlien` (`flObjectType: "3"` + `flObject` =
  cadastral code; `flIdnumber` = ID code); `MovablePledgeAPI` — mmr.reestri.gov.ge (form POST +
  `c=mortgage&m=show_lien_details&app_id=`)
- `LawyerRegistryAPI` — gba.ge `ajax/employers/ka-<hash>.json` (hash read from the members-list
  page, known URL as fallback, cached per instance); `NotaryAPI` — api.my.gov.ge `NotaryBureaus/*`
  (headers `Content-Language: ka-ge`, `DeviceTypeId: 1`; regions: თბილისი / აჭარა / რაიონები);
  `ProcurementListsAPI` — procurement.gov.ge `/ka/News/List?category=4|3|5` (black/white/warned)
- `CompanyInfoAPI` — api.companyinfo.ge `/api/*` JSON (TI Georgia, monthly NAPR mirror +
  sanctions, blacklists, declarations, reportal, permits): `corporations/search`,
  `getcorporation/{idCode}` → `corporations/{id}` + `company-info/{id}` +
  `corporation-person-info/{id}`, `people/search`, `people/{id}`. The ONLY person-by-name search:
  full name required, namesakes flagged, home address dropped; personal numbers returned in full
  (public data) so the agent can confirm identity and cross-check other registries
- `GeostatRegisterAPI` — br-api.geostat.ge `/api/documents` (filters `identificationNumber`,
  `organizationName`, `activityCode` without section letter, `legalAddressRegion`, `legalForm`,
  `size`, `isActive`), `/representatives`, `/full-name-web`, `/address-web`, `/activities`,
  `/locations/regions`; license: cite საქსტატი

### PDFs (`pdf_text.py`)

- pypdfium2 (best Georgian layout, fastest); `\ufffe` soft hyphens stripped; legacy fonts
  (AcadNusx, LitNusx, AcadMtavr, LitMtavrPS, SakarMtavr, …) converted per font run; overprinted
  "bold" lines deduped; scanned pages detected (`scanned`, noise OCR layers discarded — no OCR)
- 40 MB cap, in-process cache (32 PDFs), PDFium calls serialized by a lock

### MCP Server (`mcp_server.py`)

- Imports every module above — wrappers untouched, MCP is a pure transport layer
- 70 tools: 14 `matsne_*`, 5 `court_*`, 4 `supreme_*`, 2 `constitutional_*`, 2 `echr_*`,
  2 `parliament_*`, `verify_citation`, `research_article`, 5 `deadline_*`, 3 `fee_*`, 2 `pdf_*`,
  4 `supreme_practice_*`, 13 administrative-body tools (`tax_dispute_*`, `rs_dispute_*`,
  `tax_guidance_*`, `competition_search`, `ombudsman_*`, `procurement_dispute_*`, `bar_ethics_search`),
  13 `registry_*`, 4 `companyinfo_*`, 4 `geostat_*` (78 total)
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
- **`billTypeId` is ignored together with `searchItem`**, but the advanced-search params
  (`billName`, `packageName`, `billDescription`, `initiator`, `registrationDate1/2`) DO combine
  with it — `search_bills()` gets name matches server-side, then scans "related" bills (query only
  in the package) in parallel (6 workers, 0.3 s spacing) only when needed (`scan_related`)
- **`searchItem` also matches the package name**, so sibling bills of a package show up —
  `name_match` flags bills whose own name contains the query (name matches come first)
- The server spends ~0.1 s per returned row, but serves concurrent requests in parallel
- **Sort by `billPackage.registrationDate`** — sorting by `id` is string-ordered server-side
- Codes that are **organic laws** (e.g. Labour Code since 2013) are amended by `bill_type_id=11`, not 1

### Court practice gotchas

- **ecd.court.ge only holds decisions dated 2018-01 … 2020-04** (~44k) — tell users when practice
  outside that window is needed (use the Supreme Court search instead). No other public source of
  post-2020 first-instance / appeal decisions exists: publication stopped when the Constitutional
  Court's 2019 data-protection ruling took effect (1 May 2020); Organic Law on Common Courts
  art. 13(3¹) (in force 2024) requires depersonalized publication but is not implemented
- **ecd `Texts` semantics:** one word = substring; several words = exact phrase of whole adjacent
  words; list items = AND; no wildcards/OR; a hyphen splits words; take > 50 → HTTP 500.
  `search_decisions` cleans texts and expands `word*` into case forms (`expand_forms`) merged by
  `search_any`; `search_article` (phrase / cooccur / classifier modes) is the article-level entry
- **ecd LitigationTypes encode articles** only for criminal (Criminal Code "177 2 ა", "189 (1) 4"
  = 189¹ part 4) and administrative-offence cases; civil has ~90 dispute categories only

- **ecd.court.ge** is ASP.NET POST+JSON — always send `Content-Type: application/json; charset=utf-8`; dates are `YYYY-MM-DD` (or `null`); decision dates come back as C# `/Date(ms)/` (use `_parse_csharp_date`)
- **supremecourt.ge** is GET+HTML — Georgian full-text must be URL-encoded (`urllib.parse.urlencode`); dates are `YYYY/MM/DD`; `supreme_api_token` is decorative (ignored)
- **Grand Chamber decisions are NOT in `/ka/cases` search** — they live on static `/decisions-grand-chamber/...` pages as PDFs; `grand_chamber()` scrapes their titles + PDF URLs
- **Grand Chamber 'norms' category** (`didi-palatis-gadatsyvetilebebshi-gamoyenebul-normata-ganmartebebi`) is a single PDF of compiled binding norm interpretations — the highest authority below the Constitution/law
- **`court_verify_act` requires BOTH barcode and case number** — one alone returns an error page

### Registry gotchas

- **CAPTCHA-protected, deliberately not wrapped:** enreg company detail / extract (`show_legal_person`,
  `enteredCaptcha`), debtors registry (reCAPTCHA v3), naprweb `/api/app/{id}`. Do not add them
- naprweb ID-code (`person`) searches without a date range time out — default 90 days
- `maps.napr.gov.ge` fails TLS (hostname mismatch) — use `maps.gov.ge`
- **companyinfo.ge robots.txt** disallows individual-entrepreneur pages (legalForm 1 — excluded),
  three listed corporations and `/uploads/` on the API host (registry PDFs — links not returned).
  The www site (SSR Angular) hangs; use the API at ~1 req/s. Junk duplicate rows exist
  (`idCode` with a leading space) — deduped. Role labels can be dirty
- **Geostat API** allows ~50 requests/minute (`X-RateLimit-*`) — 1.2 s spacing; it returns
  directors' personal numbers and phones (public; returned as `head_id_number`, `phone`)
- enreg application rows show only the applicant, not the subject company (that is on the
  CAPTCHA page); procurement list titles of individual entrepreneurs include personal numbers

### PDF / analytics / agencies gotchas

- **Most pre-2010 supremecourt.ge PDFs use legacy Georgian fonts** — without `pdf_text`'s
  conversion every library returns Latin gibberish ("saqarTvelos")
- **matsne PDF downloads need a real publication number** — `/download/{id}/0/...` → Access Denied
- Older parliament "adopted law" / vote files are scans (no text layer) — reported, not OCR'd
- supremecourt.ge `get-analitics` needs a fresh CSRF token; a stale one gives HTTP 405
- InfoHub `/api/documents/{id}` is 401 — use `/details`; search rows have no `outcome`
- Procurement dispute engine ignores free-text org names — resolve via `find_organization`

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
