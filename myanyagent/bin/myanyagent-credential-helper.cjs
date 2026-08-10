const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

function localConfig(key) {
  try {
    return execFileSync("git", ["config", "--local", "--get", key], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}

function machineConfig(key) {
  const file = path.join(os.homedir(), ".config", "myanyagent", "config.toml");
  if (!fs.existsSync(file)) return "";
  const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const m = line.match(new RegExp(`^\\s*${key}\\s*=\\s*"([^"]+)"`));
    if (m) return m[1];
  }
  return "";
}

async function mintToken(repository, installationId, clientId, keyFile) {
  const key = fs.readFileSync(keyFile);
  const b64 = (v) => Buffer.from(v).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const header = b64(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = b64(JSON.stringify({ iat: now - 60, exp: now + 540, iss: clientId }));
  const input = `${header}.${payload}`;
  const jwt = `${input}.${crypto.createSign("RSA-SHA256").update(input).sign(key, "base64url")}`;

  const response = await fetch(
    `https://api.github.com/app/installations/${installationId}/access_tokens`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${jwt}`,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ repositories: [repository.split("/")[1]] }),
    }
  );
  const body = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${body.message || "GitHub App token request failed"}`);
  if (body.permissions?.contents !== "write") throw new Error("GitHub App token lacks contents:write");
  return { username: "x-access-token", password: body.token };
}

function fail(msg) {
  console.error(`myanyagent: ${msg}`);
  console.error("-> run: myanyagent-status   (inspect)   or   myanyagent-bootstrap   (reconfigure)");
  process.exitCode = 1;
}

if (require.main === module) {
  const fields = {};
  for (const line of fs.readFileSync(0, "utf8").split(/\r?\n/)) {
    const sep = line.indexOf("=");
    if (sep > 0) fields[line.slice(0, sep)] = line.slice(sep + 1);
  }

  if (process.argv[2] !== "get") process.exit(0);

  const repository = localConfig("myanyagent.repository");
  const installationId = localConfig("myanyagent.installationId");

  if (fields.protocol !== "https" || fields.host !== "github.com") process.exit(0);
  if (fields.path !== `${repository}.git`) process.exit(0);

  const clientId = machineConfig("client_id");
  const keyFile = process.env.MYANYAGENT_PRIVATE_KEY ||
    localConfig("myanyagent.privateKey") ||
    path.join(os.homedir(), ".secrets", "myanyagent.2026-08-04.private-key.pem");

  if (!clientId) { fail("client_id not found in ~/.config/myanyagent/config.toml"); return; }
  if (!installationId) { fail("myanyagent.installationId not set in git config"); return; }
  if (!fs.existsSync(keyFile)) { fail(`private key missing: ${keyFile}`); return; }

  (async () => {
    const result = await mintToken(repository, installationId, clientId, keyFile);
    process.stdout.write(`username=${result.username}\npassword=${result.password}\n`);
  })().catch((error) => fail(error.message));
} else {
  module.exports = { mintToken };
}