from fastapi import FastAPI, Response
from pydantic import BaseModel

app = FastAPI()

class DocumentRequest(BaseModel):
    title: str
    content: str

@app.post("/generate")
def generate_document(data: DocumentRequest):
    # HIER kommt später dein echtes PDF Script rein
    
    pdf_content = f"""
    PDF TITLE: {data.title}
    
    CONTENT:
    {data.content}
    """

    return Response(
        content=pdf_content.encode("utf-8"),
        media_type="application/pdf"
    )
