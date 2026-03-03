from fastapi import FastAPI, Response, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from datetime import datetime
import base64
import os

app = FastAPI(
    title="PDF Generator",
    version="1.0.0",
    servers=[{"url": "https://pdf-generator-993720113169.europe-west6.run.app"}],
)

# ✅ Never hardcode secrets in code for Cloud Run. Use env / Secret Manager.
API_KEY = os.environ.get("PDF_API_KEY")  # set in Cloud Run

env = Environment(loader=FileSystemLoader("templates"))


class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str


@app.get("/health")
def health():
    return {"ok": True}


def _require_api_key(x_api_key: str | None):
    # If you forgot to set PDF_API_KEY in Cloud Run, fail loudly (500)
    if not API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: PDF_API_KEY is not set",
        )
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="No API Key or Invalid Key")


def _render_pdf_bytes(req: DocumentRequest) -> bytes:
    template = env.get_template("master.html")

    rendered_html = template.render(
        title=req.title,
        subtitle=req.subtitle,
        tagline="Strategisches Dokument",
        section_title="Inhalt",
        content=req.content,
        callout="Dieses Dokument ist vertraulich.",
        date=datetime.now().strftime("%d.%m.%Y"),
    )

    # base_url MUST point to app root so relative assets work (css/images)
    base_url = os.getcwd()
    return HTML(string=rendered_html, base_url=base_url).write_pdf()


# ✅ Primary endpoint for GPT Actions / API clients: returns JSON with base64 PDF
@app.post("/generate_document")
def generate_document(
    request: DocumentRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    _require_api_key(x_api_key)

    pdf_bytes = _render_pdf_bytes(request)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    return JSONResponse(
        {
            "filename": "document.pdf",
            "content_type": "application/pdf",
            "pdf_base64": pdf_b64,
        }
    )


# ✅ Optional: browser-friendly direct PDF download (humans, not Actions)
@app.post("/generate_pdf")
def generate_document_pdf(
    request: DocumentRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    _require_api_key(x_api_key)

    pdf_bytes = _render_pdf_bytes(request)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=document.pdf"},
    )
