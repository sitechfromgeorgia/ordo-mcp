#!/usr/bin/env python3
"""
MCP server for Georgia's legal ecosystem.

Sources:
  - matsne.gov.ge    - legislation (საკანონმდებლო მაცნე)
  - ecd.court.ge     - common courts' decisions & acts (საქმისწარმოების სისტემა)
  - supremecourt.ge  - Supreme Court cassation + Grand Chamber decisions
  - constcourt.ge    - Constitutional Court acts
  - HUDOC            - European Court of Human Rights
  - info.parliament.ge - bills (კანონპროექტები) and their legislative history
Plus cross-source research (research.py): citation verification and
article-level practice search, and MCP prompts for common legal workflows.

Exposes search / full-text / classifiers / verification tools so any AI agent
can research Georgian law and court practice end-to-end.
"""

import json

from mcp.server.fastmcp import FastMCP

from matsne_api import MatsneAPI
from court_api import EcdCourtAPI, SupremeCourtAPI
from higher_courts_api import ConstitutionalCourtAPI, EchrAPI
from parliament_api import ParliamentAPI
from research import LegalResearch

matsne = MatsneAPI()
ecd = EcdCourtAPI()
supreme = SupremeCourtAPI()
constcourt = ConstitutionalCourtAPI()
echr = EchrAPI()
parliament = ParliamentAPI()
research = LegalResearch(matsne, ecd, supreme, constcourt, echr)

app = FastMCP(
    "matsne-ge",
    instructions=(
        "Georgia's legal ecosystem: legislation (matsne.gov.ge), common-court "
        "decisions and acts (ecd.court.ge), Supreme Court cassation + Grand "
        "Chamber (supremecourt.ge), Constitutional Court (constcourt.ge), and "
        "the European Court of Human Rights (HUDOC). "
        "Every court result carries an authority_level so you weigh precedent "
        "correctly: 7=ECHR (supranational), 6=Constitutional Court, 5=Supreme "
        "Grand Chamber (binding on all common courts), 4=Supreme cassation, "
        "3=appeals, 2=first instance. "
        "IMPORTANT: HUDOC (echr_* tools) rate-limits aggressively — make echr "
        "requests sequentially (one at a time, wait between calls) and never "
        "fire many in parallel; if a call returns an empty/error result, pause "
        "a few seconds and retry once. Verify every citation against the "
        "returned stable URL before relying on it. "
        "Legislation: cite articles via matsne_get_article (not by reading whole "
        "codes); when the facts happened in the past, pass as_of_date so you "
        "apply the version then in force; confirm an act is in force with "
        "matsne_check_status. Before quoting any law or decision in an answer, "
        "run verify_citation on the quote; research_article gathers an "
        "article's court practice in one call; parliament_* tools show pending "
        "bills that may change the law soon."
    ),
)


# ── Matsne (legislation) ─────────────────────────────────────

@app.tool(description="Quick keyword autocomplete search of legislation — fastest endpoint (~0.5s). Returns up to 50 results with id, title, and active/expired status.")
def matsne_suggest(text: str) -> list[dict]:
    return matsne.suggest(text)


