// Check src/function-notes.ts against catalog/formulas.json: every note names
// an implemented function, and its argument list agrees with the catalog's
// counts (required arguments = arity.min; without "..." the total = arity.max).
// Needs Node 22.6+ (type stripping), like web/forge-engine's tests.
import { readFileSync } from 'node:fs';
import { CATEGORY_LABELS, FUNCTION_NOTES } from '../src/function-notes.ts';

const catalog = JSON.parse(readFileSync(new URL('../../../catalog/formulas.json', import.meta.url), 'utf8'));
const byName = new Map(catalog.functions.map((fn) => [fn.name, fn]));
const problems = [];
for (const [name, note] of Object.entries(FUNCTION_NOTES)) {
  const fn = byName.get(name);
  if (!fn) {
    problems.push(`${name}: has a note but is not an implemented function in the catalog`);
    continue;
  }
  const parts = note.args.split(',').map((part) => part.trim()).filter(Boolean);
  const repeats = parts.at(-1) === '...';
  const named = repeats ? parts.slice(0, -1) : parts;
  const required = named.filter((part) => !part.startsWith('[')).length;
  if (required !== fn.arity.min) problems.push(`${name}: ${required} required arguments in the note, catalog says ${fn.arity.min}`);
  if (!repeats && named.length !== fn.arity.max) problems.push(`${name}: ${named.length} arguments in the note, catalog allows ${fn.arity.max}`);
  if (repeats && fn.arity.max !== null && fn.arity.max < 100) problems.push(`${name}: note repeats arguments, catalog stops at ${fn.arity.max}`);
  if (!/\.$/.test(note.summary)) problems.push(`${name}: summary must end with a period`);
}
const labelled = new Set(CATEGORY_LABELS.map(([id]) => id));
for (const id of catalog.categories) if (!labelled.has(id)) problems.push(`category ${id} has no label`);
const missing = catalog.functions.filter((fn) => !(fn.name in FUNCTION_NOTES)).map((fn) => fn.name);
console.log(`function notes: ${Object.keys(FUNCTION_NOTES).length} notes for ${catalog.functions.length} implemented functions`);
if (missing.length) console.log(`listed with argument counts only (no note yet): ${missing.join(', ')}`);
if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log('function notes: all agree with the catalog');
