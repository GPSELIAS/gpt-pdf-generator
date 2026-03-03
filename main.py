import os
from datetime import datetime
from fastapi import FastAPI, Response, Header, HTTPException
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

# ✅ HIER DEINE CLOUD RUN URL EINTRAGEN (OHNE trailing slash)
CLOUD_RUN_URL = os.getenv(
    "CLOUD_RUN_URL",
    "https://pdf-generator-993720113169.europe-west6.run.app"
)

API_KEY = os.getenv("API_KEY", "gps_2026_internal_secure_key")

app = FastAPI(
    title="PDF Generator",
    version="1.0.0",
    servers=[{"url": CLOUD_RUN_URL}]  # ✅ sorgt dafür, dass GPT Actions "servers" findet
)

env = Environment(loader=FileSystemLoader("templates"))

class DocumentRequest(BaseModel):
    title: str
    subtitle: str
    content: str

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

@app.post("/generate", response_class=Response)
def generate_document(
    request: DocumentRequest,
    x_api_key: str = Header(..., alias="x-api-key")  # ✅ required + korrektes Header-Flag
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    template = env.get_template("master.html")

    rendered_html = template.render(
        title=request.title,
        subtitle=request.subtitle,
        tagline="Strategisches Dokument",
        content=request.content,
        callout="Dieses Dokument ist vertraulich.",
        date=datetime.now().strftime("%d.%m.%Y")
    )

    pdf_bytes = HTML(string=rendered_html, base_url=".").write_pdf()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=document.pdf"}
    )
