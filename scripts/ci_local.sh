#!/usr/bin/env bash
# Run CI's gate set on CI's Python, from this machine.
#
# Why this exists: CI is ubuntu + Python 3.11, this workstation is Windows +
# Python 3.14. Every gate can pass locally and still fail on push for reasons
# that are purely version or platform - a stdlib behaviour change, a Linux-only
# path assumption, a dependency that resolves differently. Two such failures
# landed in one afternoon (a ruff pass that had never run locally, and a reveal
# test that asserted Windows path semantics). Finding those on the runner costs
# a push cycle each time.
#
# The repo is mounted read-only and copied inside the container, so an editable
# install cannot scatter build artifacts through your working tree.
#
#   bash scripts/ci_local.sh            # full gate set, as CI runs it
#   bash scripts/ci_local.sh ruff       # one gate
#   bash scripts/ci_local.sh pytest -k reveal
#
# Gates: ruff | mypy | imports | pytest | all (default)
set -uo pipefail

# Docker Desktop wants a Windows path (D:/DMS); Git Bash's pwd gives /d/DMS.
# `pwd -W` yields the Windows form under MSYS and is absent elsewhere.
_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "${_repo_dir}" && { pwd -W 2>/dev/null || printf '%s' "${_repo_dir}"; })"
CORTEX_CONTRACT="${CORTEX_CONTRACT_SRC:-D:/Cortex/packages/cortex_contract}"
PY_IMAGE="${CI_LOCAL_IMAGE:-python:3.11-slim}"
GATE="${1:-all}"
shift || true

if ! docker info >/dev/null 2>&1; then
  echo "FAIL docker is not running - start Docker Desktop first" >&2
  exit 2
fi
if [ ! -f "${CORTEX_CONTRACT}/pyproject.toml" ]; then
  echo "FAIL no cortex_contract source at ${CORTEX_CONTRACT}" >&2
  echo "     clone Netie-AI/Cortex, or set CORTEX_CONTRACT_SRC" >&2
  exit 2
fi

echo "== local CI =="
echo "   image   ${PY_IMAGE}   (CI uses Python 3.11 on ubuntu)"
echo "   repo    ${REPO}"
echo "   gate    ${GATE}"
echo

# pip cache survives runs so only the first one pays the install cost.
docker volume create dms-ci-pip >/dev/null 2>&1 || true

# shellcheck disable=SC2016
RUNNER='
set -uo pipefail
GATE="$1"; shift
export PIP_CACHE_DIR=/pipcache
export PYTHONUTF8=1

# Copy out of the read-only mount so editable installs stay inside the
# container.
#
# The file list arrives on stdin from the host, built with git rather than with
# tar excludes. Hand-maintained excludes are guesswork: the first version of
# this script copied everything and ruff promptly failed on
# tests/repos/CRAG/local_evaluation.py - a benchmark corpus that is gitignored,
# so it exists on this machine and has never existed on a runner. Any other
# ignored path (logs/, vendor/, playground/data/) would have leaked the same
# way. `git ls-files -co --exclude-standard` is exactly the set a runner sees
# after checkout, plus your uncommitted work, which is the point.
mkdir -p /work
cat > /tmp/filelist
echo "-- copying $(wc -l < /tmp/filelist) git-visible files (ignored paths excluded, as on a runner)"
tar -C /src -T /tmp/filelist --ignore-failed-read -cf - 2>/dev/null | tar -C /work -xf -
cd /work

# One install path, not two. An earlier draft had a "first run" branch and a
# "cached" branch; they drifted within minutes (one built the contract wheel
# from a writable copy, the other still pointed at the read-only mount). The
# editable installs have to be redone every run anyway, because /work is
# recreated each time - the pip *cache* volume is what makes repeats cheap, not
# a skipped install. So there is nothing for a second branch to save.
if [ -f /pipcache/.dms-deps-installed ]; then
  echo "-- installing (pip cache warm)"
else
  echo "-- installing (first run: downloads everything, later runs reuse the cache)"
fi

python -m pip install --quiet --upgrade pip wheel build 2>/dev/null || exit 1

# setuptools writes egg_info into the source tree, so it cannot build from the
# read-only mount. Copy it somewhere writable first.
rm -rf /tmp/contract && mkdir -p /tmp/contract
tar -C /contract -cf - --exclude=.git --exclude="*.egg-info" . | tar -C /tmp/contract -xf -
python -m build --wheel --outdir /tmp/cc /tmp/contract >/tmp/build.log 2>&1 || {
  echo "FAIL could not build cortex_contract wheel"; tail -15 /tmp/build.log; exit 1; }

pip install --quiet /tmp/cc/cortex_contract-*.whl 2>/dev/null || exit 1
pip install --quiet -e ./packages/core -e ./packages/cortex_client \
                    -e ./packages/executor -e ./packages/ledger 2>/dev/null || exit 1
pip install --quiet -e ".[dev]" 2>/dev/null || exit 1
pip install --quiet fastapi uvicorn httpx pydantic-settings cryptography 2>/dev/null || exit 1
touch /pipcache/.dms-deps-installed

export PYTHONPATH="/work/apps/api:/work/packages/core:/work/packages/cortex_client:/work/packages/executor:/work/packages/ledger"
echo "-- python $(python -V 2>&1 | cut -d" " -f2) on $(uname -s)"
echo

rc=0
run_gate() {
  local name="$1"; shift
  echo "== ${name} =="
  if "$@"; then echo "PASS ${name}"; else echo "FAIL ${name}"; rc=1; fi
  echo
}

case "$GATE" in
  ruff)    run_gate ruff ruff check apps packages tests "$@" ;;
  mypy)    run_gate mypy mypy apps/api/dms_api packages/core/dms_core \
             packages/cortex_client/cortex_client packages/executor/dms_executor \
             packages/ledger/dms_ledger "$@" ;;
  imports) run_gate import-linter lint-imports "$@" ;;
  pytest)  DMS_SKIP_CONTROL_PLANE_TESTS=1 run_gate pytest python -m pytest tests/ -q --tb=short "$@" ;;
  all)
    run_gate ruff ruff check apps packages tests
    run_gate mypy mypy apps/api/dms_api packages/core/dms_core \
      packages/cortex_client/cortex_client packages/executor/dms_executor \
      packages/ledger/dms_ledger
    run_gate import-linter lint-imports
    DMS_SKIP_CONTROL_PLANE_TESTS=1 run_gate pytest python -m pytest tests/ -q --tb=short
    ;;
  *) echo "unknown gate: $GATE (want ruff|mypy|imports|pytest|all)"; exit 2 ;;
esac

echo "======================================"
if [ "$rc" -eq 0 ]; then echo "ALL GATES PASSED on Python 3.11 / Linux"
else echo "SOME GATES FAILED - these would fail on CI too"; fi
exit "$rc"
'

# Feed the container the exact file set a runner would check out: tracked files
# plus untracked-but-not-ignored ones, minus everything .gitignore excludes.
git -C "${_repo_dir}" ls-files --cached --others --exclude-standard \
  | MSYS_NO_PATHCONV=1 docker run --rm -i \
      -v "${REPO}:/src:ro" \
      -v "${CORTEX_CONTRACT}:/contract:ro" \
      -v dms-ci-pip:/pipcache \
      -w /work \
      "${PY_IMAGE}" \
      bash -c "$RUNNER" _ "${GATE}" "$@"
