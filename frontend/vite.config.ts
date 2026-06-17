import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev-server proxy target defaults to 8000 (matches the project's
// backend dev port). Phase 4E E2E overrides this with 8765 to avoid
// clashing with a developer's running backend — and to keep the test
// backend's scratch DATA_DIR isolated from any real data.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
