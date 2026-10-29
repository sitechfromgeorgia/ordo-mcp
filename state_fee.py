#!/usr/bin/env python3
"""
state_fee.py — court state-fee (სახელმწიფო ბაჟი) helper for Georgian law (SAFE mode)
====================================================================================
Computes the state fee for civil and administrative court filings (claim,
appeal, cassation, private complaint, claim security, uncontested proceedings,
arbitration/mediation applications...) WITHOUT hardcoding any amount.

How it stays safe (same design as deadlines.py):
  * The module stores only a map  fee type → (Civil Procedure Code art. 39,
    part 1, sub-point letter, keyword that must appear in that sub-point).
    The percentage, the minimum and the fixed amounts are read live from
    matsne.gov.ge, from the version of the code in force on the filing date,
    and the exact sub-point is quoted.
  * Ceilings (art. 39(3), per instance and per party type), the magistrate
    halving (art. 39(2)) and the settlement/withdrawal reductions (art. 49)
    are also read live and regex-checked. If the wording changed, the step is
    not applied and the result says so.
  * A renumbered sub-point (keyword missing) or an unexpected number of
    amounts → no figure; the norm text is returned instead.
  * Every result carries a `warning` the agent must relay to the user.

Zero dependencies (stdlib only) — reuses the live-norm access of deadlines.py.

Usage:
    from state_fee import FeeCalculator
    fc = FeeCalculator()
    fc.list_fee_types()
    fc.get_fee_rule("appeal_property")
    fc.calculate_fee("claim_property", claim_value=250000, party_type="legal_entity")
"""

import re
import sys
from typing import Optional

from deadlines import APC, CPC, DeadlineCalculator, _parse_input_date

# ── Warning carried by every result ─────────────────────────────

WARNING = {
    "ka": ("ყურადღება: ეს არის ავტომატური გამოთვლა ციტირებული ნორმის საფუძველზე და არა "
           "იურიდიული კონსულტაცია. აუცილებლად გადაამოწმეთ: დავის საგნის ღირებულება (სსსკ 40–41 "
           "მუხლები — მას მოსამართლე შეიძლება შეცვალოს), დავის კატეგორია (ქონებრივი/არაქონებრივი), "
           "გათავისუფლების საფუძვლები (სსსკ 46–48, ასკ მე-9 მუხლი და სპეციალური კანონები), "
           "მაგისტრატის განსჯადობა, ნორმის რედაქცია ფაილის შეტანის დღეს და სასამართლოს მიმდინარე "
           "საბანკო რეკვიზიტები. ბაჟის გადაუხდელობა ან არასრული გადახდა სარჩელის/საჩივრის "
           "განუხილველად დატოვების საფუძველი შეიძლება გახდეს — მიმართეთ სასამართლოს კანცელარიას "
           "ან ადვოკატს."),
    "en": ("WARNING: this is an automated calculation from the cited norm, not legal advice. "
           "Verify: the value of the claim (CPC arts. 40–41 — the judge may re-assess it), the type of "
           "dispute (property/non-property), exemptions (CPC arts. 46–48, APC art. 9 and special laws), "
           "magistrate jurisdiction, the version of the law on the filing date and the court's current "
           "payment details. An unpaid or underpaid fee can leave the claim/complaint without "
           "consideration — check with the court registry or a lawyer."),
    "agent_instruction": ("Relay this warning to the user together with the amount, quote the cited "
                          "sub-point and give the article URL. Never present the amount as certain."),
}

FEE_ARTICLE = "39"

# ── Fee types: where each amount lives in CPC art. 39(1) ────────
# sub:      sub-point label in art. 39 part 1 ("ა", "ა3", "ე1" — superscripts as digits)
# near:     keyword that must be in that sub-point (tripwire against renumbering)
# kind:     "percent" (N% but not less than M GEL) or "fixed" (amount list, take `pick`)
# amounts:  how many GEL amounts the fixed sub-point must contain
# instance: first / appeal / cassation — selects the art. 39(3) ceiling (percent kinds)

