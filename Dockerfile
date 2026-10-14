FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_DEBUG=false

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY static/ static/

EXPOSE 5005

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3     CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5005/languages', timeout=5).read()"]

RUN adduser --disabled-password --gecos "" appuser \
    && find /app -type d -exec chmod 755 {} + \
    && find /app -type f -exec chmod 644 {} + \
    && chown -R appuser:appuser /app

USER appuser

CMD ["waitress-serve", "--listen=0.0.0.0:5005", "--threads=8", "app:app"]
