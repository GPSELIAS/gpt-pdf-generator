from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
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


class RapportDiscussedTopicRow(BaseModel):
    thema: str = ""
    beschreibung: str = ""
    ergebnis: str = ""


class RapportOpenTaskRow(BaseModel):
    aufgabe: str = ""
    verantwortlich: str = ""
    status: str = ""
    bewertung: str = ""


class RapportNewTaskRow(BaseModel):
    neue_aufgabe: str = ""
    verantwortlich: str = ""
    prioritaet: str = ""
    faellig_bis: str = ""


class RapportDecisionRow(BaseModel):
    entscheidung: str = ""
    hintergrund: str = ""
    verantwortlich: str = ""


class RapportData(BaseModel):
    datum: str = ""
    meeting_titel: str = ""
    moderator: str = ""
    teilnehmer: list[str] = Field(default_factory=list)
    besprochene_themen: list[RapportDiscussedTopicRow] = Field(default_factory=list)
    aufgabenbeurteilung: list[RapportOpenTaskRow] = Field(default_factory=list)
    neue_aufgaben: list[RapportNewTaskRow] = Field(default_factory=list)
    wichtige_entscheidungen: list[RapportDecisionRow] = Field(default_factory=list)
    fazit: str = ""


class DocumentRequest(BaseModel):
    title: str
    subtitle: str = ""
    content: str = ""
    template: Literal["document", "rapport"] = "document"
    rapport: Optional[RapportData] = None


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


def _split_text_chunks(text: str, *, max_chars: int) -> list[str]:
    """
    Split very long text into smaller chunks so pagination can stay robust.
    Tries to cut at sentence boundaries, otherwise falls back to whitespace.
    """
    t = _compact_ws(text)
    if not t:
        return []
    if len(t) <= max_chars:
        return [t]

    chunks: list[str] = []
    s = t
    while s:
        if len(s) <= max_chars:
            chunks.append(s.strip())
            break

        cut = max_chars
        window = s[: max_chars + 1]

        # Prefer a sentence boundary close to the end of the window.
        # Reverse-search: ". " / "! " / "? " before an uppercase/number start.
        m = re.search(r"[\.\!\?]\s+(?=[A-ZÄÖÜ0-9])", window[::-1])
        if m:
            idx_from_end = m.start()
            cut = max(140, max_chars - idx_from_end - 1)
        else:
            ws = window.rfind(" ")
            if ws >= 140:
                cut = ws

        chunk = s[:cut].strip()
        if chunk:
            chunks.append(chunk)
        s = s[cut:].strip()

    return [c for c in chunks if c]


