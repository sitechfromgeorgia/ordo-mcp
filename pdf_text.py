#!/usr/bin/env python3
"""
pdf_text.py — read and search the PDFs of Georgia's legal ecosystem
====================================================================
Many key sources exist only as PDFs:

  * supremecourt.ge   — Grand Chamber decisions + the compiled binding "norms" PDF
                        (SupremeCourtAPI.grand_chamber(category) → items[].url)
  * info.parliament.ge — bill files: draft law, explanatory note, committee
                        conclusions, votes (/file/1/BillReviewContent/{id})
  * ecd.court.ge      — decision / court-act PDFs (EcdCourtAPI ... pdf_url)
  * matsne.gov.ge     — English translations (/en/document/download/{id}/{pub}/en/pdf)

Georgian-specific problems handled here:

  * LEGACY FONTS — most pre-2010 court PDFs (≈80% of the Grand Chamber archive)
    are typed in AcadNusx / LitNusx / *Mtavr fonts, which draw Georgian glyphs on
    ASCII codes. Every extractor returns Latin gibberish ("saqarTvelos uzenaesi
    sasamarTlo"). We detect those fonts per text chunk (by font name, plus a
    content heuristic for renamed fonts) and transliterate only those chunks
    with the standard 1:1 keyboard map → "საქართველოს უზენაესი სასამართლო".
    Latin text in ordinary fonts (Roman numerals, English) is left untouched.
  * SCANNED PDFs — parliament's older vote sheets, resolutions and adopted laws
    are page images, sometimes with an invisible OCR layer produced by an
    English-only OCR engine (pure noise like "UrCr6oACg',U $6t'6rl"). Such pages
    are reported as scanned and the noise is dropped. OCR is out of scope (it
    would need Tesseract + the Georgian traineddata, a system binary).

  * HYPHENATION / OVERPRINT — PDFium rejoins words hyphenated at line ends
    ("უფლე-\nბების" → "უფლებების"); headings drawn several times for a fake-bold
    effect ("ABCABCABC") and letter-spaced headings ("გ ა ნ ჩ ი ნ ე ბ ა") are
    collapsed so phrase search works.

Dependency: pypdfium2 (Google PDFium; Apache-2.0/BSD-3, prebuilt wheels for
Windows/macOS/Linux) — imported lazily, so the other wrappers and the MCP
server import fine without it.

Usage:
    from pdf_text import extract_pdf_text, search_pdf
    r = extract_pdf_text("https://www.supremecourt.ge/files/upload-file/pdf/ganmarteba-d.pdf",
                         max_pages=5)
    r["text"], r["pages"], r["legacy_font_fixed"], r["scanned"]
    search_pdf(url, "უძრავი ნივთის შეძენა")
"""

import bisect
import ctypes
import io
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Optional, Union

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

MAX_BYTES = 40 * 1024 * 1024        # refuse PDFs larger than this
TIMEOUT = 60                         # seconds per HTTP attempt
RETRIES = 2                          # extra attempts, exponential backoff
RATE_LIMIT = 0.3                     # seconds between requests to the same host
_CACHE_SIZE = 32                     # PDFs (bytes) kept in-process, LRU
_RESULT_CACHE_SIZE = 64

# ── Legacy Georgian fonts → Unicode ───────────────────────────
# AcadNusx/LitNusx/AcadMtavr/LitMtavr/SakarMtavr… all share the Georgian
# keyboard layout: lowercase letters + 7 shifted letters (T S C Z W R J).
# Verified on 50+ real Supreme Court PDFs (e.g. "saqarTvelos uzenaesi
# sasamarTlos didi palata", "mowinaaRmdege", "Wanturia", "reJis").
# Other capitals are deliberately NOT mapped: in those PDFs they are Roman
# numerals (XIV, III) or Latin abbreviations, not Georgian.
_LEGACY_MAP = {
    "a": "ა", "b": "ბ", "g": "გ", "d": "დ", "e": "ე", "v": "ვ", "z": "ზ",
    "T": "თ", "i": "ი", "k": "კ", "l": "ლ", "m": "მ", "n": "ნ", "o": "ო",
    "p": "პ", "J": "ჟ", "r": "რ", "s": "ს", "t": "ტ", "u": "უ", "f": "ფ",
    "q": "ქ", "R": "ღ", "y": "ყ", "S": "შ", "C": "ჩ", "c": "ც", "Z": "ძ",
    "w": "წ", "W": "ჭ", "x": "ხ", "j": "ჯ", "h": "ჰ",
    # Punctuation glyphs of the same fonts: `…~ are Georgian quotes „…“,
    # "_" is the dash ("sxdomis mdivani _ gulnara" = სხდომის მდივანი – ...).
    "`": "„", "~": "“", "_": "–",
}
_LEGACY_TABLE = str.maketrans(_LEGACY_MAP)

