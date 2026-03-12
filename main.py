from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime, timezone
from typing import Literal, Optional, TypedDict
from pathlib import Path
import os
import json
import base64
import hmac
import hashlib
import uuid
import re
import math

app = FastAPI(title="PDF Generator", version="6.0.1")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

DOCUMENT_STORE = {}


class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str
    template: Literal["document", "rapport"] = "document"


class PdfLinkResponse(BaseModel):
    filename: str
    content_type: str = "application/pdf"
    url: str


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload_b64: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    return _b64url(sig)


def _make_token(payload: dict, secret: str) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = _b64url(payload_json)
    sig_b64 = _sign(payload_b64, secret)
    return f"{payload_b64}.{sig_b64}"


def _verify_token(token: str, secret: str) -> dict:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid token format")

    expected = _sign(payload_b64, secret)

    if not hmac.compare_digest(expected, sig_b64):
        raise HTTPException(status_code=403, detail="Invalid token signature")

    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))

    exp = payload.get("exp")
    if not exp or datetime.now(timezone.utc).timestamp() > float(exp):
        raise HTTPException(status_code=410, detail="Token expired")

    return payload


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _split_paragraphs(text: str) -> list[str]:
    text = _normalize_text(text)
    if not text:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


_LIST_ITEM_RE = re.compile(r"^(\d+[\.\)]|[-•])\s+")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMBERED_HEADING_RE = re.compile(r"^(\d{1,2})(?:[\.\)]|\s*[-–—])\s+(.+?)\s*$")


class _Block(TypedDict):
    kind: Literal["paragraph", "list", "heading"]
    text: str
    level: int
    items: list[str]


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text, flags=re.UNICODE))


def _classify_heading(paragraph: str) -> Optional[tuple[int, str]]:
    """
    Returns (level, text) for headings, otherwise None.
    Level 1 is a chapter heading (new section), >=2 are in-section subheadings.
    """
    p = paragraph.strip()
    if not p:
        return None

    m = _MD_HEADING_RE.match(p)
    if m:
        level = len(m.group(1))
        text = m.group(2).strip()
        return max(1, min(level, 6)), text

    m = _NUMBERED_HEADING_RE.match(p)
    if m and len(p) <= 90:
        num = m.group(1)
        rest = m.group(2).strip()
        return 1, f"{num}. {rest}"

    # Short, "heading-ish" lines (all-caps, or ends with colon)
    single_line = "\n" not in p and len(p) <= 70
    if single_line and p.endswith(":"):
        # Treat as in-section subheading, not a new chapter.
        return 3, p[:-1].strip()

    if single_line:
        letters = re.sub(r"[^A-Za-zÄÖÜäöüß]+", "", p)
        if letters and len(letters) >= 6:
            upper_ratio = sum(1 for c in letters if c.isupper()) / max(1, len(letters))
            if upper_ratio >= 0.9 and not p.endswith("."):
                # Treat as in-section subheading, not a new chapter.
                return 3, p.strip()

    return None


def _extract_list_items(text: str) -> list[str]:
    """
    Extract list items from a paragraph that may contain multiple lines.
    Supports bullets like "- item" or "1. item". Non-matching lines are
    treated as continuations of the previous item (common in wrapped text).
    """
    items: list[str] = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if _classify_heading(ln):
            break
        if _LIST_ITEM_RE.match(ln):
            items.append(_LIST_ITEM_RE.sub("", ln).strip())
        else:
            if items:
                items[-1] = f"{items[-1]} {ln}".strip()
            else:
                items.append(ln)
    return [it for it in (it.strip() for it in items) if it]


def _parse_blocks(paragraphs: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i].strip()
        if not p:
            i += 1
            continue

        h = _classify_heading(p)
        if h:
            level, text = h
            blocks.append({"kind": "heading", "text": text, "level": level, "items": []})
            i += 1
            continue

        if _LIST_ITEM_RE.match(p):
            items: list[str] = []
            while i < len(paragraphs):
                cand = paragraphs[i].strip()
                if not cand:
                    break
                # Prevent numbered chapter headings (e.g. "2. Kapitel ...") from
                # being swallowed as list items when they appear right after a list.
                if _classify_heading(cand):
                    break
                if not _LIST_ITEM_RE.match(cand):
                    break
                items.extend(_extract_list_items(cand))
                i += 1
            blocks.append({"kind": "list", "items": items, "text": "", "level": 0})
            continue

        blocks.append({"kind": "paragraph", "text": p, "level": 0, "items": []})
        i += 1

    return blocks


