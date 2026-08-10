#!/bin/sh
set -eu

fail() {
  printf '%s\n' "myanyagent: $1" >&2
  printf '%s\n' '-> run: myanyagent-status   (inspect)' >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail 'git is required'
command -v node >/dev/null 2>&1 || fail 'node.js is required'

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || fail 'run inside a Git repository'
cd "$repo_root"

toml="$repo_root/.myanyagent.toml"
[ -f "$toml" ] || fail ".myanyagent.toml not found at repository root"

# Parse .myanyagent.toml (simple key=value and [section] parser)
toml_get() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\\([^\"]*\\)\".*$/\\1/p" "$toml" | head -1
}

repository=$(toml_get repository) || repository=""
installation_id=$(toml_get installation_id) || installation_id=""
bot_name=$(toml_get name) || bot_name=""
bot_email=$(toml_get email) || bot_email=""

[ -n "$repository" ] || fail "repository not set in .myanyagent.toml"
[ -n "$installation_id" ] || fail "installation_id not set in .myanyagent.toml"
[ -n "$bot_name" ] || fail "bot.name not set in .myanyagent.toml"
[ -n "$bot_email" ] || fail "bot.email not set in .myanyagent.toml"

fetch_url=$(git remote get-url origin 2>/dev/null) || fail 'origin remote is missing'
push_url=$(git remote get-url --push origin 2>/dev/null) || fail 'origin push remote is missing'
target_url="https://github.com/${repository}.git"
[ "$fetch_url" = "$target_url" ] || fail "origin fetch URL must be $target_url (got $fetch_url)"
[ "$push_url" = "$target_url" ] || fail "origin push URL must be $target_url (got $push_url)"

# Private key resolution: env > machine config > default
config_file="$HOME/.config/myanyagent/config.toml"
key_file=""
if [ -n "${MYANYAGENT_PRIVATE_KEY:-}" ]; then
  key_file="$MYANYAGENT_PRIVATE_KEY"
  case "$key_file" in /*) ;; *) fail "MYANYAGENT_PRIVATE_KEY must be an absolute path" ;; esac
elif [ -f "$config_file" ]; then
  key_file=$(sed -n 's/^[[:space:]]*private_key[[:space:]]*=[[:space:]]*"\([^"]*\)".*$/\1/p' "$config_file" | head -1)
  # Expand ~ in path
  case "$key_file" in '~'*) key_file="$HOME${key_file#"~"}" ;; esac
fi
[ -n "$key_file" ] || fail "private key path not configured (set MYANYAGENT_PRIVATE_KEY or private_key in config.toml)"
[ -r "$key_file" ] || fail "private key is missing or unreadable: $key_file"

# Tool paths
tool_dir="$HOME/.local/share/myanyagent"
helper="$tool_dir/bin/myanyagent-credential-helper.cjs"
[ -f "$helper" ] || fail "tool not installed at $tool_dir (run install.sh first)"

# Write local git config
git config --local user.name "$bot_name"
git config --local user.email "$bot_email"
git config --local user.useConfigOnly true
git config --local commit.gpgsign false
git config --local credential.useHttpPath true
git config --local myanyagent.repository "$repository"
git config --local myanyagent.installationId "$installation_id"
git config --local myanyagent.privateKey "$key_file"
git config --local --unset-all credential.helper >/dev/null 2>&1 || :
git config --local credential.helper ''
git config --local --add credential.helper "!node \"$helper\""

# Smoke test
credential_result=$(
  printf 'protocol=https\nhost=github.com\npath=%s.git\n\n' "$repository" |
    GIT_TERMINAL_PROMPT=0 git credential fill |
    node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const f=Object.fromEntries(s.trim().split(/\n/).map(x=>x.split(/=(.*)/s,2)));if(f.username!=="x-access-token"||!f.password)process.exit(1);process.stdout.write(f.username+"|"+f.password.length)})'
) || fail 'GitHub App credential smoke test failed (token mint error)'

case "$credential_result" in
  x-access-token\|[1-9]*) ;;
  *) fail "credential smoke test returned an unexpected result" ;;
esac

printf 'MyAnyAgent Git identity and repository-local App authentication configured.\n'
printf 'Credential smoke test: username=x-access-token, token length=%s.\n' "${credential_result#*|}"