#!/usr/bin/env bash
#
# Print one result file, whether the run left it plain or inside its archive.
#
# Usage: dump_result_file.sh [--tail N] [--glob] <results_dir> <relative_path>
#
#   --glob   treat <relative_path> as a shell pattern and print the first match,
#            for the logs whose names carry a pod suffix.
#   --tail N print only the last N lines.
set -uo pipefail

tail_lines=""
use_glob=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tail) tail_lines="${2-}"; shift 2 ;;
    --glob) use_glob=1; shift ;;
    --) shift; break ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done

results_dir="${1-}"
relative="${2-}"

if [[ -z "$relative" ]]; then
  echo "usage: dump_result_file.sh [--tail N] [--glob] <results_dir> <relative_path>" >&2
  exit 2
fi

emit() {
  if [[ -n "$tail_lines" ]]; then
    tail -n "$tail_lines"
  else
    cat
  fi
}

# A missing dir is normal, and the dump steps run under `if: always()`, so a
# nonzero exit here would paint a red X over the real failure.
if [[ -z "$results_dir" || ! -d "$results_dir" ]]; then
  echo "no $relative (no results directory)"
  exit 0
fi

if ((use_glob)); then
  # Nullglob so a non-match leaves the array empty rather than the literal pattern.
  shopt -s nullglob
  matches=("$results_dir"/$relative)
  shopt -u nullglob
  if ((${#matches[@]})); then
    emit < "${matches[0]}"
    exit 0
  fi
elif [[ -f "$results_dir/$relative" ]]; then
  emit < "$results_dir/$relative"
  exit 0
fi

repo_root="$(cd -- "$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")/.." && pwd)"
read -r -d '' extract <<'PY' || true
import sys

from llmdbenchmark.utilities.archive import read_member, read_members

root, relative, use_glob = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
if use_glob:
    found = read_members(root, relative)
    payload = found[sorted(found)[0]] if found else None
else:
    payload = read_member(root, relative)
if payload is None:
    sys.exit(1)
sys.stdout.buffer.write(payload)
PY

status="$(mktemp)" || { echo "no $relative (no temp file)"; exit 0; }
trap 'rm -f "$status"' EXIT
{
  PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" python3 -c "$extract" \
    "$results_dir" "$relative" "$use_glob"
  echo "$?" > "$status"
} | emit

if [[ "$(cat "$status")" == "0" ]]; then
  exit 0
fi

echo "no $relative for $(basename -- "$results_dir")"