def _split_into_chapters(req: DocumentRequest, blocks: list[_Block]) -> list[dict]:
    chapters: list[dict] = []

    current_title = req.title.strip() or "Dokument"
    current_blocks: list[_Block] = []

    def flush():
        nonlocal current_title, current_blocks
        if current_blocks:
            chapters.append({"title": current_title, "blocks": current_blocks})
        current_blocks = []

    for b in blocks:
        if b["kind"] == "heading" and b["level"] == 1:
            flush()
            current_title = b["text"].strip() or current_title
            continue
        current_blocks.append(b)

    flush()

    if not chapters and blocks:
        chapters = [{"title": current_title, "blocks": blocks}]

    # Extract a per-chapter subheading (first level-2 heading near the start), so the
    # subtitle under the orange title is not the same on every page. We intentionally
    # ignore later in-section headings to avoid accidentally turning body headings into
    # chapter subtitles.
    for ch in chapters:
        ch_blocks: list[_Block] = ch.get("blocks") or []
        subheading = ""
        new_blocks: list[_Block] = []
        extracted = False
        idx = 0
        for b in ch_blocks:
            if not extracted and b["kind"] == "heading" and b["level"] == 2:
                # Only take early H2 as chapter subtitle.
                if idx <= 5:
                    cand = (b["text"] or "").strip()
                    if 0 < len(cand) <= 220:
                        subheading = cand
                        extracted = True
                        idx += 1
                        continue
            new_blocks.append(b)
            idx += 1
        ch["subheading"] = subheading
        ch["blocks"] = new_blocks

    return chapters


def _block_words(b: _Block) -> int:
    if b["kind"] == "paragraph":
        return _word_count(b["text"])
    if b["kind"] == "heading":
        return 8 + _word_count(b["text"])
    if b["kind"] == "list":
        # list items tend to consume more vertical space than plain text
        return sum(_word_count(it) for it in b["items"]) + 10 * len(b["items"])
    return 0


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _longest_word_len(text: str) -> int:
    words = re.findall(r"[^\s]+", (text or ""), flags=re.UNICODE)
    return max((len(w) for w in words), default=0)


def _estimate_units_for_block(b: _Block, *, chars_per_line: int) -> int:
    """
    Layout-aware block cost estimation (rough 'line units').
    This is intentionally conservative to avoid text overflow in fixed-position boxes.
    """
    cpl = max(30, int(chars_per_line))

    if b["kind"] == "paragraph":
        t = _compact_ws(b["text"])
        if not t:
            return 0
        lines = int(math.ceil(len(t) / cpl))
        wrap_penalty = 1 if _longest_word_len(t) >= 18 else 0
        # +1 accounts for paragraph spacing (margin-bottom)
        return lines + 1 + wrap_penalty

    if b["kind"] == "heading":
        t = _compact_ws(b["text"])
        if not t:
            return 1
        # headings consume extra vertical spacing
        lines = int(math.ceil(len(t) / max(20, int(cpl * 0.85))))
        return lines + 2

    if b["kind"] == "list":
        units = 1  # list block spacing
        for it in b["items"]:
            t = _compact_ws(it)
            if not t:
                continue
            lines = int(math.ceil(len(t) / max(22, int(cpl * 0.9))))
            # +1 per item spacing
            units += lines + 1
        return units

    return 0


def _paginate_units(
    blocks: list[_Block],
    *,
    max_units: int,
    chars_per_line: int,
    merge_small_last: bool = True,
) -> list[list[_Block]]:
    pages: list[list[_Block]] = []
    current: list[_Block] = []
    current_units = 0

    budget = max(8, int(max_units))

    def _append_block(block: _Block):
        nonlocal current, current_units
        u = _estimate_units_for_block(block, chars_per_line=chars_per_line)
        if current and (current_units + u) > budget:
            pages.append(current)
            current = []
            current_units = 0
        current.append(block)
        current_units += u

    def _split_list_block(b: _Block) -> list[_Block]:
        if b["kind"] != "list" or len(b.get("items") or []) <= 1:
            return [b]

        cpl = max(30, int(chars_per_line))
        item_cpl = max(22, int(cpl * 0.9))

        chunks: list[_Block] = []
        cur_items: list[str] = []
        cur_units = 1  # base list spacing

        def item_units(txt: str) -> int:
            t = _compact_ws(txt)
            if not t:
                return 0
            lines = int(math.ceil(len(t) / item_cpl))
            return lines + 1  # per-item spacing

        for it in b["items"]:
            iu = item_units(it)
            # If adding this item would exceed the full-page budget, flush current chunk.
            if cur_items and (cur_units + iu) > max(4, budget):
                chunks.append({"kind": "list", "items": cur_items, "text": "", "level": 0})
                cur_items = []
                cur_units = 1
            cur_items.append(it)
            cur_units += iu

        if cur_items:
            chunks.append({"kind": "list", "items": cur_items, "text": "", "level": 0})

        return chunks or [b]

    for b in blocks:
        if b["kind"] == "list":
            for lb in _split_list_block(b):
                _append_block(lb)
            continue
        _append_block(b)

    if current:
        pages.append(current)

    # Avoid a last page that is almost empty (common source of "rest lines")
    if merge_small_last and len(pages) >= 2:
        last_units = sum(_estimate_units_for_block(b, chars_per_line=chars_per_line) for b in pages[-1])
        if last_units < max(6, int(budget * 0.18)):
            pages[-2].extend(pages[-1])
            pages.pop()

    return pages


