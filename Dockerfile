FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMARTLIB_ENV=production \
    SMARTLIB_UPLOAD_DIR=/var/data/uploads
WORKDIR /app
ARG POSTGRES_CLIENT_MAJOR=18
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl fonts-liberation \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-client-${POSTGRES_CLIENT_MAJOR}" \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --user-group --create-home smartlib \
    && mkdir -p /var/data/uploads /var/data/backups \
    && chown -R smartlib:smartlib /var/data /app
# Explicit source allowlist: local environments, databases and uploads never enter the image.
COPY --chown=smartlib:smartlib *.py *.html library_guide.json ./
COPY --chown=smartlib:smartlib routers ./routers
COPY --chown=smartlib:smartlib scripts ./scripts
COPY --chown=smartlib:smartlib templates ./templates
COPY --chown=smartlib:smartlib static ./static
COPY --chown=smartlib:smartlib mobile ./mobile
# Start as root only long enough for container_start.py to prepare mounted
# persistent storage; the entrypoint drops privileges before launching uvicorn.
USER root
EXPOSE 8000
CMD ["python", "container_start.py"]
