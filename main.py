from fastapi import FastAPI, Response, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from weasyprint import HTML
from datetime import datetime
import base64
import os

app = FastAPI(
    title="PDF Generator",
    version="1.0.0",
    servers=[{"url": "https://pdf-generator-993720113169.europe-west6.run.app"}],
)

# Templates live in ./templates (must be included in the container image)
env = Environment(loader=FileSystemLoader("templates"))


class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str
    template: str = "document"

@app.get("/health")
def health():
    return {"ok": True}


def _render_pdf_bytes(req: DocumentRequest) -> bytes:
    """
    Renders master.html with data and converts it to PDF bytes using WeasyPrint.
    """
    try:
        if req.template == "rapport":
    template = env.get_template("rapport.html")
else:
    template = env.get_template("master.html")
    except TemplateNotFound:
        raise HTTPException(
            status_code=500,
            detail="Template not found: templates/master.html (is it included in the container?)",
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
        # base_url points to app root so relative assets (css/images) can resolve
        base_url = os.getcwd()
        return HTML(string=rendered_html, base_url=base_url).write_pdf()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF rendering failed: {e}")


# ✅ Public endpoint for GPT Actions / API clients: returns JSON with base64 PDF
@app.post("/generate_document")
def generate_document(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    return JSONResponse(
        {
            "filename": "document.pdf",
            "content_type": "application/pdf",
            "pdf_base64": pdf_b64,
        }
    )


# ✅ Optional: browser-friendly direct PDF download (humans)
@app.post("/generate_pdf")
def generate_document_pdf(request: DocumentRequest):
    pdf_bytes = _render_pdf_bytes(request)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=document.pdf"},
    )
