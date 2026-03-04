from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime, timezone
from typing import Literal
import os
import json
import base64
import hmac
import hashlib
import uuid

app = FastAPI(title="PDF Generator", version="2.0.0")

env = Environment(loader=FileSystemLoader("templates"))

# --- Models ---
class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str
    template: Literal["document", "rapport"] = "document"

class PdfLinkResponse(BaseModel):
    filename: str
    content_type: str = "application/pdf"
    url: str

# --- Helpers ---
def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _render_pdf_bytes(req: DocumentRequest) -> bytes:
    template_name = "rapport.html" if req.template == "rapport" else "master.html"
    try:
        template = env.get_template(template_name)
    except TemplateNotFound:
        raise HTTPException(
            status_code=500,
            detail=f"Template not found: templates/{template_name} (is it included in the container?)",
        )

    rendered_html = template.render(
        title=req.title,
        subtitle=req.subtitle,
        tagline="Strategisches Dokument",
        section_title="Inhalt",
        content=req.content,
        callout="Dieses Dokument ist vertraulich.",
        date=datetime.now().strftime("%d.%m.%Y"),
    )

    try:
        base_url = os.getcwd()
        return HTML(string=rendered_html, base_url=base_url).write_pdf()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF rendering failed: {e}")

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

# --- Routes ---
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/")
def root():
    return {"service": "pdf-generator", "ok": True}

# IMPORTANT: This endpoint ALWAYS returns JSON with a download URL.
@app.post("/generate", response_model=PdfLinkResponse)
def generate(request: DocumentRequest):
    secret = os.getenv("PDF_API_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="Server configuration: PDF_API_KEY is not set")

    base_filename = "rapport" if request.template == "rapport" else "document"
    filename = f"{base_filename}_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    ttl_minutes = int(os.getenv("PDF_URL_TTL_MINUTES", "30"))
    exp_ts = datetime.now(timezone.utc).timestamp() + ttl_minutes * 60

    # Put ONLY the data needed to regenerate the PDF into the token
    payload = {
        "title": request.title,
        "subtitle": request.subtitle,
        "content": request.content,
        "template": request.template,
        "filename": filename,
        "exp": exp_ts,
    }

    token = _make_token(payload, secret)

    base_url = os.getenv("PUBLIC_BASE_URL")  # optional override
    if not base_url:
        # behind Cloud Run this is fine because the request host is your run.app domain
        base_url = "https://pdf-generator-993720113169.europe-west6.run.app"

    url = f"{base_url}/download/{token}"
    return PdfLinkResponse(filename=filename, url=url)

# Download endpoint: generates PDF on-demand and streams it as a real PDF file.
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
