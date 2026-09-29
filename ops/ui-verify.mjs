#!/usr/bin/env node
//
// PaperGuard 浏览器 UI 自动登录与视觉验证
//
// 为什么需要它：ops/uat-verify.sh 覆盖的是**接口层**——它能自动登录、能断言
// 权限与数据形状，但断言不到「宽屏顶栏有没有被压成竖排单字」「窄屏有没有折行」
// 这类**只有渲染结果能回答**的问题。宿主内置浏览器每次触及登录表单都要人工
// 接管，元素引用还会在等待期间失效，所以逐屏 UI 复验一直靠人工。
// 本脚本用 Playwright 直接驱动 Chromium 走**真实登录表单**，把这一段也自动化。
//
// 与 ops/uat-verify.sh 的分工：
//   - uat-verify.sh：接口层，断言权限与数据形状，不碰渲染；
//   - 本脚本：浏览器层，断言登录可达性、顶栏排版、页面渲染，产出截图证据。
// 两者互补，都不替代人工对配色/间距的主观判断。
//
// 用法：
//   export PAPERGUARD_UAT_BASE_URL=http://43.167.211.145:8080
//   export PAPERGUARD_UAT_TEACHER_USERNAME=...
//   export PAPERGUARD_UAT_TEACHER_PASSWORD=...
//   export PAPERGUARD_UAT_STUDENT_USERNAME=...
//   export PAPERGUARD_UAT_STUDENT_PASSWORD=...
//   node ops/ui-verify.mjs
//
// 可选：
//   PAPERGUARD_UAT_ALLOW_PLAINTEXT=1   允许明文 HTTP（默认仅允许回环）
//   PAPERGUARD_UAT_EVIDENCE_PATH=./ui-verification-evidence.json
//   PAPERGUARD_UAT_SCREENSHOT_DIR=./ui-verification-screenshots
//   PAPERGUARD_UAT_SOURCE_REVISION=<git sha>
//   PAPERGUARD_UAT_HEADLESS=0          打开有头模式（本地排查用）
//
// 安全姿态（与 uat-verify.sh 保持一致）：
//   - 凭据只从环境变量读，只留在进程内存，不落盘、不进 argv、不进版本库；
//   - 口令短于 8 位判为占位符直接拒绝；
//   - 默认只接受 HTTPS 或回环，明文需显式 opt-in 并告警；
//   - 任何输出、证据文件、异常信息都不包含口令。
//
// 退出码：0 全部通过；1 有断言失败；2 输入/环境不满足。

import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve as resolvePath } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolvePath(__dirname, '..')

// Playwright 装在 frontend/ 下（devDependency），按候选路径解析
const require = createRequire(import.meta.url)
let chromium
for (const candidate of [
  'playwright',
  resolvePath(repoRoot, 'frontend/node_modules/@playwright/test'),
  resolvePath(repoRoot, 'node_modules/@playwright/test'),
]) {
  try {
    ;({ chromium } = require(candidate))
    break
  } catch {
    /* 试下一个候选 */
  }
}
if (!chromium) {
  process.stderr.write(
    'ui verification input error (deps.playwright): playwright not found; ' +
      'install it under frontend/ (npm i -D @playwright/test) or pass a resolvable path\n',
  )
  process.exit(2)
}

// ---------- 输入 ----------

const baseUrl = (process.env.PAPERGUARD_UAT_BASE_URL || '').replace(/\/+$/, '')
const allowPlaintext = process.env.PAPERGUARD_UAT_ALLOW_PLAINTEXT === '1'
const headless = process.env.PAPERGUARD_UAT_HEADLESS !== '0'
const evidencePath = resolvePath(
  process.cwd(),
  process.env.PAPERGUARD_UAT_EVIDENCE_PATH || 'ui-verification-evidence.json',
)
const screenshotDir = resolvePath(
  process.cwd(),
  process.env.PAPERGUARD_UAT_SCREENSHOT_DIR || 'ui-verification-screenshots',
)
const sourceRevision = process.env.PAPERGUARD_UAT_SOURCE_REVISION || 'unknown'
const navigationTimeout = Number(process.env.PAPERGUARD_UAT_NAV_TIMEOUT_MS || 20000)

