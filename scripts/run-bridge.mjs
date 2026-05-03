#!/usr/bin/env node
// scripts/run-bridge.mjs
//
// Locate the Hermes venv python and exec bridge/server.py with proper
// signal forwarding. Fails fast with a fixable error if the venv is
// missing.
//
// Why a Node wrapper instead of a shell command:
//  - Portable across macOS / Linux / WSL2 (no `set -o pipefail` quoting
//    differences, no bash-3 vs bash-5 syntax traps).
//  - Reliable POSIX signal forwarding from concurrently to Node to Python.
//  - Clear error messages when a prerequisite is missing.

import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..');

const HERMES_HOME = process.env.HERMES_HOME ?? resolve(homedir(), '.hermes');
const venvPython = resolve(HERMES_HOME, 'hermes-agent/venv/bin/python3');
const bridgeScript = resolve(repoRoot, 'bridge', 'server.py');
const dotenvPath = resolve(HERMES_HOME, '.env');

function fatal(msg) {
  for (const line of msg.split('\n')) {
    process.stderr.write(`[run-bridge] ${line}\n`);
  }
  process.exit(1);
}

if (platform() === 'win32') {
  fatal(
    'Windows-native is not supported.\n' +
      'The Hermes plugin requires Unix paths. Use WSL2.',
  );
}

if (!existsSync(venvPython)) {
  fatal(
    `Hermes venv python not found at:\n  ${venvPython}\n` +
      'Install Hermes per its docs, then run:\n  npm run check',
  );
}

if (!existsSync(bridgeScript)) {
  fatal(`bridge script not found at: ${bridgeScript}`);
}

// Parse ~/.hermes/.env into a plain key=value map and merge into the child
// env. Mirrors what `set -a; source ~/.hermes/.env; set +a` did in the old
// run.sh, but without spawning a bash subshell. The bridge itself only
// hard-requires API_SERVER_KEY (which it self-loads), but plugin handlers
// downstream may need any of the other keys (provider tokens etc.).
//
// Parsing rules:
//  - skip blank lines and lines starting with `#`
//  - split on first `=`
//  - strip a single layer of matching outer single or double quotes
//  - leave `process.env` overrides intact (don't clobber what the user set)
function loadDotenv(path) {
  const env = {};
  if (!existsSync(path)) return env;
  const text = readFileSync(path, 'utf8');
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\r$/, '').trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    if (!key || /[^A-Za-z0-9_]/.test(key)) continue;
    let value = line.slice(eq + 1).trim();
    if (
      value.length >= 2 &&
      value[0] === value[value.length - 1] &&
      (value[0] === '"' || value[0] === "'")
    ) {
      value = value.slice(1, -1);
    }
    env[key] = value;
  }
  return env;
}

const dotenv = loadDotenv(dotenvPath);
const childEnv = { ...dotenv, ...process.env, PYTHONUNBUFFERED: '1' };

const child = spawn(venvPython, [bridgeScript], {
  stdio: 'inherit',
  env: childEnv,
});

child.on('error', (err) => {
  fatal(`failed to spawn python: ${err.message}`);
});

child.on('exit', (code, signal) => {
  if (signal) {
    // Re-raise the signal on self so the parent observes the conventional
    // exit (e.g. 128 + SIGINT = 130 for Ctrl-C).
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 0);
  }
});

const forward = (sig) => {
  if (!child.killed) child.kill(sig);
};
process.on('SIGINT', () => forward('SIGINT'));
process.on('SIGTERM', () => forward('SIGTERM'));
process.on('SIGHUP', () => forward('SIGHUP'));
