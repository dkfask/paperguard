#!/usr/bin/env node
//
// ops/ui-verify.mjs 的契约自测（回环 mock，不碰生产）
//
// 为什么必须有它：ui-verify.mjs 第一版有个**假绿**缺陷——
// 引导页那两处把布尔表达式当 status 传给 record()，存成 false，
// 而汇总只匹配字符串 'failed'，于是两条失败被静默放行，
// 脚本在有 FAIL 的情况下报 passed。
//
// 一个会谎报通过的验证脚本，比没有脚本更危险：它制造的是
// 「已经验过了」这种最危险的错觉。所以这里用回环 mock 把
// 「断言失败必须真的失败」钉死。
//
// 用法：node ops/test-ui-verify.mjs
// 退出码：0 全部通过；1 有用例失败。

import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve as resolvePath } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SCRIPT = resolvePath(__dirname, 'ui-verify.mjs')
const PASSWORD = 'SelfTest-Pass-2026-aA1'

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  process.stdout.write(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}\n`)
}

// ---------- 回环 mock 应用 ----------

// squeezed=true 时把用户名压到 10px 且允许折行——复刻 F-04 的原始症状，
// 用来确认 ui-verify.mjs 真的能抓到它，而不是只会报 PASS。
function loginPage() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>mock</title></head>
<body>
<form id="f">
  <input id="username" autocomplete="username">
  <input id="password" type="password" autocomplete="current-password">
  <button type="submit" class="auth-submit-btn">登录</button>
</form>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username: document.getElementById('username').value,
      password: document.getElementById('password').value,
    }),
  });
  if (!res.ok) { document.title = 'login-failed'; return; }
  const data = await res.json();
  sessionStorage.setItem('token', data.token);
  location.href = '/workspace';
});
</script>
</body></html>`
}

// squeezed 变体：容器极窄 + 允许在字符间断行 → Range 量到的行盒数会 >1
function appShell({ squeezed = false, mainChars = 600 } = {}) {
  const nameStyle = squeezed
    ? 'display:inline-block;width:10px;white-space:normal;word-break:break-all;font-size:13px;'
    : 'display:inline-block;max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px;'
  const filler = '工'.repeat(Math.max(0, Math.floor(mainChars / 2)))
  return `<!doctype html><html><head><meta charset="utf-8"><title>mock</title>
<style>
 body{margin:0;font-family:system-ui}
 .app-shell-header{display:flex;justify-content:space-between;height:60px;align-items:center;padding:0 16px}
 .user-tray{display:flex;gap:8px;align-items:center;flex:0 0 auto}
 .shell-user-name{${nameStyle}}
 .scope-context,.shell-logout{white-space:nowrap;flex:0 0 auto}
 .app-shell-main{padding:16px}
 @media (max-width:900px){ .shell-user-name{display:none} }
</style></head>
<body>
<header class="app-shell-header">
  <div class="brand"><span class="brand-title">PaperGuard</span></div>
  <div class="user-tray">
    <span class="scope-context">指导教师</span>
    <span class="user-avatar-btn"><span class="user-avatar-initial">验</span></span>
    <span class="shell-user-name">${squeezed ? '验收-超长用户名测试用例' : '验收-selftest'}</span>
    <button class="shell-logout" type="button">退出登录</button>
  </div>
</header>
<div class="app-shell-main"><h1>角色工作台</h1><p>${filler}</p></div>
</body></html>`
}

function startServer({ squeezed = false, blankOnboarding = false } = {}) {
  return new Promise((resolve) => {
    const server = createServer((req, res) => {
      const url = req.url || '/'
      if (url === '/login') {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
        res.end(loginPage())
        return
      }
      if (url === '/api/auth/login') {
        let body = ''
        req.on('data', (c) => (body += c))
        req.on('end', () => {
          let ok = false
          try {
            const parsed = JSON.parse(body || '{}')
            ok = !!parsed.token || parsed.password === PASSWORD
          } catch {
            ok = false
          }
          res.writeHead(ok ? 200 : 401, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify(ok ? { token: 'selftest-token' } : { detail: 'bad credentials' }))
        })
        return
      }
      if (blankOnboarding && url === '/first-login') {
        // 故意渲染一个没有 h1、没有可交互控件的空白页 ——
        // 这是「接口全 200 但界面空白」的真实故障形态。
        // 引导页那处 record() 传的是**原始布尔**，这条路径正是
        // ui-verify.mjs 第一版假绿的来源，必须被本自测盯住。
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
        res.end('<!doctype html><html><body><div></div></body></html>')
        return
      }
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
      res.end(appShell({ squeezed }))
    })
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      resolve({ server, baseUrl: `http://127.0.0.1:${port}` })
    })
  })
}