const roles = [
  {
    key: 'teacher',
    username: process.env.PAPERGUARD_UAT_TEACHER_USERNAME || '',
    password: process.env.PAPERGUARD_UAT_TEACHER_PASSWORD || '',
  },
  {
    key: 'student',
    username: process.env.PAPERGUARD_UAT_STUDENT_USERNAME || '',
    password: process.env.PAPERGUARD_UAT_STUDENT_PASSWORD || '',
  },
]

// 可选：两个引导页各需**不同状态**的账号，用一个账号覆盖两页必然一头被重定向。
//   /first-login         → role=student 且 onboarding_required（邮箱未验证等）
//   /role-certification  → role=user 且 role_application_status=unselected
// 第九轮修掉越权后，这两个前置条件互斥，因此这里拆成两组环境变量。
// 哪组没给就跳过对应页面的取证，不算失败。
const firstLoginRole = {
  key: 'firstlogin',
  username: process.env.PAPERGUARD_UAT_FIRSTLOGIN_USERNAME || '',
  password: process.env.PAPERGUARD_UAT_FIRSTLOGIN_PASSWORD || '',
}
const certifyRole = {
  key: 'certify',
  username: process.env.PAPERGUARD_UAT_CERTIFY_USERNAME || '',
  password: process.env.PAPERGUARD_UAT_CERTIFY_PASSWORD || '',
}
for (const r of [firstLoginRole, certifyRole]) {
  if (r.username && r.password && r.password.length < 8) {
    die(`input.${r.key}`, `the ${r.key} password looks like a placeholder (shorter than 8 chars)`)
  }
}
const hasFirstLogin = !!(firstLoginRole.username && firstLoginRole.password)
const hasCertify = !!(certifyRole.username && certifyRole.password)

function die(code, message) {
  process.stderr.write(`ui verification input error (${code}): ${message}\n`)
  process.exit(2)
}

