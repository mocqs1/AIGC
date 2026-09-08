#!/usr/bin/env bash
# Start AIGC Studio on macOS or Linux.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=posix-common.sh
source "$SCRIPT_DIR/posix-common.sh"

DEV=0
NO_BROWSER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev|-Dev)
      DEV=1
      shift
      ;;
    --no-browser|-NoBrowser)
      NO_BROWSER=1
      shift
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

init_posix_paths

if [[ -f "$PORT_PATH" ]]; then
  saved_port="$(tr -d '[:space:]' < "$PORT_PATH")"
  if [[ "$saved_port" =~ ^[0-9]+$ && "$saved_port" -gt 0 && "$saved_port" -le 65535 ]]; then
    set_studio_endpoint "$saved_port"
  fi
fi

if [[ "$DEV" != "1" ]] && studio_is_ready; then
  printf 'AIGC Studio is already running at %s\n' "$STUDIO_URL"
  open_studio "$NO_BROWSER"
  exit 0
fi

printf '[1/4] Checking Python and Node.js...\n'
ensure_posix_runtime 0
if ! runtime_present "$VENV_PYTHON" || ! runtime_present "$NODE_EXE" || ! runtime_present "$NPM_EXE"; then
  die "Runtime bootstrap returned missing Python, Node.js, or npm paths."
fi
printf 'Using Python %s at %s\n' "$PYTHON_VERSION" "$VENV_PYTHON"
printf 'Using Node.js %s at %s\n' "$NODE_MAJOR" "$NODE_EXE"

printf '[2/4] Checking Python dependencies...\n'
if ! python_packages_ready "$VENV_PYTHON"; then
  printf 'Installing Python dependencies...\n'
  install_python_packages "$VENV_PYTHON" "$REQUIREMENTS_PATH"
fi

printf '[3/4] Preparing the web application...\n'
if [[ ! -f "$WEB_ROOT/package.json" ]]; then
  die "Web application is missing. Expected package.json at $WEB_ROOT/package.json. Unpack the full AIGC repository so the web folder is next to start-aigc.sh."
fi
if [[ ! -d "$WEB_ROOT/node_modules" ]] || ! (cd "$WEB_ROOT" && "$NPM_EXE" ls --depth=0 >/dev/null 2>&1); then
  printf 'Installing web dependencies...\n'
  install_npm_packages "$NPM_EXE" "$WEB_ROOT"
fi

if [[ "$DEV" != "1" ]]; then
  (cd "$WEB_ROOT" && "$NPM_EXE" run build) || die "Web application build failed."
fi

printf '[4/4] Starting AIGC Studio...\n'
if ! studio_is_ready; then
  set_studio_endpoint "$(find_studio_port)"
  mkdir -p "$RUNTIME_ROOT"
  stdout_log="$RUNTIME_ROOT/studio.stdout.log"
  stderr_log="$RUNTIME_ROOT/studio.stderr.log"
  nohup "$VENV_PYTHON" -m uvicorn api_server:app --host 127.0.0.1 --port "$STUDIO_PORT" \
    >"$stdout_log" 2>"$stderr_log" &
  server_pid=$!
  printf '%s\n' "$server_pid" > "$PID_PATH"
  printf '%s\n' "$STUDIO_PORT" > "$PORT_PATH"

  ready=0
  for _ in $(seq 1 80); do
    if studio_is_ready; then
      ready=1
      break
    fi
    if ! pid_alive "$server_pid"; then
      die "AIGC Studio exited before becoming ready. See $stderr_log"
    fi
    sleep 0.25
  done
  if [[ "$ready" != "1" ]]; then
    kill "$server_pid" >/dev/null 2>&1 || true
    die "AIGC Studio did not become ready within 20 seconds. See $stderr_log"
  fi
  printf 'AIGC Studio started in the background (PID %s).\n' "$server_pid"
  printf 'To stop it, run: ./stop-aigc.sh\n'
else
  printf 'Reusing the AIGC Studio backend already running on port %s.\n' "$STUDIO_PORT"
fi

if [[ "$DEV" == "1" ]]; then
  printf 'Starting the development UI at http://127.0.0.1:5173\n'
  export AIGC_API_PORT="$STUDIO_PORT"
  (cd "$WEB_ROOT" && "$NPM_EXE" run dev) || die "Development UI failed."
  exit 0
fi

printf 'AIGC Studio is ready at %s\n' "$STUDIO_URL"
open_studio "$NO_BROWSER"