def _split_paragraphs(text: str) -> list[str]:
    text = _normalize_text(text)
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    def expand_inline_list(p: str) -> list[str]:
        """
        Turn inline lists like:
          'Zwecke: - A - B - C'
        into separate paragraphs so we can render a real list and paginate correctly.
        """
        s = _compact_ws(p)
        if not s:
            return []
        if not re.search(r":\s*[-•]\s+", s):
            return [p.strip()]

        prefix, rest = s.split(":", 1)
        prefix = prefix.strip()
        rest = rest.strip()
        if len(prefix) < 4:
            return [p.strip()]

        # Remove leading marker and split on " - " / " • " occurrences.
        rest = re.sub(r"^\s*[-•]\s+", "", rest)
        items = [it.strip() for it in re.split(r"\s+[-•]\s+", rest) if it.strip()]

        # Only treat as list if it looks like a real multi-item list.
        if len(items) < 2:
            return [p.strip()]

        out = [f"{prefix}:"]
        out.extend([f"- {it}" for it in items])
        return out

    expanded: list[str] = []
    for p in paragraphs:
        expanded.extend(expand_inline_list(p))

    # Hardening: ensure single giant paragraphs don't break pagination.
    # We only split paragraphs that are not obvious list markers / headings.
    hardened: list[str] = []
    for p in expanded:
        s = _compact_ws(p)
        if not s:
            continue
        if _MD_HEADING_RE.match(s) or _NUMBERED_HEADING_RE.match(s) or _LIST_ITEM_RE.match(s):
            hardened.append(p.strip())
            continue
        if len(s) > 900:
            hardened.extend(_split_text_chunks(s, max_chars=420))
        else:
            hardened.append(p.strip())

    return hardened


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
        # Keep as normal paragraph (common in German: "Ziele:" then list).
        # It must NOT create a new chapter.
        return None

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
    last_top_level_num = 0

    def flush():
        nonlocal current_title, current_blocks
        if current_blocks:
            chapters.append({"title": current_title, "blocks": current_blocks})
        current_blocks = []

    for b in blocks:
        if b["kind"] == "heading" and b["level"] == 1:
            # Prevent sub-section numbering from creating new chapters.
            # Real documents often restart numbering inside a chapter (e.g. Chapter 3 contains "1. Holding-Ebene").
            # Top-level chapters usually increase monotonically (1,2,3,...).
            m = re.match(r"^\s*(\d{1,2})\.\s+.+$", (b.get("text") or "").strip())
            if m:
                num = int(m.group(1))
                if last_top_level_num and num < last_top_level_num:
                    current_blocks.append({"kind": "heading", "text": b["text"], "level": 2, "items": []})
                    continue
                last_top_level_num = max(last_top_level_num, num)

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
                # Avoid leaving a list lead-in (ending with ":") at the bottom of a page.
                if current and current[-1]["kind"] == "paragraph":
                    t = (current[-1].get("text") or "").rstrip()
                    if t.endswith(":"):
                        lead = current.pop()
                        current_units -= _estimate_units_for_block(lead, chars_per_line=chars_per_line)
                        # Start a new page with the lead-in + list chunk
                        if current:
                            pages.append(current)
                        current = [lead]
                        current_units = _estimate_units_for_block(lead, chars_per_line=chars_per_line)
                _append_block(lb)
            continue
        _append_block(b)

    if current:
        pages.append(current)

    # Rebalance to avoid "almost empty" pages anywhere, not just at the end.
    # We do NOT split paragraphs; we only pull whole blocks forward from the next page.
    min_fill_units = int(os.getenv("PDF_TYPEC_MIN_FILL_UNITS", "7"))
    if len(pages) >= 2 and min_fill_units > 0:
        budget = max(8, int(max_units))
        i = 0
        while i < (len(pages) - 1):
            cur_units = sum(_estimate_units_for_block(b, chars_per_line=chars_per_line) for b in pages[i])
            if cur_units >= min_fill_units or not pages[i + 1]:
                i += 1
                continue

            # Try to pull the first block of the next page into the current page.
            nxt0 = pages[i + 1][0]
            nxt0_units = _estimate_units_for_block(nxt0, chars_per_line=chars_per_line)
            if (cur_units + nxt0_units) <= budget:
                pages[i].append(pages[i + 1].pop(0))
                # If the next page becomes empty, remove it.
                if not pages[i + 1]:
                    pages.pop(i + 1)
                continue

            i += 1

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


