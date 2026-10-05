#!/usr/bin/env bash
# BANK-04 (#271): compose TLS smoke. Brings the appliance stack up with a
# throwaway database password and asserts, against the live containers:
#
#   A  compose refuses to start without DMS_DB_PASSWORD (unset, and empty)
#   B  HTTPS answers /health, Studio and /v1/* on the appliance port, with a
#      certificate that verifies against Caddy's internal CA, and the API bound
#      Postgres with the supplied password (/health backend == postgres)
#   C  plain HTTP to the same port does not serve /health, Studio or /v1/*
#   D  the only host port the stack publishes is the appliance port (T6), and
#      the ui container has no :80 or :443 listener inside
#   E  files mode serves the operator-supplied certificate, and an unknown mode
#      or an empty certificate directory fails closed (nothing answers)
#
# It exits 0 only when every assertion held. Exit 2 means a precondition is
# missing (docker, curl, openssl, the cortex-contract wheel, a free port): the
# smoke did not run, which is not a pass.
#
# Every value used here is a throwaway generated at run time (password, the
# files-mode certificate). Nothing is read from or written to deploy/compose/.env:
# the compose project gets an empty --env-file and its own project name, so a
# developer's own stack, volumes and .env are never touched.
#
#   bash scripts/compose_tls_smoke.sh
#
# Needs: docker (daemon running), curl, openssl, and
# deploy/wheelhouse/cortex_contract-*.whl (apps/api/Dockerfile cannot fetch it:
# the package is private. CI builds it; locally build or copy a 1.2.x wheel).
set -uo pipefail

# Git Bash on Windows rewrites anything that looks like a POSIX path in an
# argument, which corrupts openssl -subj and docker cp. No effect elsewhere.
export MSYS_NO_PATHCONV=1

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${REPO}/deploy/compose"
WHEELHOUSE="${REPO}/deploy/wheelhouse"
UI_DIST="${REPO}/apps/ui/dist"
PORT=8080
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-dms-bank04-smoke}"

FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
die() { echo "ERROR $1" >&2; exit 2; }

