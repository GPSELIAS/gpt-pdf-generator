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
    servers=[
        {"url": "https://pdf-generator-993720113169.europe-west6.run.app"}
    ],
)

API_KEY = "gps_2026_internal_secure_key"

env = Environment(loader=FileSystemLoader("templates"))

class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/generate")
def generate_document(
    request: DocumentRequest,
    x_api_key: str = Header(None)
):
    # API Key check
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="No Api Key or Invalid Key")

    template = env.get_template("master.html")

    rendered_html = template.render(
        title=request.title,
        subtitle=request.subtitle,
        tagline="Strategisches Dokument",
        section_title="Inhalt",
        content=request.content,
        callout="Dieses Dokument ist vertraulich.",
        date=datetime.now().strftime("%d.%m.%Y")
    )

    # base_url MUST point to app root so relative assets work if needed
    base_url = os.getcwd()

    pdf_bytes = HTML(string=rendered_html, base_url=base_url).write_pdf()

    # Return JSON (Actions can parse this)
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    return JSONResponse({
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "pdf_base64": pdf_b64
    })

# Optional: browser-friendly download endpoint (NOT for Actions, just for humans)
@app.post("/generate_pdf")
def generate_document_pdf(
    request: DocumentRequest,
    x_api_key: str = Header(None)
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="No Api Key or Invalid Key")

    template = env.get_template("master.html")
    rendered_html = template.render(
        title=request.title,
        subtitle=request.subtitle,
        tagline="Strategisches Dokument",
        section_title="Inhalt",
        content=request.content,
        callout="Dieses Dokument ist vertraulich.",
        date=datetime.now().strftime("%d.%m.%Y")
    )

    pdf_bytes = HTML(string=rendered_html, base_url=os.getcwd()).write_pdf()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=document.pdf"}
    )