def _strip_leading_number(title: str) -> str:
    return re.sub(r"^\s*\d{1,2}\s*[\.\)]\s+", "", (title or "").strip()).strip()


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
    footer_text = "Kompetenz und Qualität auf höchstem Niveau"
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
    auto_number_chapters = os.getenv("PDF_AUTO_NUMBER_CHAPTERS", "0").strip().lower() in {"1", "true", "yes", "on"}
    page_start = int(os.getenv("PDF_PAGE_START", "1"))
    page = page_start

    _MM_TO_CSS_PX = 96.0 / 25.4
    _INTRO_BODY_MAX_PX = 128.0 * _MM_TO_CSS_PX
    _CONT_BODY_MAX_PX = 236.0 * _MM_TO_CSS_PX
    _SIDEBAR_MAX_PX = 155.71 * _MM_TO_CSS_PX

    measure_tpl = env.from_string(
        """
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>measure</title>
    <link rel="stylesheet" href="templates/styles.css" />
    <style>
      /* Measurement-only: let boxes expand to natural height. */
      .tc-body-left--intro,
      .tc-body-left--continue,
      .tc-chapter--intro,
      .tc-chapter--continue {
        max-height: none !important;
        overflow: visible !important;
      }
      .tc-sidebar--intro,
      .tc-sidebar__inner {
        height: auto !important;
        overflow: visible !important;
      }
    </style>
  </head>
  <body>
    <!-- Avoid an initial blank page from .page--content break-before rules -->
    <section class="page page--cover"></section>
    {% include partial_name %}
  </body>
</html>
""".strip()
    )

    def _iter_boxes(box):
        yield box
        for ch in getattr(box, "children", []) or []:
            yield from _iter_boxes(ch)

    def _box_bottom(box) -> float:
        return float(getattr(box, "position_y", 0.0)) + float(getattr(box, "height", 0.0))

    def _find_box_by_class(root, class_name: str):
        for b in _iter_boxes(root):
            el = getattr(b, "element", None)
            if el is None:
                continue
            try:
                cls = (el.get("class") or "").split()
            except Exception:
                continue
            if class_name in cls:
                return b
        return None

    def _page_overflows(
        *,
        partial_name: str,
        section: dict,
        body_class: str,
        sidebar_inner_class: str | None,
        body_max_px: float,
        sidebar_max_px: float | None,
    ) -> bool:
        rendered = measure_tpl.render(partial_name=partial_name, section=section, footer_text=footer_text)
        doc = HTML(string=rendered, base_url=str(BASE_DIR)).render()
        if not getattr(doc, "pages", None):
            return False
        last = doc.pages[-1]
        root = getattr(last, "_page_box", None)
        if root is None:
            return False

        eps = 2.0  # CSS px-ish tolerance
        body_box = _find_box_by_class(root, body_class)
        if body_box is not None:
            if float(getattr(body_box, "height", 0.0)) > (float(body_max_px) + eps):
                return True

        if sidebar_inner_class and sidebar_max_px:
            sb = _find_box_by_class(root, sidebar_inner_class)
            if sb is not None:
                if float(getattr(sb, "height", 0.0)) > (float(sidebar_max_px) + eps):
                    return True

        return False

    def _pre_split_blocks(blocks: list[_Block]) -> list[_Block]:
        """
        Ensure no single block is so large it can't fit on a page.
        We do not split headings (they should fit via title scaling).
        """
        out: list[_Block] = []
        for b in blocks:
            if b["kind"] == "paragraph":
                t = _compact_ws(b.get("text") or "")
                if len(t) > 520:
                    for chunk in _split_text_chunks(t, max_chars=420):
                        out.append({"kind": "paragraph", "text": chunk, "level": 0, "items": []})
                else:
                    out.append(b)
                continue
            if b["kind"] == "list":
                items = [it for it in (b.get("items") or []) if (it or "").strip()]
                if len(items) > 10:
                    step = 8
                    for i0 in range(0, len(items), step):
                        out.append({"kind": "list", "items": items[i0 : i0 + step], "text": "", "level": 0})
                else:
                    out.append({"kind": "list", "items": items, "text": "", "level": 0})
                continue
            out.append(b)
        return out

    def _fit_blocks_for_page(
        *,
        partial_name: str,
        body_class: str,
        sidebar_inner_class: str | None,
        base_section: dict,
        blocks: list[_Block],
        min_take: int = 0,
    ) -> int:
        """
        Return the max number of blocks (prefix) that fit in the page containers.
        """
        blocks = _pre_split_blocks(blocks)
        if not blocks:
            return 0

        lo = max(0, int(min_take))
        hi = len(blocks)

        while lo < hi:
            mid = (lo + hi + 1) // 2
            cand_blocks = blocks[:mid]
            cand_section = {**base_section, "body_left": _blocks_to_html(cand_blocks)}
            if _page_overflows(
                partial_name=partial_name,
                section=cand_section,
                body_class=body_class,
                sidebar_inner_class=sidebar_inner_class,
                body_max_px=(
                    _INTRO_BODY_MAX_PX
                    if body_class == "tc-body-left--intro"
                    else _CONT_BODY_MAX_PX
                    if body_class == "tc-body-left--continue"
                    else _CONT_BODY_MAX_PX
                ),
                sidebar_max_px=_SIDEBAR_MAX_PX if sidebar_inner_class else None,
            ):
                hi = mid - 1
            else:
                lo = mid

        return lo

    for chapter_no, chapter in enumerate(chapters, start=1):
        raw_title = (chapter.get("title") or "").strip() or (req.title.strip() or "Dokument")
        base_title = _strip_leading_number(raw_title) or raw_title
        chapter_title = f"{chapter_no}. {base_title}" if auto_number_chapters else raw_title
        title_variant = _title_variant(chapter_title)
        blocks: list[_Block] = _pre_split_blocks(chapter.get("blocks") or [])
        chapter_subheading = (chapter.get("subheading") or "").strip()
        subtitle_variant = _subtitle_variant(chapter_subheading or req.subtitle)
        intro_text, remaining_blocks = _pick_intro_text(blocks)
        remaining_blocks = _pre_split_blocks(remaining_blocks)

        section_label = f"KAPITEL {chapter_no}" if auto_number_chapters else _extract_section_label(chapter_title)
        overview_title = section_label or "OVERVIEW"
        overview_label = base_title or chapter_title

        base_intro_section = {
            "layout": "type_c_intro",
            "intro_title": overview_title,
            "intro_label": overview_label,
            "title": chapter_title,
            "title_variant": title_variant,
            "section_label": section_label,
            "intro": intro_text,
            "subheading": chapter_subheading or req.subtitle,
            "subtitle_variant": subtitle_variant,
            "sidebar_items": [],
            "page_number": f"{page:03d}",
        }

        base_continue_section = {
            "layout": "type_c_continue",
            "title": chapter_title,
            "title_variant": title_variant,
            "subheading": chapter_subheading or req.subtitle,
            "subtitle_variant": subtitle_variant,
            "sidebar_items": [],
            "page_number": f"{page:03d}",
        }

        # Sidebar content is computed from the full chapter (intro + remaining blocks)
        sidebar_items = _make_sidebar_items(intro_text, blocks)
        base_intro_section["sidebar_items"] = sidebar_items

        # Fit intro body blocks using real layout measurement (prevents clipping).
        take_intro = 0
        if remaining_blocks:
            take_intro = _fit_blocks_for_page(
                partial_name="partials/page_type_c_intro.html",
                body_class="tc-body-left--intro",
                sidebar_inner_class="tc-sidebar__inner" if sidebar_items else None,
                base_section=base_intro_section,
                blocks=remaining_blocks,
            )
        intro_body_blocks = remaining_blocks[:take_intro] if take_intro else []
        rest_blocks = remaining_blocks[take_intro:] if take_intro else list(remaining_blocks)

        sections.append(
            {
                **base_intro_section,
                "body_left": _blocks_to_html(intro_body_blocks),
            }
        )
        page += 1

        # Continue pages: greedily fit as many blocks as possible per page (measured).
        guard = 0
        rest_blocks = _pre_split_blocks(rest_blocks)
        while rest_blocks:
            guard += 1
            if guard > 5000:
                # Safety against infinite loops on unexpected WeasyPrint behavior.
                break

            base_continue_section["page_number"] = f"{page:03d}"

            take = _fit_blocks_for_page(
                partial_name="partials/page_type_c_continue.html",
                body_class="tc-body-left--continue",
                sidebar_inner_class=None,
                base_section=base_continue_section,
                blocks=rest_blocks,
                min_take=1,
            )

            if take <= 0:
                # Last resort: forcibly split the first block even further.
                b0 = rest_blocks[0]
                if b0["kind"] == "paragraph":
                    chunks = _split_text_chunks(b0.get("text") or "", max_chars=220)
                    if len(chunks) >= 2:
                        rest_blocks = (
                            [{"kind": "paragraph", "text": chunks[0], "level": 0, "items": []}]
                            + [{"kind": "paragraph", "text": c, "level": 0, "items": []} for c in chunks[1:]]
                            + rest_blocks[1:]
                        )
                        continue
                if b0["kind"] == "list":
                    items = list(b0.get("items") or [])
                    if len(items) >= 2:
                        rest_blocks = (
                            [{"kind": "list", "items": items[:1], "text": "", "level": 0}]
                            + [{"kind": "list", "items": items[1:2], "text": "", "level": 0}]
                            + [{"kind": "list", "items": items[2:], "text": "", "level": 0}]
                            + rest_blocks[1:]
                        )
                        continue

                # If we still can't split, emit one block to avoid stalling.
                take = 1

            pg_blocks = rest_blocks[:take]
            rest_blocks = rest_blocks[take:]

            html = _blocks_to_html(pg_blocks)
            if html.strip():
                sections.append({**base_continue_section, "body_left": html, "sidebar_items": []})
                page += 1

    return sections


