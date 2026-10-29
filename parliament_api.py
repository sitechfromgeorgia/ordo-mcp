#!/usr/bin/env python3
"""
parliament_api.py — Parliament of Georgia legislative tracker (info.parliament.ge)
==================================================================================
Bills (კანონპროექტები) before they reach matsne: what is being drafted, by whom,
which hearing it is at, and the attached documents (draft text, explanatory
note, current wording of the articles being amended, committee conclusions).

Zero dependencies (stdlib only), matching matsne_api.py / court_api.py style.
The site is an ExtJS app; its REST endpoints (reverse-engineered 2026-10-29
from app.js + network traffic) return JSON and work without a browser:

  GET /law/1/bill?searchItem=&billTypeId=&page=&start=&limit=  — bill search
      searchItem = bill name OR package name OR a sibling bill's name; billTypeId
      is IGNORED when searchItem is set. Advanced-search fields (app.js
      SearchBill form) DO combine with billTypeId server-side: billName,
      packageName, billDescription (substring), registrationNumber,
      documentNumber, initiator, author, registrationDate1/2 (d-m-Y) …
      ExtJS `filter` JSON (property billType.id / billTypeId) is ignored.
  GET /law/1/bill/{id}                                         — one bill
  GET /law/1/billPackage/{package_id}/bill                     — bills in a package
  GET /law/1/bill/{id}/billReview                              — stages (hearings)
  GET /law/1/billReview/{review_id}/billReviewContent          — stage files
  GET /law/1/billReview/{review_id}/billCommittee              — committees
  GET /law/lib/1/billType                                      — bill types
  GET /file/1/BillReviewContent/{file_id}                      — file download (PDF/DOC)

Dates come back as DD-MM-YYYY.

Usage:
    from parliament_api import ParliamentAPI
    p = ParliamentAPI()
    r = p.search_bills("საგადასახადო კოდექს", bill_type_id=1)
    bill = p.get_bill(8235)
"""

import json
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from court_api import _BaseAPI


