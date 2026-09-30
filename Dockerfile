# DocCheck — образ для Docker/Proxmox (CPU-only, без GPU)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/doccheck

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME ["/opt/doccheck/data"]
EXPOSE 5000

# Gunicorn с конфигурацией, учитывающей слабый CPU-сервер
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
