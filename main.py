from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime, timedelta
from typing import Literal
import base64
import os
import uuid

from google.cloud import storage

app = FastAPI(title="PDF Generator", version="1.0.2")

env = Environment(loader=FileSystemLoader("templates"))

# ENV VAR in Cloud Run setzen:
# PDF_BUCKET = <dein-bucket-name>
PDF_BUCKET = os.environ.get("PDF_BUCKET", "")


class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str
    template: Literal["document", "rapport"] = "document"


@app.get("/health")
def health():
    return {"ok": True}


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


def _upload_and_sign(pdf_bytes: bytes, filename: str) -> str:
    if not PDF_BUCKET:
        raise HTTPException(status_code=500, detail="PDF_BUCKET env var is not set")

    client = storage.Client()
    bucket = client.bucket(PDF_BUCKET)

    object_name = f"generated/{uuid.uuid4().hex}-{filename}"
    blob = bucket.blob(object_name)

    blob.upload_from_string(pdf_bytes, content_type="application/pdf")

    # Signed URL (V4)
    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=10),
        method="GET",
        response_disposition=f'attachment; filename="{filename}"',
        response_type="application/pdf",
    )
    return url


@app.post("/generate")
def generate_pdf(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)
    filename = "rapport.pdf" if request.template == "rapport" else "document.pdf"

    download_url = _upload_and_sign(pdf_bytes, filename)

    # optional weiterhin base64 liefern (falls du es brauchst)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    return JSONResponse(
        {
            "filename": filename,
            "content_type": "application/pdf",
            "download_url": download_url,
            "pdf_base64": pdf_b64,
        }
    )


@app.post("/generate_base64")
def generate_pdf_base64(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)
    filename = "rapport.pdf" if request.template == "rapport" else "document.pdf"

    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    return JSONResponse(
        {"filename": filename, "content_type": "application/pdf", "pdf_base64": pdf_b64}
    )
