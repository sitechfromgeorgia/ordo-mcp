#!/usr/bin/env python3
"""
registries_api.py — Georgian public registries (anonymous endpoints only)
==========================================================================
Read-only wrappers for the public registries a lawyer checks daily:

  * CompanyRegistryAPI   — enreg.reestri.gov.ge (NAPR entrepreneurs & non-commercial
                           legal entities): company search, partner/founder search,
                           registry applications (liquidation, insolvency, share pledge...)
  * PropertyRegistryAPI  — maps.gov.ge parcel card (owners, area, extract PDFs) +
                           naprweb.reestri.gov.ge real-estate application book
  * LienRegistryAPI      — naprlmr.reestri.gov.ge: seizures, prohibitions, tax liens
  * MovablePledgeAPI     — mmr.reestri.gov.ge: pledges / leasing of movables
  * LawyerRegistryAPI    — gba.ge unified list of Bar members
  * NotaryAPI            — notary bureaus directory (api.my.gov.ge)
  * ProcurementListsAPI  — procurement.gov.ge black / white / warned supplier lists

Only endpoints that answer anonymously, without login or CAPTCHA, are used. Company
detail pages (directors, partners, extracts), the debtors registry and real-estate
application details are CAPTCHA-protected and deliberately NOT wrapped.

  * CompanyInfoAPI       — companyinfo.ge (Transparency International Georgia): company and
                           people graph — directors/partners history, sanctions, blacklists,
                           asset declarations, financial reports; person search by name
  * GeostatRegisterAPI   — br.geostat.ge statistical business register: NACE activity, size,
                           addresses, director, name/address history, filtered lists

The NAPR registries take an identification code (9-digit legal entity / 11-digit personal
number), a cadastral code or a company name. Person search by name exists only in
CompanyInfoAPI (business roles only, namesake warning). Personal numbers are returned in full
so the agent can confirm identity and cross-check other registries (liens, pledges). Results
contain personal data, so every result carries `warning` that the agent must relay.

Zero dependencies (stdlib only) — reuses _AgencyBase from agencies_api.py.

Usage:
    from registries_api import CompanyRegistryAPI, PropertyRegistryAPI, LienRegistryAPI
    CompanyRegistryAPI().search_companies(id_code="204378869")
    PropertyRegistryAPI().parcel("01.18.04.009.016")
    LienRegistryAPI().search(id_code="204378869")
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Optional

from agencies_api import _AgencyBase
from court_api import _html_to_text

# ── Warning carried by every result ─────────────────────────────

WARNING = {
    "ka": ("ყურადღება: მონაცემები აღებულია საჯარო რეესტრებიდან და შეიცავს პერსონალურ მონაცემებს. "
           "გამოიყენეთ მხოლოდ კანონიერი მიზნით („პერსონალურ მონაცემთა დაცვის შესახებ“ კანონი) — "
           "არ შეადგინოთ პირის პროფილი და არ გაავრცელოთ ზედმეტი ინფორმაცია. ეს არის საცნობარო "
           "ინფორმაცია: იურიდიული ძალა აქვს მხოლოდ ოფიციალურ ამონაწერს (napr.gov.ge / my.gov.ge); "
           "მონაცემი შეიძლება მოძველებული ან არასრული იყოს — გადაამოწმეთ ამონაწერით."),
    "en": ("WARNING: data come from public registries and include personal data. Use them only for "
           "a legitimate purpose (Law on Personal Data Protection) — do not build profiles of people "
           "or spread more than needed. This is reference information: only an official registry "
           "extract (napr.gov.ge / my.gov.ge) has legal force; data may be outdated or incomplete — "
           "verify with an extract."),
    "agent_instruction": ("Relay this warning; cite the source URL; mention only the personal data the "
                          "user's legal question needs. Search a person by name only for a concrete legal "
                          "purpose (due diligence, a dispute, enforcement, conflict check), confirm identity "
                          "by personal number before attributing anything to them (namesakes are common), "
                          "and stay within business roles — never assemble a general profile of a private "
                          "individual's life. Use the full personal number / ID code from one source to "
                          "cross-check the same person or company in the other registries."),
}


def _ident(code) -> tuple[str, str]:
    """Normalize an identification code → (digits, kind) or ("", error message)."""
    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) == 9:
        return digits, "legal_entity"
    if len(digits) == 11:
        return digits, "personal_number"
    return "", f"identification code must be 9 digits (legal entity) or 11 (personal number), got '{code}'"


def _cadastral(code) -> str:
    """'01.18.04.009.016' (dots/spaces tolerated) or '' if it does not look like one."""
    code = re.sub(r"\s", "", str(code or ""))
    return code if re.fullmatch(r"\d{2}(\.\d{2,3}){2,}(\.\d{1,3})*", code) else ""


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _unix(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _cells(row_html: str) -> list[str]:
    """<td> texts of one table row (tags stripped, whitespace collapsed)."""
    out = []
    for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S):
        out.append(re.sub(r"\s+", " ", " ".join(_html_to_text(cell).split("\n"))).strip())
    return out


def _rows(html: str) -> list[str]:
    body = html.split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    return re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)


def _total(html: str) -> Optional[int]:
    m = re.search(r"სულ(?:&nbsp;|\s)*(?:<strong>|\|)?\s*(\d+)", html)
    return int(m.group(1)) if m else None


class _RegistryBase(_AgencyBase):
    KIND = "public_registry"
    BODY = ""

    def _post_json(self, url: str, payload: dict, headers: Optional[dict] = None,
                   timeout: int = 60) -> dict:
        self._wait_rate_limit()
        hdrs = {"User-Agent": self.UA, "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json", "Accept-Language": "ka,en;q=0.9", **(headers or {})}
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=hdrs, method="POST")
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except json.JSONDecodeError:
                return {"error": "Invalid JSON from registry"}
            except Exception as e:  # noqa: BLE001 — mirror _BaseAPI behaviour
                if attempt == 0:
                    time.sleep(1)
                    continue
                return {"error": f"{type(e).__name__}: {e}"}

    def _result(self, **kw) -> dict:
        return {"registry": self.BODY, **kw, "warning": WARNING}

    @staticmethod
    def _err(msg: str) -> dict:
        return {"error": msg, "warning": WARNING}


# ══════════════════════════════════════════════════════════════
#  Entrepreneurs & non-commercial legal entities (enreg)
# ══════════════════════════════════════════════════════════════

class CompanyRegistryAPI(_RegistryBase):
    """NAPR registry of entrepreneurs and non-commercial legal entities (search pages only)."""

    BASE = "https://enreg.reestri.gov.ge/_dea/main.php"
    BODY = "მეწარმეთა და არასამეწარმეო იურიდიულ პირთა რეესტრი (NAPR)"

    APP_TYPES = {
        "new_registration": 1, "change_of_data": 2, "reorganization": 3, "deregistration": 6,
        "extract": 8, "liquidation_start": 9, "insolvency_start": 16, "insolvency_end": 17,
        "reorganization_start": 18, "reorganization_end": 19, "liquidation_end": 20,
        "forced_liquidation_start": 22, "share_pledge": 25, "share_pledge_change": 26,
        "share_pledge_end": 27, "dissolution": 40, "redomiciliation_start": 42,
    }

    def __init__(self, rate_limit: float = 0.3):
        super().__init__(rate_limit)
        self._session = False

    def _search(self, method: str, form: dict, page: int) -> str:
        if not self._session:
            self._fetch(self.BASE + "?m=new_index")
            self._session = True
        html = self._fetch(self.BASE, {"c": "search", "m": method, **form})
        if page > 1 and not html.startswith("ERROR"):
            html = self._fetch(self.BASE, {"c": "search", "m": method, "p": page})
        return html

    def search_companies(self, id_code: str = "", name: str = "", page: int = 1) -> dict:
        """
        Find legal entities / individual entrepreneurs by identification code or name.

        Returns {"total", "page", "items": [{id_code, personal_number, name, legal_form,
                 status, registry_id}], "source_url", "warning"}.
        Directors, partners and extracts are behind a CAPTCHA — order an extract instead.
        """
        code = ""
        if id_code:
            code, kind = _ident(id_code)
            if not code:
                return self._err(kind)
        if not code and not (name or "").strip():
            return self._err("give id_code or name")
        html = self._search("find_legal_persons", {"s_legal_person_idnumber": code,
                                                   "s_legal_person_name": (name or "").strip()}, page)
        if html.startswith("ERROR"):
            return self._err(html)
        items = []
        for row in _rows(html):
            c = _cells(row)
            rid = re.search(r"show_legal_person\((\d+)\)", row)
            if len(c) >= 6:
                items.append({"id_code": c[1], "personal_number": c[2], "name": c[3],
                              "legal_form": c[4], "status": c[5],
                              "registry_id": int(rid.group(1)) if rid else None})
        return self._result(total=_total(html) or len(items), page=page, items=items,
                            source_url="https://enreg.reestri.gov.ge/_dea/main.php?m=new_index",
                            note="directors, partners and the extract are CAPTCHA-protected on the "
                                 "site — get them from an official extract (my.gov.ge / napr.gov.ge)")

    def find_participations(self, org_name: str = "", id_code: str = "", page: int = 1) -> dict:
        """
        Entities in which an organization (by name) or a person/entity (by identification
        code) is registered as founder or partner. Person names are not accepted.

        Returns {"total", "items": [{entity_id_code, entity_name, entity_kind, participant,
                 role, registry_id}], ...}
        """
        code = ""
        if id_code:
            code, kind = _ident(id_code)
            if not code:
                return self._err(kind)
        if not code and not (org_name or "").strip():
            return self._err("give org_name or id_code")
        html = self._search("find_participations2", {"s_participants_orgname": (org_name or "").strip(),
                                                     "s_participants_idnumber": code}, page)
        if html.startswith("ERROR"):
            return self._err(html)
        items = []
        for row in _rows(html):
            c = _cells(row)
            rid = re.search(r"show_legal_person\((\d+)\)", row)
            kind = re.search(r'title="([^"]+)"', row)
            if len(c) >= 6:
                items.append({"entity_id_code": c[1] or c[2], "entity_name": c[3],
                              "entity_kind": kind.group(1) if kind else "", "participant": c[4],
                              "role": c[5], "registry_id": int(rid.group(1)) if rid else None})
        return self._result(total=_total(html) or len(items), page=page, items=items,
                            source_url="https://enreg.reestri.gov.ge/_dea/main.php?m=new_index")

    def search_applications(self, date_from: str = "", date_to: str = "", app_type: str = "",
                            reg_number: str = "", page: int = 1) -> dict:
        """
        Registry applications filed in a period (liquidation, insolvency, reorganization,
        share pledge...) or one application by registration number (e.g. "B26439530").

        app_type: key of APP_TYPES (e.g. "insolvency_start", "liquidation_start") or ""
        Dates: YYYY-MM-DD; default = last 7 days.
        """
        if app_type and app_type not in self.APP_TYPES:
            return self._err(f"unknown app_type '{app_type}'; use one of {list(self.APP_TYPES)}")
        d_to = _parse_date(date_to) or date.today()
        d_from = _parse_date(date_from) or (d_to - timedelta(days=7))
        if (date_from and not _parse_date(date_from)) or (date_to and not _parse_date(date_to)):
            return self._err("dates must be YYYY-MM-DD or DD/MM/YYYY")
        form = {"s_app_regnumber": (reg_number or "").strip(), "s_app_app_id": "",
                "s_app_from_date": "" if reg_number else d_from.strftime("%d.%m.%Y"),
                "s_app_to_date": "" if reg_number else d_to.strftime("%d.%m.%Y"),
                "s_app_tr_type": str(self.APP_TYPES.get(app_type, 0)), "s_app_status": "0"}
        html = self._search("search_app", form, page)
        if html.startswith("ERROR"):
            return self._err(html)
        items = []
        for row in _rows(html):
            if "show_app(" not in row:
                continue
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(cells) < 4:
                continue
            head = [l for l in _html_to_text(cells[1]).split("\n") if l.strip()]
            svc = [l for l in _html_to_text(cells[2]).split("\n") if l.strip()]
            people = [l.strip() for l in _html_to_text(cells[3]).split("\n") if l.strip()]
            app_id = re.search(r"show_app\((\d+)\)", row)
            items.append({"reg_number": head[0] if head else "",
                          "registered": head[1] if len(head) > 1 else "",
                          "app_id": int(app_id.group(1)) if app_id else None,
                          "service": svc[0] if svc else "", "status": svc[1] if len(svc) > 1 else "",
                          "parties": people})
        return self._result(total=_total(html) or len(items), page=page,
                            period=None if reg_number else [d_from.isoformat(), d_to.isoformat()],
                            app_type=app_type or "all", items=items,
                            source_url="https://www.my.gov.ge/ka-ge/services/6/service/179",
                            note="the subject company is shown only on the CAPTCHA-protected detail "
                                 "page; match by registration number with the company's extract")


# ══════════════════════════════════════════════════════════════
#  Real estate (maps.gov.ge parcel card + naprweb application book)
# ══════════════════════════════════════════════════════════════

class PropertyRegistryAPI(_RegistryBase):
    """NAPR real-estate registry: public parcel card and application book."""

    MAPS = "https://maps.gov.ge"
    NAPRWEB = "https://naprweb.reestri.gov.ge/api/search"
    BODY = "უძრავი ქონების საჯარო რეესტრი (NAPR)"
    _LABELS = {"საკადასტრო კოდი": "cadastral_code", "ფართობი": "area", "ნაკვეთის ტიპი": "parcel_type",
               "მისამართი": "address", "საკუთრების ტიპი": "ownership_type"}

    def parcel(self, cadastral_code: str) -> dict:
        """
        Public parcel card: area, type, address, ownership type, registered owners and the
        list of registry documents (extracts, cadastral plans) with direct PDF links —
        read the extract PDFs with pdf_text.extract_pdf_text.
        """
        code = _cadastral(cadastral_code)
        if not code:
            return self._err(f"not a cadastral code: '{cadastral_code}' (e.g. 01.18.04.009.016)")
        raw = self._fetch(f"{self.MAPS}/map/portal/search", {"keyword": code, "keyword_description": ""})
        try:
            hits = json.loads(raw).get("result") or []
        except (json.JSONDecodeError, AttributeError):
            return self._err(raw[:200] if raw.startswith("ERROR") else "maps.gov.ge search failed")
        hit = next((h for h in hits if h.get("name") == code), None)
        if not hit:
            return self._result(found=False, cadastral_code=code,
                                candidates=[{"cadastral_code": h.get("name"), "address": h.get("descript")}
                                            for h in hits[:10]])
        link = (hit.get("details") or {}).get("info_link") or ""
        html = self._fetch(self.MAPS + link + "&lang=ka")
        if html.startswith("ERROR"):
            return self._err(html)
        out = {"found": True, "cadastral_code": code, "address": hit.get("descript", "")}
        for label, key in self._LABELS.items():
            m = re.search(r">\s*" + re.escape(label) + r"\s*</div>\s*<div[^>]*>(.*?)</div>", html, re.S)
            if m:
                out[key] = re.sub(r"\s+", " ", " ".join(_html_to_text(m.group(1)).split("\n"))).strip()
        owners = re.search(r"MI\.OWNERS.*?-->(.*?)<!--end of MI\.OWNERS", html, re.S | re.I)
        out["owners"] = ([re.sub(r"\s+", " ", _html_to_text(p)).strip()
                          for p in re.findall(r"<p[^>]*>(.*?)</p>", owners.group(1), re.S)] if owners else [])
        docs, seen = [], set()
        block = re.search(r"MI\.OUT_DOC_LIST.*?-->(.*?)<!--end of MI\.OUT_DOC_LIST", html, re.S | re.I)
        for section in re.split(r'<span class="flex-1 line-clamp-1">', block.group(1) if block else "")[1:]:
            title = re.sub(r"\s*\(\d+\)\s*$", "", _html_to_text(section.split("</span>", 1)[0])
                           .replace("\n", " ").strip())
            for url, inner in re.findall(r'<a href="(https://bs\.napr\.gov\.ge/GetBlob[^"]+)"[^>]*>(.*?)</a>',
                                         section, re.S):
                if url in seen:
                    continue
                seen.add(url)
                parts = [p for p in _html_to_text(inner).split("\n") if p.strip()]
                docs.append({"type": title, "reg_number": parts[0].strip() if parts else "",
                             "date": parts[1].strip() if len(parts) > 1 else "", "pdf_url": url})
        out["documents"] = docs
        geo = re.search(r"google\.com/maps/place/([\d.]+),([\d.]+)", html)
        if geo:
            out["coordinates"] = [float(geo.group(1)), float(geo.group(2))]
        return self._result(**out, source_url=f"{self.MAPS}/map/portal?cadcode={code}",
                            note="the newest 'ამონაწერი' PDF is the latest registry extract; owners shown "
                                 "here are the current registered owners")

    def applications(self, cadastral_code: str = "", reg_number: str = "", id_code: str = "",
                     date_from: str = "", date_to: str = "", page: int = 1) -> dict:
        """
        Real-estate registry application book: transactions filed for a parcel (sale, mortgage,
        lease, seizure...), by cadastral code, application number or applicant ID code.
        An ID-code search needs a period (default: last 90 days) — the service times out otherwise.
        """
        cad = _cadastral(cadastral_code) if cadastral_code else ""
        if cadastral_code and not cad:
            return self._err(f"not a cadastral code: '{cadastral_code}'")
        code = ""
        if id_code:
            code, kind = _ident(id_code)
            if not code:
                return self._err(kind)
        if not (cad or reg_number or code):
            return self._err("give cadastral_code, reg_number or id_code")
        d_from, d_to = _parse_date(date_from), _parse_date(date_to)
        if code and not (d_from or d_to):
            d_to = date.today()
            d_from = d_to - timedelta(days=90)
        payload = {"page": int(page), "search": "", "regno": (reg_number or "").strip(),
                   "datefrom": d_from.strftime("%d-%m-%Y") if d_from else None,
                   "dateto": (d_to or date.today()).strftime("%d-%m-%Y") if d_from else None,
                   "person": code, "address": "", "cadcode": cad}
        res = self._post_json(self.NAPRWEB, payload, timeout=90)
        if "error" in res:
            return self._err(res["error"])
        items = [{"reg_number": a.get("regNumber"), "app_id": a.get("appID"),
                  "transaction": a.get("webTransact"), "status": a.get("status"),
                  "address": a.get("address"), "registered": _unix(a.get("appRegDate")),
                  "last_action": _unix(a.get("lastActDate")), "applicants": a.get("applicants") or []}
                 for a in res.get("applist") or []]
        return self._result(total=res.get("total"), page=res.get("page"), last_page=res.get("lastpage"),
                            period=[d_from.isoformat(), (d_to or date.today()).isoformat()] if d_from else None,
                            items=items, source_url="https://www.my.gov.ge/ka-ge/services/5/service/176")


# ══════════════════════════════════════════════════════════════
#  Seizures / prohibitions / tax liens (naprlmr)
# ══════════════════════════════════════════════════════════════

class LienRegistryAPI(_RegistryBase):
    """NAPR registry of seizures (ყადაღა), prohibitions (აკრძალვა) and tax liens."""

    BASE = "https://naprlmr.reestri.gov.ge/api"
    BODY = "ყადაღის/აკრძალვის და საგადასახადო გირავნობის რეესტრი (NAPR)"

    def search(self, id_code: str = "", cadastral_code: str = "", page: int = 1) -> dict:
        """
        Liens on a person/entity (by identification code) or on a parcel (by cadastral code).

        Returns {"total", "last_page", "items": [{kind, object_type, object, object_id, note,
                 active, holder, basis, annulment, reg_number, registered, status, app_id}]}
        """
        code = cad = ""
        if id_code:
            code, kind = _ident(id_code)
            if not code:
                return self._err(kind)
        if cadastral_code:
            cad = _cadastral(cadastral_code)
            if not cad:
                return self._err(f"not a cadastral code: '{cadastral_code}'")
        if not (code or cad):
            return self._err("give id_code or cadastral_code")
        res = self._post_json(f"{self.BASE}/searchlien",
                              {"page": int(page), "flType": "0", "flName": "", "flIdnumber": code,
                               "flObjectType": "3" if cad else "0", "flObject": cad})
        if "error" in res:
            return self._err(res["error"])
        items = [{"kind": l.get("LIEN_OBJ_TYPE_NAME"), "object_type": l.get("LIEN_TYPE_NAME"),
                  "object": l.get("OBJ_DESC"), "object_id": l.get("OBJ_ID"),
                  "note": (l.get("OBJ_NOTE") or "").strip(), "active": l.get("ISACTIVE") == "1",
                  "holder": (l.get("PERSON_NAME") or "").strip(),
                  "basis": (l.get("LIEN_DOC") or "").strip(),
                  "annulment": (l.get("ANNUL_DOC") or "").strip(),
                  "reg_number": l.get("MAIN_REGNUMBER"), "registered": _unix(l.get("CREATE_DATE")),
                  "last_action": _unix(l.get("MAIN_CREATE_DATE")), "transaction": l.get("TR_NAME1"),
                  "status": l.get("STATUS_NAME1"), "app_id": l.get("APP_ID")}
                 for l in res.get("lienlist") or []]
        return self._result(total=res.get("total"), page=res.get("page"), last_page=res.get("lastpage"),
                            items=items, source_url="https://naprlmr.reestri.gov.ge/",
                            note="'basis' names the act (court ruling, enforcement or tax order) — "
                                 "check whether it is still active ('active') before advising")


# ══════════════════════════════════════════════════════════════
#  Pledge / leasing of movables (mmr)
# ══════════════════════════════════════════════════════════════

class MovablePledgeAPI(_RegistryBase):
    """NAPR registry of pledges and leasing of movable property and intangible assets."""

    BASE = "https://mmr.reestri.gov.ge/_dea/main.php"
    BODY = "მოძრავ ნივთებსა და არამატერიალურ ქონებრივ სიკეთეზე გირავნობის/ლიზინგის რეესტრი (NAPR)"

    def search(self, id_code: str, page: int = 1) -> dict:
        """Pledges/leasing where the identification code is pledgor/owner or creditor."""
        code, kind = _ident(id_code)
        if not code:
            return self._err(kind)
        html = self._fetch(self.BASE, {"c": "search", "m": "search_lien_by_person_and_object",
                                       "s_person_idnumber_both": code, "s_person_serial_both": "",
                                       "s_person_country_id_both": "995"})
        if page > 1 and not html.startswith("ERROR"):
            html = self._fetch(self.BASE, {"c": "search", "m": "search_lien_by_person_and_object",
                                           "p": page})
        if html.startswith("ERROR"):
            return self._err(html)
        items = []
        chunks = re.split(r"show_lien_details\(\s*", html)
        for chunk in chunks[1:]:
            app_id = re.match(r"(\d+)", chunk)
            lines = [l.strip() for l in _html_to_text(chunk.split(")", 1)[-1].lstrip('" >')).split("\n")
                     if l.strip() and l.strip() not in (":", "&nbsp;")]
            lines = lines[:lines.index("სულ")] if "სულ" in lines else lines
            text = " ".join(lines)
            item = {"app_id": int(app_id.group(1)) if app_id else None, "summary": text[:400]}
            for label, key in (("კრედიტორი", "creditor"), ("მესაკუთრე", "owner")):
                m = re.search(label + r"\s*:?\s*(.+?)(?=\s+(?:კრედიტორი|მესაკუთრე)\s*:|$)", text)
                if m:
                    item[key] = m.group(1).strip()
            m = re.search(r"^(.+?\(\s*[A-Z]?\d+\s*\))", text)
            if m:
                item["subject"] = m.group(1)
            items.append(item)
        return self._result(total=_total(html) or len(items), page=page, items=items,
                            source_url="https://mmr.reestri.gov.ge/")

    def get_pledge(self, app_id: int, max_chars: int = 6000) -> dict:
        """Pledge/leasing record: dates, term, creditor, owner, pledged items, documents, scans."""
        html = self._fetch(f"{self.BASE}?c=mortgage&m=show_lien_details&app_id={int(app_id)}")
        if html.startswith("ERROR"):
            return self._err(html)
        body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
        text = "\n".join(l.strip() for l in _html_to_text(body).split("\n")
                         if l.strip() and l.strip() != "&nbsp;")
        text = re.sub(r"^.*?გვერდის განახლება\n", "", text, flags=re.S)
        scans = sorted(set(re.findall(r'(https://mmr\.reestri\.gov\.ge/_dea/main\.php\?c=mortgage&m='
                                      r'get_lien_scandoc_by_id[^"\']+)', html)))
        return self._result(app_id=int(app_id), text=text[:max_chars], truncated=len(text) > max_chars,
                            scans=scans[:20], source_url=f"{self.BASE}?c=mortgage&m=show_lien_details"
                                                         f"&app_id={int(app_id)}")


# ══════════════════════════════════════════════════════════════
#  Lawyers (Georgian Bar Association) and notaries
# ══════════════════════════════════════════════════════════════

class LawyerRegistryAPI(_RegistryBase):
    """Georgian Bar Association — unified list of members (is a lawyer registered?)."""

    PAGE = "https://gba.ge/ka/ადვოკატები-რეესტრი/წევრთა-ერთიანი-სია"
    KNOWN_JSON = "https://gba.ge/ajax/employers/ka-6ae34e8e5107a.json"
    BODY = "საქართველოს ადვოკატთა ასოციაცია — წევრთა ერთიანი სია"

    def __init__(self, rate_limit: float = 0.3):
        super().__init__(rate_limit)
        self._rows = None

    def _load(self) -> list:
        if self._rows is not None:
            return self._rows
        urls = [self.KNOWN_JSON]
        page = self._fetch(urllib.parse.quote(self.PAGE, safe=":/"))
        for u in re.findall(r"""['"]([^'"]*ajax/employers/[^'"]+\.json)['"]""", page or ""):
            u = urllib.parse.urljoin("https://gba.ge/", u)
            if u not in urls:
                urls.insert(0, u)
        for u in urls:
            raw = self._fetch(u)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                data = data.get("data") or []
            if isinstance(data, list) and data:
                self._rows = data
                return data
        self._rows = []
        return []

    def search(self, query: str, limit: int = 20) -> dict:
        """
        Find a lawyer by name (any word order, partial) or by list number (e.g. "0001").
        Returns name, list number and specializations. Suspension/disbarment is NOT shown —
        check bar_ethics_search for disciplinary decisions.
        """
        q = (query or "").strip()
        if not q:
            return self._err("give a name or a list number")
        rows = self._load()
        if not rows:
            return self._err("gba.ge members list unavailable")
        words = [w.lower() for w in q.split()]
        hits = []
        for r in rows:
            cells = [_html_to_text(str(c)).replace("\n", " ").strip() for c in r]
            name = cells[0] if cells else ""
            number = cells[1] if len(cells) > 1 else ""
            if (q.isdigit() and number.lstrip("0") == q.lstrip("0")) or \
                    all(w in name.lower() for w in words):
                hits.append({"name": name, "list_number": number,
                             "specialization": cells[2] if len(cells) > 2 else "",
                             "additional_specialization": cells[3] if len(cells) > 3 else ""})
        return self._result(total_members=len(rows), total=len(hits), items=hits[:max(1, limit)],
                            source_url=self.PAGE,
                            note="the list does not show suspension or disbarment — see bar_ethics_search")


class NotaryAPI(_RegistryBase):
    """Notary bureaus directory (my.gov.ge service 205, data of the Notary Chamber)."""

    BASE = "https://api.my.gov.ge/api/NotaryBureaus"
    BODY = "სანოტარო ბიუროები (my.gov.ge / საქართველოს ნოტარიუსთა პალატა)"
    HEADERS = {"Content-Language": "ka-ge", "DeviceTypeId": "1"}

    def _get_json(self, path: str):
        raw = self._fetch(f"{self.BASE}/{path}", headers={**self.HEADERS, "Accept": "application/json"})
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": raw[:200]}

    def regions(self) -> list:
        data = self._get_json("GetRegions")
        return data if isinstance(data, list) else (data.get("data") or [] if isinstance(data, dict) else [])

    def search(self, region: str = "თბილისი", district: str = "", name: str = "", page: int = 1) -> dict:
        """Notary bureaus in a region (and district); optional filter by notary name."""
        regs = self.regions()
        reg = next((r for r in regs if region and region.strip() in str(r.get("name", ""))), None)
        if not reg:
            return self._err(f"region '{region}' not found; available: "
                             f"{[r.get('name') for r in regs]}")
        district_id = None
        if district:
            ds = self._get_json(f"GetCityAndDistricts?regionId={reg['id']}")
            ds = ds if isinstance(ds, list) else (ds.get("data") or [] if isinstance(ds, dict) else [])
            d = next((x for x in ds if district.strip() in str(x.get("name", ""))), None)
            if not d:
                return self._err(f"district '{district}' not found; available: {[x.get('name') for x in ds]}")
            district_id = d.get("id")
        res = self._post_json(f"{self.BASE}/GetNotaryBureaus",
                              {"regionId": reg["id"], "cityDistrictId": district_id,
                               "page": int(page), "offset": 50 if name else 10}, headers=self.HEADERS)
        if "error" in res:
            return self._err(res["error"])
        items = [{k: n.get(k) for k in ("fullName", "regionName", "cityDistrictName", "address",
                                        "mobileNumber", "phoneNumber", "email") if n.get(k)}
                 for n in res.get("data") or []]
        if name:
            items = [i for i in items if all(w in i.get("fullName", "") for w in name.split())]
        meta = {k: v for k, v in res.items() if k != "data"}
        return self._result(region=reg.get("name"), page=page, items=items, paging=meta,
                            source_url="https://www.my.gov.ge/ka-ge/services/6/service/205")


# ══════════════════════════════════════════════════════════════
#  Public procurement supplier lists
# ══════════════════════════════════════════════════════════════

class ProcurementListsAPI(_RegistryBase):
    """procurement.gov.ge black list (banned), white list (reliable) and warned suppliers."""

    BASE = "https://procurement.gov.ge"
    BODY = "სახელმწიფო შესყიდვების სააგენტო — მიმწოდებელთა სიები"
    LISTS = {"black": 4, "white": 3, "warned": 5}

    def list_entries(self, list_type: str = "black", page: int = 1) -> dict:
        """One page of a supplier list, newest first: [{title, id_code, date, url}]."""
        cat = self.LISTS.get(list_type)
        if not cat:
            return self._err(f"list_type must be one of {list(self.LISTS)}")
        html = self._fetch(f"{self.BASE}/ka/News/List?page={int(page)}&category={cat}")
        if html.startswith("ERROR"):
            return self._err(html)
        items = []
        for href, title, dt in re.findall(r'<a href="(/ka/n/[^"]+)">(.*?)</a>\s*<span class="DateBox">\s*'
                                          r'([^<]*?)\s*</span>', html, re.S):
            title = _html_to_text(title).replace("\n", " ").strip()
            code = re.search(r"(?<!\d)(\d{11}|\d{9})(?!\d)", title)
            items.append({"title": title, "id_code": code.group(1) if code else "",
                          "date": dt.strip(), "url": self.BASE + href})
        return self._result(list_type=list_type, page=page, items=items,
                            source_url=f"{self.BASE}/ka/News/List?category={cat}")

    def search(self, query: str, list_type: str = "black", max_pages: int = 15) -> dict:
        """Scan up to max_pages of a list for an identification code or a company name."""
        q = (query or "").strip()
        if not q:
            return self._err("give an identification code or a company name")
        hits, scanned = [], 0
        for page in range(1, max(1, min(int(max_pages), 60)) + 1):
            res = self.list_entries(list_type, page)
            if "error" in res:
                break
            scanned = page
            if not res["items"]:
                break
            hits += [i for i in res["items"] if q in i["id_code"] or q.lower() in i["title"].lower()]
        return self._result(list_type=list_type, query=q, pages_scanned=scanned, items=hits,
                            note="not found ≠ not listed if the scan stopped at max_pages"
                            if not hits else "open 'url' for the procuring body, tender and legal basis")

    def get_entry(self, url: str) -> dict:
        """Text of one list entry (procuring entity, tender number, legal basis, dates)."""
        if not url.startswith(self.BASE + "/ka/n/"):
            return self._err("url must be a procurement.gov.ge list entry (/ka/n/...)")
        html = self._fetch(url)
        if html.startswith("ERROR"):
            return self._err(html)
        title = re.search(r'CMSBreadCrumbsCurrentItem">(.*?)</span>', html, re.S)
        dt = re.search(r'<div class="Date">([^<]*)</div>', html)
        body = re.search(r'<div class="NewsBody">(.*?)<div class="(?:Clearer|Footer|LeftSide)', html, re.S)
        if not body:
            return self._err("entry text not found — open the url")
        text = _html_to_text(body.group(1))
        return self._result(url=url, title=_html_to_text(title.group(1)).strip() if title else "",
                            date=dt.group(1).strip() if dt else "",
                            text="\n".join(l.strip() for l in text.split("\n") if l.strip())[:6000])


# ══════════════════════════════════════════════════════════════
#  companyinfo.ge (Transparency International Georgia) — company & people graph
# ══════════════════════════════════════════════════════════════

def _pn(value) -> str:
    """Personal number as published (full, so it can be cross-checked in other registries)."""
    return str(value or "").strip()


def _ci_date(value) -> str:
    if isinstance(value, dict):
        value = value.get("date", "")
    return str(value or "")[:10]


class CompanyInfoAPI(_RegistryBase):
    """
    companyinfo.ge — Transparency International Georgia's monthly mirror of the NAPR business
    registry, joined with sanctions, procurement black/warning lists, asset declarations,
    financial reports (reportal), mining licences and construction permits.

    Its robots.txt disallows individual-entrepreneur pages and /uploads/ (registry PDFs), so
    individual entrepreneurs are excluded and document links are not returned.
    """

    BASE = "https://api.companyinfo.ge/api"
    BODY = "companyinfo.ge (საერთაშორისო გამჭვირვალობა — საქართველო; NAPR-ის ყოველთვიური ასლი)"
    _EXCLUDED_IDS = {904548, 944894, 259504}  # listed in companyinfo.ge robots.txt
    _FLAGS = ("sanctionPersons", "indirectSanctionPersons", "procurementBlacklist", "procurementWarningList",
              "dataDeclarations", "officialAffiliationPersons", "privatization", "miningLicenses",
              "constructionPermits", "financialReports", "beneficiaries")

    def __init__(self, rate_limit: float = 1.0):
        super().__init__(rate_limit)

    def _api(self, path: str, params: Optional[dict] = None):
        url = f"{self.BASE}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        raw = self._fetch(url, headers={"Accept": "application/json"})
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from companyinfo.ge"}

    def _flags(self, row: dict) -> list:
        return [f for f in self._FLAGS if row.get(f) is True or (isinstance(row.get(f), list) and row[f])]

    def search_companies(self, name: str = "", id_code: str = "", status: str = "", page: int = 1) -> dict:
        """Companies by name (substring) or identification code → id, name, address, form, flags."""
        params = {"page": int(page)}
        if id_code:
            code, kind = _ident(id_code)
            if not code:
                return self._err(kind)
            params["idCode"] = code
        elif (name or "").strip():
            params["name"] = name.strip()
        else:
            return self._err("give name or id_code")
        if status:
            params["status"] = status
        res = self._api("corporations/search", params)
        if "error" in res:
            return self._err(res["error"])
        items, seen, skipped = [], set(), 0
        for r in res.get("items") or []:
            code = (r.get("idCode") or "").strip()
            if r.get("legalForm") == "1" or r.get("id") in self._EXCLUDED_IDS:
                skipped += 1
                continue
            if not r.get("registrationDate") and code in seen:
                continue  # junk duplicate rows (e.g. " 204378869")
            seen.add(code)
            items.append({"company_id": r.get("id"), "id_code": code, "name": r.get("name"),
                          "address": r.get("address"), "legal_form": r.get("legalForm"),
                          "registered": _ci_date(r.get("registrationDate")), "flags": self._flags(r)})
        return self._result(total=res.get("totalItems"), page=res.get("currentPage"), per_page=res.get("itemPerPage"),
                            items=items, excluded_individual_entrepreneurs=skipped,
                            source_url="https://www.companyinfo.ge/ka",
                            note="flags show linked datasets — open with companyinfo_company")

    def company(self, id_code: str = "", company_id: int = 0, history_limit: int = 40) -> dict:
        """
        Full company card: status, legal form, address, email, current directors/partners with roles
        and shares, role history (newest first, up to history_limit), owned companies, business
        registry data (size, NACE activity), sanctions, procurement black/warning list, asset
        declarations of linked officials, mining licences, construction permits, financial reports.
        """
        cid = int(company_id or 0)
        if not cid:
            code, kind = _ident(id_code)
            if not code:
                return self._err(kind)
            got = self._api(f"getcorporation/{code}")
            if isinstance(got, dict) and "error" in got:
                return self._err(got["error"])
            try:
                cid = int(got)
            except (TypeError, ValueError):
                return self._result(found=False, id_code=code)
        if cid in self._EXCLUDED_IDS:
            return self._err("this record is excluded by companyinfo.ge robots.txt")
        c = self._api(f"corporations/{cid}")
        if "error" in c:
            return self._err(c["error"])
        if c.get("legalForm") == "1":
            return self._err("individual entrepreneurs are excluded (companyinfo.ge robots.txt) — "
                             "use geostat_company or registry_company_search")
        corp = c.get("corporation") or {}
        info = self._api(f"company-info/{cid}")
        current = [{"person_id": p.get("personId"), "name": (p.get("personName") or "").lstrip(": ").strip(),
                    "id_number": _pn(p.get("personIdNumber")), "nationality": p.get("personNationality"),
                    "role": p.get("personRole"), "share": p.get("personShare")}
                   for p in (info.get("persons") or [] if isinstance(info, dict) else [])]
        hist = sorted(c.get("corporationAffiliations") or [], key=lambda a: (a.get("exPerson"), 0))
        history = [{"person_id": a.get("personId"), "name": (a.get("personName") or "").lstrip(": ").strip(),
                    "id_number": _pn(a.get("personIdNumber")), "role": a.get("role"),
                    "share": a.get("share"), "date": a.get("date"), "former": bool(a.get("exPerson")),
                    "registry_application": a.get("registration_number")}
                   for a in hist[:max(0, int(history_limit))]]
        owned = self._api(f"corporation-person-info/{cid}")
        reports = [{"year": r.get("year"), "categories": r.get("categories"),
                    "files": [{k: f.get(k) for k in ("period", "kind", "language", "submittedAt")}
                              for f in r.get("files") or []]} for r in c.get("financialReports") or []]
        return self._result(
            found=True, company_id=cid, id_code=corp.get("idCode"), name=corp.get("name"),
            legal_form=c.get("legalFormKa"), status=c.get("status"),
            registered=_ci_date(corp.get("registrationDate")), address=corp.get("address"),
            email=corp.get("email"), data_as_of=_ci_date(corp.get("updateDate")),
            current_persons=current, role_history=history,
            role_history_total=len(c.get("corporationAffiliations") or []),
            shareholders=c.get("shareHolders"),
            owns=(owned.get("ownership") if isinstance(owned, dict) else None),
            reorganisations=(info.get("reorganisations") if isinstance(info, dict) else None),
            business_registry=c.get("businessRegistry"),
            sanctions=c.get("sanctionPersons") or [], indirect_sanctions=c.get("indirectSanctionPersons") or [],
            procurement_blacklist=c.get("procurementBlacklist") or [],
            procurement_warning_list=c.get("procurementWarningList") or [],
            linked_officials=c.get("officialAffiliationPersons") or [],
            asset_declarations=c.get("dataDeclarations") or [],
            mining_licenses=c.get("miningLicenses") or [], construction_permits=c.get("constructionPermits") or [],
            financial_reports=reports,
            source_url=f"https://www.companyinfo.ge/ka/corporations/{cid}",
            note="monthly mirror of the NAPR registry (see data_as_of) — confirm current directors/partners "
                 "with an official extract; financial report PDFs are on reportal.ge")

    def search_people(self, name: str = "", id_number: str = "", page: int = 1) -> dict:
        """
        People registered as directors / partners / founders, by name (substring) or by personal
        number. Results can include namesakes: confirm identity (personal number, roles) before
        attributing anything to a person.
        """
        params = {"page": int(page)}
        if id_number:
            code, kind = _ident(id_number)
            if not code:
                return self._err(kind)
            params["idNumber"] = code
        elif len((name or "").split()) >= 2:
            params["name"] = " ".join(name.split())
        else:
            return self._err("give a full name (first + last) or a personal number")
        res = self._api("people/search", params)
        if "error" in res:
            return self._err(res["error"])
        items = [{"person_id": r.get("id"), "name": r.get("name"),
                  "id_number": _pn(r.get("idNumber") or r.get("personalIdNumber")),
                  "nationality": r.get("nationality"), "flags": self._flags(r)}
                 for r in res.get("items") or []]
        names = [i["name"] for i in items]
        namesakes = sorted({n for n in names if names.count(n) > 1})
        return self._result(total=res.get("totalItems"), page=res.get("currentPage"), items=items,
                            namesakes=namesakes,
                            identity_warning=("Several different people share a name here — identify the "
                                              "right one by personal number and roles before attributing "
                                              "any company, debt or fact to them.") if namesakes or len(items) > 1
                            else "", source_url="https://www.companyinfo.ge/ka")

    def person(self, person_id: int) -> dict:
        """A person's business roles: companies (current and former), role, date, ownership, sanctions."""
        p = self._api(f"people/{int(person_id)}")
        if "error" in p:
            return self._err(p["error"])
        info = p.get("person") or {}
        roles = [{"company_id": a.get("companyId"), "company": a.get("companyName"), "id_code": a.get("idCode"),
                  "role": a.get("role"), "date": a.get("date"), "former": bool(a.get("ex")),
                  "company_flags": self._flags(a)}
                 for a in p.get("affiliations") or []]
        roles.sort(key=lambda r: r["former"])
        return self._result(person_id=int(person_id), name=info.get("name"),
                            id_number=_pn(info.get("idNumber")), nationality=info.get("nationality"),
                            roles=roles, ownership=p.get("ownership") or [],
                            sanctions=p.get("sanctionPersons") or [],
                            indirect_sanctions=p.get("indirectSanctionPersons") or [],
                            asset_declarations=p.get("dataDeclarations") or [],
                            source_url=f"https://www.companyinfo.ge/ka/people/{int(person_id)}",
                            note="business roles only (home address withheld); monthly NAPR mirror — "
                                 "confirm with an official extract")


# ══════════════════════════════════════════════════════════════
#  Geostat statistical business register (br.geostat.ge)
# ══════════════════════════════════════════════════════════════

class GeostatRegisterAPI(_RegistryBase):
    """
    Geostat statistical business register: ~1.15M entities with NACE activity, size, legal and
    actual address, director, registration dates and name/address history. Open API; license
    requires citing Geostat (საქსტატი). Statistical data — NAPR remains the legal register.
    """

    BASE = "https://br-api.geostat.ge/api"
    BODY = "საქსტატი — ბიზნეს რეგისტრი (br.geostat.ge)"
    SIZES = {"small": 1, "medium": 2, "large": 3, "unknown": 4}

    def __init__(self, rate_limit: float = 1.2):  # ~50 requests/minute window
        super().__init__(rate_limit)
        self._activities = None

    def _api(self, path: str, params: Optional[dict] = None):
        url = f"{self.BASE}/{path}" + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        raw = self._fetch(url, headers={"Accept": "application/json"})
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from br-api.geostat.ge"}

    @staticmethod
    def _entity(d: dict) -> dict:
        return {
            "stat_id": d.get("Stat_ID"), "id_code": d.get("Legal_Code"),
            "name": d.get("Full_Name"), "legal_form": d.get("Legal_Form"),
            "active": d.get("ISActive") == 1, "size": d.get("Zoma"),
            "activity": f"{d.get('Activity_2_Code') or ''} {d.get('Activity_2_Name') or ''}".strip(),
            "ownership": (d.get("Ownership_Type") or "").strip(),
            "legal_address": d.get("Address"), "legal_region": d.get("Region_name"),
            "actual_address": d.get("Address2"), "head": d.get("Head"),
            "head_id_number": _pn(d.get("Head_PN")), "partner": d.get("Partner"),
            "partner_id_number": _pn(d.get("Partner_PN")),
            "email": d.get("Email"), "phone": d.get("mob"), "web": d.get("web"),
            "registered": str(d.get("Init_Reg_date") or "")[:10],
            "last_change": str(d.get("Reg_Date") or "")[:10],
        }

    def search(self, name: str = "", id_code: str = "", activity_code: str = "", region_code: str = "",
               legal_form_id: int = 0, size: str = "", active: Optional[bool] = None,
               page: int = 1, limit: int = 20) -> dict:
        """
        Search/filter the register: name (substring), ID code, NACE rev.2 activity code without the
        section letter ("64.19", "64.19.1"), region code ("11" = Tbilisi; see geostat_regions),
        legal_form_id (see /legal-forms), size (small/medium/large/unknown), active.
        """
        params = {"lang": "ge", "page": int(page), "limit": max(1, min(int(limit), 100))}
        if id_code:
            code, kind = _ident(id_code)
            if not code:
                return self._err(kind)
            params["identificationNumber"] = code
        if (name or "").strip():
            params["organizationName"] = name.strip()
        if activity_code:
            params["activityCode"] = re.sub(r"^[A-Za-z]\s*", "", activity_code.strip())
        if region_code:
            params["legalAddressRegion"] = str(region_code)
        if legal_form_id:
            params["legalForm"] = int(legal_form_id)
        if size:
            if size not in self.SIZES:
                return self._err(f"size must be one of {list(self.SIZES)}")
            params["size"] = self.SIZES[size]
        if active is not None:
            params["isActive"] = "true" if active else "false"
        if len(params) <= 3:
            return self._err("give at least one of name, id_code, activity_code, region_code, legal_form_id, size")
        res = self._api("documents", params)
        if "error" in res:
            return self._err(res["error"])
        return self._result(pagination=res.get("pagination"), items=[self._entity(d) for d in res.get("data") or []],
                            source_url="https://br.geostat.ge/", cite="წყარო: საქსტატი (Geostat business register)")

    def company(self, id_code: str) -> dict:
        """One entity with director history (representatives) and dated name/address history."""
        code, kind = _ident(id_code)
        if not code:
            return self._err(kind)
        res = self._api("documents", {"identificationNumber": code, "lang": "ge", "page": 1, "limit": 5})
        if "error" in res:
            return self._err(res["error"])
        data = res.get("data") or []
        if not data:
            return self._result(found=False, id_code=code)
        ent = self._entity(data[0])
        sid = ent["stat_id"]
        reps = self._api("representatives", {"statId": sid})
        names = self._api("full-name-web", {"statId": sid})
        addrs = self._api("address-web", {"statId": sid})
        return self._result(
            found=True, **ent,
            representatives=[{"name": r.get("Name"), "position": r.get("Position"), "as_of": r.get("Date")}
                             for r in (reps if isinstance(reps, list) else [])][:60],
            name_history=[{"name": n.get("Full_Name"), "legal_form": n.get("Legal_Form"), "as_of": n.get("Date")}
                          for n in (names if isinstance(names, list) else [])],
            address_history=[{"address": a.get("Address"), "as_of": a.get("Date")}
                             for a in (addrs if isinstance(addrs, list) else [])],
            source_url="https://br.geostat.ge/", cite="წყარო: საქსტატი (Geostat business register)",
            note="statistical register — the legal register is NAPR (registry_company_search)")

    def activity_codes(self, query: str, limit: int = 20) -> dict:
        """Find NACE rev.2 activity codes by Georgian keyword or code prefix (for search filters)."""
        if self._activities is None:
            acts = self._api("activities")
            self._activities = acts if isinstance(acts, list) else []
        q = (query or "").strip().lower()
        hits = []
        for a in self._activities:
            code = str(a.get("Activity_Code") or a.get("Code") or "")
            label = str(a.get("Activity_Name") or a.get("Name") or "")
            if q and (code.startswith(q) or q in label.lower()):
                hits.append({"code": code, "name": label})
        return self._result(total=len(hits), items=hits[:max(1, int(limit))])

    def regions(self) -> dict:
        regs = self._api("locations/regions")
        return self._result(items=[{"code": r.get("Location_Code"), "name": r.get("Location_Name")}
                                   for r in (regs if isinstance(regs, list) else [])])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    BOG = "204378869"

    def show(label, res, keys=("total",), n=2):
        print(f"\n== {label}: " + ("ERROR " + res["error"] if "error" in res else
                                    " ".join(f"{k}={res.get(k)}" for k in keys)))
        for it in (res.get("items") or [])[:n]:
            print("   ", json.dumps(it, ensure_ascii=False)[:260])

    co = CompanyRegistryAPI()
    show("company by id", co.search_companies(id_code=BOG))
    show("company by name", co.search_companies(name="საქართველოს ბანკი"))
    show("participations", co.find_participations(org_name="საქართველოს ბანკი"))
    show("insolvency filings (30 days)", co.search_applications(
        date_from=(date.today() - timedelta(days=30)).isoformat(), app_type="insolvency_start"),
        ("total", "period"))
    pr = PropertyRegistryAPI()
    p = pr.parcel("01.18.04.009.016")
    print("\n== parcel:", {k: p.get(k) for k in ("cadastral_code", "area", "parcel_type", "ownership_type",
                                                  "address")}, "docs:", len(p.get("documents", [])),
          p.get("documents", [{}])[0] if p.get("documents") else p.get("error"))
    show("property applications", pr.applications(cadastral_code="01.18.04.009.016"), ("total", "last_page"))
    li = LienRegistryAPI()
    show("liens by id", li.search(id_code=BOG), ("total", "last_page"))
    show("liens by parcel", li.search(cadastral_code="05.34.23.779.01.01.506"))
    mp = MovablePledgeAPI()
    s = mp.search(BOG)
    show("movable pledges", s)
    if s.get("items"):
        d = mp.get_pledge(s["items"][0]["app_id"], 400)
        print("   detail:", d.get("text", d.get("error"))[:300].replace("\n", " | "), "scans:", len(d.get("scans", [])))
    show("lawyer by number", LawyerRegistryAPI().search("0001"), ("total_members", "total"))
    show("notaries Tbilisi", NotaryAPI().search("თბილისი"), ("region",))
    pl = ProcurementListsAPI()
    b = pl.list_entries("black")
    show("black list p1", b, ("list_type",))
    if b.get("items"):
        print("   entry:", pl.get_entry(b["items"][0]["url"]).get("text", "")[:300].replace("\n", " | "))
