FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMARTLIB_ENV=production \
    SMARTLIB_UPLOAD_DIR=/var/data/uploads
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --user-group --create-home smartlib \
    && mkdir -p /var/data/uploads \
    && chown -R smartlib:smartlib /var/data /app
# Explicit source allowlist: local environments, databases and uploads never enter the image.
COPY --chown=smartlib:smartlib *.py *.html library_guide.json ./
COPY --chown=smartlib:smartlib routers ./routers
COPY --chown=smartlib:smartlib templates ./templates
COPY --chown=smartlib:smartlib static ./static
COPY --chown=smartlib:smartlib mobile ./mobile
USER smartlib
EXPOSE 8000
CMD ["python", "container_start.py"]