if (!baseUrl) die('input.base_url', 'PAPERGUARD_UAT_BASE_URL is required')
if (/[@?#]/.test(baseUrl)) {
  die('input.base_url', 'the base URL must not contain credentials, a query, or a fragment')
}
if (!/^https?:\/\//.test(baseUrl)) die('input.base_url', `unsupported scheme in ${baseUrl}`)
for (const role of roles) {
  if (!role.username || !role.password) {
    die(`input.${role.key}`, `PAPERGUARD_UAT_${role.key.toUpperCase()}_USERNAME/PASSWORD are required`)
  }
  if (role.password.length < 8) {
    die(`input.${role.key}`, `the ${role.key} password looks like a placeholder (shorter than 8 chars)`)
  }
}
if (baseUrl.startsWith('http://') && !/^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/)/.test(baseUrl)) {
  if (!allowPlaintext) {
    die(
      'input.base_url',
      'refusing a plaintext HTTP target; set PAPERGUARD_UAT_ALLOW_PLAINTEXT=1 to override ' +
        '(the session token would cross the network in clear)',
    )
  }
  process.stderr.write(
    'warning: running against a plaintext HTTP target; the session token crosses the network unencrypted\n',
  )
}
if (!Number.isFinite(navigationTimeout) || navigationTimeout <= 0) {
  die('input.nav_timeout', 'PAPERGUARD_UAT_NAV_TIMEOUT_MS must be a positive number')
}

// ---------- 断言收集 ----------

const checks = []
// 状态在这里收口，是为了让「汇总漏判失败」在结构上不可能发生。
// 教训：本脚本第一版把布尔表达式当 status 传进来，存成 false，而汇总只匹配
// 字符串 'failed' —— 两条失败被记成「非 passed 也非 failed」静默放行，
// 脚本在有 FAIL 的情况下报 passed。假绿的测试比没有测试更糟。
// 因此：布尔值归一化，未知状态一律当失败。
function record(name, status, detail = '') {
  const normalized =
    status === true || status === 'passed'
      ? 'passed'
      : status === false || status === 'failed'
        ? 'failed'
        : status === 'skipped'
          ? 'skipped'
          : 'failed'
  checks.push({ name, status: normalized, detail })
  const mark = normalized === 'passed' ? 'PASS' : normalized === 'skipped' ? 'SKIP' : 'FAIL'
  process.stdout.write(`  [${mark}] ${name}${detail ? ` — ${detail}` : ''}\n`)
}
function assert(name, condition, detail = '') {
  record(name, condition ? 'passed' : 'failed', detail)
  return !!condition
}

// ---------- 浏览器辅助 ----------

mkdirSync(screenshotDir, { recursive: true })

// 每个视口各自开一个 context 并**各自走一次真实登录表单**。
// 不能复用 storageState：会话 token 存在 sessionStorage，而 Playwright 的
// storageState 只序列化 cookie 与 localStorage，不含 sessionStorage。
// 复用会让窄屏那一轮变成未登录、页面停在 /login，顶栏断言拿不到元素——
// 这是「测试自身有缺陷」而不是「产品有缺陷」，两者必须分清。
async function openSession(browser, role, viewport, deviceScaleFactor) {
  const context = await browser.newContext({ viewport, deviceScaleFactor, locale: 'zh-CN' })
  const page = await context.newPage()
  page.setDefaultTimeout(navigationTimeout)
  page.setDefaultNavigationTimeout(navigationTimeout)
  await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('#username', { state: 'visible' })
  await page.fill('#username', role.username)
  await page.fill('#password', role.password)
  await Promise.all([
    page.waitForResponse((res) => res.url().includes('/api/auth/login') && res.status() === 200, {
      timeout: navigationTimeout,
    }),
    page.click('.auth-submit-btn'),
  ])
  // 登录成功后 SPA 会离开 /login
  await page.waitForFunction(() => !location.pathname.startsWith('/login'), null, {
    timeout: navigationTimeout,
  })
  await page.waitForLoadState('networkidle').catch(() => {})
  return { context, page, landed: new URL(page.url()).pathname }
}

// 顶栏排版：宽屏下用户名不能被压成竖排单字。
// 判据有三条，缺一不可——
//   1) 元素可见；
//   2) 用 Range 量文本的行盒数：被压窄到折行时会 >1，压成竖排就是每字一行；
//   3) 宽度下限：13px 字号下一个汉字约 14px，只剩「验」那种宽度 <20px。
async function inspectTopbar(page) {
  return page.evaluate(() => {
    const pick = (sel) => document.querySelector(sel)
    const describe = (el) => {
      if (!el) return { present: false }
      const style = getComputedStyle(el)
      const rect = el.getBoundingClientRect()
      let lineCount = 0
      let lineWidths = []
      try {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
        let node
        while ((node = walker.nextNode())) {
          if (!node.nodeValue || !node.nodeValue.trim()) continue
          const range = document.createRange()
          range.selectNodeContents(node)
          const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0)
          if (rects.length) {
            lineCount += rects.length
            lineWidths.push(...rects.map((r) => Math.round(r.width)))
          }
        }
      } catch {
        lineCount = -1
      }
      return {
        present: true,
        visible: style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0,
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        lineCount,
        lineWidths,
        text: (el.textContent || '').trim().slice(0, 40),
      }
    }
    const header = pick('header.app-shell-header')
    return {
      username: describe(pick('.shell-user-name')),
      tray: describe(pick('.user-tray')),
      scope: describe(pick('.user-tray .scope-context')),
      logout: describe(pick('.user-tray .shell-logout')),
      headerHeight: header ? Math.round(header.getBoundingClientRect().height) : 0,
      docScrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }
  })
}

