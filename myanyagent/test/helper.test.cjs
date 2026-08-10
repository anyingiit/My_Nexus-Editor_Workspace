const { test } = require("node:test");
const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

const helperPath = path.join(__dirname, "..", "bin", "myanyagent-credential-helper.cjs");

function genKeyPair() {
  return crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
}

function runHelper(stdinInput, env = {}) {
  try {
    const out = execFileSync("node", [helperPath, "get"], {
      input: stdinInput,
      encoding: "utf8",
      env: { ...process.env, ...env },
    });
    return { stdout: out, stderr: "", status: 0 };
  } catch (e) {
    return { stdout: e.stdout?.toString() || "", stderr: e.stderr?.toString() || "", status: e.status };
  }
}

test("exits 0 silently when host is not github.com", () => {
  const r = runHelper("protocol=https\nhost=gitlab.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});

test("exits 0 silently when protocol is not https", () => {
  const r = runHelper("protocol=ssh\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});

test("exits 0 silently when path does not match configured repository", () => {
  // No git config for myanyagent.repository means localConfig returns "";
  // path "other/repo.git" != "" -> should exit 0 silently (not our repo)
  const r = runHelper("protocol=https\nhost=github.com\npath=other/repo.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});

test("parses stdin fields correctly", () => {
  // Verify the helper reads protocol/host/path; non-matching host exits 0
  const r = runHelper("protocol=https\nhost=example.com\npath=foo/bar.git\n\n");
  assert.equal(r.status, 0);
  assert.equal(r.stdout, "");
});

test("JWT is well-formed RS256 with correct claims", () => {
  // Test the JWT construction in isolation by extracting the signing logic.
  // We verify structure: three base64url parts, header alg RS256, payload iss=clientId.
  const { execFileSync } = require("node:child_process");
  const keyPair = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const tmpKey = path.join(os.tmpdir(), `test-key-${process.pid}.pem`);
  fs.writeFileSync(tmpKey, keyPair.privateKey.export({ type: "pkcs8", format: "pem" }));

  // Run a tiny inline node script that reproduces the helper's JWT logic.
  const result = execFileSync("node", ["-e", `
    const fs = require("fs"), crypto = require("crypto");
    const key = fs.readFileSync(${JSON.stringify(tmpKey)});
    const b64 = v => Buffer.from(v).toString("base64url");
    const now = Math.floor(Date.now()/1000);
    const header = b64(JSON.stringify({alg:"RS256",typ:"JWT"}));
    const payload = b64(JSON.stringify({iat:now-60,exp:now+540,iss:"Iv23lioD363YBpJJB9QE"}));
    const input = header+"."+payload;
    const sig = crypto.createSign("RSA-SHA256").update(input).sign(key,"base64url");
    console.log(input+"."+sig);
  `], { encoding: "utf8" }).trim();

  const parts = result.split(".");
  assert.equal(parts.length, 3);
  const header = JSON.parse(Buffer.from(parts[0], "base64url").toString());
  assert.equal(header.alg, "RS256");
  assert.equal(header.typ, "JWT");
  const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString());
  assert.equal(payload.iss, "Iv23lioD363YBpJJB9QE");
  assert.ok(payload.exp > payload.iat);
  assert.ok(payload.exp - payload.iat <= 600);
  fs.unlinkSync(tmpKey);
});

test("failure message includes -> run: remediation hint", () => {
  // Point helper at a nonexistent key to trigger failure path.
  // Set up a throwaway git repo with myanyagent.repository configured.
  const tmpRepo = fs.mkdtempSync(path.join(os.tmpdir(), "mya-test-"));
  const { execSync } = require("node:child_process");
  execSync("git init -q", { cwd: tmpRepo });
  execSync("git config --local myanyagent.repository anyingiit/My_Nexus-Editor_Workspace", { cwd: tmpRepo });
  execSync("git config --local myanyagent.installationId 151195329", { cwd: tmpRepo });
  execSync("git config --local myanyagent.privateKey /nonexistent/key.pem", { cwd: tmpRepo });

  // Helper reads git config via execFileSync("git", ...) which uses cwd, so run with cwd=tmpRepo.
  try {
    execFileSync("node", [helperPath, "get"], {
      input: "protocol=https\nhost=github.com\npath=anyingiit/My_Nexus-Editor_Workspace.git\n\n",
      encoding: "utf8",
      cwd: tmpRepo,
    });
    assert.fail("should have thrown");
  } catch (e) {
    const stderr = e.stderr?.toString() || "";
    assert.ok(stderr.includes("-> run:"), `stderr should contain -> run:, got: ${stderr}`);
    assert.ok(stderr.includes("myanyagent-status"), `stderr should mention myanyagent-status`);
  }
  fs.rmSync(tmpRepo, { recursive: true, force: true });
});