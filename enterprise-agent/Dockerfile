FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y fonts-dejavu-core nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.32

COPY pyproject.toml uv.lock README.md ./
COPY config ./config
COPY templates ./templates
COPY src ./src
RUN uv sync --frozen --no-dev

# Mount the approved @oai/artifact-tool node_modules directory and set
# ARTIFACT_TOOL_NODE_MODULES for XLSX/PPTX. Other artifact formats do not require it.

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["uvicorn", "enterprise_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
