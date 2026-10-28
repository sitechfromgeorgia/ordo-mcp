#!/usr/bin/env python3
"""
research.py — cross-source legal research on top of the stdlib wrappers
========================================================================
Two things a lawyer's assistant needs that no single source provides:

  * verify_citation()  — does the quoted text really appear in the cited
                         article / decision? (anti-hallucination check)
  * research_article() — one article of a law + the court practice that
                         cites it, ordered by authority_level

Zero dependencies (stdlib only). Wrappers are passed in, not created here,
so the MCP server keeps one rate-limited client per site.

Usage:
    from research import LegalResearch
    r = LegalResearch(MatsneAPI(), EcdCourtAPI(), SupremeCourtAPI(),
                      ConstitutionalCourtAPI(), EchrAPI())
    r.verify_citation("ვალდებულება უნდა შესრულდეს ჯეროვნად",
                      source="matsne", law="სამოქალაქო კოდექსი", article="361")
    r.research_article("361", law="სამოქალაქო კოდექსი")
"""

import difflib

# Quote/dash variants normalized before comparing
_CHAR_MAP = str.maketrans({
    "„": '"', "“": '"', "”": '"', "«": '"', "»": '"', "'": '"', "‘": '"', "’": '"',
    "–": "-", "—": "-", "‑": "-", "\xa0": " ",
})


