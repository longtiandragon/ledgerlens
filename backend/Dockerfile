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
COPY docs/retrieval-validation.json docs/retrieval-abstention-validation.json ./docs/
ENV PYTHONPATH=/app/backend:/app
ENV PATH=/app/.venv/bin:$PATH
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
