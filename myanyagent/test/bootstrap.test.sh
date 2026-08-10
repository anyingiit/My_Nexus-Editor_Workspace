#!/bin/sh
set -eu

BOOTS="${BOOTS:-$(cd "$(dirname "$0")/.." && pwd)/bin/myanyagent-bootstrap.sh}"
TMPDIR=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# --- Setup: throwaway git repo with .myanyagent.toml ---
git init -q "$TMPDIR/repo" || fail "git init failed"
cd "$TMPDIR/repo"
git config --global user.email "test@test.test" 2>/dev/null || true
git config --global user.name "Test" 2>/dev/null || true

cat > .myanyagent.toml <<EOF
repository = "anyingiit/My_Nexus-Editor_Workspace"
installation_id = "151195329"
[bot]
name = "MyAnyAgent[bot]"
email = "312959697+myanyagent[bot]@users.noreply.github.com"
EOF

git remote add origin "https://github.com/anyingiit/My_Nexus-Editor_Workspace.git" || fail "remote add failed"

# --- Test 1: rejects when origin URL does not match toml repository ---
git remote set-url origin "https://github.com/other/repo.git"
if sh "$BOOTS" 2>/dev/null; then
  fail "bootstrap should reject mismatched origin"
fi
printf 'PASS: mismatched origin rejected\n'

# --- Test 2: rejects without .myanyagent.toml ---
git remote set-url origin "https://github.com/anyingiit/My_Nexus-Editor_Workspace.git"
rm -f .myanyagent.toml
if sh "$BOOTS" 2>/dev/null; then
  fail "bootstrap should reject missing .myanyagent.toml"
fi
printf 'PASS: missing toml rejected\n'

# --- Test 3: rejects when private key missing ---
cat > .myanyagent.toml <<EOF
repository = "anyingiit/My_Nexus-Editor_Workspace"
installation_id = "151195329"
[bot]
name = "MyAnyAgent[bot]"
email = "312959697+myanyagent[bot]@users.noreply.github.com"
EOF
if MYANYAGENT_PRIVATE_KEY="/nonexistent/key.pem" sh "$BOOTS" 2>/dev/null; then
  fail "bootstrap should reject missing private key"
fi
printf 'PASS: missing private key rejected\n'

# --- Test 4: writes correct .git/config when key exists (dry-run: skip smoke test) ---
# Generate a dummy RSA key so the key-exists check passes.
KEY="$TMPDIR/dummy.pem"
openssl genrsa -out "$KEY" 2048 2>/dev/null || fail "openssl genrsa failed"

# We expect bootstrap to fail at the smoke test (token mint will fail with a dummy
# key against the real GitHub API), but we verify .git/config was written first.
# Run bootstrap and capture output; it should write config before smoke test.
MYANYAGENT_PRIVATE_KEY="$KEY" sh "$BOOTS" 2>"$TMPDIR/err" || true

# Verify git config was written
[ "$(git config --local user.name)" = "MyAnyAgent[bot]" ] || fail "user.name not set"
[ "$(git config --local user.email)" = "312959697+myanyagent[bot]@users.noreply.github.com" ] || fail "user.email not set"
[ "$(git config --local user.useConfigOnly)" = "true" ] || fail "useConfigOnly not set"
[ "$(git config --local commit.gpgsign)" = "false" ] || fail "gpgsign not set"
[ "$(git config --local credential.useHttpPath)" = "true" ] || fail "useHttpPath not set"
[ "$(git config --local myanyagent.repository)" = "anyingiit/My_Nexus-Editor_Workspace" ] || fail "myanyagent.repository not set"
[ "$(git config --local myanyagent.installationId)" = "151195329" ] || fail "myanyagent.installationId not set"
[ "$(git config --local myanyagent.privateKey)" = "$KEY" ] || fail "myanyagent.privateKey not set"
HELPER=$(git config --local credential.helper)
echo "$HELPER" | grep -q "myanyagent-credential-helper" || fail "credential.helper not set to helper path"
printf 'PASS: git config written correctly\n'

# --- Test 5: idempotent (second run produces same config) ---
MYANYAGENT_PRIVATE_KEY="$KEY" sh "$BOOTS" 2>/dev/null || true
[ "$(git config --local myanyagent.repository)" = "anyingiit/My_Nexus-Editor_Workspace" ] || fail "idempotent run broke repository"
[ "$(git config --local user.name)" = "MyAnyAgent[bot]" ] || fail "idempotent run broke user.name"
printf 'PASS: idempotent\n'

printf '\nALL bootstrap tests passed\n'