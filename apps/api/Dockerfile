FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY contract ./contract
COPY alembic ./alembic
# cortex-contract is private (Netie-AI/Cortex) and on no package index, so the
# build cannot fetch it. Whoever builds this image drops the wheel in
# deploy/wheelhouse/ first (CI does; the directory is otherwise empty and no
# wheel is ever committed). Not a new dependency: the pin is unchanged.
COPY deploy/wheelhouse /wheelhouse

# The wheel goes in FIRST, by exact local path, with no index in play, and the
# build fails unless the wheelhouse holds exactly one. Letting the main install
# resolve cortex-contract (even with --find-links) would also search PyPI, where
# the name is unclaimed: a cortex-contract 1.2.1+ published there would win and
# land in the appliance. Once the pinned wheel is installed, the main install
# finds the requirement satisfied, and the constraint file freezes it to that
# exact version so no index candidate can replace it.
RUN set -eu; \
    n="$(find /wheelhouse -maxdepth 1 -name 'cortex_contract-*.whl' | wc -l | tr -d ' ')"; \
    if [ "${n}" -ne 1 ]; then \
        echo "deploy/wheelhouse must hold exactly one cortex_contract wheel, found ${n}" >&2; \
        exit 1; \
    fi; \
    pip install --no-cache-dir --no-index --no-deps /wheelhouse/cortex_contract-*.whl; \
    pip freeze --all | grep -iE '^cortex[_-]contract==' > /tmp/cortex-contract.pin; \
    pip install --no-cache-dir -c /tmp/cortex-contract.pin \
        -e ./packages/core \
        -e ./packages/cortex_client \
        -e ./packages/executor \
        -e ./packages/ledger \
        "fastapi>=0.110" "uvicorn[standard]>=0.27" "httpx>=0.27" "pydantic-settings>=2.2" \
        "duckdb>=1.0" "psycopg[binary]>=3.1" "sqlalchemy>=2.0" "alembic>=1.13" "python-multipart>=0.0.9"; \
    rm -f /tmp/cortex-contract.pin

ENV PYTHONPATH=/app/apps/api:/app/packages/core:/app/packages/cortex_client:/app/packages/executor:/app/packages/ledger
EXPOSE 8080
# Entrypoint runs alembic upgrade head (idempotent) before uvicorn so a
# fresh postgres volume has dms.spaces without a manual migrate.
ENTRYPOINT ["/app/apps/api/entrypoint.sh"]
CMD ["uvicorn", "dms_api.app:app", "--host", "0.0.0.0", "--port", "8080"]
