from fastapi import FastAPI, Response, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime
import base64
import os
from typing import Optional, Literal

app = FastAPI(title="PDF Generator", version="1.0.0")

env = Environment(loader=FileSystemLoader("templates"))

PDF_API_KEY = os.getenv("PDF_API_KEY")  # set in Cloud Run env vars


class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str
    template: Literal["document", "rapport"] = "document"


@app.get("/health")
def health():
    return {"ok": True}


def _require_api_key(x_api_key: Optional[str]):
    # If you want "no key required", just return here.
    if not PDF_API_KEY:
        raise HTTPException(status_code=500, detail="Server not configured: PDF_API_KEY not set")
    if not x_api_key or x_api_key != PDF_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: missing or invalid API key")


def _render_pdf_bytes(req: DocumentRequest) -> bytes:
    try:
        template_name = "rapport.html" if req.template == "rapport" else "master.html"
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


# ✅ Action endpoint: returns REAL PDF bytes (so ChatGPT can offer direct download)
@app.post("/generate")
def generate_pdf(request: DocumentRequest, x_api_key: Optional[str] = Header(default=None, alias="x-api-key")):
    _require_api_key(x_api_key)
    pdf_bytes = _render_pdf_bytes(request)

    filename = "rapport.pdf" if request.template == "rapport" else "document.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ✅ Optional: keep base64 JSON endpoint (useful for non-ChatGPT clients)
@app.post("/generate_document")
def generate_document_base64(request: DocumentRequest, x_api_key: Optional[str] = Header(default=None, alias="x-api-key")):
    _require_api_key(x_api_key)
    pdf_bytes = _render_pdf_bytes(request)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    filename = "rapport.pdf" if request.template == "rapport" else "document.pdf"
    return JSONResponse(
        {"filename": filename, "content_type": "application/pdf", "pdf_base64": pdf_b64}
    )
