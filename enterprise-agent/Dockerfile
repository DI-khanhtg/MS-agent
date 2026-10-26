FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.32

COPY pyproject.toml uv.lock README.md ./
COPY config ./config
COPY templates ./templates
COPY src ./src
COPY data/documents/Project-X ./data/documents/Project-X
RUN uv sync --frozen --no-dev --extra production --extra voice \
    && useradd --uid 10001 --create-home agent \
    && mkdir -p /app/data \
    && chown -R agent:agent /app/data

# All six artifact formats use the packaged Python renderers by default.

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
USER agent
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('APP_PORT', '8000') + '/health',timeout=3)"

CMD ["uvicorn", "enterprise_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
