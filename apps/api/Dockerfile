FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY contract ./contract
COPY alembic ./alembic

RUN pip install --no-cache-dir \
    -e ./packages/core \
    -e ./packages/cortex_client \
    -e ./packages/executor \
    -e ./packages/ledger \
    "fastapi>=0.110" "uvicorn[standard]>=0.27" "httpx>=0.27" "pydantic-settings>=2.2" \
    "duckdb>=1.0" "psycopg[binary]>=3.1" "sqlalchemy>=2.0" "alembic>=1.13" "python-multipart>=0.0.9"

ENV PYTHONPATH=/app/apps/api:/app/packages/core:/app/packages/cortex_client:/app/packages/executor:/app/packages/ledger
EXPOSE 8080
# Entrypoint runs alembic upgrade head (idempotent) before uvicorn so a
# fresh postgres volume has dms.spaces without a manual migrate.
ENTRYPOINT ["/app/apps/api/entrypoint.sh"]
CMD ["uvicorn", "dms_api.app:app", "--host", "0.0.0.0", "--port", "8080"]
