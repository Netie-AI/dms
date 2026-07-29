#!/usr/bin/env bash
set -euo pipefail
curl -fsS http://127.0.0.1:8080/health >/dev/null && echo "OK api.health"
curl -fsS http://127.0.0.1:8080/ready >/dev/null && echo "OK api.ready"
TOKEN=$(curl -fsS -X POST http://127.0.0.1:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@dms.local","password":"admin","org_slug":"default"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -fsS http://127.0.0.1:8080/spaces -H "Authorization: Bearer $TOKEN" >/dev/null && echo "OK spaces"
echo "VERIFY PASSED"