async function assertWideTopbar(page, roleKey) {
  const bar = await inspectTopbar(page)
  const name = bar.username
  assert(`ui.${roleKey}.topbar.username_present`, name.present, `found=${name.present}`)
  if (!name.present) return bar
  assert(`ui.${roleKey}.topbar.username_visible`, name.visible, `display/visibility/box check`)
  assert(
    `ui.${roleKey}.topbar.username_single_line`,
    name.lineCount === 1,
    `line boxes=${name.lineCount} widths=${JSON.stringify(name.lineWidths)}`,
  )
  assert(
    `ui.${roleKey}.topbar.username_not_squeezed`,
    name.width >= 40,
    `width=${name.width}px height=${name.height}px (a single CJK char at 13px is ~14px)`,
  )
  assert(
    `ui.${roleKey}.topbar.tray_single_row`,
    bar.tray.height <= 56,
    `tray height=${bar.tray.height}px (wrapping to a second row roughly doubles it)`,
  )
  assert(
    `ui.${roleKey}.topbar.header_not_wrapped`,
    bar.headerHeight > 0 && bar.headerHeight <= 76,
    `header height=${bar.headerHeight}px`,
  )
  assert(
    `ui.${roleKey}.topbar.no_horizontal_overflow`,
    bar.docScrollWidth <= bar.innerWidth + 1,
    `scrollWidth=${bar.docScrollWidth} innerWidth=${bar.innerWidth}`,
  )
  return bar
}

// 窄屏：用户名按设计隐藏（@media width<=900px），这里验的是「元素在、但隐藏」
async function assertNarrowTopbar(page, roleKey) {
  const bar = await inspectTopbar(page)
  // present=false 与 visible=true 是两种完全不同的失败：
  // 前者是这一轮根本没登录成功（顶栏不存在），后者才是产品缺陷。
  assert(
    `ui.${roleKey}.topbar_mounted`,
    bar.username.present,
    `present=${bar.username.present} — 元素不存在说明这一轮未登录成功，是脚本问题不是产品问题`,
  )
  if (bar.username.present) {
    assert(
      `ui.${roleKey}.username_hidden_by_design`,
      !bar.username.visible,
      `visible=${bar.username.visible} — 窄屏仍显示用户名说明 @media 兜底没生效`,
    )
  }
  if (bar.tray.present) {
    assert(
      `ui.${roleKey}.tray_single_row`,
      bar.tray.height <= 56,
      `tray height=${bar.tray.height}px`,
    )
  }
  assert(
    `ui.${roleKey}.no_horizontal_overflow`,
    bar.docScrollWidth <= bar.innerWidth + 1,
    `scrollWidth=${bar.docScrollWidth} innerWidth=${bar.innerWidth}`,
  )
  return bar
}

// 等主区域真正有内容再继续。
// 教训：只用 waitUntil:'domcontentloaded' 就截图，会拍到「侧栏已渲染、主区还空白」
// 的半成品。第一版脚本就是这么拍出了一张主区全空的工作台截图，差点被当成
// 「接口全 200 但界面空白」的严重缺陷上报——实际只是工作台数据还没回来。
// 空白页是真实存在的故障模式，所以必须等，但不能只靠固定 sleep。
async function waitForMainContent(page, minChars = 120) {
  const deadline = Date.now() + navigationTimeout
  let lastLen = 0
  while (Date.now() < deadline) {
    const len = await page
      .evaluate(() => {
        const main = document.querySelector('.app-shell-main') || document.querySelector('main')
        return (main ? main.innerText || '' : '').replace(/\s+/g, '').length
      })
      .catch(() => 0)
    lastLen = len
    if (len >= minChars) return { ok: true, len }
    await page.waitForTimeout(400)
  }
  return { ok: false, len: lastLen }
}

async function capturePage(page, fileName) {
  const target = resolvePath(screenshotDir, fileName)
  await page.screenshot({ path: target, fullPage: true })
  return target
}

