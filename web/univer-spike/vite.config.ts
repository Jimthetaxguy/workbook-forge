import { defineConfig } from 'vite';

export default defineConfig({
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
    ],
  },
});
