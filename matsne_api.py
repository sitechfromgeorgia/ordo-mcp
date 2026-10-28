#!/usr/bin/env python3
"""
Matsne.gov.ge API Wrapper — Production Version
================================================
Reverse-engineered from Drupal's internal JavaScript API.

Endpoints discovered (2026-05-17):
  - POST /document/suggest              — autocomplete search
  - GET  /ka/document/search?is-ajax=1  — JSON search results
  - GET  /ka/document/tree/{id}/{part}  — document structure tree
  - GET  /ka/document/backReferences/{id} — back references
  - GET  /ka/document/comment/{id}/read — comments
  - GET  /ka/document/linked/{id}/{anchor} — inline HTML fragment
  - GET  /annotation/api/search         — annotation search
  - POST /favorites                     — favorites management (auth)
  - GET  /favorites?method=getFavoritesList — favorites list (auth)
  - GET  speech.matsne.gov.ge:443/voice — text-to-speech

Usage:
    from matsne_api import MatsneAPI
    api = MatsneAPI()
    results = api.suggest("პერსონალური")
    doc = api.get_document(6840140)
    tree = api.get_tree(6840140)
    recent = api.search(page=0)
"""

import difflib
import html as html_lib
import json
import re
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_UNSUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
# Article heading: "მუხლი 361. …", "მუხლი 1507¹. …", English "Article 361 – …"
_ARTICLE_HEAD = r'(?:მუხლი|Article)\s+(\d+)([⁰¹²³⁴⁵⁶⁷⁸⁹]*)\s*(?=[.–—-]|$)'


@dataclass
class DocumentMeta:
    """Structured document metadata."""
    id: int
    title: str = ""
    number: str = ""
    organ: str = ""
    date_received: str = ""
    date_published: str = ""
    doc_type: str = ""
    registration_code: str = ""
    status: str = ""  # active/expired
    expiry_date: str = ""  # ძალის დაკარგვის თარიღი (DD/MM/YYYY), empty if in force
    url: str = ""
    text: str = ""
    tree: dict = field(default_factory=dict)
    back_references: list = field(default_factory=list)
    comments: list = field(default_factory=list)


