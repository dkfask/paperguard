import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

/**
 * 教师端审查（第二轮）修复的回归锁定。
 *
 * 这些缺陷里有好几个是「肉眼难察觉、但用户直接受影响」的类型：
 * 样式缺失取决于访问顺序、图标名写错会渲染成 __EXTENDED、panel 参数静默失效。
 * 只靠人工复查会漏，所以固化成断言。
 *
 * 断言方式为源码静态检查，与仓库既有风格一致（本仓模板/样式为单行压缩，
 * 必须整段字符串包含或全局替换，不能用 ^行首$ 之类的行锚定）。
 */

const root = path.resolve(process.cwd())
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8')

describe('F-22 工作台通用样式不得挂在懒加载路由组件上', () => {
  it('Workspace.vue 不得再持有非 scoped 的 <style> 块', () => {
    const source = read('src/views/Workspace.vue')
    // 非 scoped 写法是 "<style>"，带 scoped 是 "<style scoped>"
    expect(source).not.toMatch(/<style>/)
    expect(source).not.toMatch(/<style\s+src=/)
  })

  it('工作台通用布局类必须定义在全局 style.css', () => {
    const css = read('src/style.css')
    for (const selector of ['.workspace-page', '.workspace-hero', '.workspace-identity', '.workspace-stats', '.workspace-layout']) {
      expect(css).toContain(selector)
    }
  })

  it('其他页面继续复用这些类时，不再依赖 /workspace 被访问过', () => {
    // 抽查两个共用类名的页面，它们自己不定义这些布局样式，靠全局兜底
    for (const view of ['src/views/Audit.vue', 'src/views/Cohorts.vue']) {
      expect(read(view)).toContain('workspace-identity')
    }
  })
})

describe('F-21 角色标签不得用二元三元', () => {
  it('不得出现「非 admin 一律显示学院管理员」的写法', () => {
    // 剥注释：i18n.js 的 roleLabel 文档里引用了这个反例写法本身，不能算作残留
    const stripComments = (text) => text
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    const offenders = []
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full)
        else if (/\.(vue|js|ts)$/.test(entry.name)) {
          const text = stripComments(fs.readFileSync(full, 'utf8'))
          if (/role\s*===\s*'admin'\s*\?\s*t\('role\.admin'\)/.test(text)) {
            offenders.push(path.relative(root, full))
          }
        }
      }
    }
    walk(path.join(root, 'src'))
    expect(offenders).toEqual([])
  })

  it('i18n 导出统一的 roleLabel 并对未知角色回落', () => {
    const i18n = read('src/i18n.js')
    expect(i18n).toContain('export function roleLabel')
    expect(i18n).toContain("translations.zh['role.user']")
  })
})

describe('F-14 不得向无权角色展示必然 403 的按钮', () => {
  it('批次「关闭/恢复」按钮必须受 canManage 约束', () => {
    const workflow = read('src/views/Workflow.vue')
    // 后端 close_cohort / reopen_cohort 走 _ensure_manager，角色集 {admin, college_admin}
    expect(workflow).toContain('v-if="canManage && cohort.status === \'published\'"')
    expect(workflow).toContain('v-if="canManage && cohort.status === \'closed\'"')
  })

  it('两个 _ensure_manager 同名不同实现时，不得靠猜测放宽后端', () => {
    // 记录约束：workflow.py 的 _MANAGER_ROLES 不含 teacher，auth.py 的 MANAGE_ROLES 含
    const backend = read('../backend/app/api/workflow.py')
    expect(backend).toMatch(/_MANAGER_ROLES\s*=\s*\{"admin",\s*"college_admin"\}/)
  })
})

describe('F-13 panel 参数不得静默失效', () => {
  it('review 不得再被映射到 submissions（那会让 URL 写 review 却高亮「提交与归档」）', () => {
    const workflow = read('src/views/Workflow.vue')
    // 只取 activeSection 自身的函数体，避免把相邻 computed 的注释一起圈进来
    const activeSectionBody = (workflow.match(/const activeSection = computed\(\(\) => \{[\s\S]*?\n\}\)/) || [''])[0]
    expect(activeSectionBody).not.toBe('')
    // 注释里可以解释历史，但可执行语句不得再出现 review -> submissions 的映射
    const executable = activeSectionBody.replace(/\/\/[^\n]*/g, '')
    expect(executable).not.toMatch(/'review'/)
    // review 的内容由 overview 区承载，必须落回 overview
    expect(executable).toMatch(/return 'overview'/)
  })

  it('tab 高亮必须按 URL 语义单独计算，不能直接复用 activeSection', () => {
    const workflow = read('src/views/Workflow.vue')
    // 教师/学生的「待审核」与 overview 共用 section，tab key 才是 'overview'
    expect(workflow).toContain('const activeTabKey = computed')
    expect(workflow).toContain("if (panel === 'messages') return 'messages'")
    expect(workflow).toContain("if (panel === 'submissions' || panel === 'archives') return canManage.value ? 'archives' : 'submissions'")
    // 模板高亮条件已从 section 改为 tab key
    expect(workflow).toContain(':class="{ active: activeTabKey === tab.key }"')
    expect(workflow).not.toContain(':class="{ active: activeSection === tab.section }"')
  })

  it('review 入口的标题必须是审核语义', () => {
    const workflow = read('src/views/Workflow.vue')
    expect(workflow).toMatch(/panelTarget\.value === 'review' \? t\('review\.pending'\)/)
    expect(workflow).toMatch(/panelTarget\.value === 'review' \? t\('workspace\.reviewDesk'\)/)
  })

  it('progress / materials 必须被显式识别并驱动标题', () => {
    const workflow = read('src/views/Workflow.vue')
    expect(workflow).toContain('panelFocused')
    expect(workflow).toMatch(/panelTarget\.value === 'progress'/)
    expect(workflow).toMatch(/panelTarget\.value === 'materials'/)
  })

  it('聚焦到 progress/materials 时，批次配置区必须让位', () => {
    const workflow = read('src/views/Workflow.vue')
    expect(workflow).toContain("activeSection === 'overview' && !panelFocused")
    // 过程材料收件箱与学生任务列表不能被隐藏，否则入口本身无内容可看
    expect(workflow).toContain("processInbox.length && activeSection === 'overview'")
    expect(workflow).toContain('v-else-if="activeSection === \'overview\'"')
  })
})