def _blocks_to_html(blocks: list[_Block]) -> str:
    out: list[str] = []
    for b in blocks:
        if b["kind"] == "paragraph":
            out.append(f"<p>{b['text']}</p>")
        elif b["kind"] == "heading":
            if b["level"] >= 3:
                out.append(f"<h3>{b['text']}</h3>")
            else:
                out.append(f"<h2>{b['text']}</h2>")
        elif b["kind"] == "list":
            items_html = "".join(f"<li>{it}</li>" for it in b["items"] if it.strip())
            if items_html:
                out.append(f"<ul>{items_html}</ul>")
    return "".join(out)


def _pick_intro_text(chapter_blocks: list[_Block]) -> tuple[str, list[_Block]]:
    """
    Picks an intro paragraph (short lead text) and returns (intro_text, remaining_blocks).
    """
    blocks = list(chapter_blocks)
    for idx, b in enumerate(blocks):
        if b["kind"] == "paragraph" and _word_count(b["text"]) >= 10:
            intro = b["text"].strip()
            remaining = blocks[:idx] + blocks[idx + 1 :]
            return intro, remaining
    for idx, b in enumerate(blocks):
        if b["kind"] == "paragraph":
            intro = b["text"].strip()
            remaining = blocks[:idx] + blocks[idx + 1 :]
            return intro, remaining
    # Fallback: no paragraph, maybe a list
    for b in blocks:
        if b["kind"] == "list" and b["items"]:
            intro = b["items"][0].strip()
            return intro, blocks
    return "", blocks


def _make_sidebar_items(intro_text: str, chapter_blocks: list[_Block]) -> list[dict]:
    # The sidebar has a fixed height. We therefore keep the content compact and
    # aggressively truncate when needed to prevent overflow.
    sidebar_char_budget = int(os.getenv("PDF_TYPEC_SIDEBAR_CHAR_BUDGET", "620"))
    summary_max = int(os.getenv("PDF_TYPEC_SIDEBAR_SUMMARY_MAX", "260"))

    summary = (intro_text or "").strip()
    if len(summary) > summary_max:
        summary = summary[: max(0, summary_max - 1)].rstrip() + "…"

    items: list[dict] = []
    items.append({"title": "Zusammenfassung", "text": summary})

    # Optional key points from first list encountered
    for b in chapter_blocks:
        if b["kind"] == "list" and b["items"]:
            max_pts = int(os.getenv("PDF_TYPEC_SIDEBAR_KEYPOINTS_MAX", "3"))
            pt_max_len = int(os.getenv("PDF_TYPEC_SIDEBAR_KEYPOINT_MAXLEN", "110"))
            pts = []
            for it in (it.strip() for it in b["items"] if it.strip()):
                t = it
                if len(t) > pt_max_len:
                    t = t[: max(0, pt_max_len - 1)].rstrip() + "…"
                pts.append(t)
                if len(pts) >= max_pts:
                    break

            if pts:
                items.append({"title": "Kernpunkte", "text": "<br/>".join(pts)})
            break

    # Final safety pass: shrink/remove keypoints if the sidebar would get too long.
    approx_len = sum(len((it.get("title") or "")) + len((it.get("text") or "")) for it in items)
    if approx_len > sidebar_char_budget and len(items) >= 2:
        # Drop keypoints first (most likely to overflow).
        items = items[:1]
        approx_len = sum(len((it.get("title") or "")) + len((it.get("text") or "")) for it in items)

    if approx_len > sidebar_char_budget:
        # Further truncate summary.
        t = items[0]["text"]
        hard_max = max(80, sidebar_char_budget - 40)
        if len(t) > hard_max:
            items[0]["text"] = t[: hard_max - 1].rstrip() + "…"

    return items


