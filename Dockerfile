FROM python:3.12-slim

# System deps for Pillow, python-docx, and pdf/ppt processing.
# libreoffice-writer/-impress: headless .doc/.ppt -> .docx/.pptx conversion for
# legacy template uploads (core/office_convert.py) — no pip package exists for
# this, it's invoked as a subprocess (soffice --headless), hence apt not requirements.txt.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libjpeg-dev \
    libpng-dev \
    libzstd-dev \
    libreoffice-writer \
    libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Remove local .env so container must use env vars from runtime
RUN rm -f .env

EXPOSE 8002

# Unbuffered output so logs appear immediately in docker logs
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
