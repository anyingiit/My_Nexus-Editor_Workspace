const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const CLIENT_ID = "Iv23lioD363YBpJJB9QE";
const INSTALLATION_ID = "151195329";
const REPOSITORY = "anyingiit/My_Nexus-Editor_Workspace";

const fields = {};
for (const line of fs.readFileSync(0, "utf8").split(/\r?\n/)) {
  const separator = line.indexOf("=");
  if (separator > 0) fields[line.slice(0, separator)] = line.slice(separator + 1);
}

if (process.argv[2] !== "get") process.exit(0);
if (fields.protocol !== "https" || fields.host !== "github.com" || fields.path !== `${REPOSITORY}.git`) {
  process.exit(0);
}

function localConfig(key) {
  try {
    return execFileSync("git", ["config", "--local", "--get", key], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"]
    }).trim();
  } catch {
    return "";
  }
}

const keyFile = process.env.MYANYAGENT_PRIVATE_KEY ||
  localConfig("myanyagent.privateKey") ||
  path.join(os.homedir(), ".secrets", "myanyagent.2026-08-04.private-key.pem");
const key = fs.readFileSync(keyFile);
const b64 = value => Buffer.from(value).toString("base64url");
const now = Math.floor(Date.now() / 1000);
const header = b64(JSON.stringify({ alg: "RS256", typ: "JWT" }));
const payload = b64(JSON.stringify({ iat: now - 60, exp: now + 540, iss: CLIENT_ID }));
const input = `${header}.${payload}`;
const jwt = `${input}.${crypto.createSign("RSA-SHA256").update(input).sign(key, "base64url")}`;

(async () => {
  const response = await fetch(`https://api.github.com/app/installations/${INSTALLATION_ID}/access_tokens`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28"
    },
    body: JSON.stringify({ repositories: [REPOSITORY.split("/")[1]] })
  });
  const body = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${body.message || "GitHub App token request failed"}`);
  if (body.permissions?.contents !== "write") throw new Error("GitHub App token lacks contents:write");
  process.stdout.write(`username=x-access-token\npassword=${body.token}\n`);
})().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
