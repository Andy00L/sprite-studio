#!/usr/bin/env node
// scripts/preflight.mjs
//
// Verify the dev environment is ready. Prints PASS / WARN / FAIL per
// check with a short fix instruction. Exits 0 when no FAILs (warnings
// are non-blocking), 1 when at least one check fails.
//
// Used by `npm run check` and intended to be run by every new contributor
// after `npm run setup`.

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { homedir, platform } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..');
const HERMES_HOME = process.env.HERMES_HOME ?? resolve(homedir(), '.hermes');

const C = {
  reset: '\x1b[0m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  dim: '\x1b[2m',
};
const tty = process.stdout.isTTY;
const paint = (color, s) => (tty ? `${color}${s}${C.reset}` : s);

const results = [];
function record(name, status, detail = '', fix = '') {
  results.push({ name, status, detail, fix });
}

// Compare two semver-shaped strings ("a >= b"). Missing components are
// treated as 0 so "20" satisfies ">=20.19.0" only when it is "20.19.0+".
function semverGte(a, b) {
  const pa = a.split('.').map((s) => parseInt(s, 10) || 0);
  const pb = b.split('.').map((s) => parseInt(s, 10) || 0);
  for (let i = 0; i < 3; i += 1) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x > y) return true;
    if (x < y) return false;
  }
  return true;
}

// Read the engines.node range from this package.json and pick the lowest
// floor expressed in it. Vite-style "^20.19.0 || >=22.12.0" is satisfied
// when the running Node satisfies any branch; the cheapest correct check
// is "satisfies the lowest floor" which is 20.19.0 in that example.
function lowestNodeFloor(range) {
  const branches = range.split('||').map((s) => s.trim());
  let lowest = null;
  for (const b of branches) {
    const m = b.match(/(\d+)\.(\d+)\.(\d+)/);
    if (!m) continue;
    const v = `${m[1]}.${m[2]}.${m[3]}`;
    if (lowest === null || semverGte(lowest, v)) {
      // semverGte(lowest, v): "lowest >= v" means v is a new lower bound.
      lowest = v;
    }
  }
  return lowest ?? '0.0.0';
}

// Decide whether the running Node satisfies the multi-branch range. Each
// branch may be `^x.y.z` (caret: same major, >= floor) or `>=x.y.z`.
function nodeSatisfies(cur, range) {
  const branches = range.split('||').map((s) => s.trim());
  for (const b of branches) {
    const caret = b.match(/^\^(\d+)\.(\d+)\.(\d+)$/);
    if (caret) {
      const major = caret[1];
      const floor = `${major}.${caret[2]}.${caret[3]}`;
      const curMajor = cur.split('.')[0];
      if (curMajor === major && semverGte(cur, floor)) return true;
      continue;
    }
    const gte = b.match(/^>=\s*(\d+)\.(\d+)\.(\d+)$/);
    if (gte) {
      const floor = `${gte[1]}.${gte[2]}.${gte[3]}`;
      if (semverGte(cur, floor)) return true;
    }
  }
  return false;
}

// 1. Node version
function checkNode() {
  try {
    const pkgPath = resolve(repoRoot, 'package.json');
    const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
    const required = pkg.engines?.node ?? '>=20.19.0';
    const cur = process.versions.node;
    if (nodeSatisfies(cur, required)) {
      record('node', 'PASS', `${cur} satisfies ${required}`);
    } else {
      const floor = lowestNodeFloor(required);
      record(
        'node',
        'FAIL',
        `${cur} does not satisfy ${required}`,
        `Install Node ${floor}+ from https://nodejs.org or use nvm: nvm install ${floor}`,
      );
    }
  } catch (e) {
    record('node', 'FAIL', e.message, 'Could not read root package.json');
  }
}

// 2. Python 3.11+ on PATH
function checkPython() {
  if (platform() === 'win32') {
    record(
      'python',
      'FAIL',
      'Windows-native unsupported',
      'Use WSL2 with python3.11+ inside.',
    );
    return;
  }
  const probe =
    'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")';
  const candidates = ['python3.11', 'python3.12', 'python3.13', 'python3', 'python'];
  for (const cmd of candidates) {
    try {
      const out = execFileSync(cmd, ['-c', probe], {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
      }).trim();
      if (semverGte(out, '3.11.0')) {
        record('python', 'PASS', `${cmd} ${out}`);
        return;
      }
    } catch {
      // try next candidate
    }
  }
  record(
    'python',
    'FAIL',
    'no python>=3.11 found on PATH',
    'Install python 3.11 (apt install python3.11 / brew install python@3.11). The Hermes venv depends on it.',
  );
}

