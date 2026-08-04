#!/bin/sh
set -eu

fail() {
  printf 'bootstrap failed: %s\n' "$1" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail 'git is required'
command -v node >/dev/null 2>&1 || fail 'node.js is required'

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || fail 'run inside a Git repository'
cd "$repo_root"

target_url='https://github.com/anyingiit/My_Nexus-Editor_Workspace.git'
fetch_url=$(git remote get-url origin 2>/dev/null) || fail 'origin remote is missing'
push_url=$(git remote get-url --push origin 2>/dev/null) || fail 'origin push remote is missing'
[ "$fetch_url" = "$target_url" ] || fail "origin fetch URL must be $target_url"
[ "$push_url" = "$target_url" ] || fail "origin push URL must be $target_url"

key_file=${MYANYAGENT_PRIVATE_KEY:-"$HOME/.secrets/myanyagent.2026-08-04.private-key.pem"}
case "$key_file" in
  /*) ;;
  *) fail 'MYANYAGENT_PRIVATE_KEY must be an absolute path' ;;
esac
[ -r "$key_file" ] || fail "private key is missing or unreadable: $key_file"

helper="$repo_root/scripts/myanyagent-credential-helper.cjs"
[ -f "$helper" ] || fail "credential helper is missing: $helper"

git config --local user.name 'MyAnyAgent[bot]'
git config --local user.email '312959697+myanyagent[bot]@users.noreply.github.com'
git config --local user.useConfigOnly true
git config --local commit.gpgsign false
git config --local credential.useHttpPath true
git config --local myanyagent.privateKey "$key_file"
git config --local --unset-all credential.helper >/dev/null 2>&1 || :
git config --local credential.helper ''
git config --local --add credential.helper "!node \"$helper\""

credential_result=$(
  printf 'protocol=https\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n' |
    GIT_TERMINAL_PROMPT=0 git credential fill |
    node -e 'let s=""; process.stdin.on("data", d => s += d).on("end", () => { const f = Object.fromEntries(s.trim().split(/\n/).map(x => x.split(/=(.*)/s, 2))); if (f.username !== "x-access-token" || !f.password) process.exit(1); process.stdout.write(`${f.username}|${f.password.length}`); })'
) || fail 'GitHub App credential smoke test failed'

case "$credential_result" in
  x-access-token\|[1-9]*) ;;
  *) fail 'credential smoke test returned an unexpected result' ;;
esac

printf 'MyAnyAgent Git identity and repository-local App authentication configured.\n'
printf 'Credential smoke test: username=x-access-token, token length=%s.\n' "${credential_result#*|}"
