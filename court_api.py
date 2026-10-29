#!/usr/bin/env python3
"""
court_api.py — Georgian court practice APIs
============================================
Two reverse-engineered, undocumented APIs covering Georgia's court practice:

  * ecd.court.ge      — common courts' case-production system (decisions & acts)
  * supremecourt.ge   — Supreme Court cassation decisions (+ Grand Chamber)

Zero dependencies (stdlib only), matching the style of matsne_api.py.

Authority hierarchy (authority_level, higher = stronger):
  5  — Supreme Court GRAND CHAMBER (binding on all common courts)
  4  — Supreme Court chamber (cassation)
  3  — Appeals court (second instance)
  2  — First-instance court

Endpoints (ecd.court.ge, all POST + JSON):
  /Classifiers/Instances, /Classifiers/Courts, /Classifiers/CaseCategories,
  /Classifiers/LitigationTypes, /Classifiers/DecisionTypes
  /Decision/DecisionDocuments       — decision search
  /Decision/DecisionDocumentText    — full decision text
  /Decision/DecisionDocumentPdf     — decision PDF
  /FinalDocument/FinalDecisionDocument — court-act verification (barcode + case no)
  /FinalDocument/FinalDocumentPdf   — court-act PDF

Endpoints (supremecourt.ge, GET + HTML):
  /ka/getCases                      — cassation search
  /ka/fullcase/{id}/{palata}        — full decision text
  /ka/download/{id}/{palata}        — download

Usage:
    from court_api import EcdCourtAPI, SupremeCourtAPI
    ecd = EcdCourtAPI()
    r = ecd.search_decisions(texts=["ალიმენტი"])
    sc = SupremeCourtAPI()
    r = sc.search_cases(palata=1, texts=["სამსახურიდან გათავისუფლება"])
    case = sc.get_case(75795, 1)
"""

import html as _html_lib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

# ── Authority levels ──────────────────────────────────────────
AUTHORITY_GRAND_CHAMBER = 5
AUTHORITY_SUPREME = 4
AUTHORITY_APPEALS = 3
AUTHORITY_FIRST = 2


# ── Shared helpers ────────────────────────────────────────────

def _parse_csharp_date(value: str) -> str:
    """Convert C# '/Date(1588260918000)/' → 'YYYY-MM-DD' (UTC)."""
    if not value:
        return ""
    m = re.search(r"Date\((-?\d+)\)", value)
    if m:
        try:
            dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return value
    return value


def _html_to_text(html_fragment: str) -> str:
    """Strip tags + unescape entities → clean plain-text lines."""
    text = _html_lib.unescape(html_fragment)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


class _BaseAPI:
    """Shared HTTP plumbing: rate-limit, cookies, retries."""

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

    # Subclasses whose origin wants extra headers (e.g. HUDOC/Cloudflare matches
    # a browser UA against the Sec-CH-UA client hints) may override this. Default: none.
    EXTRA_HEADERS = {}

    def __init__(self, rate_limit: float = 0.3):
        self.rate_limit = rate_limit
        self._cookies = {}
        self._last_request_time = 0.0

    def _wait_rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_time = time.time()

    def _request(self, url: str, method: str = "GET",
                 json_payload: Optional[dict] = None,
                 accept_json: bool = False, retries: int = 2) -> str:
        self._wait_rate_limit()

        headers = {
            "User-Agent": self.UA,
            "Accept": "application/json" if accept_json else "text/html,application/xhtml+xml",
            "Accept-Language": "ka,en;q=0.9",
        }
        headers.update(self.EXTRA_HEADERS)
        if accept_json:
            headers["X-Requested-With"] = "XMLHttpRequest"
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())

        body = None
        if json_payload is not None:
            body = json.dumps(json_payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    for header in resp.headers.get_all("Set-Cookie") or []:
                        m = re.match(r"([^=]+)=([^;]+)", header.strip())
                        if m:
                            self._cookies[m.group(1)] = m.group(2)
                    return resp.read().decode("utf-8")
            except Exception as e:
                if attempt < retries:
                    time.sleep(1 * (attempt + 1))
                    continue
                return f"ERROR: {e}"

    def _get(self, path: str, params: Optional[dict] = None) -> str:
        url = f"{self.BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._request(url, method="GET")

    @staticmethod
    def _json(raw: str) -> dict:
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}


# ── ecd.court.ge ──────────────────────────────────────────────

