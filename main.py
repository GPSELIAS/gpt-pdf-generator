from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime, timezone
from typing import Literal, Optional
import base64
import os
import uuid

# --- Optional: Google Cloud Storage (only used if PDF_BUCKET is set) ---
try:
    from google.cloud import storage  # type: ignore
except Exception:
    storage = None  # allows app to run even if deps not installed

app = FastAPI(title="PDF Generator", version="1.1.0")

# Templates live in ./templates (must be included in the container image)
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


def _now_stamp() -> str:
    # UTC timestamp for stable filenames
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


def _gcs_upload_and_get_public_url(pdf_bytes: bytes, filename: str) -> str:
    """
    Uploads to GCS bucket defined by env var PDF_BUCKET and returns a public URL.
    Requires:
      - env var PDF_BUCKET set
      - google-cloud-storage installed
      - Cloud Run service account has Storage Object Admin on the bucket
      - Bucket allows object public access OR you'll use signed URLs (not implemented here)
    """
    bucket_name = os.getenv("PDF_BUCKET")
    if not bucket_name:
        raise HTTPException(status_code=500, detail="Server configuration: PDF_BUCKET env var is not set")

    if storage is None:
        raise HTTPException(
            status_code=500,
            detail="google-cloud-storage is not installed but PDF_BUCKET is set. Add google-cloud-storage to requirements.txt",
        )

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        blob = bucket.blob(filename)
        blob.content_type = "application/pdf"

        # Upload bytes
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")

        # Make public (works only if org policy/bucket settings allow it)
        # If your project enforces "Public access prevention", this will fail.
        blob.make_public()

        return blob.public_url
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GCS upload failed: {e}")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    return {"service": "pdf-generator", "ok": True}


# ✅ Main endpoint for Custom GPT: returns a JSON with a real downloadable URL (preferred)
@app.post("/generate", response_model=PdfLinkResponse)
def generate_pdf(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)

    base_filename = "rapport" if request.template == "rapport" else "document"
    safe_name = f"{base_filename}_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    # If PDF_BUCKET is set -> upload to GCS and return URL
    if os.getenv("PDF_BUCKET"):
        url = _gcs_upload_and_get_public_url(pdf_bytes, safe_name)
        return PdfLinkResponse(filename=safe_name, url=url)

    # If no bucket configured -> fallback to direct binary download
    # (This works with curl/browser, but some ChatGPT UIs don't offer a clickable file link reliably.)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Cache-Control": "no-store",
        },
    )


# ✅ Optional fallback: returns JSON with base64 (never as "data:" URL; clients decide what to do)
@app.post("/generate_base64")
def generate_pdf_base64(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    base_filename = "rapport" if request.template == "rapport" else "document"
    filename = f"{base_filename}_{_now_stamp()}_{uuid.uuid4().hex[:8]}.pdf"

    return JSONResponse(
        {"filename": filename, "content_type": "application/pdf", "pdf_base64": pdf_b64}
    )
