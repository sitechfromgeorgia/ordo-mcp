#!/usr/bin/env python3
"""
agencies_api.py — decisions of specialised Georgian bodies (quasi-jurisprudence)
=================================================================================
Published decisions of administrative / quasi-judicial / advisory bodies that
Georgian practitioners cite next to court practice. Zero dependencies (stdlib
only), same style as court_api.py / parliament_api.py; all classes subclass
court_api._BaseAPI (rate-limit >= 0.3 s, cookies, retries).

Reverse-engineered 2026-10-29.

  Body                                        Class                        Source
  ------------------------------------------  ---------------------------  ------------------------------
  MoF Dispute Resolution Council              TaxDisputeCouncilAPI         infohubapi.rs.ge  (JSON API)
    (ფინანსთა სამინისტროსთან არსებული დავების განხილვის საბჭო) — 4.7k decisions, 2018→
  Revenue Service dispute decisions           RevenueServiceDisputeAPI     infohubapi.rs.ge  (JSON API)
    (შემოსავლების სამსახურის დავების განხილვის საბჭო) — 6.7k orders, 2018→
  MoF public tax rulings + RS situational     TaxGuidanceAPI               infohubapi.rs.ge  (JSON API)
    guides (საჯარო გადაწყვეტილება / სიტუაციური სახელმძღვანელო)
  Competition & Consumer Protection Agency    CompetitionAgencyAPI         gcca.gov.ge       (HTML + PDF)
  Public Defender (Ombudsman)                 PublicDefenderAPI            ombudsman.ge      (HTML + PDF)
  State Procurement Dispute Resolution Council ProcurementDisputeAPI       tenders.procurement.gov.ge/dispute
  Georgian Bar Association Ethics Commission  BarEthicsAPI                 gba.ge            (HTML + PDF)

Not implemented (see report): pdps.ge (Personal Data Protection Service —
host refuses connections), competition.ge / personaldata.ge (parked domains),
comcom.ge (GNCC — 18.7k mostly licensing decisions, server ignores filters),
rs.ge advance rulings (InfoHub type 3/791 is not public).

InfoHub endpoints (GET, JSON; header ``languageCode: ka`` is REQUIRED —
without it the API answers "Value cannot be null."):
  /api/documents/search?q=&types=&skip=&take=(<99)&...   — search
  /api/documents/search/types?...                        — type tree with counts
  /api/documents/{id}/details                            — full document (HTML)
  /api/documents/{uniqueKey}/details-by-key              — same, by GUID
  /api/documents/{id}/export/pdf                         — rendered PDF
  /api/documents/{id}/files/{file}/download              — attached file
  (/api/documents/{id} itself is 401 — use /details)

Authority representation
------------------------
All items carry ``authority_level = 1`` (AUTHORITY_ADMIN_BODY): below
first-instance courts (2) because every one of these decisions is reviewable by
the common courts, yet above plain doctrine. ``kind`` tells the agent *what*
sort of body spoke — "administrative_body" (state agencies & dispute councils;
binding on the parties / the agency), "advisory_body" (Public Defender —
recommendations, not binding) or "self_regulatory_body" (Bar ethics). ``binding``
is a short human-readable qualifier and ``doc_type`` the concrete act type.

Usage:
    from agencies_api import TaxDisputeCouncilAPI
    mof = TaxDisputeCouncilAPI()
    r = mof.search("დღგ ჩათვლა", page=1)
    d = mof.get_decision(r["items"][0]["id"])
"""

import html as _html_lib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

from court_api import _BaseAPI, _html_to_text

AUTHORITY_ADMIN_BODY = 1

_KA_MONTHS = {
    "იანვარი": 1, "თებერვალი": 2, "მარტი": 3, "აპრილი": 4, "მაისი": 5, "ივნისი": 6,
    "ივლისი": 7, "აგვისტო": 8, "სექტემბერი": 9, "ოქტომბერი": 10, "ნოემბერი": 11,
    "დეკემბერი": 12,
}


def _clean(fragment: str) -> str:
    """HTML fragment → single-line text."""
    if not fragment:
        return ""
    text = _html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _norm_date(value: str) -> str:
    """Many Georgian date shapes → 'YYYY-MM-DD' (returns input if unknown)."""
    if not value:
        return ""
    v = _clean(value).replace(",", " ")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", v)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    parts = v.split()
    day = year = month = None
    for p in parts:
        if p in _KA_MONTHS:
            month = _KA_MONTHS[p]
        elif p.isdigit() and len(p) == 4:
            year = int(p)
        elif p.isdigit():
            day = int(p)
    if day and month and year:
        return f"{year}-{month:02d}-{day:02d}"
    return v


def _to_ddmmyyyy(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else (iso or "")


class _AgencyBase(_BaseAPI):
    """_BaseAPI + form-POST / custom headers (the shared _request only does JSON)."""

    BASE = ""
    BODY = ""              # human-readable body name (Georgian)
    KIND = "administrative_body"
    BINDING = ""

    def _fetch(self, url: str, data: Optional[dict] = None,
               headers: Optional[dict] = None, retries: int = 2) -> str:
        self._wait_rate_limit()
        hdrs = {
            "User-Agent": self.UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "ka,en;q=0.9",
        }
        hdrs.update(self.EXTRA_HEADERS)
        if headers:
            hdrs.update(headers)
        if self._cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, data=body, headers=hdrs,
                                             method="POST" if body is not None else "GET")
                with urllib.request.urlopen(req, timeout=60) as resp:
                    for header in resp.headers.get_all("Set-Cookie") or []:
                        m = re.match(r"([^=]+)=([^;]+)", header.strip())
                        if m:
                            self._cookies[m.group(1)] = m.group(2)
                    return resp.read().decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001 — mirror _BaseAPI behaviour
                if attempt < retries:
                    time.sleep(1 * (attempt + 1))
                    continue
                return f"ERROR: {e}"

    def _item(self, **kw) -> dict:
        item = {
            "id": None, "title": "", "date": "", "number": "", "url": "",
            "pdf_url": None, "body": self.BODY,
            "authority_level": AUTHORITY_ADMIN_BODY, "kind": self.KIND,
            "binding": self.BINDING, "doc_type": "",
        }
        item.update(kw)
        return item

    @staticmethod
    def _abs(base: str, href: str) -> str:
        return urllib.parse.urljoin(base, _html_lib.unescape(href or ""))


