FROM python:3.12-slim

WORKDIR /app

# Copy pyproject.toml and src together so pip install can find the package
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Non-root user
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["python", "-m", "holmesgpt_runbook_mcp.server"]
