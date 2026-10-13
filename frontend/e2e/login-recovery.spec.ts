import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

test('login errors contain a helpful message without a diagnostic ID', async ({ page }) => {
  await page.goto('/login')
  await page.locator('#username').fill('unknown-fixture-account')
  await page.locator('#password').fill('WrongFixturePassword123')
  const [response] = await Promise.all([
    page.waitForResponse('**/api/auth/login'),
    page.locator('.auth-submit-btn').click(),
  ])
  expect(response.status()).toBe(401)
  expect(response.headers()['x-request-id']).toBeTruthy()
  await expect(page.locator('[role="alert"] .inline-notice-summary')).toHaveText('用户名或密码错误')
  await expect(page.getByRole('alert')).not.toContainText('请求 ID')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('recovery exposes the unavailable-mail fallback and a return to login', async ({ page }, testInfo) => {
  await page.goto('/login')
  await page.getByRole('button', { name: '忘记密码？' }).click()
  await expect(page.getByRole('heading', { name: '重置密码' })).toBeVisible()
  await expect(page.getByText('未绑定或未验证邮箱？请联系导师或管理员重置密码。')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const screenshot = testInfo.outputPath('password-recovery.png')
  await page.screenshot({ path: screenshot, fullPage: true })
  await testInfo.attach('password-recovery', { path: screenshot, contentType: 'image/png' })
  await page.locator('#recoveryEmail').fill('recovery-fixture@example.edu')
  await page.getByRole('button', { name: '发送验证码' }).click()
  await expect(page.getByRole('alert')).toContainText('邮件服务尚未配置，请联系导师或管理员重置密码')
  await expect(page.getByRole('alert')).not.toContainText('请求 ID')
  await page.getByRole('button', { name: '返回登录' }).click()
  await expect(page.locator('#username')).toBeVisible()
})

test('a linked mailbox resets its password and invalidates its old session', async ({ page, request }) => {
  test.skip(!process.env.E2E_DB_PATH, 'Requires the explicitly isolated native E2E database')
  const email = `recovery-${randomUUID()}@example.edu`
  const password = 'OldFixturePassword123'
  const newPassword = 'NewFixturePassword456'
  const code = '123456'
  const python = process.env.E2E_PYTHON_BIN ?? path.resolve('../.venv/bin/python')
  execFileSync(python, ['-c', `
import json, sys
from datetime import datetime, timedelta, timezone
from app.auth import hash_password, _password_reset_code_hash
from app.db import Store, new_id
payload = json.load(sys.stdin)
store = Store()
now = datetime.now(timezone.utc)
password_hash, salt = hash_password(payload["password"])
store.save_user({
    "id": new_id("user"), "username": payload["email"].split("@")[0],
    "email": payload["email"], "email_verified": 1, "password_set": 1,
    "password_hash": password_hash, "salt": salt, "role": "student",
    "display_name": "Recovery browser fixture", "role_application_status": "approved",
    "created_at": now.isoformat(),
})
store.save_email_code(payload["email"], _password_reset_code_hash(payload["email"], payload["code"]), "password_reset",
                      (now + timedelta(minutes=10)).isoformat(), now.isoformat())
`], {
    cwd: path.resolve('../backend'),
    env: { ...process.env, PAPERGUARD_DB_PATH: process.env.E2E_DB_PATH },
    input: JSON.stringify({ email, password, code }),
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  const oldLogin = await request.post('/api/auth/login', { data: { username: email, password } })
  expect(oldLogin.status()).toBe(200)
  const oldToken = (await oldLogin.json()).token
  // Email transport is stubbed; password reset and login hit the real backend.
  await page.route('**/api/auth/email/code', async route => {
    expect(route.request().postDataJSON()).toEqual({ email, purpose: 'password_reset' })
    await route.fulfill({ json: { ok: true, expires_in: 600 } })
  })
  await page.goto('/login')
  await page.getByRole('button', { name: '忘记密码？' }).click()
  await page.locator('#recoveryEmail').fill(email)
  await page.getByRole('button', { name: '发送验证码' }).click()
  await expect(page.getByRole('button', { name: '60 秒后重发' })).toBeDisabled()
  await page.locator('#recoveryCode').fill(code)
  await page.locator('#recoveryPassword').fill(newPassword)
  await page.locator('#recoveryConfirm').fill(newPassword)
  await page.getByRole('button', { name: '确认重置密码' }).click()
  await expect(page.getByRole('status')).toContainText('密码已更新')
  await expect(page.locator('#username')).toHaveValue(email)
  await expect(page.locator('#password')).toHaveValue('')
  const oldSession = await request.get('/api/auth/me', { headers: { Authorization: `Bearer ${oldToken}` } })
  expect(oldSession.status()).toBe(401)
  const oldPassword = await request.post('/api/auth/login', { data: { username: email, password } })
  expect(oldPassword.status()).toBe(401)
  await page.locator('#password').fill(newPassword)
  await page.locator('.auth-submit-btn').click()
  await expect(page).toHaveURL(new URL('/', test.info().project.use.baseURL).toString())
  const newToken = await page.evaluate(() => sessionStorage.getItem('pg_token'))
  expect(newToken).toBeTruthy()
  expect(newToken).not.toBe(oldToken)
  const recoveredUser = await request.get('/api/auth/me', { headers: { Authorization: `Bearer ${newToken}` } })
  expect(recoveredUser.status()).toBe(200)
  expect((await recoveredUser.json()).username).toBe(email.split('@')[0])
})
