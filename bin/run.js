#!/usr/bin/env node

const { execSync, spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const SCRIPT = path.join(__dirname, "..", "connect.py");

function checkPython() {
  for (const cmd of ["python3", "python"]) {
    try {
      const ver = execSync(`${cmd} --version 2>&1`, { encoding: "utf-8" }).trim();
      if (ver.includes("3.")) return cmd;
    } catch {}
  }
  return null;
}

function checkDeps(python) {
  try {
    execSync(`${python} -c "import websockets, requests"`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function installDeps(python) {
  console.log("[*] Installing Python dependencies (websockets, requests)...");
  try {
    execSync(`${python} -m pip install websockets requests -q`, { stdio: "inherit" });
    return true;
  } catch {
    console.error("[!] Failed to install dependencies. Please run manually:");
    console.error(`    ${python} -m pip install websockets requests`);
    return false;
  }
}

const python = checkPython();
if (!python) {
  console.error("[!] Python 3 not found. Please install Python 3.10+");
  console.error("    https://www.python.org/downloads/");
  process.exit(1);
}

if (!checkDeps(python)) {
  if (!installDeps(python)) process.exit(1);
}

const args = process.argv.slice(2);
if (args.length === 0) {
  console.log(`
  OpenClaw Relay Connector
  ========================

  Usage:
    npx openclaw-relay-connect \\
      --relay https://your-relay.up.railway.app \\
      --link-code LINK_CODE \\
      --secret SECRET

  Optional:
    --gateway ws://127.0.0.1:18789    (OpenClaw Gateway address)
    --gateway-token YOUR_TOKEN         (OpenClaw Gateway token)

  Example (echo mode):
    npx openclaw-relay-connect --relay https://xxx.up.railway.app --link-code A7X9K2 --secret f3a8b1c2...
`);
  process.exit(0);
}

const child = spawn(python, ["-u", SCRIPT, ...args], {
  stdio: "inherit",
  env: { ...process.env },
});

child.on("exit", (code) => process.exit(code || 0));
