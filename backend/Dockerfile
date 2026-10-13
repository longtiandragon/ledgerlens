FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.28
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY alembic.ini ./alembic.ini
COPY backend ./backend
COPY datasets ./datasets
COPY benchmarks ./benchmarks
COPY docs/data-sources.json ./docs/data-sources.json
# Public historical summaries are required by the read-only evaluation center.
COPY docs/root-cause-results.json ./docs/root-cause-results.json
COPY docs/finqa-program-replay-v3-summary.json docs/finqa-model-pilot-v1-summary.json docs/finqa-model-pilot-v2-summary.json docs/finqa-model-pilot-v2-next32-summary.json ./docs/
COPY docs/retrieval-validation.json docs/retrieval-abstention-validation.json ./docs/
ENV PYTHONPATH=/app/backend:/app
ENV PATH=/app/.venv/bin:$PATH
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
