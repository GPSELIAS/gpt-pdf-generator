from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime, timezone
from typing import Literal
from pathlib import Path
import os
import json
import base64
import hmac
import hashlib
import uuid
import re

app = FastAPI(title="PDF Generator", version="3.0.0")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


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

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if paragraphs:
        return paragraphs

    # fallback: if there are no paragraph breaks, split by sentence groups
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunk = []
    chunks = []
    for sentence in sentences:
        chunk.append(sentence.strip())
        if len(" ".join(chunk)) > 350:
            chunks.append(" ".join(chunk).strip())
            chunk = []
    if chunk:
        chunks.append(" ".join(chunk).strip())
    return [c for c in chunks if c]


def _first_sentence(text: str, fallback: str = "") -> str:
    if not text:
        return fallback
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[0].strip() if parts else fallback


def _short_title_from_text(text: str, fallback: str) -> str:
    if not text:
        return fallback
    first = _first_sentence(text, "")
    words = re.findall(r"\w+", first)
    if not words:
        return fallback
    return " ".join(words[:4]).title()


def _build_type_a_section(title: str, intro: str, paragraphs: list[str]) -> dict:
    items = paragraphs[:4]
    while len(items) < 4:
        items.append("")

    return {
        "layout": "type_a",
        "title": title,
        "intro": intro,
        "item_1_title": _short_title_from_text(items[0], "Punkt 1"),
        "item_1_text": items[0],
        "item_2_title": _short_title_from_text(items[1], "Punkt 2"),
        "item_2_text": items[1],
        "item_3_title": _short_title_from_text(items[2], "Punkt 3"),
        "item_3_text": items[2],
        "item_4_title": _short_title_from_text(items[3], "Punkt 4"),
        "item_4_text": items[3],
        # image_1 / image_2 intentionally omitted so template fallback assets are used
    }


def _build_type_b_section(title: str, intro: str, paragraphs: list[str]) -> dict:
    factors = paragraphs[:6]
    while len(factors) < 6:
        factors.append("")

    return {
        "layout": "type_b",
        "title": title,
        "intro": intro,
        "factor_1_title": _short_title_from_text(factors[0], "Faktor 1"),
        "factor_1_text": factors[0],
        "factor_2_title": _short_title_from_text(factors[1], "Faktor 2"),
        "factor_2_text": factors[1],
        "factor_3_title": _short_title_from_text(factors[2], "Faktor 3"),
        "factor_3_text": factors[2],
        "factor_4_title": _short_title_from_text(factors[3], "Faktor 4"),
        "factor_4_text": factors[3],
        "factor_5_title": _short_title_from_text(factors[4], "Faktor 5"),
        "factor_5_text": factors[4],
        "factor_6_title": _short_title_from_text(factors[5], "Faktor 6"),
        "factor_6_text": factors[5],
        # image_1 intentionally omitted so template fallback asset is used
    }


def _content_to_sections(req: DocumentRequest) -> list[dict]:
    paragraphs = _split_paragraphs(req.content)

    # Minimal fallback if user sends very short content
    if not paragraphs:
        paragraphs = [req.content.strip() or req.subtitle or req.title]

    sections: list[dict] = []

    # First section intro prefers subtitle if available
    intro_seed = req.subtitle.strip() if req.subtitle.strip() else _first_sentence(req.content, req.title)

    # Chunk paragraphs into reusable pages
    chunk_size_a = 4
    chunk_size_b = 6

    index = 0
    page_no = 1

    while index < len(paragraphs):
        # Alternate layouts A/B/A/B...
        if page_no % 2 == 1:
            chunk = paragraphs[index:index + chunk_size_a]
            sections.append(
                _build_type_a_section(
                    title=f"{req.title}",
                    intro=intro_seed if page_no == 1 else _first_sentence(" ".join(chunk), req.subtitle),
                    paragraphs=chunk,
                )
            )
            index += chunk_size_a
        else:
            chunk = paragraphs[index:index + chunk_size_b]
            sections.append(
                _build_type_b_section(
                    title=f"{req.title}",
                    intro=_first_sentence(" ".join(chunk), req.subtitle),
                    paragraphs=chunk,
                )
            )
            index += chunk_size_b

        page_no += 1

    return sections


def _render_pdf_bytes(req: DocumentRequest) -> bytes:
    # Use master.html for the new template system.
    # If you later add a real rapport.html again, you can switch here.
    template_name = "master.html"

    try:
        template = env.get_template(template_name)
    except TemplateNotFound:
        raise HTTPException(
            status_code=500,
            detail=f"Template not found: templates/{template_name} (is it included in the container?)",
        )

    sections = _content_to_sections(req)

    rendered_html = template.render(
        document_title=req.title,
        title=req.title,  # cover claim
        subtitle=req.subtitle,
        date=datetime.now().strftime("%d.%m.%Y"),
        sections=sections,
        end_title="Vielen Dank",
        end_text=req.subtitle if req.subtitle.strip() else "Dieses Dokument wurde automatisch generiert.",
        address="GPS Group Holding",
        telephone="+41 00 000 00 00",
        website="www.gpsgroup.ch",
    )

    try:
        return HTML(string=rendered_html, base_url=str(TEMPLATES_DIR)).write_pdf()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF rendering failed: {e}")


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
        raise HTTPException(status_code=500, detail="Server configuration: PDF_API_KEY is not set")

    base_filename = "rapport" if body.template == "rapport" else "document"
    filename = f"{base_filename}_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    ttl_minutes = int(os.getenv("PDF_URL_TTL_MINUTES", "60"))
    exp_ts = datetime.now(timezone.utc).timestamp() + ttl_minutes * 60

    payload = {
        "title": body.title,
        "subtitle": body.subtitle,
        "content": body.content,
        "template": body.template,
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
        raise HTTPException(status_code=500, detail="Server configuration: PDF_API_KEY is not set")

    payload = _verify_token(token, secret)

    req = DocumentRequest(
        title=payload["title"],
        subtitle=payload["subtitle"],
        content=payload["content"],
        template=payload["template"],
    )
    filename = payload.get("filename", "document.pdf")

    pdf_bytes = _render_pdf_bytes(req)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )