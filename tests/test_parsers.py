"""Offline unit tests for the parsers fixed in this PR.

Run from the repo root with either:
    uvx pytest -q
    python -m pytest -q          (if pytest is installed)
No test here performs network I/O.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from court_api import _html_to_text  # noqa: E402
from higher_courts_api import ConstitutionalCourtAPI  # noqa: E402
from matsne_api import MatsneAPI  # noqa: E402
from parliament_api import ParliamentAPI  # noqa: E402


# ── matsne search parameter builder ─────────────────────────────

def test_search_page_is_zero_based_for_callers():
    api = MatsneAPI()
    assert api._search_params(page=0)["page"] == 1
    assert api._search_params(page=1)["page"] == 2
    # negative pages clamp to the first page instead of requesting page 0/-1
    assert api._search_params(page=-3)["page"] == 1


def test_search_doc_type_uses_group_not_type():
    api = MatsneAPI()
    assert api._search_params(doc_type="კანონი")["group"] == "1000003"
    assert api._search_params(doc_type="საქართველოს კანონი")["group"] == "1000003"
    assert api._search_params(doc_type="1000003")["group"] == "1000003"
    # `type` is the consolidated/all scope switch, not a document type
    params = api._search_params(doc_type="all")
    assert params["type"] == "all" and "group" not in params


def test_search_rejects_unknown_doc_type_instead_of_ignoring_it():
    result = MatsneAPI()._search_params(doc_type="not-a-real-type")
    assert "error" in result
    assert "available" in result


# ── law-name resolution ─────────────────────────────────────────

def test_constitution_stem_does_not_match_constitutional_court():
    assert not MatsneAPI._core_law_match(
        "constitution", "law on the constitutional court")
    assert not MatsneAPI._core_law_match(
        "კონსტიტუცი", "საქართველოს საკონსტიტუციო სასამართლოს შესახებ")


def test_constitution_stem_still_matches_the_constitution():
    assert MatsneAPI._core_law_match("constitution", "constitution of georgia")
    assert MatsneAPI._core_law_match("კონსტიტუცი", "საქართველოს კონსტიტუცია")
    assert MatsneAPI._core_law_match(
        "სამოქალაქო კოდექს", "საქართველოს სამოქალაქო კოდექსის 361-ე მუხლი")


# ── matsne full-document text ───────────────────────────────────

def test_maindoc_text_is_not_capped_at_500_lines():
    body = "".join(f"<p>line {i}</p>" for i in range(700))
    html = f'<html><body><div id="maindoc">' \
           '<style>p.MsoNormal {margin:0in}</style>' \
           f'{body}' \
           '<div class="modal">modal junk</div>' \
           '<div id="footer-wrapper">footer junk</div>' \
           '<script>var x = 1;</script></div></body></html>'
    text = MatsneAPI()._maindoc_text(html)
    lines = text.splitlines()
    assert len(lines) >= 700
    assert lines[0] == "line 0" and lines[-1] == "line 699"
    assert "MsoNormal" not in text
    assert "junk" not in text and "var x" not in text


# ── Constitutional Court detail page ────────────────────────────

def test_constitutional_act_title_skips_site_header():
    html = (
        '<h1>საქართველოს საკონსტიტუციო სასამართლო</h1>'
        '<div id="printablePageContent">'
        '<h1>ა(ა)იპ "პუბლიკა" საქართველოს პარლამენტის წინააღმდეგ</h1>'
        "<p>text</p></div>"
        '<h1>1. სადავო ნორმატიული აქტები</h1>'
    )
    assert ConstitutionalCourtAPI._extract_act_title(html).startswith('ა(ა)იპ "პუბლიკა"')


def test_constitutional_act_title_falls_back_to_second_h1():
    html = "<h1>site header</h1><h1>the act title</h1>"
    assert ConstitutionalCourtAPI._extract_act_title(html) == "the act title"


# ── HTML → text ─────────────────────────────────────────────────

def test_html_to_text_drops_style_and_script_bodies():
    html = (
        "<style>.s800EAC49 { font-size:12pt }</style>"
        "<p>CASE OF X v. GEORGIA</p>"
        "<script>alert('no')</script>"
        "<!-- hidden comment -->"
    )
    text = _html_to_text(html)
    assert "CASE OF X v. GEORGIA" in text
    for junk in ("font-size", "s800EAC49", "alert", "hidden comment"):
        assert junk not in text


# ── parliament package pagination ───────────────────────────────

def test_package_bills_paginates_with_page_max(monkeypatch):
    api = ParliamentAPI()
    calls = []

    def fake_get_json(path, params=None):
        params = params or {}
        calls.append(params)
        if params["page"] == 1:
            return {"total": 51,
                    "list": [{"id": i, "billName": f"bill {i}"} for i in range(50)]}
        return {"total": 51, "list": [{"id": 50, "billName": "bill 50"}]}

    monkeypatch.setattr(api, "_get_json", fake_get_json)
    result = api.get_package_bills(7)
    assert calls[0]["limit"] == api.PAGE_MAX == 50
    assert len(calls) == 2
    assert result["total"] == 51
    assert len(result["bills"]) == 51
