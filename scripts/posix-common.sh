#!/usr/bin/env bash
# Shared helpers for POSIX Studio launchers. Sourced, not executed.

set -euo pipefail

PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=11
NODE_MIN_MAJOR=18
STUDIO_PORT_MIN=49152
STUDIO_PORT_MAX=65535
PYTHON_STANDALONE_TAG="20260901"
PYTHON_STANDALONE_VERSION="3.12.14"
NODE_VERSION="22.20.0"

RESERVED_STUDIO_PORTS="8000 8080 8081 8443 8888 9000 9090 9200 9300 9418 27017 3306 5432 6379 11211 1433 1521 5000 5001 5173 3000 3001"

PROJECT_ROOT="${PROJECT_ROOT:-}"
WEB_ROOT=""
REQUIREMENTS_PATH=""
RUNTIME_ROOT=""
BOOTSTRAP_ROOT=""
PORT_PATH=""
PID_PATH=""
STUDIO_PORT=0
STUDIO_URL=""
HEALTH_URL=""
OPENAPI_URL=""
PYTHON_EXE=""
VENV_PYTHON=""
NODE_EXE=""
NPM_EXE=""
PYTHON_VERSION=""
NODE_MAJOR=""

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

json_escape() {
  local value="${1-}"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  printf '%s' "$value"
}

json_optional_string() {
  if [[ -n "${1-}" ]]; then
    printf '"%s"' "$(json_escape "$1")"
  else
    printf 'null'
  fi
}

json_optional_number() {
  if [[ -n "${1-}" ]]; then
    printf '%s' "$1"
  else
    printf 'null'
  fi
}

json_bool() {
  if [[ "${1-}" == "true" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

runtime_present() {
  [[ -n "${1-}" && ( -x "$1" || -f "$1" ) ]]
}

resolve_path() {
  local target="$1" dir base
  if [[ -d "$target" ]]; then
    (cd "$target" && pwd)
    return 0
  fi
  dir="$(cd "$(dirname "$target")" && pwd)"
  base="$(basename "$target")"
  printf '%s/%s\n' "$dir" "$base"
}

init_posix_paths() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$script_dir/.." && pwd)}"
  PROJECT_ROOT="$(resolve_path "$PROJECT_ROOT")"
  WEB_ROOT="$PROJECT_ROOT/web"
  REQUIREMENTS_PATH="$PROJECT_ROOT/requirements.txt"
  RUNTIME_ROOT="$PROJECT_ROOT/.runtime"
  BOOTSTRAP_ROOT="$RUNTIME_ROOT/bootstrap"
  PORT_PATH="$RUNTIME_ROOT/studio.port"
  PID_PATH="$RUNTIME_ROOT/studio.pid"
}

host_os() {
  uname -s | tr '[:upper:]' '[:lower:]'
}

host_arch() {
  local machine
  machine="$(uname -m)"
  case "$machine" in
    x86_64|amd64) printf 'x86_64' ;;
    arm64|aarch64) printf 'aarch64' ;;
    *) die "Unsupported CPU architecture: $machine. Install Python 3.11+ and Node.js 18+ yourself, then rerun the launcher." ;;
  esac
}

python_standalone_triple() {
  local os arch
  os="$(host_os)"
  arch="$(host_arch)"
  case "$os" in
    darwin) printf '%s-apple-darwin' "$arch" ;;
    linux) printf '%s-unknown-linux-gnu' "$arch" ;;
    *) die "Unsupported operating system: $os. The POSIX launcher supports macOS and Linux." ;;
  esac
}

node_dist_name() {
  local os arch
  os="$(host_os)"
  arch="$(host_arch)"
  case "$os-$arch" in
    darwin-x86_64) printf 'darwin-x64' ;;
    darwin-aarch64) printf 'darwin-arm64' ;;
    linux-x86_64) printf 'linux-x64' ;;
    linux-aarch64) printf 'linux-arm64' ;;
    *) die "No official Node.js $NODE_VERSION archive is configured for $os/$arch." ;;
  esac
}

venv_python_path() {
  local root="${1:-$PROJECT_ROOT}"
  if [[ -f "$root/.venv/bin/python" ]]; then
    printf '%s' "$root/.venv/bin/python"
  elif [[ -f "$root/.venv/Scripts/python.exe" ]]; then
    printf '%s' "$root/.venv/Scripts/python.exe"
  fi
}

command_path() {
  command -v "$1" 2>/dev/null || true
}

python_version_of() {
  local exe="$1"
  "$exe" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true
}

python_is_usable() {
  local exe="$1"
  runtime_present "$exe" || return 1
  "$exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (${PYTHON_MIN_MAJOR}, ${PYTHON_MIN_MINOR}) else 1)" >/dev/null 2>&1
}

node_major_of() {
  local exe="$1"
  "$exe" -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || true
}

node_is_usable() {
  local exe="$1" major
  runtime_present "$exe" || return 1
  major="$(node_major_of "$exe")"
  [[ -n "$major" && "$major" -ge "$NODE_MIN_MAJOR" ]]
}

find_python_install() {
  local candidate
  local candidates=(
    "$BOOTSTRAP_ROOT/python/bin/python3"
    "$BOOTSTRAP_ROOT/python/bin/python"
    "$BOOTSTRAP_ROOT/python/python.exe"
  )
  for name in python3 python python3.exe python.exe; do
    candidate="$(command_path "$name")"
    [[ -n "$candidate" ]] && candidates+=("$candidate")
  done
  for candidate in "${candidates[@]}"; do
    if python_is_usable "$candidate"; then
      resolve_path "$candidate" | tr -d '\n'
      return 0
    fi
  done
  return 1
}

find_node_install() {
  local candidate
  local candidates=(
    "$BOOTSTRAP_ROOT/node/bin/node"
    "$BOOTSTRAP_ROOT/node/node.exe"
  )
  for name in node node.exe; do
    candidate="$(command_path "$name")"
    [[ -n "$candidate" ]] && candidates+=("$candidate")
  done
  for candidate in "${candidates[@]}"; do
    if node_is_usable "$candidate"; then
      resolve_path "$candidate" | tr -d '\n'
      return 0
    fi
  done
  return 1
}

find_npm_install() {
  local node_path="${1-}" candidate
  local candidates=()
  if [[ -n "$node_path" ]]; then
    candidates+=("$(dirname "$node_path")/npm")
    candidates+=("$(dirname "$node_path")/npm.cmd")
  fi
  candidates+=("$BOOTSTRAP_ROOT/node/bin/npm")
  for name in npm npm.cmd; do
    candidate="$(command_path "$name")"
    [[ -n "$candidate" ]] && candidates+=("$candidate")
  done
  for candidate in "${candidates[@]}"; do
    if [[ -n "$candidate" && ( -x "$candidate" || -f "$candidate" ) ]]; then
      resolve_path "$candidate" | tr -d '\n'
      return 0
    fi
  done
  return 1
}

download_file() {
  local url="$1" destination="$2"
  mkdir -p "$(dirname "$destination")"
  printf 'Downloading %s\n' "$url"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --retry-delay 2 -o "$destination" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$destination" "$url"
  else
    die "Neither curl nor wget is available to download $url"
  fi
  [[ -s "$destination" ]] || die "Downloaded installer is missing or empty: $destination"
}

install_python_runtime() {
  local triple archive url tmp dest
  triple="$(python_standalone_triple)"
  archive="cpython-${PYTHON_STANDALONE_VERSION}+${PYTHON_STANDALONE_TAG}-${triple}-install_only.tar.gz"
  url="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_TAG}/cpython-${PYTHON_STANDALONE_VERSION}%2B${PYTHON_STANDALONE_TAG}-${triple}-install_only.tar.gz"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/aigc-python.XXXXXX")"
  dest="$BOOTSTRAP_ROOT/python"
  printf 'Python %s.%s+ was not found. Installing a project-local CPython %s...\n' "$PYTHON_MIN_MAJOR" "$PYTHON_MIN_MINOR" "$PYTHON_STANDALONE_VERSION"
  download_file "$url" "$tmp/$archive"
  rm -rf "$dest"
  mkdir -p "$BOOTSTRAP_ROOT"
  tar -xzf "$tmp/$archive" -C "$tmp"
  if [[ -d "$tmp/python" ]]; then
    mv "$tmp/python" "$dest"
  else
    rm -rf "$tmp"
    die "Python archive did not contain a python/ directory."
  fi
  rm -rf "$tmp"
  python_is_usable "$dest/bin/python3" || die "Project-local Python is still unusable after installation. Install Python 3.11+ from https://www.python.org/downloads/ and rerun the launcher."
}

