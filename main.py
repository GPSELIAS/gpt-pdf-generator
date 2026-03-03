from fastapi import FastAPI, Response, Header, HTTPException
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from datetime import datetime

app = FastAPI()

API_KEY = "gps_2026_internal_secure_key"

env = Environment(loader=FileSystemLoader("templates"))

class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str

@app.post("/generate")
def generate_document(
    request: DocumentRequest,
    x_api_key: str = Header(None)
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

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

    pdf = HTML(
        string=rendered_html,
        base_url="."
    ).write_pdf()

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=document.pdf"
        }
    )