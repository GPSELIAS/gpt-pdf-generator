import os
from datetime import datetime
from fastapi import FastAPI, Response, Header, HTTPException
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

# >>> SET THIS to your Cloud Run URL (no trailing slash)
SERVICE_BASE_URL = "https://pdf-generator-993720113169.europe-west6.run.app"

API_KEY = os.getenv("API_KEY", "gps_2026_internal_secure_key")

app = FastAPI(title="PDF Generator API", version="1.0.0")

env = Environment(loader=FileSystemLoader("templates"), autoescape=True)

class DocumentRequest(BaseModel):
    title: str
    subtitle: str | None = None     # <-- optional, so the tool won't break either way
    content: str

@app.get("/health")
def health():
    return {"ok": True}

@app.post(
    "/generate",
    response_class=Response,
    responses={
        200: {
            "description": "PDF file",
            "content": {"application/pdf": {}},
        }
    },
)
def generate_document(request: DocumentRequest, x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    template = env.get_template("master.html")
    rendered_html = template.render(
        title=request.title,
        subtitle=request.subtitle or "",
        tagline="Strategisches Dokument",
        section_title="Inhalt",
        content=request.content,
        callout="Dieses Dokument ist vertraulich.",
        date=datetime.now().strftime("%d.%m.%Y"),
    )

    pdf_bytes = HTML(string=rendered_html, base_url=".").write_pdf()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=document.pdf"},
    )

# ---- IMPORTANT: force absolute servers URL so ChatGPT Actions accepts it
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )

    schema["servers"] = [{"url": SERVICE_BASE_URL}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
