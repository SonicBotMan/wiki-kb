FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/

ENV PYTHONPATH=/app/scripts
ENV PYTHONUNBUFFERED=1

WORKDIR /app
CMD ["python", "/app/scripts/wiki_mcp_server.py"]
