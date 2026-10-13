import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { LoginPage, PaperGuardPage } from './pages/paperguard.page'

// 本 spec 覆盖 2026-10-01 测试报告第五节列为「未验证」的界面验收缺口：
// 1) BUG-20261001-01 教师认证死路的界面级回归锁定
// 2) 8 个此前从未做界面走查的组件 + college_admin 角色闭环
//
// 验收标准：页面可渲染、无错误横幅、主标题非空、且存在可交互控件
//（BUG-01 的本质就是"能进页面但没有任何操作可用"，只看 HTTP 200 抓不到）。
// 角色提升全部走真实界面操作，同时顺带验证 AdminUsers 的角色按钮确实可用。

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? ''
const ADMIN_REPLACEMENT = `${ADMIN_PASSWORD}-walkthrough`

async function assertReadableSecondaryActions(page: Page) {
  // Empty standards pages keep their single action group beside the guidance.
  const actions = page.locator('.resource-hero-actions button:not(.primary), .policy-empty-actions button:not(.primary)')
  expect(await actions.count()).toBeGreaterThan(0)
  for (const action of await actions.all()) {
    await expect(action).toBeVisible()
    const contrast = await action.evaluate(el => {
      const rgb = (value: string) => value.match(/[\d.]+/g)!.map(Number)
      const luminance = (channels: number[]) => channels.slice(0, 3).map(value => {
        const fraction = value / 255
        return fraction <= .04045 ? fraction / 12.92 : ((fraction + .055) / 1.055) ** 2.4
      }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0)
      let surface: Element | null = el
      while (surface && getComputedStyle(surface).backgroundColor === 'rgba(0, 0, 0, 0)') surface = surface.parentElement
      const foreground = luminance(rgb(getComputedStyle(el).color))
      const background = luminance(rgb(getComputedStyle(surface || document.body).backgroundColor))
      return (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05)
    })
    expect(contrast, `次要操作「${await action.innerText()}」文字对比度`).toBeGreaterThanOrEqual(4.5)
  }
}

const stamp = () => `wt${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`
const fixturePassword = () => `Pwd-${stamp()}-9a`

async function createAccount(page: Page, role: 'student' | 'teacher') {
  const username = `wt_${role}_${stamp()}`
  const password = fixturePassword()
  const response = await page.request.post('/api/auth/register', {
    data: { username, password, display_name: `${role}-walkthrough`, role },
  })
  expect(response.status(), `注册 ${role} 夹具账号失败`).toBe(201)
  return { username, password }
}

/** 通过管理员界面把某账号提升为目标角色（AdminUsers 的角色按钮会同时把 status 置为 active）。 */
async function promoteViaUi(adminPage: Page, username: string, buttonName: string) {
  await adminPage.goto('/admin/users')
  const directory = new PaperGuardPage(adminPage)
  const dialog = await directory.openUserDetails(username)
  const button = dialog.getByRole('button', { name: buttonName })
  await expect(button, `${username} 缺少「${buttonName}」按钮`).toBeVisible()
  await new PaperGuardPage(adminPage).waitForMutation('/role-status', async () => {
    await button.click()
  }, 'PUT')
  await expect(adminPage.locator('.ok-banner')).toBeVisible()
  await directory.closeDetails()
}

/** 断言页面处于可用状态：有主标题、无错误横幅、且不是进得去却动不了的空壳。 */
async function assertPageUsable(page: Page, label: string) {
  await expect(page.locator('.error-banner'), `${label} 出现了错误横幅`).toHaveCount(0)
  const heading = page.locator('h1').first()
  await expect(heading, `${label} 缺少主标题`).toBeVisible()
  const headingText = (await heading.innerText()).trim()
  expect(headingText.length, `${label} 主标题为空`).toBeGreaterThan(0)
  const controls = await page.locator('button:not([disabled]), a[href], input, select').count()
  expect(controls, `${label} 没有任何可交互控件，疑似死路`).toBeGreaterThan(0)
  return { heading: headingText, controls }
}

async function loginAdmin(page: Page) {
  // 内置 admin 首次登录会触发强制改密，把密码换成 ADMIN_REPLACEMENT。
  // 同一轮里后续用例必须用新密码，否则全部卡在 /login。
  const candidates = [ADMIN_REPLACEMENT, ADMIN_PASSWORD]
  let lastError: unknown = null
  for (const password of candidates) {
    try {
      await new LoginPage(page).login('admin', password, ADMIN_REPLACEMENT)
      return
    } catch (error) {
      lastError = error
      await page.goto('/login').catch(() => {})
    }
  }
  throw new Error(`admin 登录失败（已尝试原始密码与改密后密码）: ${String(lastError)}`)
}