def _build_sections(req: DocumentRequest) -> list[dict]:
    if req.template == "rapport":
        return _build_rapport_sections(req)

    paragraphs = _split_paragraphs(req.content)

    if not paragraphs:
        return []

    blocks = _parse_blocks(paragraphs)
    chapters = _split_into_chapters(req, blocks)
    return _build_type_c_sections(req, chapters)


def _build_rapport_sections(req: DocumentRequest) -> list[dict]:
    r = req.rapport or RapportData()
    page_start = int(os.getenv("PDF_PAGE_START", "1"))
    page = page_start

    def _to_dict(model: BaseModel) -> dict:
        # Support both Pydantic v1 and v2
        if hasattr(model, "model_dump"):
            return model.model_dump()  # type: ignore[attr-defined]
        return model.dict()  # type: ignore[no-any-return]

    def _placeholders_discussed() -> list[dict]:
        return [
            {"thema": "—", "beschreibung": "—", "ergebnis": "—"},
            {"thema": "—", "beschreibung": "—", "ergebnis": "—"},
            {"thema": "—", "beschreibung": "—", "ergebnis": "—"},
            {"thema": "—", "beschreibung": "—", "ergebnis": "—"},
        ]

    def _placeholders_open_tasks() -> list[dict]:
        return [
            {"aufgabe": "—", "verantwortlich": "—", "status": "—", "bewertung": "—"},
            {"aufgabe": "—", "verantwortlich": "—", "status": "—", "bewertung": "—"},
            {"aufgabe": "—", "verantwortlich": "—", "status": "—", "bewertung": "—"},
            {"aufgabe": "—", "verantwortlich": "—", "status": "—", "bewertung": "—"},
        ]

    def _placeholders_new_tasks() -> list[dict]:
        return [
            {"neue_aufgabe": "—", "verantwortlich": "—", "prioritaet": "—", "faellig_bis": "—"},
            {"neue_aufgabe": "—", "verantwortlich": "—", "prioritaet": "—", "faellig_bis": "—"},
            {"neue_aufgabe": "—", "verantwortlich": "—", "prioritaet": "—", "faellig_bis": "—"},
            {"neue_aufgabe": "—", "verantwortlich": "—", "prioritaet": "—", "faellig_bis": "—"},
        ]

    def _placeholders_decisions() -> list[dict]:
        return [
            {"entscheidung": "—", "hintergrund": "—", "verantwortlich": "—"},
            {"entscheidung": "—", "hintergrund": "—", "verantwortlich": "—"},
            {"entscheidung": "—", "hintergrund": "—", "verantwortlich": "—"},
        ]

    meeting_title = (r.meeting_titel or req.title).strip() or "Meeting"
    datum = (r.datum or "").strip() or "TT.MM.JJJJ"
    moderator = (r.moderator or "").strip() or "Name"
    teilnehmer = r.teilnehmer or ["Teilnehmerliste"]
    fazit = (r.fazit or "").strip() or "Hier eine kurze Zusammenfassung der wichtigsten Punkte des Meetings eintragen."

    # Type-C budgeting heuristics for responsive pagination
    continue_words_budget = int(os.getenv("PDF_TYPEC_CONTINUE_BUDGET_WORDS", "420"))
    continue_units_default = max(28, continue_words_budget // 9)
    continue_units_budget = int(os.getenv("PDF_TYPEC_CONTINUE_BUDGET_UNITS", str(int(continue_units_default))))
    continue_chars_per_line = int(os.getenv("PDF_TYPEC_CONTINUE_CHARS_PER_LINE", "110"))

    def _split_text_chunks(text: str, *, max_chars: int) -> list[str]:
        t = _compact_ws(text)
        if not t:
            return []
        if len(t) <= max_chars:
            return [t]
        chunks: list[str] = []
        s = t
        while s:
            if len(s) <= max_chars:
                chunks.append(s.strip())
                break
            cut = max_chars
            window = s[: max_chars + 1]
            m = re.search(r"[\.\!\?]\s+(?=[A-ZÄÖÜ0-9])", window[::-1])
            if m:
                # m.start() is from reversed string
                idx_from_end = m.start()
                cut = max(140, max_chars - idx_from_end - 1)
            else:
                # fall back to whitespace
                ws = window.rfind(" ")
                if ws >= 140:
                    cut = ws
            chunk = s[:cut].strip()
            if chunk:
                chunks.append(chunk)
            s = s[cut:].strip()
        return [c for c in chunks if c]

    def _fazit_blocks(text: str) -> list[_Block]:
        # Ensure we can paginate even if user provides one huge paragraph.
        raw_paragraphs = _split_paragraphs(text)
        paragraphs: list[str] = []
        for p in raw_paragraphs:
            if len(_compact_ws(p)) > 520:
                paragraphs.extend(_split_text_chunks(p, max_chars=420))
            else:
                paragraphs.append(p)
        return _parse_blocks(paragraphs)

    fazit_blocks = _fazit_blocks(fazit) if fazit else []
    inline_max_units = int(os.getenv("PDF_RAPPORT_FAZIT_INLINE_MAX_UNITS", "14"))
    inline_units = sum(_estimate_units_for_block(b, chars_per_line=continue_chars_per_line) for b in fazit_blocks)
    fazit_inline_ok = inline_units <= max(4, inline_max_units)

    sec_common = {
        "title": "RAPPORT",
        "title_variant": "tc-title--md",
        "subheading": meeting_title,
        "subtitle_variant": "",
        "meeting": {
            "datum": datum,
            "titel": meeting_title,
            "moderator": moderator,
            "teilnehmer": teilnehmer,
        },
    }

    sections: list[dict] = []

    sections.append(
        {
            **sec_common,
            "layout": "rapport_intro",
            "tables": {
                "besprochene_themen": [_to_dict(row) for row in (r.besprochene_themen or [])]
                or _placeholders_discussed(),
                "aufgabenbeurteilung": [_to_dict(row) for row in (r.aufgabenbeurteilung or [])]
                or _placeholders_open_tasks(),
            },
            "page_number": f"{page:03d}",
        }
    )
    page += 1

    sections.append(
        {
            **sec_common,
            "layout": "rapport_continue",
            "tables": {
                "neue_aufgaben": [_to_dict(row) for row in (r.neue_aufgaben or [])] or _placeholders_new_tasks(),
                "wichtige_entscheidungen": [_to_dict(row) for row in (r.wichtige_entscheidungen or [])]
                or _placeholders_decisions(),
            },
            "fazit": fazit if fazit_inline_ok else "",
            "fazit_inline": fazit_inline_ok,
            "page_number": f"{page:03d}",
        }
    )
    page += 1

    if not fazit_inline_ok and fazit_blocks:
        fazit_page_units = int(os.getenv("PDF_RAPPORT_FAZIT_PAGE_UNITS", str(max(20, continue_units_budget - 4))))
        pages = _paginate_units(
            fazit_blocks,
            max_units=fazit_page_units,
            chars_per_line=continue_chars_per_line,
            merge_small_last=False,
        )
        for idx, pg in enumerate(pages):
            html = _blocks_to_html(pg)
            if not html.strip():
                continue
            sections.append(
                {
                    **sec_common,
                    "layout": "rapport_fazit",
                    "fazit_title": "Fazit/Zusammenfassung" if idx == 0 else "Fazit/Zusammenfassung (Fortsetzung)",
                    "fazit_html": html,
                    "page_number": f"{page:03d}",
                }
            )
            page += 1

    return sections


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
