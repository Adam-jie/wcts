import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'serve' ? '/' : '/static/hardware-app/',
  build: { outDir: '../web/hardware-app', emptyOutDir: true, sourcemap: false,
    rolldownOptions: { input: { hardware: fileURLToPath(new URL('./index.html', import.meta.url)), webTesting: fileURLToPath(new URL('./web-testing.html', import.meta.url)), engineering: fileURLToPath(new URL('./engineering.html', import.meta.url)) } } },
  server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:8765' } },
}));
