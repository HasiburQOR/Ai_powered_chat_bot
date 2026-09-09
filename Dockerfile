FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Normalize line endings (repo may be checked out on Windows) and drop privileges.
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh \
    && useradd --create-home appuser \
    && mkdir -p /home/appuser/.cache \
    && chown -R appuser:appuser /app /home/appuser

USER appuser

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]

