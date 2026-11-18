import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// In development, Vite serves the interface and sends /api to the web process.
// In production, the web process serves the built interface from dist/ itself.
const API = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': API } },
  // A test may take up to 30 seconds on a busy machine, not Vitest's 5.
  test: { environment: 'jsdom', setupFiles: ['test/setup.ts'], testTimeout: 30_000 },
});