@app.tool(description="Full-text legislative search with filters. Supports pagination, date range (DD/MM/YYYY), document type, issuer organ, and sort order.")
def matsne_search(
    page: int = 0,
    query: str = "",
    limit: int = 20,
    sort: str = "",
    issuer: str = "",
    doc_type: str = "",
    number: str = "",
    registration_code: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    return matsne.search(
        page=page, query=query, limit=limit, sort=sort,
        issuer=issuer, doc_type=doc_type, number=number,
        registration_code=registration_code,
        date_from=date_from, date_to=date_to,
    )


@app.tool(description="Get full legislative document: metadata (incl. status/expiry_date), text, structure tree, back-references, and comments in one call. Optional historical version: publication (0 = original text) or as_of_date (DD/MM/YYYY or YYYY-MM-DD). Text is cut at max_chars — for a single article use matsne_get_article instead of reading a whole code.")
def matsne_get_document(doc_id: int, publication: int | None = None,
                        as_of_date: str = "", max_chars: int = 20000) -> dict:
    if as_of_date:
        picked = matsne.version_for_date(doc_id, as_of_date)
        if "error" in picked:
            return picked
        publication = picked["publication"]
    doc = matsne.get_document(doc_id, publication)
    if as_of_date:
        doc["as_of_date"] = as_of_date
        doc["version"] = picked
    if max_chars and len(doc.get("text", "")) > max_chars:
        doc["text_total_chars"] = len(doc["text"])
        doc["text"] = doc["text"][:max_chars]
        doc["text_truncated"] = True
    return doc


@app.tool(description="Get ONE article of a law by human citation — e.g. article='361', law='სამოქალაქო კოდექსი' (or 'civil code', any grammatical case, or doc_id). Superscript articles as '1507¹' or '1507^1'. Optional part (ნაწილი number) extracts that part. as_of_date (DD/MM/YYYY or YYYY-MM-DD) returns the version in force on that date — use it whenever the facts happened in the past. lang='en' returns matsne's unofficial English translation (+ PDF link) when one exists. Returns text + stable URL to cite.")
def matsne_get_article(article: str, law: str = "", doc_id: int = 0, part: int | None = None,
                       as_of_date: str = "", publication: int | None = None,
                       lang: str = "ka") -> dict:
    return matsne.get_article(article, law=law, doc_id=doc_id, part=part,
                              publication=publication, as_of_date=as_of_date, lang=lang)


@app.tool(description="List all consolidated versions (რედაქციები) of a legislative document (publication index + date, 0 = original) and the amending acts behind each version (number, adoption/publication dates, link). Version dates are adoption dates of the amending act, not entry into force.")
def matsne_list_versions(doc_id: int) -> dict:
    return matsne.list_versions(doc_id)


@app.tool(description="Diff two versions of a legislative document article by article: which articles were added, removed, or modified, with a unified diff of each change. to_publication omitted = current version; article limits the diff to one article.")
def matsne_compare_versions(doc_id: int, from_publication: int, to_publication: int | None = None,
                            article: str = "") -> dict:
    return matsne.compare_versions(doc_id, from_publication, to_publication, article)


@app.tool(description="'Is this still good law?' for a legislative act: in force or expired (with expiry date), latest version, the most recent amending acts, and how many documents cite it. Check this before relying on any act.")
def matsne_check_status(doc_id: int) -> dict:
    return matsne.check_status(doc_id)


@app.tool(description="Get legislative document structure tree (hierarchical parts with Title and Anchor). Complex codes return deep nested trees.")
def matsne_get_tree(doc_id: int, part_id: int = 0) -> dict:
    return matsne.get_tree(doc_id, part_id)


@app.tool(description="List documents that cite or reference the given legislative document.")
def matsne_get_back_references(doc_id: int, part_id: int = 0) -> list:
    return matsne.get_back_references(doc_id, part_id)


@app.tool(description="Get comments on a legislative document or specific part.")
def matsne_get_comments(doc_id: int, part_id: int = 0) -> list:
    return matsne.get_comments(doc_id, part_id)


@app.tool(description="Get inline HTML fragment for a linked legislative document part.")
def matsne_get_linked(doc_id: int, anchor: str = "DOCUMENT:1;HEADER:1;") -> dict:
    return matsne.get_linked(doc_id, anchor)


@app.tool(description="Quick keyword search of active legislation using the suggest endpoint.")
def matsne_search_by_keyword(keyword: str, max_results: int = 10) -> list[dict]:
    return matsne.search_by_keyword(keyword, max_results)


@app.tool(description="Check if any legislative documents were published today.")
def matsne_has_today() -> str:
    return "yes" if matsne.has_today() else "no"


@app.tool(description="Get legislative documents published today (if any).")
def matsne_today_documents() -> list[dict]:
    return matsne.today_documents()


# ── Common courts (ecd.court.ge) ─────────────────────────────

@app.tool(description="Search common-court decisions. Filters: case number, decision date range (YYYY-MM-DD), instance (1=first, 2=appeals, 3=supreme), court code, case category, litigation type (dispute category/article/მუხლი), decision type, and full-text terms. Use court_classifiers to look up filter ids. Results carry authority_level.")
def court_search_decisions(
    case_no: str = "",
    date_from: str = "",
    date_to: str = "",
    instance_id: str = "",
    court_code: str = "",
    case_category_id: str = "",
    litigation_type_id: str = "",
    type_id: str = "",
    texts: list[str] = None,
    skip: int = 0,
    take: int = 20,
) -> dict:
    return ecd.search_decisions(
        case_no=case_no,
        date_from=date_from or None,
        date_to=date_to or None,
        instance_id=instance_id or "",
        court_code=court_code or "",
        case_category_id=case_category_id or "",
        litigation_type_id=litigation_type_id or "",
        type_id=type_id or "",
        texts=texts or [],
        skip=skip, take=take,
    )


@app.tool(description="Get the full text of a common-court decision by its DecisionDocumentId and InstanceId (both come from court_search_decisions results).")
def court_get_decision(decision_document_id: int, instance_id: int) -> dict:
    return ecd.get_decision(decision_document_id, instance_id)


@app.tool(description="Look up classifier dictionaries for decision search filters. kind: instances, courts, case_categories, litigation_types, decision_types. Pass instance_id and/or case_category_id to cascade.")
def court_classifiers(
    kind: str = "instances",
    instance_id: str = "",
    case_category_id: str = "",
    filter_text: str = "",
) -> dict:
    iid = instance_id or None
    cid = case_category_id or None
    if kind == "instances":
        return ecd.get_instances()
    if kind == "courts":
        return ecd.get_courts(iid)
    if kind == "case_categories":
        return ecd.get_case_categories(iid, filter_text)
    if kind == "litigation_types":
        return ecd.get_litigation_types(iid, cid, filter_text)
    if kind == "decision_types":
        return ecd.get_decision_types(iid, filter_text)
    return {"error": f"Unknown kind: {kind}"}


@app.tool(description="Verify a court act (სასამართლო აქტი) by its barcode (შტრიხკოდი) and case number. Returns the document name and a PDF download link.")
def court_verify_act(barcode: str, case_no: str) -> dict:
    return ecd.verify_act(barcode, case_no)


# ── Supreme Court (supremecourt.ge) ──────────────────────────

@app.tool(description="Search Supreme Court cassation decisions. palata: 0=administrative, 1=civil, 2=criminal. Filters: case number, date range (YYYY/MM/DD), judge, result code, appeal-type code, category/kind, and up to 6 full-text terms. Result codes are in the tool description of supreme_classifiers.")
def supreme_search(
    palata: int = 1,
    page: int = 1,
    texts: list[str] = None,
    case_number: str = "",
    date_from: str = "",
    date_to: str = "",
    judge: str = "",
    result: str = "",
    appeal_type: str = "",
    category: str = "",
    kind: str = "",
) -> dict:
    return supreme.search_cases(
        palata=palata, page=page, texts=texts or [],
        case_number=case_number, date_from=date_from, date_to=date_to,
        judge=judge, result=result, appeal_type=appeal_type,
        category=category, kind=kind,
    )


@app.tool(description="Get the full text of a Supreme Court cassation decision by case id and palata (both come from supreme_search results).")
def supreme_get_case(case_id: int, palata: int) -> dict:
    return supreme.get_case(case_id, palata)


@app.tool(description="Supreme Court result (shedegi) and appeal-type (sSaxe) codes for the supreme_search filters.")
def supreme_classifiers() -> dict:
    return {
        "result_codes": supreme.RESULT_CODES,
        "appeal_type_codes": supreme.APPEAL_TYPE_CODES,
        "palata": supreme.PALATA,
    }


@app.tool(description="Supreme Court GRAND CHAMBER decisions — the binding norm interpretations (authority_level 5, mandatory for all common courts). category: civil, criminal, admin, or norms (compiled norm interpretations). Returns PDF links; the title includes date + case number + summary.")
def supreme_grand_chamber(category: str = "civil") -> dict:
    return supreme.grand_chamber(category)


# ── Constitutional Court (constcourt.ge) ─────────────────────

@app.tool(description="Search Constitutional Court of Georgia judicial acts. Filters: fullsearch (name/number/result), intext (full-text inside act texts), number (e.g. 'N1/21/1727'), name, date range, quantity (10-100), sort. Results carry authority_level 6.")
def constitutional_search_acts(
    fullsearch: str = "",
    intext: str = "",
    number: str = "",
    name: str = "",
    date_from: str = "",
    date_to: str = "",
    quantity: int = 20,
    sort: str = "",
) -> dict:
    return constcourt.search_acts(
        fullsearch=fullsearch, intext=intext, number=number, name=name,
        date_from=date_from, date_to=date_to, quantity=quantity, sort=sort,
    )


@app.tool(description="Get the full text of a Constitutional Court act by its legal id (from constitutional_search_acts results).")
def constitutional_get_act(legal_id: int) -> dict:
    return constcourt.get_act(legal_id)


# ── European Court of Human Rights (HUDOC) ───────────────────

@app.tool(description="Search European Court of Human Rights case law (HUDOC). Filters: respondent state code (e.g. 'GEO' for Georgia), Convention article (e.g. '6', '8'), application number, free-text keyword, language. Results carry authority_level 7. NOTE: HUDOC rate-limits aggressively — call sequentially (not in parallel), and if a call returns empty/error, pause a few seconds and retry once.")
def echr_search(
    respondent: str = "",
    article: str = "",
    appno: str = "",
    text: str = "",
    language: str = "ENG",
    start: int = 0,
    length: int = 20,
) -> dict:
    return echr.search(
        respondent=respondent, article=article, appno=appno, text=text,
        language=language, start=start, length=length,
    )


@app.tool(description="Get the full text of an ECHR decision by its HUDOC itemid (from echr_search results). NOTE: HUDOC rate-limits — call sequentially and retry once if empty/error.")
def echr_get_case(itemid: str) -> dict:
    return echr.get_case(itemid)


# ── Parliament (info.parliament.ge) ──────────────────────────

@app.tool(description="Search bills in the Parliament of Georgia (კანონპროექტები), newest first — laws being drafted that may soon change the legislation. query matches bill/package names (Georgian, e.g. 'საგადასახადო კოდექს'); bill_type_id: 1=law draft, 11=organic law (codes that are organic laws, e.g. the Labour Code, are amended by type 11), 12=constitutional law, 2=parliament resolution. name_match=false means the hit came from another bill in the same package. Each result shows its current stage (I/II/III hearing, კანონი = adopted).")
def parliament_search_bills(query: str = "", bill_type_id: int | None = None,
                            page: int = 1, limit: int = 20) -> dict:
    return parliament.search_bills(query, bill_type_id=bill_type_id, page=page, limit=limit)


@app.tool(description="Full legislative history of one bill (id from parliament_search_bills): every stage (initiated version, I/II/III hearing, resolutions, adopted law) with dates, vote counts, committees, and downloadable files (draft text, explanatory note, current wording of the amended articles, committee conclusions).")
def parliament_get_bill(bill_id: int) -> dict:
    return parliament.get_bill(bill_id)


# ── Cross-source research ────────────────────────────────────

@app.tool(description="Anti-hallucination check: does the quoted text really appear in the cited source? source='matsne' (+ law or doc_id, optional article/part/as_of_date), 'ecd' (+ decision_document_id, instance_id), 'supreme' (+ case_id, palata), 'constitutional' (+ legal_id), 'echr' (+ itemid). Verdict: exact / close (same passage, wording differs — use matched_text) / partial / not_found (do not attribute). Run it on every quote before putting it in an answer.")
def verify_citation(quote: str, source: str, law: str = "", doc_id: int = 0,
                    article: str = "", part: int | None = None, as_of_date: str = "",
                    decision_document_id: int = 0, instance_id: int = 0,
                    case_id: int = 0, palata: int = 1, legal_id: int = 0,
                    itemid: str = "") -> dict:
    return research.verify_citation(
        quote, source, law=law, doc_id=doc_id, article=article, part=part,
        as_of_date=as_of_date, decision_document_id=decision_document_id,
        instance_id=instance_id, case_id=case_id, palata=palata,
        legal_id=legal_id, itemid=itemid,
    )


@app.tool(description="One-call research of a law article: its text (optionally as_of_date) plus the court practice citing it — Constitutional Court, Supreme Court (chamber chosen by the law) and common courts — ordered by authority_level, with hit counts. Search uses the Georgian citation form (e.g. 'სამოქალაქო კოდექსის 361-ე მუხლ'). Read the decisions before relying on them.")
def research_article(article: str, law: str = "", doc_id: int = 0,
                     as_of_date: str = "", max_results: int = 5) -> dict:
    return research.research_article(article, law=law, doc_id=doc_id,
                                     as_of_date=as_of_date, max_results=max_results)


# ── Prompts (legal workflows) ────────────────────────────────

_RULES = (
    "Rules: (1) Every legal statement needs a source with its URL. "
    "(2) Apply the law as in force when the facts happened — matsne_get_article with as_of_date; "
    "mention when the current text differs (matsne_compare_versions). "
    "(3) Confirm each act is in force (matsne_check_status). "
    "(4) Weigh practice by authority_level: 7 ECHR > 6 Constitutional Court > 5 Grand Chamber "
    "> 4 Supreme Court > 3 appeals > 2 first instance. "
    "(5) Run verify_citation on every quote before using it; never quote from memory. "
    "(6) If a source is unavailable, say so instead of guessing. "
    "Answer in the user's language (Georgian by default)."
)


@app.prompt(description="Legal memorandum: question → applicable law (correct version) → court practice → analysis → conclusion, with verified citations.")
def legal_memo(question: str, facts: str = "", facts_date: str = "") -> str:
    return (
        "Prepare a legal memorandum (იურიდიული მემორანდუმი) under Georgian law.\n\n"
        f"Question: {question}\n"
        f"Facts: {facts or '(none given — state the assumptions you make)'}\n"
        f"Date of the facts: {facts_date or '(not given — use the current law and say so)'}\n\n"
        "Steps: 1) identify the governing norms (matsne_suggest / matsne_search); "
        "2) read each article with matsne_get_article (as_of_date = date of the facts); "
        "3) research_article for each key article, then read the strongest decisions "
        "(constitutional_get_act, supreme_get_case, court_get_decision) and check "
        "supreme_grand_chamber; 4) check parliament_search_bills for pending changes; "
        "5) verify every quote with verify_citation.\n\n"
        "Structure: Question · Short answer · Applicable law · Court practice (by authority) · "
        "Analysis · Risks and open points · Sources (URL for each).\n\n" + _RULES
    )


@app.prompt(description="Case analysis in IRAC form (Issue, Rule, Application, Conclusion) for a fact pattern under Georgian law.")
def case_analysis(facts: str, facts_date: str = "", side: str = "") -> str:
    return (
        f"Analyse this case under Georgian law using IRAC.\n\nFacts: {facts}\n"
        f"Date of the facts: {facts_date or '(not given)'}\n"
        f"Perspective: {side or 'neutral'}\n\n"
        "For each legal issue: Issue → Rule (matsne_get_article, correct version) → "
        "Application (compare with research_article practice; distinguish unfavourable decisions) → "
        "Conclusion with likelihood. Finish with procedural notes: competent court, "
        "deadlines and evidence — cite the procedural code articles, do not state deadlines from memory.\n\n"
        + _RULES
    )


@app.prompt(description="What changed in a law between two dates, and what is pending in Parliament.")
def law_change_review(law: str, from_date: str, to_date: str = "") -> str:
    return (
        f"Review changes to '{law}' between {from_date} and {to_date or 'today'}.\n\n"
        "Steps: 1) matsne_check_status and matsne_list_versions; 2) pick the versions in force on both "
        "dates (matsne_get_article / version dates) and run matsne_compare_versions; 3) list the "
        "amending acts in that period with their dates; 4) parliament_search_bills for pending bills "
        "on this law and their current stage.\n\n"
        "Output: table of changed articles (old → new, one-line summary, amending act), "
        "practical impact, pending bills.\n\n" + _RULES
    )


# ── Resources ────────────────────────────────────────────────

@app.resource("matsne://document/{doc_id}")
def document_resource(doc_id: int) -> str:
    return json.dumps(matsne.get_document(doc_id), ensure_ascii=False, indent=2)


@app.resource("matsne://today")
def today_resource() -> str:
    return json.dumps(matsne.today_documents(), ensure_ascii=False, indent=2)


@app.resource("court://decision/{decision_document_id}/{instance_id}")
def court_decision_resource(decision_document_id: int, instance_id: int) -> str:
    return json.dumps(ecd.get_decision(decision_document_id, instance_id),
                      ensure_ascii=False, indent=2)


@app.resource("supreme://case/{case_id}/{palata}")
def supreme_case_resource(case_id: int, palata: int) -> str:
    return json.dumps(supreme.get_case(case_id, palata),
                      ensure_ascii=False, indent=2)


@app.resource("constitutional://act/{legal_id}")
def constitutional_act_resource(legal_id: int) -> str:
    return json.dumps(constcourt.get_act(legal_id), ensure_ascii=False, indent=2)


@app.resource("echr://case/{itemid}")
def echr_case_resource(itemid: str) -> str:
    return json.dumps(echr.get_case(itemid), ensure_ascii=False, indent=2)


def main():
    app.run()


if __name__ == "__main__":
    main()