def _paginate(blocks: list[_Block], page_budget_words: int, *, merge_small_last: bool = True) -> list[list[_Block]]:
    pages: list[list[_Block]] = []
    current: list[_Block] = []
    current_words = 0

    for b in blocks:
        w = _block_words(b)
        if current and (current_words + w) > page_budget_words:
            pages.append(current)
            current = [b]
            current_words = w
        else:
            current.append(b)
            current_words += w

    if current:
        pages.append(current)

    # Avoid a last page that is almost empty (common source of "rest lines")
    if merge_small_last and len(pages) >= 2:
        last_words = sum(_block_words(b) for b in pages[-1])
        if last_words < 120:
            pages[-2].extend(pages[-1])
            pages.pop()

    return pages


def _extract_section_label(chapter_title: str) -> str:
    m = re.match(r"^\s*(\d{1,2})\.\s+.+$", chapter_title)
    if m:
        return f"KAPITEL {m.group(1)}"
    return ""


def _title_variant(title: str) -> str:
    """
    Type-C title scaling for long / very long German headings.
    Returns a CSS class name used to pick a smaller font size.
    """
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return "tc-title--lg"
    words = re.split(r"\s+", t)
    longest = max((len(w) for w in words if w), default=0)
    n = len(t)
    if n >= 52 or longest >= 18:
        return "tc-title--xs"
    if n >= 40 or longest >= 16:
        return "tc-title--sm"
    if n >= 28 or longest >= 14:
        return "tc-title--md"
    return "tc-title--lg"


def _subtitle_variant(subtitle: str) -> str:
    """
    Type-C subtitle scaling to avoid clipping for long H2 subtitles.
    Returns a CSS class name (or empty string).
    """
    t = re.sub(r"\s+", " ", (subtitle or "").strip())
    if not t:
        return ""
    n = len(t)
    longest = _longest_word_len(t)
    if n >= 110 or longest >= 24:
        return "tc-subtitle--xs"
    if n >= 90 or longest >= 20:
        return "tc-subtitle--sm"
    if n >= 72 or longest >= 18:
        return "tc-subtitle--md"
    return ""