/**
 * 恢复内置 admin 的种子密码。
 *
 * playwright.config.ts 明确说明整个 harness 共享同一个一次性 SQLite 库
 * （"shares one disposable SQLite database across legacy and generic lifecycle suites"），
 * 而内置 admin 首次登录会触发强制改密。本 spec 改了密码却不恢复的话，
 * 后续的 document-platform / full-lifecycle 会以
 * `admin login: 401 用户名或密码错误` 失败——那是本 spec 造成的污染，不是产品缺陷。
 */
async function restoreAdminPassword(request: APIRequestContext) {
  const replacementLogin = await request.post('/api/auth/login', {
    data: { username: 'admin', password: ADMIN_REPLACEMENT },
  })
  if (!replacementLogin.ok()) return // 已是种子密码，无需恢复
  const { token } = await replacementLogin.json()
  const restored = await request.post('/api/auth/change-password', {
    headers: { Authorization: `Bearer ${token}` },
    data: { current_password: ADMIN_REPLACEMENT, new_password: ADMIN_PASSWORD },
  })
  expect(restored.status(), '未能恢复 admin 种子密码，会污染后续 spec').toBe(200)
}

test.describe('2026-10-01 界面走查与 BUG-01 回归', () => {
  test.afterAll(async ({ request }) => {
    await restoreAdminPassword(request)
  })

  test('BUG-20261001-01 回归：管理员可在界面把 pending 教师认证为 active', async ({ page }, info) => {
    // Put the target beyond the first desktop page to exercise real pagination.
    for (let index = 0; index < 4; index++) await createAccount(page, 'student')
    const teacher = await createAccount(page, 'teacher')
    await loginAdmin(page)

    await page.goto('/admin/users')
    const directory = new PaperGuardPage(page)
    const row = await directory.userRow(teacher.username)
    await expect(row, '待认证教师应出现在用户目录').toBeVisible()
    if (info.project.name === 'chromium') {
      await expect(page.getByRole('navigation', { name: '列表分页' }).getByRole('button', { name: '上一页', exact: true })).toBeEnabled()
    }

    // BUG-01 的核心断言：认证按钮必须可见。旧实现下它被 role_application_status 错误守卫隐藏，
    // 且「设为教师」因 role 已是 teacher 被隐藏，四个按钮全部落空形成死路。
    const dialog = await directory.openUserDetails(teacher.username)
    const approveButton = dialog.getByRole('button', { name: '通过认证' })
    await expect(approveButton, 'BUG-01 未修复：pending 教师看不到「通过认证」按钮').toBeVisible()

    // pendingTeachers 统计项此前计算了但从不渲染，一并回归
    await expect(page.locator('.workspace-stats').getByText('待审核教师')).toBeVisible()

    await new PaperGuardPage(page).waitForMutation('/role-status', async () => {
      await approveButton.click()
    }, 'PUT')
    await directory.closeDetails()
    await expect(await directory.userRow(teacher.username), '认证后应显示已认证').toContainText('已认证')
    const refreshedDialog = await directory.openUserDetails(teacher.username)
    await expect(refreshedDialog.getByRole('button', { name: '通过认证' }), '重新打开已认证账号不应再显示认证按钮').toHaveCount(0)
    await directory.closeDetails()

    // 反向验证：认证通过后该教师真的能进指导端（_ensure_manager 要求 status=active）
    await new PaperGuardPage(page).logout()
    await new LoginPage(page).login(teacher.username, teacher.password)
    await page.goto('/teacher/students')
    await page.waitForLoadState('networkidle').catch(() => {})
    await expect(page, '已认证教师仍被挡在指导端之外').toHaveURL(/\/teacher\/students/)
    console.log('  [BUG-01] pending 教师 → 界面认证 → 成功进入指导端')
  })

  test('管理员视角：六个管理组件逐个走查', async ({ page }) => {
    await loginAdmin(page)
    const adminPages: Array<[string, string]> = [
      ['/cohorts', '批次与阶段配置'],
      ['/workflow', '论文业务闭环'],
      ['/admin/ai', 'AI 与系统设置'],
      ['/audit', '审计记录'],
      ['/operations', '运行状态与数据治理'],
      ['/policies', '写作规范'],
    ]
    for (const [route, label] of adminPages) {
      await page.goto(route)
      await page.waitForLoadState('networkidle').catch(() => {})
      await expect(page, `${label} 被路由守卫重定向`).toHaveURL(new RegExp(`${route.replace(/\//g, '\\/')}$`))
      const info = await assertPageUsable(page, `[admin] ${label}`)
      if (route === '/operations') {
        await expect(page.getByText('平台操作受限', { exact: true })).toBeVisible()
        await expect(page.getByRole('button', { name: '生成备份' })).toBeDisabled()
        await expect(page.locator('.operations-retention-form')).toHaveCount(0)
      }
      if (route === '/policies') await assertReadableSecondaryActions(page)
      console.log(`  [admin] ${route} → 标题「${info.heading}」，可交互控件 ${info.controls} 个`)
    }
    await page.goto('/templates/new/edit')
    await expect(page.locator('h1')).toBeVisible()
    await assertReadableSecondaryActions(page)
    await page.locator('.resource-hero-actions button:not(.primary)').click()
    await expect(page).toHaveURL(/\/templates$/)
  })

  test('未登录视角：公开入口可渲染，受保护入口被正确拦截', async ({ page }) => {
    // 真正公开的入口：应各自渲染自身页面
    const openPages: Array<[string, string]> = [
      ['/login', '登录'],
      ['/register', '注册'],
    ]
    for (const [route, label] of openPages) {
      await page.goto(route)
      await page.waitForLoadState('networkidle').catch(() => {})
      await expect(page, `${label} 被重定向了`).toHaveURL(new RegExp(`${route.replace(/\//g, '\\/')}$`))
      const info = await assertPageUsable(page, `[anon] ${label}`)
      console.log(`  [anon] ${route} → 标题「${info.heading}」，可交互控件 ${info.controls} 个`)
    }

    // 需要登录的入口：匿名访问必须被挡回登录页，而不是渲染出半成品页面
    const guardedPages = ['/role-certification', '/first-login']
    for (const route of guardedPages) {
      await page.goto(route)
      await page.waitForLoadState('networkidle').catch(() => {})
      await expect(page, `${route} 匿名访问未被守卫拦截`).toHaveURL(/\/login(\?|$)/)
      console.log(`  [anon] ${route} → 正确挡回登录页 ${new URL(page.url()).pathname}${new URL(page.url()).search}`)
    }
  })

  test('教师视角：指导学生页可渲染且无权限死路', async ({ page, browser }) => {
    // 从 student 提升为 teacher：「设为教师」按钮本就对 role 已是 teacher 的账号隐藏
    // （该守卫行为正确，与 BUG-01 无关；BUG-01 走的是「通过认证」按钮，见首条用例）
    const teacher = await createAccount(page, 'student')
    await loginAdmin(page)
    await promoteViaUi(page, teacher.username, '设为教师')

    const teacherContext = await browser.newContext()
    const teacherPage = await teacherContext.newPage()
    try {
      await new LoginPage(teacherPage).login(teacher.username, teacher.password)
      await teacherPage.goto('/teacher/students')
      await teacherPage.waitForLoadState('networkidle').catch(() => {})
      await expect(teacherPage, '教师访问指导学生页被重定向').toHaveURL(/\/teacher\/students$/)
      const info = await assertPageUsable(teacherPage, '[teacher] 指导学生')
      console.log(`  [teacher] /teacher/students → 标题「${info.heading}」，可交互控件 ${info.controls} 个`)

      await teacherPage.goto('/admin/ai')
      await teacherPage.waitForLoadState('networkidle').catch(() => {})
      await expect(teacherPage, '教师越权进入 AI 系统设置页').not.toHaveURL(/\/admin\/ai/)
      console.log(`  [teacher] /admin/ai → 被守卫挡回 ${new URL(teacherPage.url()).pathname}`)
    } finally {
      await teacherContext.close()
    }
  })

  test('college_admin 闭环：角色可用性与越权边界', async ({ page, browser }) => {
    const base = await createAccount(page, 'student')
    await loginAdmin(page)
    await promoteViaUi(page, base.username, '设为学院管理员')

    const caContext = await browser.newContext()
    const caPage = await caContext.newPage()
    try {
      await new LoginPage(caPage).login(base.username, base.password)

      const caPages: Array<[string, string]> = [
        ['/cohorts', '批次与阶段配置'],
        ['/audit', '审计记录'],
        ['/organizations', '组织与作用域'],
      ]
      for (const [route, label] of caPages) {
        await caPage.goto(route)
        await caPage.waitForLoadState('networkidle').catch(() => {})
        await expect(caPage, `college_admin 进入 ${label} 被拒`).toHaveURL(new RegExp(`${route.replace(/\//g, '\\/')}$`))
        const info = await assertPageUsable(caPage, `[college_admin] ${label}`)
        console.log(`  [college_admin] ${route} → 标题「${info.heading}」，可交互控件 ${info.controls} 个`)
      }

      // 平台运维也遵循当前管理员路由边界，不为沿用旧用例而放宽权限。
      for (const route of ['/admin/users', '/admin/ai', '/operations']) {
        await caPage.goto(route)
        await caPage.waitForLoadState('networkidle').catch(() => {})
        await expect(caPage, `college_admin 越权进入 ${route}`).not.toHaveURL(new RegExp(`${route.replace(/\//g, '\\/')}$`))
        console.log(`  [college_admin] ${route} → 被守卫挡回 ${new URL(caPage.url()).pathname}`)
      }
    } finally {
      await caContext.close()
    }
  })
})