class ParliamentAPI(_BaseAPI):
    """Parliament of Georgia bill tracker (საკანონმდებლო მაცნე-მდე ეტაპი)."""

    BASE = "https://info.parliament.ge"
    PAGE_MAX = 50  # larger "limit" values return an empty body

    # Most useful billTypeId values (full list: bill_types())
    BILL_TYPES = {
        1: "საქართველოს კანონის პროექტი",
        2: "საქართველოს პარლამენტის დადგენილების პროექტი",
        11: "საქართველოს ორგანული კანონის პროექტი",
        12: "საქართველოს კონსტიტუციური კანონის პროექტი",
        17: "კოდექსი",
    }

    def _get_json(self, path: str, params: dict = None):
        url = f"{self.BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def _normalize_bill(self, item: dict) -> dict:
        package = item.get("billPackage") or {}
        stages = item.get("billReviewTypes") or []
        return {
            "id": item.get("id"),
            "name": item.get("billName"),
            "description": item.get("billDescription"),
            "type": (item.get("billType") or {}).get("name"),
            "registration_number": package.get("registrationNumber"),
            "registration_date": package.get("registrationDate"),
            "initiator": package.get("initiator"),
            "author": package.get("author"),
            "package_id": package.get("id"),
            "package_name": package.get("packageName"),
            "bills_in_package": package.get("billCount"),
            "stages": [s.get("name") for s in stages],
            "current_stage": stages[-1].get("name") if stages else None,
            "finished": bool(stages and stages[-1].get("finalReview")),
            "url": f"{self.BASE}/#law-drafting/{item.get('id')}",
        }

    def search_bills(self, query: str = "", bill_type_id: int = None,
                     page: int = 1, limit: int = 20, scan_related=None) -> dict:
        """
        Search bills, newest first.

        Args:
            query: Free text matched against bill / package names (Georgian)
            bill_type_id: Filter by type — 1 = law draft, 11 = organic law,
                12 = constitutional law, 2 = parliament resolution (see bill_types())
            page / limit: Pagination (1-indexed page; limit ≤ 50)
            scan_related: only with query + bill_type_id — whether to also collect
                "related" bills (query only in the package / a sibling bill, not in
                the bill's own name). None (default) = only when the requested page
                runs past the name matches; True = always (exact total, slower);
                False = never.

        Returns:
            {"total": int|None, "bills": [... each with name_match ...]}
            With query + bill_type_id also: name_matches, related_scanned,
            text_matches, scanned, note.
        """
        # "id" sorts as a string server-side; the package date sorts correctly
        sort = json.dumps([{"property": "billPackage.registrationDate", "direction": "DESC"}])
        if query and bill_type_id:
            return self._search_filtered(query, bill_type_id, page, limit, sort, scan_related)
        limit = min(limit, self.PAGE_MAX)
        params = {"searchItem": query, "page": page,
                  "start": (page - 1) * limit, "limit": limit, "sort": sort}
        if bill_type_id:
            params["billTypeId"] = bill_type_id
        res = self._get_json("/law/1/bill", params)
        if "error" in res:
            return res
        return {"total": res.get("total", 0),
                "bills": [self._with_name_match(self._normalize_bill(i), query)
                          for i in res.get("list", [])]}

    @staticmethod
    def _with_name_match(bill: dict, query: str) -> dict:
        # searchItem also matches the package name, so a bill can be a hit only
        # because a sibling bill in its package mentions the query
        bill["name_match"] = bool(query) and query.lower() in (bill.get("name") or "").lower()
        return bill

    # Parallel page fetches: the server spends ~0.1 s per returned row (a 50-row
    # page takes 3–7 s) but serves concurrent requests in parallel — measured
    # 8 × 50-row pages: 38.5 s sequential, 11.1 s with 4 workers, 7.3 s with 8.
    SCAN_WORKERS = 6

    def __init__(self, rate_limit: float = 0.3):
        super().__init__(rate_limit)
        self._rate_lock = threading.Lock()

    def _wait_rate_limit(self):
        # thread-safe variant of _BaseAPI's: request STARTS stay ≥ rate_limit apart
        with self._rate_lock:
            super()._wait_rate_limit()

    def _search_filtered(self, query: str, bill_type_id: int, page: int, limit: int,
                         sort: str, scan_related=None, scan_pages: int = 10) -> dict:
        """
        Type-filtered text search. The server ignores billTypeId together with
        searchItem, but honours it with the advanced-search field billName
        (substring of the bill's own name — exactly the name_match set):

          1. name matches  — GET /law/1/bill?billName=q&billTypeId=t (server-side,
             exact, one request per page)
          2. related bills — searchItem also hits bills whose package / sibling bill
             mentions q; those can only be type-filtered client-side, so the newest
             scan_pages × PAGE_MAX searchItem hits are fetched in parallel batches,
             stopping early once the requested page is filled (unless exhaustive).

        Order: name matches (newest first), then related bills (newest first).
        """
        limit = min(limit, self.PAGE_MAX)
        start, end = (page - 1) * limit, page * limit
        res = self._get_json("/law/1/bill", {"billName": query, "billTypeId": bill_type_id,
                                             "page": page, "start": start,
                                             "limit": limit, "sort": sort})
        if "error" in res:
            return res
        name_total = res.get("total", 0)
        bills = [self._with_name_match(self._normalize_bill(i), query) for i in res.get("list", [])]
        for b in bills:
            b["name_match"] = True  # server matched the name (normalization-safe)

        exhaustive = scan_related is True
        if scan_related is False or (scan_related is None and end <= name_total):
            return {"total": None if scan_related is None else name_total,
                    "name_matches": name_total, "related_scanned": False,
                    "note": "name matches only (server-side billName + billTypeId); bills "
                            "related to the query only via their package are listed after "
                            "them — request a later page or scan_related=True",
                    "bills": bills}

        want = end - name_total  # related bills needed to fill this page
        related, total, scanned = [], 0, 0

        def fetch(p):
            return self._get_json("/law/1/bill", {"searchItem": query, "page": p,
                                                  "start": (p - 1) * self.PAGE_MAX,
                                                  "limit": self.PAGE_MAX, "sort": sort})

        def take(page_res):
            nonlocal total, scanned
            total = page_res.get("total", total)
            rows = page_res.get("list", [])
            scanned += len(rows)
            for i in rows:
                if (i.get("billType") or {}).get("id") != bill_type_id:
                    continue
                b = self._with_name_match(self._normalize_bill(i), query)
                if not b["name_match"]:
                    related.append(b)

        first = fetch(1)
        if "error" in first:
            return first
        take(first)
        last = min(scan_pages, -(-total // self.PAGE_MAX))
        p = 2
        with ThreadPoolExecutor(max_workers=self.SCAN_WORKERS) as pool:
            while p <= last and (exhaustive or len(related) < want):
                # exhaustive: everything at once; otherwise one batch at a time so
                # we can stop as soon as the requested page is filled
                step = last if exhaustive else self.SCAN_WORKERS
                batch = list(range(p, min(p + step, last + 1)))
                for page_res in pool.map(fetch, batch):  # map keeps page order
                    if "error" in page_res:
                        return page_res
                    take(page_res)
                p = batch[-1] + 1
        complete = scanned >= total
        r_start, r_end = max(0, start - name_total), max(0, want)
        return {"total": name_total + len(related) if complete else None,
                "name_matches": name_total, "related_scanned": True,
                "text_matches": total, "scanned": scanned,
                "note": (f"name matches server-side; related bills filtered client-side over "
                         f"the {scanned} newest of {total} text matches"),
                "bills": (bills if start < name_total else []) + related[r_start:r_end]}

    def bill_types(self) -> dict:
        """All bill types: {"total", "list": [{id, name}]}."""
        return self._get_json("/law/lib/1/billType")

    def get_bill(self, bill_id: int, with_files: bool = True) -> dict:
        """
        One bill with its legislative history: every stage (initiated version,
        I/II/III hearing, resolution, adopted law) with dates, vote counts,
        committees and downloadable files.

        Returns:
            {..bill fields.., "stages": [{name, dates, votes_for, votes_against,
             committees, files: [{type, date, url}]}]}
        """
        res = self._get_json(f"/law/1/bill/{bill_id}")
        if isinstance(res, dict) and "error" in res:
            return res
        if not res:
            return {"error": f"Bill {bill_id} not found"}
        bill = self._normalize_bill(res[0])

        reviews = self._get_json(f"/law/1/bill/{bill_id}/billReview")
        if isinstance(reviews, dict) and "error" in reviews:
            bill["stages_error"] = reviews["error"]
            return bill
        stages = []
        for r in reviews:
            stage = {
                "review_id": r.get("id"),
                "name": r.get("reviewTypeName") or (r.get("billReviewType") or {}).get("name"),
                "bureau_review_date": r.get("bureauReviewDate"),
                "session_review_date": r.get("sessionReviewDate"),
                "review_from": r.get("billReviewFromDate"),
                "review_to": r.get("billReviewToDate"),
                "document_number": r.get("documentNumber"),
                "votes_for": r.get("voteY"),
                "votes_against": r.get("voteN"),
                "note": r.get("billReviewNote") or None,
            }
            if with_files:
                files = self._get_json(f"/law/1/billReview/{r.get('id')}/billReviewContent")
                stage["files"] = [] if isinstance(files, dict) else [{
                    "type": (f.get("billReviewAttachmentType") or {}).get("name"),
                    "date": f.get("note"),
                    "format": (f.get("extension") or "").lstrip("."),
                    "size": f.get("size"),
                    "url": f"{self.BASE}/file/1/BillReviewContent/{f.get('id')}",
                } for f in files]
                committees = self._get_json(f"/law/1/billReview/{r.get('id')}/billCommittee")
                stage["committees"] = [] if isinstance(committees, dict) else [{
                    "name": c.get("nodeName"),
                    "role": (c.get("billCommitteeType") or {}).get("name"),
                } for c in committees]
            stages.append(stage)
        bill["stages"] = stages
        return bill

    def get_package_bills(self, package_id: int) -> dict:
        """All bills in one legislative package (a main law + its accompanying amendments)."""
        res = self._get_json(f"/law/1/billPackage/{package_id}/bill", {"page": 1, "start": 0, "limit": 100})
        if "error" in res:
            return res
        items = res.get("list", res) if isinstance(res, dict) else res
        return {"package_id": package_id, "bills": [self._normalize_bill(i) for i in items]}


# ── CLI Test ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    p = ParliamentAPI()

    print("=" * 60)
    print("🏛️ PARLIAMENT (info.parliament.ge) — TEST")
    print("=" * 60)

    print("\n📋 Latest law drafts:")
    r = p.search_bills(bill_type_id=1, limit=5)
    print(f"  Total law drafts: {r.get('total')}")
    for b in r.get("bills", []):
        print(f"  {b['registration_date']} | {b['registration_number']} | {b['current_stage']} | {b['name'][:60]}")

    print("\n🔎 Search 'საგადასახადო კოდექს':")
    r = p.search_bills("საგადასახადო კოდექს", limit=3)
    print(f"  Total: {r.get('total')}")
    for b in r.get("bills", []):
        print(f"  ID:{b['id']} | {b['type']} | {b['name'][:60]}")

    import time
    print("\n🔎 Law drafts (type 1) named 'საგადასახადო კოდექს…' (server-side filter):")
    t0 = time.time()
    r = p.search_bills("საგადასახადო კოდექს", bill_type_id=1, limit=3)
    print(f"  Name matches: {r.get('name_matches')} ({time.time() - t0:.1f}s)")
    for b in r.get("bills", []):
        print(f"  ID:{b['id']} | {b['registration_date']} | name_match={b['name_match']} | {b['name'][:50]}")

    print("\n🔎 Type 1 + 'შრომის კოდექს' incl. related bills (parallel scan):")
    t0 = time.time()
    r = p.search_bills("შრომის კოდექს", bill_type_id=1, limit=20, scan_related=True)
    print(f"  Total: {r.get('total')} (name {r.get('name_matches')}, scanned "
          f"{r.get('scanned')}/{r.get('text_matches')}) in {time.time() - t0:.1f}s")

    print("\n📑 Bill 8235 with stages and files:")
    bill = p.get_bill(8235)
    print(f"  {bill.get('name')} | finished={bill.get('finished')}")
    for s in bill.get("stages", []):
        print(f"  - {s['name']} | session {s['session_review_date']} | "
              f"votes {s['votes_for']}/{s['votes_against']} | files {len(s.get('files', []))}")

    print("\n✅ Parliament test complete!")
