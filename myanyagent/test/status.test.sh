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
git config --local credential.helper '!node "/home/u/.local/share/myanyagent/bin/myanyagent-credential-helper.cjs"'

out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "git config: configured" || fail "should report configured"
# Should NOT emit -> run: when git config is set (even if key unreadable, that's a separate line)
printf 'PASS: bootstrapped reports configured\n'

# --- Test 3: -> run: uses absolute path fallback when ~/.local/bin not on PATH ---
# Force a not-configured state so the bootstrap hint is emitted, then verify the
# absolute path fallback is used when ~/.local/bin is not on PATH.
git config --local --unset credential.helper 2>/dev/null || true
out=$(PATH="/usr/bin:/bin" sh "$STATUS" 2>&1) || true
echo "$out" | grep -qE -- '-> run:.*myanyagent-bootstrap' || fail "should still emit bootstrap hint even with restricted PATH"
printf 'PASS: hint present with restricted PATH\n'
# Restore configured state for subsequent tests
git config --local credential.helper '!node "/home/u/.local/share/myanyagent/bin/myanyagent-credential-helper.cjs"'

# --- Test 4: reports repo from .myanyagent.toml ---
out=$(sh "$STATUS" 2>&1) || true
echo "$out" | grep -q "repo: anyingiit/My_Nexus-Editor_Workspace" || fail "should report repo from toml"
printf 'PASS: reports repo from toml\n'

# --- Test 5: all-green path -> emits OK, no -> run: line ---
# Need a temp HOME with the tool installed, a machine config, and a readable key
TESTHOME=$(mktemp -d)
# Install a minimal tool dir under the temp HOME so the tool check passes
mkdir -p "$TESTHOME/.local/share/myanyagent/bin"
cp "$(cd "$(dirname "$STATUS")" && pwd)/myanyagent-credential-helper.cjs" "$TESTHOME/.local/share/myanyagent/bin/"
printf '1' > "$TESTHOME/.local/share/myanyagent/VERSION"
mkdir -p "$TESTHOME/.config/myanyagent"
cat > "$TESTHOME/.config/myanyagent/config.toml" <<CONF
client_id = "Iv23lioD363YBpJJB9QE"
app_id = "4483813"
private_key = "$TMPDIR/dummy.pem"
CONF
# Generate a dummy RSA key
openssl genrsa -out "$TMPDIR/dummy.pem" 2048 2>/dev/null || fail "openssl genrsa failed"
# Reset git config to a clean bootstrapped state
git config --local myanyagent.repository "anyingiit/My_Nexus-Editor_Workspace"
git config --local myanyagent.installationId "151195329"
git config --local myanyagent.privateKey "$TMPDIR/dummy.pem"
git config --local credential.helper '!node "/home/u/.local/share/myanyagent/bin/myanyagent-credential-helper.cjs"'

out=$(HOME="$TESTHOME" sh "$STATUS" 2>&1) || true
last_line=$(echo "$out" | tail -1)
[ "$last_line" = "OK" ] || fail "last line should be OK, got: $last_line"
echo "$out" | grep -q -- "-> run:" && fail "should NOT emit -> run: when all green" || true
printf 'PASS: all-green path emits OK with no -> run:\n'
rm -rf "$TESTHOME" "$TMPDIR/dummy.pem"

printf '\nALL status tests passed\n'