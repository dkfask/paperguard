import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const e2eDataDir = mkdtempSync(join(tmpdir(), 'paperguard-e2e-'))
const e2ePassword = process.env.E2E_ADMIN_PASSWORD ?? ''
const backendPort = process.env.E2E_BACKEND_PORT ?? '8000'
const frontendPort = process.env.E2E_FRONTEND_PORT ?? '5173'
const workerReadyPort = process.env.E2E_WORKER_READY_PORT ?? '8001'
const localPython = join(process.cwd(), '../.venv/bin/python')
const pythonBin = process.env.E2E_PYTHON_BIN ?? (existsSync(localPython) ? localPython : 'python')
const e2eEnvironment = {
  ...process.env,
  VITE_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
  PAPERGUARD_ADMIN_PASSWORD: e2ePassword,
  PAPERGUARD_DB_PATH: process.env.E2E_DB_PATH ?? join(e2eDataDir, 'paperguard.db'),
  PAPERGUARD_TEMPLATE_DIR: process.env.E2E_TEMPLATE_DIR ?? join(e2eDataDir, 'templates'),
  PAPERGUARD_ARTIFACT_DIR: process.env.E2E_ARTIFACT_DIR ?? join(e2eDataDir, 'artifacts'),
  PAPERGUARD_DATA_DIR: process.env.E2E_DATA_DIR ?? join(e2eDataDir, 'data'),
  PAPERGUARD_WORKER_INTERVAL_SECONDS: '5',
}
const localWebServers = [
  {
    command: `cd ../backend && ${pythonBin} -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
    url: `http://127.0.0.1:${backendPort}/openapi.json`,
    env: e2eEnvironment,
    reuseExistingServer: Boolean(process.env.TEST_REUSE_SERVER),
    timeout: 120_000,
  },
  {
    command: `cd ../backend && (${pythonBin} -m app.worker & worker_pid=$!; ready_dir=$(mktemp -d /tmp/paperguard-e2e-ready.XXXXXX); trap 'kill $worker_pid; rmdir "$ready_dir"' EXIT TERM INT; ${pythonBin} -m http.server ${workerReadyPort} --bind 127.0.0.1 --directory "$ready_dir" >/dev/null 2>&1)`,
    // The worker has no HTTP port. A loopback-only readiness server keeps the
    // worker in the same lifecycle without exposing any application files.
    url: `http://127.0.0.1:${workerReadyPort}`,
    env: e2eEnvironment,
    reuseExistingServer: false,
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
]

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  // The local harness intentionally shares one disposable SQLite database
  // across legacy and generic lifecycle suites.
  workers: 1,
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  // ★ CI 上缺失的视觉基线必须**每一次都失败**，不得被 retry 变成绿灯。
  //
  // 为什么不直接用 Playwright 的默认值：Playwright 1.63 实测（隔离最小项目验证）
  //   · 默认（未设该项）：快照缺失 ⇒ **写下该文件**且本次**失败**
  //   · 紧接着重跑（= 上面的 retries: 1）⇒ **通过**
  // ⇒ 在一台**从来没有 linux 基线**的干净 CI 机器上，
  //    视觉门禁会「先红后绿」，CI 报绿而**什么都没比对过**——
  //    这正是 INC-70「恒绿而什么都没查」的形态，只是触发得更隐蔽。
  //
  // 本仓现有 5 张基线**全是 `-darwin`**，而 CI 跑在 ubuntu ⇒ 一旦把
  // `test:e2e` 接进 CI，这段链条立刻生效。'none' 下不写文件，
  // 所以重试多少次都是红，直到**真的提交 linux 基线**为止。
  // 本机保持 'missing'，不改变开发者现有的便捷默认。
  updateSnapshots: process.env.CI ? 'none' : 'missing',
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
  ...(process.env.TEST_BASE_URL ? {} : { webServer: localWebServers }),
})