class EcdCourtAPI(_BaseAPI):
    """Georgia's common courts case-production system (საქმისწარმოების სისტემა)."""

    BASE = "https://ecd.court.ge"

    # InstanceId → (authority_level, name)
    INSTANCES = {
        1: (AUTHORITY_FIRST, "პირველი ინსტანცია"),
        2: (AUTHORITY_APPEALS, "მეორე ინსტანცია"),
        3: (AUTHORITY_SUPREME, "მესამე ინსტანცია"),
    }

    def _post_json(self, path: str, payload: dict) -> dict:
        url = f"{self.BASE}{path}"
        raw = self._request(url, method="POST", json_payload=payload, accept_json=True)
        return self._json(raw)

    # ── Classifiers ───────────────────────────────────────────

    def get_instances(self) -> dict:
        """Court instances: [{Id, Name, Fullname}, ...]."""
        return self._post_json("/Classifiers/Instances", {})

    def get_courts(self, instance_id=None) -> dict:
        payload = {}
        if instance_id not in (None, ""):
            payload["InstanceId"] = instance_id
        return self._post_json("/Classifiers/Courts", payload)

    def get_case_categories(self, instance_id=None, filter_text: str = "") -> dict:
        payload = {"filterText": filter_text}
        if instance_id not in (None, ""):
            payload["InstanceId"] = instance_id
        return self._post_json("/Classifiers/CaseCategories", payload)

    def get_litigation_types(self, instance_id=None, case_category_id=None,
                             filter_text: str = "") -> dict:
        payload = {"filterText": filter_text}
        if instance_id not in (None, ""):
            payload["InstanceId"] = instance_id
        if case_category_id not in (None, ""):
            payload["CaseCategoryId"] = case_category_id
        return self._post_json("/Classifiers/LitigationTypes", payload)

    def get_decision_types(self, instance_id=None, filter_text: str = "") -> dict:
        payload = {"filterText": filter_text}
        if instance_id not in (None, ""):
            payload["InstanceId"] = instance_id
        return self._post_json("/Classifiers/DecisionTypes", payload)

    # ── Decision search ───────────────────────────────────────

    @staticmethod
    def _authority(instance_id) -> int:
        return EcdCourtAPI.INSTANCES.get(instance_id, (0, ""))[0]

    def _normalize_item(self, item: dict) -> dict:
        instance_id = item.get("InstanceId")
        return {
            "id": item.get("DecisionDocumentId"),
            "instance_id": instance_id,
            "instance": item.get("InstanceName"),
            "authority_level": self._authority(instance_id),
            "case_number": item.get("CaseNo"),
            "court": item.get("CourtName"),
            "case_category": item.get("CaseCategoryName"),
            "decision_type": item.get("TypeName"),
            "litigation_type": item.get("LitigationTypeName"),
            "date": _parse_csharp_date(item.get("DecisionDate") or ""),
            "barcode": item.get("Barcode"),
            "pdf_url": f"{self.BASE}/Decision/DecisionDocumentPdf?InstanceId={instance_id}&DecisionDocumentId={item.get('DecisionDocumentId')}",
        }

    # ── Full-text semantics (measured live 2026-10-29) ─────────
    #
    # The DB only holds decisions dated 2018-01-01 … 2020-04-30 (44,489 docs);
    # nothing newer or older is searchable here — use supremecourt.ge for that.
    #
    # Each entry of Texts is matched like this, and all entries are ANDed:
    #   * ONE word  → substring inside a word: "ალიმენტ" 1363, "ლიმენტ" 1375,
    #                 "ალიმენტი" 1312. '*', '?' are ignored ("ალიმენტ*" 1363).
    #                 A hyphen kills it: "361-ე" → 0 (words are split on '-'),
    #                 while the phrase "361 ე" → 5151.
    #   * SEVERAL words → exact phrase of WHOLE words, adjacent, in order:
    #                 "361-ე მუხლის" 4361 but "361-ე მუხლ" 3 (stem ≠ word),
    #                 "ალიმენტი ბავშვი" 0. No wildcards, no OR/|/quotes syntax.
    #   * Texts=[a, b] → a AND b anywhere in the decision
    #                 (["ალიმენტი", "ბავშვი"] 987).
    # Results come back newest first; Take > 50 → HTTP 500.
    # So: stems only work as single words; in phrases every case form
    # (მუხლის / მუხლით / მუხლი …) is a separate query — OR them via search_any().

    MAX_TAKE = 50

    # Case endings of a consonant-final noun stem, most frequent first
    # (hit counts for "სამოქალაქო საპროცესო კოდექსის 102-ე მუხლ…": ის 6893,
    #  ით 507, ზე 327, ი 279, ში 40, ს 33, ისა 24, იდან 22, მა 16, ების 9).
    STEM_ENDINGS = ("ის", "ით", "ი", "ზე", "ს", "ში", "იდან", "ისა", "მა", "ების", "ებით", "ები")
    ARTICLE_ENDINGS = STEM_ENDINGS[:8]

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalize one Texts entry to what the server can match: collapse
        whitespace, drop wildcard chars; a single hyphenated word ("361-ე",
        "მე-2") becomes the phrase "361 ე" (the index splits words on '-')."""
        text = " ".join(str(text).replace("%", " ").split())
        if " " not in text:
            text = text.strip("*?")
            if "-" in text:
                text = " ".join(p for p in text.replace("*", "").split("-") if p)
        return text

    @classmethod
    def expand_forms(cls, text: str, endings: Optional[tuple] = None,
                     max_forms: int = 24) -> list:
        """
        Expand a phrase whose words end in '*' into concrete word forms.

        Multi-word texts only match whole words, so "361-ე მუხლ*" must become
        ["361-ე მუხლის", "361-ე მუხლით", "361-ე მუხლი", …]. Single words are
        substring-matched server-side and are returned as-is (minus '*').
        Several starred words expand to their product, capped at max_forms.
        """
        words = " ".join(str(text).split()).split(" ")
        if len(words) < 2 or not any(w.endswith("*") and len(w) > 1 for w in words):
            return [cls._clean_text(text)] if text and text.strip() else []
        endings = endings or cls.STEM_ENDINGS
        forms = [""]
        for w in words:
            alts = [w[:-1] + e for e in endings] if w.endswith("*") and len(w) > 1 else [w]
            forms = [f"{f} {a}".strip() for f in forms for a in alts][:max_forms]
        return forms

    @staticmethod
    def _article_ordinal(article: str) -> str:
        """Georgian ordinal citation form: "361-ე", "მე-20", "პირველი", "151¹"."""
        number, _, sup = str(article).strip().partition("^")
        if sup:
            return number + sup.translate(str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
        n = int(number)
        if n == 1:
            return "პირველი"
        if n <= 20 or n % 20 == 0:
            return f"მე-{n}"
        return f"{n}-ე"

    def search_decisions(self, case_no: str = "", date_from: Optional[str] = None,
                         date_to: Optional[str] = None, instance_id=None,
                         court_code=None, case_category_id=None,
                         litigation_type_id=None, type_id=None,
                         texts: Optional[list] = None,
                         skip: int = 0, take: int = 20,
                         expand: bool = True) -> dict:
        """
        POST /Decision/DecisionDocuments — full decision search.

        Coverage: decisions dated 2018-01-01 … 2020-04-30 only (~44.5k).

        Args:
            case_no: Case number (full 16-digit or partial)
            date_from / date_to: Decision date range, 'YYYY-MM-DD' (or None)
            instance_id: 1=first, 2=appeals, 3=supreme
            court_code: Court code from get_courts()
            case_category_id: from get_case_categories() (1=criminal, 2=civil, 3=admin)
            litigation_type_id: ONE id from get_litigation_types() — for criminal
                cases it is a Criminal Code article/part/subparagraph ("177 2 ა"),
                for admin cases an Administrative Offences Code article; for civil
                cases a dispute category. See litigation_types_for_article().
            type_id: decision type from get_decision_types()
            texts: full-text terms, ANDed. One word = substring match ("ალიმენტ");
                several words = exact phrase of whole words ("361-ე მუხლის", NOT
                "361-ე მუხლ"). End a word with '*' to search all its case forms
                ("სამოქალაქო კოდექსის 361-ე მუხლ*") — this runs one query per form
                and merges them (see search_any).
            skip / take: pagination (take ≤ 50 — the server 500s above that)
            expand: expand '*' stems in phrases (default True)

        Returns:
            {"total": int, "items": [...]}  (+ "variants"/"total_exact" when expanded)
        """
        texts = [t for t in (texts or []) if t and str(t).strip()]
        if expand and any(len(self.expand_forms(t)) > 1 for t in texts):
            # AND of ORs → product of alternatives, one server query each
            combos = [[]]
            for t in texts:
                combos = [c + [f] for c in combos for f in self.expand_forms(t)][:24]
            common = dict(case_no=case_no, date_from=date_from, date_to=date_to,
                          instance_id=instance_id, court_code=court_code,
                          case_category_id=case_category_id,
                          litigation_type_id=litigation_type_id, type_id=type_id)
            return self.search_any([dict(common, texts=c) for c in combos], skip=skip, take=take)
        texts = [c for c in (self._clean_text(t) for t in texts) if c]
        payload = {
            "CaseNo": case_no,
            "DecisionDateFrom": date_from,
            "DecisionDateTo": date_to,
            "InstanceId": instance_id if instance_id not in (None, "") else "",
            "CourtCode": court_code if court_code not in (None, "") else "",
            "CaseCategoryId": case_category_id if case_category_id not in (None, "") else "",
            "LitigationTypeId": litigation_type_id if litigation_type_id not in (None, "") else "",
            "TypeId": type_id if type_id not in (None, "") else "",
            "Texts": texts,
            "Skip": skip,
            "Take": max(1, min(int(take), self.MAX_TAKE)),
        }
        res = self._post_json("/Decision/DecisionDocuments", payload)
        if "error" in res:
            return res
        if not res.get("success"):
            return {"error": "Server error", "raw": res}
        data = res.get("data") or {}
        items = [self._normalize_item(i) for i in (data.get("Items") or [])]
        return {"total": data.get("Total", len(items)), "items": items}

    # ── OR-search / article search ────────────────────────────

    def search_any(self, variants: list, skip: int = 0, take: int = 20,
                   exact_total: bool = False, max_scan: int = 2000) -> dict:
        """
        OR-merge several searches (the server itself only ANDs).

        Each variant is a dict of search_decisions() kwargs (texts, litigation_type_id,
        filters …) — or a plain list of texts / a single text string. Every variant is
        queried for its newest skip+take hits (pages of 50), results are de-duplicated
        and re-sorted newest first, so skip/take paginate the union.

        Args:
            variants: [{"texts": [...], "instance_id": 1, ...}, ["a b"], "c", ...]
            skip / take: pagination over the merged result
            exact_total: also page through every variant (≤ max_scan hits each) to
                count the union exactly — slow for broad queries (one request / 50 hits)

        Returns:
            {"total": int, "total_exact": bool, "total_max": int,
             "variants": [{"texts", "litigation_type_id", "total"}],
             "items": [... each with "matched": [variant indexes]]}
            total is exact when total_exact, else a lower bound (largest variant);
            total_max is the sum of variant totals (upper bound).
        """
        merged, report, totals, complete = {}, [], [], True
        need = skip + take
        for idx, v in enumerate(variants):
            if isinstance(v, str):
                v = {"texts": [v]}
            elif isinstance(v, (list, tuple)):
                v = {"texts": list(v)}
            v = dict(v, expand=False)
            v.pop("skip", None); v.pop("take", None)
            fetched, total, pos = 0, None, 0
            limit = max_scan if exact_total else need
            while total is None or pos < min(total, limit):
                res = self.search_decisions(skip=pos, take=self.MAX_TAKE, **v)
                if "error" in res:
                    return {"error": res["error"], "variant": v}
                total = res.get("total", 0)
                for item in res.get("items", []):
                    key = (item.get("instance_id"), item.get("id"))
                    if key in merged:
                        merged[key]["matched"].append(idx)
                    else:
                        merged[key] = dict(item, matched=[idx])
                    fetched += 1
                if not res.get("items"):
                    break
                pos += self.MAX_TAKE
            totals.append(total or 0)
            complete = complete and fetched >= (total or 0)
            report.append({"texts": v.get("texts"), "litigation_type_id": v.get("litigation_type_id"),
                           "total": total or 0})
        items = sorted(merged.values(), key=lambda i: (i.get("date") or "", i.get("id") or 0),
                       reverse=True)
        return {"total": len(merged) if complete else max(totals, default=0),
                "total_exact": complete, "total_max": sum(totals),
                "variants": report, "items": items[skip:skip + take]}

    # Criminal Code ("177 2 ა", "18 -  177 3 ", "189 (1) 4") and Administrative
    # Offences Code ("165(7)-2", "119-1-ა") litigation-type names
    _LT_CRIMINAL = re.compile(r"^(?:(18|19|25)\s*-\s*)?(\d+)\s*(?:\((\d+)\))?\s*(\d+)?\s*([ა-ჰ])?$")
    _LT_ADMIN = re.compile(r"^(\d+)(?:\((\d+)\))?(?:-(\d+))?(?:-([ა-ჰ]))?$")

    def litigation_types_for_article(self, article: str, part=None, subparagraph: str = "",
                                     case_category_id: int = 1,
                                     include_inchoate: bool = False) -> list:
        """
        Litigation-type ids that encode a code article — the precise, classifier-
        based article filter (no full-text guessing).

        Only criminal (case_category_id=1 → Criminal Code: "177 2 ა" = art. 177
        part 2 subpar. ა; "18 - 177" / "19 - 177" / "25 - 177" = preparation /
        attempt / complicity) and administrative (3 → Administrative Offences Code:
        "165(7)-2" = art. 165⁷ part 2) cases are classified by article. Civil
        cases (2) use dispute categories instead — use texts there.

        Args:
            article: "177" or "189^1" (superscript)
            part / subparagraph: narrow to one part ("2") / subparagraph ("ა")
            include_inchoate: also include art. 18/19/25 combinations (criminal)

        Returns:
            [{"id": int, "name": str}] (ids are global across instances)
        """
        number, _, sup = str(article).strip().partition("^")
        res = self.get_litigation_types(case_category_id=case_category_id, filter_text=number)
        if "error" in res:
            return []
        pattern = self._LT_CRIMINAL if int(case_category_id) == 1 else self._LT_ADMIN
        found = {}
        for lt in res.get("data") or []:
            name = " ".join((lt.get("Name") or "").split())
            m = pattern.match(name)
            if not m:
                continue
            if pattern is self._LT_CRIMINAL:
                inchoate, art, art_sup, art_part, letter = m.groups()
                if inchoate and not include_inchoate:
                    continue
            else:
                art, art_sup, art_part, letter = m.groups()
            if art != number or (art_sup or "") != sup:
                continue
            if part not in (None, "") and art_part != str(part):
                continue
            if subparagraph and letter != subparagraph:
                continue
            found[lt["LitigationTypeId"]] = name
        return [{"id": k, "name": v} for k, v in sorted(found.items())]

    def search_article(self, article: str, law: str = "", mode: str = "auto",
                       part=None, instance_id=None, case_category_id=None,
                       date_from: Optional[str] = None, date_to: Optional[str] = None,
                       skip: int = 0, take: int = 20, all_forms: bool = False) -> dict:
        """
        Decisions citing one article of a law — OR-merges the citation's case forms.

        Args:
            article: article key, "361" or "151^1"
            law: the law's name in the GENITIVE as decisions write it, e.g.
                "სამოქალაქო კოდექსის", "სისხლის სამართლის კოდექსის",
                "„საჯარო სამსახურის შესახებ“ საქართველოს კანონის". Empty = any law.
            mode:
                "phrase"     — "<law> 361-ე მუხლის|მუხლით|მუხლი|…" as exact phrases
                               (precise: the article is cited next to the law name)
                "cooccur"    — [law] AND "361-ე მუხლის|…" anywhere (higher recall,
                               catches "ამავე კოდექსის 361-ე მუხლის"; may pick up the
                               same article number of another law in the decision)
                "classifier" — litigation-type ids (Criminal Code / Administrative
                               Offences Code only; exact, independent of wording)
                "auto"       — phrase, plus classifier when law is the Criminal Code
                               or the Administrative Offences Code
            part: with classifier modes, restrict to one part of the article
            all_forms: try all 12 case endings instead of the 8 frequent ones
            instance_id / case_category_id / date_from / date_to: extra filters
            skip / take: pagination over the merged result

        Returns:
            search_any() result + "search_mode", "phrase", "litigation_types".
            Coverage: 2018-01 … 2020-04 only.
        """
        ordinal = self._article_ordinal(article)
        law = " ".join(law.split())
        endings = self.STEM_ENDINGS if all_forms else self.ARTICLE_ENDINGS
        forms = [f"{ordinal} მუხლ{e}" for e in endings]
        common = dict(instance_id=instance_id, case_category_id=case_category_id,
                      date_from=date_from, date_to=date_to)

        lt_category = None
        if "სისხლის სამართლის კოდექს" in law and "საპროცესო" not in law:
            lt_category = 1
        elif "სამართალდარღვევათა კოდექს" in law:
            lt_category = 3
        if mode == "auto":
            mode = "phrase+classifier" if lt_category else "phrase"

        variants, litigation_types = [], []
        if "phrase" in mode:
            variants += [dict(common, texts=[f"{law} {f}".strip()]) for f in forms]
        elif mode == "cooccur":
            variants += [dict(common, texts=([law, f] if law else [f])) for f in forms]
        if "classifier" in mode:
            if not lt_category:
                return {"error": "classifier mode needs law = Criminal Code or "
                                 "Administrative Offences Code (genitive form)"}
            litigation_types = self.litigation_types_for_article(
                article, part=part, case_category_id=lt_category)
            variants += [dict(common, litigation_type_id=lt["id"]) for lt in litigation_types]
        if not variants:
            return {"error": f"Unknown mode '{mode}' — use auto, phrase, cooccur or classifier"}
        res = self.search_any(variants, skip=skip, take=take)
        if "error" not in res:
            res.update(search_mode=mode, phrase=f"{law} {ordinal} მუხლ…".strip(),
                       litigation_types=litigation_types,
                       coverage="ecd.court.ge holds decisions dated 2018-01-01 … 2020-04-30 only")
        return res

    def get_decision_text(self, decision_document_id: int, instance_id: int) -> dict:
        """
        POST /Decision/DecisionDocumentText — full decision text.

        Returns:
            {"text": str, "id": int, "instance_id": int}
        """
        payload = {"InstanceId": instance_id, "DecisionDocumentId": decision_document_id}
        res = self._post_json("/Decision/DecisionDocumentText", payload)
        if "error" in res:
            return res
        if not res.get("success"):
            return {"error": "Server error", "raw": res}
        data = res.get("data") or {}
        raw_data = data.get("RawData")
        text = _html_to_text(raw_data) if raw_data else ""
        return {
            "id": decision_document_id,
            "instance_id": instance_id,
            "text": text,
            "pdf_url": f"{self.BASE}/Decision/DecisionDocumentPdf?InstanceId={instance_id}&DecisionDocumentId={decision_document_id}",
        }

    def get_decision(self, decision_document_id: int, instance_id: int) -> dict:
        """Alias for get_decision_text — full decision content."""
        return self.get_decision_text(decision_document_id, instance_id)

    def decision_pdf_url(self, decision_document_id: int, instance_id: int) -> str:
        return f"{self.BASE}/Decision/DecisionDocumentPdf?InstanceId={instance_id}&DecisionDocumentId={decision_document_id}"

    # ── Court acts (სასამართლო აქტები) ────────────────────────

    def verify_act(self, barcode: str, case_no: str) -> dict:
        """
        POST /FinalDocument/FinalDecisionDocument — verify a court act.

        Requires BOTH the document barcode (შტრიხკოდი) and case number.

        Returns:
            {"document_name": str, "pdf_url": str} or {"error": ...}
        """
        payload = {"BarcodeText": barcode, "CaseNo": case_no}
        res = self._post_json("/FinalDocument/FinalDecisionDocument", payload)
        if "error" in res:
            return res
        if not res.get("success"):
            return {"error": "Server error", "raw": res}
        data = res.get("data")
        return {
            "document_name": (data or {}).get("DocumentName") if isinstance(data, dict) else None,
            "pdf_url": self.act_pdf_url(barcode, case_no),
        }

    def act_pdf_url(self, barcode: str, case_no: str) -> str:
        return (f"{self.BASE}/FinalDocument/FinalDocumentPdf"
                f"?BarcodeText={urllib.parse.quote(barcode)}"
                f"&CaseNo={urllib.parse.quote(case_no)}")


# ── supremecourt.ge ───────────────────────────────────────────

class SupremeCourtAPI(_BaseAPI):
    """Georgia's Supreme Court — cassation decisions."""

    BASE = "https://www.supremecourt.ge"

    PALATA = {
        0: "ადმინისტრაციული",
        1: "სამოქალაქო",
        2: "სისხლის",
    }

    # 'shedegi' (result) codes — hardcoded from the search form
    RESULT_CODES = {
        "1": "დატოვებულია განუხილველად", "2": "დატოვებულია უცვლელად",
        "16": "შეწყდა", "17": "გაუქმდა და შეწყდა",
        "18": "გაუქმდა და დაუბრუნდა სასამართლოს",
        "19": "გაუქმდა და მიღებულია ახალი გადაწყვეტილება",
        "20": "დაუშვებლად იქნა ცნობილი",
        "21": "გაუქმდა და დაუბრუნდა ხელახლა განსახილველად",
        "22": "დამთავრდა მორიგებით", "23": "დაუბრუნდა ადმინისტრაციულ ორგანოს",
        "30": "გადაეცა სამოქალაქო პალატას",
        "31": "განსჯადობით გადაეცა საქალაქო (რაიონულ) სასამართლოს",
        "32": "საკასაციო საჩივარი ცნობილია დაუშვებლად",
        "33": "დატოვებულია უცვლელად", "34": "შეიცვალა კვალიფიკაცია",
        "35": "შეიცვალა სასჯელი", "36": "შეიცვალა კვალიფიკაცია და სასჯელი",
        "37": "შეიცვალა კვალიფიკაცია დამძიმებისაკენ",
        "38": "შეიცვალა სასჯელი დამძიმებისაკენ",
        "39": "შეიცვალა კვალიფიკაცია და სასჯელი დამძიმებისაკენ",
        "40": "შეიცვალა კვალიფიკაცია სიმსუბუქისკენ და სასჯელი დამძიმდა",
        "41": "შეიცვალა კვალიფიკაცია სიმძიმისკენ და სასჯელი შემსუბუქდა",
        "42": "გაუქმდა გამამტყუნებელი განაჩენი და გამოტანილი იქნა გამამართლებელი",
        "43": "გაუქმდა გამამართლებელი განაჩენი და გამოტანილ იქნა გამამტყუნებელი",
        "44": "განაჩენი გაუქმდა და საქმე შეწყდა",
        "45": "განაჩენი გაუქმდა და საქმე შეწყდა წარმოებით",
        "46": "განაჩენი გაუქმდა და საქმე გადაეცა სასამართლოს ხელახლა განსახილველად",
        "47": "გაუქმდა განაჩენი სამოქალაქო ნაწილში და საქმე ამ ნაწილში ხელახლა განსახილველად გადაეცა სასამართლოს",
        "48": "განაჩენი შეიცვალა სამოქალაქო ნაწილში",
        "49": "შესაბამისობაში მოვიდა ახალ კოდექსთან",
        "50": "ამნისტიის საფუძველზე შეიცვალა სასჯელი",
        "51": "ამნისტიის საფუძველზე განთავისუფლდა",
        "52": "შეწყდა სისხლის სამართლებრივი დევნა შეწყალების გამო",
        "53": "შეწყალების საფუძველზე შეიცვალა სასჯელი",
        "54": "გაუქმდა სააპელაციო სასამართლოს განაჩენი",
        "55": "განაჩენი გაუქმდა და საქმე დაუბრუნდა დამატებით გამოძიებას",
        "56": "საქმე დაუბრუნდა განუხილველად საჩივრის უკან გათხოვის გამო",
        "57": "საკასაციო საჩივარი დატოვებულია განუხილველად ხარვეზის შეუვსებლობის გამო",
        "58": "საკასაციო საჩივარი დატოვებულია განუხილველად კასატორის გამოუცხადებლობის გამო",
        "59": "საქმე დაბრუნდა როგორც არასწორად შემოსული",
        "60": "განჩინება შეიცვალა", "61": "დაკმაყოფილდა", "62": "არ დაკმაყოფილდა",
        "63": "დასაშვებად იქნა მიჩნეული", "64": "ნაწილობრივ დაკმაყოფილდა",
        "65": "უარი ეთქვა", "66": "საჩივარი არ დაკმაყოფილდა",
        "67": "საჩივარი დაკმაყოფილდა",
        "68": "სასჯელის აღსრულება გადაივადა გამოჯანმრთელებამდე",
        "69": "შეიცვალა სასჯელი შემსუბუქებისკენ",
    }

    # 'sSaxe' (appeal type) codes
    APPEAL_TYPE_CODES = {
        "1": "საკასაციო საჩივარი", "2": "კერძო საჩივარი",
        "3": "ახალად აღმოჩენილი გარემოებები", "7": "განმარტება",
        "8": "ბათილად ცნობა", "9": "განსჯადობა", "10": "უზრუნველყოფა",
        "11": "შუამდგომლობა", "12": "საზედამხედველო", "13": "განცხადება",
        "14": "დადგენილება", "15": "შუამდგომლობა", "16": "საზედამხედველო",
    }

    @staticmethod
    def _field(block: str, label: str) -> str:
        m = re.search(label + r":?</span>\s*([^<]+)", block)
        return m.group(1).strip() if m else ""

    def _parse_cases(self, html: str, palata: int) -> dict:
        if html.startswith("ERROR"):
            return {"error": html}
        total_m = re.search(r"სულ მოიძებნა\s*(\d+)", html)
        total = int(total_m.group(1)) if total_m else 0

        blocks = re.split(r'<div class="cases clearfix">', html)[1:]
        cases = []
        for block in blocks:
            id_m = re.search(r"seeMore\((\d+),(\d+)\)", block) or \
                   re.search(r"/ka/fullcase/(\d+)/(\d+)", block)
            if not id_m:
                continue
            case_id = int(id_m.group(1))
            case_palata = int(id_m.group(2))
            cases.append({
                "id": case_id,
                "palata": case_palata,
                "chamber": self.PALATA.get(case_palata, ""),
                "authority_level": AUTHORITY_SUPREME,
                "case_number": self._field(block, "საქმის ნომერი"),
                "date": self._field(block, "თარიღი"),
                "subject": self._field(block, "დავის საგანი"),
                "result": self._field(block, "შედეგი"),
                "appeal_type": self._field(block, "საჩივრის სახე"),
                "url": f"{self.BASE}/ka/fullcase/{case_id}/{case_palata}",
                "download_url": f"{self.BASE}/ka/download/{case_id}/{case_palata}",
            })
        return {"total": total, "palata": palata,
                "chamber": self.PALATA.get(palata, ""), "cases": cases}

    def search_cases(self, palata: int = 1, page: int = 1,
                     texts: Optional[list] = None, case_number: str = "",
                     date_from: str = "", date_to: str = "", judge: str = "",
                     result: str = "", appeal_type: str = "",
                     category: str = "", kind: str = "") -> dict:
        """
        GET /ka/getCases — Supreme Court cassation search.

        Args:
            palata: 0=ადმინისტრაციული, 1=სამოქალაქო, 2=სისხლის
            page: page number (1-indexed)
            texts: list of full-text search terms (up to 6)
            case_number: case number (e.g. 'ას-1374-2025')
            date_from / date_to: 'YYYY/MM/DD'
            judge: judge name
            result: 'shedegi' code (see RESULT_CODES)
            appeal_type: 'sSaxe' code (see APPEAL_TYPE_CODES)
            category / kind: category & kind codes (from caseGetCategory)

        Returns:
            {"total": int, "cases": [...]}
        """
        texts = texts or []
        params = {
            "palata": palata, "page": page,
            "caseNumber": case_number,
            "tarigiDan": date_from, "tarigiMde": date_to,
            "judgeH": judge, "shedegi": result, "sSaxe": appeal_type,
            "sCategory": category, "sKind": kind,
        }
        for i, t in enumerate(texts[:6]):
            key = "fulltext" if i == 0 else f"fulltext{i}"
            if t:
                params[key] = t
        html = self._get("/ka/getCases", params)
        return self._parse_cases(html, palata)

    def get_case(self, case_id: int, palata: int) -> dict:
        """
        GET /ka/fullcase/{id}/{palata} — full decision text.

        Returns:
            {"id": int, "palata": int, "url": str, "text": str}
        """
        html = self._get(f"/ka/fullcase/{case_id}/{palata}")
        if html.startswith("ERROR"):
            return {"error": html}
        m = re.search(r'id="modalBody">(.*?)</div>', html, re.DOTALL)
        text = _html_to_text(m.group(1)) if m else _html_to_text(html)
        return {
            "id": case_id,
            "palata": palata,
            "chamber": self.PALATA.get(palata, ""),
            "authority_level": AUTHORITY_SUPREME,
            "url": f"{self.BASE}/ka/fullcase/{case_id}/{palata}",
            "download_url": f"{self.BASE}/ka/download/{case_id}/{palata}",
            "text": text,
        }

    def download_url(self, case_id: int, palata: int) -> str:
        return f"{self.BASE}/ka/download/{case_id}/{palata}"

    # ── Grand Chamber (დიდი პალატა) ──────────────────────────

    GRAND_CHAMBER_CATEGORIES = {
        "civil": "samoqalaqo-samartlis-saqmeebze-didi-palata",
        "criminal": "siskhlis-samartlis-saqmeebze-didi-palata",
        "admin": "administratsiul-da-skhva-kategoriis-saqmeebze-didi-palata",
        "norms": "didi-palatis-gadatsyvetilebebshi-gamoyenebul-normata-ganmartebebi",
    }

    def _parse_pdf_list(self, html: str) -> list:
        """Extract PDF links (title + url) from a grand-chamber/decisions page."""
        items = []
        for m in re.finditer(r'<a href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
            href = m.group(1)
            if "/files/upload-file/" in href or "/uploads/files/" in href:
                url = href if href.startswith("http") else self.BASE + href
                items.append({"title": _html_to_text(m.group(2)), "url": url})
        return items

    def grand_chamber(self, category: str = "civil") -> dict:
        """
        Grand Chamber decisions (binding on all common courts).

        The Grand Chamber's legal interpretation of a norm is mandatory for
        every common court — the highest authority below the Constitution/law.

        Args:
            category: 'civil' | 'criminal' | 'admin' | 'norms'
                'norms' = compiled norm interpretations used in Grand Chamber
                         decisions (the ratio decidendi compilation).

        Returns:
            {"category": str, "authority_level": 5, "items": [{"title", "url"}]}
            Items are PDFs — the title contains date + case number + summary.
        """
        slug = self.GRAND_CHAMBER_CATEGORIES.get(category)
        if not slug:
            return {"error": f"Unknown category: {category}. "
                             f"Use: {list(self.GRAND_CHAMBER_CATEGORIES)}"}
        html = self._get(f"/decisions-grand-chamber/{slug}")
        if html.startswith("ERROR"):
            return {"error": html}
        return {
            "category": category,
            "authority_level": AUTHORITY_GRAND_CHAMBER,
            "items": self._parse_pdf_list(html),
        }


# ── CLI Test ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 60)
    print("COURT PRACTICE API — LIVE TEST")
    print("=" * 60)

    ecd = EcdCourtAPI()
    sc = SupremeCourtAPI()

    # 1. ECD classifiers
    print("\n[ecd] Instances:")
    inst = ecd.get_instances()
    if "error" not in inst:
        for i in inst.get("data", []):
            print(f"  {i.get('Id')} | {i.get('Name')} | {i.get('Fullname')}")

    # 2. ECD search
    print("\n[ecd] Search decisions (first 5, latest):")
    r = ecd.search_decisions(take=5)
    if "error" not in r:
        print(f"  Total: {r['total']}")
        for it in r["items"]:
            print(f"  [{it['authority_level']}] {it['date']} | {it['court']} | "
                  f"{it['case_number']} | {it['decision_type']}")

    # 3. ECD full text
    print("\n[ecd] Decision text (sample):")
    if "error" not in r and r["items"]:
        first = r["items"][0]
        txt = ecd.get_decision(first["id"], first["instance_id"])
        if "error" not in txt:
            print(f"  Chars: {len(txt['text'])}")
            print(f"  Preview: {txt['text'][:120]}")

    # 3b. ECD full-text semantics + article search
    print("\n[ecd] Full-text semantics (stem vs phrase):")
    for t in (["361-ე მუხლ"], ["361-ე მუხლის"], ["361-ე მუხლ*"], ["ალიმენტ"]):
        rr = ecd.search_decisions(texts=t, take=1)
        print(f"  {t} → {rr.get('total')}" + ("" if "total_exact" not in rr
                                               else f" (exact={rr['total_exact']})"))
    print("\n[ecd] search_article: Civil Code 361 / Criminal Code 177:")
    for art, law in (("361", "სამოქალაქო კოდექსის"), ("177", "სისხლის სამართლის კოდექსის")):
        ra = ecd.search_article(art, law=law, take=3)
        if "error" not in ra:
            print(f"  {law} {art}: ≥{ra['total']} (≤{ra['total_max']}) mode={ra['search_mode']} "
                  f"classifier ids={len(ra['litigation_types'])}")
            for it in ra["items"]:
                print(f"    [{it['authority_level']}] {it['date']} | {it['case_number']} | {it['court']}")

    # 4. Supreme search
    print("\n[supreme] Cassation search (palata=1):")
    sr = sc.search_cases(palata=1, page=1)
    if "error" not in sr:
        print(f"  Total: {sr['total']}")
        for c in sr["cases"][:3]:
            print(f"  {c['date']} | {c['case_number']} | {c['result']} | "
                  f"{c['subject'][:40]}")

    # 5. Supreme full text
    print("\n[supreme] Full case (sample):")
    if "error" not in sr and sr["cases"]:
        c0 = sr["cases"][0]
        full = sc.get_case(c0["id"], c0["palata"])
        if "error" not in full:
            print(f"  Chars: {len(full['text'])}")
            print(f"  Preview: {full['text'][:120]}")

    print("\nTest complete!")

    # 6. Grand Chamber
    print("\n[supreme] Grand Chamber (civil):")
    gc = sc.grand_chamber("civil")
    if "error" not in gc:
        for it in gc["items"]:
            print(f"  {it['title'][:70]}")
            print(f"    {it['url']}")
    print("\n[supreme] Grand Chamber norms:")
    gn = sc.grand_chamber("norms")
    if "error" not in gn:
        for it in gn["items"]:
            print(f"  {it['title'][:70]}  →  {it['url']}")

    print("\nAll tests complete!")