def _build_type_c_sections(req: DocumentRequest, chapters: list[dict]) -> list[dict]:

    sections = []
    # Backwards compatible: if only *_BUDGET_WORDS is set, derive a conservative unit budget.
    intro_words_budget = int(os.getenv("PDF_TYPEC_INTRO_BUDGET_WORDS", "260"))
    continue_words_budget = int(os.getenv("PDF_TYPEC_CONTINUE_BUDGET_WORDS", "420"))
    intro_units_default = max(20, intro_words_budget // 9)
    continue_units_default = max(28, continue_words_budget // 9)
    intro_units_budget = int(os.getenv("PDF_TYPEC_INTRO_BUDGET_UNITS", str(intro_units_default)))
    continue_units_budget = int(os.getenv("PDF_TYPEC_CONTINUE_BUDGET_UNITS", str(continue_units_default)))
    # Approximate wrap density based on column width (intro is narrow, continue is wide)
    intro_chars_per_line = int(os.getenv("PDF_TYPEC_INTRO_CHARS_PER_LINE", "58"))
    continue_chars_per_line = int(os.getenv("PDF_TYPEC_CONTINUE_CHARS_PER_LINE", "110"))
    page_start = int(os.getenv("PDF_PAGE_START", "1"))
    page = page_start

    for chapter in chapters:
        chapter_title = (chapter.get("title") or "").strip() or (req.title.strip() or "Dokument")
        title_variant = _title_variant(chapter_title)
        blocks: list[_Block] = chapter.get("blocks") or []
        chapter_subheading = (chapter.get("subheading") or "").strip()
        subtitle_variant = _subtitle_variant(chapter_subheading or req.subtitle)
        intro_text, remaining_blocks = _pick_intro_text(blocks)

        # Intro page body is a subset; remainder flows into continue pages
        intro_pages = (
            _paginate_units(
                remaining_blocks,
                max_units=intro_units_budget,
                chars_per_line=intro_chars_per_line,
                merge_small_last=False,
            )
            if remaining_blocks
            else []
        )
        intro_body_blocks = intro_pages[0] if intro_pages else []
        rest_blocks = []
        if intro_pages and len(intro_pages) > 1:
            for pg in intro_pages[1:]:
                rest_blocks.extend(pg)

        sidebar_items = _make_sidebar_items(intro_text, blocks)
        section_label = _extract_section_label(chapter_title)
        # Dynamic overview title + label (previously hardcoded -> always identical).
        overview_title = section_label or "OVERVIEW"
        # Use chapter title without leading numbering as the overview label (icon row).
        overview_label = re.sub(r"^\s*\d{1,2}\s*[\.\)]\s*", "", chapter_title).strip() or chapter_title

        sections.append(
            {
                "layout": "type_c_intro",
                "intro_title": overview_title,
                "intro_label": overview_label,
                "title": chapter_title,
                "title_variant": title_variant,
                "section_label": section_label,
                "intro": intro_text,
                "subheading": chapter_subheading or req.subtitle,
                "subtitle_variant": subtitle_variant,
                "body_left": _blocks_to_html(intro_body_blocks),
                "sidebar_items": sidebar_items,
                "page_number": f"{page:03d}",
            }
        )
        page += 1

        if rest_blocks:
            cont_pages = _paginate_units(
                rest_blocks,
                max_units=continue_units_budget,
                chars_per_line=continue_chars_per_line,
                merge_small_last=True,
            )
            for pg in cont_pages:
                html = _blocks_to_html(pg)
                if not html.strip():
                    continue
                sections.append(
                    {
                        "layout": "type_c_continue",
                        "title": chapter_title,
                        "title_variant": title_variant,
                        "subheading": chapter_subheading or req.subtitle,
                        "subtitle_variant": subtitle_variant,
                        "body_left": html,
                        "sidebar_items": [],
                        "page_number": f"{page:03d}",
                    }
                )
                page += 1

    return sections


def _build_sections(req: DocumentRequest) -> list[dict]:

    paragraphs = _split_paragraphs(req.content)

    if not paragraphs:
        return []

    blocks = _parse_blocks(paragraphs)
    chapters = _split_into_chapters(req, blocks)
    return _build_type_c_sections(req, chapters)


def _render_pdf_bytes(req: DocumentRequest) -> bytes:

    template_name = "master.html"

    try:
        template = env.get_template(template_name)
    except TemplateNotFound:
        raise HTTPException(
            status_code=500,
            detail=f"Template not found: templates/{template_name}",
        )

    sections = _build_sections(req)

    rendered_html = template.render(
        document_title=req.title,
        title=req.title,
        subtitle=req.subtitle,
        brand_name="GPS Group Holding",
        footer_text="Kompetenz und Qualität auf höchstem Niveau",
        sections=sections,
        end_title="Vielen Dank",
        end_text=req.subtitle,
        address="GPS Group Holding",
        telephone="+41 00 000 00 00",
        website="www.gpsgroup.ch",
    )

    # Use BASE_DIR so relative URLs like "assets/..." resolve to /app/assets in Docker.
    # Templates/CSS are referenced explicitly via "templates/...".
    return HTML(string=rendered_html, base_url=str(BASE_DIR)).write_pdf()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    return {"service": "pdf-generator", "ok": True}


@app.post("/generate", response_model=PdfLinkResponse)
def generate(request: Request, body: DocumentRequest):

    secret = os.getenv("PDF_API_KEY")

    if not secret:
        raise HTTPException(status_code=500, detail="PDF_API_KEY not set")

    filename = f"document_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    ttl_minutes = int(os.getenv("PDF_URL_TTL_MINUTES", "60"))
    exp_ts = datetime.now(timezone.utc).timestamp() + ttl_minutes * 60

    doc_id = uuid.uuid4().hex

    pdf_bytes = _render_pdf_bytes(body)

    DOCUMENT_STORE[doc_id] = pdf_bytes

    payload = {
        "id": doc_id,
        "filename": filename,
        "exp": exp_ts,
    }

    token = _make_token(payload, secret)

    base_url = str(request.base_url).rstrip("/")

    url = f"{base_url}/download/{token}"

    return PdfLinkResponse(filename=filename, url=url)


@app.get("/download/{token}")
def download(token: str):

    secret = os.getenv("PDF_API_KEY")

    if not secret:
        raise HTTPException(status_code=500, detail="PDF_API_KEY not set")

    payload = _verify_token(token, secret)

    doc_id = payload.get("id")

    if doc_id not in DOCUMENT_STORE:
        raise HTTPException(status_code=404, detail="Document not found")

    pdf_bytes = DOCUMENT_STORE[doc_id]

    filename = payload.get("filename", "document.pdf")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
