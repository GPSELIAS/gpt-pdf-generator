# ---------- Base Image ----------
FROM python:3.11-slim

# ---------- Environment ----------
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ---------- Working Directory ----------
WORKDIR /app

# ---------- System Dependencies (WeasyPrint Required) ----------
RUN apt-get update && apt-get install -y \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# ---------- Python Dependencies ----------
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Copy App ----------
COPY . .

# ---------- Cloud Run Port Handling ----------
# VERY IMPORTANT: must use $PORT (Cloud Run injects this)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
