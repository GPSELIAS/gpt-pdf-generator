# =========================
# Dockerfile (Cloud Run)
# FastAPI + WeasyPrint
# =========================

# 1) Base image (stable + small)
FROM python:3.11-slim

# 2) Environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3) Workdir
WORKDIR /app

# 4) System dependencies for WeasyPrint (Debian slim)
#    - cairo + pango + gdk-pixbuf: rendering stack
#    - shared-mime-info: avoids warnings & some resource detection issues
#    - fonts: decent default fonts for PDF
#    - build-essential + libffi-dev: some wheels/ffi deps if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu \
    fonts-liberation \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 5) Python deps (cache-friendly)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# 6) Copy app code
COPY . /app

# 7) Cloud Run listens on $PORT
#    Use sh -c so $PORT is expanded.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
