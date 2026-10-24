// Report raw and gzip sizes of dist/ (run after `vite build`). Lazily loaded
// docs hyphenation dictionaries are listed in aggregate; they load only for
// their languages.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const dir = new URL('../dist/assets/', import.meta.url);
const rows = readdirSync(dir).map((name) => {
  const bytes = readFileSync(new URL(name, dir));
  return { name, raw: statSync(new URL(name, dir)).size, gzip: gzipSync(bytes, { level: 9 }).length };
});
const named = (re) => rows.filter((r) => re.test(r.name));
const sum = (list) => list.reduce((a, r) => ({ raw: a.raw + r.raw, gzip: a.gzip + r.gzip }), { raw: 0, gzip: 0 });
const kb = ({ raw, gzip }) => `${(raw / 1024).toFixed(0)} KiB raw / ${(gzip / 1024).toFixed(0)} KiB gzip`;
const groups = {
  'main JS (Univer core, sheets, UI, adapter)': named(/^index-.*\.js$/),
  'main CSS': named(/^index-.*\.css$/),
  'lazy formula plugins (sheets-formula + UI) JS+CSS': named(/^formula-plugins-/),
  'Forge worker JS': named(/^worker-.*\.js$/),
  'Forge WASM': named(/\.wasm$/),
};
const counted = new Set(Object.values(groups).flat());
groups['lazy hyphenation dictionaries (not loaded for en-US)'] = rows.filter((r) => !counted.has(r));
for (const [label, list] of Object.entries(groups)) console.log(`${label}: ${kb(sum(list))} (${list.length} files)`);
const initial = sum([...groups['main JS (Univer core, sheets, UI, adapter)'], ...groups['main CSS'], ...groups['lazy formula plugins (sheets-formula + UI) JS+CSS'], ...groups['Forge worker JS'], ...groups['Forge WASM']]);
console.log(`loaded at startup in a formula mode (excl. hyphenation): ${kb(initial)}`);
