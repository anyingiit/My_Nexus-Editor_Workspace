#!/bin/sh
set -eu

fail() { printf 'install failed: %s\n' "$1" >&2; exit 1; }

command -v node >/dev/null 2>&1 || fail 'node.js is required'

# Resolve script directory (works when piped via curl|sh by detecting repo layout)
# When run from repo, $0 points to the script. When piped, we use a different approach.
script_dir=""
if [ -f "$0" ] && [ -d "$(dirname "$0")/bin" ]; then
  script_dir=$(dirname "$0")
else
  fail 'run from the myanyagent repo (myanyagent/install.sh) or download the full directory'
fi

target_dir="$HOME/.local/share/myanyagent"
target_bin="$target_dir/bin"
config_dir="$HOME/.config/myanyagent"
config_file="$config_dir/config.toml"
local_bin="$HOME/.local/bin"

reset_config=false
for arg in "$@"; do
  case "$arg" in
    --reset-config) reset_config=true ;;
  esac
done

# Install tool
mkdir -p "$target_bin" "$config_dir" "$local_bin"
cp "$script_dir/bin/myanyagent-credential-helper.cjs" "$target_bin/"
cp "$script_dir/bin/myanyagent-bootstrap.sh" "$target_bin/"
chmod +x "$target_bin/myanyagent-bootstrap.sh"
cp "$script_dir/bin/myanyagent-status.sh" "$target_bin/"
chmod +x "$target_bin/myanyagent-status.sh"
cp "$script_dir/VERSION" "$target_dir/VERSION"

# Symlinks into ~/.local/bin
ln -sf "$target_bin/myanyagent-bootstrap.sh" "$local_bin/myanyagent-bootstrap"
ln -sf "$target_bin/myanyagent-status.sh" "$local_bin/myanyagent-status"
ln -sf "$target_bin/myanyagent-credential-helper.cjs" "$local_bin/myanyagent-helper"

# Config: preserve existing unless --reset-config
if [ ! -f "$config_file" ] || $reset_config; then
  cp "$script_dir/config/config.template.toml" "$config_file"
  printf 'Config written to %s\n' "$config_file"
else
  printf 'Config preserved at %s (use --reset-config to regenerate)\n' "$config_file"
fi

printf 'MyAnyAgent tool installed to %s\n' "$target_dir"
printf 'Symlinks created in %s\n' "$local_bin"

# PATH check
case ":$PATH:" in
  *:"$local_bin":*) ;;
  *)
    printf '\nNOTE: %s is not on your PATH.\n' "$local_bin"
    printf 'Add this line to your shell profile:\n'
    printf '  export PATH="%s:$PATH"\n' "$local_bin"
    ;;
esac

printf '\nNext: in any repo with .myanyagent.toml, run: myanyagent-bootstrap\n'