install_node_runtime() {
  local dist archive url tmp dest
  dist="$(node_dist_name)"
  archive="node-v${NODE_VERSION}-${dist}.tar.gz"
  url="https://nodejs.org/dist/v${NODE_VERSION}/${archive}"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/aigc-node.XXXXXX")"
  dest="$BOOTSTRAP_ROOT/node"
  printf 'Node.js %s+ was not found. Installing a project-local Node.js %s...\n' "$NODE_MIN_MAJOR" "$NODE_VERSION"
  download_file "$url" "$tmp/$archive"
  rm -rf "$dest"
  mkdir -p "$BOOTSTRAP_ROOT"
  tar -xzf "$tmp/$archive" -C "$tmp"
  if [[ -d "$tmp/node-v${NODE_VERSION}-${dist}" ]]; then
    mv "$tmp/node-v${NODE_VERSION}-${dist}" "$dest"
  else
    rm -rf "$tmp"
    die "Node.js archive did not contain node-v${NODE_VERSION}-${dist}/."
  fi
  rm -rf "$tmp"
  node_is_usable "$dest/bin/node" || die "Project-local Node.js is still unusable after installation. Install Node.js 18+ LTS from https://nodejs.org/ and rerun the launcher."
}

create_project_venv() {
  local root="${1:-$PROJECT_ROOT}" python_exe="${2:-$PYTHON_EXE}" venv_dir venv_python
  venv_dir="$root/.venv"
  venv_python="$(venv_python_path "$root")"
  if [[ -n "$venv_python" ]] && python_is_usable "$venv_python"; then
    printf '%s' "$venv_python"
    return 0
  fi
  if [[ -d "$venv_dir" ]]; then
    printf 'Replacing unusable project virtual environment...\n' >&2
    rm -rf "$venv_dir"
  fi
  printf 'Creating project virtual environment...\n' >&2
  "$python_exe" -m venv "$venv_dir"
  venv_python="$(venv_python_path "$root")"
  python_is_usable "$venv_python" || die "Failed to create a Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ virtual environment at $venv_dir"
  printf '%s' "$venv_python"
}

emit_runtime_probe() {
  local python_path node_path npm_path venv_path python_ver node_major python_ok node_ok npm_ok venv_ok
  python_path="$(find_python_install || true)"
  node_path="$(find_node_install || true)"
  npm_path="$(find_npm_install "$node_path" || true)"
  venv_path="$(venv_python_path "$PROJECT_ROOT")"
  python_ver=""
  node_major=""
  python_ok="false"
  node_ok="false"
  npm_ok="false"
  venv_ok="false"
  if [[ -n "$python_path" ]]; then
    python_ver="$(python_version_of "$python_path")"
    python_ok="true"
  fi
  if [[ -n "$node_path" ]]; then
    node_major="$(node_major_of "$node_path")"
    node_ok="true"
  fi
  [[ -n "$npm_path" ]] && npm_ok="true"
  if [[ -n "$venv_path" ]] && python_is_usable "$venv_path"; then
    venv_ok="true"
  fi
  printf '{"python":{"path":%s,"version":%s,"usable":%s},"node":{"path":%s,"major":%s,"usable":%s},"npm":{"path":%s,"usable":%s},"venv":{"path":%s,"usable":%s}}\n' \
    "$(json_optional_string "$python_path")" \
    "$(json_optional_string "$python_ver")" \
    "$(json_bool "$python_ok")" \
    "$(json_optional_string "$node_path")" \
    "$(json_optional_number "$node_major")" \
    "$(json_bool "$node_ok")" \
    "$(json_optional_string "$npm_path")" \
    "$(json_bool "$npm_ok")" \
    "$(json_optional_string "$venv_path")" \
    "$(json_bool "$venv_ok")"
}

