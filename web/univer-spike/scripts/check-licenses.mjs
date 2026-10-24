// License gate for the spike's npm dependencies (the repo's Python test only
// covers Cargo.lock). Every installed package in package-lock.json must carry
// a permissive SPDX license; prints a summary and exits 1 otherwise.
import { readFileSync } from 'node:fs';

// The accepted list from README.md, "License and source policy". It applies to
// development dependencies too, so build tools are checked like runtime ones.
const PERMISSIVE = new Set(['MIT', 'MIT-0', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', '0BSD', 'ISC', 'Zlib', 'PSF-2.0', 'Unicode-3.0', 'Unicode-DFS-2016', 'BSL-1.0', 'CC0-1.0', 'Unlicense']);
// A dual-licensed package qualifies when one OR option is accepted; every AND
// term must be accepted.
const accepted = (expr) =>
  expr.replace(/[()]/g, '').split(/\s+OR\s+/).some((option) => option.split(/\s+AND\s+/).every((id) => PERMISSIVE.has(id.trim())));
const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'));
const counts = {};
const problems = [];
for (const [path, entry] of Object.entries(lock.packages)) {
  if (!path.startsWith('node_modules/')) continue;
  if (entry.link) continue; // the local @workbook-forge/engine package (MIT, this repo)
  const name = path.slice(path.lastIndexOf('node_modules/') + 13);
  let license = entry.license;
  try {
    license ??= JSON.parse(readFileSync(new URL(`../${path}/package.json`, import.meta.url), 'utf8')).license;
  } catch {}
  const ok = typeof license === 'string' && accepted(license);
  const key = `${license}${entry.dev ? ' (dev)' : ''}`;
  counts[key] = (counts[key] ?? 0) + 1;
  if (!ok) problems.push(`${name}@${entry.version}: ${license}`);
}
console.log(JSON.stringify(counts, null, 2));
if (problems.length) {
  console.error(`non-permissive or missing licenses:\n${problems.join('\n')}`);
  process.exit(1);
}
console.log('npm license check: all permissive');
