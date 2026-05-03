#!/usr/bin/env node
// scripts/setup.mjs
//
// First-time setup. Idempotent. Run once after cloning the repo and
// `npm install` at the root. Safe to re-run.
//
// What it does:
//  1. Copies .env.example to .env if .env is absent.
//  2. Runs `npm install` inside web/ so the Vite dev server can boot.

import { copyFileSync, existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..');

function step(msg) {
  console.log(`\n-> ${msg}`);
}

step('checking root .env');
const envPath = resolve(repoRoot, '.env');
const envExample = resolve(repoRoot, '.env.example');
if (existsSync(envPath)) {
  console.log('  .env already exists; leaving it alone');
} else if (existsSync(envExample)) {
  copyFileSync(envExample, envPath);
  console.log('  copied .env.example to .env');
  console.log('  edit .env if you want to override defaults');
} else {
  console.log(
    '  no .env.example found (acceptable; the bridge reads ~/.hermes/.env)',
  );
}

step('installing web dependencies (npm install in web/)');
const child = spawn('npm', ['install'], {
  cwd: resolve(repoRoot, 'web'),
  stdio: 'inherit',
  shell: false,
});

child.on('error', (err) => {
  console.error(`\nfailed to spawn npm: ${err.message}`);
  process.exit(1);
});

child.on('exit', (code) => {
  if (code !== 0) {
    console.error(`\nnpm install in web/ failed with exit ${code}.`);
    process.exit(code ?? 1);
  }
  console.log('\n-> done. Now run: npm run check && npm run dev');
});