# Font-name families known to use the ASCII→Georgian layout.
_LEGACY_FONT_RE = re.compile(
    r"nusx|mtavr|sakar|kolkhet|venuri|grigol|^geo_|^lit[a-z_]|^acad[a-z_]|^bpg.*(?:ascii|nusx)",
    re.IGNORECASE)

# Content heuristic (for renamed/subsetted legacy fonts): very common
# Georgian words in transliterated form vs. English function words.
_GEO_WORDS = frozenset(
    "da rom an ar ara aris iyo ki mier Tu ar ver unda Tavis misi saqarTvelos "
    "sasamarTlo sasamarTlos muxlis muxli kodeqsis kanonis Sesaxeb palata "
    "saqmis saqme mxare mxaris gadawyvetileba Tanaxmad aseve rogorc xolo "
    "SemTxvevaSi am es is iqna amis Semdeg".split())
_ENG_WORDS = frozenset(
    "the of and to in is that for on with by as are was be this it or an at from "
    "which not shall article law".split())
_INNER_SHIFT_RE = re.compile(r"^[a-z]+[TSCZWRJ][a-z]")   # saqarTvelos, gadaSveba


def legacy_score(text: str) -> float:
    """
    How much `text` looks like Georgian typed in a legacy ASCII font
    (0 = not at all, ~0.3+ = clearly). English prose scores ≤ 0.
    """
    words = re.findall(r"[A-Za-z]+", text)
    if len(words) < 5:
        return 0.0
    geo = sum(1 for w in words if w in _GEO_WORDS or _INNER_SHIFT_RE.match(w))
    eng = sum(1 for w in words if w.lower() in _ENG_WORDS)
    return (geo - 2 * eng) / len(words)


def legacy_to_unicode(text: str) -> str:
    """Transliterate AcadNusx/LitNusx-encoded text to Unicode Georgian (Mkhedruli)."""
    return text.translate(_LEGACY_TABLE)


# ── Download (rate-limited, retried, size-capped, cached) ──────

_lock = threading.Lock()
_last_request: dict = {}
_bytes_cache: "OrderedDict[str, bytes]" = OrderedDict()
_result_cache: "OrderedDict[tuple, dict]" = OrderedDict()


def _wait_rate_limit(host: str):
    with _lock:
        elapsed = time.time() - _last_request.get(host, 0.0)
        if elapsed < RATE_LIMIT:
            time.sleep(RATE_LIMIT - elapsed)
        _last_request[host] = time.time()


def _not_pdf_error(url: str, data: bytes, ctype: str) -> str:
    snippet = re.sub(r"<[^>]+>|\s+", " ", data[:3000].decode("utf-8", "replace"))
    m = re.search(r"<title>(.*?)</title>", data[:3000].decode("utf-8", "replace"), re.S | re.I)
    title = (m.group(1).strip() if m else snippet.strip())[:120]
    msg = f"Not a PDF (Content-Type {ctype or '?'}): {title}"
    if "matsne.gov.ge" in url and "/download/" in url:
        msg += (" — matsne PDF downloads need a real publication number "
                "(/download/{id}/{publication}/...); publication 0 returns 'Access Denied'")
    return msg


