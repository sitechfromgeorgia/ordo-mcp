#!/usr/bin/env python3
"""
Ordo — MCP server for Georgia's legal ecosystem (Ordo ab chao: order out of legal chaos).

Sources:
  - matsne.gov.ge    - legislation (საკანონმდებლო მაცნე)
  - ecd.court.ge     - common courts' decisions & acts (საქმისწარმოების სისტემა)
  - supremecourt.ge  - Supreme Court cassation + Grand Chamber decisions
  - constcourt.ge    - Constitutional Court acts
  - HUDOC            - European Court of Human Rights
  - info.parliament.ge - bills (კანონპროექტები) and their legislative history
  - supremecourt.ge analytics - practice generalizations, recommendations,
                       explanations, decision collections, journal
  - administrative bodies - MoF tax dispute council, Revenue Service,
                       competition agency, Public Defender, procurement
                       disputes, Bar ethics commission
Plus procedural-deadline and court state-fee calculation from live norms
(deadlines.py, state_fee.py) and
PDF text extraction with legacy Georgian font repair (pdf_text.py), and the
public registries (registries_api.py): companies, real estate, liens, movable
pledges, lawyers, notaries, procurement supplier lists.
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
from supreme_practice_api import SupremePracticeAPI
from agencies_api import (TaxDisputeCouncilAPI, RevenueServiceDisputeAPI, TaxGuidanceAPI,
                          CompetitionAgencyAPI, PublicDefenderAPI, ProcurementDisputeAPI,
                          BarEthicsAPI)
from deadlines import DeadlineCalculator
from state_fee import FeeCalculator
from registries_api import (CompanyRegistryAPI, PropertyRegistryAPI, LienRegistryAPI,
                            MovablePledgeAPI, LawyerRegistryAPI, NotaryAPI, ProcurementListsAPI,
                            CompanyInfoAPI, GeostatRegisterAPI)
from pdf_text import extract_pdf_text, search_pdf

matsne = MatsneAPI()
ecd = EcdCourtAPI()
supreme = SupremeCourtAPI()
constcourt = ConstitutionalCourtAPI()
echr = EchrAPI()
parliament = ParliamentAPI()
research = LegalResearch(matsne, ecd, supreme, constcourt, echr)
supreme_practice = SupremePracticeAPI()
tax_disputes = TaxDisputeCouncilAPI()
rs_disputes = RevenueServiceDisputeAPI()
tax_guidance = TaxGuidanceAPI()
competition = CompetitionAgencyAPI()
ombudsman = PublicDefenderAPI()
procurement = ProcurementDisputeAPI()
bar_ethics = BarEthicsAPI()
deadline_calc = DeadlineCalculator()
fee_calc = FeeCalculator(deadlines=deadline_calc)
company_registry = CompanyRegistryAPI()
property_registry = PropertyRegistryAPI()
lien_registry = LienRegistryAPI()
pledge_registry = MovablePledgeAPI()
lawyer_registry = LawyerRegistryAPI()
notaries = NotaryAPI()
procurement_lists = ProcurementListsAPI()
companyinfo = CompanyInfoAPI()
geostat = GeostatRegisterAPI()

app = FastMCP(
    "ordo",
    instructions=(
        "Ordo — Georgia's legal ecosystem: legislation (matsne.gov.ge), common-court "
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
        "bills that may change the law soon. Deadlines: NEVER state a procedural "
        "deadline from memory — use deadline_calculate / deadline_get_rule and "
        "always relay their warning, quoted norm and URL to the user. The same applies to court state fees (სახელმწიფო ბაჟი): use fee_calculate / fee_get_rule, never amounts from memory. PDFs "
        "(Grand Chamber, Supreme Court analytics, parliament files, agency "
        "decisions) are read with pdf_get_text / pdf_search. Administrative-body "
        "decisions (tax_*, rs_*, competition_*, ombudsman_*, procurement_*, "
        "bar_ethics_*) carry authority_level 1: below every court. Public "
        "registries (registry_* — companies, real estate, liens, movable pledges, "
        "lawyers, notaries, procurement black/white lists), companyinfo_* "
        "(Transparency International's company/people graph) and geostat_* "
        "(statistical business register) are reference data: relay their "
        "personal-data warning, mention only what the legal question needs, "
        "and say that only an official extract has legal force. Search a person "
        "by name only for a concrete legal purpose (due diligence, dispute, "
        "enforcement, conflict check); namesakes are common — confirm identity "
        "by personal number before attributing anything, and stay within "
        "business roles. For complex company / counterparty checks combine "
        "companyinfo_company (people, history, sanctions, blacklists), "
        "geostat_company, registry_liens, registry_movable_pledges and "
        "registry_property_parcel. Lower-court decisions after April 2020 are not "
        "published anywhere (ecd.court.ge stops at 2020-04) — say so and use the "
        "Supreme Court search."
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

@app.tool(description="Search common-court decisions (ecd.court.ge). COVERAGE: only decisions dated 2018-01 to 2020-04 — lower-court decisions after April 2020 are not published anywhere (Organic Law on Common Courts art. 13(3¹) not implemented); use supreme_search for later practice. Filters: case number, decision date range (YYYY-MM-DD), instance (1=first, 2=appeals, 3=supreme), court code, case category, litigation type (dispute category/article), decision type, full-text terms. texts: one word = substring match (stems OK, e.g. 'ალიმენტ'); several words = exact phrase of whole adjacent words (write '361-ე მუხლის', not '361-ე მუხლ'); several list items = AND. End a word with * to OR-search all its case forms (e.g. 'სამოქალაქო კოდექსის 361-ე მუხლ*'). take ≤ 50. For one article prefer court_search_article. Results carry authority_level.")
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
    expand: bool = True,
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
        skip=skip, take=take, expand=expand,
    )


@app.tool(description="Common-court decisions (ecd.court.ge, 2018-01..2020-04) citing ONE article. article like '361' or '151^1'; law in the GENITIVE as decisions write it ('სამოქალაქო კოდექსის', 'სისხლის სამართლის კოდექსის'). mode: auto | phrase (law + article adjacent) | cooccur (both anywhere — higher recall) | classifier (Criminal Code / Administrative Offences Code article classifier). total is a lower bound unless total_exact.")
def court_search_article(article: str, law: str = "", mode: str = "auto", part: str = "",
                         instance_id: str = "", case_category_id: str = "",
                         date_from: str = "", date_to: str = "", skip: int = 0,
                         take: int = 20) -> dict:
    return ecd.search_article(article, law=law, mode=mode, part=part or None,
                              instance_id=instance_id or None,
                              case_category_id=case_category_id or None,
                              date_from=date_from or None, date_to=date_to or None,
                              skip=skip, take=take)


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


@app.tool(description="Supreme Court GRAND CHAMBER decisions — the binding norm interpretations (authority_level 5, mandatory for all common courts). category: civil, criminal, admin, or norms (compiled norm interpretations). Returns PDF links; the title includes date + case number + summary. Read or search a PDF with pdf_get_text / pdf_search (old legacy-font PDFs are converted to Unicode).")
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

@app.tool(description="Search bills in the Parliament of Georgia (კანონპროექტები), newest first — laws being drafted that may soon change the legislation. query matches bill/package names (Georgian, e.g. 'საგადასახადო კოდექს'); bill_type_id: 1=law draft, 11=organic law (codes that are organic laws, e.g. the Labour Code, are amended by type 11), 12=constitutional law, 2=parliament resolution. name_match=false means the hit came from another bill in the same package. Each result shows its current stage (I/II/III hearing, კანონი = adopted). With query + bill_type_id, bills whose own name matches are filtered server-side (fast); related bills (query only in the package / a sibling bill) need a slow scan — scan_related: default only when the page runs past the name matches; true = always (exact total); false = never. limit ≤ 50.")
def parliament_search_bills(query: str = "", bill_type_id: int | None = None,
                            page: int = 1, limit: int = 20,
                            scan_related: bool | None = None) -> dict:
    return parliament.search_bills(query, bill_type_id=bill_type_id, page=page, limit=limit,
                                   scan_related=scan_related)


@app.tool(description="Full legislative history of one bill (id from parliament_search_bills): every stage (initiated version, I/II/III hearing, resolutions, adopted law) with dates, vote counts, committees, and downloadable files (draft text, explanatory note, current wording of the amended articles, committee conclusions) — read them with pdf_get_text; older adopted-law files are scans without a text layer.")
def parliament_get_bill(bill_id: int) -> dict:
    return parliament.get_bill(bill_id)


# ── Cross-source research ────────────────────────────────────

@app.tool(description="Anti-hallucination check: does the quoted text really appear in the cited source? source='matsne' (+ law or doc_id, optional article/part/as_of_date), 'ecd' (+ decision_document_id, instance_id), 'supreme' (+ case_id, palata), 'constitutional' (+ legal_id), 'echr' (+ itemid), 'pdf' (+ url — Grand Chamber, analytics, parliament files; the page is returned). Verdict: exact / close (same passage, wording differs — use matched_text) / partial / not_found (do not attribute). Run it on every quote before putting it in an answer.")
def verify_citation(quote: str, source: str, law: str = "", doc_id: int = 0,
                    article: str = "", part: int | None = None, as_of_date: str = "",
                    decision_document_id: int = 0, instance_id: int = 0,
                    case_id: int = 0, palata: int = 1, legal_id: int = 0,
                    itemid: str = "", url: str = "") -> dict:
    return research.verify_citation(
        quote, source, law=law, doc_id=doc_id, article=article, part=part,
        as_of_date=as_of_date, decision_document_id=decision_document_id,
        instance_id=instance_id, case_id=case_id, palata=palata,
        legal_id=legal_id, itemid=itemid, url=url,
    )


@app.tool(description="One-call research of a law article: its text (optionally as_of_date) plus the court practice citing it — Constitutional Court, Supreme Court (chamber chosen by the law) and common courts — ordered by authority_level, with hit counts. Search uses the Georgian citation form (e.g. 'სამოქალაქო კოდექსის 361-ე მუხლ'). Read the decisions before relying on them.")
def research_article(article: str, law: str = "", doc_id: int = 0,
                     as_of_date: str = "", max_results: int = 5) -> dict:
    return research.research_article(article, law=law, doc_id=doc_id,
                                     as_of_date=as_of_date, max_results=max_results)


# ── Procedural deadlines (deadlines.py) ──────────────────────

@app.tool(description="List procedural-deadline types (civil appeal, cassation, private complaint, default-judgment objection, administrative lawsuit/complaint, criminal appeal/cassation, labour disputes, limitation periods) with the law article each period is read from. No periods are stored — they are read live from matsne.")
def deadline_list_procedures() -> list[dict]:
    return deadline_calc.list_procedures()


@app.tool(description="Live text of the norm that sets a procedural deadline: the period with its quoted sentence, start-rule norms and the counting rules (start day, months, non-working days). Use it before stating any deadline. as_of_date = date of the triggering event (version then in force).")
def deadline_get_rule(procedure: str, as_of_date: str = "") -> dict:
    return deadline_calc.get_deadline_rule(procedure, as_of_date)


@app.tool(description="Compute the last day of a procedural term from the live norm and live counting/holiday rules. start_date (YYYY-MM-DD or DD/MM/YYYY) is the triggering event (delivery/announcement of the reasoned decision, acquaintance with the act). status: computed | conservative | not_computed (ambiguous norm — no date guessed). extra_holidays: ad-hoc non-working days declared by government (YYYY-MM-DD). ALWAYS relay the warning, the quoted sentence and the URL to the user; the start date and special rules must be verified by a lawyer.")
def deadline_calculate(procedure: str, start_date: str, as_of_date: str = "",
                       extra_holidays: list[str] | None = None) -> dict:
    return deadline_calc.calculate_deadline(procedure, start_date, as_of_date, extra_holidays)


@app.tool(description="Compute a period the agent found in another norm (e.g. a special 5-day term) using the live counting rules of a regime: civil_procedure, administrative_procedure, criminal_procedure, general_administrative, civil_code. unit: days, weeks, months, years, working_days. Cite law + article: the norm is fetched and a mismatch with the stated period is flagged. Relay the warning.")
def deadline_calculate_custom(start_date: str, value: int, unit: str = "days",
                              regime: str = "civil_procedure", law: str = "", article: str = "",
                              part: int | None = None, as_of_date: str = "",
                              extra_holidays: list[str] | None = None) -> dict:
    return deadline_calc.calculate_custom(start_date, value, unit=unit, regime=regime, law=law,
                                          article=article, part=part, as_of_date=as_of_date,
                                          extra_holidays=extra_holidays)


@app.tool(description="Public holidays (non-working days) of a year, parsed live from the Labour Code + Orthodox Easter dates, with the source article.")
def deadline_holidays(year: int, as_of_date: str = "") -> dict:
    return deadline_calc.holidays(year, as_of_date)


# ── Court state fee (state_fee.py) ───────────────────────────

@app.tool(description="List court state-fee (სახელმწიფო ბაჟი) types — property/non-property claim, appeal, cassation, private complaint, claim security, reopening, uncontested proceedings, bill of exchange, arbitration and mediation applications — with the Civil Procedure Code art. 39 sub-point each amount is read from. No amounts are stored — they are read live from matsne. Administrative-court claims use the same types (CPC 38(ე)).")
def fee_list_types() -> list[dict]:
    return fee_calc.list_fee_types()


@app.tool(description="Live text of the norm that sets a court state fee: the quoted CPC art. 39(1) sub-point, extracted percentage + minimum or fixed amount, the per-instance ceilings for individuals / legal entities (art. 39(3)) and the magistrate rule (art. 39(2)). as_of_date = filing date (version then in force).")
def fee_get_rule(fee_type: str, as_of_date: str = "") -> dict:
    return fee_calc.get_fee_rule(fee_type, as_of_date)


@app.tool(description="Compute a court state fee in GEL from the live norm. claim_value (GEL) is required for percentage types (property claim/appeal/cassation, court-mediation claim) — determine it per CPC art. 40–41. party_type 'individual' | 'legal_entity' selects the art. 39(3) ceiling (status needs_input with both amounts if it matters and is missing). magistrate=true halves the fee (art. 39(2)). settlement: 'at_main_hearing' (halved) | 'before_main_hearing' (waived) for withdrawal, admission or settlement (art. 49). administrative=true adds the APC art. 9 exemptions. status: computed | conservative | needs_input | not_computed. Exemptions (CPC 46–48, special laws) are NOT applied automatically — check related_norms. ALWAYS relay the warning, the quoted sub-point and the URL.")
def fee_calculate(fee_type: str, claim_value: float | None = None, party_type: str = "",
                  magistrate: bool = False, filing_date: str = "", settlement: str = "",
                  administrative: bool = False) -> dict:
    return fee_calc.calculate_fee(fee_type, claim_value, party_type, magistrate, filing_date,
                                  settlement, administrative)


# ── Public registries (registries_api.py) ────────────────────
# Anonymous endpoints only; CAPTCHA-protected pages (company extract, debtors registry)
# are not wrapped. Every result carries a personal-data warning.

_REG = (" Input: identification code (9-digit company / 11-digit personal number), cadastral "
        "code or company name. Reference data only: relay the personal-data `warning`; an official "
        "extract prevails.")


@app.tool(description="Company registry (NAPR entrepreneurs & non-commercial legal entities): find a legal entity or individual entrepreneur by identification code or name → id code, name, legal form, status (active / liquidated / code cancelled). Directors, partners and the extract are CAPTCHA-protected on the site and NOT available — tell the user to order an extract." + _REG)
def registry_company_search(id_code: str = "", name: str = "", page: int = 1) -> dict:
    return company_registry.search_companies(id_code=id_code, name=name, page=page)


@app.tool(description="Company registry: entities in which an organization (by name) or a person/entity (by identification code) is registered as founder or partner — for corporate links and asset tracing." + _REG)
def registry_company_participations(org_name: str = "", id_code: str = "", page: int = 1) -> dict:
    return company_registry.find_participations(org_name=org_name, id_code=id_code, page=page)


@app.tool(description="Company registry applications filed in a period (default last 7 days) or one by registration number (e.g. B26439530). app_type: new_registration, change_of_data, reorganization, deregistration, extract, liquidation_start, liquidation_end, forced_liquidation_start, insolvency_start, insolvency_end, reorganization_start, reorganization_end, share_pledge, share_pledge_change, share_pledge_end, dissolution, redomiciliation_start. Useful to monitor insolvency / liquidation filings." + _REG)
def registry_company_applications(date_from: str = "", date_to: str = "", app_type: str = "",
                                  reg_number: str = "", page: int = 1) -> dict:
    return company_registry.search_applications(date_from, date_to, app_type, reg_number, page)


@app.tool(description="Real-estate registry parcel card by cadastral code (e.g. 01.18.04.009.016): area, parcel type, address, ownership type, registered owners and every registry document (extracts, cadastral plans) with a direct PDF link — read the newest extract with pdf_get_text for rights and encumbrances." + _REG)
def registry_property_parcel(cadastral_code: str) -> dict:
    return property_registry.parcel(cadastral_code)


@app.tool(description="Real-estate registry application book: transactions filed (sale, mortgage, lease, seizure...) with status and dates, by cadastral code, application number or applicant identification code (ID-code searches need a period; default last 90 days)." + _REG)
def registry_property_applications(cadastral_code: str = "", reg_number: str = "", id_code: str = "",
                                   date_from: str = "", date_to: str = "", page: int = 1) -> dict:
    return property_registry.applications(cadastral_code, reg_number, id_code, date_from, date_to, page)


@app.tool(description="Seizure / prohibition / tax-lien registry (NAPR): liens on a person or entity (identification code) or on a property (cadastral code) → kind, object, active flag, basis (court ruling, enforcement or tax order with number and date), registration number and status." + _REG)
def registry_liens(id_code: str = "", cadastral_code: str = "", page: int = 1) -> dict:
    return lien_registry.search(id_code=id_code, cadastral_code=cadastral_code, page=page)


@app.tool(description="Movable-property / intangible-asset pledge and leasing registry (NAPR): records where the identification code is owner or creditor → subject, creditor, owner, status, app_id for details." + _REG)
def registry_movable_pledges(id_code: str, page: int = 1) -> dict:
    return pledge_registry.search(id_code, page)


@app.tool(description="One movable pledge/leasing record by app_id (from registry_movable_pledges): registration date and term, creditor, owner, pledged items, documents and scan links." + _REG)
def registry_movable_pledge_get(app_id: int, max_chars: int = 6000) -> dict:
    return pledge_registry.get_pledge(app_id, max_chars)


@app.tool(description="Georgian Bar Association unified members list: is a person a registered lawyer? Search by name or list number → list number and specializations. Suspension/disbarment is not shown — check bar_ethics_search.")
def registry_lawyer_search(query: str, limit: int = 20) -> dict:
    return lawyer_registry.search(query, limit)


@app.tool(description="Notary bureaus directory (my.gov.ge): notaries in a region — the API has three: თბილისი (default), აჭარა, რაიონები (rest of Georgia) — with optional city/district and name filter → name, district, phone, email.")
def registry_notary_search(region: str = "თბილისი", district: str = "", name: str = "", page: int = 1) -> dict:
    return notaries.search(region, district, name, page)


@app.tool(description="Public procurement supplier lists (procurement.gov.ge), newest first: list_type black (banned from tenders), white (reliable), warned. One page → title, identification code, date, entry URL.")
def registry_procurement_list(list_type: str = "black", page: int = 1) -> dict:
    return procurement_lists.list_entries(list_type, page)


@app.tool(description="Search a procurement supplier list (black / white / warned) for an identification code or company name, scanning up to max_pages pages (≤60). Not found within the scanned pages ≠ not listed.")
def registry_procurement_search(query: str, list_type: str = "black", max_pages: int = 15) -> dict:
    return procurement_lists.search(query, list_type, max_pages)


@app.tool(description="Text of one procurement list entry (URL from registry_procurement_list/search): procuring entity, tender number, CPV, supplier, legal basis.")
def registry_procurement_get(url: str) -> dict:
    return procurement_lists.get_entry(url)


# ── companyinfo.ge + Geostat business register (registries_api.py) ──

@app.tool(description="companyinfo.ge (Transparency International Georgia, monthly mirror of the NAPR business registry): find companies by name (substring) or identification code → company_id, id code, name, address, registration date, flags of linked datasets (sanctions, procurement blacklist/warning list, asset declarations of linked officials, financial reports, mining licences, construction permits). Individual entrepreneurs are excluded (site robots.txt)." + _REG)
def companyinfo_company_search(name: str = "", id_code: str = "", status: str = "", page: int = 1) -> dict:
    return companyinfo.search_companies(name=name, id_code=id_code, status=status, page=page)


@app.tool(description="companyinfo.ge full company card by identification code (or company_id): status, form, address, email, CURRENT directors/partners with roles and shares, role history newest-first (history_limit), shareholders, companies it owns, reorganisations, business-registry size/activity, sanctions, procurement black/warning list, linked officials and their asset declarations, mining licences, construction permits, financial-report years. Best single call for counterparty due diligence; personal numbers are returned in full — use them to cross-check people in registry_liens, registry_movable_pledges, companyinfo_people_search." + _REG)
def companyinfo_company(id_code: str = "", company_id: int = 0, history_limit: int = 40) -> dict:
    return companyinfo.company(id_code=id_code, company_id=company_id, history_limit=history_limit)


@app.tool(description="companyinfo.ge people search — persons registered as directors/partners/founders, by FULL name (first + last, substring) or by personal number. Use only for a concrete legal purpose (due diligence, dispute, enforcement, conflict check). Namesakes are common: the result lists them and an identity_warning — confirm the right person (personal number, roles) before attributing anything, then cross-check that personal number in the other registries (registry_liens, registry_movable_pledges, registry_property_applications, registry_company_participations)." + _REG)
def companyinfo_people_search(name: str = "", id_number: str = "", page: int = 1) -> dict:
    return companyinfo.search_people(name=name, id_number=id_number, page=page)


@app.tool(description="companyinfo.ge person card by person_id (from companyinfo_people_search or companyinfo_company): business roles only — companies (current and former), role, date, ownership, sanctions, asset declarations. Home address is withheld." + _REG)
def companyinfo_person(person_id: int) -> dict:
    return companyinfo.person(person_id)


@app.tool(description="Geostat statistical business register (br.geostat.ge, ~1.15M entities): search/filter by name, identification code, NACE rev.2 activity code without the section letter ('64.19', '64.19.1' — find codes with geostat_activity_codes), region code ('11' = Tbilisi — geostat_regions), legal_form_id, size (small/medium/large/unknown), active. Returns activity, size, legal and actual address, director, registration date. Good for market / competitor lists. Cite 'საქსტატი'." + _REG)
def geostat_search(name: str = "", id_code: str = "", activity_code: str = "", region_code: str = "",
                   legal_form_id: int = 0, size: str = "", active: bool | None = None,
                   page: int = 1, limit: int = 20) -> dict:
    return geostat.search(name, id_code, activity_code, region_code, legal_form_id, size, active, page, limit)


@app.tool(description="Geostat business register entity by identification code: activity, size, ownership, legal + actual address, director, dates, director history (representatives), dated name and address history. Statistical register — NAPR is the legal one. Cite 'საქსტატი'." + _REG)
def geostat_company(id_code: str) -> dict:
    return geostat.company(id_code)


@app.tool(description="Find NACE rev.2 activity codes in the Geostat dictionary by Georgian keyword or code prefix (e.g. 'ბანკ', '64.19') — for geostat_search filters.")
def geostat_activity_codes(query: str, limit: int = 20) -> dict:
    return geostat.activity_codes(query, limit)


@app.tool(description="Geostat region codes for geostat_search (e.g. '11' = Tbilisi, '15' = Adjara).")
def geostat_regions() -> dict:
    return geostat.regions()


# ── PDF documents (pdf_text.py) ──────────────────────────────

@app.tool(description="Extract text from a PDF — supremecourt.ge (Grand Chamber, norms compilation, analytics, decision collections), info.parliament.ge bill files, ecd.court.ge, matsne English PDFs, agency decisions. Converts legacy Georgian fonts (AcadNusx/LitNusx…) to Unicode and reports scanned/image-only pages (no OCR). Large PDFs: page through with offset_chars / max_chars.")
def pdf_get_text(url: str, max_pages: int = 30, offset_chars: int = 0,
                 max_chars: int = 50000) -> dict:
    doc = extract_pdf_text(url, max_pages=max_pages or None)
    if "error" in doc:
        return doc
    text = doc.get("text", "")
    doc["text"] = text[offset_chars:offset_chars + max_chars] if max_chars else text[offset_chars:]
    doc["offset_chars"] = offset_chars
    doc["text_truncated"] = offset_chars + len(doc["text"]) < len(text)
    return doc


@app.tool(description="Find a phrase in a PDF; returns page numbers and surrounding passages. Tolerates line breaks, hyphenation, quote styles and legacy fonts; falls back to fuzzy matching for quotes of 10+ characters.")
def pdf_search(url: str, query: str, max_results: int = 10) -> dict:
    return search_pdf(url, query, max_results=max_results)


# ── Supreme Court analytical materials ───────────────────────

@app.tool(description="List Supreme Court analytical-material categories with authority levels: practice generalizations, recommendations (civil/admin/criminal/magistrate), important explanations (განმარტებები №1-100), decision collections (digest_*), research, human-rights centre, ECHR translations, international acts, 'Justice and Law' journal.")
def supreme_practice_categories() -> dict:
    return supreme_practice.list_categories()


@app.tool(description="List the materials (mostly PDFs) of one Supreme Court analytics category, optionally filtered by title text and year. Analytical guidance is authority_level 4 and NOT binding — the Grand Chamber (5) prevails; cite the underlying decision. Read PDFs with pdf_get_text.")
def supreme_practice_list(category: str, query: str = "", year: int | None = None,
                          limit: int = 50) -> dict:
    return supreme_practice.list_materials(category, query=query, year=year, limit=limit)


@app.tool(description="Search titles of Supreme Court generalizations, recommendations, explanations, research and ECHR translations (sorted by authority, then year). Pass categories=['journal'] or ['digest_civil'] to search those too.")
def supreme_practice_search(query: str, categories: list[str] | None = None,
                            limit: int = 30) -> dict:
    return supreme_practice.search_materials(query, categories=categories, limit=limit)


@app.tool(description="Get an HTML Supreme Court analytics material or a journal issue's table of contents. For PDFs it returns the URL — pass it to pdf_get_text.")
def supreme_practice_get(url: str, max_chars: int = 20000) -> dict:
    return supreme_practice.get_material(url, max_chars=max_chars)


# ── Administrative bodies (authority_level 1) ────────────────

@app.tool(description="Search decisions of the Ministry of Finance Dispute Resolution Council (2nd-stage tax appeals, 2018→, ~4,700, full text). outcome: 1 upheld, 2 rejected, 3 partly upheld, 4 not considered. Dates YYYY-MM-DD.")
def tax_dispute_search(query: str = "", page: int = 1, page_size: int = 20, number: str = "",
                       date_from: str = "", date_to: str = "", outcome: int | None = None) -> dict:
    return tax_disputes.search(query, page=page, page_size=page_size, number=number,
                               date_from=date_from, date_to=date_to, outcome=outcome)


@app.tool(description="Full text of a MoF Dispute Resolution Council decision: facts, reasoning, cited Tax Code articles, outcome, the dispute chain (Revenue Service → MoF) and a PDF link.")
def tax_dispute_get(decision_id: str) -> dict:
    return tax_disputes.get_decision(decision_id)


@app.tool(description="Search Revenue Service dispute-council orders (1st-stage tax disputes, 2018→, ~6,700, full text). Same filters as tax_dispute_search.")
def rs_dispute_search(query: str = "", page: int = 1, page_size: int = 20, number: str = "",
                      date_from: str = "", date_to: str = "", outcome: int | None = None) -> dict:
    return rs_disputes.search(query, page=page, page_size=page_size, number=number,
                              date_from=date_from, date_to=date_to, outcome=outcome)


@app.tool(description="Full text of a Revenue Service dispute-council order.")
def rs_dispute_get(decision_id: str) -> dict:
    return rs_disputes.get_decision(decision_id)


@app.tool(description="Search tax guidance: doc_type public_decision (Minister of Finance public rulings), situational_guide (Revenue Service situational guides), tax_court_decision (tax court decisions curated by RS), all_disputes. Advance/individual rulings are not public.")
def tax_guidance_search(query: str = "", doc_type: str = "public_decision", page: int = 1,
                        page_size: int = 20) -> dict:
    return tax_guidance.search(query, page=page, page_size=page_size, doc_type=doc_type)


@app.tool(description="Full text of a tax-guidance document (public ruling, situational guide, tax court decision) with attachments.")
def tax_guidance_get(document_id: str) -> dict:
    return tax_guidance.get_decision(document_id)


@app.tool(description="Decisions of the Competition and Consumer Protection Agency (gcca.gov.ge). category: dominance, agreements, state_bodies, unfair_competition, mergers, state_aid, consumer, market_monitoring, antidumping. query matches title + summary. Decisions are PDFs (pdf_get_text).")
def competition_search(query: str = "", category: str | None = None, page: int = 1,
                       page_size: int = 20) -> dict:
    return competition.search(query, page=page, category=category, page_size=page_size)


@app.tool(description="Public Defender (ombudsman) recommendations and proposals — advisory, NOT binding. section: recommendations, proposals, general_proposals. A query crawls up to max_pages listing pages.")
def ombudsman_search(query: str = "", section: str | None = None, page: int = 1,
                     max_pages: int = 30) -> dict:
    return ombudsman.search(query, page=page, section=section, max_pages=max_pages)


@app.tool(description="Text and attachments of one Public Defender recommendation/proposal (URL or slug from ombudsman_search).")
def ombudsman_get(url: str) -> dict:
    return ombudsman.get_decision(url)


@app.tool(description="Public-procurement Dispute Resolution Council complaints and decisions (2011→, 50 per page). query: complaint no. (DIS…), tender no. (NAT…/SPA…) or an organisation name. status: 40 inadmissible, 50 pending, 60 upheld, 70 rejected, 80 partly upheld, 90 withdrawn, 95 not considered. Dates DD.MM.YYYY or YYYY-MM-DD.")
def procurement_dispute_search(query: str = "", page: int = 1, status: int | None = None,
                               date_from: str = "", date_to: str = "") -> dict:
    return procurement.search(query, page=page, status=status, date_from=date_from, date_to=date_to)


@app.tool(description="One procurement complaint with the council's decision documents (PDF links) and the tender link. decision_id as returned by procurement_dispute_search.")
def procurement_dispute_get(decision_id: str) -> dict:
    return procurement.get_decision(decision_id)


@app.tool(description="Bar Association ethics-commission decisions (anonymised PDFs). category: procedural_refusal, procedural_termination, review_warning, review_recommendation, review_acquittal, review_termination, plenary_warning, plenary_recommendation, plenary_acquittal, plenary_suspension, plenary_disbarment, plenary_termination. query matches case numbers only.")
def bar_ethics_search(category: str | None = None, query: str = "", page: int = 1,
                      max_pages: int = 3) -> dict:
    return bar_ethics.search(query, page=page, category=category, max_pages=max_pages)


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
    "(7) Never state a procedural deadline from memory; use the deadline_* tools and warn "
    "the user that the start date and special rules must be checked by a lawyer. "
    "(8) The same for court state fees: fee_calculate / fee_get_rule, never from memory. "
    "(9) Registry data (registry_*, companyinfo_*, geostat_*) is reference only — relay the "
    "personal-data warning; the official extract prevails; search people by name only for the "
    "legal purpose at hand and confirm identity by personal number (namesakes). "
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
        "supreme_grand_chamber and supreme_practice_search (generalizations, "
        "recommendations, explanations); for tax/competition/procurement matters also "
        "the administrative bodies (tax_dispute_search, …); 4) check "
        "parliament_search_bills for pending changes; 5) verify every quote with "
        "verify_citation; 6) if a procedural step is involved, deadline_calculate — "
        "and relay its warning.\n\n"
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
        "deadlines (deadline_calculate / deadline_get_rule — never from memory; relay the "
        "warning), state fee (fee_calculate) and evidence — for a company, property or "
        "debt-recovery question check the registries (registry_company_search, "
        "registry_property_parcel, registry_liens, registry_movable_pledges).\n\n"
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
