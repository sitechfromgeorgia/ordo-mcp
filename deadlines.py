#!/usr/bin/env python3
"""
deadlines.py — procedural-deadline helper for Georgian law (SAFE mode)
======================================================================
Computes procedural deadlines (appeal, cassation, private complaint, default
judgment objection, administrative lawsuits/complaints, criminal appeals,
limitation periods...) WITHOUT hardcoding any number of days or months.

How it stays safe:
  * The module stores only a map  procedure → (law doc_id, article, part).
    The period itself ("14 დღე", "ერთი თვის", "1 კვირის", "ათ წელს") is read
    live from matsne.gov.ge, from the version of the law in force on the date
    of the triggering event, and the exact sentence it came from is quoted.
  * Counting rules (when counting starts, how months end, what happens when
    the last day is a holiday/rest day) are also read live from the codes and
    checked with a regex. If the wording changed, nothing is computed.
  * Public holidays come live from Labour Code art. 30 (fixed dates parsed
    from the text + Orthodox Easter days computed as the article lists them).
  * Ambiguous extraction → no date; the article text is returned instead.
  * Every result carries a `warning` the agent must relay to the user.

Zero dependencies (stdlib only) — reuses MatsneAPI from matsne_api.py.

Usage:
    from deadlines import DeadlineCalculator
    dc = DeadlineCalculator()
    dc.list_procedures()
    dc.get_deadline_rule("civil_appeal")
    dc.calculate_deadline("civil_appeal", "2026-03-27")
    dc.calculate_custom("2026-03-27", 30, "days", regime="civil_code")
"""

import calendar
import re
import sys
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Optional

from matsne_api import MatsneAPI

# ── Warning carried by every result ─────────────────────────────

WARNING = {
    "ka": ("ყურადღება: ეს არის ავტომატური გამოთვლა ციტირებული ნორმის საფუძველზე და არა "
           "იურიდიული კონსულტაცია. აუცილებლად გადაამოწმეთ: ვადის ათვლის დაწყების თარიღი "
           "(დასაბუთებული გადაწყვეტილების/აქტის ჩაბარება, გამოცხადება ან გაცნობა), სპეციალური "
           "ნორმები და გამონაკლისები, ვადის შეჩერება/აღდგენა/გაგრძელება, მთავრობის მიერ დამატებით "
           "გამოცხადებული უქმე დღეები და ნორმის რედაქცია მოვლენის დღეს. გაშვებული საპროცესო ვადა "
           "ხშირად არ აღდგება — საჩივარი წარადგინეთ ვადის ბოლო დღემდე ადრე და მიმართეთ "
           "კომპეტენტურ სასამართლოს ან ადვოკატს."),
    "en": ("WARNING: this is an automated calculation from the cited norm, not legal advice. "
           "Verify: the start date (delivery/announcement/receipt of the reasoned decision or act), "
           "special rules and exceptions, suspension/restoration/extension of the term, any extra "
           "public holidays declared by the Government, and which version of the law applied on the "
           "event date. A missed procedural deadline is often irreversible — file well before the "
           "last day and consult the competent court or a lawyer."),
    "agent_instruction": ("Relay this warning to the user together with the date, quote the cited "
                          "sentence and give the article URL. Never present the date as certain."),
}

# ── Georgian number words (stems, case endings optional) ────────

_UNIT_STEMS = {1: "ერთ", 2: "ორ", 3: "სამ", 4: "ოთხ", 5: "ხუთ", 6: "ექვს", 7: "შვიდ",
               8: "რვა", 9: "ცხრა", 10: "ათ", 11: "თერთმეტ", 12: "თორმეტ", 13: "ცამეტ",
               14: "თოთხმეტ", 15: "თხუთმეტ", 16: "თექვსმეტ", 17: "ჩვიდმეტ", 18: "თვრამეტ",
               19: "ცხრამეტ"}
_TENS_STEMS = {20: "ოც", 40: "ორმოც", 60: "სამოც", 80: "ოთხმოც"}


def _build_number_words() -> dict:
    """Stem → value for 1..100 (vigesimal: ოცდაათი = 20+10, ორმოცდახუთი = 40+5)."""
    words = {stem: n for n, stem in _UNIT_STEMS.items()}
    for tens, stem in _TENS_STEMS.items():
        words[stem] = tens
        for n, unit in _UNIT_STEMS.items():
            words[f"{stem}და{unit}"] = tens + n
    words["ას"] = 100
    return words


_NUMBER_WORDS = _build_number_words()
_NUM_ALT = "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True))
# "14 დღე", "ერთი თვის", "10-დღიანი", "ათდღიანი", "5 კალენდარულ დღეს", "1 კვირის", "ათ წელს"
_PERIOD_RE = re.compile(
    r'(?<![\w⁰¹²³⁴⁵⁶⁷⁸⁹])(?P<num>\d{1,4}|(?:' + _NUM_ALT + r')ი?)'
    r'(?:\s*-\s*|\s+|)'
    r'(?P<adj>(?:კალენდარულ|სამუშაო)\w*\s+)?'
    r'(?P<unit>დღე-ღამ|დღ|კვირ|თვ|წელიწად|წელ|წლ)\w*')
_UNIT_MAP = {"დღე-ღამ": "days", "დღ": "days", "კვირ": "weeks", "თვ": "months",
             "წელიწად": "years", "წელ": "years", "წლ": "years"}
_UNIT_ALIASES = {"day": "days", "days": "days", "d": "days", "week": "weeks", "weeks": "weeks",
                 "w": "weeks", "month": "months", "months": "months", "m": "months",
                 "year": "years", "years": "years", "y": "years",
                 "working_days": "working_days", "working_day": "working_days"}

# Amendment-history lines appended under each article on matsne
_HISTORY_LINE = re.compile(r'^(?:საქართველოს\s+\d{4}\s+წლის|სსმ\s)')