# ══════════════════════════════════════════════════════════════
#  InfoHub (Revenue Service knowledge hub) — tax disputes & guidance
# ══════════════════════════════════════════════════════════════

class _InfoHubAPI(_AgencyBase):
    """infohub.rs.ge — Revenue Service 'Information & Methodology Hub' JSON API."""

    BASE = "https://infohubapi.rs.ge/api"
    SITE = "https://infohub.rs.ge/ka"
    EXTRA_HEADERS = {"languageCode": "ka"}  # mandatory
    TAKE_MAX = 98                            # server: take must be < 99

    TYPES = {}             # name → InfoHub document-type id
    DEFAULT_TYPE = None
    ALL_SUBTYPES = False   # search nested sub-types (situational guides are nested)

    # documentDecisionContent ids (outcome of a dispute)
    OUTCOMES = {
        1: "დაკმაყოფილდა",               # upheld
        2: "არ დაკმაყოფილდა",            # rejected
        3: "ნაწილობრივ დაკმაყოფილდა",    # partly upheld
        4: "განუხილველად დარჩა",         # left without consideration
        5: "საჩივრის თაობაზე",
    }

    def _api(self, path: str, params: Optional[dict] = None):
        url = f"{self.BASE}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        raw = self._fetch(url, headers={"Accept": "application/json"})
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def _doc_type_name(self, d: dict) -> str:
        return (d.get("type") or {}).get("name") or ""

    def _normalize(self, d: dict) -> dict:
        key = d.get("uniqueKey")
        outcome = (d.get("documentDecisionContent") or {}).get("name")
        return self._item(
            id=d.get("id"),
            title=_clean(d.get("name")),
            date=(d.get("receiptDate") or d.get("publishDate") or d.get("createDate") or "")[:10],
            number=d.get("documentNumber") or "",
            url=f"{self.SITE}/workspace/document/{key}" if key else "",
            pdf_url=f"{self.BASE}/documents/{d.get('id')}/export/pdf" if d.get("id") else None,
            doc_type=self._doc_type_name(d),
            summary=_html_to_text(d.get("additionalDescription") or "")[:1500],
            outcome=outcome,
            status=(d.get("status") or {}).get("name"),
            unique_key=key,
        )

    def search(self, query: str = "", page: int = 1, page_size: int = 20,
               number: str = "", date_from: str = "", date_to: str = "",
               outcome: Optional[int] = None, doc_type: Optional[str] = None,
               search_in: str = "all") -> dict:
        """
        Full-text search (title + text), newest first.

        Args:
            query: Georgian free text ('' = browse everything)
            page / page_size: 1-indexed paging (page_size ≤ 98)
            number: exact document number, e.g. '104738/1/2026'
            date_from / date_to: 'YYYY-MM-DD' (decision/receipt date)
            outcome: 1 upheld (rare), 2 rejected, 3 partly upheld, 4 left without
                consideration, 5 "on the complaint" (procedural). Search rows carry
                outcome=None — the value is filled by get_decision()
            doc_type: key of self.TYPES (subclass-specific)
            search_in: 'all' | 'title' | 'text'

        Returns:
            {"total": int, "page": int, "items": [...]}
        """
        type_id = self.TYPES.get(doc_type) if doc_type else self.DEFAULT_TYPE
        if doc_type and type_id is None:
            return {"error": f"unknown doc_type {doc_type!r}; use one of {list(self.TYPES)}"}
        take = max(1, min(int(page_size), self.TAKE_MAX))
        search_type = {"title": "1", "text": "2"}.get(search_in, "3")
        params = {
            "q": query or "", "types": type_id or "", "AISearch": "false",
            "searchInName": "true" if search_type != "2" else "false",
            "searchInText": "true" if search_type != "1" else "false",
            "searchType": search_type, "searchForm": "false",
            "legislativeNormIds": "", "skip": (max(1, int(page)) - 1) * take, "take": take,
            "searchInAllSubTypes": "true" if self.ALL_SUBTYPES else "false",
        }
        if number:
            params["number"] = number
        # 'fromDate/toDate' filter the decision date; receiptDate* the registration date
        if date_from:
            params["fromDate"] = date_from
        if date_to:
            params["toDate"] = date_to
        if outcome:
            params["decisionContentId"] = outcome
        res = self._api("documents/search", params)
        if "error" in res:
            return res
        return {"total": (res.get("meta") or {}).get("total", 0), "page": int(page),
                "items": [self._normalize(d) for d in res.get("data") or []]}

    def get_decision(self, id_or_url) -> dict:
        """
        Full decision text.

        Args:
            id_or_url: numeric id, uniqueKey GUID, or an infohub.rs.ge URL

        Returns:
            normalized item + {"text", "html", "summary", "related" (dispute
            chain: earlier/later stages), "attachments", "legal_norms"}
        """
        s = str(id_or_url).strip()
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s)
        if m:
            d = self._api(f"documents/{m.group(0)}/details-by-key")
        elif re.fullmatch(r"\d+", s):
            d = self._api(f"documents/{s}/details")
        else:
            return {"error": f"unrecognised InfoHub id/url: {s}"}
        if not isinstance(d, dict) or "error" in d:
            return d if isinstance(d, dict) else {"error": "unexpected response"}
        item = self._normalize(d)
        doc_id = d.get("id")
        attachments = []
        if d.get("mainAttachedDocumentFile"):
            f = d["mainAttachedDocumentFile"]
            attachments.append({"name": f, "url": f"{self.BASE}/documents/{doc_id}/files/"
                                                  f"{urllib.parse.quote(f)}/download"})
        for a in d.get("attachedDocuments") or []:
            name = a.get("path") or a.get("name") or a.get("fileName")
            if name:
                attachments.append({"name": a.get("name") or name,
                                    "url": f"{self.BASE}/documents/{doc_id}/files/"
                                           f"{urllib.parse.quote(name)}/download"})
        related = []
        for link in d.get("taxCustomsDisputeLinks") or []:
            related.append({"title": link.get("name"),
                            "url": (link.get("link") or {}).get("href"),
                            "stage": link.get("taxCustomsDisputeStages")})
        for link in d.get("relatedLinks") or []:
            related.append({"title": link.get("name"),
                            "url": (link.get("link") or {}).get("href") if isinstance(link.get("link"), dict) else link.get("link"),
                            "stage": None})
        item.update({
            "text": _html_to_text(d.get("description") or ""),
            "html": d.get("description") or "",
            "related": related,
            "attachments": attachments,
            "legal_norms": [n.get("name") for n in d.get("legislativeNorms") or [] if isinstance(n, dict)],
            "tags": [t.get("name") for t in d.get("tags") or [] if isinstance(t, dict)],
            "accounting_authorities": [a.get("name") for a in d.get("accountingAuthorities") or []],
        })
        if item["pdf_url"] is None and attachments:
            item["pdf_url"] = attachments[0]["url"]
        return item

    def type_counts(self) -> list:
        """InfoHub document-type tree with public document counts."""
        res = self._api("documents/search/types", {"q": "", "skip": 0, "take": 10})
        if isinstance(res, dict):
            return [res]

        def walk(nodes, depth=0):
            out = []
            for n in nodes:
                out.append({"id": n["id"], "name": n["name"], "key": n.get("key"),
                            "depth": depth,
                            "count": n.get("documentCount", 0) + n.get("nestedDocumentCount", 0)})
                out.extend(walk(n.get("children") or [], depth + 1))
            return out
        return walk(res)