ensure_posix_runtime() {
  local skip_install="${1:-0}"
  mkdir -p "$RUNTIME_ROOT"
  PYTHON_EXE="$(find_python_install || true)"
  if [[ -z "$PYTHON_EXE" ]]; then
    [[ "$skip_install" == "1" ]] && die "Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ was not found."
    install_python_runtime
    PYTHON_EXE="$(find_python_install || true)"
    [[ -n "$PYTHON_EXE" ]] || die "Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ is still missing after installation."
  fi
  NODE_EXE="$(find_node_install || true)"
  if [[ -z "$NODE_EXE" ]]; then
    [[ "$skip_install" == "1" ]] && die "Node.js ${NODE_MIN_MAJOR}+ was not found."
    install_node_runtime
    NODE_EXE="$(find_node_install || true)"
    [[ -n "$NODE_EXE" ]] || die "Node.js ${NODE_MIN_MAJOR}+ is still missing after installation."
  fi
  NPM_EXE="$(find_npm_install "$NODE_EXE" || true)"
  [[ -n "$NPM_EXE" ]] || die "npm was not found. Reinstall Node.js LTS and keep the npm feature enabled."
  VENV_PYTHON="$(create_project_venv "$PROJECT_ROOT" "$PYTHON_EXE")"
  PYTHON_VERSION="$(python_version_of "$PYTHON_EXE")"
  NODE_MAJOR="$(node_major_of "$NODE_EXE")"
  export PATH="$(dirname "$NODE_EXE"):$(dirname "$VENV_PYTHON"):${PATH}"
}

emit_runtime_json() {
  printf '{"python":"%s","pythonVersion":"%s","venvPython":"%s","node":"%s","nodeMajor":%s,"npm":"%s"}\n' \
    "$(json_escape "$PYTHON_EXE")" \
    "$(json_escape "$PYTHON_VERSION")" \
    "$(json_escape "$VENV_PYTHON")" \
    "$(json_escape "$NODE_EXE")" \
    "$NODE_MAJOR" \
    "$(json_escape "$NPM_EXE")"
}

port_is_reserved() {
  local port="$1" item
  for item in $RESERVED_STUDIO_PORTS; do
    [[ "$item" == "$port" ]] && return 0
  done
  return 1
}

port_in_use() {
  local port="$1"
  (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1
}

studio_port_candidate() {
  local port="$1"
  [[ "$port" -ge "$STUDIO_PORT_MIN" && "$port" -le "$STUDIO_PORT_MAX" ]] || return 1
  port_is_reserved "$port" && return 1
  port_in_use "$port" && return 1
  return 0
}

find_studio_port() {
  local attempt candidate offset index span
  span=$((STUDIO_PORT_MAX - STUDIO_PORT_MIN + 1))
  for ((attempt = 1; attempt <= 64; attempt++)); do
    candidate=$((STUDIO_PORT_MIN + RANDOM % span))
    if studio_port_candidate "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  offset=$((RANDOM % span))
  for ((index = 0; index < span; index++)); do
    candidate=$((STUDIO_PORT_MIN + (offset + index) % span))
    if studio_port_candidate "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  die "No available local port was found between $STUDIO_PORT_MIN and $STUDIO_PORT_MAX."
}

set_studio_endpoint() {
  STUDIO_PORT="$1"
  STUDIO_URL="http://127.0.0.1:${STUDIO_PORT}"
  HEALTH_URL="${STUDIO_URL}/api/health"
  OPENAPI_URL="${STUDIO_URL}/openapi.json"
}

fetch_url() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "$url"
  elif [[ -n "${VENV_PYTHON:-}" ]]; then
    "$VENV_PYTHON" - "$url" <<'PY'
import sys
import urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
    sys.stdout.write(response.read().decode("utf-8", "replace"))
PY
  else
    return 1
  fi
}