def _normalize_with_map(s: str) -> tuple[str, list[int]]:
    """Lowercase, unify quotes/dashes, collapse whitespace; keep a map back to s."""
    out, idx, prev_space = [], [], True
    for i, ch in enumerate(s.translate(_CHAR_MAP).lower()):
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def verify_quote(text: str, quote: str, context: int = 200) -> dict:
    """
    Locate `quote` in `text`, tolerating whitespace/quote-style differences.

    Returns:
        {"verdict": "exact"|"close"|"partial"|"not_found", "similarity": float,
         "matched_text": str (the source's own wording), "context": str}

        exact     — the quote appears verbatim (after normalization)
        close     — ≥ 85% similar: same passage, wording differs — cite matched_text
        partial   — ≥ 60% similar: related passage, the quote is NOT accurate
        not_found — nothing similar; the quote should not be attributed to this source
    """
    nt, tmap = _normalize_with_map(text)
    nq = _normalize_with_map(quote)[0].strip()
    if len(nq) < 10:
        return {"error": "Quote too short to verify (min 10 characters)"}
    if not nt:
        return {"error": "Source text is empty"}

    def snippet(start: int, end: int) -> tuple[str, str]:
        a, b = tmap[start], tmap[min(end, len(tmap)) - 1] + 1
        return text[a:b], text[max(0, a - context):b + context]

    pos = nt.find(nq)
    if pos >= 0:
        matched, ctx = snippet(pos, pos + len(nq))
        return {"verdict": "exact", "similarity": 1.0, "occurrences": nt.count(nq),
                "matched_text": matched, "context": ctx}

    # Fuzzy: coarse scan with a sliding window, then refine around the best hit.
    n = len(nq)
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(nq)
    step = max(10, n // 5)

    def scan(starts) -> tuple[float, int]:
        best = (0.0, 0)
        for s in starts:
            sm.set_seq1(nt[s:s + n])
            if sm.real_quick_ratio() <= best[0] or sm.quick_ratio() <= best[0]:
                continue
            r = sm.ratio()
            if r > best[0]:
                best = (r, s)
        return best

    ratio, start = scan(range(0, max(1, len(nt) - n + 1), step))
    ratio, start = max((ratio, start),
                       scan(range(max(0, start - step), min(len(nt), start + step + 1), 2)))
    verdict = "close" if ratio >= 0.85 else "partial" if ratio >= 0.6 else "not_found"
    matched, ctx = snippet(start, start + n)
    result = {"verdict": verdict, "similarity": round(ratio, 3)}
    if verdict != "not_found":
        result.update(matched_text=matched, context=ctx)
    return result


def article_phrase(key: str) -> str:
    """
    Georgian citation form used in court decisions: "361-ე მუხლ", "მე-20 მუხლ",
    "პირველი მუხლ", "151¹ მუხლ" (stem without case ending, for full-text search).
    """
    number, _, sup = key.partition("^")
    if sup:
        return f"{number}{sup.translate(str.maketrans('0123456789', '⁰¹²³⁴⁵⁶⁷⁸⁹'))} მუხლ"
    n = int(number)
    if n == 1:
        return "პირველი მუხლ"
    # Georgian ordinals take "მე-…" when the number ends in a round vigesimal word
    if n <= 20 or n % 20 == 0:
        return f"მე-{n} მუხლ"
    return f"{n}-ე მუხლ"


class LegalResearch:
    """Cross-source helpers: citation verification + article-level research."""

    # matsne doc_id → (genitive law name used in decisions, Supreme Court palatas)
    # palata: 0=administrative, 1=civil, 2=criminal
    LAW_CONTEXT = {
        31702: ("სამოქალაქო კოდექსის", [1]),
        29962: ("სამოქალაქო საპროცესო კოდექსის", [1]),
        1155567: ("შრომის კოდექსის", [1]),
        16426: ("სისხლის სამართლის კოდექსის", [2]),
        90034: ("სისხლის სამართლის საპროცესო კოდექსის", [2]),
        16270: ("ზოგადი ადმინისტრაციული კოდექსის", [0]),
        16492: ("ადმინისტრაციული საპროცესო კოდექსის", [0]),
        28216: ("ადმინისტრაციულ სამართალდარღვევათა კოდექსის", [0]),
        1043717: ("საგადასახადო კოდექსის", [0]),
        30346: ("კონსტიტუციის", []),
    }

    def __init__(self, matsne, ecd, supreme, constcourt, echr):
        self.matsne = matsne
        self.ecd = ecd
        self.supreme = supreme
        self.constcourt = constcourt
        self.echr = echr

    def _source_text(self, source: str, law: str = "", doc_id: int = 0, article: str = "",
                     part=None, as_of_date: str = "", decision_document_id: int = 0,
                     instance_id: int = 0, case_id: int = 0, palata: int = 1,
                     legal_id: int = 0, itemid: str = "") -> dict:
        """Fetch the text a quote is attributed to: {"text", "url", "label"} or {"error"}."""
        if source == "matsne":
            if article:
                art = self.matsne.get_article(article, law=law, doc_id=doc_id, part=part,
                                              as_of_date=as_of_date)
                if "error" in art:
                    return art
                text = art.get("part_text") or art["text"]
                label = f"{art.get('title', '')} — {art['heading']}"
                if art.get("part_text"):
                    label += f", ნაწილი {part}"
                return {"text": text, "url": art["url"], "label": label,
                        "version_date": art.get("version_date")}
            if not doc_id:
                resolved = self.matsne.resolve_law(law)
                if "error" in resolved:
                    return resolved
                doc_id = resolved["doc_id"]
            publication = None
            if as_of_date:
                picked = self.matsne.version_for_date(doc_id, as_of_date)
                if "error" in picked:
                    return picked
                publication = picked["publication"]
            full = self.matsne.get_full_text(doc_id, publication)
            if "error" in full:
                return full
            query = f"?publication={publication}" if publication is not None else ""
            return {"text": full["text"], "label": f"matsne document {doc_id}",
                    "url": f"{self.matsne.BASE}/ka/document/view/{doc_id}{query}"}
        if source == "ecd":
            doc = self.ecd.get_decision(decision_document_id, instance_id)
            return doc if "error" in doc else {
                "text": doc["text"], "url": doc["pdf_url"],
                "label": f"ecd decision {decision_document_id} (instance {instance_id})"}
        if source == "supreme":
            doc = self.supreme.get_case(case_id, palata)
            return doc if "error" in doc else {
                "text": doc["text"], "url": doc["url"], "label": f"Supreme Court case {case_id}"}
        if source == "constitutional":
            doc = self.constcourt.get_act(legal_id)
            return doc if "error" in doc else {
                "text": doc["text"], "url": doc["url"], "label": doc.get("title") or f"act {legal_id}"}
        if source == "echr":
            doc = self.echr.get_case(itemid)
            return doc if "error" in doc else {
                "text": doc["text"], "url": doc["url"], "label": f"ECHR {itemid}"}
        return {"error": f"Unknown source '{source}' — use matsne, ecd, supreme, constitutional or echr"}

    def verify_citation(self, quote: str, source: str, **ids) -> dict:
        """
        Check that `quote` really appears in the cited source.

        source + ids:
            matsne         — law or doc_id, optional article / part / as_of_date
            ecd            — decision_document_id + instance_id
            supreme        — case_id + palata
            constitutional — legal_id
            echr           — itemid

        Returns verify_quote()'s verdict plus the source label and URL.
        """
        src = self._source_text(source, **ids)
        if "error" in src:
            return {"verdict": "source_unavailable", "error": src["error"]}
        result = verify_quote(src["text"], quote)
        result["source"] = {k: v for k, v in src.items() if k != "text" and v}
        if result.get("verdict") in ("partial", "not_found"):
            result["advice"] = ("Do not attribute this quote to the source as written. "
                                "Use matched_text (if any) or re-read the source.")
        return result

    def research_article(self, article: str, law: str = "", doc_id: int = 0,
                         as_of_date: str = "", max_results: int = 5) -> dict:
        """
        One article + the court practice that cites it, strongest authority first.

        Searches the Constitutional Court (intext), the Supreme Court (full text,
        chamber chosen by the law), and common courts (ecd full text) for the
        Georgian citation form "<law genitive> <N>-ე მუხლ".

        Returns:
            {"article": {...get_article...}, "search_phrase", "practice": [
             {"court", "authority_level", "total", "items"}], "notes"}
        """
        art = self.matsne.get_article(article, law=law, doc_id=doc_id, as_of_date=as_of_date)
        if "error" in art:
            return {"error": art["error"], "article": art}
        doc_id = art["doc_id"]
        phrase = article_phrase(art["article"])
        law_gen, palatas = self.LAW_CONTEXT.get(doc_id, (None, [0, 1, 2]))
        if not law_gen:
            # e.g. „საჯარო სამსახურის შესახებ“ საქართველოს კანონის 5-ე მუხლ
            title = art.get("title", "").replace("საქართველოს კანონი", "").strip(' „“"')
            law_gen = title
        full_phrase = f"{law_gen} {phrase}".strip()
        practice, notes = [], []

        cc = self.constcourt.search_acts(intext=full_phrase)
        if "error" in cc:
            notes.append(f"constitutional: {cc['error']}")
        else:
            practice.append({"court": "საკონსტიტუციო სასამართლო", "authority_level": 6,
                             "total": cc.get("count", 0), "items": cc.get("items", [])[:max_results]})

        for palata in palatas:
            sc = self.supreme.search_cases(palata=palata, texts=[phrase, law_gen])
            if "error" in sc:
                notes.append(f"supreme palata {palata}: {sc['error']}")
                continue
            practice.append({"court": f"უზენაესი სასამართლო — {sc.get('chamber', palata)}",
                             "authority_level": 4, "total": sc.get("total", 0),
                             "items": sc.get("cases", [])[:max_results]})

        ecd = self.ecd.search_decisions(texts=[full_phrase], take=max_results)
        if "error" in ecd:
            notes.append(f"common courts: {ecd['error']}")
        else:
            items = sorted(ecd.get("items", []), key=lambda i: -(i.get("authority_level") or 0))
            practice.append({"court": "საერთო სასამართლოები (ecd.court.ge)",
                             "authority_level": max((i.get("authority_level") or 0 for i in items), default=2),
                             "total": ecd.get("total", 0), "items": items})

        practice.sort(key=lambda p: -p["authority_level"])
        notes.append("Grand Chamber decisions (authority 5) are PDFs without text search — "
                     "check supreme_grand_chamber, esp. category 'norms'.")
        notes.append("Full-text hits mention the article; read each decision before relying on it "
                     "(it may cite the article only in passing).")
        return {"article": art, "search_phrase": full_phrase, "practice": practice, "notes": notes}


# ── CLI Test ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    from matsne_api import MatsneAPI
    from court_api import EcdCourtAPI, SupremeCourtAPI
    from higher_courts_api import ConstitutionalCourtAPI, EchrAPI

    r = LegalResearch(MatsneAPI(), EcdCourtAPI(), SupremeCourtAPI(),
                      ConstitutionalCourtAPI(), EchrAPI())

    print("=" * 60)
    print("🔬 LEGAL RESEARCH — TEST")
    print("=" * 60)

    print("\n✅ Citation phrases:", [article_phrase(k) for k in ("1", "5", "20", "40", "361", "1507^1")])

    print("\n🔍 Verify quotes against Civil Code art. 361:")
    for q in ("ვალდებულება უნდა შესრულდეს ჯეროვნად, კეთილსინდისიერად, დათქმულ დროსა და ადგილას",
              "ვალდებულება უნდა შესრულდეს კეთილსინდისიერად და დროულად, დათქმულ ადგილას",
              "მოვალე პასუხს აგებს კრედიტორის წინაშე ნებისმიერი ზიანისთვის"):
        v = r.verify_citation(q, source="matsne", law="სამოქალაქო კოდექსი", article="361")
        print(f"  {v['verdict']:>9} ({v.get('similarity')}) ← {q[:50]}")

    print("\n📚 Research Civil Code art. 361:")
    res = r.research_article("361", law="სამოქალაქო კოდექსი", max_results=2)
    print(f"  Phrase: {res.get('search_phrase')}")
    for p in res.get("practice", []):
        print(f"  [{p['authority_level']}] {p['court']}: {p['total']} hits")

    print("\n✅ Research test complete!")