FEE_TYPES = {
    "claim_property": {
        "sub": "ა", "near": "დავის საგნის ღირებულების", "kind": "percent", "instance": "first",
        "name_ka": "სარჩელი ქონებრივ დავაზე (ასევე შეგებებული, მესამე პირის, ადმინისტრაციული სარჩელი)",
        "name_en": "Property claim (also counterclaim, third-party claim, administrative claim)"},
    "appeal_property": {
        "sub": "ბ", "near": "სააპელაციო საჩივ", "kind": "percent", "instance": "appeal",
        "name_ka": "სააპელაციო საჩივარი ქონებრივ დავაზე",
        "name_en": "Appeal in a property dispute"},
    "cassation_property": {
        "sub": "გ", "near": "საკასაციო საჩივ", "kind": "percent", "instance": "cassation",
        "name_ka": "საკასაციო საჩივარი ქონებრივ დავაზე",
        "name_en": "Cassation complaint in a property dispute"},
    "claim_non_property": {
        "sub": "თ", "near": "არაქონებრივ", "kind": "fixed", "amounts": 3, "pick": 0,
        "name_ka": "სარჩელი არაქონებრივ დავაზე", "name_en": "Non-property claim"},
    "appeal_non_property": {
        "sub": "თ", "near": "არაქონებრივ", "kind": "fixed", "amounts": 3, "pick": 1,
        "name_ka": "სააპელაციო საჩივარი არაქონებრივ დავაზე", "name_en": "Appeal, non-property dispute"},
    "cassation_non_property": {
        "sub": "თ", "near": "არაქონებრივ", "kind": "fixed", "amounts": 3, "pick": 2,
        "name_ka": "საკასაციო საჩივარი არაქონებრივ დავაზე",
        "name_en": "Cassation complaint, non-property dispute"},
    "private_complaint": {
        "sub": "დ", "near": "კერძო საჩივარ", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "კერძო საჩივარი", "name_en": "Private (interlocutory) complaint"},
    "claim_security": {
        "sub": "ე", "near": "სარჩელის უზრუნველყოფ", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "განცხადება სარჩელის უზრუნველყოფის შესახებ", "name_en": "Application to secure a claim"},
    "reopening_new_circumstances": {
        "sub": "ვ", "near": "ახლად აღმოჩენილ", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "განცხადება ახლად აღმოჩენილ გარემოებათა გამო წარმოების განახლებაზე",
        "name_en": "Application to reopen on newly discovered circumstances"},
    "annulment_of_decision": {
        "sub": "ზ", "near": "ბათილად ცნობ", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "განცხადება გადაწყვეტილების ბათილად ცნობის შესახებ",
        "name_en": "Application to annul a decision"},
    "lost_case_restoration": {
        "sub": "ი", "near": "დაკარგული საქმის", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "დავა დაკარგული საქმის აღდგენაზე (მხარის ბრალით)",
        "name_en": "Restoring a lost case file (lost through a party's fault)"},
    "bill_of_exchange_claim": {
        "sub": "კ", "near": "თამასუქ", "kind": "fixed", "amounts": 3, "pick": 0,
        "name_ka": "სარჩელი თამასუქის ან ჩეკის თაობაზე", "name_en": "Claim on a bill of exchange or cheque"},
    "bill_of_exchange_appeal": {
        "sub": "კ", "near": "თამასუქ", "kind": "fixed", "amounts": 3, "pick": 1,
        "name_ka": "სააპელაციო საჩივარი (თამასუქი/ჩეკი)", "name_en": "Appeal (bill of exchange/cheque)"},
    "bill_of_exchange_cassation": {
        "sub": "კ", "near": "თამასუქ", "kind": "fixed", "amounts": 3, "pick": 2,
        "name_ka": "საკასაციო საჩივარი (თამასუქი/ჩეკი)", "name_en": "Cassation (bill of exchange/cheque)"},
    "uncontested_proceedings": {
        "sub": "ლ", "near": "უდავო წარმოებ", "kind": "fixed", "amounts": 3, "pick": 0,
        "name_ka": "უდავო წარმოების საქმე", "name_en": "Uncontested (non-contentious) proceedings"},
    "uncontested_appeal": {
        "sub": "ლ", "near": "უდავო წარმოებ", "kind": "fixed", "amounts": 3, "pick": 1,
        "name_ka": "სააპელაციო საჩივარი (უდავო წარმოება)", "name_en": "Appeal (uncontested proceedings)"},
    "uncontested_cassation": {
        "sub": "ლ", "near": "უდავო წარმოებ", "kind": "fixed", "amounts": 3, "pick": 2,
        "name_ka": "საკასაციო საჩივარი (უდავო წარმოება)", "name_en": "Cassation (uncontested proceedings)"},
    "entrepreneurs_law_application": {
        "sub": "მ", "near": "მეწარმეთა", "kind": "fixed", "amounts": 2, "pick": 0,
        "name_ka": "განცხადება „მეწარმეთა შესახებ“ კანონით გათვალისწინებულ საკითხზე",
        "name_en": "Application under the Law on Entrepreneurs"},
    "mandatory_share_sale": {
        "sub": "მ", "near": "აქციათა სავალდებულო", "kind": "fixed", "amounts": 2, "pick": 1,
        "name_ka": "განცხადება აქციათა სავალდებულო მიყიდვის თაობაზე",
        "name_en": "Application for mandatory sale of shares (squeeze-out)"},
    "arbitration_assistance": {
        "sub": "ა1", "near": "არბიტრ", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "საარბიტრაჟო წარმოებაში დახმარება / არბიტრის დანიშვნა, აცილება, კომპეტენცია",
        "name_en": "Court assistance in arbitration / appointment, challenge, competence of arbitrators"},
    "arbitration_award": {
        "sub": "ა2", "near": "საარბიტრაჟო გადაწყვეტილების", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "საარბიტრაჟო გადაწყვეტილების გაუქმება / ცნობა და აღსრულება",
        "name_en": "Setting aside / recognition and enforcement of an arbitral award"},
    "court_mediation_claim": {
        "sub": "ა3", "near": "187", "kind": "percent", "instance": "first", "first_sentence": True,
        "name_ka": "სარჩელი, რომელიც სავალდებულო სასამართლო მედიაციას ექვემდებარება (სსსკ 187³)",
        "name_en": "Claim subject to mandatory court mediation (CPC art. 187³)"},
    "mediation_security": {
        "sub": "ა6", "near": "სამედიაციო მორიგების უზრუნველყოფ", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "სამედიაციო მორიგების უზრუნველყოფის ღონისძიება",
        "name_en": "Interim measure securing a mediation settlement"},
    "mediation_settlement_enforcement": {
        "sub": "ა7", "near": "სამედიაციო მორიგების აღსრულ", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "სამედიაციო მორიგების აღსრულება / საერთაშორისო მორიგების ცნობა",
        "name_en": "Enforcement of a mediation settlement / recognition of an international one"},
    "arbitration_interim_measures": {
        "sub": "ე1", "near": "საარბიტრაჟო წარმოებასთან", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "სარჩელის უზრუნველყოფა საარბიტრაჟო წარმოებასთან დაკავშირებით",
        "name_en": "Interim measures in support of arbitration"},
    "cape_town_interim_measure": {
        "sub": "ე2", "near": "კეიპტაუნის", "kind": "fixed", "amounts": 1, "pick": 0,
        "name_ka": "კეიპტაუნის კონვენციით გათვალისწინებული დროებითი ღონისძიება",
        "name_en": "Interim relief under the Cape Town Convention"},
}

