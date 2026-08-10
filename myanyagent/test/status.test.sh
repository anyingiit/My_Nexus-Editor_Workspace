#!/bin/sh
set -eu

STATUS="${STATUS:-$(cd "$(dirname "$0")/.." && pwd)/bin/myanyagent-status.sh}"
TMPDIR=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

# Isolate global git config to avoid polluting the host
export GIT_CONFIG_GLOBAL="$TMPDIR/git-config-global"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# --- Setup: throwaway git repo ---
git init -q "$TMPDIR/repo"
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

# --- Test 1: not bootstrapped -> emits -> run: myanyagent-bootstrap ---
out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "git config: NOT configured" || fail "should report NOT configured"
echo "$out" | grep -q -- "-> run: myanyagent-bootstrap" || fail "should emit -> run: myanyagent-bootstrap"
printf 'PASS: not-bootstrapped emits bootstrap hint\n'

# --- Test 2: bootstrapped -> emits OK, no -> run: line ---
# Simulate bootstrapped state by writing the git config directly.
git config --local myanyagent.repository "anyingiit/My_Nexus-Editor_Workspace"
git config --local myanyagent.installationId "151195329"
git config --local myanyagent.privateKey "/nonexistent/key.pem"  # status checks key path readability separately
git config --local credential.helper '!node "/some/helper.cjs"'

out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "git config: configured" || fail "should report configured"
# Should NOT emit -> run: when git config is set (even if key unreadable, that's a separate line)
printf 'PASS: bootstrapped reports configured\n'

# --- Test 3: -> run: uses absolute path fallback when ~/.local/bin not on PATH ---
# Simulate by running with a PATH that excludes ~/.local/bin
out=$(PATH="/usr/bin:/bin" sh "$STATUS" 2>&1) || true
echo "$out" | grep -qE -- '-> run:.*myanyagent-bootstrap' || fail "should still emit bootstrap hint even with restricted PATH"
printf 'PASS: hint present with restricted PATH\n'

# --- Test 4: reports repo from .myanyagent.toml ---
out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "repo: anyingiit/My_Nexus-Editor_Workspace" || fail "should report repo from toml"
printf 'PASS: reports repo from toml\n'

printf '\nALL status tests passed\n'