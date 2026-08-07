#!/bin/sh
set -eu

usage() {
  echo "usage: $0 <digest-pinned-image> <source-dir> <output-dir> <command> [args...]" >&2
  exit 64
}

[ "$#" -ge 4 ] || usage

image=$1
source_dir=$2
output_dir=$3
shift 3

case "$image" in
  *@sha256:*) digest=${image##*@sha256:} ;;
  sha256:*) digest=${image#sha256:} ;;
  *) echo "image must be pinned by a full sha256 digest" >&2; exit 65 ;;
esac
[ "${#digest}" -eq 64 ] || { echo "image digest must contain 64 hex characters" >&2; exit 65; }
case "$digest" in
  *[!0-9a-f]*) echo "image digest must use lowercase hexadecimal" >&2; exit 65 ;;
esac

[ -d "$source_dir" ] || { echo "source directory does not exist" >&2; exit 66; }
[ -d "$output_dir" ] || { echo "output directory must already exist" >&2; exit 66; }

source_abs=$(CDPATH= cd -- "$source_dir" && pwd -P)
output_abs=$(CDPATH= cd -- "$output_dir" && pwd -P)

is_broad_host_path() {
  candidate_path=$1
  case "$candidate_path" in
    /|/root|/home|/Users|/private|/tmp|/var|/usr|/etc) return 0 ;;
  esac
  parent_path=${candidate_path%/*}
  case "$parent_path" in
    /home|/Users) return 0 ;;
  esac
  return 1
}

! is_broad_host_path "$source_abs" || { echo "refusing broad source mount" >&2; exit 65; }
! is_broad_host_path "$output_abs" || { echo "refusing broad output mount" >&2; exit 65; }
[ "$source_abs" != "$output_abs" ] || { echo "source and output must be separate" >&2; exit 65; }

# Nested bind mounts can turn the supposedly read-only source writable through
# an ancestor/descendant alias. Reject overlap in either direction.
case "$output_abs/" in
  "$source_abs/"*) echo "output must not be inside source" >&2; exit 65 ;;
esac
case "$source_abs/" in
  "$output_abs/"*) echo "source must not be inside output" >&2; exit 65 ;;
esac

wall_seconds=${RUNNER_WALL_SECONDS:-600}
output_mb=${RUNNER_OUTPUT_MB:-64}
disk_mb=${RUNNER_DISK_MB:-512}
for numeric_value in "$wall_seconds" "$output_mb" "$disk_mb"; do
  case "$numeric_value" in
    ''|*[!0-9]*|0) echo "runner limits must be positive integers" >&2; exit 64 ;;
  esac
done

allowed_commands=${RUNNER_COMMAND_ALLOWLIST:-}
[ -n "$allowed_commands" ] || { echo "RUNNER_COMMAND_ALLOWLIST must be set explicitly" >&2; exit 65; }
case ",$allowed_commands," in
  *,"$1",*) ;;
  *) echo "command is not in RUNNER_COMMAND_ALLOWLIST: $1" >&2; exit 65 ;;
esac

if [ "${RUNNER_PREFLIGHT_ONLY:-0}" = "1" ]; then
  echo "runner preflight passed; no container was started"
  exit 0
fi

engine=${CONTAINER_ENGINE:-podman}
case "$engine" in
  podman)
    rootless=$($engine info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)
    [ "$rootless" = "true" ] || { echo "podman is not rootless" >&2; exit 69; }
    ;;
  docker)
    security_options=$($engine info --format '{{json .SecurityOptions}}' 2>/dev/null || true)
    case "$security_options" in
      *rootless*) ;;
      *) echo "docker daemon is not rootless" >&2; exit 69 ;;
    esac
    ;;
  *) echo "CONTAINER_ENGINE must be podman or rootless docker" >&2; exit 64 ;;
esac

# The engine receives no --env-file, host credential mount, or daemon socket.
# Static tools can execute repository configuration/plugins, so every command
# (including lint/format/typecheck) uses this same boundary.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
runner_python=${RUNNER_PYTHON:-python3}
max_output_bytes=$((output_mb * 1024 * 1024))

exec "$runner_python" "$script_dir/run_with_limits.py" \
  --wall-seconds "$wall_seconds" \
  --output-dir "$output_abs" \
  --max-output-bytes "$max_output_bytes" \
  -- \
  "$engine" run --rm \
  --network none \
  --read-only \
  --user 65532:65532 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --ipc none \
  --pids-limit 128 \
  --memory 1024m \
  --cpus 1 \
  --ulimit nofile=1024:1024 \
  --env-file /dev/null \
  --env HOME=/nonexistent \
  --env XDG_CONFIG_HOME=/nonexistent \
  --env GITHUB_TOKEN= \
  --env GH_TOKEN= \
  --env OPENAI_API_KEY= \
  --env ANTHROPIC_API_KEY= \
  --env AWS_ACCESS_KEY_ID= \
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${disk_mb}m" \
  --mount "type=bind,src=$source_abs,dst=/workspace/source,readonly" \
  --mount "type=bind,src=$output_abs,dst=/workspace/output" \
  --workdir /workspace/source \
  "$image" "$@"