// ---------- 运行被测脚本 ----------

function runScript(env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SCRIPT], {
      env: { ...process.env, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (c) => (stdout += c))
    child.stderr.on('data', (c) => (stderr += c))
    child.on('close', (code) => resolve({ code, stdout, stderr }))
  })
}

const baseEnv = {
  PAPERGUARD_UAT_TEACHER_USERNAME: 'selftest_teacher',
  PAPERGUARD_UAT_TEACHER_PASSWORD: PASSWORD,
  // 压短超时：自测里任何一个「等不到」的分支都该快速失败，
  // 而不是让整套自测跑上两分钟。mock 正常时不会走到超时。
  PAPERGUARD_UAT_NAV_TIMEOUT_MS: '6000',
}

const workDir = mkdtempSync(join(tmpdir(), 'paperguard-ui-selftest.'))

try {
  // 场景 1：一切正常 → 退出 0、证据 passed
  {
    const { server, baseUrl } = await startServer({ squeezed: false })
    const evidence = join(workDir, 'ok.json')
    const shots = join(workDir, 'ok-shots')
    const r = await runScript({
      ...baseEnv,
      PAPERGUARD_UAT_BASE_URL: baseUrl,
      PAPERGUARD_UAT_EVIDENCE_PATH: evidence,
      PAPERGUARD_UAT_SCREENSHOT_DIR: shots,
    })
    const ev = JSON.parse(readFileSync(evidence, 'utf8'))
    check(
      'scenario1.healthy_run_passes',
      r.code === 0 && ev.result === 'passed' && ev.counts.failed === 0,
      `exit=${r.code} result=${ev.result} failed=${ev.counts.failed} total=${ev.counts.total}`,
    )
    check(
      'scenario1.evidence_has_no_password',
      !r.stdout.includes(PASSWORD) && !r.stderr.includes(PASSWORD) && !readFileSync(evidence, 'utf8').includes(PASSWORD),
      '口令不得出现在 stdout / stderr / 证据文件中',
    )
    server.close()
  }

  // 场景 2：**假绿回归**。用户名被压成竖排 → 断言必须失败。
  // 这一条是本文件存在的理由：ui-verify.mjs 第一版在这里会报 passed。
  {
    const { server, baseUrl } = await startServer({ squeezed: true })
    const evidence = join(workDir, 'squeezed.json')
    const r = await runScript({
      ...baseEnv,
      PAPERGUARD_UAT_BASE_URL: baseUrl,
      PAPERGUARD_UAT_EVIDENCE_PATH: evidence,
      PAPERGUARD_UAT_SCREENSHOT_DIR: join(workDir, 'squeezed-shots'),
    })
    const ev = JSON.parse(readFileSync(evidence, 'utf8'))
    check(
      'scenario2.squeezed_username_fails_loudly',
      r.code === 1 && ev.result === 'failed' && ev.counts.failed > 0,
      `exit=${r.code}（应 1）result=${ev.result}（应 failed）failed=${ev.counts.failed}（应 >0）`,
    )
    const squeezedFailed = ev.checks.filter((c) => /not_squeezed|single_line/.test(c.name))
    check(
      'scenario2.the_squeeze_assert_is_the_one_that_failed',
      squeezedFailed.length > 0 && squeezedFailed.every((c) => c.status === 'failed'),
      `命中的断言: ${squeezedFailed.map((c) => `${c.name}=${c.status}`).join(', ') || '(无 — 说明没测到排版)'}`,
    )
    check(
      'scenario2.failure_detail_is_actionable',
      ev.checks.some((c) => c.status === 'failed' && /\d+px/.test(c.detail)),
      '失败项要带上像素数值，否则无法区分是量错还是真缺陷',
    )
    server.close()
  }

  // 场景 3：缺凭据 → 退出 2，且不得回落到「用空口令继续跑」
  {
    const { server, baseUrl } = await startServer()
    const r = await runScript({
      PAPERGUARD_UAT_BASE_URL: baseUrl,
      PAPERGUARD_UAT_TEACHER_USERNAME: '',
      PAPERGUARD_UAT_TEACHER_PASSWORD: '',
      PAPERGUARD_UAT_EVIDENCE_PATH: join(workDir, 'nocred.json'),
    })
    check('scenario3.missing_credentials_exit_2', r.code === 2, `exit=${r.code}（应 2）`)
    check(
      'scenario3.missing_credentials_message',
      r.stderr.includes('PAPERGUARD_UAT_TEACHER_USERNAME'),
      `stderr 应点名缺失的环境变量，实际: ${r.stderr.trim().slice(0, 90)}`,
    )
    server.close()
  }

  // 场景 4：占位符口令 → 退出 2
  {
    const { server, baseUrl } = await startServer()
    const r = await runScript({
      ...baseEnv,
      PAPERGUARD_UAT_BASE_URL: baseUrl,
      PAPERGUARD_UAT_TEACHER_PASSWORD: 'short',
      PAPERGUARD_UAT_EVIDENCE_PATH: join(workDir, 'ph.json'),
    })
    check('scenario4.placeholder_password_exit_2', r.code === 2, `exit=${r.code}（应 2）`)
    check(
      'scenario4.placeholder_password_message',
      r.stderr.includes('placeholder'),
      `stderr 应说明是占位符，实际: ${r.stderr.trim().slice(0, 90)}`,
    )
    server.close()
  }

  // 场景 6：**这条才是本文件真正的存在理由**。
  // 引导页那处 record() 收到的是原始布尔（`cond ? 'passed' : 'failed'` 的
  // 短路形式被绕过了），第一版正是因此把失败存成 false、汇总漏判、报 passed。
  // 场景 1–5 全都走 assert()，而 assert() 传的是字符串，
  // 所以**即使把 record() 的归一化整个删掉，这五条也照样全绿**——
  // 我第一版自测就是这么骗过了自己的。
  // 这里用一个渲染成空白的引导页，逼出布尔 false 的那条路径。
  {
    const { server, baseUrl } = await startServer({ blankOnboarding: true })
    const evidence = join(workDir, 'blank.json')
    const r = await runScript({
      ...baseEnv,
      PAPERGUARD_UAT_BASE_URL: baseUrl,
      PAPERGUARD_UAT_FIRSTLOGIN_USERNAME: 'selftest_firstlogin',
      PAPERGUARD_UAT_FIRSTLOGIN_PASSWORD: PASSWORD,
      PAPERGUARD_UAT_EVIDENCE_PATH: evidence,
      PAPERGUARD_UAT_SCREENSHOT_DIR: join(workDir, 'blank-shots'),
    })
    const ev = JSON.parse(readFileSync(evidence, 'utf8'))
    const blankCheck = ev.checks.find((c) => c.name === 'ui.onboarding_first_login')
    check(
      'scenario6.blank_onboarding_page_fails_loudly',
      r.code === 1 && ev.result === 'failed' && ev.counts.failed > 0,
      `exit=${r.code}（应 1）result=${ev.result}（应 failed）failed=${ev.counts.failed}（应 >0）`,
    )
    check(
      'scenario6.boolean_false_becomes_string_failed',
      !!blankCheck && blankCheck.status === 'failed',
      `引导页检查项 status=${blankCheck ? JSON.stringify(blankCheck.status) : '(缺失)'}；` +
        '若是布尔 false 而非字符串 "failed"，说明 record() 的归一化被删掉了',
    )
    server.close()
  }

  // 场景 5：非回环明文 HTTP 必须拒绝（除非显式 opt-in）
  {
    const r = await runScript({
      ...baseEnv,
      PAPERGUARD_UAT_BASE_URL: 'http://43.167.211.145:8080',
      PAPERGUARD_UAT_EVIDENCE_PATH: join(workDir, 'plain.json'),
    })
    check('scenario5.plaintext_refused_without_optin', r.code === 2, `exit=${r.code}（应 2）`)
    check(
      'scenario5.plaintext_refusal_names_the_override',
      r.stderr.includes('PAPERGUARD_UAT_ALLOW_PLAINTEXT'),
      `stderr 应提示 opt-in 开关，实际: ${r.stderr.trim().slice(0, 90)}`,
    )
  }
} finally {
  rmSync(workDir, { recursive: true, force: true })
}

const failed = results.filter((r) => !r.ok)
process.stdout.write(
  `\nui-verify 自测: ${results.length - failed.length}/${results.length} 通过\n`,
)
if (failed.length) {
  for (const f of failed) process.stderr.write(`  - ${f.name}: ${f.detail}\n`)
  process.exit(1)
}
process.exit(0)