class TaxDisputeCouncilAPI(_InfoHubAPI):
    """Dispute Resolution Council at the Ministry of Finance (2nd-stage tax appeals).

    Taxpayers appeal Revenue Service acts here before (or instead of) court;
    decisions follow a fixed structure: სადავო საკითხი / ფაქტობრივი გარემოებები /
    გადაწყვეტილება / დასაბუთება (+ cited Tax Code articles). ~4.75k decisions,
    2018-07 → present, updated daily. Decision PDFs are rendered on demand.
    """
    BODY = "ფინანსთა სამინისტროსთან არსებული დავების განხილვის საბჭო"
    BINDING = "binding on the Revenue Service; appealable to court"
    TYPES = {"decision": 16}
    DEFAULT_TYPE = 16


class RevenueServiceDisputeAPI(_InfoHubAPI):
    """Revenue Service's own dispute council (1st-stage tax/customs appeals) — ~6.7k orders."""
    BODY = "შემოსავლების სამსახურის დავების განხილვის საბჭო"
    BINDING = "binding on the Revenue Service; appealable to MoF council / court"
    TYPES = {"decision": 15}
    DEFAULT_TYPE = 15


class TaxGuidanceAPI(_InfoHubAPI):
    """Official tax guidance published on InfoHub.

    doc_type:
      'public_decision'   — Minister of Finance public rulings (საჯარო გადაწყვეტილება —
                            official interpretation of the Tax Code), ~16 published
      'situational_guide' — Revenue Service situational guides (სიტუაციური
                            სახელმძღვანელო), ~630 numbered Q&A explanations
      'tax_court_decision'— court decisions in tax cases curated by RS (~200)
      'all_disputes'      — RS + MoF dispute decisions together
    Advance/individual rulings (წინასწარი გადაწყვეტილება) are NOT public on InfoHub.
    """
    BODY = "შემოსავლების სამსახური / ფინანსთა სამინისტრო"
    BINDING = "binding on the tax authority (public rulings); guidance otherwise"
    TYPES = {"public_decision": 2, "situational_guide": 4,
             "tax_court_decision": 17, "all_disputes": 1}
    DEFAULT_TYPE = 2
    ALL_SUBTYPES = True

    def search(self, query: str = "", page: int = 1, page_size: int = 20,
               doc_type: str = "public_decision", **kw) -> dict:
        return super().search(query, page, page_size, doc_type=doc_type, **kw)


# ══════════════════════════════════════════════════════════════
#  Competition and Consumer Protection Agency — gcca.gov.ge
# ══════════════════════════════════════════════════════════════