// 3. Hermes venv
function checkHermesVenv() {
  const p = resolve(HERMES_HOME, 'hermes-agent/venv/bin/python3');
  if (existsSync(p)) {
    record('hermes-venv', 'PASS', p);
  } else {
    record(
      'hermes-venv',
      'FAIL',
      p,
      'Install Hermes Agent (https://github.com/NousResearch/hermes-agent) so the venv exists at this path. Or set HERMES_HOME to a different prefix.',
    );
  }
}

// 4 + 5. ~/.hermes/.env and API_SERVER_KEY
function checkEnv() {
  const envPath = resolve(HERMES_HOME, '.env');
  if (!existsSync(envPath)) {
    record(
      'hermes-env',
      'FAIL',
      envPath,
      `Create the file with at minimum: API_SERVER_KEY=$(openssl rand -hex 16)`,
    );
    return;
  }
  try {
    const content = readFileSync(envPath, 'utf8');
    const m = content.match(/^[ \t]*API_SERVER_KEY[ \t]*=[ \t]*(.+?)[ \t]*$/m);
    const value = m ? m[1].replace(/^['"]|['"]$/g, '').trim() : '';
    if (!m || !value) {
      record(
        'hermes-env',
        'FAIL',
        `${envPath} (missing or empty API_SERVER_KEY)`,
        `Add API_SERVER_KEY=$(openssl rand -hex 16) to ${envPath}.`,
      );
      return;
    }
    record('hermes-env', 'PASS', `${envPath} (API_SERVER_KEY set)`);
  } catch (e) {
    record('hermes-env', 'FAIL', e.message, `Cannot read ${envPath}`);
  }
}

// 6. Plugin path
function checkPlugin() {
  const p =
    process.env.SPRITE_PLUGIN_PATH ??
    resolve(HERMES_HOME, 'plugins/sprite-studio');
  if (existsSync(resolve(p, '__init__.py'))) {
    record('plugin', 'PASS', p);
  } else {
    record(
      'plugin',
      'FAIL',
      p,
      `Symlink or install the sprite-studio plugin into ${p}. The plugin's __init__.py must exist there.`,
    );
  }
}

// 7. Web deps
function checkWebDeps() {
  const nm = resolve(repoRoot, 'web/node_modules');
  if (existsSync(nm)) {
    record('web-deps', 'PASS', nm);
  } else {
    record(
      'web-deps',
      'WARN',
      'web/node_modules missing',
      'Run: npm run setup',
    );
  }
}

// 8. Ports
async function checkPort(port, name) {
  return new Promise((resolveP) => {
    const srv = createServer();
    srv.once('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        record(
          `port-${port}`,
          'WARN',
          `${name} (${port}) is in use`,
          `Stop the existing process on port ${port} or expect a bind failure when running npm run dev.`,
        );
      } else {
        record(
          `port-${port}`,
          'WARN',
          `${name} (${port}) check failed: ${err.message}`,
        );
      }
      resolveP();
    });
    srv.once('listening', () => {
      srv.close(() => {
        record(`port-${port}`, 'PASS', `${name} (${port}) free`);
        resolveP();
      });
    });
    srv.listen(port, '127.0.0.1');
  });
}

async function main() {
  checkNode();
  checkPython();
  checkHermesVenv();
  checkEnv();
  checkPlugin();
  checkWebDeps();
  await checkPort(8643, 'bridge');
  await checkPort(9120, 'asset');
  await checkPort(5173, 'vite');

  let failed = 0;
  let warned = 0;
  for (const r of results) {
    const tag =
      r.status === 'PASS'
        ? paint(C.green, ' PASS')
        : r.status === 'WARN'
          ? paint(C.yellow, ' WARN')
          : paint(C.red, ' FAIL');
    console.log(`${tag}  ${r.name.padEnd(14)} ${paint(C.dim, r.detail)}`);
    if (r.status === 'FAIL') {
      console.log(`        ${paint(C.dim, '-> ' + r.fix)}`);
      failed += 1;
    } else if (r.status === 'WARN') {
      if (r.fix) console.log(`        ${paint(C.dim, '-> ' + r.fix)}`);
      warned += 1;
    }
  }

  console.log('');
  if (failed > 0) {
    console.log(
      paint(C.red, `${failed} check(s) failed.`) +
        ' Fix the items above and re-run npm run check.',
    );
    process.exit(1);
  } else if (warned > 0) {
    console.log(
      paint(C.yellow, `${warned} warning(s).`) +
        ' Dev should still work. Address as needed.',
    );
    process.exit(0);
  } else {
    console.log(paint(C.green, 'all checks passed.') + ' Run: npm run dev');
    process.exit(0);
  }
}

main().catch((e) => {
  console.error('preflight crashed:', e);
  process.exit(2);
});
