FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir mcp pyyaml requests && \
    rm -rf /root/.cache/pip

# Install curl for health check
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser -g 1000 && useradd -r -g appuser -u 1000 -d /app appuser

# Copy scripts
COPY scripts/ /app/scripts/

# Create data directory
RUN mkdir -p /data && chown -R appuser:appuser /app /data

# Environment
ENV WIKI_ROOT=/data
ENV MCP_PORT=8764
ENV PYTHONPATH=/app/scripts
ENV PYTHONUNBUFFERED=1

EXPOSE 8764

# Health check — POST with valid JSON-RPC initialize
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf -X POST http://localhost:8764/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"health","version":"1.0"}}}' > /dev/null || exit 1

USER appuser

CMD ["python", "/app/scripts/wiki_mcp_server.py"]
