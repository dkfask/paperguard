import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const e2eDataDir = mkdtempSync(join(tmpdir(), 'paperguard-e2e-'))
const e2ePassword = process.env.E2E_ADMIN_PASSWORD ?? ''
const backendPort = process.env.E2E_BACKEND_PORT ?? '8000'
const frontendPort = process.env.E2E_FRONTEND_PORT ?? '5173'
const localPython = join(process.cwd(), '../.venv/bin/python')
const pythonBin = process.env.E2E_PYTHON_BIN ?? (existsSync(localPython) ? localPython : 'python')
const e2eEnvironment = {
  ...process.env,
  VITE_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
  PAPERGUARD_ADMIN_PASSWORD: e2ePassword,
  PAPERGUARD_DB_PATH: process.env.E2E_DB_PATH ?? join(e2eDataDir, 'paperguard.db'),
  PAPERGUARD_TEMPLATE_DIR: process.env.E2E_TEMPLATE_DIR ?? join(e2eDataDir, 'templates'),
  PAPERGUARD_ARTIFACT_DIR: process.env.E2E_ARTIFACT_DIR ?? join(e2eDataDir, 'artifacts'),
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [['html', { open: 'never' }]],
  use: {
    baseURL: process.env.TEST_BASE_URL ?? `http://127.0.0.1:${frontendPort}`,
    actionTimeout: 10_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-chrome', use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } } },
  ],
  webServer: [
    {
      command: `cd ../backend && ${pythonBin} -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      url: `http://127.0.0.1:${backendPort}/openapi.json`,
      env: e2eEnvironment,
      reuseExistingServer: Boolean(process.env.TEST_REUSE_SERVER),
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      url: `http://127.0.0.1:${frontendPort}`,
      env: {
        ...process.env,
        VITE_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
      },
      reuseExistingServer: Boolean(process.env.TEST_REUSE_SERVER),
      timeout: 120_000,
    },
  ],
})