_GE_MONTHS = {"იანვარ": 1, "თებერვალ": 2, "მარტ": 3, "აპრილ": 4, "მაის": 5, "ივნის": 6,
              "ივლის": 7, "აგვისტო": 8, "სექტემბერ": 9, "ოქტომბერ": 10, "ნოემბერ": 11,
              "დეკემბერ": 12}
_GE_WEEKDAYS = ["ორშაბათი", "სამშაბათი", "ოთხშაბათი", "ხუთშაბათი", "პარასკევი", "შაბათი", "კვირა"]
_EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ── Law ids (matsne document ids) ───────────────────────────────

CPC, APC, GAC, CRPC, CC, LABOUR = 29962, 16492, 16270, 90034, 31702, 1155567
LAW_NAMES = {
    CPC: "საქართველოს სამოქალაქო საპროცესო კოდექსი (Civil Procedure Code)",
    APC: "საქართველოს ადმინისტრაციული საპროცესო კოდექსი (Administrative Procedure Code)",
    GAC: "საქართველოს ზოგადი ადმინისტრაციული კოდექსი (General Administrative Code)",
    CRPC: "საქართველოს სისხლის სამართლის საპროცესო კოდექსი (Criminal Procedure Code)",
    CC: "საქართველოს სამოქალაქო კოდექსი (Civil Code)",
    LABOUR: "საქართველოს შრომის კოდექსი (Labour Code)",
}

# ── Counting regimes: which live norms govern the arithmetic ────
# Each rule: (doc_id, article, part, regex that must match the live text, role).
# The regex is a tripwire: if the legislature rewords the rule, computation stops.

_CPC_RULES = [
    (CPC, "60", 2, r"მომდევნო\s+დღიდან", "start: counting begins on the day after the triggering event"),
    (CPC, "61", 1, r"შესაბამის\s+თვესა\s+და\s+რიცხვში.*უკანასკნელ\s+დღეს",
     "months: end on the same-numbered day of the last month, or that month's last day"),
    (CPC, "61", 2, r"უქმე\s+და\s+დასვენების\s+დღეს.*მომდევნო\s+პირველი\s+სამუშაო\s+დღე",
     "end on a holiday/rest day → next working day"),
    (CPC, "61", 3, r"ოცდაოთხ\s+საათამდე", "the act may be done until 24:00 of the last day (incl. by post)"),
]
_CC_RULES = [
    (CC, "121", None, r"კანონებში", "scope: Civil Code term rules apply to terms set in laws"),
    (CC, "122", None, r"არ\s+მიეთვლება\s+ის\s+დღე", "start: the day of the event is not counted"),
    (CC, "123", None, r"შეესაბამება\s+მოვლენის\s+დადგომის\s+დღეს.*ბოლო\s+დღის",
     "weeks/months/years: end on the corresponding day, or the month's last day"),
    (CC, "127", None, r"მომდევნო\s+სამუშაო\s+დღე", "end on a non-working day → next working day"),
]
REGIMES = {
    "civil_procedure": {
        "description": "CPC arts. 60–61: start next day; month → same day number; "
                       "last day on holiday/rest day → next working day",
        "rules": _CPC_RULES, "count": "calendar", "shift": True,
    },
    "administrative_procedure": {
        "description": "APC art. 1(2) applies the CPC, so CPC arts. 60–61 govern the counting",
        "rules": [(APC, "1", 2, r"სამოქალაქო\s+საპროცესო\s+კოდექსის\s+დებულებანი",
                   "APC refers to the CPC for anything it does not regulate")] + _CPC_RULES,
        "count": "calendar", "shift": True,
    },
    "criminal_procedure": {
        "description": "CrPC art. 86: start day not counted; non-working days INCLUDED; no shift",
        "rules": [
            (CRPC, "86", 1, r"მხედველობაში\s+არ\s+მიიღება\s+ის\s+დღე-ღამე",
             "start: the day the term begins is not counted"),
            (CRPC, "86", 2, r"შედის\s+არასამუშაო\s+დროც", "non-working days and holidays are included (no shift)"),
            (CRPC, "86", 3, r"ბოლო\s+თვის\s+შესაბამის\s+რიცხვში",
             "months: end on the corresponding day, or that month's last day"),
        ],
        "count": "calendar", "shift": False,
    },
    "general_administrative": {
        "description": "GAC art. 15: holidays and rest days are not counted; start/end per Civil Code "
                       "arts. 121–123 (CC applies to terms set in laws)",
        "rules": [(GAC, "15", None, r"უქმე\s+და\s+დასვენების\s+დღეები\s+არ\s+ჩაითვლება",
                   "holidays and rest days are not counted")] + _CC_RULES[:3],
        "count": "working", "shift": True,
    },
    "civil_code": {
        "description": "Civil Code arts. 121–127: start next day; corresponding day; "
                       "non-working last day → next working day",
        "rules": _CC_RULES, "count": "calendar", "shift": True,
    },
}

# ── Procedure map — NO durations here, only where to read them ──
# near: regex that must precede the wanted period in the same sentence (when an
#       article names several periods); related: extra norms quoted for the start rule.

