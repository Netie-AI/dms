#!/usr/bin/env bash
# Split the pytest suite across CI runners without deselecting anything.
#
#   ci_pytest_shard.sh files K N   print the test files of shard K (1..N)
#   ci_pytest_shard.sh verify N    fail unless the N shards together collect
#                                  exactly the ids `pytest tests/` collects,
#                                  each id once
#
# pytest is the only source of the file list: the files are the ones
# `pytest tests/ --collect-only` reports, in its own order, and shard K takes
# every Nth. A file pytest cannot collect makes --collect-only exit non-zero,
# and with pipefail that fails the caller: it is not silently left out.
# Run from the repo root with the same PYTHONPATH and env as the test step.
set -euo pipefail
# bash drops -e inside $(...) unless told otherwise. Without this line a
# collection error inside shard_files was swallowed and the file it named was
# left out of its shard (seen with a syntax-error probe file, #530).
shopt -s inherit_errexit

shard_files() { # K N
  local out
  out=$(python -m pytest tests/ --collect-only -q)
  printf '%s\n' "$out" | grep '::' | sed 's/::.*//' | awk '!seen[$0]++' \
    | awk -v k="$1" -v n="$2" '(NR - 1) % n == k - 1'
}

case "${1:-}" in
  files)
    [ "$2" -ge 1 ] && [ "$2" -le "$3" ] || { echo "shard $2 is not in 1..$3" >&2; exit 2; }
    files=$(shard_files "$2" "$3")
    [ -n "$files" ] || { echo "shard $2 of $3 is empty" >&2; exit 1; }
    printf '%s\n' "$files"
    ;;
  verify)
    n="$2"
    tmp=$(mktemp -d)
    full=$(python -m pytest tests/ --collect-only -q)
    printf '%s\n' "$full" | grep '::' | sort > "$tmp/full"
    : > "$tmp/shards"
    for k in $(seq 1 "$n"); do
      shard_files "$k" "$n" > "$tmp/files"
      part=$(xargs -a "$tmp/files" python -m pytest --collect-only -q)
      printf '%s\n' "$part" | grep '::' >> "$tmp/shards"
      echo "shard $k of $n: $(wc -l < "$tmp/files") files, $(printf '%s\n' "$part" | grep -c '::') tests"
    done
    sort "$tmp/shards" > "$tmp/union"
    echo "pytest tests/: $(wc -l < "$tmp/full") tests. shards together: $(wc -l < "$tmp/union") tests."
    [ -s "$tmp/full" ] || { echo "::error::pytest collected nothing"; exit 1; }
    if ! diff "$tmp/full" "$tmp/union" > "$tmp/diff"; then
      echo "::error::the shards do not run exactly the suite (< only in pytest tests/, > only or twice in the shards)"
      head -50 "$tmp/diff"
      exit 1
    fi
    echo "OK - the $n shards collect exactly the ids pytest tests/ collects, each once"
    ;;
  *)
    echo "usage: $0 files K N | verify N" >&2
    exit 2
    ;;
esac
