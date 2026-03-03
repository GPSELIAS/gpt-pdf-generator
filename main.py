import os
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, Response, Header, HTTPException
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

app = FastAPI(title="PDF Generator", version="1.0.0")

# API Key (für Actions)
API_KEY = os.getenv("API_KEY", "gps_2026_internal_secure_key")

# Pfade
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"])
)

class DocumentRequest(BaseModel):
    title: str
    subtitle: Optional[str] = None
    content: str

    # Optional: falls du später weitere Felder brauchst:
    tagline: Optional[str] = "Strategisches Dokument"
    section_title: Optional[str] = "Inhalt"
    callout: Optional[str] = "Dieses Dokument ist vertraulich."


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/generate")
def generate_document(
    request: DocumentRequest,
    x_api_key: Optional[str] = Header(None)
):
    # Auth
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Template rendern
    template = env.get_template("master.html")
    rendered_html = template.render(
        title=request.title,
        subtitle=request.subtitle or "",
        tagline=request.tagline or "",
        section_title=request.section_title or "Inhalt",
        content=request.content,
        callout=request.callout or "",
        date=datetime.now().strftime("%d.%m.%Y")
    )

    # base_url muss auf Projektverzeichnis zeigen, damit relative Pfade (assets/...) funktionieren
    pdf_bytes = HTML(string=rendered_html, base_url=BASE_DIR).write_pdf()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=document.pdf"}
    )
