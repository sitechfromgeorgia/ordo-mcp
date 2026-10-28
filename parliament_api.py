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
import urllib.parse

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
                     page: int = 1, limit: int = 20) -> dict:
        """
        Search bills, newest first.

        Args:
            query: Free text matched against bill / package names (Georgian)
            bill_type_id: Filter by type — 1 = law draft, 11 = organic law,
                12 = constitutional law, 2 = parliament resolution (see bill_types())
            page / limit: Pagination (1-indexed page; limit ≤ 50)

        Returns:
            {"total": int, "bills": [...]}
        """
        # "id" sorts as a string server-side; the package date sorts correctly
        sort = json.dumps([{"property": "billPackage.registrationDate", "direction": "DESC"}])
        if query and bill_type_id:
            return self._search_filtered(query, bill_type_id, page, limit, sort)
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

    def _search_filtered(self, query: str, bill_type_id: int, page: int, limit: int,
                         sort: str, scan_pages: int = 10) -> dict:
        """The server ignores billTypeId when searchItem is set — filter client-side
        over the newest scan_pages × PAGE_MAX text matches; bills whose own name
        contains the query come first."""
        matched, total = [], 0
        for p in range(1, scan_pages + 1):
            res = self._get_json("/law/1/bill", {"searchItem": query, "page": p,
                                                 "start": (p - 1) * self.PAGE_MAX,
                                                 "limit": self.PAGE_MAX, "sort": sort})
            if "error" in res:
                return res
            total = res.get("total", 0)
            matched += [self._with_name_match(self._normalize_bill(i), query)
                        for i in res.get("list", [])
                        if (i.get("billType") or {}).get("id") == bill_type_id]
            if p * self.PAGE_MAX >= total:
                break
        scanned = min(total, p * self.PAGE_MAX)
        matched.sort(key=lambda b: not b["name_match"])  # stable: keeps newest-first within groups
        return {"total": len(matched) if scanned >= total else None,
                "text_matches": total, "scanned": scanned,
                "note": f"type filter applied client-side over the {scanned} newest text matches",
                "bills": matched[(page - 1) * limit: page * limit]}

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

    print("\n📑 Bill 8235 with stages and files:")
    bill = p.get_bill(8235)
    print(f"  {bill.get('name')} | finished={bill.get('finished')}")
    for s in bill.get("stages", []):
        print(f"  - {s['name']} | session {s['session_review_date']} | "
              f"votes {s['votes_for']}/{s['votes_against']} | files {len(s.get('files', []))}")

    print("\n✅ Parliament test complete!")