// 页面渲染体检。**不要断言 <form> 一定存在**：RoleCertification.vue 用的是
// 一组 <button type="button"> 角色卡片，没有 <form> 标签；按 form 断言会把
// 正确的页面判成失败。所以统一看「有没有可交互控件」。
async function inspectRenderedPage(page) {
  return page.evaluate(() => {
    const h1 = document.querySelector('h1')
    const form = document.querySelector('form')
    const errorBanner = document.querySelector('.error-banner')
    const body = document.body
    const buttonCount = document.querySelectorAll('button, [role="button"]').length
    const fieldCount = document.querySelectorAll('input,select,textarea').length
    return {
      path: location.pathname,
      title: (document.title || '').slice(0, 80),
      h1: h1 ? (h1.textContent || '').trim().slice(0, 60) : '',
      hasForm: !!form,
      buttonCount,
      fieldCount,
      hasInteractive: !!form || buttonCount > 0,
      errorText: errorBanner ? (errorBanner.textContent || '').trim().slice(0, 120) : '',
      textLength: (body ? body.innerText || '' : '').replace(/\s+/g, '').length,
      // 空白页是最典型的「接口全 200 但界面没东西」
      looksBlank: (body ? body.innerText || '' : '').replace(/\s+/g, '').length < 10,
    }
  })
}

// ---------- 执行 ----------

const browser = await chromium.launch({ headless })
let browserError = null

// 顶栏只在 AppShell 页面里有；onboarding 引导页没有 AppShell。
function workspaceTarget(landed) {
  return landed === '/first-login' || landed === '/role-certification' ? '/workspace' : '/'
}

async function runViewportPass(role, viewport, deviceScaleFactor, passName) {
  const key = `${role.key}.${passName}`
  let session
  try {
    session = await openSession(browser, role, viewport, deviceScaleFactor)
  } catch (error) {
    record(`ui.${key}.login`, 'failed', `login did not complete: ${error.message}`)
    return null
  }
  record(`ui.${key}.login`, 'passed', `landed on ${session.landed}`)

  // 登录落点本身就是引导页时，顺手取渲染证据
  if (session.landed === '/first-login' || session.landed === '/role-certification') {
    const render = await inspectRenderedPage(session.page)
    record(
      `ui.${key}.landing_page_rendered`,
      render.h1 !== '' && render.hasInteractive && !render.looksBlank,
      `landed=${render.path} h1="${render.h1}" form=${render.hasForm} buttons=${render.buttonCount} fields=${render.fieldCount} chars=${render.textLength}`,
    )
    await capturePage(session.page, `${role.key}-${session.landed.replace(/\W+/g, '-')}.png`)
  }

  // 量顶栏需要回到有 AppShell 的页面
  const target = workspaceTarget(session.landed)
  await session.page.goto(`${baseUrl}${target}`, { waitUntil: 'domcontentloaded' })
  const mounted = await session.page
    .waitForSelector('header.app-shell-header', { state: 'attached', timeout: 8000 })
    .then(() => true)
    .catch(() => false)
  if (!mounted) {
    record(
      `ui.${key}.topbar_mounted`,
      'failed',
      `AppShell header not found on ${target} — 这一轮大概率没登录成功，是脚本问题不是产品问题`,
    )
    await session.context.close()
    return session
  }
  // 主区必须有内容才继续，否则量到的顶栏与拍到的截图都只是半成品
  const content = await waitForMainContent(session.page)
  record(
    `ui.${key}.main_content_rendered`,
    content.ok,
    content.ok
      ? `${content.len} chars in .app-shell-main`
      : `主区只有 ${content.len} 字符，超时未渲染出内容 — 需要人工确认是真空白页还是接口失败`,
  )
  return session
}