class CompetitionAgencyAPI(_AgencyBase):
    """Georgian Competition and Consumer Protection Agency (former competition.ge).

    PHP site; each category page lists 10 decisions (title, date, summary,
    PDF). The site search does not cover decisions, so `query` is matched
    locally against title+summary after crawling the category (≈10 pages max).
    """

    BASE = "https://gcca.gov.ge"
    BODY = "საქართველოს კონკურენციისა და მომხმარებლის დაცვის სააგენტო"
    BINDING = "binding administrative act; appealable to court"

    # category → (m, cat_id, Georgian name)
    CATEGORIES = {
        "dominance": ("352", "76", "დომინანტური მდგომარეობის ბოროტად გამოყენება"),
        "agreements": ("353", "76", "კონკურენციის შემზღუდველი შეთანხმებები"),
        "state_bodies": ("354", "76", "სახელმწიფო უწყებების მხრიდან კონკურენციის შეზღუდვა"),
        "unfair_competition": ("355", "76", "არაკეთილსინდისიერი კონკურენცია"),
        "mergers": ("356", None, "კონცენტრაციების კონტროლი"),
        "state_aid": ("357", None, "სახელმწიფო დახმარება"),
        "consumer": ("379", "103", "მომხმარებლის უფლებები — გადაწყვეტილება"),
        "market_monitoring": ("358", None, "ბაზრის მონიტორინგი"),
        "antidumping": ("362", None, "ანტიდემპინგი — სააგენტოს დასკვნა"),
    }
    COMPETITION = ("dominance", "agreements", "state_bodies", "unfair_competition",
                   "mergers", "state_aid")
    PAGE_SIZE = 10
    MAX_PAGES = 15

    def __init__(self, rate_limit: float = 0.3):
        super().__init__(rate_limit)
        self._cache = {}   # (category, page) → items

    def _page(self, category: str, page: int) -> list:
        key = (category, page)
        if key in self._cache:
            return self._cache[key]
        m, cat_id, name = self.CATEGORIES[category]
        params = {"m": m}
        if cat_id:
            params["cat_id"] = cat_id
        if page > 1:
            params["page"] = page
        url = f"{self.BASE}/index.php?" + urllib.parse.urlencode(params)
        raw = self._fetch(url)
        if raw.startswith("ERROR"):
            return [{"error": raw}]
        items = []
        for block in re.split(r'<div class="page_info', raw)[1:]:
            pdf = re.search(r'href="([^"]+\.(?:pdf|docx?|PDF))"', block)
            if not pdf:
                continue
            title = re.search(r'class="sm_title[^"]*">(.*?)</div>', block, re.S)
            date = re.search(r'date__container">(.*?)</div>', block, re.S)
            if not title:  # consumer layout: <div class="date_item"><p>title</p><p>date</p>
                ps = re.search(r'class="date_item[^"]*"[^>]*>\s*<p>(.*?)</p>\s*<p>(.*?)</p>', block, re.S)
                if ps:
                    title, date = ps.group(1), ps.group(2)
            else:
                title, date = title.group(1), date.group(1) if date else ""
            summary = re.search(r'class="sm_txt">(.*?)</div>', block, re.S)
            pdf_url = self._abs(f"{self.BASE}/index.php", pdf.group(1))
            stem = re.sub(r"\.\w+$", "", pdf_url.rsplit("/", 1)[-1])
            items.append(self._item(
                id=f"{category}:{stem}", title=_clean(title), date=_norm_date(date or ""),
                url=url, pdf_url=pdf_url, doc_type=name, category=category,
                summary=_clean(summary.group(1)) if summary else "",
            ))
        self._cache[key] = items
        return items

    def search(self, query: str = "", page: int = 1, category: Optional[str] = None,
               page_size: int = 20) -> dict:
        """
        Agency decisions, newest first.

        Args:
            query: text matched (case-insensitive) in title + summary; '' = browse
            page / page_size: paging over the merged result
            category: one of CATEGORIES (dominance, agreements, state_bodies,
                unfair_competition, mergers, state_aid, consumer,
                market_monitoring, antidumping); None = all competition
                categories + consumer decisions

        Returns:
            {"total": int, "page": int, "items": [...]}  (total = matches found
            in the crawled pages)
        """
        if category and category not in self.CATEGORIES:
            return {"error": f"unknown category {category!r}; use one of {list(self.CATEGORIES)}"}
        cats = [category] if category else list(self.COMPETITION) + ["consumer"]
        q = (query or "").lower().strip()
        need = page * page_size
        results = []
        for cat in cats:
            for p in range(1, self.MAX_PAGES + 1):
                items = self._page(cat, p)
                if items and "error" in items[0]:
                    return items[0]
                if not items:
                    break
                for it in items:
                    if not q or q in (it["title"] + " " + it["summary"]).lower():
                        results.append(it)
                if len(items) < self.PAGE_SIZE:
                    break
                # browsing one category: stop as soon as the requested page is filled
                if not q and category and len(results) >= need:
                    break
        # de-duplicate (some decisions are listed in two categories)
        seen, uniq = set(), []
        for it in results:
            if it["pdf_url"] not in seen:
                seen.add(it["pdf_url"])
                uniq.append(it)
        uniq.sort(key=lambda x: x["date"], reverse=True)
        start = (page - 1) * page_size
        return {"total": len(uniq), "page": page, "items": uniq[start:start + page_size]}

    def get_decision(self, id_or_url: str) -> dict:
        """Decision metadata + pdf_url (decisions are PDFs; text extraction is
        pdf_text.py's job). Accepts an id from search() or a PDF URL."""
        s = str(id_or_url)
        cat = s.split(":", 1)[0] if ":" in s and not s.startswith("http") else None
        stem = s.rsplit("/", 1)[-1].split(":")[-1]
        stem = re.sub(r"\.\w+$", "", stem)
        for c in ([cat] if cat in self.CATEGORIES else list(self.CATEGORIES)):
            for p in range(1, self.MAX_PAGES + 1):
                items = self._page(c, p)
                if not items or "error" in items[0]:
                    break
                for it in items:
                    if it["pdf_url"].rsplit("/", 1)[-1].startswith(stem):
                        return dict(it, text=None)
                if len(items) < self.PAGE_SIZE:
                    break
        if s.startswith("http"):
            return self._item(id=s, url=s, pdf_url=s, text=None)
        return {"error": f"decision {s} not found"}


# ══════════════════════════════════════════════════════════════
#  Public Defender (Ombudsman) — ombudsman.ge
# ══════════════════════════════════════════════════════════════