class MatsneAPI:
    """Production wrapper around matsne.gov.ge's hidden Drupal API."""

    BASE = "https://matsne.gov.ge"
    VOICE_BASE = "https://speech.matsne.gov.ge:443"
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

    def __init__(self, lang: str = "ka", rate_limit: float = 0.3):
        """
        Args:
            lang: Language prefix ('ka' for Georgian, 'en' for English)
            rate_limit: Minimum seconds between requests (default 0.3s)
        """
        self.lang = lang
        self.rate_limit = rate_limit
        self._cookies = {}
        self._last_request_time = 0

    def _wait_rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_time = time.time()

    def _request(self, url: str, method: str = "GET",
                 data: Optional[dict] = None,
                 accept_json: bool = False,
                 retries: int = 2) -> str:
        """Make HTTP request with proper headers, cookies, and retry logic."""
        self._wait_rate_limit()

        headers = {
            "User-Agent": self.UA,
            "Accept": "application/json" if accept_json else "text/html,application/xhtml+xml",
            "Accept-Language": "ka,en;q=0.9",
        }
        if accept_json:
            headers["X-Requested-With"] = "XMLHttpRequest"

        # Add cookies
        if self._cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            headers["Cookie"] = cookie_str

        body = None
        if data:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    # Save cookies
                    for header in resp.headers.get_all("Set-Cookie") or []:
                        m = re.match(r'([^=]+)=([^;]+)', header.strip())
                        if m:
                            self._cookies[m.group(1)] = m.group(2)
                    return resp.read().decode("utf-8")
            except Exception as e:
                if attempt < retries:
                    time.sleep(1 * (attempt + 1))  # exponential backoff
                    continue
                return f"ERROR: {e}"

    # ── Public API Endpoints ───────────────────────────────────

    def suggest(self, text: str) -> list[dict]:
        """
        POST /document/suggest — autocomplete search.

        Returns list of matching documents with {id, state, title}.
        Fast (~0.58s), returns up to 50 results.

        Args:
            text: Search query (Georgian text)

        Returns:
            [{"id": int, "state": "active"|"expired", "title": str}, ...]
        """
        url = f"{self.BASE}/document/suggest"
        raw = self._request(url, method="POST", data={"text": text})
        if raw.startswith("ERROR"):
            return [{"error": raw}]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return [{"error": "Invalid JSON", "raw": raw[:200]}]

    def search(self, page: int = 0, query: str = "",
               limit: int = 20, sort: str = "",
               issuer: str = "", doc_type: str = "",
               number: str = "", registration_code: str = "",
               date_from: str = "", date_to: str = "") -> dict:
        """
        GET /ka/document/search — search with JSON response.

        Uses is-ajax=1 parameter to get JSON instead of full HTML page.

        Args:
            page: Page number (0-indexed)
            query: Full-text search query
            limit: Results per page (5, 10, 20, 30, 50, 100)
            sort: Sort order (e.g., 'publishDate_desc')
            issuer: Document issuer/organ filter
            doc_type: Document type filter
            number: Document number filter
            registration_code: Registration code filter
            date_from: Date from (DD/MM/YYYY format)
            date_to: Date to (DD/MM/YYYY format)

        Returns:
            {"documents": [...], "count": int, "raw_pagination": str}
        """
        params = {"page": page, "is-ajax": 1}
        if query:
            params["query"] = query
        if limit != 20:
            params["limit"] = limit
        if sort:
            params["sort"] = sort
        if issuer:
            params["issuer"] = issuer
        if doc_type:
            params["type"] = doc_type
        if number:
            params["number"] = number
        if registration_code:
            params["registration_code"] = registration_code
        if date_from:
            params["signing_date_fr[date]"] = date_from
        if date_to:
            params["signing_date_to[date]"] = date_to

        url = f"{self.BASE}/{self.lang}/document/search?{urllib.parse.urlencode(params)}"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

        # Parse documents_list HTML into structured data
        docs = []
        html = data.get("documents_list", "")

        # Try panel-based structure (used with query search)
        if '<div class="panel' in html:
            panels = re.split(r'<div class="panel panel-success document-search-result-item">', html)
            for panel in panels[1:]:
                doc = self._parse_panel(panel)
                if doc:
                    docs.append(doc)
        else:
            # Fallback: simple link-based structure
            blocks = re.split(r'<p><a href="/ka/document/view/', html)
            for block in blocks[1:]:
                doc_id_m = re.match(r'([0-9]+)', block)
                if not doc_id_m:
                    continue
                doc_id = int(doc_id_m.group(1))
                title_m = re.search(r'">([^<]+)</a>', block)
                title = title_m.group(1).strip() if title_m else "N/A"
                date_m = re.search(r'<small>(\d{2}/\d{2}/\d{4})', block)
                date = date_m.group(1) if date_m else "N/A"
                docs.append({
                    "id": doc_id,
                    "title": title,
                    "date": date,
                    "url": f"{self.BASE}/ka/document/view/{doc_id}"
                })

        return {
            "documents": docs,
            "count": len(docs),
            "raw_pagination": data.get("pagination", "")
        }

    def _parse_panel(self, panel_html: str) -> Optional[dict]:
        """Parse a single panel block from search results."""
        # Document link and title
        link_m = re.search(r'href="/ka/document/view/(\d+)"[^>]*>(.*?)</a>', panel_html, re.DOTALL)
        if not link_m:
            return None
        doc_id = int(link_m.group(1))
        title = re.sub(r'<[^>]+>', '', link_m.group(2)).strip()

        # Date
        date_m = re.search(r'<small>(\d{2}/\d{2}/\d{4})', panel_html)
        date = date_m.group(1) if date_m else ""

        # Document type
        type_m = re.search(r'<li>\s*<small>([^<●]+)</small>', panel_html)
        doc_type = type_m.group(1).strip() if type_m else ""

        # Organ
        organ_m = re.search(r'<small>●</small>\s*</li>\s*<li>\s*<small>([^<]+)</small>', panel_html)
        organ = organ_m.group(1).strip() if organ_m else ""

        return {
            "id": doc_id,
            "title": title,
            "date": date,
            "doc_type": doc_type,
            "organ": organ,
            "url": f"{self.BASE}/ka/document/view/{doc_id}"
        }

    def search_quick(self) -> list[str]:
        """
        Quick check: what dates are on the first search page?
        Returns list of unique dates (DD/MM/YYYY format), newest first.
        """
        url = f"{self.BASE}/{self.lang}/document/search?page=0"
        html = self._request(url)
        if html.startswith("ERROR"):
            return []
        dates = re.findall(r'<small>(\d{2}/\d{2}/\d{4})', html)
        return sorted(set(dates), reverse=True)

    def get_tree(self, doc_id: int, part_id: int = 0) -> dict:
        """
        GET /ka/document/tree/{id}/{part} — document structure tree.

        Returns hierarchical structure with Title, Anchor, DocumentPart.
        Complex documents (codes) return deep nested trees.

        Args:
            doc_id: Document ID
            part_id: Part ID (usually 0)

        Returns:
            {"Title": str, "Anchor": str, "DocumentPart": {...}|[...]}
        """
        url = f"{self.BASE}/ka/document/tree/{doc_id}/{part_id}"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def get_back_references(self, doc_id: int, part_id: int = 0) -> list:
        """
        GET /ka/document/backReferences/{id}?part_id=X

        Returns documents that reference this document.
        """
        url = f"{self.BASE}/ka/document/backReferences/{doc_id}?part_id={part_id}"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    def get_comments(self, doc_id: int, part_id: int = 0) -> list:
        """
        GET /ka/document/comment/{id}/read?part_id=X

        Returns comments on a document. Each comment has:
        {cid, text, html, anchor, rating, author: {name, fullName, uid},
         created, formattedCreate, changed, formattedChange}
        """
        url = f"{self.BASE}/ka/document/comment/{doc_id}/read?part_id={part_id}"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    def get_linked(self, doc_id: int, anchor: str = "document") -> dict:
        """
        GET /ka/document/linked/{id}/{anchor} — inline HTML fragment.

        Returns HTML content for a specific document part.
        Used for inline viewing of linked documents.

        Args:
            doc_id: Document ID
            anchor: Document anchor (e.g., "DOCUMENT:1;HEADER:1;")

        Returns:
            {"html": str}
        """
        url = f"{self.BASE}/ka/document/linked/{doc_id}/{anchor}"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def get_annotations(self, uri: str) -> dict:
        """
        GET /annotation/api/search?uri=...

        Returns annotations for a document URL.

        Args:
            uri: Document URI (e.g., "document/view/6840140")

        Returns:
            {"total": int, "rows": [...]}
        """
        url = f"{self.BASE}/annotation/api/search?uri={urllib.parse.quote(uri)}"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    # ── Authenticated Endpoints (require login) ────────────────

    def add_comment(self, doc_id: int, anchor: str, text: str, title: str = "") -> dict:
        """
        POST /ka/document/comment/{id}/create — create comment (AUTH REQUIRED).

        Args:
            doc_id: Document ID
            anchor: Document anchor (e.g., "DOCUMENT:1;")
            text: Comment text
            title: Comment title (optional)

        Returns:
            {"cid": int, ...} on success
        """
        url = f"{self.BASE}/ka/document/comment/{doc_id}/create"
        data = {"anchor": anchor, "text": text}
        if title:
            data["title"] = title
        raw = self._request(url, method="POST", data=data, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def update_comment(self, comment_id: int, text: str) -> dict:
        """
        POST /ka/document/comment/{cid}/update — update comment (AUTH REQUIRED).
        """
        url = f"{self.BASE}/ka/document/comment/{comment_id}/update"
        raw = self._request(url, method="POST", data={"text": text}, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def remove_comment(self, comment_id: int) -> dict:
        """
        GET /ka/document/comment/{cid}/remove — delete comment (AUTH REQUIRED).
        """
        url = f"{self.BASE}/ka/document/comment/{comment_id}/remove"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def add_favorite(self, doc_id: int) -> dict:
        """
        POST /favorites — add document to favorites (AUTH REQUIRED).
        """
        url = f"{self.BASE}/favorites"
        raw = self._request(url, method="POST",
                           data={"documentId": doc_id, "method": "add"},
                           accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def remove_favorite(self, doc_id: int) -> dict:
        """
        POST /favorites — remove document from favorites (AUTH REQUIRED).
        """
        url = f"{self.BASE}/favorites"
        raw = self._request(url, method="POST",
                           data={"documentId": doc_id, "method": "remove"},
                           accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    def get_favorites_list(self) -> dict:
        """
        GET /favorites?method=getFavoritesList — get favorites (AUTH REQUIRED).
        """
        url = f"{self.BASE}/favorites?method=getFavoritesList"
        raw = self._request(url, accept_json=True)
        if raw.startswith("ERROR"):
            return {"error": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON", "raw": raw[:200]}

    # ── Voice Server ───────────────────────────────────────────

    def get_voice_url(self, text: str) -> str:
        """
        Get text-to-speech URL for short text.
        Note: Uses anti-bot protection (XZCookie + document.ZXKey).
        """
        return f"{self.VOICE_BASE}/voice?t={urllib.parse.quote(text)}"

    def get_voice_url_big(self, doc_id: int) -> str:
        """
        Get text-to-speech URL for full document.
        POST to this URL with: t=text, r=rate, docId=id, pubId=0
        """
        return f"{self.VOICE_BASE}/voice/big"

    # ── Document HTML (may be blocked by bot detection) ─────────

    def get_document_html(self, doc_id: int, publication: Optional[int] = None,
                          lang: Optional[str] = None) -> str:
        """
        GET /ka/document/view/{id}[?publication=N] — raw HTML page.

        ⚠️ INTERMITTENTLY BLOCKED by bot detection.
        Some IDs work, some return "Access Denied".
        Use as primary attempt, fall back to browser tools.

        Args:
            publication: Consolidated version index (0 = original text).
                None = current version.
            lang: 'ka' or 'en' (English translation page); default self.lang
        """
        url = f"{self.BASE}/{lang or self.lang}/document/view/{doc_id}"
        if publication is not None:
            url += f"?publication={publication}"
        return self._request(url)

    def parse_document_meta(self, html: str, doc_id: int) -> DocumentMeta:
        """Parse document metadata from HTML table."""
        meta = DocumentMeta(id=doc_id, title="", url=f"{self.BASE}/ka/document/view/{doc_id}")

        # Title from <title> tag
        m = re.search(r'<title>([^<|]+)', html)
        if m:
            meta.title = m.group(1).strip().strip('"').strip('\u201e').strip('\u201c')

        # Find metadata table
        tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
        for table_html in tables:
            if "დოკუმენტის ნომერი" not in table_html:
                continue
            rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) < 2:
                    continue
                label = re.sub(r'<[^>]+>', '', cells[0]).strip()
                value = re.sub(r'<[^>]+>', '', cells[1]).strip()
                if "ძალის დაკარგვის" in label:
                    meta.expiry_date = value
                elif "ნომერი" in label:
                    meta.number = value
                elif "მიმღები" in label:
                    meta.organ = value
                elif "მიღების" in label:
                    meta.date_received = value
                elif "გამოქვეყნების" in label:
                    meta.date_published = value
                elif "ტიპი" in label:
                    meta.doc_type = value
                elif "რეგისტრაციო" in label:
                    meta.registration_code = value
            break
        meta.status = "expired" if meta.expiry_date else "active"

        # Main document text
        main = re.search(r'id="maindoc"[^>]*>(.*?)$', html, re.DOTALL)
        if main:
            text = re.sub(r'<[^>]+>', '\n', main.group(1)[:100000])
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            meta.text = '\n'.join(lines[:500])

        return meta

    def _collect_anchors(self, part: dict | list, max_depth: int = 10) -> list[str]:
        """Recursively collect all anchors from a document tree part."""
        anchors = []
        if isinstance(part, dict):
            anchor = part.get("Anchor", "")
            if anchor and anchor != "DOCUMENT:1;":
                anchors.append(anchor)
            dp = part.get("DocumentPart")
            if max_depth > 0:
                anchors.extend(self._collect_anchors(dp, max_depth - 1))
        elif isinstance(part, list):
            for item in part:
                if max_depth > 0:
                    anchors.extend(self._collect_anchors(item, max_depth - 1))
        return anchors

    def get_text_via_linked(self, doc_id: int, tree: dict) -> str:
        """Get document text by fetching linked fragments for every tree anchor.

        Always available (uses API endpoint, not HTML page).
        """
        # linked/{id}/document returns the whole current text in one call
        full = self.get_linked(doc_id, "document")
        if full.get("html"):
            return self._fragment_to_text(full["html"])
        anchors = self._collect_anchors(tree)
        texts = []
        for anchor in anchors[:100]:
            linked = self.get_linked(doc_id, anchor)
            if "error" not in linked:
                html = linked.get("html", "")
                text = re.sub(r'<[^>]+>', '\n', html)
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                texts.extend(lines)
        return '\n'.join(texts)

    def get_document(self, doc_id: int, publication: Optional[int] = None) -> dict:
        """
        Get full document: metadata + text + tree + back-references + comments.

        publication: consolidated version index (see list_versions); None = current.
        The tree / linked-text fallback always reflects the CURRENT version.

        Uses multiple endpoints:
        1. HTML page (may be blocked) → metadata + text
        2. get_text_via_linked() → reliable text via API (fallback)
        3. Tree endpoint → document structure
        4. Back-references → citing documents
        5. Comments → user comments

        Returns dict with all available data.
        """
        url = f"{self.BASE}/ka/document/view/{doc_id}"
        if publication is not None:
            url += f"?publication={publication}"
        result = {
            "id": doc_id,
            "url": url,
            "publication": publication,
            "meta": {},
            "text": "",
            "tree": {},
            "back_references": [],
            "comments": [],
            "linked_docs": [],
            "errors": []
        }

        # 1. Get tree structure — needed early for text fallback
        tree = self.get_tree(doc_id)
        if "error" not in tree:
            result["tree"] = tree

        # 2. Get text — try HTML first, fall back to linked fragments
        html = self.get_document_html(doc_id, publication)
        if html.startswith("ERROR"):
            result["errors"].append(f"HTML fetch failed: {html}")
        elif "Access Denied" in html:
            result["errors"].append("HTML fetch blocked (Access Denied)")
        else:
            meta = self.parse_document_meta(html, doc_id)
            result["meta"] = {
                "title": meta.title,
                "number": meta.number,
                "organ": meta.organ,
                "date_received": meta.date_received,
                "date_published": meta.date_published,
                "doc_type": meta.doc_type,
                "registration_code": meta.registration_code,
                "status": meta.status,
                "expiry_date": meta.expiry_date,
            }
            result["text"] = meta.text

        # If HTML text is empty/missing, use tree-based fallback (current version only)
        if result["tree"] and not result["text"] and publication is None:
            result["text"] = self.get_text_via_linked(doc_id, result["tree"])
            if result["text"]:
                result["meta"]["title"] = result["tree"].get("Title", "")

        # 3. Get back-references
        refs = self.get_back_references(doc_id)
        result["back_references"] = refs

        # 4. Get comments
        comments = self.get_comments(doc_id)
        result["comments"] = comments

        return result

    def has_today(self) -> bool:
        """Check if today's documents are published."""
        from datetime import datetime
        today = datetime.now().strftime("%d/%m/%Y")
        dates = self.search_quick()
        return today in dates

    def today_documents(self) -> list[dict]:
        """Get today's documents (if any)."""
        from datetime import datetime
        today = datetime.now().strftime("%d/%m/%Y")
        result = self.search(page=0)
        if "error" in result:
            return []
        return [d for d in result.get("documents", []) if d.get("date") == today]

    def search_by_keyword(self, keyword: str, max_results: int = 10) -> list[dict]:
        """
        Search using suggest endpoint for quick keyword matching.
        Returns active documents matching the keyword.
        """
        suggestions = self.suggest(keyword)
        results = []
        for s in suggestions[:max_results]:
            if "error" in s:
                continue
            results.append({
                "id": s.get("id"),
                "title": s.get("title"),
                "status": s.get("state"),
                "url": f"{self.BASE}/ka/document/view/{s.get('id')}"
            })
        return results

    def get_document_by_type(self, doc_id: int) -> dict:
        """
        Get document and detect its type from tree structure.
        Returns document data with detected type.
        """
        doc = self.get_document(doc_id)
        tree = doc.get("tree", {})
        if tree and "Title" in tree:
            doc["detected_type"] = self._detect_doc_type(tree)
        return doc

    def _detect_doc_type(self, tree: dict) -> str:
        """Detect document type from tree structure."""
        title = tree.get("Title", "").lower()
        if "კანონი" in title:
            return "law"
        elif "ბრძანებულება" in title:
            return "decree"
        elif "დადგენილება" in title:
            return "resolution"
        elif "კოდექსი" in title:
            return "code"
        elif "ბრძანება" in title:
            return "order"
        else:
            return "other"

    # ── Legal research: versions, articles, status ─────────────

    # Core codes → matsne document id (verified live 2026-10-29).
    # Lowercase stems matched as substrings of the caller's law name; the
    # procedural codes come first so "სამოქალაქო საპროცესო კოდექსი" never
    # resolves to the Civil Code.
    CORE_LAWS = {
        "სამოქალაქო საპროცესო კოდექს": 29962,
        "სისხლის სამართლის საპროცესო კოდექს": 90034,
        "ადმინისტრაციული საპროცესო კოდექს": 16492,
        "ზოგადი ადმინისტრაციული კოდექს": 16270,
        "ადმინისტრაციულ სამართალდარღვევათა კოდექს": 28216,
        "სისხლის სამართლის კოდექს": 16426,
        "სამოქალაქო კოდექს": 31702,
        "საგადასახადო კოდექს": 1043717,
        "შრომის კოდექს": 1155567,
        "კონსტიტუცი": 30346,
        "civil procedure code": 29962,
        "criminal procedure code": 90034,
        "administrative procedure code": 16492,
        "general administrative code": 16270,
        "administrative offences code": 28216,
        "criminal code": 16426,
        "civil code": 31702,
        "tax code": 1043717,
        "labour code": 1155567,
        "labor code": 1155567,
        "constitution": 30346,
    }

    @staticmethod
    def _html_error(html: str) -> str:
        """Return an error string if a view-page fetch failed or was blocked."""
        if html.startswith("ERROR"):
            return f"HTML fetch failed: {html}"
        if "Access Denied" in html[:5000]:
            return "HTML fetch blocked (Access Denied)"
        return ""

    @staticmethod
    def _fragment_to_text(fragment: str) -> str:
        """HTML fragment → plain text; <sup>1</sup> becomes ¹ (151¹ style)."""
        s = re.sub(r'\s+', ' ', fragment)
        s = re.sub(r'<sup>\s*(\d+)\s*</sup>',
                   lambda m: m.group(1).translate(_SUPERSCRIPT), s)
        s = re.sub(r'<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>', '\n', s, flags=re.I)
        s = re.sub(r'<[^>]+>', '', s)
        s = html_lib.unescape(s).replace('\xa0', ' ')
        lines = [re.sub(r'[ \t\r]+', ' ', l).strip() for l in s.split('\n')]
        return '\n'.join(l for l in lines if l)

    @staticmethod
    def _article_key(article) -> str:
        """Normalize an article number: 361 → "361", 1507¹ / "1507^1" → "1507^1"."""
        m = re.match(r'\s*(\d+)\s*(?:([⁰¹²³⁴⁵⁶⁷⁸⁹]+)|\^\s*(\d+))?', str(article))
        if not m:
            return ""
        sup = (m.group(2) or "").translate(_UNSUPERSCRIPT) or m.group(3) or ""
        return m.group(1) + (f"^{sup}" if sup else "")

    @staticmethod
    def _parse_date(value: str) -> Optional[datetime]:
        """Parse DD/MM/YYYY or YYYY-MM-DD."""
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
        return None

    def resolve_law(self, law: str) -> dict:
        """
        Resolve a law name (Georgian or English, any grammatical case) or a
        numeric id to a matsne document id.

        Core codes resolve from CORE_LAWS without a request; anything else goes
        through suggest(), preferring active documents with an exact title.

        Returns:
            {"doc_id": int, "title": str, "resolved_by": "id"|"core_table"|"suggest"}
        """
        law = (law or "").strip()
        if not law:
            return {"error": "Provide a law name or doc_id"}
        if law.isdigit():
            return {"doc_id": int(law), "title": "", "resolved_by": "id"}
        low = law.lower()
        for stem, doc_id in self.CORE_LAWS.items():
            if stem in low:
                return {"doc_id": doc_id, "title": stem, "resolved_by": "core_table"}
        hits = [s for s in self.suggest(law) if "error" not in s]
        if not hits:
            return {"error": f"No matsne document matches '{law}'"}
        norm = lambda t: re.sub(r'\s+', ' ', t).strip().lower()
        exact = [s for s in hits if norm(s.get("title", "")) in (low, f"საქართველოს {low}")]
        active = [s for s in (exact or hits) if s.get("state") == "active"]
        best = (active or exact or hits)[0]
        return {"doc_id": int(best["id"]), "title": best.get("title", ""),
                "status": best.get("state", ""), "resolved_by": "suggest"}

    def _parse_versions(self, html: str, doc_id: int) -> list[dict]:
        """Consolidated versions from the view page sidebar, oldest first."""
        versions, seen = [], set()
        pattern = r'href="/\w+/document/view/%d\?publication=(\d+)"[^>]*>\s*(\d{2}/\d{2}/\d{4})\s*<' % doc_id
        for pub, date in re.findall(pattern, html):
            if pub in seen:
                continue
            seen.add(pub)
            versions.append({
                "publication": int(pub),
                "date": date,
                "url": f"{self.BASE}/ka/document/view/{doc_id}?publication={pub}",
            })
        versions.sort(key=lambda v: v["publication"])
        return versions

    def _parse_amendments(self, html: str) -> list[dict]:
        """Amending acts from the "დოკუმენტის ცვლილებები" modal, newest first."""
        i = html.find('id="changesModal"')
        if i < 0:
            return []
        nxt = re.search(r'class="modal[ "]', html[i + 30:])  # next modal, not modal-dialog
        modal = html[i:i + 30 + nxt.start()] if nxt else html[i:]
        amendments = []
        for m in re.finditer(r'<a href="/\w+/document/view/(\d+)"\s+class="row list-group-item">(.*?)</a>',
                             modal, re.S):
            block = m.group(2)
            field_of = lambda label: (re.search(label + r'\s*:\s*([^<]+)<', block) or [None, ""])[1].strip()
            index = re.search(r'col-md-1">\s*(\d+)', block)
            title = re.search(r'col-md-12">(.*?)</div>', block, re.S)
            amendments.append({
                "publication": int(index.group(1)) if index else None,
                "amending_doc_id": int(m.group(1)),
                "date_received": field_of("მიღების თარიღი"),
                "date_published": field_of("გამოქ. თარიღი"),
                "number": field_of("დოკუმენტის ნომერი"),
                "title": self._fragment_to_text(title.group(1)) if title else "",
                "url": f"{self.BASE}/ka/document/view/{m.group(1)}",
            })
        return amendments

    def list_versions(self, doc_id: int) -> dict:
        """
        All consolidated versions (რედაქციები) of a document + the amending acts.

        Each version is reachable via ?publication=N (0 = original text). The
        version date is matsne's consolidation date; individual amendments may
        have their own entry-into-force / transitional rules — check the
        amending act when the exact date matters.

        Returns:
            {"doc_id", "title", "status", "expiry_date", "versions": [{publication, date, url}],
             "amendments": [{publication, amending_doc_id, date_received, date_published, number, title, url}]}
        """
        html = self.get_document_html(doc_id)
        err = self._html_error(html)
        if err:
            return {"doc_id": doc_id, "error": err}
        meta = self.parse_document_meta(html, doc_id)
        return {
            "doc_id": doc_id,
            "title": meta.title,
            "status": meta.status,
            "expiry_date": meta.expiry_date,
            "versions": self._parse_versions(html, doc_id),
            "amendments": self._parse_amendments(html),
        }

    def version_for_date(self, doc_id: int, as_of_date: str) -> dict:
        """
        Pick the consolidated version in force on a date (DD/MM/YYYY or YYYY-MM-DD):
        the latest version whose consolidation date is on or before it.
        """
        target = self._parse_date(as_of_date)
        if not target:
            return {"error": f"Unrecognized date '{as_of_date}' — use DD/MM/YYYY or YYYY-MM-DD"}
        data = self.list_versions(doc_id)
        if "error" in data:
            return data
        if not data["versions"]:
            return {"error": "No consolidated versions listed for this document", "doc_id": doc_id}
        eligible = [v for v in data["versions"] if self._parse_date(v["date"]) <= target]
        if not eligible:
            return {"error": f"{as_of_date} precedes the first published version "
                             f"({data['versions'][0]['date']})", "doc_id": doc_id}
        picked = dict(max(eligible, key=lambda v: v["publication"]))
        picked["amendment"] = next((a for a in data["amendments"]
                                    if a["publication"] == picked["publication"]), None)
        picked["caveat"] = ("Version date = adoption date of the amending act, not its entry "
                            "into force. If the facts fall near this date, read the amending "
                            "act's final provisions and compare with the previous version.")
        return picked

    def _split_articles(self, html: str) -> list[dict]:
        """
        Split a view page into articles: [{key, label, anchor, text}].

        Articles are <a name="part_N"> anchors whose label starts with "მუხლი";
        an article runs until the next anchor with a different part id.
        Falls back to splitting plain text on "მუხლი N." / "Article N –" heading
        lines if there are no anchors (English translations have none).
        """
        start = html.find('id="maindoc"')
        body = html[start:] if start >= 0 else html
        script = body.find('<script')
        if script > 0:
            body = body[:script]
        anchors = list(re.finditer(r'<a[^>]*\bname="(part_\d+)"[^>]*>(.*?)</a>', body, re.S))
        articles = []
        for i, m in enumerate(anchors):
            if articles and articles[-1]["anchor"] == m.group(1):
                continue
            label = self._fragment_to_text(m.group(2))
            am = re.match(_ARTICLE_HEAD, label)
            if not am:
                continue
            end = len(body)
            for nxt in anchors[i + 1:]:
                if nxt.group(1) != m.group(1):
                    end = nxt.start()
                    break
            articles.append({
                "key": self._article_key(am.group(1) + am.group(2)),
                "label": label,
                "anchor": m.group(1),
                "text": self._fragment_to_text(body[m.start():end]),
            })
        if not articles:
            text = self._fragment_to_text(body)
            heads = list(re.finditer(r'(?m)^' + _ARTICLE_HEAD, text))
            for i, am in enumerate(heads):
                end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
                chunk = text[am.start():end].strip()
                articles.append({
                    "key": self._article_key(am.group(1) + am.group(2)),
                    "label": chunk.split('\n', 1)[0],
                    "anchor": "",
                    "text": chunk,
                })
        return articles

    @staticmethod
    def _extract_part(text: str, part: int) -> str:
        """Pull numbered part ("2. ...") out of an article text, with its sub-points."""
        lines = text.split('\n')
        out = []
        for line in lines[1:]:  # skip the "მუხლი N." heading
            num = re.match(r'(\d+)\.\s', line)
            if num:
                if out:
                    break
                if int(num.group(1)) == part:
                    out.append(line)
            elif out:
                out.append(line)
        return '\n'.join(out)

    def _active_version(self, html: str) -> dict:
        m = re.search(r'publication=(\d+)"\s*class="active list-group-item">\s*(\d{2}/\d{2}/\d{4})', html)
        return {"publication": int(m.group(1)), "date": m.group(2)} if m else {}

    def get_article(self, article: str, law: str = "", doc_id: int = 0,
                    part: Optional[int] = None, publication: Optional[int] = None,
                    as_of_date: str = "", lang: str = "ka") -> dict:
        """
        Get one article by human citation — e.g. article="361", law="სამოქალაქო კოდექსი".

        Args:
            article: Article number; superscript articles as "1507¹" or "1507^1"
            law: Law name (Georgian/English, any case) — ignored if doc_id given
            doc_id: Matsne document id
            part: Optional numbered part (ნაწილი) to extract
            publication: Consolidated version index (0 = original); None = current
            as_of_date: Return the version in force on this date (DD/MM/YYYY or
                YYYY-MM-DD); overrides `publication`
            lang: 'ka' (official) or 'en' (matsne's English translation, when one
                exists — unofficial and may lag the Georgian text)

        Returns:
            {"doc_id", "title", "article", "heading", "text", "part_text"?,
             "publication", "version_date", "url", "source"}
        """
        if not doc_id:
            resolved = self.resolve_law(law)
            if "error" in resolved:
                return resolved
            doc_id = resolved["doc_id"]
        key = self._article_key(article)
        if not key:
            return {"error": f"Unrecognized article number '{article}'"}

        picked = {}
        if as_of_date:
            picked = self.version_for_date(doc_id, as_of_date)
            if "error" in picked:
                return picked
            publication = picked["publication"]

        result = {"doc_id": doc_id, "article": key, "publication": publication, "version_date": "",
                  "title": "", "heading": "", "text": "", "url": "", "source": "view_page"}
        if picked:
            result.update(as_of_date=as_of_date, amendment=picked["amendment"],
                          caveat=picked["caveat"])
        html = self.get_document_html(doc_id, publication, lang=lang)
        err = self._html_error(html)
        if not err:
            meta = self.parse_document_meta(html, doc_id)
            active = self._active_version(html)
            result["title"] = meta.title
            result["status"] = meta.status
            result["publication"] = active.get("publication", publication)
            result["version_date"] = active.get("date", "")
            if lang == "en":
                result["lang"] = "en"
                pub = result["publication"] if result["publication"] is not None else 0
                result["pdf_url"] = f"{self.BASE}/en/document/download/{doc_id}/{pub}/en/pdf"
                result["translation_caveat"] = ("Unofficial English translation — the Georgian text is the "
                                    "only authentic one and the translation may lag behind it.")
        elif publication is None and lang != "en":
            # View page blocked — linked/{id}/document returns the whole current
            # text through the API, which bot detection doesn't cover.
            linked = self.get_linked(doc_id, "document")
            html = linked.get("html", "")
            if not html:
                return {**result, "error": f"{err}; linked fallback: {linked.get('error', 'empty')}"}
            result["source"] = "linked_api"
        else:
            return {**result, "error": err}

        matches = [a for a in self._split_articles(html) if a["key"] == key]
        if not matches:
            if lang == "en" and not re.search(r'\bArticle\s+\d', html):
                return {**result, "error": "No English translation of this document on matsne"}
            return {**result, "error": f"Article {key} not found in this version"}
        art = matches[0]
        result.update(heading=art["label"], text=art["text"])
        if len(matches) > 1:
            result["other_matches"] = len(matches) - 1
        anchor = art["anchor"]

        if part is not None:
            result["part"] = part
            result["part_text"] = self._extract_part(result["text"], part)
            if not result["part_text"]:
                result["part_error"] = f"Part {part} not found in article {key}"
        query = f"?publication={result['publication']}" if result["publication"] is not None else ""
        page = f"{self.BASE}/{lang}/document/view/{doc_id}{query}"
        result["url"] = f"{page}#{anchor}" if anchor else page
        return result

    def get_full_text(self, doc_id: int, publication: Optional[int] = None) -> dict:
        """
        Complete plain text of a document (no 500-line cap, unlike get_document).

        View page first (any version); for the current version falls back to
        linked/{id}/document, which bot detection doesn't cover.

        Returns:
            {"doc_id", "publication", "text", "source": "view_page"|"linked_api"}
        """
        html = self.get_document_html(doc_id, publication)
        err = self._html_error(html)
        if not err:
            start = html.find('id="maindoc"')
            body = html[start:] if start >= 0 else html
            script = body.find('<script')
            text = self._fragment_to_text(body[:script] if script > 0 else body)
            return {"doc_id": doc_id, "publication": publication, "text": text, "source": "view_page"}
        if publication is not None:
            return {"doc_id": doc_id, "publication": publication, "error": err}
        linked = self.get_linked(doc_id, "document")
        if not linked.get("html"):
            return {"doc_id": doc_id, "error": f"{err}; linked fallback: {linked.get('error', 'empty')}"}
        return {"doc_id": doc_id, "publication": None,
                "text": self._fragment_to_text(linked["html"]), "source": "linked_api"}

    def compare_versions(self, doc_id: int, from_publication: int,
                         to_publication: Optional[int] = None, article: str = "",
                         max_articles: int = 30) -> dict:
        """
        Diff two consolidated versions, article by article.

        Args:
            from_publication: Older version index
            to_publication: Newer version index (None = current)
            article: Limit the diff to one article
            max_articles: Cap on modified articles returned with diffs

        Returns:
            {"from": {...}, "to": {...}, "added": [keys], "removed": [keys],
             "modified": [{"article", "heading", "diff"}], "modified_count"}
        """
        pages = {}
        for name, pub in (("from", from_publication), ("to", to_publication)):
            html = self.get_document_html(doc_id, pub)
            err = self._html_error(html)
            if err:
                return {"doc_id": doc_id, "error": f"{name} version: {err}"}
            arts = {}
            for a in self._split_articles(html):
                arts.setdefault(a["key"], a)
            pages[name] = (self._active_version(html), arts)
        (from_v, old), (to_v, new) = pages["from"], pages["to"]
        key = self._article_key(article) if article else ""
        keys = [key] if key else sorted(set(old) | set(new),
                                        key=lambda k: [int(x) for x in k.split("^")])
        result = {"doc_id": doc_id, "from": from_v, "to": to_v,
                  "added": [], "removed": [], "modified": [], "modified_count": 0}
        for k in keys:
            if k not in old and k not in new:
                continue
            if k not in old:
                result["added"].append(k)
            elif k not in new:
                result["removed"].append(k)
            elif old[k]["text"] != new[k]["text"]:
                if len(result["modified"]) < max_articles:
                    diff = difflib.unified_diff(old[k]["text"].split('\n'), new[k]["text"].split('\n'),
                                                lineterm="", n=1)
                    result["modified"].append({"article": k, "heading": new[k]["label"],
                                               "diff": '\n'.join(list(diff)[2:])})
                result["modified_count"] += 1
        if key and key not in old and key not in new:
            result["error"] = f"Article {key} not found in either version"
        return result

    def check_status(self, doc_id: int) -> dict:
        """
        "Is this still good law?" for a legislative act: in force or expired,
        latest consolidated version, the most recent amending acts, and how many
        documents cite it.

        Returns:
            {"doc_id", "title", "in_force": bool, "status", "expiry_date", "number",
             "organ", "doc_type", "date_received", "date_published", "versions_count",
             "latest_version", "recent_amendments", "back_references_count", "url"}
        """
        result = {"doc_id": doc_id, "url": f"{self.BASE}/ka/document/view/{doc_id}"}
        html = self.get_document_html(doc_id)
        err = self._html_error(html)
        if err:
            # Fall back to the suggest endpoint's active/expired flag.
            tree = self.get_tree(doc_id)
            parts = tree.get("DocumentPart") if isinstance(tree, dict) else None
            first = parts[0] if isinstance(parts, list) and parts else parts if isinstance(parts, dict) else {}
            title = html_lib.unescape((first or {}).get("Title", "")).strip()
            state = next((s.get("state") for s in self.suggest(title)
                          if str(s.get("id")) == str(doc_id)), "") if title else ""
            return {**result, "title": title, "status": state or "unknown",
                    "in_force": state == "active" if state else None,
                    "warning": f"{err}; status from suggest endpoint only"}
        meta = self.parse_document_meta(html, doc_id)
        versions = self._parse_versions(html, doc_id)
        amendments = self._parse_amendments(html)
        refs = self.get_back_references(doc_id)
        result.update({
            "title": meta.title,
            "in_force": not meta.expiry_date,
            "status": meta.status,
            "expiry_date": meta.expiry_date,
            "number": meta.number,
            "organ": meta.organ,
            "doc_type": meta.doc_type,
            "date_received": meta.date_received,
            "date_published": meta.date_published,
            "versions_count": len(versions),
            "latest_version": versions[-1] if versions else None,
            "recent_amendments": amendments[:5],
            "amendments_count": len(amendments),
            "back_references_count": len(refs) if isinstance(refs, list) else 0,
        })
        return result


# ── CLI Test ───────────────────────────────────────────────────

if __name__ == "__main__":
    api = MatsneAPI()

    print("=" * 60)
    print("🔍 MATSNE.GOV.GE API WRAPPER — FULL TEST")
    print("=" * 60)

    # 1. Quick check — today's dates
    print("\n📅 Recent dates on search page:")
    dates = api.search_quick()
    for d in dates[:5]:
        print(f"  {d}")
    print(f"  Today published: {'✅ YES' if api.has_today() else '❌ NO'}")

    # 2. Search recent documents
    print("\n📄 Recent documents (first page):")
    recent = api.search(page=0)
    if "error" not in recent:
        for doc in recent["documents"][:5]:
            print(f"  {doc.get('date', 'N/A')} | ID:{doc['id']} | {doc['title'][:60]}")
    else:
        print(f"  Error: {recent['error']}")

    # 3. Search with query
    print("\n🔎 Search 'პერსონალური' (with query):")
    results = api.search(page=0, query="პერსონალური", limit=5)
    if "error" not in results:
        for doc in results["documents"][:5]:
            print(f"  {doc.get('date', 'N/A')} | ID:{doc['id']} | {doc['title'][:60]}")
    else:
        print(f"  Error: {results['error']}")

    # 4. Suggest search
    print("\n🔎 Suggest: 'შრომის':")
    suggest_results = api.suggest("შრომის")
    for r in suggest_results[:5]:
        if "error" not in r:
            print(f"  [{r['state']}] ID:{r['id']} | {r['title'][:60]}")

    # 5. Get full document
    print("\n📑 Document 6840140 (full):")
    doc = api.get_document(6840140)
    print(f"  Title: {doc['meta'].get('title', 'N/A')[:60]}")
    print(f"  Number: {doc['meta'].get('number', 'N/A')}")
    print(f"  Organ: {doc['meta'].get('organ', 'N/A')}")
    print(f"  Type: {doc['meta'].get('doc_type', 'N/A')}")
    print(f"  Tree: {'✅' if doc['tree'] else '❌'}")
    print(f"  Back-refs: {len(doc['back_references'])} refs")
    print(f"  Comments: {len(doc['comments'])} comments")
    print(f"  Text: {len(doc['text'])} chars")
    print(f"  Errors: {doc['errors'] or 'None'}")

    # 6. Tree endpoint
    print("\n🌳 Tree for 6840140:")
    tree = api.get_tree(6840140)
    if "error" not in tree:
        print(f"  Title: {tree.get('Title', 'N/A')}")
        print(f"  Anchor: {tree.get('Anchor', 'N/A')}")
        if "DocumentPart" in tree:
            dp = tree["DocumentPart"]
            if isinstance(dp, list):
                print(f"  Parts: {len(dp)}")
            elif isinstance(dp, dict):
                print(f"  Part: {dp.get('Title', 'N/A')}")

    # 7. Linked document
    print("\n🔗 Linked doc 6840140:")
    linked = api.get_linked(6840140, "DOCUMENT:1;HEADER:1;")
    if "error" not in linked:
        html = linked.get("html", "")
        print(f"  HTML length: {len(html)} chars")
        print(f"  Preview: {html[:100]}...")

    # 8. Back references
    print("\n📎 Back references for 6840140:")
    refs = api.get_back_references(6840140)
    print(f"  Count: {len(refs)}")
    if refs:
        for r in refs[:3]:
            print(f"  {r}")

    # 9. Comments
    print("\n💬 Comments for 6840140:")
    comments = api.get_comments(6840140)
    print(f"  Count: {len(comments)}")

    # 10. Today's documents
    print("\n📆 Today's documents:")
    today_docs = api.today_documents()
    if today_docs:
        for d in today_docs:
            print(f"  ID:{d['id']} | {d['title'][:60]}")
    else:
        print("  No documents published today")

    # 11. Versions of the Civil Code
    print("\n🗂️ Civil Code (31702) versions:")
    versions = api.list_versions(31702)
    if "error" not in versions:
        print(f"  Versions: {len(versions['versions'])} | Amending acts: {len(versions['amendments'])}")
        print(f"  Original: {versions['versions'][0]['date']} | Latest: {versions['versions'][-1]['date']}")
    else:
        print(f"  Error: {versions['error']}")

    # 12. Article by citation — current and historical
    print("\n📜 Civil Code art. 361 §2 (current) and art. 1145 on 01/01/2020:")
    art = api.get_article("361", law="სამოქალაქო კოდექსი", part=2)
    print(f"  {art.get('heading', art.get('error'))} → {art.get('part_text', '')[:60]}")
    old = api.get_article("1145", law="civil code", as_of_date="01/01/2020")
    print(f"  v{old.get('publication')} ({old.get('version_date')}): {old.get('text', old.get('error', ''))[:60]}")

    # 13. Diff between two versions
    print("\n🔀 Civil Code diff v138 → v140:")
    diff = api.compare_versions(31702, 138, 140)
    print(f"  Modified: {[m['article'] for m in diff.get('modified', [])]} | "
          f"Added: {diff.get('added')} | Removed: {diff.get('removed')}")

    # 14. Status of an expired act (old Labour Code)
    print("\n⚖️ Status of old Labour Code (26350):")
    status = api.check_status(26350)
    print(f"  in_force={status.get('in_force')} expiry={status.get('expiry_date')}")

    print("\n✅ Full test complete!")