to_native() {
  # Docker Desktop and the MinGW curl want C:/... rather than /tmp/...
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

for tool in docker curl openssl; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: ${tool}"
done
docker info >/dev/null 2>&1 || die "docker is not running"
compgen -G "${WHEELHOUSE}/cortex_contract-*.whl" >/dev/null \
  || die "no cortex_contract wheel in ${WHEELHOUSE} (private package; see the header of this script)"
if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
  die "something already listens on 127.0.0.1:${PORT}; stop it, the smoke needs the appliance port"
fi

TMP_DIR="$(mktemp -d)"
NTMP="$(to_native "${TMP_DIR}")"
EMPTY_ENV="${NTMP}/empty.env"
: > "${TMP_DIR}/empty.env"
DISCARD="${NTMP}/discard.out"

STUB_DIST=0
if [ ! -d "${UI_DIST}" ]; then
  # The SPA bundle is not built in this job. Caddy still has to serve a real
  # file over TLS, so a one-line stand-in carries a marker the plain-HTTP
  # checks look for. Removed on exit; never left in the tree.
  mkdir -p "${UI_DIST}"
  echo '<!doctype html><title>dms-bank04-smoke</title>dms-bank04-smoke-studio' > "${UI_DIST}/index.html"
  STUB_DIST=1
fi

dc() { (cd "${COMPOSE_DIR}" && docker compose --env-file "${EMPTY_ENV}" "$@"); }

cleanup() {
  # Interpolation needs the variable even for `down`; the value is irrelevant.
  DMS_DB_PASSWORD="${DMS_DB_PASSWORD:-unused-by-down}" dc down -v --remove-orphans >/dev/null 2>&1
  if [ "${STUB_DIST}" = 1 ]; then rm -rf "${UI_DIST}"; fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

dump_logs() {
  echo "---- compose ps ----"
  DMS_DB_PASSWORD="${DMS_DB_PASSWORD:-unused}" dc ps -a 2>&1 | tail -20
  echo "---- compose logs (tail) ----"
  DMS_DB_PASSWORD="${DMS_DB_PASSWORD:-unused}" dc logs --no-color --tail 60 2>&1 | tail -150
}

project_containers() {
  docker ps -a -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}"
}

# ---- A: compose refuses to start without the database password -------------
for case_name in unset empty; do
  if [ "${case_name}" = unset ]; then
    out="$(env -u DMS_DB_PASSWORD bash -c "cd '${COMPOSE_DIR}' && docker compose --env-file '${EMPTY_ENV}' up -d" 2>&1)"
  else
    out="$(env DMS_DB_PASSWORD= bash -c "cd '${COMPOSE_DIR}' && docker compose --env-file '${EMPTY_ENV}' up -d" 2>&1)"
  fi
  rc=$?
  if [ "${rc}" -ne 0 ] && printf '%s' "${out}" | grep -q 'DMS_DB_PASSWORD' && [ -z "$(project_containers)" ]; then
    pass "A compose refuses to start with DMS_DB_PASSWORD ${case_name} (exit ${rc}, no container created)"
  else
    fail "A compose started or created containers with DMS_DB_PASSWORD ${case_name} (exit ${rc}): $(printf '%s' "${out}" | tail -3)"
    DMS_DB_PASSWORD=unused dc down -v --remove-orphans >/dev/null 2>&1
  fi
done

# ---- bring the stack up with a throwaway password ---------------------------
DMS_DB_PASSWORD="$(openssl rand -hex 16)"
export DMS_DB_PASSWORD
if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::add-mask::${DMS_DB_PASSWORD}"; fi

echo "== docker compose up -d --build (project ${COMPOSE_PROJECT_NAME}) =="
if ! dc up -d --build; then
  fail "stack did not come up"
  dump_logs
  echo "SMOKE FAILED (${FAILS} assertion(s))"
  exit 1
fi

ROOT_CRT="${NTMP}/caddy-root.crt"
# Git Bash curl on Windows is Schannel, which demands a revocation answer that a private
# CA cannot give. --ssl-no-revoke skips only that; chain and hostname checks stay on.
# Accepted and ignored by OpenSSL builds (CI).
NOREVOKE="--ssl-no-revoke"
BASE="https://localhost:${PORT}"
RESOLVE="localhost:${PORT}:127.0.0.1"

# Caddy writes its internal root on first load; the API needs a few seconds for
# alembic. Poll for the certificate, then for a 200 that verifies against it.
code=000
for _ in $(seq 1 90); do
  [ -s "${TMP_DIR}/caddy-root.crt" ] || dc cp ui:/data/caddy/pki/authorities/local/root.crt "${ROOT_CRT}" >/dev/null 2>&1
  if [ -s "${TMP_DIR}/caddy-root.crt" ]; then
    code="$(curl -sS -m 15 ${NOREVOKE} --resolve "${RESOLVE}" --cacert "${ROOT_CRT}" -o "${NTMP}/health.json" -w '%{http_code}' "${BASE}/health" 2>/dev/null || true)"
    [ "${code}" = 200 ] && break
  fi
  sleep 2
done

# ---- B: HTTPS serves the appliance --------------------------------------
if [ "${code}" = 200 ]; then
  pass "B1 HTTPS /health answers 200, certificate verifies against Caddy's internal CA"
else
  fail "B1 HTTPS /health did not answer 200 (last code ${code})"
fi

if grep -Eq '"product"[[:space:]]*:[[:space:]]*"dms"' "${TMP_DIR}/health.json" 2>/dev/null \
  && grep -Eq '"backend"[[:space:]]*:[[:space:]]*"postgres"' "${TMP_DIR}/health.json" 2>/dev/null; then
  pass "B2 /health is the DMS API and it bound Postgres with the supplied password (backend postgres)"
else
  fail "B2 /health is not the DMS API on Postgres: $(head -c 300 "${TMP_DIR}/health.json" 2>/dev/null)"
fi

code="$(curl -sS -m 15 ${NOREVOKE} --resolve "${RESOLVE}" --cacert "${ROOT_CRT}" -o "${NTMP}/studio.html" -w '%{http_code}' "${BASE}/" 2>/dev/null || true)"
if [ "${code}" = 200 ] && grep -qi '<html\|<!doctype' "${TMP_DIR}/studio.html"; then
  pass "B3 HTTPS Studio / answers 200 with an HTML document"
else
  fail "B3 HTTPS Studio / did not serve HTML (code ${code})"
fi

code="$(curl -sS -m 15 ${NOREVOKE} --resolve "${RESOLVE}" --cacert "${ROOT_CRT}" -o "${NTMP}/spaces.json" -w '%{http_code}' "${BASE}/v1/spaces" 2>/dev/null || true)"
if [ "${code}" = 200 ] && grep -q '"spaces"' "${TMP_DIR}/spaces.json"; then
  pass "B4 HTTPS /v1/spaces answers 200 with data (the route exists, so C proves refusal, not absence)"
else
  fail "B4 HTTPS /v1/spaces did not answer with data (code ${code})"
fi

# ---- C: plain HTTP does not serve data -----------------------------------
# Accept refusal (000, 4xx) or a redirect to https; fail on any 2xx or on a body
# that carries what the HTTPS side serves.
plain_not_served() {
  # $1 path  $2 marker the HTTPS side serves at that path
  local pcode pbody
  pcode="$(curl -sS -m 10 --http1.1 -o "${NTMP}/plain.out" -w '%{http_code}' "http://127.0.0.1:${PORT}$1" 2>/dev/null || true)"
  pbody="$(head -c 4000 "${TMP_DIR}/plain.out" 2>/dev/null || true)"
  case "${pcode}" in 2*) echo "code ${pcode}"; return 1 ;; esac
  if printf '%s' "${pbody}" | grep -q "$2"; then echo "body carries '$2' (code ${pcode})"; return 1; fi
  echo "code ${pcode}"
  return 0
}
for spec in '/v1/spaces|"spaces"' '/health|"product"' '/|dms-bank04-smoke-studio'; do
  path="${spec%%|*}"; marker="${spec#*|}"
  if [ "${path}" = "/" ] && [ "${STUB_DIST}" = 0 ]; then marker='<html'; fi
  if detail="$(plain_not_served "${path}" "${marker}")"; then
    pass "C plain HTTP ${path} is not served (${detail})"
  else
    fail "C plain HTTP ${path} served data: ${detail}"
  fi
