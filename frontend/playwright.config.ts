import { defineConfig, devices } from '@playwright/test';

/**
 * Phase 4E: Playwright E2E config.
 *
 * Boots two webServers in CI / local `npm run e2e`:
 *   1. Backend (uvicorn) with LLM_MOCK=1 and a scratch data dir so the run
 *      is hermetic — no real OpenAI calls, no clobbering dev data.
 *   2. Frontend (vite dev) on 5173 with /api proxied to the E2E backend.
 *
 * Tests target the frontend port; the proxy forwards API calls to the
 * backend. Redis must already be running (CI brings it up as a service
 * container; locally the user starts it).
 *
 * All env vars are passed via the `env` field rather than inline shell
 * syntax (`LLM_MOCK=1 uvicorn ...`) so the same command works on POSIX
 * shells and Windows cmd.
 */
const BACKEND_PORT = 8765;
const FRONTEND_PORT = 5173;
const SCRATCH_DIR = '.e2e-data';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // shared backend + sqlite files; serialize for safety
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: 'on-first-retry',
    // Auth bootstrap hits /api/auth/me on every page load; give the backend
    // a generous timeout since cold starts can be slow on CI runners.
    actionTimeout: 10_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      // `cd ../backend` because Playwright runs this from frontend/.
      // Env vars go in the `env` map so the command works on Windows too.
      command: `cd ../backend && uvicorn app.main:app --port ${BACKEND_PORT}`,
      url: `http://localhost:${BACKEND_PORT}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        PYTHONUNBUFFERED: '1',
        LLM_MOCK: '1',
        // Scratch data dir lives under frontend/ so .gitignore catches it.
        DATA_DIR: `../frontend/${SCRATCH_DIR}`,
        // DB 15 is reserved for E2E so we don't collide with dev sessions.
        REDIS_URL: 'redis://localhost:6379/15',
        JWT_SECRET: 'e2e-secret',
      },
    },
    {
      command: `npm run dev -- --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        // Route the vite proxy at our E2E backend instead of the dev 8000.
        VITE_API_PROXY_TARGET: `http://localhost:${BACKEND_PORT}`,
      },
    },
  ],
});
