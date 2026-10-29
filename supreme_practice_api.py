#!/usr/bin/env python3
"""
supreme_practice_api.py — Supreme Court of Georgia analytical materials
=======================================================================
Practice generalizations, recommendations, "important explanations", decision
digests, research, translated ECHR judgments, international acts and the
"Justice and Law" journal — everything the Supreme Court publishes *about*
its practice, as opposed to the individual decisions (see court_api.py).

Zero dependencies (stdlib only). Shares HTTP plumbing with court_api.py.

Where the materials live (verified 2026-10):

  1. NEW SITE ANALYTICS  (www.supremecourt.ge/ka/analytics/*)
     The pages look empty without JS: the list is loaded by
       POST /get-analitics   form: cat=<id>, searchT=<text>, collectin='',
                                   alldoc='', _token=<CSRF from page JS>
     (needs the laravel session cookie from the GET of the page). cat ids:
       29 practice generalizations   (/ka/analytics/erovnuli-sasamartlos-praqtikis-ganzogadeba)
       30 research                   (/ka/analytics/kvlevisa-da-analizis-tsentri, tab 1)
       32 research-centre publications (same page, tab 2)
       10 human-rights centre        (/ka/analytics/adamianis-uflebata-tsentri)
       12 ECHR judgments vs Georgia  (/ka/analytics/saertashoriso-sasamartloebis-gadatsyvetilebebi, tab 1)
       13 important ECHR judgments   (same page, tab 2)
       23 international acts (Georgian translations)
     cat='' returns nothing (no global search); 14 (publishing sector) is empty.

  2. DECISION DIGESTS  (www.supremecourt.ge/decisions/<slug>)
     Static HTML, year paragraphs "2025 წ. კრებული: №1, №2 ..." → PDFs,
     2000–current for civil/criminal/admin, plus disciplinary & qualification.

  3. JOURNAL "მართლმსაჯულება და კანონი"  (www.supremecourt.ge/journal/ka/issues[/N])
     Laravel app; current issue lists articles (author, title, pages, PDF);
     archive issues list whole-issue PDFs back to 2006.

  4. LEGACY OLD SITE (frozen; old.supremecourt.ge now returns empty pages)
     Recommendations, practice generalizations 2011–2014 and the numbered
     "მნიშვნელოვანი განმარტებები" (№1–100; e.g. ganmarteba12.pdf cited from
     matsne's Civil Code) were only on the old site. Their frozen index pages
     (pinned Wayback snapshots, 2022) are embedded below as LEGACY_CATALOG
     (legacy_live=True re-reads them from archive.org); the PDFs themselves are
     STILL LIVE at https://www.supremecourt.ge/files/upload-file/pdf/<name>.pdf.

Authority model (authority_level, higher = stronger; see court_api.py):
   7  ECHR (translations here carry the ECHR judgment's weight; the binding text is
      the original — fetch it via EchrAPI)
   5  Grand Chamber (court_api.SupremeCourtAPI.grand_chamber) — binding
   4  Supreme Court practice-derived ANALYTICAL GUIDANCE: generalizations,
      recommendations, important explanations, decision digests. They restate /
      summarize Supreme Court cassation holdings (level 4) but are NOT binding
      in themselves → every item also carries binding=False and a `kind`.
   1  Doctrine / secondary: research studies, journal articles, unofficial
      translations of international acts (official text: matsne).

Usage:
    from supreme_practice_api import SupremePracticeAPI
    sp = SupremePracticeAPI()
    sp.list_categories()
    sp.list_materials("generalizations")
    sp.search_materials("შრომის")
    sp.get_material("https://www.supremecourt.ge/journal/ka/issues/current")
"""

import html as _html_lib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from court_api import _BaseAPI, _html_to_text

# ── Authority levels ──────────────────────────────────────────
AUTHORITY_ECHR = 7
AUTHORITY_ANALYTICAL = 4      # Supreme-practice-derived guidance (not binding)
AUTHORITY_DOCTRINE = 1        # research, journal, unofficial translations

AUTHORITY_NOTES = {
    "analytical_guidance": "Supreme Court analytical guidance (generalization/recommendation) — "
                           "persuasive, reflects cassation practice, NOT binding. "
                           "Cite the underlying decisions; Grand Chamber (level 5) prevails.",
    "case_digest": "Digest/commentary of a specific Supreme Court cassation ruling — the ruling "
                   "itself is level 4 precedent; the digest is not binding.",
    "decision_collection": "Official periodical collection of Supreme Court decisions (level 4 "
                           "cassation practice, compiled).",
    "echr_translation": "Georgian translation of an ECHR judgment — the judgment binds Georgia "
                        "(level 7), the translation is unofficial; verify against HUDOC.",
    "research": "Analytical research / comparative study — doctrinal, not binding.",
    "journal_article": "Journal article (doctrine) — not binding.",
    "international_act": "Unofficial Georgian translation of an international instrument — "
                         "for force and official text check matsne.gov.ge.",
}

_WWW = "https://www.supremecourt.ge"
_LEGACY_PDF = _WWW + "/files/upload-file/pdf/"
_WAYBACK = "https://web.archive.org/web/{ts}id_/{url}"

