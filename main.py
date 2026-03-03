from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape
from pydantic import BaseModel
from weasyprint import HTML

# ----------------------------
# Config
# ----------------------------
BASE_URL = "https://pdf-generator-993720113169.europe-west6.run.app"
API_KEY = os.getenv("API_KEY", "gps_2026_internal_secure_key")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
TMP_DIR = Path("/tmp/pdfs")  # Cloud Run writable temp
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------
# App
# ----------------------------
app = FastAPI(
    title="PDF Generator",
    version="1.0.0",
    servers=[{"url": BASE_URL}],
)

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# ----------------------------
# Models
# ----------------------------
TemplateType = Literal["document", "rapport"]

class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str
    template: TemplateType = "document"


# ----------------------------
# Helpers
# ----------------------------
def _require_api_key(x_api_key: str | None) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="No Api Key or Invalid Key")

def _pick_template(req: DocumentRequest) -> str:
    return "rapport.html" if req.template == "rapport" else "master.html"

def _render_pdf_bytes(req: DocumentRequest) -> bytes:
    try:
        template = env.get_template(_pick_template(req))
    except TemplateNotFound as e:
        raise HTTPException(status_code=500, detail=f"Template not found: {e}")

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
        # base_url MUST point to project root so relative assets (assets/..., css/...) resolve
        return HTML(string=rendered_html, base_url=str(BASE_DIR)).write_pdf()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF rendering failed: {e}")

def _safe_filename(name: str) -> str:
    # minimal safe filename (no slashes)
    return "".join(c for c in name if c.isalnum() or c in ("-", "_", ".", " ")).strip() or "document.pdf"


# ----------------------------
# Routes
# ----------------------------
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/generate_document")
def generate_document(
    request: DocumentRequest,
    x_api_key: str | None = Header(None, alias="x-api-key"),
):
    """
    GPT Actions endpoint: returns JSON with a download_url (no base64 needed).
    """
    _require_api_key(x_api_key)

    pdf_bytes = _render_pdf_bytes(request)

    file_id = uuid.uuid4().hex
    stored_name = f"{file_id}.pdf"
    file_path = TMP_DIR / stored_name

    file_path.write_bytes(pdf_bytes)

    # optional: nicer visible filename
    pretty = _safe_filename(f"{request.title}.pdf")

    return JSONResponse(
        {
            "filename": pretty,
            "content_type": "application/pdf",
            "download_url": f"{BASE_URL}/download/{stored_name}?name={pretty}",
        }
    )

@app.get("/download/{stored_name}")
def download(
    stored_name: str,
    name: str | None = None,
):
    """
    Browser download endpoint. Files live in /tmp (ephemeral on Cloud Run).
    """
    file_path = TMP_DIR / stored_name
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    out_name = _safe_filename(name or "document.pdf")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=out_name,
    )

# Optional: keep your direct-PDF endpoint for humans/testing
@app.post("/generate_pdf")
def generate_pdf(
    request: DocumentRequest,
    x_api_key: str | None = Header(None, alias="x-api-key"),
):
    _require_api_key(x_api_key)
    pdf_bytes = _render_pdf_bytes(request)

    out_name = _safe_filename(f"{request.title}.pdf")
    # Save once so relative assets behaviour matches download endpoint
    file_id = uuid.uuid4().hex
    stored_name = f"{file_id}.pdf"
    file_path = TMP_DIR / stored_name
    file_path.write_bytes(pdf_bytes)

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=out_name,
    )