try {
  process.stdout.write('== 1/3 宽屏（1440x900）登录与顶栏排版 ==\n')
  for (const role of roles) {
    const session = await runViewportPass(role, { width: 1440, height: 900 }, 1, 'wide')
    if (!session) continue
    const bar = await assertWideTopbar(session.page, `${role.key}.wide`)
    await capturePage(session.page, `${role.key}-wide-1440.png`)
    record(
      `ui.${role.key}.wide.username_measured`,
      'passed',
      `width=${bar.username.width}px height=${bar.username.height}px lineBoxes=${bar.username.lineCount} text="${bar.username.text}"`,
    )
    await session.context.close()
  }

  process.stdout.write('== 2/3 窄屏（390x844）顶栏折行与溢出 ==\n')
  for (const role of roles) {
    // 窄屏必须重新走一遍登录表单：token 在 sessionStorage，storageState 不含它
    const session = await runViewportPass(role, { width: 390, height: 844 }, 2, 'narrow')
    if (!session) continue
    await assertNarrowTopbar(session.page, `${role.key}.narrow`)
    await capturePage(session.page, `${role.key}-narrow-390.png`)
    await session.context.close()
  }

  process.stdout.write('== 3/3 引导页视觉取证 ==\n')
  // 每页配一个状态匹配的账号，逐页取证并截图
  const onboardingCases = [
    {
      route: '/first-login',
      role: firstLoginRole,
      available: hasFirstLogin,
      envHint: 'PAPERGUARD_UAT_FIRSTLOGIN_USERNAME/PASSWORD',
    },
    {
      route: '/role-certification',
      role: certifyRole,
      available: hasCertify,
      envHint: 'PAPERGUARD_UAT_CERTIFY_USERNAME/PASSWORD',
    },
  ]
  for (const c of onboardingCases) {
    if (!c.available) {
      record(`ui.onboarding${c.route.replace(/\W+/g, '_')}`, 'skipped', `${c.envHint} 未提供`)
      continue
    }
    let session
    try {
      session = await openSession(browser, c.role, { width: 1440, height: 900 }, 1)
    } catch (error) {
      record(`ui.onboarding${c.route.replace(/\W+/g, '_')}`, 'failed', `login failed: ${error.message}`)
      continue
    }
    record(`ui.onboarding.${c.role.key}.login`, 'passed', `landed on ${session.landed}`)

    await session.page.goto(`${baseUrl}${c.route}`, { waitUntil: 'domcontentloaded' })
    await session.page.waitForLoadState('networkidle').catch(() => {})
    await session.page.waitForSelector('h1', { timeout: 8000 }).catch(() => {})
    const render = await inspectRenderedPage(session.page)
    const redirected = render.path !== c.route
    record(
      `ui.onboarding${c.route.replace(/\W+/g, '_')}`,
      !redirected && render.h1 !== '' && render.hasInteractive && !render.looksBlank,
      redirected
        ? `访问 ${c.route} 被重定向到 ${render.path} — 该账号不满足此页准入口径`
        : `h1="${render.h1}" form=${render.hasForm} buttons=${render.buttonCount} fields=${render.fieldCount} chars=${render.textLength} error="${render.errorText}"`,
    )
    await capturePage(session.page, `onboarding${c.route.replace(/\W+/g, '-')}.png`)
    await session.context.close()
  }
} catch (error) {
  browserError = error
} finally {
  await browser.close().catch(() => {})
}

if (browserError) {
  record('ui.browser', 'failed', browserError.message)
} else {
  record('ui.browser', 'passed', 'chromium launched and closed cleanly')
}

// ---------- 汇总 ----------

const failures = checks.filter((c) => c.status === 'failed')
const skipped = checks.filter((c) => c.status === 'skipped')
const result = failures.length === 0 ? 'passed' : 'failed'
writeFileSync(
  evidencePath,
  `${JSON.stringify(
    {
      format: 'paperguard-ui-verification',
      format_version: 1,
      result,
      non_destructive: true,
      credentials_persisted: false,
      layer: 'browser',
      complementary_to: 'ops/uat-verify.sh (API layer)',
      target_base_url: baseUrl,
      source_revision: sourceRevision,
      completed_at: new Date().toISOString(),
      viewports: { wide: '1440x900', narrow: '390x844' },
      screenshot_dir: screenshotDir,
      counts: { total: checks.length, failed: failures.length, skipped: skipped.length },
      checks,
    },
    null,
    2,
  )}\n`,
  { mode: 0o600 },
)

if (failures.length === 0) {
  process.stdout.write(
    `ui verification passed: ${checks.length - skipped.length} checks passed` +
      `${skipped.length ? `, ${skipped.length} skipped` : ''}; ` +
      `evidence -> ${evidencePath}; screenshots -> ${screenshotDir}\n`,
  )
  process.exit(0)
}

process.stderr.write(
  `ui verification FAILED: ${failures.length} of ${checks.length} checks failed; evidence -> ${evidencePath}\n`,
)
for (const f of failures) process.stderr.write(`  - ${f.name}: ${f.detail}\n`)
process.exit(1)
