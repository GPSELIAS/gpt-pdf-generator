from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime
from typing import Literal
import base64
import os

app = FastAPI(title="PDF Generator", version="1.0.0")

# Templates live in ./templates (must be included in the container image)
env = Environment(loader=FileSystemLoader("templates"))


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


# ✅ Custom GPT Action endpoint: returns REAL PDF bytes (binary)
@app.post("/generate")
def generate_pdf(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)

    filename = "rapport.pdf" if request.template == "rapport" else "document.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # hilft manchen Clients beim korrekten Handling:
            "Cache-Control": "no-store",
        },
    )


# ✅ Optional fallback: returns JSON with base64
@app.post("/generate_base64")
def generate_pdf_base64(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    filename = "rapport.pdf" if request.template == "rapport" else "document.pdf"
    return JSONResponse(
        {"filename": filename, "content_type": "application/pdf", "pdf_base64": pdf_b64}
    )
