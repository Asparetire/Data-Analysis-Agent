import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Phase 5E: performance baseline for non-LLM endpoints.
//
// What this script measures:
//   - /api/v1/health/ready   (no auth, pure dependency check)
//   - /api/v1/auth/login     (bcrypt hash dominates latency)
//   - /api/v1/datasources    (filesystem + sidecar JSON)
//   - /api/v1/datasources/{id}/preview  (SQLite SELECT)
//   - /api/v1/datasources/{id}/rows     (paginated + PII mask)
//
// What it does NOT measure:
//   - /api/v1/chat, /api/v1/chat/stream — LLM latency is provider-bound
//   - /api/v1/upload — I/O-bound, not a steady-VU workload
//
// Prerequisites:
//   1. Start backend in mock mode (no real LLM dependency):
//        cd backend
//        LLM_MOCK=1 JWT_SECRET=test-only-32-bytes-long-aaaaaaaaaa \
//          DATABASE_URL=sqlite:///./data/perf-main.db \
//          DATA_DIR=./data-perf \
//          uvicorn app.main:app --port 8000 &
//   2. Register a user + upload a CSV so /datasources + /rows have data:
//        # The setup() function below does this if BASE_URL + test creds
//        # are set; otherwise it skips the authenticated endpoints.
//
// Run:
//   k6 run perf/k6-baseline.js
//
// Env:
//   BASE_URL       — default http://localhost:8000
//   TEST_EMAIL     — default perf@example.com (must exist)
//   TEST_PASSWORD  — default perf-password-123
//   TEST_DATASOURCE_ID — the file_id to hit /preview + /rows on

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const TEST_EMAIL = __ENV.TEST_EMAIL || 'perf@example.com';
const TEST_PASSWORD = __ENV.TEST_PASSWORD || 'perf-password-123';
const TEST_DS = __ENV.TEST_DATASOURCE_ID || '';

// Custom metrics so the summary is readable.
const loginLatency = new Trend('login_latency', true);
const loginFailRate = new Rate('login_failures');

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-vus',
      vus: 20,
      duration: '30s',
    },
  },
  thresholds: {
    // p95 targets. /auth/login gets more headroom because bcrypt is
    // intentionally slow (~100ms per hash on commodity hardware).
    'http_req_duration{endpoint:health}': ['p(95)<200'],
    'http_req_duration{endpoint:login}': ['p(95)<1500'],
    'http_req_duration{endpoint:datasources}': ['p(95)<500'],
    'http_req_duration{endpoint:preview}': ['p(95)<500'],
    'http_req_duration{endpoint:rows}': ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
    login_failures: ['rate<0.01'],
  },
};

let token = '';

export function setup() {
  // Try to log in once to get a token for the authenticated endpoints.
  // If login fails (user doesn't exist), we skip those endpoints in the
  // default function and only measure /health.
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, tags: { setup: 'true' } }
  );
  if (res.status === 200) {
    return { token: res.json('access_token'), ds: TEST_DS };
  }
  console.warn(`setup login failed (status=${res.status}); authenticated endpoints will be skipped`);
  return { token: '', ds: '' };
}

export default function (data) {
  const authHeaders = data.token
    ? { headers: { Authorization: `Bearer ${data.token}`, 'Content-Type': 'application/json' } }
    : null;

  group('health', () => {
    const r = http.get(`${BASE_URL}/api/v1/health/ready`, { tags: { endpoint: 'health' } });
    check(r, { 'health 200': (r) => r.status === 200 });
  });

  if (!authHeaders) {
    sleep(0.1);
    return;
  }

  group('login', () => {
    const r = http.post(
      `${BASE_URL}/api/v1/auth/login`,
      JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD }),
      { headers: { 'Content-Type': 'application/json' }, tags: { endpoint: 'login' } }
    );
    loginLatency.add(r.timings.duration);
    loginFailRate.add(r.status !== 200);
  });

  group('datasources list', () => {
    const r = http.get(`${BASE_URL}/api/v1/datasources`, {
      ...authHeaders,
      tags: { endpoint: 'datasources' },
    });
    check(r, { 'datasources 200': (r) => r.status === 200 });
  });

  if (data.ds) {
    group('preview', () => {
      const r = http.get(`${BASE_URL}/api/v1/datasources/${data.ds}/preview?limit=5`, {
        ...authHeaders,
        tags: { endpoint: 'preview' },
      });
      check(r, { 'preview 200': (r) => r.status === 200 });
    });

    group('rows', () => {
      const r = http.get(`${BASE_URL}/api/v1/datasources/${data.ds}/rows?offset=0&limit=20`, {
        ...authHeaders,
        tags: { endpoint: 'rows' },
      });
      check(r, { 'rows 200': (r) => r.status === 200 });
    });
  }

  sleep(0.05);
}
