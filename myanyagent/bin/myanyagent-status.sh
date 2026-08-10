#!/bin/sh
set -eu

tool_dir="$HOME/.local/share/myanyagent"
bin_dir="$tool_dir/bin"
config_file="$HOME/.config/myanyagent/config.toml"
local_bin="$HOME/.local/bin"

# Determine command path for -> run: hints: bare name if ~/.local/bin on PATH, else absolute
on_path() {
  case ":$PATH:" in
    *:"$local_bin":*) return 0 ;;
    *) return 1 ;;
  esac
}

hint_cmd() {
  if on_path; then
    printf '%s' "$1"
  else
    printf '%s/%s' "$bin_dir" "$1"
  fi
}

# Check we are in a git repo with .myanyagent.toml
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || true
toml="${repo_root:-.}/.myanyagent.toml"

if [ -f "$toml" ]; then
  repository=$(sed -n 's/^[[:space:]]*repository[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$toml" | head -1)
  installation_id=$(sed -n 's/^[[:space:]]*installation_id[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$toml" | head -1)
  printf 'repo: %s   (from .myanyagent.toml)\n' "$repository"
else
  printf 'repo: (no .myanyagent.toml found)\n'
  printf '%s\n' "-> run: $(hint_cmd myanyagent-bootstrap)   (create .myanyagent.toml with repository, installation_id, [bot])"
  exit 0
fi

# Tool installed?
if [ -d "$tool_dir" ] && [ -f "$bin_dir/myanyagent-credential-helper.cjs" ]; then
  version=$(cat "$tool_dir/VERSION" 2>/dev/null || echo "?")
  printf 'tool: installed v%s at %s\n' "$version" "$tool_dir"
else
  printf 'tool: NOT installed\n'
  printf 'install the tool first, then run myanyagent-bootstrap\n'
  printf '%s\n' "-> run: sh myanyagent/install.sh"
  exit 0
fi

# Machine config?
client_id=""
if [ -f "$config_file" ]; then
  client_id=$(sed -n 's/^[[:space:]]*client_id[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$config_file" | head -1)
  printf 'client_id: %s   (from %s)\n' "${client_id:-?}" "$config_file"
else
  printf 'client_id: NOT configured   (missing %s)\n' "$config_file"
fi

# Private key?
key_file=""
if [ -n "${MYANYAGENT_PRIVATE_KEY:-}" ]; then
  key_file="$MYANYAGENT_PRIVATE_KEY"
elif [ -f "$config_file" ]; then
  key_file=$(sed -n 's/^[[:space:]]*private_key[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$config_file" | head -1)
  case "$key_file" in '~'*) key_file="$HOME${key_file#"~"}" ;; esac
fi
if [ -n "$key_file" ] && [ -r "$key_file" ]; then
  printf 'private key: OK   %s\n' "$key_file"
else
  printf 'private key: MISSING   %s\n' "${key_file:-not configured}"
fi

# Git config bootstrapped?
configured=false
if [ -n "$repo_root" ]; then
  cfg_repo=$(git config --local --get myanyagent.repository 2>/dev/null || true)
  cfg_helper=$(git config --local --get credential.helper 2>/dev/null || true)
  if [ -n "$cfg_repo" ] && echo "$cfg_helper" | grep -q "myanyagent-credential-helper" 2>/dev/null; then
    configured=true
  fi
fi

needs_action=false

if $configured; then
  printf 'git config: configured\n'
else
  printf 'git config: NOT configured\n'
  needs_action=true
fi

# Private key or client_id missing also need action
if [ -z "$client_id" ] || [ -z "$key_file" ] || [ ! -r "$key_file" ]; then
  needs_action=true
fi

if $needs_action; then
  printf '%s\n' "-> run: $(hint_cmd myanyagent-bootstrap)"
else
  printf 'OK\n'
fi