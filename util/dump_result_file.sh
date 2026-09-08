#!/usr/bin/env bash
#
# Print one result file, whether the run left it plain or inside its archive.
#
# The CI dump steps used `[ -f "$f" ] && cat "$f"`, which reads as "absent" for an
# archived file -- and the fallback branch is the success path, so a compressed run
# degrades to "no <file>" with a green check. Logs are archived, so that is now the
# normal case rather than an edge one.
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

# An empty results dir is the caller's normal "nothing found" state, not an error:
# the discovery step upstream initialises exp="" and the dump steps run under
# `if: always()`, so exiting nonzero here would paint a red X per dump step on top
# of whatever actually failed, which is exactly what those dumps exist to show.
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

# The archive the PVC script wrote inside this result set, keyed relative to it.
# The member is addressed by its full path, never by basename: several treatments
# have a file by the same name, so basename-matching would attribute one arm's
# state to another.
archive="$results_dir/workspace.tar.zst"
if [[ -f "$archive" ]]; then
  if ! command -v zstd >/dev/null 2>&1; then
    echo "cannot read $archive: zstd not installed" >&2
  else
    # Escape unconditionally: an exact path carrying a '.' or '+' would otherwise
    # reach grep -E as a wildcard and can match a different member. '/' is not an
    # ERE metacharacter, so escaping it only earns a "stray \ before /" warning.
    want="$(printf '%s' "$relative" | sed -e 's/[].[^$*+?(){}|\\]/\\&/g')"
    if ((use_glob)); then
      # Only '*' is meaningful in these patterns, and it must not cross a '/'.
      want="${want//\\\*/[^/]*}"
    fi
    member="$(zstd -dc "$archive" 2>/dev/null | tar -tf - 2>/dev/null \
      | grep -m1 -x -E "(\./)?${want}")" || true
    if [[ -n "$member" ]]; then
      zstd -dc "$archive" | tar -xOf - "$member" | emit && exit 0
    fi
  fi
fi

echo "no $relative for $(basename -- "$results_dir")"