studio_is_ready() {
  local health metadata page
  [[ "$STUDIO_PORT" -gt 0 && -n "$HEALTH_URL" ]] || return 1
  health="$(fetch_url "$HEALTH_URL" 2>/dev/null || true)"
  metadata="$(fetch_url "$OPENAPI_URL" 2>/dev/null || true)"
  page="$(fetch_url "$STUDIO_URL" 2>/dev/null || true)"
  [[ "$health" == *'"status":"ok"'* || "$health" == *'"status": "ok"'* ]] || return 1
  [[ "$metadata" == *'"title":"AIGC Studio"'* || "$metadata" == *'"title": "AIGC Studio"'* ]] || return 1
  [[ "$page" == *"<title>AIGC Studio</title>"* ]] || return 1
  return 0
}

proxy_env_names() {
  printf '%s\n' HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy PIP_PROXY NPM_CONFIG_PROXY NPM_CONFIG_HTTPS_PROXY
}

run_without_proxy() {
  (
    local name
    while IFS= read -r name; do
      unset "$name" || true
    done < <(proxy_env_names)
    "$@"
  )
}

python_packages_ready() {
  local python="$1"
  "$python" -c "import importlib.util, sys; mods=('fastapi','uvicorn','boto3','dotenv','multipart','cryptography'); sys.exit(0 if all(importlib.util.find_spec(name) for name in mods) else 1)"
}

install_python_packages() {
  local python="$1" requirements="$2"
  printf 'Trying current network...\n'
  if "$python" -m pip install --disable-pip-version-check -r "$requirements" && python_packages_ready "$python"; then
    return 0
  fi
  printf 'Python package install via current network failed.\n'
  printf 'Trying direct connection...\n'
  if run_without_proxy "$python" -m pip install --disable-pip-version-check --proxy "" -r "$requirements" && python_packages_ready "$python"; then
    return 0
  fi
  printf 'Python package install via direct connection failed.\n'
  printf 'Trying Tsinghua PyPI mirror...\n'
  if run_without_proxy "$python" -m pip install --disable-pip-version-check --proxy "" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn -r "$requirements" && python_packages_ready "$python"; then
    return 0
  fi
  printf 'Python package install via Tsinghua PyPI mirror failed.\n'
  printf 'Trying Aliyun PyPI mirror...\n'
  if run_without_proxy "$python" -m pip install --disable-pip-version-check --proxy "" -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com -r "$requirements" && python_packages_ready "$python"; then
    return 0
  fi
  die "Python dependency installation failed. pip could not reach a package index, usually because a system HTTP proxy is set but unreachable. Turn off the unused proxy or set a working HTTP_PROXY/HTTPS_PROXY, then run ./start-aigc.sh again."
}

install_npm_packages() {
  local npm="$1" web_root="$2"
  [[ -f "$web_root/package.json" ]] || die "Web application is missing. Expected package.json at $web_root/package.json. Unpack the full AIGC repository so the web folder is next to start-aigc.sh."
  printf 'Trying current network...\n'
  if (cd "$web_root" && "$npm" install) && [[ -d "$web_root/node_modules" ]]; then
    return 0
  fi
  printf 'Web dependency install via current network failed.\n'
  printf 'Trying direct connection...\n'
  if run_without_proxy bash -c 'cd "$1" && "$2" install' _ "$web_root" "$npm" && [[ -d "$web_root/node_modules" ]]; then
    return 0
  fi
  printf 'Web dependency install via direct connection failed.\n'
  printf 'Trying npmmirror registry...\n'
  if run_without_proxy bash -c 'cd "$1" && "$2" install --registry https://registry.npmmirror.com' _ "$web_root" "$npm" && [[ -d "$web_root/node_modules" ]]; then
    return 0
  fi
  die "Web dependency installation failed in $web_root. If npm reported a missing package.json, unpack the full repository so the web folder is present. Otherwise turn off an unused HTTP proxy or set a working HTTP_PROXY/HTTPS_PROXY, then run ./start-aigc.sh again."
}