PROCEDURES = {
    "civil_appeal": {
        "name_ka": "სააპელაციო საჩივარი (სამოქალაქო საქმე)",
        "name_en": "Civil appeal (to the Court of Appeals)",
        "doc_id": CPC, "article": "369", "part": 1, "regime": "civil_procedure",
        "start_hint": "delivery of the reasoned decision (or its announcement if present; CPC 259¹ deemed delivery)",
        "related": [(CPC, "369", 2), (CPC, "259^1", 1), (CPC, "59", 4)],
    },
    "civil_cassation": {
        "name_ka": "საკასაციო საჩივარი (სამოქალაქო საქმე)",
        "name_en": "Civil cassation complaint (to the Supreme Court)",
        "doc_id": CPC, "article": "397", "part": 1, "regime": "civil_procedure",
        "start_hint": "delivery of the appellate decision (or announcement of the reasoned decision if present)",
        "related": [(CPC, "397", 2), (CPC, "259^1", 1), (CPC, "59", 4)],
    },
    "civil_private_complaint": {
        "name_ka": "კერძო საჩივარი (სამოქალაქო საქმე)",
        "name_en": "Private complaint against a court ruling (განჩინება), civil",
        "doc_id": CPC, "article": "416", "part": None, "regime": "civil_procedure",
        "start_hint": "delivery of the ruling, or its announcement if the complainant attended",
        "related": [(CPC, "59", 4)],
    },
    "civil_default_judgment_objection": {
        "name_ka": "დაუსწრებელი გადაწყვეტილების გასაჩივრება",
        "name_en": "Objection to a default judgment (to the same court)",
        "doc_id": CPC, "article": "237", "part": None, "regime": "civil_procedure",
        "start_hint": "service of the default judgment copy under CPC arts. 70–78",
        "related": [(CPC, "236", None)],
    },
    "admin_court_appeal": {
        "name_ka": "სააპელაციო საჩივარი (ადმინისტრაციული საქმე)",
        "name_en": "Appeal in an administrative case (CPC rule via APC art. 1(2))",
        "doc_id": CPC, "article": "369", "part": 1, "regime": "administrative_procedure",
        "start_hint": "delivery of the reasoned decision; runs only if the act explained the appeal (APC 12)",
        "related": [(APC, "12", 1), (APC, "12", 2), (CPC, "259^1", 1)],
    },
    "admin_court_cassation": {
        "name_ka": "საკასაციო საჩივარი (ადმინისტრაციული საქმე)",
        "name_en": "Cassation in an administrative case (CPC rule via APC art. 1(2))",
        "doc_id": CPC, "article": "397", "part": 1, "regime": "administrative_procedure",
        "start_hint": "delivery of the appellate decision; runs only if the act explained the appeal (APC 12)",
        "related": [(APC, "12", 1), (APC, "12", 2), (APC, "34", 1)],
    },
    "admin_court_private_complaint": {
        "name_ka": "კერძო საჩივარი (ადმინისტრაციული საქმე)",
        "name_en": "Private complaint in an administrative case (CPC rule via APC art. 1(2)); "
                   "special chapters of the APC may set shorter terms",
        "doc_id": CPC, "article": "416", "part": None, "regime": "administrative_procedure",
        "start_hint": "delivery/announcement of the ruling; check APC special chapters",
        "related": [(APC, "12", 1)],
    },
    "admin_appeal_unexplained": {
        "name_ka": "გასაჩივრება, როცა გასაჩივრების წესი არ განემარტა (ადმ. სამართალწარმოება)",
        "name_en": "Appeal when the court act did not explain how to appeal (APC art. 12(2))",
        "doc_id": APC, "article": "12", "part": 2, "regime": "administrative_procedure",
        "start_hint": "the date the court act was issued (გამოტანა)",
        "related": [(APC, "12", 1)],
    },
    "admin_lawsuit_individual_act": {
        "name_ka": "სარჩელი ინდივიდუალური ადმინისტრაციულ-სამართლებრივი აქტის ბათილად ცნობაზე",
        "name_en": "Lawsuit to annul an individual administrative act (or complaint decision)",
        "doc_id": APC, "article": "22", "part": 3, "regime": "administrative_procedure",
        "near": r"ინდივიდუალური\s+ადმინისტრაციულ",
        "start_hint": "acquaintance with the act / complaint decision, or expiry of the complaint-decision term",
        "related": [(GAC, "180", 1)],
    },
    "admin_lawsuit_normative_act": {
        "name_ka": "სარჩელი ნორმატიული აქტის თაობაზე",
        "name_en": "Lawsuit against a normative administrative act",
        "doc_id": APC, "article": "22", "part": 3, "regime": "administrative_procedure",
        "near": r"ნორმატიული\s+აქტის",
        "start_hint": "the day direct damage was caused",
        "related": [],
    },
    "admin_lawsuit_refusal_to_issue": {
        "name_ka": "სარჩელი ადმინისტრაციულ-სამართლებრივი აქტის გამოცემის თაობაზე",
        "name_en": "Lawsuit compelling issuance of an administrative act (after refusal)",
        "doc_id": APC, "article": "23", "part": 3, "regime": "administrative_procedure",
        "start_hint": "receipt of the refusal (missed issuance term = refusal, GAC art. 177(2))",
        "related": [(GAC, "177", 2)],
    },
    "gac_complaint_act": {
        "name_ka": "ადმინისტრაციული საჩივარი ადმინისტრაციულ-სამართლებრივ აქტზე",
        "name_en": "Administrative complaint against an administrative act (GAC)",
        "doc_id": GAC, "article": "180", "part": 1, "regime": "general_administrative",
        "start_hint": "publication of the act or official acquaintance with it",
        "related": [(GAC, "180", 4)],
    },
    "gac_complaint_action": {
        "name_ka": "ადმინისტრაციული საჩივარი ადმინისტრაციული ორგანოს ქმედებაზე",
        "name_en": "Administrative complaint against an action of an administrative body (GAC)",
        "doc_id": GAC, "article": "180", "part": 2, "regime": "general_administrative",
        "start_hint": "the day the party learned of the action/inaction",
        "related": [(GAC, "180", 4)],
    },
    "criminal_appeal": {
        "name_ka": "სააპელაციო საჩივარი (სისხლის სამართლის საქმე)",
        "name_en": "Criminal appeal against a first-instance verdict",
        "doc_id": CRPC, "article": "293", "part": 1, "regime": "criminal_procedure",
        "start_hint": "announcement (გამოცხადება) of the verdict",
        "related": [(CRPC, "87", 1), (CRPC, "89", 1)],
    },
    "criminal_cassation": {
        "name_ka": "საკასაციო საჩივარი (სისხლის სამართლის საქმე)",
        "name_en": "Criminal cassation complaint against an appellate verdict",
        "doc_id": CRPC, "article": "302", "part": 1, "regime": "criminal_procedure",
        "start_hint": "announcement (გამოცხადება) of the appellate verdict",
        "related": [(CRPC, "87", 1), (CRPC, "89", 1)],
    },
    "criminal_restore_term_motion": {
        "name_ka": "გაცდენილი ვადის აღდგენის შუამდგომლობა (სისხლის სამართლის პროცესი)",
        "name_en": "Motion to restore a missed term (criminal procedure)",
        "doc_id": CRPC, "article": "89", "part": 2, "regime": "criminal_procedure",
        "near": r"საპატიო\s+მიზეზის\s+აღმოფხვრიდან",
        "start_hint": "the day the valid reason for missing the term ceased",
        "related": [(CRPC, "89", 1)],
    },
    "labour_dismissal_lawsuit": {
        "name_ka": "შრომითი ხელშეკრულების შეწყვეტის გასაჩივრება სასამართლოში",
        "name_en": "Court challenge of a dismissal (after the employer's written reasons)",
        "doc_id": LABOUR, "article": "48", "part": 6, "regime": "civil_code",
        "near": r"დასაბუთების\s+მიღებიდან",
        "start_hint": "receipt of the employer's written reasons (if none within the term: art. 48(7))",
        "related": [(LABOUR, "48", 7)],
    },
    "labour_dismissal_reasons_request": {
        "name_ka": "შეწყვეტის საფუძვლის წერილობითი დასაბუთების მოთხოვნა",
        "name_en": "Employee's request for written reasons for dismissal",
        "doc_id": LABOUR, "article": "48", "part": 4, "regime": "civil_code",
        "start_hint": "receipt of the employer's termination notice",
        "related": [(LABOUR, "48", 5)],
    },
    "labour_other_claims": {
        "name_ka": "შრომის კოდექსიდან გამომდინარე სხვა სარჩელი",
        "name_en": "Other Labour Code claims (limitation)",
        "doc_id": LABOUR, "article": "74", "part": None, "regime": "civil_code",
        "start_hint": "the day the person learned or should have learned of the violation",
        "related": [],
    },
    "civil_limitation_general": {
        "name_ka": "ხანდაზმულობის საერთო ვადა",
        "name_en": "General limitation period (Civil Code)",
        "doc_id": CC, "article": "128", "part": 3, "regime": "civil_code",
        "start_hint": "the moment the claim arose (CC art. 130)",
        "related": [(CC, "130", 1)],
    },
    "civil_limitation_contract": {
        "name_ka": "სახელშეკრულებო მოთხოვნის ხანდაზმულობა",
        "name_en": "Limitation period for contractual claims",
        "doc_id": CC, "article": "129", "part": 1, "regime": "civil_code",
        "near": r"სახელშეკრულებო\s+მოთხოვნების\s+ხანდაზმულობის",
        "start_hint": "the moment the claim arose (CC art. 130)",
        "related": [(CC, "130", 1)],
    },
    "civil_limitation_contract_immovable": {
        "name_ka": "უძრავ ნივთთან დაკავშირებული სახელშეკრულებო მოთხოვნის ხანდაზმულობა",
        "name_en": "Limitation period for contractual claims concerning immovable property",
        "doc_id": CC, "article": "129", "part": 1, "regime": "civil_code",
        "near": r"უძრავ\s+ნივთებთან",
        "start_hint": "the moment the claim arose (CC art. 130)",
        "related": [(CC, "130", 1)],
    },
}


