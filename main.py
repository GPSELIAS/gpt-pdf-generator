from fastapi import FastAPI, Response, Header, HTTPException
from pydantic import BaseModel
import os

app = FastAPI()

API_KEY = "gps_2026_internal_secure_key"

class DocumentRequest(BaseModel):
    title: str
    content: str

@app.post("/generate")
def generate_document(
    data: DocumentRequest,
    x_api_key: str = Header(None)
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    pdf_content = f"""
PDF TITLE: {data.title}

CONTENT:
{data.content}
"""

    return Response(
        content=pdf_content.encode("utf-8"),
        media_type="application/pdf"
    )
