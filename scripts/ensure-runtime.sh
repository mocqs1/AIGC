#!/usr/bin/env bash
# Detects Python 3.11+ and Node.js 18+, installing project-local copies when missing.
# Usage: scripts/ensure-runtime.sh [--probe] [--skip-install] [--project-root PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=posix-common.sh
source "$SCRIPT_DIR/posix-common.sh"

PROBE=0
SKIP_INSTALL=0
PROJECT_ROOT_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --probe|-Probe)
      PROBE=1
      shift
      ;;
    --skip-install|-SkipInstall)
      SKIP_INSTALL=1
      shift
      ;;
    --project-root|-ProjectRoot)
      PROJECT_ROOT_ARG="${2-}"
      [[ -n "$PROJECT_ROOT_ARG" ]] || die "Missing value for $1"
      shift 2
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

if [[ -n "$PROJECT_ROOT_ARG" ]]; then
  PROJECT_ROOT="$PROJECT_ROOT_ARG"
fi
init_posix_paths

if [[ "$PROBE" == "1" ]]; then
  emit_runtime_probe
  exit 0
fi

ensure_posix_runtime "$SKIP_INSTALL"
emit_runtime_json