# Ceilings (art. 39(3)): instance → sub-point letter + keyword of its heading line.
_CAP_INSTANCES = {"first": ("ა", "პირველი ინსტანციის"), "appeal": ("ბ", "სააპელაციო"),
                  "cassation": ("გ", "საკასაციო")}
_PARTY = {"individual": ("ა", "ფიზიკური"), "legal_entity": ("ბ", "იურიდიული")}
_PARTY_ALIASES = {"individual": "individual", "natural": "individual", "person": "individual",
                  "ფიზიკური": "individual", "legal_entity": "legal_entity", "legal": "legal_entity",
                  "company": "legal_entity", "იურიდიული": "legal_entity"}
_SETTLEMENT = {"before_main_hearing": (2, r"მთავარ\s+სხდომამდე.*მთლიანად\s+თავისუფლდებიან"),
               "at_main_hearing": (1, r"მთავარ\s+სხდომაზე.*ნახევრდება")}

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUB_LABEL = re.compile(r'^([ა-ჰ])\s*(\d*)\s*\)\s*')
_GEL = r'(\d{1,3}(?:[  ]\d{3})+|\d+)'
_AMOUNT = re.compile(_GEL + r'(?=\s*(?:ლარ|და\s+\d[\d  ]*\s*ლარ))')
_PERCENT = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:%|პროცენტ)')
_MINIMUM = re.compile(r'არანაკლებ\s+' + _GEL + r'\s*ლარ')


