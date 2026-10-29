---
name: matsne-gov-ge-scraping
description: >
  Complete API wrappers for Georgia's legal ecosystem — matsne.gov.ge legislation,
  ecd.court.ge common-court decisions/acts, and supremecourt.ge cassation + Grand
  Chamber decisions. Reverse-engineered undocumented APIs. Includes 12+ matsne
  endpoints, court classifiers/search/full-text, rate limiting, retry logic.
---

# Matsne.gov.ge — Complete API Reference

## Overview

matsne.gov.ge is Georgia's official legislative database (საკანონმდებლო მაცნე).
It runs Drupal CMS with custom modules. No official public API exists, but the
site's JavaScript exposes **12+ internal JSON endpoints** that are significantly
more reliable than HTML scraping.

**API Wrapper:** `matsne_api.py`  
**MCP Server:** `mcp_server.py`

## Quick Start

### Python API
```python
from matsne_api import MatsneAPI

api = MatsneAPI()

# Autocomplete search (0.58s)
results = api.suggest("პერსონალური")

# Search with filters
docs = api.search(page=0, query="შრომის", limit=10)

# Full document
doc = api.get_document(6840140)

# Quick date check
api.has_today()  # → True/False
api.today_documents()  # → [{id, title, date}, ...]
```

## Complete Endpoint Catalog

### Public Endpoints (no auth required)

| # | Endpoint | Method | Response | Speed | Description |
|---|----------|--------|----------|-------|-------------|
| 1 | `/document/suggest` | POST | `[{id, state, title}]` | 0.58s | Autocomplete search |
| 2 | `/ka/document/search?page=N&is-ajax=1` | GET | `{documents_list, pagination}` | 1.3s | JSON search results |
| 3 | `/ka/document/tree/{id}/{part}` | GET | `{Title, Anchor, DocumentPart}` | 1.1s | Document structure tree |
| 4 | `/ka/document/backReferences/{id}?part_id=X` | GET | `[{ref}]` or `[]` | 1.0s | Back references |
| 5 | `/ka/document/comment/{id}/read?part_id=X` | GET | `[{comment}]` or `[]` | 1.0s | Read comments |
| 6 | `/ka/document/linked/{id}/{anchor}` | GET | `{html: "..."}` | 1.0s | Inline HTML fragment |
| 7 | `/annotation/api/search?uri=...` | GET | `{total, rows}` | 1.0s | Annotation search |
| — | `/ka/document/view/{id}?publication=N` | GET | HTML | 2–4s | Consolidated version N (0 = original); bot-protected like any view page |

### Authenticated Endpoints (require login)

| # | Endpoint | Method | Description |
|---|----------|--------|-------------|
| 8 | `/ka/document/comment/{id}/create` | POST | Create comment |
| 9 | `/ka/document/comment/{cid}/update` | POST | Update comment |
| 10 | `/ka/document/comment/{cid}/remove` | GET | Delete comment |
| 11 | `/ka/document/comment/{cid}/rate` | GET | Rate comment |
| 12 | `/ka/document/highlight/{id}/create` | POST | Create highlight |
| 13 | `/favorites` | POST | Add/remove favorite |
| 14 | `/favorites?method=getFavoritesList` | GET | Get favorites list |

### Voice Server

| # | Endpoint | Method | Description |
|---|----------|--------|-------------|
| 15 | `speech.matsne.gov.ge:443/voice?t=TEXT` | GET | TTS for short text |
| 16 | `speech.matsne.gov.ge:443/voice/big` | POST | TTS for full document |

### MCP Server

The repo includes an MCP server (`mcp_server.py`) that exposes the matsne API as 14 tools + 2 resources (30 tools / 3 prompts in total — see the other sections):