def orthodox_easter(year: int) -> date:
    """Orthodox Easter Sunday (Meeus Julian algorithm) converted to the Gregorian calendar."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    julian_offset = year // 100 - year // 400 - 2  # 13 days for 1900–2099
    return date(year, month, day + 1) + timedelta(days=julian_offset)


def _parse_input_date(value) -> Optional[date]:
    """date / YYYY-MM-DD / DD/MM/YYYY / DD.MM.YYYY → date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _add_months(d: date, months: int) -> date:
    """Same day number `months` later; clamps to the month's last day (31 Jan + 1m → 28/29 Feb)."""
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _fmt(d: date) -> str:
    return f"{d.isoformat()} ({_EN_WEEKDAYS[d.weekday()]} / {_GE_WEEKDAYS[d.weekday()]})"


class _CachingMatsne(MatsneAPI):
    """MatsneAPI whose view-page fetches are cached (LRU) per (doc_id, publication, lang)."""

    def __init__(self, *args, max_pages: int = 16, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = OrderedDict()
        self._max_pages = max_pages

    def get_document_html(self, doc_id: int, publication: Optional[int] = None,
                          lang: Optional[str] = None) -> str:
        key = (int(doc_id), publication, lang or self.lang)
        if key in self._pages:
            self._pages.move_to_end(key)
            return self._pages[key]
        html = super().get_document_html(doc_id, publication, lang)
        if not self._html_error(html):  # never cache failures / bot blocks
            self._pages[key] = html
            while len(self._pages) > self._max_pages:
                self._pages.popitem(last=False)
        return html


class DeadlineCalculator:
    """Live, citation-backed procedural deadline calculator for Georgian law."""

    def __init__(self, rate_limit: float = 0.3, weekend_days: tuple = (5, 6)):
        """
        Args:
            rate_limit: Seconds between matsne requests (MatsneAPI default 0.3)
            weekend_days: Weekdays treated as rest days (დასვენების დღე); 5,6 = Sat, Sun.
                The codes refer to "rest days" without naming them; Labour Code art. 24(7)
                only guarantees weekly rest — Saturday/Sunday is the courts' working week.
        """
        self.api = _CachingMatsne(rate_limit=rate_limit)
        self.weekend_days = tuple(weekend_days)
        self._articles = {}

    # ── Live norm access ────────────────────────────────────────

    def _article(self, doc_id: int, article: str, part: Optional[int], as_of: str) -> dict:
        """get_article() with an in-instance cache; adds `body` (text minus history lines)."""
        key = (doc_id, article, part, as_of)
        if key not in self._articles:
            res = self.api.get_article(article, doc_id=doc_id, part=part, as_of_date=as_of)
            if "error" not in res:
                src = res.get("part_text") if part is not None else res.get("text", "")
                lines = (src or "").split("\n")
                if part is None and lines:
                    lines = lines[1:]  # drop "მუხლი N. heading"
                res["body"] = "\n".join(l for l in lines if not _HISTORY_LINE.match(l)).strip()
                if part is not None and not res["body"]:
                    res["error"] = res.get("part_error", f"Part {part} not found in article {article}")
            self._articles[key] = res
        return self._articles[key]

    @staticmethod
    def _cite(doc_id: int, article: str, part: Optional[int]) -> str:
        art = article.replace("^1", "¹").replace("^2", "²")
        return f"{LAW_NAMES.get(doc_id, doc_id)}, მუხლი {art}" + (f", ნაწილი {part}" if part else "")

    def _quote(self, doc_id: int, article: str, part: Optional[int], as_of: str) -> dict:
        res = self._article(doc_id, article, part, as_of)
        out = {"citation": self._cite(doc_id, article, part), "doc_id": doc_id,
               "article": article, "part": part}
        if "error" in res:
            out["error"] = res["error"]
            return out
        out.update(quote=res["body"], url=res.get("url", ""),
                   version_date=res.get("version_date", ""), publication=res.get("publication"))
        return out

    # ── Period extraction ───────────────────────────────────────

    @staticmethod
    def _sentences(text: str) -> list[str]:
        out = []
        for line in text.split("\n"):
            line = re.sub(r'^\d+[⁰¹²³⁴⁵⁶⁷⁸⁹]*\.\s*', '', line.strip())
            out += [s.strip() for s in re.split(r'(?<=[.;])\s+', line) if s.strip()]
        return out

    @staticmethod
    def _parse_number(raw: str) -> Optional[int]:
        if raw.isdigit():
            return int(raw)
        return _NUMBER_WORDS.get(raw, _NUMBER_WORDS.get(raw[:-1]) if raw.endswith("ი") else None)

    @classmethod
    def find_periods(cls, text: str) -> list[dict]:
        """
        All period expressions in a norm text, with the sentence each sits in.

        Returns:
            [{"value": int, "unit": "days"|"weeks"|"months"|"years", "working_days": bool,
              "calendar_days": bool, "text": matched text, "sentence": str, "pos": int}]
        """
        found = []
        for sentence in cls._sentences(text):
            for m in _PERIOD_RE.finditer(sentence):
                value = cls._parse_number(m.group("num"))
                unit = _UNIT_MAP[m.group("unit")]
                if not value or (unit == "years" and value >= 1000):  # "2015 წლის" is a date
                    continue
                adj = m.group("adj") or ""
                found.append({"value": value, "unit": unit,
                              "working_days": adj.startswith("სამუშაო"),
                              "calendar_days": adj.startswith("კალენდარულ"),
                              "text": m.group(0).strip(), "sentence": sentence, "pos": m.start()})
        return found

    def _extract(self, spec: dict, body: str) -> dict:
        """Pick the one period this procedure refers to, or explain why not."""
        periods = self.find_periods(body)
        if not periods:
            return {"ok": False, "reason": "No period expression found in the norm text"}
        near = spec.get("near")
        if near:
            picks = []
            for sentence in dict.fromkeys(p["sentence"] for p in periods):
                m = re.search(near, sentence)
                if m:
                    after = [p for p in periods if p["sentence"] == sentence and p["pos"] >= m.end()]
                    if after:
                        picks.append(min(after, key=lambda p: p["pos"]))
            distinct = {(p["value"], p["unit"]) for p in picks}
            if len(distinct) != 1:
                return {"ok": False, "candidates": [p["text"] for p in periods],
                        "reason": f"Selector /{near}/ matched {len(distinct)} distinct periods — ambiguous"}
            return {"ok": True, "period": picks[0], "candidates": [p["text"] for p in periods]}
        distinct = {(p["value"], p["unit"], p["working_days"]) for p in periods}
        if len(distinct) > 1:
            return {"ok": False, "candidates": [p["text"] for p in periods],
                    "reason": "The norm names several different periods — ambiguous, not computed"}
        return {"ok": True, "period": periods[0], "candidates": [p["text"] for p in periods]}

    # ── Counting rules and holidays ─────────────────────────────

    def _counting_rules(self, regime: str, as_of: str) -> tuple[list, bool]:
        """Fetch + verify every live rule of a regime. Returns (quotes, all_verified)."""
        quotes, ok = [], True
        for doc_id, article, part, check, role in REGIMES[regime]["rules"]:
            q = self._quote(doc_id, article, part, as_of)
            q["role"] = role
            q["verified"] = "quote" in q and bool(re.search(check, q["quote"], re.S))
            if not q["verified"]:
                ok = False
                q["verification_failed"] = (f"Expected wording /{check}/ not found — the rule may have "
                                            f"changed; arithmetic disabled")
            quotes.append(q)
        return quotes, ok

    def holidays(self, year: int, as_of_date: str = "") -> dict:
        """
        Public holidays (უქმე დღეები) of a year, parsed live from Labour Code art. 30(1)
        in the version in force on `as_of_date` (default: current), Easter days computed.

        Returns:
            {"year", "dates": [{"date", "label"}], "easter", "source": {citation, quote, url, ...}}
        """
        spec = self._holiday_spec(as_of_date)
        if "error" in spec:
            return spec
        days = self._holiday_dates(year, spec)
        return {"year": year, "easter": orthodox_easter(year).isoformat(),
                "dates": [{"date": d.isoformat(), "label": lbl} for d, lbl in sorted(days.items())],
                "source": spec["source"],
                "note": "Labour Code (holiday article, part 3): the Government may declare additional holidays by "
                        "decree — those are NOT included; pass them as extra_holidays."}

    def _holiday_article(self, as_of: str) -> str:
        """
        Number of the "უქმე დღეები" article in the Labour Code version in force on
        `as_of` — it moved (art. 20 before the 2020 reform, art. 30 now), so it is
        located by its heading, not hardcoded.
        """
        if as_of:
            picked = self.api.version_for_date(LABOUR, as_of)
            if "error" in picked:
                return ""
            html = self.api.get_document_html(LABOUR, picked["publication"])
        else:
            html = self.api.get_document_html(LABOUR)
        if self.api._html_error(html):
            return ""
        for art in self.api._split_articles(html):
            if re.search(r'უქმე\s+დღეები', art["label"]):
                return art["key"]
        return ""

    def _holiday_spec(self, as_of: str) -> dict:
        caveat = ""
        article = self._holiday_article(as_of)
        if not article and as_of:
            # e.g. dates before this Labour Code's first version (2010) → current text
            caveat = f"Labour Code holiday article as of {as_of} unavailable; current version used."
            as_of = ""
            article = self._holiday_article("")
        if not article:
            return {"error": "Could not locate the 'უქმე დღეები' article in the Labour Code"}
        q = self._quote(LABOUR, article, 1, as_of)
        if "error" in q:
            return {"error": f"Could not read Labour Code art. {article}: {q['error']}"}
        fixed, easter = [], []
        for line in q["quote"].split("\n"):
            m = re.match(r'^[^\s)]{1,4}\)\s*((?:\d{1,2}\s*(?:,|და)\s*)*\d{1,2})\s+(\S+)\s*[–—-]?\s*(.*)', line)
            if m:
                month = next((n for stem, n in _GE_MONTHS.items() if m.group(2).startswith(stem)), None)
                if month:
                    for day in re.findall(r'\d{1,2}', m.group(1)):
                        fixed.append((month, int(day), m.group(3).strip(" –—−-;.")[:80]))
                    continue
            if "აღდგომ" in line:
                for needle, offset, label in (("დიდი პარასკევი", -2, "დიდი პარასკევი (Good Friday)"),
                                              ("დიდი შაბათი", -1, "დიდი შაბათი (Holy Saturday)"),
                                              ("აღდგომის დღე", 0, "აღდგომა (Easter Sunday)"),
                                              ("აღდგომის მეორე დღე", 1, "აღდგომის მეორე დღე (Easter Monday)")):
                    if needle in line:
                        easter.append((offset, label))
        if len(fixed) < 5:
            return {"error": "Holiday list in Labour Code art. 30(1) could not be parsed reliably",
                    "source": q}
        source = {k: q[k] for k in ("citation", "quote", "url", "version_date", "publication")}
        if caveat:
            source["caveat"] = caveat
        return {"fixed": fixed, "easter": easter, "source": source}

    @staticmethod
    def _holiday_dates(year: int, spec: dict) -> dict:
        days = {}
        for month, day, label in spec["fixed"]:
            try:
                days[date(year, month, day)] = label
            except ValueError:
                pass
        e = orthodox_easter(year)
        for offset, label in spec["easter"]:
            days[e + timedelta(days=offset)] = label
        return days

    def _non_working(self, spec: dict, extra: set):
        cache = {}

        def reason(d: date) -> str:
            if d.year not in cache:
                cache[d.year] = self._holiday_dates(d.year, spec)
            if d in extra:
                return "extra holiday (passed by caller)"
            if d in cache[d.year]:
                return f"public holiday: {cache[d.year][d]}"
            if d.weekday() in self.weekend_days:
                return f"rest day ({_EN_WEEKDAYS[d.weekday()]})"
            return ""
        return reason

    # ── Arithmetic ──────────────────────────────────────────────

    def _compute(self, start: date, value: int, unit: str, regime: str, working_days: bool,
                 spec: dict, extra: set) -> dict:
        """Apply a regime's (verified) rules. Returns {end, steps, status, notes}."""
        reg = REGIMES[regime]
        why = self._non_working(spec, extra)
        steps = [f"Triggering event: {_fmt(start)} — this day is not counted; counting starts "
                 f"{(start + timedelta(days=1)).isoformat()}"]
        status, notes = "computed", []
        count_working = working_days or (reg["count"] == "working" and unit in ("days", "weeks"))

        if count_working:
            n = value * (7 if unit == "weeks" else 1)
            d, counted, skipped = start, 0, []
            while counted < n:
                d += timedelta(days=1)
                r = why(d)
                if r:
                    skipped.append(f"{d.isoformat()} ({r})")
                else:
                    counted += 1
            steps.append(f"Counted {n} working days (non-working days skipped: "
                         f"{', '.join(skipped) if skipped else 'none'}) → {_fmt(d)}")
            end = d
        else:
            if unit == "days":
                end = start + timedelta(days=value)
            elif unit == "weeks":
                end = start + timedelta(days=7 * value)
            else:
                months = value * (12 if unit == "years" else 1)
                end = _add_months(start, months)
                if end.day != start.day:
                    steps.append(f"{end.strftime('%m/%Y')} has no day {start.day} → last day of that month")
            steps.append(f"+{value} {unit} → last day {_fmt(end)}")
            if reg["count"] == "working":
                status = "conservative"
                notes.append("GAC art. 15 excludes holidays/rest days but the period is in "
                             f"{unit}; how to exclude them from a {unit}-based period is not spelled "
                             "out. The date given is the EARLIEST plausible one (calendar period, "
                             "then moved off a non-working day) — a stricter reading could give a "
                             "later date. Treat this date as the safe filing limit.")

        if reg["shift"]:
            while why(end):
                steps.append(f"{_fmt(end)} is a non-working day — {why(end)} → moved to next day")
                end += timedelta(days=1)
        elif why(end):
            steps.append(f"{_fmt(end)} is a non-working day ({why(end)}) but this regime does NOT "
                         f"move the deadline (non-working days are included)")
        steps.append(f"DEADLINE (last day): {_fmt(end)}")
        return {"end": end, "steps": steps, "status": status, "notes": notes}

    def _calculate(self, start: date, value: int, unit: str, regime: str, working_days: bool,
                   as_of: str, extra_holidays) -> dict:
        """Shared core: counting rules + holidays (version re-checked at the end date) + arithmetic."""
        rules, verified = self._counting_rules(regime, as_of)
        out = {"counting_regime": regime, "counting_regime_description": REGIMES[regime]["description"],
               "counting_rules": rules}
        if not verified:
            out.update(status="not_computed",
                       reason="A counting-rule norm could not be fetched or its wording changed — "
                              "read counting_rules and compute manually.")
            return out
        extra = {d for d in (_parse_input_date(x) for x in (extra_holidays or [])) if d}
        spec = self._holiday_spec(start.isoformat())
        if "error" in spec:
            out.update(status="not_computed", reason=spec["error"])
            return out
        res = self._compute(start, value, unit, regime, working_days, spec, extra)
        # Holidays may have changed between start and end: re-run with the end-date list
        spec_end = self._holiday_spec(res["end"].isoformat())
        if "error" not in spec_end and spec_end["source"].get("publication") != spec["source"].get("publication"):
            res = self._compute(start, value, unit, regime, working_days, spec_end, extra)
            spec = spec_end
        out.update(status=res["status"], end_date=res["end"].isoformat(),
                   end_date_display=res["end"].strftime("%d/%m/%Y"),
                   end_weekday=f"{_EN_WEEKDAYS[res['end'].weekday()]} / {_GE_WEEKDAYS[res['end'].weekday()]}",
                   steps=res["steps"], holidays_source=spec["source"],
                   rest_days=[_EN_WEEKDAYS[d] for d in self.weekend_days],
                   rest_days_note="The codes say 'rest days' without naming them; Saturday/Sunday "
                                  "is assumed (courts' 5-day week). Labour Code art. 24(7) only "
                                  "guarantees a weekly rest period.")
        if res["notes"]:
            out["interpretation_notes"] = res["notes"]
        return out

    # ── Public API ──────────────────────────────────────────────

    def list_procedures(self) -> list[dict]:
        """Available procedures with the law + article where their period is read (no network)."""
        return [{"procedure": key, "name_ka": p["name_ka"], "name_en": p["name_en"],
                 "law": LAW_NAMES[p["doc_id"]], "doc_id": p["doc_id"], "article": p["article"],
                 "part": p["part"], "counting_regime": p["regime"], "start_event": p["start_hint"]}
                for key, p in PROCEDURES.items()]

    def get_deadline_rule(self, procedure: str, as_of_date: str = "") -> dict:
        """
        The live norm behind a procedure: article text, quoted period, extracted
        duration, start-rule norms and the (verified) counting rules — no dates.

        Args:
            procedure: Key from list_procedures()
            as_of_date: Version of the law in force on this date (YYYY-MM-DD or DD/MM/YYYY);
                default = current version
        """
        spec = PROCEDURES.get(procedure)
        if not spec:
            return {"error": f"Unknown procedure '{procedure}'", "available": list(PROCEDURES)}
        as_of = ""
        if as_of_date:
            d = _parse_input_date(as_of_date)
            if not d:
                return {"error": f"Unrecognized date '{as_of_date}' — use YYYY-MM-DD or DD/MM/YYYY"}
            as_of = d.isoformat()
        res = self._article(spec["doc_id"], spec["article"], spec["part"], as_of)
        out = {"procedure": procedure, "name_ka": spec["name_ka"], "name_en": spec["name_en"],
               "citation": self._cite(spec["doc_id"], spec["article"], spec["part"]),
               "as_of_date": as_of or "current", "warning": WARNING}
        if "error" in res:
            out.update(error=res["error"], status="not_computed")
            return out
        out.update(norm_text=res["body"], url=res.get("url", ""),
                   version_date=res.get("version_date", ""), publication=res.get("publication"))
        if res.get("caveat"):
            out["version_caveat"] = res["caveat"]
        ext = self._extract(spec, res["body"])
        if ext["ok"]:
            p = ext["period"]
            out["period"] = {"value": p["value"], "unit": p["unit"], "working_days": p["working_days"],
                             "calendar_days": p["calendar_days"], "matched_text": p["text"],
                             "quoted_sentence": p["sentence"]}
            out["status"] = "extracted"
        else:
            out.update(status="not_extracted", reason=ext["reason"],
                       candidates=ext.get("candidates", []))
        out["start_event"] = spec["start_hint"] + " — confirm against the quoted sentence"
        out["related_norms"] = [self._quote(d, a, p, as_of) for d, a, p in spec.get("related", [])]
        out["counting_rules"], out["counting_rules_verified"] = self._counting_rules(spec["regime"], as_of)
        out["counting_regime"] = spec["regime"]
        return out

    def calculate_deadline(self, procedure: str, start_date: str, as_of_date: str = "",
                           extra_holidays: Optional[list] = None) -> dict:
        """
        Compute the last day of a procedural term from the live norm.

        Args:
            procedure: Key from list_procedures()
            start_date: Triggering event (delivery/announcement/acquaintance...), YYYY-MM-DD or DD/MM/YYYY
            as_of_date: Law version date; default = start_date (version in force on the event day)
            extra_holidays: Additional non-working dates declared by Government decree

        Returns:
            {"status": "computed"|"conservative"|"not_computed", "end_date", "steps",
             "period": {value, unit, matched_text, quoted_sentence}, "url", "counting_rules",
             "holidays_source", "related_norms", "warning", ...}
        """
        start = _parse_input_date(start_date)
        if not start:
            return {"error": f"Unrecognized start_date '{start_date}' — use YYYY-MM-DD or DD/MM/YYYY",
                    "warning": WARNING}
        rule = self.get_deadline_rule(procedure, as_of_date or start.isoformat())
        if "error" in rule and "procedure" not in rule:
            return {**rule, "warning": WARNING}
        out = {k: rule[k] for k in ("procedure", "name_ka", "name_en", "citation", "as_of_date")}
        out["start_date"] = start.isoformat()
        out.update({k: rule[k] for k in ("url", "version_date", "publication", "version_caveat",
                                         "period", "start_event", "related_norms") if k in rule})
        if rule.get("status") != "extracted":
            out.update(status="not_computed",
                       reason=rule.get("reason") or rule.get("error", "norm not available"),
                       norm_text=rule.get("norm_text", ""), candidates=rule.get("candidates", []),
                       counting_rules=rule.get("counting_rules", []), warning=WARNING)
            return out
        p = rule["period"]
        calc = self._calculate(start, p["value"], p["unit"], rule["counting_regime"],
                               p["working_days"], rule["as_of_date"] if rule["as_of_date"] != "current" else "",
                               extra_holidays)
        out.update(calc)
        out["warning"] = WARNING
        return out

    def calculate_custom(self, start_date: str, value: int, unit: str = "days",
                         regime: str = "civil_procedure", law: str = "", article: str = "",
                         part: Optional[int] = None, as_of_date: str = "",
                         extra_holidays: Optional[list] = None) -> dict:
        """
        Compute a period the agent found itself (e.g. in a special law), with live counting rules.

        Args:
            start_date: Triggering event date
            value, unit: e.g. 30, "days" | "weeks" | "months" | "years" | "working_days"
            regime: civil_procedure | administrative_procedure | criminal_procedure |
                    general_administrative | civil_code
            law, article, part: Source norm (name or doc_id) — fetched and quoted; if the norm
                names exactly one period and it differs from value/unit, a mismatch is flagged
            as_of_date: Law version date; default = start_date
        """
        start = _parse_input_date(start_date)
        if not start:
            return {"error": f"Unrecognized start_date '{start_date}'", "warning": WARNING}
        unit = _UNIT_ALIASES.get(str(unit).lower().strip())
        if not unit:
            return {"error": "unit must be days, weeks, months, years or working_days", "warning": WARNING}
        if regime not in REGIMES:
            return {"error": f"Unknown regime '{regime}'", "available": list(REGIMES), "warning": WARNING}
        try:
            value = int(value)
        except (TypeError, ValueError):
            return {"error": f"value must be an integer, got '{value}'", "warning": WARNING}
        if value <= 0:
            return {"error": "value must be positive", "warning": WARNING}
        as_of = (_parse_input_date(as_of_date) or start).isoformat()
        working = unit == "working_days"
        unit = "days" if working else unit
        out = {"start_date": start.isoformat(), "period": {"value": value, "unit": unit,
                                                           "working_days": working,
                                                           "source": "supplied by caller"},
               "as_of_date": as_of}
        if article:
            doc_id = int(law) if str(law).isdigit() else 0
            if not doc_id:
                resolved = self.api.resolve_law(law)
                if "error" in resolved:
                    return {**out, "error": resolved["error"], "warning": WARNING}
                doc_id = resolved["doc_id"]
            q = self._quote(doc_id, self.api._article_key(article), part, as_of)
            out["source_norm"] = q
            if "quote" in q:
                found = {(p["value"], p["unit"]) for p in self.find_periods(q["quote"])}
                out["source_norm"]["periods_in_text"] = sorted(f"{v} {u}" for v, u in found)
                if found and (value, unit) not in found:
                    out["mismatch"] = (f"The cited norm mentions {sorted(found)}, not {value} {unit} — "
                                       f"re-check the period before relying on this date.")
        else:
            out["source_norm"] = "not given — the agent must cite the norm the period comes from"
        out.update(self._calculate(start, value, unit, regime, working, as_of, extra_holidays))
        out["warning"] = WARNING
        return out


# ════════════════════════════════════════════════════════════════
# CLI / Live test
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    dc = DeadlineCalculator()

    print("Easter check:", [orthodox_easter(y).isoformat() for y in (2024, 2025, 2026, 2027)])
    print("\nProcedures:")
    for p in dc.list_procedures():
        print(f"  {p['procedure']:38} art. {p['article']}" + (f"({p['part']})" if p['part'] else "")
              + f"  doc {p['doc_id']}")

    print("\nRule extraction (current versions):")
    for key in PROCEDURES:
        r = dc.get_deadline_rule(key)
        per = r.get("period")
        print(f"  {key:38} {r['status']:13} "
              + (f"{per['value']} {per['unit']} ← «{per['matched_text']}»" if per else r.get("reason", r.get("error", "")))
              + ("" if r.get("counting_rules_verified", True) else "  [COUNTING RULES NOT VERIFIED]"))

    def show(res):
        print(f"\n== {res.get('procedure', 'custom')}  start={res.get('start_date')}  "
              f"law-version={res.get('version_date', res.get('as_of_date'))}")
        if res.get("period", {}).get("quoted_sentence"):
            print(f"  «{res['period']['quoted_sentence'][:160]}»")
            print(f"  {res.get('url')}")
        for s in res.get("steps", []):
            print("   ", s)
        print(f"  status={res.get('status')} end={res.get('end_date')} {res.get('reason', '')}")
        for n in res.get("interpretation_notes", []):
            print("  note:", n[:140])
        if res.get("mismatch"):
            print("  MISMATCH:", res["mismatch"])

    show(dc.calculate_deadline("civil_appeal", "2026-04-27"))            # +14d → 11 May (Mon)
    show(dc.calculate_deadline("civil_private_complaint", "2026-05-14"))  # +12d → 26 May = holiday
    show(dc.calculate_deadline("civil_cassation", "2026-01-31"))
    show(dc.calculate_deadline("civil_default_judgment_objection", "2025-12-29"))
    show(dc.calculate_deadline("admin_lawsuit_individual_act", "2026-01-31"))  # month end clamp
    show(dc.calculate_deadline("gac_complaint_act", "2026-03-07"))
    show(dc.calculate_deadline("criminal_appeal", "2026-03-09"))           # no shift
    show(dc.calculate_deadline("criminal_restore_term_motion", "2026-04-08"))
    show(dc.calculate_deadline("labour_dismissal_lawsuit", "2026-04-10"))
    show(dc.calculate_deadline("civil_appeal", "2015-06-01"))              # historical version
    show(dc.calculate_custom("2026-04-08", 7, "days", regime="administrative_procedure",
                             law="ადმინისტრაციული საპროცესო კოდექსი", article="21^107", part=1))
    print("\nHolidays 2026:", [(h["date"], h["label"][:25]) for h in dc.holidays(2026)["dates"]])
    print("\nCached pages:", len(dc.api._pages))
