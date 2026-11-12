import { readFileSync } from 'node:fs';
import { defineConfig, type Plugin } from 'vite';

/**
 * `virtual:forge-catalog`: the functions the engines implement, read from the
 * repo's catalog at build time, so the function reference never drifts from
 * what Forge calculates. Only names, categories and argument counts are
 * bundled; the catalog's specs and source records stay out of the page.
 */
function forgeCatalog(): Plugin {
  const id = 'virtual:forge-catalog';
  const read = (name: string) => JSON.parse(readFileSync(new URL(`../../catalog/${name}`, import.meta.url), 'utf8'));
  return {
    name: 'forge-catalog',
    resolveId: (source) => (source === id ? `\0${id}` : null),
    load(resolved) {
      if (resolved !== `\0${id}`) return null;
      const catalog = read('formulas.json') as {
        catalog_version: string;
        functions: { name: string; category: string; arity: { min: number; max: number | null } }[];
      };
      const inventory = read('function_inventory.json') as { functions: unknown[] };
      const functions = catalog.functions.map(({ name, category, arity }) => ({ name, category, min: arity.min, max: arity.max ?? null }));
      return `export default ${JSON.stringify({ version: catalog.catalog_version, inventoried: inventory.functions.length, functions })};`;
    },
  };
}

export default defineConfig({
  plugins: [forgeCatalog()],
  server: { fs: { allow: ['..', '../../fixtures'] } },
  assetsInclude: ['**/*.xlsx'],
  worker: { format: 'es' },
  build: { target: 'es2022', chunkSizeWarningLimit: 8000, assetsInlineLimit: 0 },
  optimizeDeps: {
    exclude: ['@workbook-forge/engine'],
    // Pre-bundle the lazily imported formula stack so the dev server does not
    // re-optimize (and reload) the first time a formula mode is opened.
    include: [
      '@univerjs/engine-formula',
      '@univerjs/engine-formula/facade',
      '@univerjs/sheets-formula',
      '@univerjs/sheets-formula/facade',
      '@univerjs/sheets-formula-ui',
      '@univerjs/sheets-numfmt',
    ],
  },
});