**Tools:**
- `matsne_suggest` — keyword autocomplete
- `matsne_search` — filtered search (date, type, organ, pagination)
- `matsne_get_document` — full document (meta + text + tree + back-refs + comments); `publication` / `as_of_date` / `max_chars`
- `matsne_get_article` — one article by citation (law name or doc_id + article, optional part, `as_of_date`)
- `matsne_list_versions` — consolidated versions + amending acts
- `matsne_compare_versions` — article-level diff between two versions
- `matsne_check_status` — in force / expired, latest version, recent amendments
- `matsne_get_tree` — document structure tree
- `matsne_get_back_references` — citing documents
- `matsne_get_comments` — user comments
- `matsne_get_linked` — inline HTML fragment for a document part
- `matsne_search_by_keyword` — quick active-doc keyword search
- `matsne_has_today` — check today's publications
- `matsne_today_documents` — today's documents list

**Resources:**
- `matsne://document/{id}` — document as JSON
- `matsne://today` — today's documents as JSON

**Run:** `uv run mcp_server.py`

## Required Headers

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
Accept: application/json          # for JSON endpoints
X-Requested-With: XMLHttpRequest  # for JSON endpoints
Accept-Language: ka,en;q=0.9
```

## Endpoint Details

### 1. POST /document/suggest — Autocomplete

**Input:** `text=პერსონალური` (form-urlencoded)
**Output:**
```json
[
  {"id": 1244845, "state": "active", "title": "პერსონალური მონაცემების..."},
  {"id": 2352161, "state": "expired", "title": "..."}
]
```
- Returns up to 50 results
- `state` can be "active" or "expired"
- Fastest endpoint (~0.58s)

### 2. GET /ka/document/search?is-ajax=1 — Search

**Parameters:**
- `page` — Page number (0-indexed)
- `query` — Full-text search
- `limit` — Results per page (5, 10, 20, 30, 50, 100)
- `sort` — Sort order (e.g., `publishDate_desc`)
- `issuer` — Document issuer filter
- `type` — Document type filter
- `number` — Document number
- `registration_code` — Registration code
- `signing_date_fr[date]` — Date from (DD/MM/YYYY)
- `signing_date_to[date]` — Date to (DD/MM/YYYY)

**Output:** `{documents_list: "<HTML>", pagination: "<HTML>"}`
- `documents_list` contains panel-based HTML with document links
- The wrapper parses this into structured `{id, title, date, doc_type, organ}`

**HTML Structure (query search):**
```html
<div class="panel panel-success document-search-result-item">
  <div class="panel-heading">
    <p><a href="/ka/document/view/{id}">Title</a></p>
  </div>
  <div class="panel-body">
    <ul class="list-inline">
      <li><small>დოკუმენტის ტიპი</small></li>
      <li><small>●</small></li>
      <li><small>ორგანო</small></li>
      <li><small>●</small></li>
      <li><small>DD/MM/YYYY</small></li>
    </ul>
  </div>
