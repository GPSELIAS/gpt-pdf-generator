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

app = FastAPI(title="PDF Generator", version="3.1.0")

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

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paragraphs


def _content_to_sections(req: DocumentRequest) -> list[dict]:

    paragraphs = _split_paragraphs(req.content)

    if not paragraphs:
        paragraphs = [req.content]

    sections = []

    chunk_size = 4
    index = 0

    while index < len(paragraphs):

        chunk = paragraphs[index:index + chunk_size]

        sections.append(
            {
                "layout": "type_a",
                "title": req.title,
                "intro": req.subtitle,
                "item_1_title": "Punkt 1",
                "item_1_text": chunk[0] if len(chunk) > 0 else "",
                "item_2_title": "Punkt 2",
                "item_2_text": chunk[1] if len(chunk) > 1 else "",
                "item_3_title": "Punkt 3",
                "item_3_text": chunk[2] if len(chunk) > 2 else "",
                "item_4_title": "Punkt 4",
                "item_4_text": chunk[3] if len(chunk) > 3 else "",
            }
        )

        index += chunk_size

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

    sections = _content_to_sections(req)

    rendered_html = template.render(
        document_title=req.title,
        title=req.title,
        subtitle=req.subtitle,
        date=datetime.now().strftime("%d.%m.%Y"),
        sections=sections,
        end_title="Vielen Dank",
        end_text=req.subtitle,
        address="GPS Group Holding",
        telephone="+41 00 000 00 00",
        website="www.gpsgroup.ch",
    )

    return HTML(string=rendered_html, base_url=str(TEMPLATES_DIR)).write_pdf()


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

    DOCUMENT_STORE[doc_id] = body

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

    req = DOCUMENT_STORE[doc_id]

    pdf_bytes = _render_pdf_bytes(req)

    filename = payload.get("filename", "document.pdf")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