def download_pdf(url: str, max_bytes: int = MAX_BYTES) -> Union[bytes, dict]:
    """GET a PDF → bytes, or {"error": ...}. Cached in-process by URL."""
    with _lock:
        if url in _bytes_cache:
            _bytes_cache.move_to_end(url)
            return _bytes_cache[url]
    host = urllib.parse.urlsplit(url).netloc
    headers = {"User-Agent": UA, "Accept": "application/pdf,*/*;q=0.8",
               "Accept-Language": "ka,en;q=0.9"}
    last_err = ""
    for attempt in range(RETRIES + 1):
        _wait_rate_limit(host)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                ctype = resp.headers.get("Content-Type", "")
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > max_bytes:
                    return {"error": f"PDF too large: {int(length)} bytes (limit {max_bytes})"}
                buf = io.BytesIO()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    buf.write(chunk)
                    if buf.tell() > max_bytes:
                        return {"error": f"PDF too large: over {max_bytes} bytes"}
                data = buf.getvalue()
            if b"%PDF-" not in data[:1024]:
                return {"error": _not_pdf_error(url, data, ctype)}
            with _lock:
                _bytes_cache[url] = data
                while len(_bytes_cache) > _CACHE_SIZE:
                    _bytes_cache.popitem(last=False)
            return data
        except Exception as e:                      # network / HTTP error
            last_err = f"{type(e).__name__}: {e}"
            if getattr(e, "code", None) in (401, 403, 404, 410):
                break
            if attempt < RETRIES:
                time.sleep(2 ** attempt)
    return {"error": f"Download failed: {last_err}"}


# ── Extraction (pypdfium2 = Google's PDFium) ──────────────────

_pdfium_lock = threading.Lock()      # PDFium is not thread-safe


def _import_pdfium():
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except ImportError:
        return None, None
    return pdfium, pdfium_c


def _scan_page(page, pdfium_c) -> dict:
    """
    One page → {"chunks": [(text, font, invisible)], "images": int}.
    Chunks are runs of consecutive characters sharing a font and visibility,
    so legacy-font runs can be converted without touching the rest.
    """
    images = sum(1 for _ in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3))
    modes = [pdfium_c.FPDFTextObj_GetTextRenderMode(o.raw)
             for o in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT], max_depth=3)]
    has_invisible = pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE in modes

    textpage = page.get_textpage()
    try:
        n = textpage.count_chars()
        text = textpage.get_text_range(0, n) if n > 0 else ""
        if len(text) != n:                  # surrogates etc. — rebuild char by char
            text = "".join(chr(pdfium_c.FPDFText_GetUnicode(textpage, i) or 0xFFFD)
                           for i in range(n))
        buf = ctypes.create_string_buffer(128)
        flags = ctypes.c_int()
        chunks, cur_key, start = [], None, 0
        for i in range(n):
            size = pdfium_c.FPDFText_GetFontInfo(textpage, i, buf, 128, ctypes.byref(flags))
            font = buf.value.decode("latin-1", "replace") if size else ""
            if len(font) > 7 and font[6] == "+":
                font = font[7:]                # subset prefix "ABCDEE+LitNusx"
            invisible = False
            if has_invisible:
                obj = pdfium_c.FPDFText_GetTextObject(textpage, i)
                invisible = bool(obj) and (pdfium_c.FPDFTextObj_GetTextRenderMode(obj)
                                           == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE)
            if not font and cur_key is not None:
                font, invisible = cur_key     # generated spaces/newlines join the run
            key = (font, invisible)
            if key != cur_key:
                if cur_key is not None and i > start:
                    chunks.append((text[start:i], *cur_key))
                cur_key, start = key, i
        if cur_key is not None and n > start:
            chunks.append((text[start:n], *cur_key))
    finally:
        textpage.close()
    return {"chunks": chunks, "images": images}


def _good_ocr_layer(text: str) -> bool:
    """An invisible OCR layer is worth keeping if it is real Georgian or English."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    if sum("ა" <= c <= "ჿ" for c in letters) / len(letters) > 0.5:
        return True
    words = re.findall(r"[A-Za-z]+", text)
    return bool(words) and sum(w.lower() in _ENG_WORDS for w in words) / len(words) > 0.08


_SPACED_RE = re.compile("(?<![ა-ჿ])[ა-ჿ](?: [ა-ჿ]){3,}(?![ა-ჿ])")
_CTRL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _dedupe_line(line: str) -> str:
    """'ABC ABC ABC' → 'ABC': fake-bold/shadow headings drawn several times over."""
    s = line.strip()
    if len(s) < 12:
        return line
    for probe in (s, s + " "):
        n = len(probe)
        p = (probe + probe).find(probe, 1)                 # smallest period
        if 6 <= p < n and n % p == 0:
            return probe[:p].strip()
    return line


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # PDFium marks a line-end hyphenation as \x02 / U+FFFE and joins the word.
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[ \t\f\v]+\n", "\n", text)             # trailing spaces
    text = "\n".join(_dedupe_line(ln) for ln in text.split("\n"))
    # Letter-spaced Georgian headings "გ ა დ ა წ ყ ვ ე ტ ი ლ ე ბ ა" → "გადაწყვეტილება"
    # (Georgian has no one-letter words, so 4+ spaced letters are one word).
    text = _SPACED_RE.sub(lambda m: m.group(0).replace(" ", ""), text)
    text = re.sub(r"\n[ \t]*(\n[ \t]*)+\n", "\n\n", text)  # at most one blank line
    return text.strip()


def _extract(data: bytes, max_pages: Optional[int]) -> dict:
    pdfium, pdfium_c = _import_pdfium()
    if pdfium is None:
        return {"error": "PDF support needs the 'pypdfium2' package: "
                         "pip install pypdfium2  (or: uv sync)"}
    with _pdfium_lock:
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as e:
            msg = str(e)
            if "password" in msg.lower():
                return {"error": f"Encrypted PDF (password required): {msg}"}
            return {"error": f"Unreadable PDF: {type(e).__name__}: {msg}"}
        try:
            total = len(pdf)
            n = total if not max_pages or max_pages <= 0 else min(max_pages, total)
            scans = []
            for i in range(n):
                page = pdf[i]
                try:
                    scans.append(_scan_page(page, pdfium_c))
                except Exception as e:
                    scans.append({"chunks": [], "images": 0, "error": f"{type(e).__name__}: {e}"})
                finally:
                    page.close()
        finally:
            pdf.close()

    # Which fonts are legacy? By name, or (renamed fonts) by what they say.
    per_font: dict = {}
    for s in scans:
        for text, font, invisible in s["chunks"]:
            if not invisible:
                per_font.setdefault(font, []).append(text)
    legacy_fonts = set()
    for font, texts in per_font.items():
        if font and _LEGACY_FONT_RE.search(font):
            legacy_fonts.add(font)
        else:
            joined = " ".join(texts)
            if len(joined) >= 200 and legacy_score(joined) >= 0.25:
                legacy_fonts.add(font)

    page_texts, scanned_pages, ocr_dropped, ocr_kept, page_errors = [], [], [], [], []
    converted_chars = 0
    for i, s in enumerate(scans):
        visible, ocr = [], []
        for text, font, invisible in s["chunks"]:
            if font in legacy_fonts and not invisible:
                converted_chars += sum(c in _LEGACY_MAP for c in text)
                text = legacy_to_unicode(text)
            (ocr if invisible else visible).append(text)
        vis_text, ocr_text = "".join(visible), "".join(ocr)
        if s.get("error"):
            page_errors.append({"page": i + 1, "error": s["error"]})
        if s["images"] and len(vis_text.strip()) < 30:
            scanned_pages.append(i + 1)
            if ocr_text.strip():
                if _good_ocr_layer(ocr_text):
                    ocr_kept.append(i + 1)
                    vis_text += "\n" + ocr_text
                else:
                    ocr_dropped.append(i + 1)
        else:
            vis_text += ocr_text
        page_texts.append(_clean(vis_text))

    text_pages = sum(1 for t in page_texts if t)
    result = {
        "text": "\n\n".join(page_texts),
        "pages": total,
        "pages_extracted": n,
        "method": "pdfium" + ("+legacy-font-map" if converted_chars else ""),
        "legacy_font_fixed": bool(converted_chars),
        "scanned": bool(scanned_pages) and len(scanned_pages) >= max(1, n - text_pages),
        "page_texts": page_texts,
    }
    if converted_chars:
        result["legacy_fonts"] = sorted(f or "(unnamed)" for f in legacy_fonts)
    if scanned_pages:
        result["scanned_pages"] = scanned_pages
    notes = []
    if n < total:
        notes.append(f"Only the first {n} of {total} pages were extracted (max_pages).")
    if ocr_dropped:
        result["ocr_layer_discarded_pages"] = ocr_dropped
        notes.append("Scanned page(s) "
                     f"{ocr_dropped} carry an invisible OCR layer that is noise "
                     "(OCR run without Georgian support) — discarded. No OCR is "
                     "performed here; read the PDF visually or OCR it with "
                     "Tesseract 'kat' traineddata.")
    elif scanned_pages and not ocr_kept:
        notes.append(f"Scanned page(s) {scanned_pages} are images with no text layer; "
                     "OCR is not available in this tool.")
    if ocr_kept:
        notes.append(f"Text of page(s) {ocr_kept} comes from the PDF's own OCR layer "
                     "and may contain recognition errors.")
    if converted_chars:
        notes.append("Converted from legacy Georgian fonts "
                     f"({', '.join(result['legacy_fonts'])}) to Unicode.")
    if page_errors:
        result["page_errors"] = page_errors
    if notes:
        result["note"] = " ".join(notes)
    if not result["text"].strip() and not scanned_pages:
        result["error"] = "No extractable text in this PDF."
    return result


def extract_pdf_text(url_or_bytes: Union[str, bytes], max_pages: Optional[int] = None,
                     include_pages: bool = False) -> dict:
    """
    Download (if a URL) and extract a PDF's text, fixing legacy Georgian fonts.

    Args:
        url_or_bytes: http(s) URL of the PDF, or the raw PDF bytes
        max_pages:    only extract the first N pages (None/0 = all)
        include_pages: also return "page_texts" (list, one string per page)

    Returns:
        {"text", "pages" (total), "pages_extracted", "method",
         "legacy_font_fixed": bool, "legacy_fonts"?: [...],
         "scanned": bool, "scanned_pages"?: [...], "ocr_layer_discarded_pages"?,
         "note"?, "url"?, "size_bytes", "chars"}  or  {"error", ...}
        Pages in "text" are separated by a blank line.
    """
    url = url_or_bytes if isinstance(url_or_bytes, str) else None
    if url is not None:
        if not re.match(r"https?://", url, re.I):
            return {"error": "Expected an http(s) URL or PDF bytes", "url": url}
        key = (url, max_pages or 0)
        with _lock:
            if key in _result_cache:
                _result_cache.move_to_end(key)
                cached = dict(_result_cache[key])
                if not include_pages:
                    cached.pop("page_texts", None)
                return cached
        data = download_pdf(url)
        if isinstance(data, dict):
            return {**data, "url": url}
    else:
        data = bytes(url_or_bytes)
        if b"%PDF-" not in data[:1024]:
            return {"error": "Not a PDF (missing %PDF- header)"}

    result = _extract(data, max_pages)
    result["size_bytes"] = len(data)
    result["chars"] = len(result.get("text", ""))
    if url is not None:
        result["url"] = url
        if "error" not in result:
            with _lock:
                _result_cache[(url, max_pages or 0)] = result
                while len(_result_cache) > _RESULT_CACHE_SIZE:
                    _result_cache.popitem(last=False)
    out = dict(result)
    if not include_pages:
        out.pop("page_texts", None)
    return out


# ── Search ────────────────────────────────────────────────────

def search_pdf(url_or_bytes: Union[str, bytes], query: str, max_results: int = 20,
               context: int = 200, fuzzy: bool = True,
               max_pages: Optional[int] = None) -> dict:
    """
    Find `query` in a PDF and return matching passages with page numbers.

    Matching is case-insensitive and tolerant of whitespace / line breaks and
    quote/dash styles (research._normalize_with_map), so a phrase broken across
    lines or pages is still found. If there is no exact hit and fuzzy=True,
    falls back to research.verify_quote's fuzzy matcher (min 10 chars) and
    returns the closest passage with its similarity.

    Returns:
        {"query", "total" (exact occurrences), "matches": [{"page", "text", "context"}],
         "fuzzy_match"?: {"page", "verdict", "similarity", "matched_text", "context"},
         "pages", "legacy_font_fixed", "scanned", "note"?, "url"?}  or  {"error"}
    """
    from research import _normalize_with_map, verify_quote   # lazy: stdlib-only module

    doc = extract_pdf_text(url_or_bytes, max_pages=max_pages, include_pages=True)
    if "error" in doc:
        return doc
    page_texts = doc.pop("page_texts")
    out = {"query": query, "total": 0, "matches": [],
           **{k: doc[k] for k in ("pages", "pages_extracted", "legacy_font_fixed",
                                   "scanned", "note", "url") if k in doc}}
    q = (query or "").strip()
    if not q:
        return {**out, "error": "Empty query"}
    if not any(page_texts):
        return {**out, "error": "PDF has no searchable text (scanned/image-only)."}

    # One string for the whole document + page start offsets.
    sep = "\n\n"
    starts, pos = [], 0
    for t in page_texts:
        starts.append(pos)
        pos += len(t) + len(sep)
    text = sep.join(page_texts)

    def page_of(i: int) -> int:
        return bisect.bisect_right(starts, i)                 # 1-based

    nt, tmap = _normalize_with_map(text)
    nq = _normalize_with_map(q)[0].strip()
    i = nt.find(nq)
    while i >= 0:
        out["total"] += 1
        if len(out["matches"]) < max_results:
            a, b = tmap[i], tmap[i + len(nq) - 1] + 1
            out["matches"].append({
                "page": page_of(a),
                "text": text[a:b],
                "context": re.sub(r"\s+", " ", text[max(0, a - context):b + context]).strip(),
            })
        i = nt.find(nq, i + max(1, len(nq)))

    if not out["total"] and fuzzy and len(nq) >= 10:
        v = verify_quote(text, q, context=context)
        if v.get("verdict") in ("close", "partial"):
            a = text.find(v["matched_text"])
            out["fuzzy_match"] = {
                "page": page_of(a) if a >= 0 else None,
                "verdict": v["verdict"],
                "similarity": v.get("similarity"),
                "matched_text": v["matched_text"],
                "context": re.sub(r"\s+", " ", v.get("context", "")).strip(),
            }
    return out


# ── CLI Test ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 60)
    print("PDF TEXT — LIVE TEST")
    print("=" * 60)

    from court_api import EcdCourtAPI, SupremeCourtAPI
    from parliament_api import ParliamentAPI

    samples = []
    sc = SupremeCourtAPI()
    for cat in ("civil", "criminal", "admin", "norms"):
        items = sc.grand_chamber(cat).get("items", [])
        if items:
            samples.append((f"Grand Chamber {cat} (newest)", items[0]["url"]))
        if cat in ("admin",) and len(items) > 1:
            samples.append((f"Grand Chamber {cat} (oldest)", items[-1]["url"]))

    bill = ParliamentAPI().get_bill(8235)
    files = [f for st in bill.get("stages", []) for f in st.get("files", [])
             if f.get("format") == "pdf"]
    for f in files[:2] + files[-1:]:
        samples.append((f"Parliament: {f['type']}", f["url"]))

    dec = EcdCourtAPI().search_decisions(texts=["ალიმენტი"], take=1)
    if dec.get("items"):
        samples.append(("ecd decision", dec["items"][0]["pdf_url"]))
    samples.append(("matsne English (Civil Code)",
                    "https://matsne.gov.ge/en/document/download/31702/75/en/pdf"))

    for label, url in samples:
        t0 = time.time()
        r = extract_pdf_text(url, max_pages=40)
        print(f"\n── {label}\n   {url}")
        if "error" in r:
            print(f"   ERROR: {r['error']}")
            continue
        print(f"   pages={r['pages']} extracted={r['pages_extracted']} method={r['method']} "
              f"legacy_fixed={r['legacy_font_fixed']} scanned={r['scanned']} "
              f"chars={r['chars']} ({time.time() - t0:.1f}s)")
        if r.get("note"):
            print(f"   note: {r['note']}")
        print("   " + re.sub(r"\s+", " ", r["text"][:300]))

    print("\n── search_pdf: Grand Chamber norms compilation")
    norms = sc.grand_chamber("norms")["items"][0]["url"]
    s = search_pdf(norms, "საავტორო უფლება", max_results=3)
    print(f"   total={s.get('total')} pages={s.get('pages')}")
    for m in s.get("matches", []):
        print(f"   p.{m['page']}: …{m['context'][:160]}…")
    s = search_pdf(norms, "დარღვეული საავტორო უფლებების დაცვა, საავტორო გასამრჯელოს მიცემა",
                   max_results=1)
    print(f"   phrase: total={s.get('total')} "
          f"page={s['matches'][0]['page'] if s.get('matches') else None}")
    s = search_pdf(norms, "დარღვეული საავტორო უფლების დაცვა და საავტორო გასამრჯელოს გადახდა")
    print(f"   fuzzy: {s.get('fuzzy_match', {}).get('verdict')} "
          f"sim={s.get('fuzzy_match', {}).get('similarity')} "
          f"page={s.get('fuzzy_match', {}).get('page')}")
    t0 = time.time()
    extract_pdf_text(norms)
    print(f"   cached re-extract: {time.time() - t0:.2f}s")