</div>
```

### 3. GET /ka/document/tree/{id}/{part} — Document Tree

**Output:**
```json
{
  "Title": "დოკუმენტი. კანონის შაბლონი",
  "Anchor": "DOCUMENT:1;",
  "DocumentPart": {
    "Title": "საქართველოს კანონი",
    "Anchor": "DOCUMENT:1;HEADER:1;"
  }
}
```

**Variations by document type:**
- **Simple law:** Flat structure, single DocumentPart
- **Decree:** Multiple DocumentParts (HEADER, ARTICLE:1, ARTICLE:2)
- **Complex code:** Deep nested tree (506KB for Civil Code)
- **Resolution:** Same flat structure as simple law

**Tree node structure:**
```json
{
  "Title": "მუხლი 1",
  "Anchor": "DOCUMENT:1;ARTICLE:1;",
  "DocumentPart": [...]  // nested parts or single object
}
```

### 4. GET /ka/document/backReferences/{id} — Back References

**Output:** `[]` (empty array) or list of references
- Returns documents that cite/reference the given document
- New documents typically have 0 back-references

### 5. GET /ka/document/comment/{id}/read — Comments

**Output:**
```json
[{
  "cid": 12345,
  "text": "Comment text",
  "html": "<p>Comment HTML</p>",
  "anchor": "DOCUMENT:1;",
  "rating": 5,
  "author": {
    "name": "username",
    "fullName": "Full Name",
    "uid": 12345,
    "avatar": "..."
  },
  "created": 1234567890,
  "formattedCreate": "01/01/2026",
  "changed": 1234567890,
  "formattedChange": "01/01/2026"
}]
```

### 6. GET /ka/document/linked/{id}/{anchor} — Inline Fragment

**Output:** `{"html": "<a name='...'>...</a><table>..."}`
- Returns HTML fragment for inline display of linked documents
- Used when hovering over document links within other documents
- `anchor` format: `DOCUMENT:1;HEADER:1;` or `DOCUMENT:1;ARTICLE:1;`, or a view-page anchor `part_N`
- **`anchor=document` returns the WHOLE current document** (e.g. ~1.2 MB for the Civil Procedure Code) — the bot-proof way to get full text; `get_text_via_linked()` and `get_article()` use it as fallback
- ⚠️ `part_N` anchors from the **tree** endpoint are stale (they follow an older numbering — Civil Procedure Code tree says `part_304` for art. 178, the live page uses `part_185`). Never use tree anchors to fetch a specific article.

### 7. GET /annotation/api/search — Annotations

**Parameters:** `uri=document/view/{id}` (URL-encoded)
**Output:** `{"total": 0, "rows": []}`

## Versions, Articles, Status (legal research)

View pages carry everything needed for "which text applied when":

| Data | Where on `/ka/document/view/{id}` | Parsed by |
|---|---|---|
| Consolidated versions | sidebar `<a href="/ka/document/view/{id}?publication=N" class="list-group-item">DD/MM/YYYY</a>`; current one has `class="active list-group-item"` | `_parse_versions()`, `_active_version()` |
| Amending acts | `#changesModal` → `<a href="/ka/document/view/{amending_id}" class="row list-group-item">` with index, მიღების/გამოქ. თარიღი, number, title | `_parse_amendments()` |
| Expiry | metadata row `ძალის დაკარგვის თარიღი` (absent while in force) | `parse_document_meta()` → `status`, `expiry_date` |
| Articles | `<a name="part_N">მუხლი 361. …</a>`; superscripts as `მუხლი 1507<sup>1</sup>` | `_split_articles()` |

- **Version date = adoption date of the amending act**, not entry into force (Civil Code v140: adopted 25/06/2026,
  published 29/06/2026). `version_for_date()` picks the latest version dated on/before the target date and returns
  the amending act + a caveat so the agent can check transitional provisions.
- Article keys: `"361"`, `"1507^1"` (input also accepts `1507¹`); text renders superscripts as `¹²³`.
- English: `get_article(..., lang="en")` reads `/en/document/view/{id}` (no `part_N` anchors —
  split on `Article N –` headings) and returns `pdf_url` = `/en/document/download/{id}/{pub}/en/pdf`
  plus a `translation_caveat` (unofficial; Georgian text is authentic). Untranslated documents
  serve Georgian text on the English URL → error "No English translation".
- `get_full_text(doc_id, publication)` — complete text (no 500-line cap), view page or linked fallback.
- Core codes resolve without a request via `MatsneAPI.CORE_LAWS` (civil 31702, civil procedure 29962,
  criminal 16426, criminal procedure 90034, general administrative 16270, administrative procedure 16492,
  administrative offences 28216, tax 1043717, labour 1155567, constitution 30346); other names go through `suggest()`.

```python
api.get_article("361", law="სამოქალაქო კოდექსი", part=2)          # current text of art. 361 §2
api.get_article("178", law="civil procedure code", as_of_date="2015-03-01")
api.list_versions(31702)                                           # 141 versions + 140 amending acts
api.compare_versions(31702, 138, 140)                              # added / removed / modified articles + diffs
api.check_status(26350)                                            # old Labour Code → in_force False, expiry 27/12/2010
```

## Drupal.settings API Map

Found in `Drupal.settings.document_view.api` on document pages:

```json
{
  "document": "/ka/document/view/",
  "backReferences": "/ka/document/backReferences/{id}?part_id=%",
  "markerString": "/ka/document/view/{id}&getMarkers",
  "tree": "/ka/document/tree/{id}/{part}",
  "comments": {
    "read": "/ka/document/comment/{id}/read?part_id=%",
    "create": "/ka/document/comment/{id}/create",
    "update": "/ka/document/comment/%/update",
    "remove": "/ka/document/comment/%/remove",
    "rate": "/ka/document/comment/%/rate"
  },
  "highlights": {
    "create": "/ka/document/highlight/{id}/create?publication_id=0",
    "read": "/ka/annotation/api/search?uri=document/view/{id}",
    "update": "/ka/annotation/api/annotations/%?action=update",
    "remove": "/ka/annotation/api/annotations/%?action=destroy"
  },
  "voiceServer": "https://speech.matsne.gov.ge:443/voice/big"
}
```

## Anti-Bot / Bot Detection

### What's Protected
- `/ka/document/view/{id}` — INTERMITTENTLY blocked (some IDs work, some don't)
- `/ka/document/recent` — CONSISTENTLY blocked
- Search form date pickers — Non-functional via automation
- Direct URL search with `date_from/date_to` — Blocked

### What's NOT Protected (API endpoints work reliably)
- `/document/suggest` — Always works ✅
- `/ka/document/search?is-ajax=1` — Always works ✅
- `/ka/document/tree/{id}/{part}` — Always works ✅
- `/ka/document/backReferences/{id}` — Always works ✅
- `/ka/document/comment/{id}/read` — Always works ✅
- `/ka/document/linked/{id}/{anchor}` — Always works ✅
- `/annotation/api/search` — Always works ✅

### Document Text Fallback

`get_document()` auto-falls back to `get_text_via_linked()` when the HTML page is blocked. It fetches `/ka/document/linked/{id}/document` (never blocked), which returns the whole current text; only if that is empty does it walk tree anchors one by one. The fallback has no historical versions — `?publication=N` exists only on the view page.

### Voice Server Anti-Bot
The voice server uses a special anti-bot mechanism:
- Cookie name: `XZCookie` (set on first visit)
- JavaScript reads `document.ZXKey` and sends it as header
- Not easily bypassable without browser execution

## Rate Limiting

No explicit rate limiting observed on API endpoints (5 rapid requests all returned 200).
The wrapper enforces 0.3s delay between requests as a safety measure.

## Pitfalls

- ⚠️ `curl` for `/ka/document/view/{id}` pages is **INTERMITTENTLY blocked** — some IDs work via curl, others return "Access Denied". Use API endpoints instead.
- ❌ `curl` for `/ka/document/recent` is **CONSISTENTLY blocked**
- ❌ matsne homepage sidebar "ცვლილებების მაცნე" shows **OLD content** (2023 and earlier)
- ❌ UI date pickers and `additional_status=normative` parameter are **non-functional**
- ❌ Direct URL search (`/ka/search?text=...`) is **blocked by anti-bot**
- ❌ Search results with `query` parameter return different HTML structure than plain search
- ⚠️ `DocumentPart` in tree can be a single object OR an array — always check type
- ⚠️ Title parsing: some titles contain `<b>` tags (keyword highlighting) — strip HTML
- ⚠️ Speech server uses XZCookie anti-bot — not easily bypassable
- ✅ API endpoints (tree, comments, search, suggest) are **reliably unprotected**
- ✅ `is-ajax=1` parameter converts search results to JSON
- ✅ `limit` parameter works (5, 10, 20, 30, 50, 100)
- ✅ `sort` parameter works (e.g., `publishDate_desc`)

## Workflow Examples

### Daily Scan (Cron Job)
```python
from matsne_api import MatsneAPI
api = MatsneAPI()

# Quick check
dates = api.search_quick()
if not api.has_today():
    print("დღეს რელევანტური ცვლილება არ არის")
    exit()

# Get today's documents
today_docs = api.today_documents()
for doc in today_docs:
    print(f"ID:{doc['id']} | {doc['title']}")
```

### Keyword Monitoring
```python
keywords = ["პერსონალური", "მონაცემ", "ელექტრონული", "შრომის", "საგადასახადო"]
for kw in keywords:
    results = api.suggest(kw)
    active = [r for r in results if r.get("state") == "active"]
    print(f"'{kw}': {len(active)} active laws")
```

### Full Document Analysis
```python
doc = api.get_document(6840140)
print(f"Title: {doc['meta']['title']}")
print(f"Number: {doc['meta']['number']}")
print(f"Organ: {doc['meta']['organ']}")
print(f"Tree: {doc['tree']}")
print(f"Back-refs: {len(doc['back_references'])}")
print(f"Text: {len(doc['text'])} chars")
```

## Files

- **Wrapper:** `matsne_api.py`
- **Court wrapper:** `court_api.py`
- **MCP Server:** `mcp_server.py`
- **Skill:** `SKILL.md`
- **AGENTS.md** — agent instructions (OpenCode / Claude Code)

---

# Court Practice API Reference (`court_api.py`)

Two classes cover Georgia's court practice. **`authority_level`** is attached to every result:

| Level | Authority |
|-------|-----------|
| 5 | Supreme Court **Grand Chamber** (დიდი პალატა) — binding on all common courts |
| 4 | Supreme Court chamber (cassation) |
| 3 | Appeals court (second instance) |
| 2 | First-instance court |

## EcdCourtAPI — ecd.court.ge (common courts)

ASP.NET, all endpoints are **POST + JSON** (`Content-Type: application/json; charset=utf-8`).

| Method | Endpoint | Returns |
|--------|----------|---------|
| `get_instances()` | `/Classifiers/Instances` | `[{Id, Name, Fullname}]` — 1=first, 2=appeals, 3=supreme |
| `get_courts(instance_id)` | `/Classifiers/Courts` | courts filtered by instance |
| `get_case_categories(instance_id)` | `/Classifiers/CaseCategories` | case categories |
| `get_litigation_types(instance_id, case_category_id)` | `/Classifiers/LitigationTypes` | dispute category / article (მუხლი) |
| `get_decision_types(instance_id)` | `/Classifiers/DecisionTypes` | decision types |
| `search_decisions(...)` | `/Decision/DecisionDocuments` | `{total, items}` — full search |
| `get_decision(decision_document_id, instance_id)` | `/Decision/DecisionDocumentText` | `{text, pdf_url}` — full text |
| `verify_act(barcode, case_no)` | `/FinalDocument/FinalDecisionDocument` | court-act verification |

`search_decisions` params: `case_no`, `date_from`/`date_to` (`YYYY-MM-DD`), `instance_id`,
`court_code`, `case_category_id`, `litigation_type_id`, `type_id`, `texts` (list), `skip`, `take`.

Decision dates come back as C# `/Date(1588260918000)/` — parsed to `YYYY-MM-DD` by `_parse_csharp_date`.

## SupremeCourtAPI — supremecourt.ge (Supreme Court)

Laravel-style, endpoints are **GET + HTML** (Georgian params must be URL-encoded).

| Method | Endpoint | Returns |
|--------|----------|---------|
| `search_cases(...)` | `/ka/getCases` | `{total, cases}` — cassation search |
| `get_case(case_id, palata)` | `/ka/fullcase/{id}/{palata}` | `{text, ...}` — full text |
| `grand_chamber(category)` | `/decisions-grand-chamber/{slug}` | `{items: [{title, url}]}` — binding decisions (PDFs) |

`search_cases` params: `palata` (0=ადმინისტრაციული, 1=სამოქალაქო, 2=სისხლის), `page` (1-indexed),
`texts` (list, up to 6 full-text terms), `case_number`, `date_from`/`date_to` (`YYYY/MM/DD`),
`judge`, `result` (see `RESULT_CODES`), `appeal_type` (see `APPEAL_TYPE_CODES`), `category`, `kind`.

`grand_chamber` categories: `civil`, `criminal`, `admin`, `norms`. `norms` returns the single
compiled PDF of binding norm interpretations — the highest authority below the Constitution/law.

---

# Higher Courts API Reference (`higher_courts_api.py`)

Two apex-court sources. `authority_level` continues the hierarchy upward:

| Level | Authority |
|-------|-----------|
| 7 | European Court of Human Rights (ECHR) — supranational, binding on Georgia |
| 6 | Constitutional Court of Georgia — can strike down laws |
| 5 | Supreme Court Grand Chamber |
| 4 | Supreme Court chamber |

## ConstitutionalCourtAPI — constcourt.ge

Laravel + DataTables. Endpoints are **GET + HTML**, results render server-side only when a
search is performed.

| Method | Endpoint | Returns |
|--------|----------|---------|
| `search_acts(...)` | `/ka/judicial-acts` (GET params) | `{items: [{id, title, summary, url}], count}` |
| `get_act(legal_id)` | `/ka/judicial-acts?legal={id}` | `{id, title, text, url}` — full act text |

`search_acts` params: `fullsearch` (name/number/result), `intext` (full-text), `number`,
`name`, `date_from`/`date_to` (site format), `quantity` (10–100), `sort` (`desc`/`asc`).
The wrapper auto-adds `intextsearch=intextsearch` when `intext` is set. Act text lives in
`id="printablePageContent"` (extracted with a balanced-div walk).

## EchrAPI — hudoc.echr.coe.int (HUDOC)

GET + JSON. HUDOC rate-limits aggressively, so the default delay is 0.5s.

| Method | Endpoint | Returns |
|--------|----------|---------|
| `search(...)` | `/app/query/results?query=...&select=...` | `{total, items}` — metadata search |
| `get_case(itemid)` | `/app/conversion/docx/html/body?library=ECHR&id={itemid}` | `{itemid, text, url}` — full text |

`search` params: `respondent` (state code, e.g. `GEO`), `article` (e.g. `6`, `6-1`, `8`),
`appno` (application number), `text` (free-text keyword), `language` (`ENG`/`FRE`),
`start`/`length` (pagination, `length` max 500), `query` (raw HUDOC query override), and `sort`
(HUDOC sort expression — default `kpdate Descending`, i.e. newest judgments first; pass `sort=""`
for HUDOC's relevance ranking).

**Cloudflare bot management (2026-09-25):** HUDOC is fronted by Cloudflare, which challenges
non-browser-looking clients with `HTTP 403` + `Cf-Mitigated: challenge` — the *same* URL returns
200 to `curl`. A bare browser UA (no `sec-ch-ua*` hints) and `Python-urllib/3.x` are both
challenged, so `EchrAPI` sends a browser-consistent UA + client hints and falls back to a plain
`curl/<version>` UA if a 403 still occurs.

The wrapper builds the mandatory query parts — `contentsitename:ECHR` plus exclusion of press
releases (`doctype:PR`, `HFCOMOLD`, `HECOMOLD`) — so results are real judgments. `itemid`s look
like `001-57574`. Full text is an HTML body with numeric entities — `_html_to_text` unescapes it.

**Rate-limit guidance for agents:** HUDOC returns empty/404 on bursty parallel requests. Always
call the `echr_*` tools **sequentially** (never fire several in one batch), and if a call returns
an empty/error result, pause a few seconds and retry once. This is server-side throttling, not a
wrapper bug — the 0.5s delay only helps for serial calls.


---

# Parliament API Reference (`parliament_api.py`)

info.parliament.ge is an ExtJS single-page app; reverse-engineered from `app.js` + network
traffic (2026-10-29). Plain urllib works (Cloudflare only challenges the HTML shell).

| Method | Endpoint | Returns |
|--------|----------|---------|
| `search_bills(query, bill_type_id, page, limit)` | `GET /law/1/bill?searchItem=&billTypeId=&page=&start=&limit=&sort=` | `{total, bills}` |
| `get_bill(id)` | `GET /law/1/bill/{id}` + `/law/1/bill/{id}/billReview` | bill + `stages` |
| (per stage) | `GET /law/1/billReview/{review_id}/billReviewContent` | files → `/file/1/BillReviewContent/{file_id}` |
| (per stage) | `GET /law/1/billReview/{review_id}/billCommittee` | committees (lead / mandatory opinion) |
| `get_package_bills(package_id)` | `GET /law/1/billPackage/{id}/bill` | bills in one package |
| `bill_types()` | `GET /law/lib/1/billType` | 50 types (1 law, 11 organic, 12 constitutional, 2 resolution) |

- Dates are `DD-MM-YYYY`. Stages: ინიციირებული ვარიანტი → I/II/III მოსმენა (+ დადგენილება) → კანონი;
  `voteY`/`voteN` carry the plenary votes.
- Pitfalls: `limit` max 50; `billTypeId` ignored together with `searchItem` (client-side filter);
  `searchItem` matches package names too (`name_match` flag); sort by `billPackage.registrationDate`.

# Research Helpers (`research.py`)

- `verify_quote(text, quote)` → `{verdict: exact|close|partial|not_found, similarity, matched_text, context}`.
  Normalizes case, whitespace, quote marks (`„“«»`) and dashes; fuzzy search is a sliding
  `difflib.SequenceMatcher` window (coarse step len/5, refined ±step).
- `LegalResearch.verify_citation(quote, source, **ids)` — `source`: `matsne` (law/doc_id, article,
  part, as_of_date), `ecd` (decision_document_id, instance_id), `supreme` (case_id, palata),
  `constitutional` (legal_id), `echr` (itemid).
- `LegalResearch.research_article(article, law, doc_id, as_of_date)` — article + practice:
  constcourt `intext="<law genitive> <phrase>"`, Supreme Court `texts=[phrase, law genitive]` in the
  chamber(s) from `LAW_CONTEXT`, ecd `texts=[full phrase]`. ecd full-text matching is strict
  (few hits); the Supreme Court search is the productive one. Grand Chamber PDFs are not searchable.
- `article_phrase(key)` — Georgian ordinal citation stems: 1 → `პირველი მუხლ`, 2–20 and multiples of
  20 → `მე-N მუხლ`, others → `N-ე მუხლ`, superscripts → `151¹ მუხლ`.

# Supreme Court Analytics (`supreme_practice_api.py`)

| Category key | Source | Items | Authority |
|---|---|---|---|
| `generalizations` | `/get-analitics` cat 29 | 53 (2005–2022) | 4, analytical_guidance, non-binding |
| `generalizations_legacy` | old site (embedded catalog) | 15 (2011–2014) | 4 |
| `recommendations_{civil,admin,criminal,magistrate}` | old site | 5 / 3 / 2 / 2 | 4 |
| `explanations` | old site `/ganmartebebi` | 99 (2010–2021) | 4, case_digest |
| `publications_legacy` | old site `/publications/` | 91 | 4 |
| `research`, `research_publications` | cat 30, 32 | 25, 10 | 1 (4 if title is practice/guidance) |
| `human_rights_center` | cat 10 | 75 | 7 for ECHR material, else 1 |
| `echr_vs_georgia`, `echr_key_judgments` | cat 12, 13 | 152, 25 | 7 (unofficial translation) |
| `international_acts` | cat 23 | 37 | 1 (official text on matsne) |
| `digest_{civil,criminal,admin,disciplinary,qualification}` | `/decisions/...` | 318/188/319/65/17 | 4, decision_collection |
| `journal` | `/journal/ka/issues` | 78 (2006–2026) | 1 |

`POST https://www.supremecourt.ge/get-analitics` with `cat`, `searchT`, `collectin`, `alldoc`, `_token`
(CSRF from the page's inline JS, plus the session cookie). Methods: `list_categories()`,
`list_materials(category, query, year, limit)`, `search_materials(query, categories, limit)`,
`get_material(url)`, `check_available(url)`.

# Administrative Bodies (`agencies_api.py`)

| Class | Source | Coverage |
|---|---|---|
| `TaxDisputeCouncilAPI` | InfoHub type 16 | MoF Dispute Resolution Council, ~4.7k, 2018→, full text |
| `RevenueServiceDisputeAPI` | InfoHub type 15 | RS dispute council orders, ~6.7k, 2018→ |
| `TaxGuidanceAPI` | InfoHub types 2/4/17/1 | public rulings (16), situational guides (~630), tax court decisions (203) |
| `CompetitionAgencyAPI` | gcca.gov.ge `index.php?m=…` | 9 categories, PDFs |
| `PublicDefenderAPI` | ombudsman.ge | recommendations ~276, proposals ~156, general ~72 |
| `ProcurementDisputeAPI` | tenders.procurement.gov.ge dispute engine | ~15k complaints 2011→ |
| `BarEthicsAPI` | gba.ge | ethics decisions (anonymised PDFs), 12 categories |

InfoHub: `GET https://infohubapi.rs.ge/api/documents/search?q=&types=&skip=&take=&searchType=3…`
(header `languageCode: ka`), `/api/documents/{id}/details`, `/api/documents/{id}/export/pdf`.
All items: `authority_level 1`, `kind`, `binding`, `doc_type`.

# Procedural Deadlines (`deadlines.py`)

`DeadlineCalculator().calculate_deadline(procedure, start_date, as_of_date="", extra_holidays=None)`
→ `status` (computed / conservative / not_computed), `end_date`, `steps`, `period.quoted_sentence`,
`url`, `counting_rules`, `holidays_source`, `related_norms`, `version_caveat`, `warning`.
22 procedures (`list_procedures()`): civil appeal/cassation/private complaint/default judgment,
administrative appeal/cassation/lawsuits (individual act, normative act, refusal), GAC complaints,
criminal appeal/cassation/restoration motion, labour dismissal claims, civil limitation periods.
`calculate_custom()` for any other period (flags a mismatch with the cited norm), `holidays(year)`.
Known limits: start-date choice, suspension (CPC 62), ad-hoc government holidays (pass
`extra_holidays`), GAC month periods (`conservative`), Saturday/Sunday assumed as rest days.

# Court State Fee (`state_fee.py`)

`FeeCalculator().calculate_fee(fee_type, claim_value=None, party_type="", magistrate=False,
filing_date="", settlement="", administrative=False)` → `status` (computed / conservative /
needs_input / not_computed), `amount_gel`, `steps`, `quoted_subpoint`, `rule`, `ceilings`, `url`,
`version_date`, `notes`, `related_norms` (CPC 40, 41, 46–49, APC 9), `warning`.
26 fee types (`list_fee_types()`) read from CPC art. 39(1): property claim / appeal / cassation
(percent + minimum, ceiling by party type and instance from 39(3)), non-property, private complaint,
claim security, reopening, annulment, lost case, bill of exchange, uncontested proceedings,
entrepreneurs / squeeze-out, arbitration and mediation applications. `get_fee_rule()` = the norm
only. `settlement`: `at_main_hearing` (half, 49(1)) / `before_main_hearing` (waived, 49(2)).
Known limits: claim value is the user's input (CPC 40–41), exemptions are not applied
automatically, magistrate + ceiling order is ambiguous (higher amount → `conservative`).

# PDF Text (`pdf_text.py`)

`extract_pdf_text(url_or_bytes, max_pages=None, include_pages=False)` → `text, pages, method
(pdfium | pdfium+legacy-font-map), legacy_font_fixed, legacy_fonts, scanned, scanned_pages, note`.
`search_pdf(url_or_bytes, query, max_results=20, context=200, fuzzy=True)` → matches with page
numbers (+ `fuzzy_match` via `research.verify_quote`). `download_pdf()` (40 MB cap, retries,
`%PDF-` check), `legacy_to_unicode()`, `legacy_score()`. Library comparison on 66 real PDFs:
pypdfium2 best (pypdf joins lines on the norms PDF, pdfminer misorders justified text, pymupdf
drops spaces + AGPL).
