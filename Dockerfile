# ── Imagem base ──────────────────────────────────────────────────
FROM python:3.13-slim

# ── Variáveis de ambiente Python ────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Deps do sistema ──────────────────────────────────────────────
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── 작업 디렉토리 ──────────────────────────────────────────
WORKDIR /app

# ── Instalar dependências Python ──────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copiar código da aplicação ────────────────────────────────
COPY server.py .
COPY index.html .

# ── Variável de ambiente para o Flask ─────────────────────────
ENV FLASK_APP=server.py

# ── Non-root user (security) ───────────────────────────────────
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# ── Expor porta ────────────────────────────────────────────────
EXPOSE 5000

# ── Entrypoint: Gunicorn ───────────────────────────────────────
# Gunicorn com 4 workers synchronous — adequado para I/O-bound
# (requests para PubMed/PharmGKB são bloqueantes).
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "server:app"]
