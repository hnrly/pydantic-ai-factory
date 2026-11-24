import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// In development, Vite serves the interface and sends /api to the board's web process.
// A build goes into the library, which serves it from pydantic_ai_factory/serve/_static/
// and ships it in the wheel.
const API = 'http://127.0.0.1:8000';
const STATIC = '../../src/pydantic_ai_factory/serve/_static'; // relative to this folder

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': API } },
  build: { outDir: STATIC, emptyOutDir: true },
  // A test may take up to 30 seconds on a busy machine, not Vitest's 5.
  test: { environment: 'jsdom', setupFiles: ['test/setup.ts'], testTimeout: 30_000 },
});
