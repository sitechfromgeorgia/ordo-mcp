#!/usr/bin/env python3
"""
higher_courts_api.py — apex courts (Constitutional Court + ECHR)
=================================================================
Two more sources for the Georgia Legal Ecosystem MCP:

  * constcourt.ge     — Constitutional Court of Georgia (საკონსტიტუციო სასამართლო)
  * hudoc.echr.coe.int — European Court of Human Rights (HUDOC)

Zero dependencies (stdlib only), matching matsne_api.py / court_api.py style.

Authority hierarchy (authority_level, higher = stronger):
  7  — ECHR (supranational; binding on Georgia under the Convention)
  6  — Constitutional Court (can strike down laws)
  5  — Supreme Court Grand Chamber (binding norm interpretation)
  4  — Supreme Court chamber (cassation)
  3  — Appeals court
  2  — First-instance court

Usage:
    from higher_courts_api import ConstitutionalCourtAPI, EchrAPI
    cc = ConstitutionalCourtAPI()
    r = cc.search_acts(intext="საკუთრება")
    act = cc.get_act(15566)

    echr = EchrAPI()
    r = echr.search(respondent="GEO", article="6")
    case = echr.get_case("001-57574")
"""

import re
import urllib.parse

from court_api import _BaseAPI, _html_to_text

AUTHORITY_CONSTITUTIONAL = 6
AUTHORITY_ECHR = 7


# ── Constitutional Court of Georgia ───────────────────────────

