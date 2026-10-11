FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt /app/
RUN pip install -r requirements.txt

COPY . /app/
COPY entrypoint.sh /entrypoint.sh

# Run as an unprivileged user; only media/ and chroma_db/ need to be writable.
RUN chmod +x /entrypoint.sh \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /app/media /app/chroma_db /app/staticfiles \
    && chown -R app:app /app/media /app/chroma_db /app/staticfiles
USER app

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]

# gthread workers keep streaming chat responses from blocking other requests.
CMD ["sh", "-c", "exec gunicorn academic_copilot.wsgi:application --bind 0.0.0.0:${PORT} --worker-class gthread --workers 2 --threads 8 --timeout 120"]
