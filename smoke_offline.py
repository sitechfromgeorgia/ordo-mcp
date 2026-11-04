#!/usr/bin/env python3
"""Offline smoke test — imports every module and exercises the parsers.

No network is used. Run from the repo root:

    python smoke_offline.py
    uv run python smoke_offline.py

The checks mirror the regressions covered by tests/test_parsers.py; this
script is the quick "does the package still import and parse" gate for a
machine that cannot reach the Georgian sites.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from court_api import EcdCourtAPI, SupremeCourtAPI, _html_to_text
from higher_courts_api import ConstitutionalCourtAPI
from matsne_api import MatsneAPI
from parliament_api import ParliamentAPI


def check_search_params():
    api = MatsneAPI()
    assert api._search_params(page=0)["page"] == 1
    assert api._search_params(page=1)["page"] == 2
    assert api._search_params(doc_type="კანონი")["group"] == "1000003"
    assert "error" in api._search_params(doc_type="not-a-type")


def check_law_resolution():
    assert MatsneAPI._core_law_match("constitution", "constitution of georgia")
    assert not MatsneAPI._core_law_match("constitution", "constitutional court")
    assert not MatsneAPI._core_law_match(
        "კონსტიტუცი", "საქართველოს საკონსტიტუციო სასამართლოს შესახებ")


def check_maindoc_text():
    body = "".join(f"<p>line {i}</p>" for i in range(700))
    html = (f'<div id="maindoc">{body}<div class="modal">junk</div>'
            '<div id="footer-wrapper">footer</div><script>x</script></div>')
    text = MatsneAPI()._maindoc_text(html)
    assert len(text.splitlines()) >= 700
    assert "junk" not in text and "footer" not in text


def check_html_to_text():
    text = _html_to_text(
        "<style>.css{font-size:12pt}</style><p>CASE OF X v. GEORGIA</p>"
        "<script>bad()</script>")
    assert "CASE OF X v. GEORGIA" in text
    assert "font-size" not in text and "bad()" not in text


def check_constitutional_title():
    html = ('<h1>საქართველოს საკონსტიტუციო სასამართლო</h1>'
            '<div id="printablePageContent"><h1>აქტის სახელწოდება</h1></div>')
    assert ConstitutionalCourtAPI._extract_act_title(html) == "აქტის სახელწოდება"


def check_package_pagination():
    api = ParliamentAPI()
    api._get_json = lambda path, params=None: (
        {"total": 51, "list": [{"id": i, "billName": f"b{i}"}
                               for i in range(50 if (params or {}).get("page") == 1 else 1)]}
    )
    result = api.get_package_bills(1)
    assert result["total"] == 51 and len(result["bills"]) == 51


def check_mcp_tool_registry():
    try:
        import mcp_server
    except ModuleNotFoundError as exc:  # e.g. a shell PYTHONPATH shadows the venv's mcp
        print(f"skip MCP tool registry: {exc}")
        return
    tools = asyncio.run(mcp_server.app.list_tools())
    assert len(tools) == 78, f"expected 78 tools, found {len(tools)}"


def main():
    # construct the wrappers too: this catches import/constructor regressions
    EcdCourtAPI()
    SupremeCourtAPI()
    checks = [
        ("matsne search params", check_search_params),
        ("law-name resolution", check_law_resolution),
        ("matsne full document text", check_maindoc_text),
        ("HTML to text", check_html_to_text),
        ("constitutional act title", check_constitutional_title),
        ("parliament package pagination", check_package_pagination),
        ("MCP tool registry", check_mcp_tool_registry),
    ]
    for name, fn in checks:
        fn()
        print(f"ok  {name}")
    print(f"\nOFFLINE SMOKE OK — {len(checks)} checks, no network used")


if __name__ == "__main__":
    main()