class ConstitutionalCourtAPI(_BaseAPI):
    """Georgia's Constitutional Court (საკონსტიტუციო სასამართლო)."""

    BASE = "https://www.constcourt.ge"

    def search_acts(self, fullsearch: str = "", intext: str = "", number: str = "",
                    name: str = "", date_from: str = "", date_to: str = "",
                    quantity: int = 20, sort: str = "") -> dict:
        """
        Search Constitutional Court judicial acts.

        Args:
            fullsearch: search name / number / result (partial matches)
            intext: full-text search inside act texts
            number: act number (e.g. 'N1/21/1727')
            name: act title / case name
            date_from / date_to: act date range (site format DD/MM/YYYY)
            quantity: results per page (10, 20, 50, 100)
            sort: 'desc' (newest first) or 'asc'

        Returns:
            {"items": [{"id", "title", "summary", "url", "authority_level"}], "count": n}
        """
        params = {"quantity": quantity}
        if fullsearch:
            params["fullsearch"] = fullsearch
        if intext:
            params["intext"] = intext
            params["intextsearch"] = "intextsearch"
        if number:
            params["number"] = number
        if name:
            params["nameing"] = name
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        if sort:
            params["sort"] = sort

        html = self._get("/ka/judicial-acts", params)
        if html.startswith("ERROR"):
            return {"error": html}
        items = self._parse_results(html)
        return {"items": items, "count": len(items)}

    def _parse_results(self, html: str) -> list:
        items = []
        for block in re.split(r'<div class="legal-act">', html)[1:]:
            id_m = re.search(r"\?legal=(\d+)", block)
            if not id_m:
                continue
            title = ""
            tm = re.search(r'class="legal-act-title"[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.DOTALL)
            if tm:
                title = _html_to_text(tm.group(1))
            summary = ""
            um = re.search(r'<ul class="legal-act-info-list[^"]*"[^>]*>(.*?)</ul>', block, re.DOTALL)
            if um:
                summary = _html_to_text(um.group(1))
            items.append({
                "id": int(id_m.group(1)),
                "title": title,
                "summary": summary,
                "url": f"{self.BASE}/ka/judicial-acts?legal={id_m.group(1)}",
                "authority_level": AUTHORITY_CONSTITUTIONAL,
            })
        return items

    def get_act(self, legal_id: int) -> dict:
        """
        Get a full Constitutional Court act by its legal id.

        Returns:
            {"id", "title", "text", "url", "authority_level"}
        """
        html = self._get("/ka/judicial-acts", {"legal": legal_id})
        if html.startswith("ERROR"):
            return {"error": html}
        text = self._extract_act_text(html)
        title = ""
        tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        if tm:
            title = _html_to_text(tm.group(1))
        return {
            "id": legal_id,
            "title": title,
            "text": text,
            "url": f"{self.BASE}/ka/judicial-acts?legal={legal_id}",
            "authority_level": AUTHORITY_CONSTITUTIONAL,
        }

    def _extract_act_text(self, html: str) -> str:
        m = re.search(r'<div[^>]*id="printablePageContent"[^>]*>', html)
        if not m:
            return _html_to_text(html)
        i = m.end()
        depth = 1
        end = -1
        k = i
        while k < len(html) - 6:
            if html[k:k + 4] == "<div":
                depth += 1
            elif html[k:k + 6] == "</div>":
                depth -= 1
                if depth == 0:
                    end = k
                    break
            k += 1
        if end < 0:
            return _html_to_text(html[i:])
        return _html_to_text(html[i:end])


# ── European Court of Human Rights (HUDOC) ───────────────────

class EchrAPI(_BaseAPI):
    """European Court of Human Rights case law via the HUDOC database."""

    BASE = "https://hudoc.echr.coe.int"

    # HUDOC sits behind Cloudflare bot management. A browser User-Agent that does
    # NOT also send the matching Sec-CH-UA client hints (or a non-browser UA such as
    # "Python-urllib/3.x") is answered with a managed challenge: HTTP 403 +
    # "Cf-Mitigated: challenge" — even though the same URL returns 200 to curl.
    # Measured 2026-09-25: bare Chrome UA → 403 · Chrome UA + sec-ch-ua* → 200 ·
    # "curl/8.5.0" → 200 · "Python-urllib/3.13" → 403. So: keep UA and client
    # hints consistent, and fall back to a plain UA only if challenged anyway.
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    EXTRA_HEADERS = {
        "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://hudoc.echr.coe.int/",
    }
    FALLBACK_UA = "curl/8.5.0"

    SELECT = ("itemid,docname,doctype,application,appno,conclusion,importance,"
              "originatingbody,typedescription,kpdate,kpdateAsText,"
              "documentcollectionid,languageisocode,extractedappno,respondent,ecli")

    def __init__(self, rate_limit: float = 0.5):
        # HUDOC rate-limits aggressively — use a slightly higher delay
        super().__init__(rate_limit=rate_limit)

    def _build_query(self, respondent="", article="", appno="", text="",
                     language="ENG") -> str:
        parts = ["contentsitename:ECHR"]
        parts.append("(NOT (doctype:PR OR doctype:HFCOMOLD OR doctype:HECOMOLD))")
        if language:
            parts.append(f'((languageisocode:"{language}"))')
        if respondent:
            parts.append(f'((respondent:"{respondent}"))')
        if article:
            parts.append(f'((article:"{article}"))')
        if appno:
            parts.append(f'((appno:"{appno}"))')
        if text:
            parts.append(f'((text:"{text}"))')
        return " AND ".join(parts)

    def search(self, respondent: str = "", article: str = "", appno: str = "",
               text: str = "", language: str = "ENG",
               start: int = 0, length: int = 20, query: str = "",
               sort: str = "kpdate Descending") -> dict:
        """
        Search ECHR case law in HUDOC.

        Args:
            respondent: respondent state code (e.g. 'GEO' for Georgia)
            article: Convention article (e.g. '6', '6-1', '8')
            appno: application number (e.g. '26134/19')
            text: free-text keyword
            language: 'ENG' (English) or 'FRE' (French)
            start / length: pagination (HUDOC caps length at 500)
            query: raw HUDOC query string (overrides the structured filters)
            sort: HUDOC sort expression, default 'kpdate Descending' (newest first);
                  pass sort="" for HUDOC's relevance ranking

        Returns:
            {"total": int, "items": [...]}
        """
        q = query or self._build_query(respondent, article, appno, text, language)
        url = (f"{self.BASE}/app/query/results"
               f"?query={urllib.parse.quote(q)}"
               f"&select={urllib.parse.quote(self.SELECT)}"
               f"&sort={urllib.parse.quote(sort)}&start={start}&length={length}")
        raw = self._request_hudoc(url)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            import json
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON (likely rate-limited)", "raw": raw[:200]}
        results = data.get("results") or []
        items = [self._normalize(r.get("columns") or {}) for r in results]
        return {"total": data.get("resultcount", len(items)), "items": items}

    def _request_hudoc(self, url: str, accept_json: bool = True) -> str:
        """
        GET a HUDOC URL with a one-shot Cloudflare fallback.

        If the browser-consistent header set is still challenged (HTTP 403 with
        'Cf-Mitigated: challenge'), retry once as a plain non-browser client —
        HUDOC/Cloudflare answers 200 to those instead of a challenge page.
        """
        raw = self._request(url, accept_json=accept_json)
        if raw.startswith("ERROR") and "403" in raw:
            self.UA = self.FALLBACK_UA       # instance attr shadows the class attr
            self.EXTRA_HEADERS = {}
            try:
                raw = self._request(url, accept_json=accept_json, retries=1)
            finally:
                del self.UA
                del self.EXTRA_HEADERS
        return raw

    def _normalize(self, columns: dict) -> dict:
        itemid = columns.get("itemid", "")
        kpdate = columns.get("kpdate") or ""
        return {
            "itemid": itemid,
            "title": columns.get("docname", ""),
            "appno": columns.get("appno") or columns.get("extractedappno", ""),
            "respondent": columns.get("respondent", ""),
            "conclusion": columns.get("conclusion", ""),
            "importance": columns.get("importance", ""),
            "type": columns.get("typedescription") or columns.get("doctype", ""),
            "date": kpdate[:10],
            "language": columns.get("languageisocode", ""),
            "ecli": columns.get("ecli", ""),
            "authority_level": AUTHORITY_ECHR,
            "url": (f"{self.BASE}/eng#%7B%22itemid%22%3A%5B%22{itemid}%22%5D%7D"),
            "full_text_url": f"{self.BASE}/app/conversion/docx/html/body?library=ECHR&id={itemid}",
        }

    def get_case(self, itemid: str) -> dict:
        """
        Get the full text of an ECHR decision by its HUDOC itemid.

        Returns:
            {"itemid", "text", "url", "authority_level"}
        """
        url = f"{self.BASE}/app/conversion/docx/html/body?library=ECHR&id={itemid}"
        html = self._request_hudoc(url, accept_json=False)
        if html.startswith("ERROR"):
            return {"error": html}
        return {
            "itemid": itemid,
            "text": _html_to_text(html),
            "url": f"{self.BASE}/eng#%7B%22itemid%22%3A%5B%22{itemid}%22%5D%7D",
            "authority_level": AUTHORITY_ECHR,
        }


# ── CLI Test ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 60)
    print("HIGHER COURTS API — LIVE TEST")
    print("=" * 60)

    cc = ConstitutionalCourtAPI()

    print("\n[constcourt] Search acts (intext=საკუთრება):")
    r = cc.search_acts(intext="საკუთრება", quantity=5)
    if "error" not in r:
        print(f"  Count: {r['count']}")
        for it in r["items"][:3]:
            print(f"  [{it['authority_level']}] {it['id']} | {it['title'][:50]}")
    else:
        print("  Error:", r["error"])

    print("\n[constcourt] Full act (sample):")
    if "error" not in r and r["items"]:
        act = cc.get_act(r["items"][0]["id"])
        if "error" not in act:
            print(f"  Chars: {len(act['text'])}")
            print(f"  Preview: {act['text'][:120]}")

    echr = EchrAPI()

    print("\n[echr] Search (respondent=GEO, article=6):")
    er = echr.search(respondent="GEO", article="6", length=3)
    if "error" not in er:
        print(f"  Total: {er['total']}")
        for it in er["items"]:
            print(f"  {it['date']} | {it['title'][:50]} | {it['appno']}")
    else:
        print("  Error:", er["error"])

    print("\nAll tests complete!")
