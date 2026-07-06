# ScopeTrace API — production container image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first for better layer caching.
# psycopg2-binary bundles its own libpq, so no system build deps are required.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY . .

# Normalise line endings + make the entrypoint executable (robust across
# Windows checkouts), then drop to a non-root user. beat-data is pre-created
# here (empty) so its ownership is already correct when docker-compose.yml's
# `beat` service mounts a named volume onto it — Docker seeds a fresh named
# volume from whatever already exists in the image at that path, permissions
# included; without this, the volume would appear owned by root and appuser
# (the Celery Beat process) couldn't write its schedule file there.
RUN sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh \
    && adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/beat-data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