# ── Category registry ─────────────────────────────────────────
# source: analytics (POST /get-analitics), decisions (static page),
#         journal, legacy (Wayback snapshot of the old site)
CATEGORIES = {
    # --- practice generalizations & recommendations (the core) ---
    "generalizations": {
        "source": "analytics", "cat": 29,
        "page": "/ka/analytics/erovnuli-sasamartlos-praqtikis-ganzogadeba",
        "title_ka": "ეროვნული სასამართლოს პრაქტიკის განზოგადება",
        "title_en": "National court practice generalizations (thematic, 2005–2022)",
        "kind": "analytical_guidance", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "generalizations_legacy": {
        "source": "legacy",
        "snapshots": [
            ("20220523195533", "https://supremecourt.ge/uniform-court-practice/the-court-practice-of-the-supreme-court-of-georgia-in-2011-2012-and-2013-6-months-period/"),
            ("20220523214210", "https://supremecourt.ge/uniform-court-practice/the-court-practice-of-the-supreme-court-of-georgia-in-2013-second-period/"),
            ("20220523204645", "https://supremecourt.ge/uniform-court-practice/the-court-practice-of-the-supreme-court-of-georgia-in-2014-6-months-period/"),
        ],
        "title_ka": "სასამართლო პრაქტიკის განზოგადება 2011–2014 (ძველი საიტი)",
        "title_en": "Periodic practice generalizations by chamber, 2011 – 2014 H1 (old site)",
        "kind": "analytical_guidance", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "recommendations_civil": {
        "source": "legacy",
        "snapshots": [("20221108101015", "https://www.supremecourt.ge/uniform-court-practice/recommendations-on-civil-cases/")],
        "title_ka": "რეკომენდაციები სამოქალაქო საქმეებზე",
        "title_en": "Recommendations on problematic issues — civil cases (old site)",
        "kind": "analytical_guidance", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "recommendations_admin": {
        "source": "legacy",
        "snapshots": [("20220523203407", "https://supremecourt.ge/uniform-court-practice/recommendations-on-administrative-cases/")],
        "title_ka": "რეკომენდაციები ადმინისტრაციულ საქმეებზე",
        "title_en": "Recommendations on problematic issues — administrative cases (old site)",
        "kind": "analytical_guidance", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "recommendations_criminal": {
        "source": "legacy",
        "snapshots": [("20221129083816", "https://supremecourt.ge/uniform-court-practice/recommendations-on-criminal-cases/")],
        "title_ka": "რეკომენდაციები სისხლის სამართლის საქმეებზე",
        "title_en": "Guiding proposals & recommendations — criminal cases (old site)",
        "kind": "analytical_guidance", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "recommendations_magistrate": {
        "source": "legacy",
        "snapshots": [("20221129073949", "https://supremecourt.ge/uniform-court-practice/recommendations-for-magistrate-judges/")],
        "title_ka": "რეკომენდაციები მაგისტრატი მოსამართლეებისთვის",
        "title_en": "Practical recommendations for magistrate judges (old site)",
        "kind": "analytical_guidance", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "explanations": {
        "source": "legacy",
        "snapshots": [("20221203202433", "https://www.supremecourt.ge/ganmartebebi")],
        "title_ka": "მნიშვნელოვანი განმარტებები",
        "title_en": "Important explanations №1–100 — digests of key cassation rulings (old site)",
        "kind": "case_digest", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "publications_legacy": {
        "source": "legacy",
        "snapshots": [("20221130101434", "https://supremecourt.ge/publications/")],
        "title_ka": "გამოცემები (ძველი საიტი)",
        "title_en": "Old-site publications: uniform-practice books, ECHR digests, journal 2009–2022",
        "kind": "analytical_guidance", "authority_level": AUTHORITY_ANALYTICAL,
    },
    # --- research ---
    "research": {
        "source": "analytics", "cat": 30,
        "page": "/ka/analytics/kvlevisa-da-analizis-tsentri",
        "title_ka": "კვლევისა და ანალიზის ცენტრი — კვლევები",
        "title_en": "Research & Analysis Centre — studies (incl. uniform-practice analyses)",
        "kind": "research", "authority_level": AUTHORITY_DOCTRINE,
    },
    "research_publications": {
        "source": "analytics", "cat": 32,
        "page": "/ka/analytics/kvlevisa-da-analizis-tsentri",
        "title_ka": "კვლევისა და ანალიზის ცენტრი — გამოცემები",
        "title_en": "Research & Analysis Centre — publications",
        "kind": "research", "authority_level": AUTHORITY_DOCTRINE,
    },
    "human_rights_center": {
        "source": "analytics", "cat": 10,
        "page": "/ka/analytics/adamianis-uflebata-tsentri",
        "title_ka": "ადამიანის უფლებათა ცენტრი",
        "title_en": "Human Rights Centre — ECHR digests, thematic ECHR case-law studies",
        "kind": "research", "authority_level": AUTHORITY_DOCTRINE,
    },
    # --- international ---
    "echr_vs_georgia": {
        "source": "analytics", "cat": 12,
        "page": "/ka/analytics/saertashoriso-sasamartloebis-gadatsyvetilebebi",
        "title_ka": "საქართველოს წინააღმდეგ (ECHR)",
        "title_en": "ECHR judgments against Georgia — Georgian translations",
        "kind": "echr_translation", "authority_level": AUTHORITY_ECHR,
    },
    "echr_key_judgments": {
        "source": "analytics", "cat": 13,
        "page": "/ka/analytics/saertashoriso-sasamartloebis-gadatsyvetilebebi",
        "title_ka": "მნიშვნელოვანი გადაწყვეტილებები (ECHR)",
        "title_en": "Important ECHR judgments (other states) — Georgian translations",
        "kind": "echr_translation", "authority_level": AUTHORITY_ECHR,
    },
    "international_acts": {
        "source": "analytics", "cat": 23,
        "page": "/ka/analytics/saertashoriso-aqtebi",
        "title_ka": "საერთაშორისო აქტები",
        "title_en": "International acts — Georgian translations (conventions, principles)",
        "kind": "international_act", "authority_level": AUTHORITY_DOCTRINE,
    },
    # --- decision digests (periodical publications) ---
    "digest_civil": {
        "source": "decisions", "page": "/decisions/samoqalaqo-samartlis-saqmeebze",
        "title_ka": "გადაწყვეტილებათა კრებული — სამოქალაქო",
        "title_en": "Decision collections — civil cases (2000–current, monthly PDFs)",
        "kind": "decision_collection", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "digest_criminal": {
        "source": "decisions", "page": "/decisions/siskhlis-samartlis-saqmeebze",
        "title_ka": "გადაწყვეტილებათა კრებული — სისხლის სამართალი",
        "title_en": "Decision collections — criminal cases",
        "kind": "decision_collection", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "digest_admin": {
        "source": "decisions", "page": "/decisions/administratsiuli-samartlis-saqmeebze",
        "title_ka": "გადაწყვეტილებათა კრებული — ადმინისტრაციული",
        "title_en": "Decision collections — administrative cases",
        "kind": "decision_collection", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "digest_disciplinary": {
        "source": "decisions", "page": "/decisions/sadistsiplino-palatis-gadatsyvetilebani",
        "title_ka": "სადისციპლინო პალატის გადაწყვეტილებანი",
        "title_en": "Disciplinary chamber decisions (judges' discipline)",
        "kind": "decision_collection", "authority_level": AUTHORITY_ANALYTICAL,
    },
    "digest_qualification": {
        "source": "decisions", "page": "/decisions/sakvalifikatsio-palatis-gadatsyvetilebani",
        "title_ka": "საკვალიფიკაციო პალატის გადაწყვეტილებანი",
        "title_en": "Qualification chamber decisions",
        "kind": "decision_collection", "authority_level": AUTHORITY_ANALYTICAL,
    },
    # --- journal ---
    "journal": {
        "source": "journal", "page": "/journal/ka/issues",
        "title_ka": "ჟურნალი \"მართლმსაჯულება და კანონი\"",
        "title_en": "\"Justice and Law\" journal (2006–current; articles for new issues)",
        "kind": "journal_article", "authority_level": AUTHORITY_DOCTRINE,
    },
}

# Titles containing these markers are practice-derived even inside research cats
_PRACTICE_MARKERS = ("პრაქტიკ", "განმარტ", "რეკომენდ", "განზოგად")


def _clean(text: str) -> str:
    text = _html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip(" ,;:-– ")


def _norm_url(href: str, base: str = _WWW) -> str:
    """Absolute, percent-encoded URL; legacy old-site / relative PDF paths → live www host."""
    href = _html_lib.unescape(href.strip())
    m = re.search(r"/files/upload-file/pdf/(.+)$", href)
    if m:  # old.supremecourt.ge, http://, relative ../../files/... → live copy on www
        href = _LEGACY_PDF + m.group(1)
    elif href.startswith("//"):
        href = "https:" + href
    elif not href.startswith("http"):
        href = urllib.parse.urljoin(base + "/", href)
    href = re.sub(r"^http://(www\.)?supremecourt\.ge", _WWW, href)
    href = re.sub(r"^https://supremecourt\.ge", _WWW, href)
    return urllib.parse.quote(href, safe=":/?&=%#()+,;'@!$*~")


def _file_type(url: str) -> str:
    m = re.search(r"\.(pdf|docx?|xlsx?|rtf)(?:$|[?#])", url, re.I)
    return m.group(1).lower() if m else "html"


def _years(text: str) -> list:
    return sorted({int(y) for y in re.findall(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)", text)})


def _chamber(title: str) -> str:
    if "სამოქალაქო" in title:
        return "civil"
    if "ადმინისტრაციულ" in title:
        return "admin"
    if "სისხლის" in title:
        return "criminal"
    return ""


class SupremePracticeAPI(_BaseAPI):
    """Supreme Court of Georgia — analytical materials (generalizations, recommendations,
    explanations, digests, research, ECHR translations, international acts, journal)."""

    BASE = _WWW

    def __init__(self, rate_limit: float = 0.4, legacy_live: bool = False):
        """
        Args:
            rate_limit: seconds between requests to supremecourt.ge
            legacy_live: re-read the old-site index pages from the Wayback Machine
                         instead of the embedded frozen catalog (slow; archive.org 429s)
        """
        super().__init__(rate_limit=rate_limit)
        self.legacy_live = legacy_live
        self._token = ""
        self._cache = {}
        self._last_wb = 0.0

    # ── HTTP helpers ─────────────────────────────────────────

    def _refresh_token(self, page: str) -> str:
        html = self._get(page)
        m = re.search(r'token="([^"]+)"', html) or \
            re.search(r'name="csrf-token" content="([^"]+)"', html)
        self._token = m.group(1) if m else ""
        return self._token

    def _post_analytics(self, cat, search: str = "", page: str = "/ka/analytics") -> str:
        """POST /get-analitics (form-encoded, CSRF token + session cookie)."""
        for attempt in range(3):
            if not self._token or attempt:
                self._refresh_token(page)
            data = urllib.parse.urlencode({
                "collectin": "", "alldoc": "", "cat": str(cat),
                "_token": self._token, "searchT": search,
            }).encode("utf-8")
            headers = {
                "User-Agent": self.UA, "Accept": "text/html, */*; q=0.01",
                "Accept-Language": "ka,en;q=0.9", "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": self.BASE + page,
            }
            if self._cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            self._wait_rate_limit()
            try:
                req = urllib.request.Request(self.BASE + "/get-analitics", data=data,
                                             headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    for h in resp.headers.get_all("Set-Cookie") or []:
                        m = re.match(r"([^=]+)=([^;]+)", h.strip())
                        if m:
                            self._cookies[m.group(1)] = m.group(2)
                    return resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                # 405/419 = stale CSRF token/session (Laravel redirects back as GET) → refresh
                if e.code in (405, 419) and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                return f"ERROR: {e}"
            except Exception as e:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                return f"ERROR: {e}"
        return "ERROR: get-analitics failed"

    WAYBACK_DELAY = 1.5   # archive.org returns 429 on bursts

    def _get_wayback(self, ts: str, url: str) -> str:
        """Raw (id_) Wayback snapshot — no cookies sent to archive.org.
        Own, slower pacing + backoff on 429 (archive.org rate-limits hard)."""
        wb = _WAYBACK.format(ts=ts, url=url)
        for attempt in range(4):
            wait = self.WAYBACK_DELAY - (time.time() - self._last_wb)
            if wait > 0:
                time.sleep(wait)
            self._last_wb = time.time()
            try:
                req = urllib.request.Request(wb, headers={"User-Agent": self.UA})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if attempt < 3 and e.code in (429, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                return f"ERROR: {e}"
            except Exception as e:
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                return f"ERROR: {e}"
        return "ERROR: Wayback fetch failed"

    # ── Item builder ─────────────────────────────────────────

    def _item(self, key: str, title: str, url: str, source_page: str, **extra) -> dict:
        meta = CATEGORIES[key]
        kind = meta["kind"]
        level = meta["authority_level"]
        if kind == "research" and any(mk in title for mk in _PRACTICE_MARKERS):
            kind, level = "analytical_guidance", AUTHORITY_ANALYTICAL
        if key == "human_rights_center" and ("ევროპული სასამართლოს" in title or
                                             title.rstrip(" .").endswith("წინააღმდეგ")):
            kind, level = "echr_translation", AUTHORITY_ECHR
        yrs = _years(title + " " + str(extra.get("year_hint", "")))
        extra.pop("year_hint", None)
        item = {
            "title": title,
            "url": url,
            "type": _file_type(url),
            "year": extra.pop("year", None) or (yrs[-1] if yrs else None),
            "category": key,
            "kind": kind,
            "authority_level": level,
            "binding": False,
            "source_page": source_page,
        }
        item.update({k: v for k, v in extra.items() if v not in (None, "")})
        return item

    # ── Parsers ──────────────────────────────────────────────

    def _parse_links(self, key: str, html: str, source_page: str) -> list:
        """Generic <a href=...pdf>title</a> list (analytics & legacy pages).
        Fragments of one title split over several <a> to the same URL are merged."""
        merged = {}
        order = []
        for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
            href = m.group(1)
            if not re.search(r"\.(pdf|docx?)(?:$|[?#])", href, re.I) and \
               "/uploads/files/" not in href and "/upload-file/" not in href:
                continue
            url = _norm_url(href)
            text = _clean(m.group(2))
            if url not in merged:
                merged[url] = ""
                order.append(url)
            if text and text not in merged[url]:
                merged[url] = (merged[url] + " " + text).strip()
        items = []
        for url in order:
            title = merged[url] or urllib.parse.unquote(url.rsplit("/", 1)[-1])
            extra = {}
            num = re.match(r"№\s*(\d+)", title)
            if num:
                extra["number"] = int(num.group(1))
            case = re.search(r"საქმე\s*№?\s*([^)\s][^)]*)\)", title)
            if case:
                extra["case_number"] = case.group(1).strip()
            ch = _chamber(title)
            if ch:
                extra["chamber"] = ch
            items.append(self._item(key, title, url, source_page, **extra))
        return items

    def _parse_generalization_legacy(self, key: str, html: str, source_page: str) -> list:
        """Legacy generalization pages: link text is just the period ('2012'); the
        chamber is in the filename (sisxli/samoqalaqo/admin)."""
        items = self._parse_links(key, html, source_page)
        names = {"sisxl": ("criminal", "სისხლის სამართლის საქმეთა"),
                 "samoq": ("civil", "სამოქალაქო საქმეთა"),
                 "admin": ("admin", "ადმინისტრაციულ საქმეთა")}
        for it in items:
            fname = it["url"].rsplit("/", 1)[-1].lower()
            for stem, (ch, ka) in names.items():
                if stem in fname:
                    it["chamber"] = ch
                    it["title"] = f"{ka} პალატის პრაქტიკის განზოგადება — {it['title']}"
                    break
        return items

    def _parse_decisions_page(self, key: str, html: str, source_page: str) -> list:
        """/decisions/<slug>: <p><strong>2025 წ. კრებული:</strong> <a>№1</a>, ...</p>
        (disciplinary/qualification pages: one <a> per decision, title=case no + date)."""
        meta = CATEGORIES[key]
        body = html
        m = re.search(r'id="nav-tabContent"|გადაწყვეტილებათა კრებული', html)
        if m:
            body = html[m.start():]
        end = body.find("<footer")
        if end > 0:
            body = body[:end]
        items, seen = [], set()
        year = None
        for para in re.split(r"<p[\s>]", body):
            ym = re.search(r"(?<!\d)(19[89]\d|20[0-4]\d)(?:\s|&nbsp;| )*წ", para)
            if ym:
                year = int(ym.group(1))
            for am in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', para, re.S | re.I):
                href, text = am.group(1), _clean(am.group(2))
                if not text or not re.search(r"\w", text):
                    continue  # stray "," links in the CMS markup
                if "/uploads/files/" not in href and "/upload-file/" not in href:
                    continue
                url = _norm_url(href)
                if url in seen:
                    continue
                seen.add(url)
                fy = _years(urllib.parse.unquote(url))
                y = year or (fy[-1] if fy else None)
                if text.startswith("№"):
                    title = f"{meta['title_ka']} {y or ''} {text}".replace("  ", " ")
                else:
                    title = f"{meta['title_ka']}: {text}" + (f" ({y})" if y and str(y) not in text else "")
                num = re.match(r"№\s*(\d+)", text)
                items.append(self._item(key, title.strip(), url, source_page, year=y,
                                        number=int(num.group(1)) if num else None,
                                        chamber=_chamber(meta["title_ka"])))
        return items

    def _parse_journal_issue(self, html: str, issue_url: str) -> dict:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        issue_title = _clean(h1.group(1)) if h1 else ""
        date_m = re.search(r"გამოქვეყნების თარიღი:\s*([\d.]+)", html)
        start = h1.start() if h1 else 0
        end = html.find("არქივში დაბრუნება")
        body = html[start:end if end > 0 else len(html)]
        articles = []
        # modern issues: table rows (author, title, pages, PDF), section header rows
        section = ""
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
        for row in rows:
            sec = re.search(r'<td colspan="3"[^>]*>(.*?)</td>', row, re.S)
            if sec:
                section = _clean(sec.group(1))
                continue
            a = re.search(r'href="([^"]+\.pdf)"', row)
            if not a:
                continue
            ps = [_clean(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", row, re.S)]
            author = ps[0] if len(ps) > 1 else ""
            title = ps[-1] if ps else ""
            pages = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            articles.append({"title": title, "author": author, "section": section,
                             "pages": _clean(pages[1]) if len(pages) > 1 else "",
                             "url": _norm_url(a.group(1))})
        whole = None
        hm = re.search(r"<h2[^>]*>.*?href=\"([^\"]+\.pdf)\"", body, re.S)
        if hm:
            whole = _norm_url(hm.group(1))
        if not articles:
            # archive issues: list of whole-issue PDFs ("№3(2008)", "#1")
            for am in re.finditer(r'<a\s[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', body, re.S | re.I):
                text = _clean(am.group(2))
                if not re.search(r"\w", text):
                    continue
                articles.append({"title": text, "author": "", "section": "", "pages": "",
                                 "url": _norm_url(am.group(1)), "whole_issue": True})
        return {"issue_title": issue_title, "published": date_m.group(1) if date_m else "",
                "url": issue_url, "pdf": whole, "articles": articles}

    # ── Public API ───────────────────────────────────────────

    def list_categories(self) -> dict:
        """All categories with Georgian/English names, kind, authority level and source."""
        cats = []
        for key, m in CATEGORIES.items():
            page = m.get("page") or m["snapshots"][0][1]
            cats.append({
                "key": key, "title_ka": m["title_ka"], "title_en": m["title_en"],
                "kind": m["kind"], "authority_level": m["authority_level"],
                "binding": False, "source": m["source"],
                "source_page": (self.BASE + page) if page.startswith("/") else page,
                "authority_note": AUTHORITY_NOTES.get(m["kind"], ""),
            })
        return {"count": len(cats), "categories": cats,
                "authority_scale": {"7": "ECHR", "5": "Grand Chamber (binding, court_api)",
                                    "4": "Supreme practice-derived analytical guidance (not binding)",
                                    "1": "doctrine / research / unofficial translations"}}

    def list_materials(self, category: str, query: str = "", year: Optional[int] = None,
                       limit: int = 0) -> dict:
        """
        List materials of one category.

        Args:
            category: key from list_categories() (e.g. 'generalizations', 'explanations',
                      'recommendations_civil', 'digest_civil', 'echr_vs_georgia', 'journal')
            query: optional title filter (all words must match; Georgian stems ok)
            year: optional year filter
            limit: max items (0 = all)

        Returns:
            {"category", "title_ka", "title_en", "source_page", "total", "items": [
              {title, url, type, year, category, kind, authority_level, binding,
               source_page, [number, case_number, chamber, author, pages, section]}]}
        """
        meta = CATEGORIES.get(category)
        if not meta:
            return {"error": f"Unknown category: {category}. Use: {list(CATEGORIES)}"}
        if category not in self._cache:
            items = self._fetch_category(category)
            if isinstance(items, dict):  # error
                return items
            self._cache[category] = items
        items = self._cache[category]
        if query:
            items = [it for it in items if self._match(query, it)]
        if year:
            items = [it for it in items if it.get("year") == int(year)
                     or str(year) in it["title"]]
        total = len(items)
        if limit:
            items = items[:limit]
        page = meta.get("page") or meta["snapshots"][0][1]
        return {"category": category, "title_ka": meta["title_ka"],
                "title_en": meta["title_en"],
                "source_page": (self.BASE + page) if page.startswith("/") else page,
                "authority_note": AUTHORITY_NOTES.get(meta["kind"], ""),
                "total": total, "items": items}

    def _fetch_category(self, key: str):
        meta = CATEGORIES[key]
        src = meta["source"]
        if src == "analytics":
            html = self._post_analytics(meta["cat"], page=meta["page"])
            if html.startswith("ERROR"):
                return {"error": html}
            return self._parse_links(key, html, self.BASE + meta["page"])
        if src == "decisions":
            html = self._get(meta["page"])
            if html.startswith("ERROR"):
                return {"error": html}
            return self._parse_decisions_page(key, html, self.BASE + meta["page"])
        if src == "legacy":
            src_page = _WAYBACK.format(ts=meta["snapshots"][0][0], url=meta["snapshots"][0][1])
            if self.legacy_live:
                items, errors = [], []
                for ts, url in meta["snapshots"]:
                    html = self._get_wayback(ts, url)
                    if html.startswith("ERROR"):
                        errors.append(f"{url}: {html}")
                        continue
                    page = _WAYBACK.format(ts=ts, url=url)
                    if key == "generalizations_legacy":
                        items += self._parse_generalization_legacy(key, html, page)
                    else:
                        items += self._parse_links(key, html, page)
                if items and not errors:
                    return items
            # embedded frozen catalog (default, and fallback when Wayback fails)
            items = []
            for path, title in LEGACY_CATALOG.get(key, []):
                url = path if path.startswith("http") else _LEGACY_PDF + path
                extra = {}
                num = re.match(r"№\s*(\d+)", title)
                if num:
                    extra["number"] = int(num.group(1))
                case = re.search(r"საქმე\s*№?\s*([^)\s][^)]*)\)", title)
                if case:
                    extra["case_number"] = case.group(1).strip()
                ch = _chamber(title)
                if ch:
                    extra["chamber"] = ch
                items.append(self._item(key, title, url, src_page, **extra))
            return items
        if src == "journal":
            return self._fetch_journal()
        return {"error": f"bad source {src}"}

    def _fetch_journal(self) -> list:
        index = self._get("/journal/ka/issues")
        if index.startswith("ERROR"):
            return {"error": index}
        issue_urls = sorted(set(re.findall(r'href="(https://www\.supremecourt\.ge/journal/ka/issues/\d+)"',
                                           index)),
                            key=lambda u: -int(u.rsplit("/", 1)[-1]))
        items = []
        for iu in issue_urls:
            html = self._request(iu)
            if html.startswith("ERROR"):
                continue
            issue = self._parse_journal_issue(html, iu)
            iyears = _years(issue["issue_title"])
            if issue["pdf"]:
                items.append(self._item("journal", f"{issue['issue_title']} (სრული ნომერი)",
                                        issue["pdf"], iu, year=iyears[-1] if iyears else None,
                                        date=issue["published"]))
            for a in issue["articles"]:
                if a.get("whole_issue"):
                    y = _years(a["title"]) or _years(urllib.parse.unquote(a["url"])) or iyears
                    title = a["title"]
                    if "მართლმსაჯულება" not in title:
                        title = f"ჟურნალი \"მართლმსაჯულება და კანონი\" {title}"
                    if y and str(y[-1]) not in title:
                        title += f" ({y[-1]})"
                else:
                    title = f"{a['author']} — {a['title']}" if a["author"] else a["title"]
                    y = iyears
                items.append(self._item("journal", title, a["url"], iu,
                                        year=y[-1] if y else None,
                                        author=a["author"], pages=a["pages"],
                                        section=a["section"], date=issue["published"]))
        return items

    @staticmethod
    def _match(query: str, item: dict) -> bool:
        hay = (item["title"] + " " + item.get("case_number", "") + " " +
               urllib.parse.unquote(item["url"].rsplit("/", 1)[-1])).lower()
        for tok in re.findall(r"\w+", query.lower()):
            if tok in hay:
                continue
            # crude Georgian stemming: drop case endings (შრომის → შრომ)
            if len(tok) >= 5 and tok[:max(4, len(tok) - 2)] in hay:
                continue
            return False
        return True

    DEFAULT_SEARCH = ("generalizations", "generalizations_legacy", "recommendations_civil",
                      "recommendations_admin", "recommendations_criminal",
                      "recommendations_magistrate", "explanations", "publications_legacy",
                      "research", "research_publications", "human_rights_center",
                      "echr_vs_georgia", "echr_key_judgments", "international_acts")

    def search_materials(self, query: str, categories: Optional[list] = None,
                         limit: int = 50) -> dict:
        """
        Title search across categories (client-side over cached lists — the site has no
        global search for these materials).

        Args:
            query: words to match in titles (Georgian; case numbers like 'ას-1278' work for
                   'explanations')
            categories: list of category keys; default = all analytical categories except
                        the big decision digests and the journal (pass them explicitly,
                        e.g. ["journal"] or ["digest_civil"])
            limit: max results

        Returns:
            {"query", "total", "searched", "errors", "items": [...]} sorted by
            authority_level desc, then year desc.
        """
        cats = categories or list(self.DEFAULT_SEARCH)
        results, errors = [], {}
        for c in cats:
            r = self.list_materials(c, query=query)
            if "error" in r:
                errors[c] = r["error"]
                continue
            results += r["items"]
        # de-duplicate (same PDF listed in two categories)
        seen, uniq = set(), []
        for it in results:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            uniq.append(it)
        uniq.sort(key=lambda it: (-it["authority_level"], -(it.get("year") or 0)))
        return {"query": query, "total": len(uniq), "searched": cats,
                "errors": errors, "items": uniq[:limit] if limit else uniq}

    def get_material(self, url: str, max_chars: int = 0) -> dict:
        """
        Fetch an HTML material (journal issue page, legacy/analytics page) as text.

        PDFs/DOCs are NOT downloaded here — returns {"type": "pdf", "url": ...,
        "text": None} so the caller can hand the URL to the PDF text extractor
        (pdf_text.py). Journal issue URLs return the parsed table of contents.
        """
        url = _norm_url(url)
        ftype = _file_type(url)
        if ftype != "html":
            return {"url": url, "type": ftype, "text": None,
                    "note": "Binary document — extract text with the PDF tool "
                            "(pdf_text.py). Use check_available(url) to verify it is live."}
        html = self._request(url)
        if html.startswith("ERROR"):
            return {"url": url, "error": html}
        if "/journal/" in url and "/issues" in url:
            issue = self._parse_journal_issue(html, url)
            issue.update({"type": "journal_issue", "kind": "journal_article",
                          "authority_level": AUTHORITY_DOCTRINE, "binding": False})
            return issue
        body = re.sub(r"<(script|style|nav|header|footer)\b.*?</\1>", " ", html,
                      flags=re.S | re.I)
        m = re.search(r'id="(nav-tabContent|content|printablePageContent)"', body)
        if m:
            body = body[body.find(">", m.end()) + 1:]
        title_m = re.search(r"<title>(.*?)</title>", html, re.S)
        text = _html_to_text(body)
        if max_chars:
            text = text[:max_chars]
        return {"url": url, "type": "html",
                "title": _clean(title_m.group(1)) if title_m else "", "text": text}

    def check_available(self, url: str) -> dict:
        """Cheap liveness check (Range: bytes=0-0) — useful for legacy PDFs."""
        url = _norm_url(url)
        self._wait_rate_limit()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.UA,
                                                       "Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                ctype = resp.headers.get("Content-Type", "")
                return {"url": url, "available": True, "status": resp.status,
                        "content_type": ctype}
        except urllib.error.HTTPError as e:
            return {"url": url, "available": False, "status": e.code}
        except Exception as e:
            return {"url": url, "available": False, "error": str(e)}


# ── Embedded legacy catalog ───────────────────────────────────
# Frozen old-site index pages (snapshots listed in CATEGORIES[...]["snapshots"], 2022),
# parsed once and embedded so listing works offline and without archive.org
# (which rate-limits hard — HTTP 429). Entries: (file under /files/upload-file/pdf/
# or absolute URL, title). Refresh with SupremePracticeAPI(legacy_live=True).
LEGACY_CATALOG = {
    'generalizations_legacy': [
        ('20136tveganzogadebasisxli.pdf', 'სისხლის სამართლის საქმეთა პალატის პრაქტიკის განზოგადება — 2013 წლის 6 თვე'),
        ('2012wlisganzogadebasisxli.pdf', 'სისხლის სამართლის საქმეთა პალატის პრაქტიკის განზოგადება — 2012'),
        ('2011wlisganzogadebasisxli.pdf', 'სისხლის სამართლის საქმეთა პალატის პრაქტიკის განზოგადება — 2011'),
        ('samoqalaqo2013ganzogadeba.pdf', 'სამოქალაქო საქმეთა პალატის პრაქტიკის განზოგადება — 2013 წლის 6 თვე'),
        ('samoqalaqo2012ganzogadeba.pdf', 'სამოქალაქო საქმეთა პალატის პრაქტიკის განზოგადება — 2012'),
        ('samoqalaqo2011ganzogadeba.pdf', 'სამოქალაქო საქმეთა პალატის პრაქტიკის განზოგადება — 2011'),
        ('admin2013ganzogadeba.pdf', 'ადმინისტრაციულ საქმეთა პალატის პრაქტიკის განზოგადება — 2013 წლის 6 თვე'),
        ('admin2012ganzogadeba.pdf', 'ადმინისტრაციულ საქმეთა პალატის პრაქტიკის განზოგადება — 2012'),
        ('admin2011ganzogadeba.pdf', 'ადმინისტრაციულ საქმეთა პალატის პრაქტიკის განზოგადება — 2011'),
        ('sisxli-2013-wlis-meore-naxevari.pdf', 'სისხლის სამართლის საქმეთა პალატის პრაქტიკის განზოგადება — 2013 წლის მეორე ნახევარი'),
        ('samoqalaqo-2013-wlis-meore-naxevari.pdf', 'სამოქალაქო საქმეთა პალატის პრაქტიკის განზოგადება — 2013 წლის მეორე ნახევარი'),
        ('administraciuli-2013-wlis-meore-naxevari.pdf', 'ადმინისტრაციულ საქმეთა პალატის პრაქტიკის განზოგადება — 2013 წლის მეორე ნახევარი'),
        ('sisxli-2014-weli-6-tve.pdf', 'სისხლის სამართლის საქმეთა პალატის პრაქტიკის განზოგადება — 2014 წლის 6 თვის'),
        ('samoqalaqo-2014-weli-6-tve.pdf', 'სამოქალაქო საქმეთა პალატის პრაქტიკის განზოგადება — 2014 წლის 6 თვის'),
        ('administr-2014-weli-6-tve.pdf', 'ადმინისტრაციულ საქმეთა პალატის პრაქტიკის განზოგადება — 2014 წლის 6 თვის'),
    ],
    'recommendations_civil': [
        ('rekomend.pdf', 'რეკომენდაციები სამოქალაქო სამართლის სასამართლო პრაქტიკის პრობლემატურ საკითხებზე'),
        ('samoqrek.pdf', 'საქართველოს უზენაესი სასამართლოს ერთგვაროვანი პრაქტიკა სამოქალაქო საქმეებთან დაკავშირებით'),
        ('rekomenda-samoqadmin.pdf', 'სამოქალაქო და ადმინისტრაციული სამართლის სფეროში შემუშავებული რეკომენდაციები და სამოქალაქო სამართლის საკითხებზე საქართველოს უზენაესი სასამართლოს ერთგვაროვანი პრაქტიკა'),
        ('Praqtikulirekomendacebi6-12-2010.pdf', 'პრაქტიკული რეკომენდაციები სამოქალაქო საპროცესო სამართლის საკითხებზე საერთო სასამართლოების მოსამართლეთათვის'),
        ('shexvedrisshejameba.pdf', 'რეკომენდაციები სამშენებლო დავებთან დაკავშირებით'),
    ],
    'recommendations_admin': [
        ('rekadm.pdf', 'რეკომენდაციები ადმინისტრაციული სამართლის სასამართლო პრაქტიკის პრობლემატურ საკითხებზე'),
        ('rekomendaciebi%20administraciuli.pdf', 'რეკომენდაციები ადმინისტრაციული სამართლის სასამართლო პრაქტიკის პრობლემატურ საკითხებზე (2010 წელი, 17 აპრილი'),
        ('rekomenda-samoqadmin.pdf', 'სამოქალაქო და ადმინისტრაციული სამართლის სფეროში შემუშავებული რეკომენდაციები და სამოქალაქო სამართლის საკითხებზე საქართველოს უზენაესი სასამართლოს ერთგვაროვანი პრაქტიკა'),
    ],
    'recommendations_criminal': [
        ('https://www.supremecourt.ge/files/upload-file/doc/614.doc', '2009 წლის 20 იანვარს საქართველოს უზენაესი სასამართლოს სისხლის სამართლის საქმეთა პალატამ ერთგვაროვანი სასამართლო პრაქტიკის ჩამოყალიბებისათვის მნიშვნელოვანი განმარტება გააკეთა'),
        ('reko.pdf', 'სახელმძღვანელო წინადადებები და რეკომენდაციები სისხლის სამართლის სასამაართლო პრაქტიკის პრობლემატურ საკითხებზე'),
    ],
    'recommendations_magistrate': [
        ('rek2008.pdf', 'პრაქტიკული რეკომენდაციები სამოქალაქო საპროცესო სამართლის საკითხებზე მაგისტრატი მოსამართლისათვის'),
        ('magistratebi2.pdf', 'პრაქტიკული რეკომენდაციები სისხლის სამართლის პროცესის ძირითად საკითხებზე მაგისტრატი მოსამართლისათვის'),
    ],
    'explanations': [
        ('ganmarteba1.pdf', '№1 მოტყუებით ქონებრივი დაზიანებისა და თაღლითობის განსხვავება (საქმე №2104აპ-07)'),
        ('ganmarteba2.pdf', '№2 ნარკოტიკული დანაშაულის დამამძიმებელი გარემოებები (საქმე №937აპ-09)'),
        ('ganmarteba3.pdf', '№3 პირადობის მოწმობის გატაცება (საქმე №382აპ-09)'),
        ('ganmarteba4.pdf', '№4 ყალბი დასკვნის გაცემისა და ყალბი დოკუმენტის დამზადების განსხვავება (საქმე №357აპ-11)'),
        ('ganmarteba5.pdf', '№5 დანაშაულის შეუტყობინებლობა (საქმე №175აპ-11)'),
        ('ganmarteba6.pdf', '№6 ახლად გამოვლენილ გარემოებათა გამო გასაჩივრების სუბიექტი (საქმე №1აგ-12)'),
        ('ganmarteba7.pdf', '№7 სიტყვისა და გამოხატვის, აგრეთვე პრესის თავისუფლების მნიშვნელობა (საქმე ას-1278-1298-2011)'),
        ('ganmarteba8.pdf', '№8 კონკრეტული ადმინისტრაციული სამართალდარღვევის სუბიექტთან დაკავშირებით (საქმე №ბს-724-696(კ-08)'),
        ('ganmarteba9.pdf', '№9. არქიტექტურულ-სამშენებლო საქმიანობაში სამართალდარღვევისათვის პასუხისმგებელი სუბიექტის უფლებამონაცვლედ მიჩნევასთან დაკავშირებით (საქმე №ბს-787-761 (კ-10))'),
        ('ganmarteba10.pdf', '№10. მშობიარობის გამო დახმარების გაცემასთან დაკავშირებით (საქმე №ბს-190-187 (კ-11)).'),
        ('ganmarteba11.pdf', '№11. აღმჭურველი აქტის ბათილად გამოცხადებასთან დაკავშირებით (საქმე №ბს-201-195 (კ-09)).'),
        ('ganmarteba12.pdf', '№12. ვალდებულების შესრულების და ხელშეკრულების მოქმედების ვადების გამიჯვნასთან დაკავშირებით (საქმე №ბს-1034-992(კ-09)).'),
        ('ganmarteba13.pdf', '№13. დავის მხარეების იდენტურობასთან დაკავშირებით (საქმე №ბს-482-461(კ-08) და საქმე №ბს-1058-1016(2კს-09)).'),
        ('ganmarteba14.pdf', '№14. სამართალდარღვევის განმეორებითად მიჩნევასთან დაკავშირებით (საქმე №ბს-903-877(კ-10)).'),
        ('ganmarteba15.pdf', '№15. სატრანსპორტო საშუალების ექსპლუატაციის შედეგად დამდგარი ზიანისათვის სატრანსპორტო საშუალების მფლობელის სამოქალაქო-სამართლებრივი პასუხისმგებლობის თაობაზე (საქმე №ას-254-239-2010).'),
        ('ganmarteba16.pdf', '№16. „საცხოვრებელი სადგომით სარგებლობისას წარმოშობილი ურთიერთობის შესახებ“ საქართველოს კანონით გათვალისწინებული მოსარგებლის უფლებასთან დაკავშირებით ამ უკანასკნელის მიერ დაკავებული საცხოვრებელი სადგომის დანგრევის შემთხვევაში (საქმე №ას-266-250-2011).'),
        ('ganmarteba17.pdf', '№17. დივიდენდის სამართლებრივი ბუნების შესახებ (საქმე №ას-1328-1170-2010).'),
        ('ganmarteba18.pdf', '№18. სამკვიდროს მიღების ვადის გაგრძელების საკითხთან დაკავშირებით (საქმე №177-167-2011)'),
        ('ganmarteba19.pdf', '№ 19. მოვალის მიერ ვალდებულების შესრულებისა და კრედიტორის მიერ ამ შესრულების მიღების ფაქტის დადასტურებასთან დაკავშირებული საკითხის თაობაზე (საქმე №1541-1547-2011).'),
        ('ganmarteba20.pdf', '№20. სასამართლოსადმი მიმართვის უფლების შეზღუდვის თაობაზე (საქმე №ა-998-ა-7-2011).'),
        ('ganmarteba21.pdf', '№21. სამოქალაქო საქმის განხილვაში ორდერის საფუძველზე მონაწილე ადვოკატის უფლებამოსილების ფარგლებთან ( მოცულობასთან) დაკავშირებით (საქმე №ას-1614-1608-2011 და საქმე №ას-1269-1289-2011)'),
        ('ganmarteba22.pdf', '№22. ნარკოტიკული საშუალების უკანონო გასაღების ხერხთან დაკავშირებით (იხ. განჩინება №345აპ-11).'),
        ('ganmarteba23.pdf', '№23. ზღვარი თაღლითობისას დანაშაულის მცდელობასა და დამთავრებულ დანაშაულს შორის (იხ. განჩინება №1106აპ-10).'),
        ('ganmarteba24.pdf', '№24. კანონიერ ნდობასთან დაკავშირებით (საქმე №ბს-1153-1142(კ-11)).'),
        ('ganmarteba25.pdf', '№25. დისკრეციულ უფლებასთან დაკავშირებით (საქმე 1655-1627(კ-11)).'),
        ('ganmarteba26.pdf', '№26. სააღსრულებო საფასურის დაკისრებასთან დაკავშირებით (საქმე № ბს-818-812(კ-11)).'),
        ('ganmarteba27.pdf', '№27. ზიანის ანაზღაურების მოთხოვნის უფლების მზღვეველზე გადაცემის ფარგლებთან დაკავშირებით (საქმე Nას-581-549-2011).'),
        ('ganmarteba28.pdf', '№28. დაგირავებული ქონების სახელმწიფოს საკუთრებაში გადასვლისას გირავნობის შენარჩუნებისა და ამ შემთხვევაში გამოსაყენებელი ნორმების (კანონის ანალოგია) თაობაზე (საქმე №ას-743-699-2012)'),
        ('ganmarteba29.pdf', '№29. სესხის ხელშეკრულების ფარგლებში საპროცენტო სარგებლისა და ვალდებულების დარღვევით გამოწვეული ზიანის ანაზღაურების ურთიერთმიმართებასთან დაკავშირებით (საქმე Nას-1701-1685-2011).'),
        ('ganmarteba30.pdf', '№30. თაღლითობისას მნიშვნელოვანი და დიდი ოდენობით ზიანის განსხვავება (საქმე № 236აპ-12)'),
        ('ganmarteba31.pdf', '№31. ახლად გამოვლენილ გარემოებათა გამო განაჩენის გადასინჯვისას დაუშვებელია „ამნისტიის შესახებ“ საქართველოს კანონით გათვალისწინებული შეღავათების გავრცელება/გამოყენება მსჯავრდებულთა მიმართ (საქმე №31აგ-12).'),
        ('ganmarteba32.pdf', '№32. ახლად გამოვლენილ გარემოებათა გამო განაჩენის გადასინჯვისას სასამართლო არ არის უფლებამოსილი იმსჯელოს დანიშნული სასჯელის მსჯავრდებულის პიროვნებასა და მის მიერ ჩადენილი დანაშაულის სიმძიმესთან შესაბამისობის თაობაზე (საქმე №39აგ-12).'),
        ('ganmarteba33.pdf', '№33. საქმის საკასაციო წესით (შესაბამისად სააპელაციო წესით) განხილვისას სამართლებრივად შეუძლებელია ბრალდების მხარის მიერ ბრალდებაზე უარის თქმა (საქმე №370აპ-12).'),
        ('ganmarteba34.pdf', '№34. ახლად გამოვლენილ გარემოებებთან დაკავშირებით საკასაციო საჩივრის განხილვის ფარგლები (საქმე №142აგ-12).'),
        ('ganmarteba35.pdf', '№35. სისხლის სამართლის პროცესში შეურაცხაობის საკითხის დადგენასთან დაკავშირებით (საქმე №3აგ-13)'),
        ('ganmarteba36.pdf', '№36. გამარტივებული წარმოებით საქმის განხილვის თაობაზე საპროცესო დოკუმენტებში აღნიშვნისა და კანონით დადგენილი შემჭიდროებული ვადების დაცვის სავალდებულობის შესახებ (№ას-937-880-2012).'),
        ('ganmarteba37.pdf', '№37. სამოქალაქო კოდექსის 413-ე მუხლით გათვალისწინებული არაქონებრივი ზიანის ანაზღაურების თაობაზე (საქმე Nას-1156-1176-2011).'),
        ('ganmarteba38.pdf', '№38. იპოთეკით უზრუნველყოფილი მოთხოვნის ფარგლებთან დაკავშირებით (საქმე Nას-285-269-2011).'),
        ('ganmarteba39.pdf', '№39. მიწის ქონების გადასახადის დავალიანების გამო ქონებაზე ყადაღის დადების მართლზომიერებასთან დაკავშირებით (საქმე №ბს-1774-1742(კ-11)).'),
        ('ganmarteba40.pdf', '№40. აპელანტის მოწინააღმდეგე მხარის მიერ შესაგებლის წარუდგენლობისას სასამართლოს მიერ შესაფასებელ გარემოებებთან დაკავშირებით (№ას-1532-1435-2012).'),
        ('ganmarteba41.pdf', '№41. ბინათმესაკუთრეთა ამხანაგობის წევრის მფლობელობიდან საერთო საკუთრების გამოთხოვის საფუძვლებისა და ამ საკითხზე ამხანაგობის თავმჯდომარის უფლებამოსილების თაობაზე (საქმე №ას-1098-1029-2012).'),
        ('ganmarteba42.pdf', '№42. მესამე პირის სასარგებლოდ დადებული სიცოცხლის დაზღვევისას რეგრესის წესით ზიანის ანაზღაურებასთან დაკავშირებით (Nას-663-624-2011).'),
        ('ganmarteba43.pdf', '№43. სარკინიგზო გადაზიდვებისას ტვირთის დაყოვნებით გამოწვეული ზიანის ანაზღაურების საკითხის გადასაწყვეტად დასაშვები მტკიცებულებების თაობაზე (საქმე №ას-792-1007-08)'),
        ('ganmarteba44.pdf', '№44. უნებართვო მშენებლობისთვის დაჯარიმებასთან დაკავშირებით (საქმე №ბს-391-386(3კ-12))'),
        ('ganmarteba45.pdf', '№45, საჯარო აუქციონზე შეძენილ უძრავ ქონებაზე საგადასახადო გირავნობის/იპოთეკის უფლების გადასვლასთან დაკავშირებით (საქმე №ბს-34-34(3კ-12)).'),
        ('ganmarteba46.pdf', '№46. მართლზომიერ მფლობელობაში არსებულ მიწის ნაკვეთზე საკუთრების უფლების რეგისტრაციასთან დაკავშირებით (საქმე №ბს-22-22(კ-12)).'),
        ('ganmarteba47.pdf', '№47. იძულებით გადაადგილებული პირის - დევნილის სტატუსის მინიჭებასთან დაკავშირებით (საქმე №ბს-348-345(კ-11)).'),
        ('ganmarteba48.pdf', '№48. საჯარო რეესტრში ყადაღის რეგისტრაციასთან დაკავშირებით (საქმე №ბს-1747-1703(კ-10)).'),
        ('ganmarteba49.pdf', '№49. ორმაგი მოქალაქეობის მქონე პირთათვის დევნილის სტატუსის მინიჭებასთან დაკავშირებით (საქმე №ბს-142-141(კ-12)).'),
        ('ganmarteba50.pdf', '№50. იძულებით გადაადგილებული პირების მიერ საარსებო შემწეობის დანიშვნაზე წერილობითი თანხმობის წარდგენასთან დაკავშირებით (საქმე №ბს-1256-1220(კ-10)).'),
        ('ganmarteba51.pdf', '№51. ,,მუდმივი საცხოვრებელი ადგილის“ დეფინიციასთან დაკავშირებით (საქმე №ბს-1896-1849(კ-10)).'),
        ('bs-367-363.pdf', '№52. მიწის ნაკვეთების საზღვრების გადაფარვასთან დაკავშირებით (საქმე №ბს-367-363(კ-12)).'),
        ('622-610-12.pdf', '№53. ,,სახელმწიფო ვალის შესახებ“ საქართველოს კანონის შესაბამისად სახელმწიფო სავალო ვალდებულებების დაფარვის წესთან დაკავშირებით (საქმე #ბს-622-610(კ-12))'),
        ('ganmarteba54.pdf', '№54. განსჯადობასთან დაკავშირებით (#ბს-425-414(გ-13)).'),
        ('nardobisas-gadaxarjvis-anazgaureba.pdf', '№55. ნარდობის ხელშეკრულებისას გადახარჯვის ანაზღაურების თაობაზე ( № ას -888-834-2012 ).'),
        ('25tebervali-mnishvnelovani-ganmarteba.pdf', '№56. საქართველოს უზენაესი სასამართლოს დიდმა პალატამ 2014 წლის 25 თებერვალს მნიშვნელოვანი განმარტება გააკეთა საუარესოდ შებრუნების საკითხზე (საქმე #227აპ-13)'),
        ('-n.57-mnishvnelovani-ganmarteba.pdf', '№57. უძრავ ნივთზე უფლების საჯარო რეესტრში რეგისტრაციის, როგორც გარიგების ფორმის თაობაზე ( №ას-1529-1443-2012 )'),
        ('-n-58-ganmarteba.pdf', '№58. საჯარო რეესტრში რეგისტრირებული ბოლო ჩანაწერის მნიშვნელობის თაობაზე ( №ას-340-325-2012 )'),
        ('n59-mnishvnelovani-ganmarteba.pdf', '№59. სარჩელში მითითებული ფაქტობრივი გარემოებების სამართლებრივად მოსარჩელისაგან განსხვავებულად შეფასების შესაძლებლობისა და ამ შესაძლებლობის რეალიზების პირობებში ორივე მხარის ინტერესების დაცვის აუცილებლობის თაობაზე ( №ას-1350-1275-2012 )'),
        ('n60-mnishvnelovani-ganmarteba.pdf', '№60. სისხლის სამართლის საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა, რითაც ზღვარი გაავლო ბინის ხელშეუხებლობის დარღვევისა და იძულების დანაშაულებს შორის (იხ. განაჩენი #202აპ-13)'),
        ('n61-mnishvnelovani-ganmarteba.pdf', '№61. საჯარო რეესტრის უტყუარობისა და სისრულის პრეზუმფციაზე, როგორც მესამე პირთა დაცვის საშუალების თაობაზე ( №ას-189-182-2013 )'),
        ('n62-mnishvnelovani-ganmarteba.pdf', '№62. სასამართლო ხარჯების, როგორც დამოუკიდებელი დავის საგნის არსებობის თაობაზე ( №ას-165-158-2013 )'),
        ('n63-mnishvnelovani-ganmarteba.pdf', '№63. სისხლის სამართლის საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა განაჩენთა ერთობლიობისას წინა განაჩენით დანიშნული დამატებითი სასჯელის ბოლო განაჩენისათვის მიმატებასთან მიმართებით (საქმე #100აპ-14)'),
        ('n64mnishvnelovani-ganmarteba.pdf', '№64. სისხლის სამართლის საქმეთა პალატამ 2014 წლის 13 ივნისს მნიშვნელოვანი განმარტება გააკეთა, საქართველოს სსკ-ის 238 1 –ე მუხლით გათვალისწინებული დანაშაულის სუბიექტთან მიმართებით (საქმე №103აპ-14)'),
        ('n65-mnishvnelovani-ganmarteba.pdf', '№65. სისხლის სამართლის საქმეთა პალატამ 2014 წლის 28 ივლისს მნიშვნელოვანი განმარტება გააკეთა საქართველოს სსკ-ის 273-ე მუხლთან მიმართებით ადმინისტრაციული სამართალდარღვევიდან წარმოშობილი პასუხისმგებლობის ჭრილში. (საქმე №134აპ-14)'),
        ('ganmarteba111.pdf', '№66 ორსული საჯარო მოსამსახურის სამსახურიდან განთავისუფლებასთან დაკავშირებით (საქმე №ბს-388-377(კ-13); საქმე №ბს-463-451(კ-13))'),
        ('n67-mnishvnelovani-ganmarteba.pdf', '№67 საქართველოს უზენაესი სასამართლოს პრაქტიკის გამოყენება კიდევ უფრო შედეგიანი ხდება. მოცემული პრაქტიკა შეეხება წინასწარი შეთანხმებით ჯგუფის მიერ ჩადენილ ხულიგნობას.'),
        ('n68-mnishvnelovani-ganmarteba.pdf', '№68. საქართველოს უზენაესი სასამართლოს დიდმა პალატამ 2014 წლის 30 დეკემბერს მნიშვნელოვანი განმარტება გააკეთა სამოქალაქო საპროცესო კოდექსის 259 1 -ე მუხლთან დაკავშირებით. (№ას-1161-1106-2014)'),
        ('n69-mnishvnelovani-ganmarteba.pdf', '№69. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ავტომანქანის, როგორც მოძრავი ნივთის , შეძენისას საკუთრების უფლების წარმოშობისა და უფლებრივი ნაკლის მქონე ნივთზე ნასყიდობის ხელშეკრულების დადების სამართლებრივ შედეგებთან დაკავშირებით (საქმე №ას-658-625-2014 ).'),
        ('n70-mnishvnelovani-ganmarteba.pdf', '№70. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა მტკიცებულებათა შეფასების არა მარტო პროცესუალურ, არამედ მატერიალურსამართლებრივ კრიტერიუმებზე (საქმე№ას-872-830-2013).'),
        ('n71-mnishvnelovani-ganmarteba.pdf', '№71. საქართველოს უზენაესი სასამართლოს ადმინისტრაციულ საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ადმინისტრაციული ორგანოს გადაწყვეტილების გასაჩივრების ვადის ათვლის შესახებ (საქმე #ბს-854-836 (კ-12)).'),
        ('n72-mnishvnelovani-ganmarteba.pdf', '№72. საქართველოს უზენაესი სასამართლოს ადმინისტრაციულმა საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ინტელექტუალური საკუთრების შესახებ დავების განსჯადობასთან დაკავშირებით (საქმე #ბს-424-413(გ-13)).'),
        ('n73-mnishvnelovani-ganmarteba.pdf', '№73. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ვინდიკაციურ მოთხოვნებთან დაკავშირებით (საქმე №ას-899-857-2013).'),
        ('n74-mnishvnelovani-ganmarteba.pdf', '№74. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ელექტროგადამცემი ხაზით სარგებლობისათვის კომპენსაციის განსაზღვრის წესისა და მოცულობის თაობაზე (საქმე №ას-803-762-2013).'),
        ('n75-mnishvnelovani-ganmarteba.pdf', '№75. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა სისხლის სამართლის საქმეზე დაკითხულ მოწმეთა ჩვენებების სამოქალაქო საქმეზე მტკიცებულებად მიჩნევისა და სააპელაციო სასამართლოს მიერ ახალი მტკიცებულებების გამოთხოვის საკითხზე (საქმე №ას-788-748-2013).'),
        ('n76-mnishvnelovani-ganmarteba.pdf', '№76. საქართველოს უზენაესი სასამართლოს სისხლის სამართლის საქმეთა პალატამ 2015 წლის 13 მარტს მნიშვნელოვანი განმარტება გააკეთა, რომლითაც განსაზღვრა ახლად გამოვლენილ გარემოებათა გამო განაჩენის გადასინჯვის ფარგლები (საქმე №83აგ-14).'),
        ('n77-mnishvnelovani-ganmarteba.pdf', '№77. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ხელშეკრულების ბუნდოვანი გამონათქვამების განმარტებასთან დაკავშირებით. (საქმე #ას-1144-1090-2014)'),
        ('n78-mnishvnelovani-ganmarteba.pdf', '№78. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა კომპანიის საგადასახადო ვალდებულებებისათვის კომპანიის პარტნიორებისა და დირექტორის პირადი პასუხისმგებლობის თაობაზე (საქმე #ას-1307-1245-2014 და საქმე #ას-1158-1104-2014).'),
        ('n79-mnishvnelovani-ganmarteba.pdf', '№79. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა საზიარო უფლების გაუქმების სამართლებრივ ასპექტებთან დაკავშირებით (საქმე #ას-1148-1094-2014).'),
        ('n80-mnishvnelovani-ganmarteba.pdf', '№80. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა სამოქალაქო საქმეში პირადი შეხვედრისას განხორციელებული საუბრის ფარული აუდიოჩანაწერის დაუშვებელ მტკიცებულებად მიჩნევის თაობაზე (იხ.№ას-1155-1101-2014).'),
        ('n81-mnishvnelovani-ganmarteba.pdf', '№81. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა კრედიტორისა და მოვალის ურთიერთობებში კეთილსინდისიერებისა და გულისხმიერების მოვალეობის დარღვევის შედეგად კრედიტორის მიერ უფლების ბოროტად გამოყენების თაობაზე (№ას-1338-1376-2014 ).'),
        ('n83(82)-mnishvnelovani-ganmarteba.pdf', '№82. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა სააღსრულებო ფურცლის მოქმედების ნაწილობრივ შეჩერებასთან დაკავშირებით (საქმე №ას-786-743-2015).'),
        ('n82-mnishvnelovani-ganmarteba.pdf', '№83. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ხელშეკრულებიდან გასვლის შემთხვევაში, მიუღებელი შემოსავლის სახით, დამდგარი ზიანის ანაზღაურების დაკისრებისათვის აუცილებელ სამართლებრივ ასპექტებთან დაკავშირებით (საქმე №ას-459-438-2015).'),
        ('n84-mnishvnelovani-ganmarteba.pdf', '№84. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა არაუფლებამოსილი პირის მიმართ ვალდებულების შესრულებასთან დაკავშირებით (საქმე №ას-570-541-2015).'),
        ('n85-mnishvnelovani-ganmarteba.pdf', '№85. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა სასაქონლო ნიშნების გამოყენებასთან დაკავშირებულ დავაში სასაქონლო ნიშანზე განსაკუთრებული უფლების წარმოშობასა და საქონლის კონტრაფაქციულობის დასადასტურებლად მხარეთა შორის მტკიცების ტვირთის გადანაწილებასთან დაკავშირებით, ასევე საქონლისგანადგურების მოთხოვნის დაკმაყოფილების სამართლებრივი წინაპირობების თაობაზე ( №ას- 1285- 1223-201 4).'),
        ('n86-mnishvnelovani-ganmarteba.pdf', '№86. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა საქართველოს სამოქალაქო კოდექსის 995-ე მუხლის დანაწესთან დაკავშირებით (საქმე №ას-711-677-2015).'),
        ('n87-mnishvnelovani-ganmarteba.pdf', '№87. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა საწარმოო ტრავმის შედეგად მარჩენალის დაღუპვით მიყენებული ზიანის ანაზღაურებასთან დაკავშირებით (საქმე №ას-789-746-2015).'),
        ('n88-mnishvnelovani-ganmarteba.pdf', '№88. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა "ბავშვთა საერთაშორისო გატაცების სამოქალაქო ასპექტების შესახებ" 1980 წლის ჰააგის კონვენციის შესაბამისად არასრულწლოვანი ბავშვის გერმანიაში დაბრუნებასთან დაკავშირებით (საქმე №ას-967-916-2015).'),
        ('n89-mnishvnelovani-ganmarteba.pdf', '№89. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ადვოკატის ჰონორარის ანაზღაურების წესთან დაკავშირებით (საქმე №ას-1161-1116-2016).'),
        ('n90-mnishvnelovani-ganmarteba.pdf', '№90. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა სშკ-ის 36-ე მუხლის მე-2 ნაწილის „დ“ ქვეპუნქტით გათვალისწინებული საფუძვლით შრომითი ურთიერთობის შეჩერების შემთხვევაში დასაქმებულის შრომის ანაზღაურების მოთხოვნის უფლებასთან, აგრეთვე სამსახურიდან გათავისუფლების შესახებ ბრძანების გასაჩივრების ვადასთან დაკავშირებით (სუსგ № ას-1210-2018) .'),
        ('n91-mnishvnelovani-ganmarteba.pdf', '№91. საქართველოს უზენაესი სასამართლოს ადმინისტრაციულ საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა საგადასახადო ორგანოს მიერ საგადასახადო ოპერაციის ფორმისა და შინაარსის გათვალისწინებით მისი კვალიფიკაციის შეცვლასთან დაკავშირებით(საქმე №Nბს-854-846(კ-16)).'),
        ('n92-mnishvnelovani-ganmarteba.pdf', '№92. უზენაესი სასამართლოს ადმინისტრაციულ საქმეთა პალატის მიერ განხილული საქმეებიდან მნიშვნელოვანია საიდუმლოებას მიკუთვნებული ინფორმაციის იდენტიფიცირებისა და მისი დაცვის სტანდარტის თაობაზე მიღებულ განჩინებაში ასახული განმარტებები(საქმე №ბს-33-32(კ-16).'),
        ('n93-mnishvnelovani-ganmarteba.pdf', '№93. საქართველოს უზენაესი სასამართლოს ადმინისტრაციულ საქმეთა პალატამ მნიშვნელოვანი განმარტებები გააკეთა ბუნებრივი გაზის დარიცხვის ღირებულების მართლზომიერების შეფასებასთან დაკავშირებით მარეგულირებელი ორგანოს უფლებამოსილებებზე, დარიცხვის მართლზომიერების შემოწმების წესზე, უძრავი ნივთის ახალი მესაკუთრის მიერ დავალიანების ანაზღაურების საკითხებზე(საქმე №Nბს- 682-668 ( კ -14) .'),
        ('n94-mnishvnelovani-ganmarteba.pdf', '№94.საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა საქართველოს სამოქალაქო საპროცესო კოდექსის XXXIV 3 თავით გათვალისწინებული ცალკეული დელიქტით (დანაშაულით) მიყენებული ზიანის ანაზღაურების შესახებ სარჩელის განხილვის გამარტივებულ წესთან დაკავშირებით (საქმე №ას-1322-2018, 04 აპრილი, 2019 წელი).'),
        ('n95-mnishvnelovani-ganmarteba.pdf', '№95. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა კანონის ანალოგიის, კანონის განმარტებისა და შეჯიბრებითობის პრინციპთან დაკავშირებით (საქმე №ას-559-2019, 04 დეკემბერი, 2019 წელი).'),
        ('n97-mnishvnelovani-ganmarteba.pdf', '№97. საქართველოს უზენაესი სასამართლოს ადმინისტრაციულ საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა სამოქალაქო კოდექსის 142-ე მუხლით გათვალისწინებული 10-წლიანი ხანდაზმულობის ვადის გამოყენებაზე (საქმე Nბს-100(კ-20)).'),
        ('n98-mnishvnelovani-ganmarteba.pdf', '№98. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ფულის დეპოზიტზე მიღებისას ნოტარიუსის საზღაურის განსაზღვრის საკითხთან, აგრეთვე, კრედიტორის მიერ დეპოზიტზე განთავსებული ფულის კანონით დადგენილ ვადაში მიუღებლობის შემთხვევაში, ნოტარიუსის ვალდებულებებთან დაკავშირებით (საქმე №ას-468-468-2018).'),
        ('n99-mnishvnelovani-ganmarteba.pdf', '№99. საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა, გეოგრაფიული ადგილის სახელის არსებობა ნიშანში არის თუ არა აუცილებელი წინაპირობა, საქონლის გეოგრაფიულ წარმოშობის თაობაზე სასაქონლო ნიშნის შეცდომაში შემყვანად მისაჩნევად, ასევე სასაქონლო ნიშნის არსებული სახით გამოყენებას საქონლის გეოგრაფიული წარმომავლობის თაობაზე შეცდომაში შეყავს თუ არა მომხმარებელი (საქმე №ას-307-2021).'),
        ('n100-mnishvnelovani-ganmarteba.pdf', '№100. საქართველოს უზენაესი სასამართლოს ადმინისტრაციულ საქმეთა პალატამ მნიშვნელოვანი განმარტება გააკეთა ყადაღადადებული ქონების ღირებულებასა და მეწარმე სუბიექტის საგადასახადო დავალიანებას შორის დისპროპორციის საკითხზე (საქმე Nბს-881(კ-19) .'),
    ],
    'publications_legacy': [
        ('valdebulebiti-samartlis-zogadi-nawili(2016-2021-wlis-i-naxevari).pdf', 'ვალდებულებითი სამართლის ზოგადი ნაწილი საქართველოს უზენაესი სასამართლოს პრაქტიკაში რჩეული მუხლები (2016-2021 წლის I ნახევარი)'),
        ('ucxo-saxelmwifota-sasamartlo-gadawyvetilebata-cnoba-agsruleba-(2018-2021-wlis-i-naxevari).pdf', 'უცხო სახელმწიფოთა სასამართლო გადაწყვეტილებათა ცნობა-აღსრულება საქართველოს უზენაესი სასამართლოს პრაქტიკაში (2018-2021 წლის I ნახევარი)'),
        ('samoqalaqo-davebi-2011-2016.pdf', 'საქართველოს უზენაესი სასამართლოს 2011-2016 წლების განმარტებები სამოქალაქო დავებზე'),
        ('news-biznes-davebi.pdf', 'ბიზნესდავები და სასამართლო პრაქტიკა'),
        ('ertgvarovani-praktika-shromit-samartalshi.pdf', 'საქართველოს უზენაესი სასამართლოს ერთგვაროვანი პრაქტიკა შრომის სამართალში'),
        ('mnishvnelovani-ganmartebebi-2016.pdf', 'უზენაესი სასამართლოს მნიშვნელოვანი განმარტებანი'),
        ('saq,uzenaesi-sasamartlos-samoq.saqmeta-palatis-mier-komerciuli--iuridiuli-pirebis-monawileobit-ganx.saqmeta-analizi.pdf', 'საქართველოს უზენაესი სასამართლოს სამოქალაქო საქმეთა პალატის მიერ კომერციული იურიდიული პირების მონსაწილეობით განხილულ საქმეთა ანალიზი'),
        ('sarchelis-gadawyvetilebis-agsrulebis-uzrunvelyofa.pdf', 'სარჩელის/გადაწყვეტილების აღსრულების უზრუნველყოფა საქართველოს უზენაესი სასამართლოს პრაქტიკაში'),
        ('saproceso-vadebi(2016-2020-i-naxevari).pdf', 'საპროცესო ვადები საქართველოს უზენაესი სასამართლოს პრაქტიკაში (2016-2020 წლის I ნახევარი)'),
        ('ilia-chavchavadze-da-literaturuli-sakutreba.pdf', 'ილია ჭავჭავაძე და ლიტერატურული საკუთრება'),
        ('ertgvarovani-praktika-administraciuli.pdf', 'ერთგვაროვანი პრაქტიკა ადმინისტრაციულ საქმეებზე'),
        ('ertgvarovani-praktika-administraciuli-2016.pdf', 'ერთგვაროვანი პრაქტიკა ადმინისტრაციულ საქმეებზე 2016'),
        ('ertgvarovani-praktika-samoqalaqo-2016.pdf', 'ერთგავროვანი პრაქტიკა სამოქალაქო საქმეებზე 2016'),
        ('ertgvarovani-praktika-samoqalaqo.pdf', 'ერთგავროვანი პრაქტიკა სამოქალაქო საქმეებზე'),
        ('ertgvarovani-praktika-sisxli.pdf', 'ერთგვაროვანი პრაქტიკა სისხლის სამართლის საქმეებზე'),
        ('2016gamocema-vadebi-samoqalaqo.pdf', 'ვადები სამოქალაქო და სამოქალაქო საპროცესო კოდექსების მიხედვით'),
        ('usapuzvlo-gamdidreba.pdf', 'უსაფუძვლო გამდიდრება'),
        ('mtkicebis-tvirti.pdf', 'მტკიცების ტვირთი'),
        ('samsheneblo-davebi-(2012-2017).pdf', 'საქართველოს უზენაესი სასამართლოს ერთგვაროვანი პრაქტიკა სამშენებლო ურთიერთობიდან წარმოშობილ დავებზე (2012-2017 წლები)'),
        ('saxelshekrulebo.pdf', 'საქართველოს უზენაესი სასამართლოს ერთგვაროვანი პრაქტიკა ადმინისტრაციულ ხელშეკრულებებთან დაკავშირებულ დავებზე (2007-2017 წლები)'),
        ('vadebi-2019.pdf', 'ვადები საჯარო სამართალში 2019'),
        ('vadebi-sajaro-samartalshi.pdf', 'ვადები საჯარო სამართალში 2016'),
        ('sasjelis-danishvnisas-gasatvaliswinebeli-garemoebebi.pdf', 'სასჯელის დანიშვნისას გასათვალისწინებელი გარემოებები'),
        ('martlmsajuleba3-2008.pdf', '№ 3(2008)'),
        ('martlmsajuleba2-2008.pdf', '№ 2(2008)'),
        ('martlmsajuleba3-2007.pdf', '№3 (2007)'),
        ('martlmsajuleba2-2007.pdf', '№ 2(2007)'),
        ('martlmsajuleba1-2006.pdf', '№ 1(2006)'),
        ('ABA23.pdf', '№4 (2009)'),
        ('ABA24.pdf', '№1 (2010)'),
        ('ABA26.pdf', '№3 (2010)'),
        ('ABA27.pdf', '№4 (2010)'),
        ('ABA28.pdf', '№1 (2011)'),
        ('ABA29.pdf', '№2 (2011)'),
        ('ABA30.pdf', '№ 3(2011)'),
        ('ABA31.pdf', '№4 (2011)'),
        ('ABA32.pdf', '№1 (2012)'),
        ('ABA33.pdf', '№2 (2012)'),
        ('ABA34.pdf', '№3 (2012)'),
        ('ABA35.pdf', '№4 (2012)'),
        ('ABA36.pdf', '№1 (2013)'),
        ('ABA37.pdf', '№2 (2013)'),
        ('ABA38.pdf', '№3 (2013)'),
        ('ABA39.pdf', '№4 (2013)'),
        ('martmsajuleba-da-kanoni-2014w-n1.pdf', '№1 (2014)'),
        ('martmsajuleba-da-kanoni-2014w-n2.pdf', '№2 (2014)'),
        ('martlmsajuleba-da-kanoni-2014w-n3.pdf', '№3 (2014)'),
        ('martlmsajuleba-da-kanoni-2014w-n4.pdf', '№4 (2014)'),
        ('martlmsajuleba-da-kanoni-2015w-n1.pdf', '№1 (2015)'),
        ('martlmsajuleba-da-kanoni-2015w-n2.pdf', '№ 2/3 (2015)'),
        ('martlmsajuleba-da-kanoni-2015w-n4.pdf', '№4 (2015)'),
        ('martlmsajuleba-da-kanoni-2015w-n5.pdf', '№5 (2015)'),
        ('martlmsajuleba-da-kanoni-2016w-n1.pdf', '№1 (2016)'),
        ('martlmsajuleba-da-kanoni-2016w-n2.pdf', '№2 (2016)'),
        ('martlmsajuleba-da-kanoni-2016w-n3.pdf', '№3 (2016)'),
        ('martlmsajuleba-da-kanoni-2016w-n4.pdf', '№4 (2016)'),
        ('martlmsajuleba-da-kanoni-2017w-n1.pdf', '№1 (2017)'),
        ('martlmsajuleba-da-kanoni-2017w-n2.pdf', '№2 (2017)'),
        ('martlmsajuleba-da-kanoni-2017w-n3.pdf', '№3 (2017)'),
        ('martlmsajuleba-da-kanoni-2017w-n4.pdf', '№4 (2017)'),
        ('martlmsajuleba-da-kanoni-2018w-n1.pdf', '№1 (2018)'),
        ('martlmsajuleba-da-kanoni-2018w-n2.pdf', '№2 (2018)'),
        ('martlmsajuleba-da-kanoni-2018w-n3.pdf', '№3 (2018)'),
        ('martlmsajuleba-da-kanoni-2018w-n4.pdf', '№4 (2018)'),
        ('martlmsajuleba-da-kanoni-2019w-n1.pdf', '№1 (2019)'),
        ('martlmsajuleba-da-kanoni-2019w-n2.pdf', '№2 (2019)'),
        ('martlmsajuleba-da-kanoni-2019w-n3.pdf', '№3 (2019)'),
        ('martlmsajuleba-da-kanoni-2019w-n4.pdf', '№4 (2019)'),
        ('martlmsajuleba-da-kanoni-2020w-n1.pdf', '№1 (2020)'),
        ('martlmsajuleba-da-kanoni-2020w-n2.pdf', '№2 (2020)'),
        ('martlmsajuleba-da-kanoni-2020w-n3.pdf', '№3 (2020)'),
        ('martlmsajuleba-da-kanoni-2020w-n4.pdf', '№4 (2020)'),
        ('martlmsajuleba-da-kanoni-2021w-n1.pdf', '№1 (2021)'),
        ('martlmsajuleba-da-kanoni-2021w-n2.pdf', '№2 (2021)'),
        ('martlmsajuleba-da-kanoni-2021w-n3.pdf', '№3 (2021)'),
        ('martlmsajuleba-da-kanoni-2021w-n4.pdf', '№4 (2021)'),
        ('martlmsajuleba-da-kanoni-2022w-n1.pdf', '№1 (2022)'),
        ('martlmsajuleba-da-kanoni-2022w-n2.pdf', '№2 (2022)'),
        ('martlmsajuleba-da-kanoni-2022w-n3.pdf', '№3 (2022)'),
        ('praqtikuli%20rekomendaciebi.pdf', 'ურთიერთობები სასამართლო სხდომის დარბაზში'),
        ('BROSHURA-adamianis%20uflebata.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს განჩინებათა კრებული საქართველოს საქმეებზე - 2010'),
        ('krebuli-saqartvelos-saqmeebze-2010-2011.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს განჩინებათა კრებული საქართველოს საქმეებზე 2010 - 2011 წლები'),
        ('krebuli-saqartvelos-saqmeebze.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს განჩინებათა კრებული საქართველოს საქმეებზე'),
        ('evropuli-krebuli-2007-2008.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს 2007 - 2008 წლების შერჩეულ განჩინებათა და გადაწყვეტილებათა კრებული'),
        ('evropuli-krebuli-2009.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს 2009 წლის შერჩეულ განჩინებათა და გადაწყვეტილებათა კრებული'),
        ('evropuli-krebuli-2010.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს 2010 წლის შერჩეულ განჩინებათა და გადაწყვეტილებათა კრებული'),
        ('evropuli-krebuli-2012.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს 2012 წლის შერჩეულ განჩინებათა და გადაწყვეტილებათა კრებული'),
        ('tematuri-saziebeli.pdf', 'ადამიანის უფლებათა ევროპული სასამართლოს გადაწყვეტილებების თემატური საძიებელი'),
        ('bavshvidasamartali1-2011.pdf', 'ბავშვი და სამართალი'),
        ('12msajuli.pdf', 'ნაფიც მსაჯულთა სასამართლო'),
        ('dasabuteba.pdf', 'რეკომენდაციები სასამართლოს გადაწყვეტილების შედგენისას გასათვალისწინებელი ძირითადი წესების შესახებ'),
    ],
}


# ── CLI Test ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 60)
    print("SUPREME COURT ANALYTICAL MATERIALS — LIVE TEST")
    print("=" * 60)
    sp = SupremePracticeAPI()

    cats = sp.list_categories()
    print(f"\n[categories] {cats['count']}")
    for c in cats["categories"]:
        print(f"  {c['key']:<28} L{c['authority_level']} {c['kind']:<20} {c['source']}")

    counts = {}
    for key in CATEGORIES:
        r = sp.list_materials(key)
        if "error" in r:
            print(f"\n[{key}] ERROR: {r['error'][:150]}")
            counts[key] = "ERR"
            continue
        ys = [it["year"] for it in r["items"] if it.get("year")]
        counts[key] = r["total"]
        print(f"\n[{key}] {r['total']} items, years {min(ys) if ys else '-'}–{max(ys) if ys else '-'}")
        for it in r["items"][:2]:
            print(f"  [{it['authority_level']}/{it['kind']}] {it['year']} | {it['title'][:80]}")
            print(f"    {it['url'][:120]}")

    print("\n[search] 'შრომის':")
    s = sp.search_materials("შრომის", limit=8)
    print(f"  total {s['total']}, errors {list(s['errors'])}")
    for it in s["items"]:
        print(f"  [{it['authority_level']}] {it['category']:<22} {it['title'][:70]}")

    print("\n[search] explanation by case no 'ას-1278':")
    for it in sp.search_materials("ას-1278", categories=["explanations"])["items"]:
        print(f"  №{it.get('number')} {it.get('case_number')} → {it['url']}")

    print("\n[get_material] journal current issue:")
    j = sp.get_material("https://www.supremecourt.ge/journal/ka/issues/current")
    if "error" not in j:
        print(f"  {j['issue_title']} ({j['published']}), {len(j['articles'])} articles")
        for a in j["articles"][:3]:
            print(f"   - {a['author']}: {a['title'][:60]} [{a['pages']}]")

    print("\n[get_material] PDF pointer + liveness of legacy ganmarteba12.pdf:")
    print(" ", sp.get_material("http://www.supremecourt.ge/files/upload-file/pdf/ganmarteba12.pdf"))
    print(" ", sp.check_available("https://www.supremecourt.ge/files/upload-file/pdf/ganmarteba12.pdf"))

    print("\nCounts:", counts)
    print("\nAll tests complete!")
