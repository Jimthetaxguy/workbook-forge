import { defineConfig } from 'vite';

export default defineConfig({
  server: { fs: { allow: ['..', '../../fixtures'] } },
  assetsInclude: ['**/*.xlsx'],
  worker: { format: 'es' },
  build: { target: 'es2022', chunkSizeWarningLimit: 8000, assetsInlineLimit: 0 },
  optimizeDeps: { exclude: ['@workbook-forge/engine'] },
});