open_studio() {
  local no_browser="${1:-0}"
  [[ "$no_browser" == "1" ]] && return 0
  if command -v open >/dev/null 2>&1; then
    open "$STUDIO_URL" >/dev/null 2>&1 || printf 'Studio is ready, but the browser could not be opened automatically. Open %s manually.\n' "$STUDIO_URL"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$STUDIO_URL" >/dev/null 2>&1 || printf 'Studio is ready, but the browser could not be opened automatically. Open %s manually.\n' "$STUDIO_URL"
  else
    printf 'Studio is ready at %s\n' "$STUDIO_URL"
  fi
}

listening_pids_for_port() {
  local port="$1" line pid
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP "-iTCP:${port}" -sTCP:LISTEN -t 2>/dev/null | awk 'NF && !seen[$0]++'
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -lptn "sport = :${port}" 2>/dev/null | while IFS= read -r line; do
      if [[ "$line" =~ pid=([0-9]+) ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
      fi
    done | awk 'NF && !seen[$0]++'
    return 0
  fi
  die "Unable to verify the listener on port $port. Install lsof or iproute2 ss, then rerun ./stop-aigc.sh."
}

process_command_line() {
  local pid="$1"
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    tr '\0' ' ' < "/proc/${pid}/cmdline" | sed 's/[[:space:]]*$//'
    return 0
  fi
  ps -o command= -p "$pid" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

process_name() {
  local pid="$1"
  if [[ -r "/proc/${pid}/comm" ]]; then
    tr -d '\n' < "/proc/${pid}/comm"
    return 0
  fi
  ps -o comm= -p "$pid" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

process_tree_ids() {
  local root_pid="$1" current kids kid
  local queue=("$root_pid")
  local seen=("$root_pid")
  local seen_csv=",$root_pid,"
  while ((${#queue[@]})); do
    current="${queue[0]}"
    queue=("${queue[@]:1}")
    kids=""
    if command -v pgrep >/dev/null 2>&1; then
      kids="$(pgrep -P "$current" 2>/dev/null || true)"
    fi
    if [[ -z "$kids" && -r /proc ]]; then
      kids="$(awk -v parent="$current" '$1 ~ /^[0-9]+$/ {
        status="/proc/" $1 "/status"
        while ((getline line < status) > 0) {
          if (line ~ /^PPid:/) {
            split(line, parts, " ")
            if (parts[2] == parent) print $1
            break
          }
        }
        close(status)
      }' /proc/[0-9]*/stat 2>/dev/null || true)"
    fi
    for kid in $kids; do
      if [[ "$seen_csv" != *",$kid,"* ]]; then
        seen+=("$kid")
        seen_csv="${seen_csv}${kid},"
        queue+=("$kid")
      fi
    done
  done
  printf '%s' "${seen[*]}"
}

is_aigc_studio_command() {
  local name="$1" command_line="$2" port="$3"
  case "$name" in
    python|python3|Python) ;;
    *) return 1 ;;
  esac
  [[ "$command_line" =~ (^|[[:space:]])-m[[:space:]]+uvicorn[[:space:]]+api_server:app([[:space:]]|$) ]] || return 1
  [[ "$command_line" =~ (^|[[:space:]])--port[[:space:]]+$port([[:space:]]|$) ]]
}

foreign_listener_ids() {
  local owners="$1" tree_ids="$2" owner
  local tree_csv=",${tree_ids// /,},"
  for owner in $owners; do
    [[ "$owner" =~ ^[0-9]+$ && "$owner" -gt 0 ]] || continue
    if [[ "$tree_csv" != *",$owner,"* ]]; then
      printf '%s ' "$owner"
    fi
  done
}

pid_alive() {
  kill -0 "$1" >/dev/null 2>&1
}

wait_for_exit() {
  local pid="$1" seconds="${2:-5}" elapsed=0
  while (( elapsed < seconds * 5 )); do
    pid_alive "$pid" || return 0
    sleep 0.2
    elapsed=$((elapsed + 1))
  done
  pid_alive "$pid" && return 1
  return 0
}
