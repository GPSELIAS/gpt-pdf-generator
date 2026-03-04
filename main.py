from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
import base64
import os
import uuid

from google.cloud import storage  # MUST be in requirements.txt


app = FastAPI(title="PDF Generator", version="2.0.0")

env = Environment(loader=FileSystemLoader("templates"))


class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str
    template: Literal["document", "rapport"] = "document"


class PdfLinkResponse(BaseModel):
    filename: str
    content_type: str = "application/pdf"
    url: str
    expires_at_utc: str


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


def _gcs_upload_and_signed_url(pdf_bytes: bytes, filename: str) -> PdfLinkResponse:
    bucket_name = os.getenv("PDF_BUCKET")
    if not bucket_name:
        raise HTTPException(status_code=500, detail="Server configuration: PDF_BUCKET env var is not set")

    # TTL in minutes (default 60)
    ttl_minutes = int(os.getenv("PDF_URL_TTL_MINUTES", "60"))
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(filename)

        blob.content_type = "application/pdf"
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")

        # Signed URL (works even if bucket is NOT public)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=ttl_minutes),
            method="GET",
            response_type="application/pdf",
            response_disposition=f'attachment; filename="{filename}"',
        )

        return PdfLinkResponse(
            filename=filename,
            url=url,
            expires_at_utc=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GCS upload/signed-url failed: {e}")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    return {"service": "pdf-generator", "ok": True}


# ✅ Custom GPT endpoint: ALWAYS JSON with a real download URL
@app.post("/generate", response_model=PdfLinkResponse)
def generate(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)

    base = "rapport" if request.template == "rapport" else "document"
    filename = f"{base}_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    return _gcs_upload_and_signed_url(pdf_bytes, filename)


# Optional fallback: base64 JSON (NOT data: URL)
@app.post("/generate_base64")
def generate_base64(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    base = "rapport" if request.template == "rapport" else "document"
    filename = f"{base}_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    return JSONResponse(
        {"filename": filename, "content_type": "application/pdf", "pdf_base64": pdf_b64}
    )


# Debug endpoint for curl/browser: returns binary PDF (NOT for Custom GPT)
@app.post("/generate_binary")
def generate_binary(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)
    base = "rapport" if request.template == "rapport" else "document"
    filename = f"{base}_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