done

# ---- D: the only published host port is the appliance port ----------------
ports="$(docker ps --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" --format '{{.Ports}}' \
  | tr ',' '\n' | grep -oE ':[0-9]+->' | tr -d ':>-' | sort -u | tr '\n' ' ')"
if [ "${ports}" = "${PORT} " ]; then
  pass "D the only published host port is ${PORT}"
else
  fail "D published host ports are [${ports}], expected only [${PORT}]"
fi

# D2: inside the container nothing listens on :80 or :443 (the redirect listener
# that auto_https disable_redirects removes), and the TLS port is up. Other
# listeners are loopback only: Caddy admin :2019 and Docker DNS.
ui_id="$(dc ps -q ui | head -1)"
listeners="$(docker exec "${ui_id}" sh -c 'netstat -tln' 2>/dev/null \
  | awk 'NR > 2 { n = split($4, a, ":"); print a[n] }' | sort -u | tr '\n' ' ')"
case " ${listeners} " in
  *" 80 "* | *" 443 "*) fail "D2 ui listens on a plain-HTTP or default-HTTPS port inside the container: [${listeners}]" ;;
  *" ${PORT} "*) pass "D2 ui container has no :80 or :443 listener and listens on ${PORT} (listeners [${listeners}]; the rest is the loopback admin API and Docker DNS)" ;;
  *) fail "D2 could not read the ui container's listeners (got [${listeners}])" ;;
esac

# ---- E: files mode and fail-closed -----------------------------------------
mkdir -p "${TMP_DIR}/tls" "${TMP_DIR}/empty"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "${NTMP}/tls/tls.key" -out "${NTMP}/tls/tls.crt" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost" \
  -addext "basicConstraints=critical,CA:TRUE" >/dev/null 2>&1

wait_for() {
  # $1 extra curl args; waits for a 200 on /health, echoes the last code
  local c=000
  for _ in $(seq 1 30); do
    c="$(curl -sS -m 10 ${NOREVOKE} --resolve "${RESOLVE}" "$@" -o "${DISCARD}" -w '%{http_code}' "${BASE}/health" 2>/dev/null || true)"
    [ "${c}" = 200 ] && break
    sleep 2
  done
  echo "${c}"
}

DMS_TLS_MODE=files DMS_TLS_DIR="${NTMP}/tls" dc up -d --force-recreate --no-deps ui >/dev/null 2>&1
code="$(wait_for --cacert "${NTMP}/tls/tls.crt")"
served_fp="$(openssl s_client -connect "127.0.0.1:${PORT}" -servername localhost </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null)"
want_fp="$(openssl x509 -in "${NTMP}/tls/tls.crt" -noout -fingerprint -sha256 2>/dev/null)"
if [ "${code}" = 200 ] && [ -n "${want_fp}" ] && [ "${served_fp}" = "${want_fp}" ]; then
  pass "E1 files mode serves the supplied certificate and /health answers 200 over it"
else
  fail "E1 files mode did not serve the supplied certificate (code ${code}; served '${served_fp}' wanted '${want_fp}')"
fi

fails_closed() {
  # $1 label; env for the recreate is set by the caller. Nothing may answer.
  local c running
  sleep 8
  c="$(curl -sS -m 10 ${NOREVOKE} -k --resolve "${RESOLVE}" -o "${DISCARD}" -w '%{http_code}' "${BASE}/health" 2>/dev/null || true)"
  running="$(docker inspect -f '{{.State.Running}}' "$(dc ps -a -q ui 2>/dev/null | head -1)" 2>/dev/null || echo unknown)"
  if [ "${c}" != 200 ] && [ "${running}" != true ]; then
    pass "E $1 fails closed (ui not running, /health code ${c})"
  else
    fail "E $1 did not fail closed (ui running=${running}, /health code ${c})"
  fi
}

DMS_TLS_MODE=files DMS_TLS_DIR="${NTMP}/empty" dc up -d --force-recreate --no-deps ui >/dev/null 2>&1
fails_closed "files mode with no tls.crt/tls.key"

DMS_TLS_MODE=bogus dc up -d --force-recreate --no-deps ui >/dev/null 2>&1
fails_closed "unknown DMS_TLS_MODE"

echo
if [ "${FAILS}" -ne 0 ]; then
  dump_logs
  echo "SMOKE FAILED (${FAILS} assertion(s))"
  exit 1
fi
echo "SMOKE OK"
