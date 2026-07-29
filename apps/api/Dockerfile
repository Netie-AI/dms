FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY contract ./contract

RUN pip install --no-cache-dir \
    -e ./packages/core \
    -e ./packages/cortex_client \
    -e ./packages/executor \
    -e ./packages/ledger \
    "fastapi>=0.110" "uvicorn[standard]>=0.27" "httpx>=0.27" "pydantic-settings>=2.2"

ENV PYTHONPATH=/app/apps/api:/app/packages/core:/app/packages/cortex_client:/app/packages/executor:/app/packages/ledger
EXPOSE 8080
CMD ["uvicorn", "dms_api.app:app", "--host", "0.0.0.0", "--port", "8080"]