describe('F-16 图标字体与 ligature 名', () => {
  it('不得使用官方不存在的图标名', () => {
    // event_extended 不是 Material Symbols 图标，未知 ligature 会渲染成 __EXTENDED
    const all = []
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full)
        else if (/\.(vue|js|ts)$/.test(entry.name)) all.push([path.relative(root, full), fs.readFileSync(full, 'utf8')])
      }
    }
    walk(path.join(root, 'src'))
    for (const [file, text] of all) {
      expect(text, `${file} 仍含 event_extended`).not.toContain('event_extended')
    }
  })

  it('不得保留无效的字体降级 CSS', () => {
    // 先剥注释：说明文字里会引用这些写法本身，不能算作残留规则
    const css = read('src/style.css').replace(/\/\*[\s\S]*?\*\//g, '')
    // font-display 是 @font-face 描述符，在普通规则里无效
    expect(css).not.toMatch(/@supports\s*\(font-display/)
    // 加伪元素无法隐藏元素自身文本
    expect(css).not.toMatch(/\.material-symbols-outlined::before/)
  })

  it('真实降级由 iconFont.js 承担并在 main.js 启用', () => {
    const helper = read('src/shared/iconFont.js')
    expect(helper).toContain('pg-icons-unavailable')
    expect(helper).toContain('Material Symbols Outlined')
    expect(read('src/main.js')).toContain('applyIconFontFallback')
    expect(read('src/style.css')).toContain('.pg-icons-unavailable .material-symbols-outlined')
  })

  it('index.html 不得再声称有未实现的备用 CDN', () => {
    const html = read('index.html')
    expect(html).not.toContain('备用：如果 loli.net 也不可用')
  })
})

describe('F-18 / F-20 路由标题与角色门禁', () => {
  it('全部 titleKey 都有中英文案', () => {
    const router = read('src/router.js')
    const i18n = read('src/i18n.js')
    const keys = [...router.matchAll(/titleKey:\s*'([^']+)'/g)].map((m) => m[1])
    expect(keys.length).toBeGreaterThan(0)
    const missing = keys.filter((k) => !new RegExp(`'${k}':`).test(i18n))
    expect(missing).toEqual([])
  })

  it('/audit 与 /operations 必须声明角色，否则无权用户只会看到 403 空壳页面', () => {
    const router = read('src/router.js')
    for (const pathName of ['/audit', '/operations']) {
      // 必须用 "{ path: '/xxx'," 定位：直接找 "path: '/audit'" 会误命中
      // /workflow 那行里的 beforeEnter 重定向 { path: '/audit' }
      const line = router.split('\n').find((l) => l.includes(`{ path: '${pathName}',`))
      expect(line, `${pathName} 路由未找到`).toBeTruthy()
      expect(line, `${pathName} 缺少 roles 门禁`).toMatch(/roles:\s*\[/)
    }
  })

  it('写作规范页的外链必须与后端门槛一致', () => {
    const policies = read('src/views/Policies.vue')
    expect(policies).toContain('v-if="canUseTemplates"')
    expect(policies).toContain('v-if="canViewAudit"')
  })

  it('模板页「相关入口」同样不得对无权角色显示审计链接', () => {
    // F-20 的漏网：先修了 Policies.vue，Templates.vue 的同名链接被漏掉
    const templates = read('src/views/Templates.vue')
    expect(templates).toContain('v-if="canViewAudit" to="/audit"')
    expect(templates).toContain("const canViewAudit = computed(() => ['admin', 'college_admin']")
  })

  it('教师能到达的页面不得出现无条件的审计链接', () => {
    // 只检查教师真实可到达的页面。Cohorts/Operations/AdminWorkspace 也有无条件的
    // /audit 链接，但它们分别被路由 roles（/cohorts、/operations）与
    // Workspace.vue 的角色分发挡住，教师根本进不去那些页面，不属于本缺陷。
    const teacherReachable = ['src/views/Policies.vue', 'src/views/Templates.vue']
    const offenders = []
    for (const file of teacherReachable) {
      for (const line of read(file).split('\n')) {
        if (!line.includes('to="/audit"')) continue
        if (!line.includes('v-if')) offenders.push(`${file}: ${line.trim().slice(0, 60)}`)
      }
    }
    expect(offenders).toEqual([])
  })
})
