#!/usr/bin/env bash
# Stop the AIGC Studio process managed by this workspace.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=posix-common.sh
source "$SCRIPT_DIR/posix-common.sh"

init_posix_paths

if [[ ! -f "$PID_PATH" ]]; then
  rm -f "$PORT_PATH"
  printf 'No AIGC Studio process is managed by this workspace.\n'
  exit 0
fi

pid_text="$(tr -d '[:space:]' < "$PID_PATH")"
if [[ ! "$pid_text" =~ ^[0-9]+$ || "$pid_text" -le 0 ]]; then
  rm -f "$PID_PATH" "$PORT_PATH"
  die "The AIGC Studio PID file is invalid. It was removed; start Studio again to recreate it."
fi
studio_pid="$pid_text"

if [[ ! -f "$PORT_PATH" ]]; then
  die "The AIGC Studio port record is missing or invalid; refusing to stop an unverified process."
fi
studio_port="$(tr -d '[:space:]' < "$PORT_PATH")"
if [[ ! "$studio_port" =~ ^[0-9]+$ || "$studio_port" -le 0 || "$studio_port" -gt 65535 ]]; then
  die "The AIGC Studio port record is missing or invalid; refusing to stop an unverified process."
fi

if ! pid_alive "$studio_pid"; then
  stale_owners="$(listening_pids_for_port "$studio_port" || true)"
  rm -f "$PID_PATH" "$PORT_PATH"
  if [[ -n "$stale_owners" ]]; then
    printf 'AIGC Studio PID %s is already stopped, but recorded port %s is now owned by PID(s) %s. The unrelated listener was not touched.\n' \
      "$studio_pid" "$studio_port" "$stale_owners" >&2
  else
    printf 'AIGC Studio is already stopped (stale PID %s removed; port %s is free).\n' "$studio_pid" "$studio_port"
  fi
  exit 0
fi

tree_ids="$(process_tree_ids "$studio_pid")"
is_studio=0
for tree_pid in $tree_ids; do
  name="$(process_name "$tree_pid")"
  command_line="$(process_command_line "$tree_pid")"
  if is_aigc_studio_command "$name" "$command_line" "$studio_port"; then
    is_studio=1
    break
  fi
done

if [[ "$is_studio" != "1" ]]; then
  die "PID $studio_pid is not an AIGC Studio server; refusing to stop it. Remove .runtime/studio.pid only after checking the process manually."
fi

port_owners="$(listening_pids_for_port "$studio_port" || true)"
foreign="$(foreign_listener_ids "$port_owners" "$tree_ids")"
if [[ -n "$foreign" ]]; then
  die "Recorded port $studio_port is owned by PID(s) $port_owners, not exclusively by AIGC Studio PID $studio_pid (process tree: $tree_ids); refusing to stop any process."
fi
if [[ -z "$port_owners" ]]; then
  printf 'AIGC Studio PID %s is running, but recorded port %s is not listening. The validated Studio process tree will still be stopped.\n' \
    "$studio_pid" "$studio_port" >&2
elif [[ " $port_owners " != *" $studio_pid "* ]]; then
  printf 'Port %s is held by AIGC Studio child PID(s); stopping the whole process tree.\n' "$studio_port"
fi

printf 'Stopping AIGC Studio (PID %s, port %s)...\n' "$studio_pid" "$studio_port"
stop_order=""
for tree_pid in $tree_ids; do
  if [[ "$tree_pid" != "$studio_pid" ]]; then
    stop_order="$stop_order $tree_pid"
  fi
done
stop_order="$stop_order $studio_pid"
for tree_pid in $stop_order; do
  kill "$tree_pid" >/dev/null 2>&1 || true
done

deadline=25
for _ in $(seq 1 "$deadline"); do
  still_running=""
  remaining_tree_owned=""
  for tree_pid in $tree_ids; do
    if pid_alive "$tree_pid"; then
      still_running="$still_running $tree_pid"
    fi
  done
  remaining_owners="$(listening_pids_for_port "$studio_port" || true)"
  tree_csv=",${tree_ids// /,},"
  for owner in $remaining_owners; do
    if [[ "$tree_csv" == *",$owner,"* ]]; then
      remaining_tree_owned="$remaining_tree_owned $owner"
    fi
  done
  if [[ -z "$still_running" && -z "$remaining_tree_owned" ]]; then
    break
  fi
  sleep 0.2
done

still_running=""
for tree_pid in $tree_ids; do
  if pid_alive "$tree_pid"; then
    still_running="$still_running $tree_pid"
  fi
done
if [[ -n "$still_running" ]]; then
  die "AIGC Studio process tree$still_running did not stop within 5 seconds."
fi

rm -f "$PID_PATH" "$PORT_PATH"
remaining_owners="$(listening_pids_for_port "$studio_port" || true)"
if [[ -n "$remaining_owners" ]]; then
  die "AIGC Studio PID $studio_pid stopped, but port $studio_port remains occupied by PID(s) $remaining_owners. The port was not fully released."
fi
printf 'AIGC Studio stopped and port %s is free.\n' "$studio_port"
