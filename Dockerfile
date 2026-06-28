FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[cli]" 2>/dev/null || pip install --no-cache-dir .

# Copy source
COPY src/ ./src/

RUN pip install --no-cache-dir -e . --no-deps

# Non-root user
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["python", "-m", "holmesgpt_runbook_mcp.server"]
