FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

COPY scripts/ ./scripts/
RUN mkdir -p /data && chown -R appuser:appuser /app /data

ENV WIKI_ROOT=/data
ENV MCP_PORT=8764
ENV PYTHONPATH=/app/scripts
ENV PYTHONUNBUFFERED=1

EXPOSE 8764

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost',8764)); s.close(); print('ok')" || exit 1

USER appuser
CMD ["python", "/app/scripts/wiki_mcp_server.py"]