def _norm(line: str) -> str:
    """Strip zero-width/nbsp noise and turn superscripts into digits ("ა ³)" → "ა 3)")."""
    return re.sub(r'[​‌‍﻿]', '', line).replace(" ", " ").translate(_SUP).strip()


def _gel(raw: str) -> float:
    return float(re.sub(r'\s', '', raw).replace(",", "."))


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


class FeeCalculator:
    """Live, citation-backed court state-fee calculator (CPC art. 39)."""

    def __init__(self, deadlines: Optional[DeadlineCalculator] = None, rate_limit: float = 0.3):
        """
        Args:
            deadlines: Share an existing DeadlineCalculator (its cached matsne pages + article cache)
            rate_limit: Seconds between matsne requests when a new one is created
        """
        self.dc = deadlines or DeadlineCalculator(rate_limit=rate_limit)

    # ── Live norm access ────────────────────────────────────────

    def _part(self, part: int, as_of: str, doc_id: int = CPC, article: str = FEE_ARTICLE) -> dict:
        return self.dc._article(doc_id, article, part, as_of)

    def _quote(self, doc_id: int, article: str, part: Optional[int], as_of: str) -> dict:
        return self.dc._quote(doc_id, article, part, as_of)

    @staticmethod
    def _subpoints(body: str) -> dict:
        """Part text → {"ა": text, "ა3": text, ...} (continuation lines appended)."""
        subs, key = {}, None
        for line in body.split("\n"):
            line = _norm(line)
            line = re.sub(r'^\d+[.]\s*', '', line) if key is None else line
            m = _SUB_LABEL.match(line)
            if m:
                key = m.group(1) + m.group(2)
                subs[key] = line[m.end():].strip()
            elif key and line:
                subs[key] += " " + line
        return subs

    @staticmethod
    def _parse(spec: dict, text: str) -> dict:
        """Amounts from one sub-point; {"ok": False, "reason"} when the shape is unexpected."""
        if spec.get("first_sentence"):
            text = re.split(r'(?<=[.;])\s', text, maxsplit=1)[0]
        if spec["kind"] == "percent":
            pct, mins = _PERCENT.findall(text), _MINIMUM.findall(text)
            if len(pct) != 1 or len(mins) != 1:
                return {"ok": False, "reason": f"expected one percentage and one minimum, found "
                                               f"{len(pct)} / {len(mins)}"}
            return {"ok": True, "percent": _gel(pct[0]), "minimum": _gel(mins[0])}
        amounts = [_gel(a) for a in _AMOUNT.findall(text)]
        if len(amounts) != spec["amounts"]:
            return {"ok": False, "reason": f"expected {spec['amounts']} GEL amount(s), found {amounts}"}
        return {"ok": True, "amounts": amounts, "fixed": amounts[spec["pick"]]}

    def _caps(self, as_of: str) -> dict:
        """art. 39(3) → {"first": {"individual": 3000, "legal_entity": 5000}, ...} or {"error"}."""
        res = self._part(3, as_of)
        if "error" in res:
            return {"error": res["error"]}
        caps, current = {}, None
        for line in res["body"].split("\n"):
            line = _norm(line)
            m = re.match(r'^([ა-ჰ])\)\s*(.*)', line)
            if m:
                current = next((inst for inst, (letter, kw) in _CAP_INSTANCES.items()
                                if letter == m.group(1) and kw in m.group(2)), None)
                continue
            m = re.match(r'^([ა-ჰ])\.([ა-ჰ])\)\s*(.*)', line)
            if m and current:
                party = next((p for p, (letter, kw) in _PARTY.items()
                              if letter == m.group(2) and kw in m.group(3)), None)
                amt = re.search(r'[–−-]\s*' + _GEL + r'\s*ლარ', m.group(3))
                if party and amt:
                    caps.setdefault(current, {})[party] = _gel(amt.group(1))
        complete = all(len(caps.get(i, {})) == 2 for i in _CAP_INSTANCES)
        return {"caps": caps, "verified": complete, "quote": res["body"], "url": res.get("url", "")}

    def _magistrate_rule(self, as_of: str) -> dict:
        res = self._part(2, as_of)
        if "error" in res:
            return {"verified": False, "error": res["error"]}
        ok = bool(re.search(r'მაგისტრატ.*პირველი\s+ნაწილით.*ნახევარ', res["body"]))
        return {"verified": ok, "quote": res["body"], "url": res.get("url", "")}

    # ── Public API ──────────────────────────────────────────────

    def list_fee_types(self) -> list[dict]:
        """Available fee types and the CPC art. 39(1) sub-point each is read from (no network)."""
        return [{"fee_type": k, "name_ka": s["name_ka"], "name_en": s["name_en"],
                 "article": f"39(1)({s['sub']})", "kind": s["kind"],
                 "needs_claim_value": s["kind"] == "percent"} for k, s in FEE_TYPES.items()]

    def get_fee_rule(self, fee_type: str, as_of_date: str = "") -> dict:
        """
        The live norm behind a fee type: quoted sub-point, extracted percentage/minimum or fixed
        amount, the ceilings (39(3)) and the magistrate rule (39(2)) — no calculation.

        Args:
            fee_type: Key from list_fee_types()
            as_of_date: Version of the code in force on this date (default: current)
        """
        spec = FEE_TYPES.get(fee_type)
        if not spec:
            return {"error": f"Unknown fee_type '{fee_type}'", "available": list(FEE_TYPES),
                    "warning": WARNING}
        as_of = ""
        if as_of_date:
            d = _parse_input_date(as_of_date)
            if not d:
                return {"error": f"Unrecognized date '{as_of_date}' — use YYYY-MM-DD or DD/MM/YYYY",
                        "warning": WARNING}
            as_of = d.isoformat()
        out = {"fee_type": fee_type, "name_ka": spec["name_ka"], "name_en": spec["name_en"],
               "citation": self.dc._cite(CPC, FEE_ARTICLE, 1) + f", ქვეპუნქტი „{spec['sub']}“",
               "as_of_date": as_of or "current"}
        res = self._part(1, as_of)
        if "error" in res:
            out.update(status="not_extracted", reason=res["error"], warning=WARNING)
            return out
        out.update(url=res.get("url", ""), version_date=res.get("version_date", ""),
                   publication=res.get("publication"))
        if res.get("caveat"):
            out["version_caveat"] = res["caveat"]
        sub_text = self._subpoints(res["body"]).get(spec["sub"])
        if sub_text is None or spec["near"] not in sub_text:
            out.update(status="not_extracted", norm_text=res["body"],
                       reason=(f"sub-point '{spec['sub']}' " + ("not found" if sub_text is None else
                               f"no longer mentions '{spec['near']}' (renumbered?)")
                               + " — read norm_text"), warning=WARNING)
            return out
        out["quoted_subpoint"] = sub_text
        parsed = self._parse(spec, sub_text)
        if not parsed.pop("ok"):
            out.update(status="not_extracted", reason=parsed["reason"], warning=WARNING)
            return out
        out["rule"] = parsed
        out["status"] = "extracted"
        if spec["kind"] == "percent":
            caps = self._caps(as_of)
            out["ceilings"] = {"instance": spec["instance"], **caps}
        out["magistrate_rule"] = self._magistrate_rule(as_of)
        out["warning"] = WARNING
        return out

    def calculate_fee(self, fee_type: str, claim_value: Optional[float] = None, party_type: str = "",
                      magistrate: bool = False, filing_date: str = "", settlement: str = "",
                      administrative: bool = False) -> dict:
        """
        Compute the state fee from the live norm.

        Args:
            fee_type: Key from list_fee_types()
            claim_value: Value of the claim in GEL (required for percentage fee types; see CPC 40–41)
            party_type: "individual" | "legal_entity" — selects the art. 39(3) ceiling
            magistrate: Case falls under a magistrate judge (art. 39(2) halving)
            filing_date: Filing date → version of the code in force (default: current)
            settlement: "" | "at_main_hearing" (fee halved) | "before_main_hearing" (fee waived) —
                withdrawal, admission or settlement (art. 49)
            administrative: Administrative case — adds APC art. 9 (exemptions, halving on termination)

        Returns:
            {"status": "computed"|"conservative"|"needs_input"|"not_computed", "amount_gel",
             "steps", "quoted_subpoint", "url", "notes", "related_norms", "warning", ...}
        """
        as_of = ""
        if filing_date:
            d = _parse_input_date(filing_date)
            if not d:
                return {"error": f"Unrecognized filing_date '{filing_date}' — use YYYY-MM-DD or DD/MM/YYYY",
                        "warning": WARNING}
            as_of = d.isoformat()
        rule = self.get_fee_rule(fee_type, as_of)
        if "fee_type" not in rule:
            return rule
        out = {k: rule[k] for k in ("fee_type", "name_ka", "name_en", "citation", "as_of_date", "url",
                                    "version_date", "publication", "version_caveat", "quoted_subpoint")
               if k in rule}
        party = _PARTY_ALIASES.get((party_type or "").strip().lower(), "") if party_type else ""
        if party_type and not party:
            return {**out, "error": f"party_type must be 'individual' or 'legal_entity', got '{party_type}'",
                    "warning": WARNING}
        if settlement and settlement not in _SETTLEMENT:
            return {**out, "error": f"settlement must be one of {list(_SETTLEMENT)}", "warning": WARNING}
        out["related_norms"] = self._related(as_of, administrative)
        if rule.get("status") != "extracted":
            out.update(status="not_computed", reason=rule.get("reason"),
                       norm_text=rule.get("norm_text", ""), warning=WARNING)
            return out

        spec, r = FEE_TYPES[fee_type], rule["rule"]
        steps, notes, status = [], [], "computed"
        if spec["kind"] == "percent":
            if claim_value is None:
                out.update(status="needs_input", rule=r, ceilings=rule.get("ceilings"),
                           reason="claim_value (GEL) is required for this fee type — see CPC art. 40–41",
                           warning=WARNING)
                return out
            value = float(claim_value)
            if value < 0:
                return {**out, "error": "claim_value must be ≥ 0", "warning": WARNING}
            raw = value * r["percent"] / 100
            amount = max(raw, r["minimum"])
            steps.append(f"{value:,.2f} × {r['percent']:g}% = {raw:,.2f} GEL"
                         + (f" → minimum {r['minimum']:,.2f} GEL applies" if amount > raw else ""))
        else:
            amount = r["fixed"]
            steps.append(f"fixed amount: {amount:,.2f} GEL")
            if claim_value is not None:
                notes.append("claim_value ignored — this fee type is a fixed amount")

        if magistrate:
            mr = rule["magistrate_rule"]
            if mr["verified"]:
                amount /= 2
                steps.append(f"magistrate case, art. 39(2) — half: {amount:,.2f} GEL")
            else:
                status = "conservative"
                notes.append("magistrate halving NOT applied — art. 39(2) wording not recognized; "
                             "read the quoted part 2")

        if spec["kind"] == "percent":
            ce = rule.get("ceilings", {})
            caps = ce.get("caps", {}).get(spec["instance"], {})
            if "error" in ce or not ce.get("verified"):
                status = "conservative"
                notes.append("ceilings (art. 39(3)) not verified — no ceiling applied")
            elif party:
                if amount > caps[party]:
                    amount = caps[party]
                    steps.append(f"ceiling art. 39(3), {spec['instance']} instance, {party}: "
                                 f"{amount:,.2f} GEL")
                    if magistrate:
                        notes.append("magistrate case hitting the ceiling: halving was applied before "
                                     "the ceiling (higher result). If the court applies the ceiling first, "
                                     f"the fee would be {caps[party] / 2:,.2f} GEL — confirm with the court")
                        status = "conservative"
            else:
                over = {p: c for p, c in caps.items() if amount > c}
                if over:
                    out.update(status="needs_input", amount_before_ceiling=_money(amount),
                               amount_by_party_type={p: _money(min(amount, c)) for p, c in caps.items()},
                               steps=steps, reason="the art. 39(3) ceiling depends on party_type "
                               "('individual' or 'legal_entity')", warning=WARNING)
                    return out

        if settlement:
            part_no, pattern = _SETTLEMENT[settlement]
            res = self._part(part_no, as_of, article="49")
            ok = "error" not in res and re.search(pattern, _norm(res.get("body", "")))
            cite = self.dc._cite(CPC, "49", part_no)
            if ok:
                due = 0.0 if settlement == "before_main_hearing" else amount / 2
                steps.append(f"{cite}: fee due {due:,.2f} GEL (refund of the difference "
                             f"{amount - due:,.2f} GEL if already paid)")
                out["fee_before_settlement_gel"] = _money(amount)
                amount = due
                notes.append("art. 49(3)–(4): partial withdrawal/admission counts only the remaining part; "
                             "on appeal/cassation the reduction applies only to that instance's fee")
            else:
                status = "conservative"
                notes.append(f"{cite} wording not recognized — settlement reduction NOT applied")

        if fee_type == "court_mediation_claim":
            notes.append("art. 39(1)(ა³): if court mediation fails, an additional fee is due on resumption "
                         "— see the quoted sub-point")
        if fee_type == "claim_property":
            notes.append("art. 39(1)(ა) excludes claims under art. 187³ (court mediation) — "
                         "use fee_type 'court_mediation_claim' for those")
        out.update(status=status, amount_gel=_money(amount), steps=steps, rule=r)
        if notes:
            out["notes"] = notes
        if spec["kind"] == "percent":
            out["ceilings"] = rule.get("ceilings", {}).get("caps")
        out["warning"] = WARNING
        return out

    def _related(self, as_of: str, administrative: bool) -> list[dict]:
        """Citations (no full quotes, to keep results small) for value, exemptions and reductions."""
        refs = [(CPC, "40", None, "claim value — who sets it, sum of claims, prices at filing"),
                (CPC, "41", None, "how the value is determined (alimony = 1 year, periodic = ≤3 years, "
                                  "unassessable property dispute = fixed value...)"),
                (CPC, "46", None, "statutory exemptions (alimony, health damage, crime damage, minors, "
                                  "socially vulnerable, domestic violence victims...)"),
                (CPC, "47", None, "exemption by the court for inability to pay"),
                (CPC, "48", None, "deferral / reduction by the court"),
                (CPC, "49", None, "halving / waiver on withdrawal, admission, settlement, mediation")]
        if administrative:
            refs.append((APC, "9", None, "administrative cases: exemptions (social protection, chapters "
                                         "VII³, VII⁹, VII¹⁷, VII²²) and halving on termination"))
        out = []
        for doc, art, part, why in refs:
            q = self._quote(doc, art, part, as_of)
            out.append({"citation": q["citation"], "url": q.get("url", ""), "relevance": why,
                        **({"error": q["error"]} if "error" in q else {})})
        return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    fc = FeeCalculator()

    print("Rule extraction (current version):")
    for key in FEE_TYPES:
        r = fc.get_fee_rule(key)
        rule = r.get("rule", {})
        val = (f"{rule['percent']:g}% min {rule['minimum']:g}" if "percent" in rule
               else f"{rule.get('fixed', '')}" if rule else r.get("reason", ""))
        print(f"  {key:34} {r.get('status'):13} {val}")
    print("\nCeilings:", fc.get_fee_rule("claim_property").get("ceilings", {}).get("caps"))

    def show(label, res):
        print(f"\n== {label}: status={res.get('status')} amount={res.get('amount_gel')} "
              f"law-version={res.get('version_date')}")
        for s in res.get("steps", []):
            print("   ", s)
        for n in res.get("notes", []):
            print("   note:", n[:150])
        if res.get("amount_by_party_type"):
            print("   by party:", res["amount_by_party_type"], res.get("reason"))
        if res.get("reason") and res.get("status") != "needs_input":
            print("   reason:", res["reason"])

    show("claim 10 000", fc.calculate_fee("claim_property", 10000))                 # 300
    show("claim 1 000", fc.calculate_fee("claim_property", 1000))                   # min 100
    show("claim 500 000, no party", fc.calculate_fee("claim_property", 500000))     # needs_input
    show("claim 500 000 legal", fc.calculate_fee("claim_property", 500000, "legal_entity"))
    show("appeal 50 000 individual", fc.calculate_fee("appeal_property", 50000, "individual"))
    show("cassation 1M individual", fc.calculate_fee("cassation_property", 1_000_000, "individual"))
    show("magistrate claim 4 000", fc.calculate_fee("claim_property", 4000, magistrate=True))
    show("non-property appeal", fc.calculate_fee("appeal_non_property"))
    show("squeeze-out", fc.calculate_fee("mandatory_share_sale"))
    show("settled at main hearing", fc.calculate_fee("claim_property", 20000, settlement="at_main_hearing"))
    show("admin claim 2015", fc.calculate_fee("claim_property", 20000, filing_date="2015-06-01",
                                              administrative=True))
