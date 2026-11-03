FROM python:3.12-slim

LABEL org.opencontainers.image.title="Talking" \
      org.opencontainers.image.vendor="hateaboutyouone" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd -m -u 10001 app

COPY . .
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app
ENV APP_DATA_DIR=/app/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    WHISPER_MODEL=base \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["python", "main.py", "serve"]