class PublicDefenderAPI(_AgencyBase):
    """Public Defender of Georgia — recommendations, proposals, general proposals.

    Listings are 12 per page (?start=N). Site search needs a session token and
    ignores these sections, so `query` is matched locally on title + teaser.
    Detail pages contain the narrative + attached PDF/DOCX of the act.
    Equality-law (anti-discrimination) recommendations are the most cited.
    """

    BASE = "https://www.ombudsman.ge"
    BODY = "საქართველოს სახალხო დამცველი"
    KIND = "advisory_body"
    BINDING = "recommendation — not binding (addressee must respond within 20 days)"
    PAGE_SIZE = 12

    SECTIONS = {
        "recommendations": ("rekomendatsiebi", "რეკომენდაცია"),
        "proposals": ("tsinadadebebi", "წინადადება"),
        "general_proposals": ("qvela-zogadi-tsinadadeba", "ზოგადი წინადადება"),
    }

    def __init__(self, rate_limit: float = 0.3):
        super().__init__(rate_limit)
        self._cache = {}

    def _page(self, section: str, start: int) -> list:
        key = (section, start)
        if key in self._cache:
            return self._cache[key]
        slug, name = self.SECTIONS[section]
        url = f"{self.BASE}/geo/{slug}" + (f"/?start={start}&date=" if start else "")
        raw = self._fetch(url)
        if raw.startswith("ERROR"):
            return [{"error": raw}]
        items = []
        for m in re.finditer(
                r'<h3 class="news_title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>\s*'
                r'<time[^>]*>(.*?)</time>\s*(?:<div[^>]*class="news_desc">(.*?)</div>)?', raw, re.S):
            href = self._abs(self.BASE + "/", m.group(1))
            items.append(self._item(
                id=href.rstrip("/").rsplit("/", 1)[-1], title=_clean(m.group(2)),
                date=_norm_date(m.group(3)), url=href, doc_type=name, section=section,
                summary=_clean(m.group(4) or ""),
            ))
        self._cache[key] = items
        return items

    def search(self, query: str = "", page: int = 1, section: Optional[str] = None,
               page_size: int = 12, max_pages: int = 30) -> dict:
        """
        Args:
            query: text matched in title + teaser ('' = browse newest)
            section: 'recommendations' | 'proposals' | 'general_proposals' | None (all)
            max_pages: listing pages crawled per section when query is given
                (12 items/page; all ~500 items ≈ 42 requests)

        Returns:
            {"total": matches found, "page": int, "items": [...]}  (pdf_url is
            filled by get_decision)
        """
        if section and section not in self.SECTIONS:
            return {"error": f"unknown section {section!r}; use one of {list(self.SECTIONS)}"}
        sections = [section] if section else list(self.SECTIONS)
        q = (query or "").lower().strip()
        need = page * page_size
        found = []
        for sec in sections:
            limit = max_pages if q else -(-need // self.PAGE_SIZE)
            for i in range(limit):
                items = self._page(sec, i * self.PAGE_SIZE)
                if items and "error" in items[0]:
                    return items[0]
                if not items:
                    break
                found.extend(it for it in items
                             if not q or q in (it["title"] + " " + it["summary"]).lower())
                if len(items) < self.PAGE_SIZE:
                    break
        seen, uniq = set(), []
        for it in found:
            if it["url"] not in seen:
                seen.add(it["url"])
                uniq.append(it)
        uniq.sort(key=lambda x: x["date"], reverse=True)
        start = (page - 1) * page_size
        return {"total": len(uniq), "page": page, "items": uniq[start:start + page_size]}

    def get_decision(self, id_or_url: str) -> dict:
        """Full narrative text + attachments (PDF/DOCX of the recommendation)."""
        s = str(id_or_url).strip()
        if s.startswith("http"):
            url = s
        elif "/" in s:
            url = f"{self.BASE}/geo/{s.strip('/')}"
        else:   # bare slug: section unknown → recommendations path works for most
            url = f"{self.BASE}/geo/rekomendatsiebi/{s}"
        raw = self._fetch(url)
        if raw.startswith("ERROR"):
            return {"error": raw, "url": url}
        title = re.search(r'<div class="page_body">\s*<h3[^>]*>(.*?)</h3>', raw, re.S)
        if not title:
            return {"error": "detail page not found", "url": url}
        date = re.search(r'<div class="page_head">.*?<span[^>]*>(.*?)</span>', raw, re.S)
        content = re.search(r'<div class="page_content">(.*?)</div>\s*(?:<div class="page_media|</article>)',
                            raw, re.S)
        attachments = []
        att = re.search(r'class="page_media page_attachments">(.*?)</ul>', raw, re.S)
        if att:
            for m in re.finditer(r'<a href="([^"]+)"[^>]*class="attachment_title"[^>]*>(.*?)</a>',
                                 att.group(1), re.S):
                attachments.append({"name": _clean(m.group(2)),
                                    "url": self._abs(self.BASE + "/", m.group(1))})
        pdf = next((a["url"] for a in attachments if a["url"].lower().endswith(".pdf")),
                   attachments[0]["url"] if attachments else None)
        section = next((k for k, (slug, _) in self.SECTIONS.items() if f"/geo/{slug}/" in url), None)
        return self._item(
            id=url.rstrip("/").rsplit("/", 1)[-1], title=_clean(title.group(1)),
            date=_norm_date(date.group(1)) if date else "", url=url, pdf_url=pdf,
            doc_type=self.SECTIONS[section][1] if section else "", section=section,
            text=_html_to_text(content.group(1)) if content else "", attachments=attachments,
        )


# ══════════════════════════════════════════════════════════════
#  State procurement Dispute Resolution Council — tenders.procurement.gov.ge
# ══════════════════════════════════════════════════════════════

class ProcurementDisputeAPI(_AgencyBase):
    """Dispute Resolution Council for public procurement (drc.gov.ge front-end;
    data lives in the SPA e-procurement 'dispute' module).

    PHP + session: a POST to engine/controller.php (action=search_app) stores
    the filter in the PHP session, pages are then GET ...&page=N. ~15k
    complaints since 2011 (≈50 per page); each has a structured complaint and,
    once decided, the council decision PDF (via engine/document.php proxy).
    Organisation filters need numeric ids — names are resolved through the
    site's autocomplete (engine/list_org.php).
    """

    BASE = "https://tenders.procurement.gov.ge/dispute"
    BODY = "სახელმწიფო შესყიდვების დავების განხილვის საბჭო"
    BINDING = "binding on the procuring entity; appealable to court"

    STATUSES = {
        40: "დაუშვებლად მიჩნეული",        # inadmissible
        50: "განხილვის პროცესში",         # pending
        60: "საჩივარი დაკმაყოფილდა",      # upheld
        70: "საჩივარი არ დაკმაყოფილდა",   # rejected
        80: "ნაწილობრივ დაკმაყოფილდა",    # partly upheld
        90: "საჩივარი გაუქმებულია",       # withdrawn
        95: "განუხილველი",                # not considered
    }

    def __init__(self, rate_limit: float = 0.3):
        super().__init__(rate_limit)
        self._session_ready = False

    def _xhr(self, params: dict, post: bool = False) -> str:
        if not self._session_ready:
            self._fetch(f"{self.BASE}/")
            self._session_ready = True
        url = f"{self.BASE}/engine/controller.php"
        hdrs = {"X-Requested-With": "XMLHttpRequest"}
        if post:
            return self._fetch(url, data=params, headers=hdrs)
        return self._fetch(url + "?" + urllib.parse.urlencode(params), headers=hdrs)

    def find_organization(self, name: str, role: str = "procuring_entity") -> list:
        """Autocomplete: role 'procuring_entity' (orgtype 3) or 'bidder' (orgtype 1).
        Returns [{"id", "name", "code"}]."""
        if not self._session_ready:
            self._fetch(f"{self.BASE}/")
            self._session_ready = True
        orgtype = 3 if role == "procuring_entity" else 1
        raw = self._fetch(f"{self.BASE}/engine/list_org.php?" + urllib.parse.urlencode(
            {"q": name, "orgtype": orgtype, "limit": 20}))
        if raw.startswith("ERROR"):
            return []
        out = []
        for line in raw.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 2 and parts[0].isdigit():
                out.append({"id": int(parts[0]), "name": parts[1],
                            "code": parts[2] if len(parts) > 2 else ""})
        return out

    def _rows(self, raw: str) -> list:
        items = []
        for m in re.finditer(
                r'<tr class="claim[^"]*" id="A(\d+)A(\d+)">\s*<td[^>]*>\s*<img[^>]*title="(\d+) - ([^"]*)"[^>]*>'
                r'\s*([^<]+?)<br\s*/?>\s*<span[^>]*>([^<]+)</span>\s*</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>',
                raw, re.S):
            app_id, ctype, st, st_name, number, dt, claimant, entity = m.groups()
            claimant = re.sub(r"<!--.*?-->", "", claimant, flags=re.S)
            date = _norm_date(dt.split()[0])
            items.append(self._item(
                id=f"{app_id}:{ctype}", title=f"საჩივარი {number.strip()} — {_clean(claimant)} v. {_clean(entity)}",
                date=date, number=number.strip(),
                url=f"{self.BASE}/engine/controller.php?action=showapp&app_id={app_id}&claim_type_id={ctype}",
                doc_type="საჩივარი / საბჭოს გადაწყვეტილება", status=_clean(st_name),
                status_code=int(st), complainant=_clean(claimant),
                procuring_entity=_clean(entity),
            ))
        return items

    def search(self, query: str = "", page: int = 1, status: Optional[int] = None,
               date_from: str = "", date_to: str = "", procuring_entity: str = "",
               complainant: str = "", tender_number: str = "", complaint_number: str = "",
               simplified: bool = False) -> dict:
        """
        Search complaints / council decisions, newest first (50 per page).

        Args:
            query: smart field — a complaint no. (DIS…/DET…) or tender no.
                (NAT…/SPA…/CON…) is matched exactly; anything else is resolved
                as a procuring-entity name (autocomplete) and, failing that, as
                a complainant name
            status: 60 upheld, 70 rejected, 80 partly upheld, 40 inadmissible,
                50 pending, 90 withdrawn, 95 not considered
            date_from / date_to: 'YYYY-MM-DD' (complaint date)
            procuring_entity / complainant: organisation names (resolved to ids)
            tender_number / complaint_number: exact numbers
            simplified: True → 'agreement/publication' complaints (claim type 2)

        Returns:
            {"total_pages": int, "page": int, "items": [...], "resolved": {...}}
        """
        form = {"action": "search_app", "search": "yez", "app_claim_type_id": 2 if simplified else 1,
                "app_reg_id": complaint_number, "app_prefix": "", "app_spa_no": tender_number,
                "app_claim_id": 0, "org_b": "", "pcustomer_idnumber": "", "app_involved_id": 0,
                "org_c": "", "pinvolved_idnumber": "", "app_shems_id": 0, "org_a": "",
                "pclaimer_idnumber": "", "tender_status": 0, "tender_type": 0, "app_cpv": 0,
                "app_status": status or "", "app_law": "", "app_motxovna": 0,
                "app_date_from": _to_ddmmyyyy(date_from), "app_date_till": _to_ddmmyyyy(date_to),
                "app_pcomp_basis_type": 0, "app_pcomp_basis_id": 0, "app_sort": 1}
        resolved = {}
        q = (query or "").strip()
        if q:
            if re.match(r"(?i)^(DIS|DET)\d", q):
                form["app_reg_id"] = re.sub(r"\(.*\)$", "", q)
            elif re.match(r"(?i)^[A-Z]{2,4}\d{6,}", q):
                form["app_spa_no"] = q
            elif not procuring_entity and not complainant:
                procuring_entity = q
        if procuring_entity:
            orgs = self.find_organization(procuring_entity, "procuring_entity")
            if orgs:
                form["app_shems_id"], resolved["procuring_entity"] = orgs[0]["id"], orgs[0]
            elif q and procuring_entity == q:
                complainant = q
            else:
                return {"error": f"procuring entity not found: {procuring_entity}"}
        if complainant:
            orgs = self.find_organization(complainant, "bidder")
            if not orgs:
                return {"error": f"organisation not found: {complainant}"}
            form["app_claim_id"], resolved["complainant"] = orgs[0]["id"], orgs[0]
        raw = self._xhr(form, post=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        if page > 1:
            raw = self._xhr({"action": "search_app", "page": str(page)})
            if raw.startswith("ERROR"):
                return {"error": raw}
        last = re.search(r"plastpage = eval\('(\d+)'\)", raw)
        return {"total_pages": int(last.group(1)) if last else (1 if "claim" in raw else 0),
                "page": page, "items": self._rows(raw), "resolved": resolved}

    def get_decision(self, id_or_url: str) -> dict:
        """Complaint details + council decision PDF link(s).
        id: '65720:1' (app_id:claim_type) or bare app_id or a showapp URL."""
        s = str(id_or_url)
        m = re.search(r"app_id=(\d+)(?:&claim_type_id=(\d+))?", s) or re.match(r"(\d+)(?::(\d+))?$", s.strip())
        if not m:
            return {"error": f"unrecognised id: {s}"}
        app_id, ctype = m.group(1), m.group(2) or "1"
        raw = self._xhr({"action": "showapp", "app_id": app_id, "claim_type_id": ctype})
        if raw.startswith("ERROR"):
            return {"error": raw}
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
        body = re.sub(r"TinyButStrong Error.*?'noerr'\.", "", _html_lib.unescape(body), flags=re.S)
        text = _html_to_text(body)
        number = re.search(r"საჩივარი #\s*(\S+)", text)
        status = re.search(r"საჩივრის მიმდინარე სტატუსი\n\s*(.+)", text)
        docs = []
        for dm in re.finditer(r'href="(engine/document\.php\?[^"]+)"', raw):
            href = _html_lib.unescape(dm.group(1))
            name = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("save", [""])[0]
            url = f"{self.BASE}/" + href.split("?")[0] + "?" + urllib.parse.urlencode(
                urllib.parse.parse_qs(urllib.parse.urlparse(href).query), doseq=True)
            if url not in [d["url"] for d in docs]:
                docs.append({"name": name, "url": url})
        decision = next((d["url"] for d in docs if "გადაწყვეტილება" in d["name"]), None)
        tender = re.search(r'href="(https?://tenders\.procurement\.gov\.ge/public/\?go=\d+[^"]*)"', raw)
        return self._item(
            id=f"{app_id}:{ctype}", title=f"საჩივარი {number.group(1) if number else app_id}",
            number=number.group(1) if number else "",
            url=f"{self.BASE}/engine/controller.php?action=showapp&app_id={app_id}&claim_type_id={ctype}",
            pdf_url=decision or (docs[0]["url"] if docs else None),
            doc_type="საჩივარი / საბჭოს გადაწყვეტილება",
            status=status.group(1).strip() if status else "", documents=docs,
            tender_url=_html_lib.unescape(tender.group(1)) if tender else None, text=text,
        )


# ══════════════════════════════════════════════════════════════
#  Georgian Bar Association — Ethics Commission decisions (gba.ge)
# ══════════════════════════════════════════════════════════════

class BarEthicsAPI(_AgencyBase):
    """GBA Ethics Commission disciplinary decisions (anonymised PDFs).

    Listing = collegium → outcome category → paginated cards
    (case no. 'საქმე №077/25', date, PDF). Only metadata is in HTML; the
    site's own date/full-text filters are broken (HTTP 500 / always empty),
    so `query` filters case numbers locally.
    """

    BASE = "https://gba.ge"
    BODY = "საქართველოს ადვოკატთა ასოციაციის ეთიკის კომისია"
    KIND = "self_regulatory_body"
    BINDING = "disciplinary sanction on a lawyer; appealable to the Supreme Court"
    ROOT = "ეთიკის_კომისია/გადაწყვეტილებები"

    # category key → (collegium path, outcome path, Georgian label)
    CATEGORIES = {
        "procedural_refusal": ("საპროცესო-კოლეგია", "დისციპლინური-დევნის-აღძვრაზე-უარი",
                               "საპროცესო კოლეგია — დისციპლინური დევნის აღძვრაზე უარი"),
        "procedural_termination": ("საპროცესო-კოლეგია", "წარმოების-შეწყვეტა",
                                   "საპროცესო კოლეგია — წარმოების შეწყვეტა"),
        "review_warning": ("განხილვის-კოლეგია",
                           "ეთიკის_კომისია - გადაწყვეტილებები - განხილვის კოლეგია - გაფრთხილება",
                           "განხილვის კოლეგია — გაფრთხილება"),
        "review_recommendation": ("განხილვის-კოლეგია", "კერძო-სარეკომენდაციო-ბარათი",
                                  "განხილვის კოლეგია — კერძო სარეკომენდაციო ბარათი"),
        "review_acquittal": ("განხილვის-კოლეგია", "პასუხისმგებლობის-დაკისრებაზე-უარი",
                             "განხილვის კოლეგია — პასუხისმგებლობის დაკისრებაზე უარი"),
        "review_termination": ("განხილვის-კოლეგია", "წარმოების-შეწყვეტა",
                               "განხილვის კოლეგია — წარმოების შეწყვეტა"),
        "plenary_warning": ("მთლიანი-შემადგენლობა", "ეთიკის_კომისია - გადაწყვეტილებები - მთლიანი შემადგენლობა-გაფრთხილება", "მთლიანი შემადგენლობა — გაფრთხილება"),
        "plenary_recommendation": ("მთლიანი-შემადგენლობა", "ეთიკის_კომისია - გადაწყვეტილებები - მთლიანი შემადგენლობა-კერძო-სარეკომენდაციო-ბარათი",
                                   "მთლიანი შემადგენლობა — კერძო სარეკომენდაციო ბარათი"),
        "plenary_acquittal": ("მთლიანი-შემადგენლობა", "ეთიკის_კომისია - გადაწყვეტილებები - მთლიანი შემადგენლობა-პასუხისმგებლობის-დაკისრებაზე-უარი",
                              "მთლიანი შემადგენლობა — პასუხისმგებლობის დაკისრებაზე უარი"),
        "plenary_suspension": ("მთლიანი-შემადგენლობა", "ეთიკის_კომისია - გადაწყვეტილებები - მთლიანი შემადგენლობა-საქმიანობის უფლების შეჩერება",
                               "მთლიანი შემადგენლობა — საქმიანობის უფლების შეჩერება"),
        "plenary_disbarment": ("მთლიანი-შემადგენლობა", "ეთიკის_კომისია - გადაწყვეტილებები - მთლიანი შემადგენლობა-საქმიანობის უფლების შეწყვეტა",
                               "მთლიანი შემადგენლობა — საქმიანობის უფლების შეწყვეტა"),
        "plenary_termination": ("მთლიანი-შემადგენლობა", "წარმოების-შეწყვეტა",
                                "მთლიანი შემადგენლობა — წარმოების შეწყვეტა"),
    }
    # categories that are actual sanctions / merits decisions (default search set)
    SANCTIONS = ("review_warning", "review_recommendation", "plenary_warning",
                 "plenary_recommendation", "plenary_suspension", "plenary_disbarment")

    def __init__(self, rate_limit: float = 0.3):
        super().__init__(rate_limit)
        self._cache = {}

    def _url(self, category: str) -> str:
        coll, leaf, _ = self.CATEGORIES[category]
        return f"{self.BASE}/ka/" + urllib.parse.quote(f"{self.ROOT}/{coll}/{leaf}")

    def _page(self, category: str, page: int) -> list:
        key = (category, page)
        if key in self._cache:
            return self._cache[key]
        url = self._url(category) + (f"?page={page}" if page > 1 else "")
        raw = self._fetch(url)
        if raw.startswith("ERROR"):
            return [{"error": raw}]
        items = []
        for m in re.finditer(r'<h1><a href="([^"]+\.pdf)"[^>]*>([^<]+)</a></h1>\s*<div>\s*<img[^>]*>\s*'
                             r'<h5>([^<]+)</h5>', raw):
            pdf = m.group(1)
            num = re.search(r"№\s*([\d/._-]+)", m.group(2))
            items.append(self._item(
                id=re.search(r"/pdf/([^/]+?)\.pdf", pdf).group(1) if "/pdf/" in pdf else pdf,
                title=_clean(m.group(2)), date=_norm_date(m.group(3)),
                number=num.group(1) if num else "", url=self._url(category), pdf_url=pdf,
                doc_type=self.CATEGORIES[category][2], category=category,
            ))
        self._cache[key] = items
        return items

    def search(self, query: str = "", page: int = 1, category: Optional[str] = None,
               max_pages: int = 3) -> dict:
        """
        Args:
            query: case number fragment, e.g. '077/25' ('' = browse)
            page: listing page (8 cards/page) when browsing one category
            category: key of CATEGORIES (None = all sanction categories)
            max_pages: pages crawled per category when query is given

        Returns:
            {"total": int, "page": int, "items": [...]}
        """
        if category and category not in self.CATEGORIES:
            return {"error": f"unknown category {category!r}; use one of {list(self.CATEGORIES)}"}
        cats = [category] if category else list(self.SANCTIONS)
        q = (query or "").strip().replace("_", "/").replace(".", "/")
        out = []
        for cat in cats:
            pages = range(1, max_pages + 1) if q else [page]
            for p in pages:
                items = self._page(cat, p)
                if items and "error" in items[0]:
                    return items[0]
                if not items:
                    break
                out.extend(it for it in items if not q or q in it["number"])
        out.sort(key=lambda x: x["date"], reverse=True)
        return {"total": len(out), "page": page, "items": out}

    def get_decision(self, id_or_url: str) -> dict:
        """Ethics decisions are PDFs only → returns {pdf_url, text: None}."""
        s = str(id_or_url)
        if s.startswith("http"):
            return self._item(id=s, url=s, pdf_url=s, text=None)
        for (cat, p), items in self._cache.items():
            for it in items:
                if it.get("id") == s:
                    return dict(it, text=None)
        return {"error": "unknown id — pass the pdf_url from search()"}


# ══════════════════════════════════════════════════════════════
#  Live test
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    def show(label, res, n=2):
        if "error" in res:
            print(f"  ✗ {label}: {res['error']}")
            return False
        total = res.get("total", res.get("total_pages"))
        print(f"  ✓ {label}: total={total} items={len(res['items'])}")
        for it in res["items"][:n]:
            print(f"      [{it['date']}] {it['number'] or ''} {it['title'][:90]}"
                  f"{'  pdf' if it.get('pdf_url') else ''}")
        return bool(res["items"])

    print("=== MoF Dispute Resolution Council (InfoHub) ===")
    mof = TaxDisputeCouncilAPI()
    r = mof.search("დღგ", page_size=3)
    show("search 'დღგ'", r)
    show("search number", mof.search(number="104738/1/2026"))
    show("2025-01 partly upheld", mof.search(date_from="2025-01-01", date_to="2025-02-01", outcome=3, page_size=3))
    if r.get("items"):
        d = mof.get_decision(r["items"][0]["id"])
        print(f"  ✓ get_decision: {d.get('title')} outcome={d.get('outcome')} "
              f"text={len(d.get('text') or '')} chars, related={len(d.get('related') or [])}, pdf={d.get('pdf_url')}")

    print("\n=== Revenue Service dispute council (InfoHub) ===")
    rsd = RevenueServiceDisputeAPI()
    r = rsd.search("ქონების გადასახადი", page_size=3)
    show("search 'ქონების გადასახადი'", r)
    if r.get("items"):
        d = rsd.get_decision(r["items"][0]["unique_key"])
        print(f"  ✓ get_decision(by GUID): text={len(d.get('text') or '')} chars")

    print("\n=== Tax guidance (InfoHub) ===")
    tg = TaxGuidanceAPI()
    show("public decisions", tg.search(page_size=3))
    r = tg.search("დივიდენდი", doc_type="situational_guide", page_size=3)
    show("situational guides 'დივიდენდი'", r)
    if r.get("items"):
        d = tg.get_decision(r["items"][0]["id"])
        print(f"  ✓ get_decision: attachments={[a['name'] for a in d.get('attachments', [])]} "
              f"text={len(d.get('text') or '')} chars")

    print("\n=== Competition & Consumer Protection Agency ===")
    cc = CompetitionAgencyAPI()
    show("browse mergers", cc.search(category="mergers", page_size=3))
    r = cc.search("ფარმაცევტ")
    show("search 'ფარმაცევტ' (all competition + consumer)", r)
    if r.get("items"):
        print(f"  ✓ get_decision: {cc.get_decision(r['items'][0]['id']).get('pdf_url')}")

    print("\n=== Public Defender ===")
    pd = PublicDefenderAPI()
    show("browse general proposals", pd.search(section="general_proposals", page_size=3))
    r = pd.search("დისკრიმინაცი", section="recommendations", max_pages=4)
    show("recommendations 'დისკრიმინაცი' (4 pages)", r)
    if r.get("items"):
        d = pd.get_decision(r["items"][0]["url"])
        print(f"  ✓ get_decision: text={len(d.get('text') or '')} chars pdf={d.get('pdf_url')}")

    print("\n=== Procurement Dispute Resolution Council ===")
    pr = ProcurementDisputeAPI()
    r = pr.search("თბილისის მერია", status=60)
    show("Tbilisi City Hall, upheld", r)
    show("2015 page 2", pr.search(date_from="2015-01-01", date_to="2015-12-31", page=2))
    if r.get("items"):
        d = pr.get_decision(r["items"][0]["id"])
        print(f"  ✓ get_decision: {d.get('number')} status={d.get('status')} "
              f"docs={len(d.get('documents') or [])} pdf={(d.get('pdf_url') or '')[:90]}")

    print("\n=== Bar Association Ethics Commission ===")
    be = BarEthicsAPI()
    show("plenary disbarments", be.search(category="plenary_disbarment"))
    show("browse warnings p2", be.search(category="review_warning", page=2))
