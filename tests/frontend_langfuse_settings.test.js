const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const appJs = fs.readFileSync(path.join(__dirname, "..", "static", "js", "app.js"), "utf8");

assert(appJs.includes('name="langfuse_public_key"'), "settings dialog should render Langfuse public key field");
assert(appJs.includes('name="langfuse_secret_key"'), "settings dialog should render Langfuse secret key field");
assert(appJs.includes('name="langfuse_host"'), "settings dialog should render Langfuse host field");
assert(appJs.includes("if (langfusePublicKey)"), "empty Langfuse public key input must not overwrite existing key");
assert(appJs.includes("if (langfuseSecretKey)"), "empty Langfuse secret key input must not overwrite existing key");
assert(appJs.includes("secrets.langfuse_enabled"), "settings save should submit Langfuse enabled flag");

console.log("frontend_langfuse_settings.test.js passed");
