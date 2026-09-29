import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

/**
 * F-15 回归锁定：两套师生绑定机制必须合并展示，且不得顺带放宽权限。
 *
 * 背景：账号级 users.teacher_id（教师批量导入时写入）与批次级 supervision
 * （建批配置成员时写入）是两套独立数据源。教师工作台按后者取学生，
 * 指导学生页按前者取学生，导致同一教师看到两套学生集合、后者静默显示空列表。
 *
 * 修复只做「合并展示 + 标注来源 + 按真实权限禁用操作」，
 * 重置密码与解绑的校验依据（users.teacher_id）保持不变——放宽它是安全语义变更，
 * 不应由实现单方面决定。
 */

const frontend = path.resolve(process.cwd())
const backend = path.resolve(frontend, '..')
const readBackend = (rel) => fs.readFileSync(path.join(backend, rel), 'utf8')
const readFrontend = (rel) => fs.readFileSync(path.join(frontend, rel), 'utf8')

describe('F-15 师生绑定双源合并', () => {
  it('workflow 层对外暴露按教师查指导学生的只读函数', () => {
    const workflow = readBackend('backend/app/api/workflow.py')
    expect(workflow).toContain('def supervised_student_ids')
    // 生效判定要复用既有逻辑，不能另写一套时间判断
    expect(workflow).toMatch(/def supervised_student_ids[\s\S]*?_current_supervision\(store, cohort_id, student_id, now\)/)
  })

  it('指导学生接口同时返回账号绑定与批次指导两个来源', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    expect(teacher).toContain('supervised_student_ids(store, user["id"])')
    expect(teacher).toContain('"relation_sources"')
    expect(teacher).toContain('"can_manage_account"')
  })

  it('can_manage_account 只由账号级绑定决定，不得被批次来源置真', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    // 重置密码/解绑校验的是 users.teacher_id，批次来源不满足
    expect(teacher).toMatch(/"can_manage_account":\s*has_account_binding/)
  })

  it('重置密码与解绑的权限校验保持原样', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    // 这两处是安全边界，F-15 不得改动其判定条件
    expect(teacher).toMatch(/student\.get\("teacher_id"\) != user\["id"\]/)
    expect((teacher.match(/student\.get\("teacher_id"\) != user\["id"\]/g) || []).length).toBe(2)
  })

  it('指导关系指向已删除账号时跳过，不渲染空行', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    expect(teacher).toMatch(/if not s:\s*\n\s*#[\s\S]*?continue/)
  })

  it('前端标注关系来源，且只对可管理的行渲染操作按钮', () => {
    const view = readFrontend('src/views/TeacherStudents.vue')
    expect(view).toContain('v-if="student.can_manage_account"')
    expect(view).toContain('teacherStudents.relationAccount')
    expect(view).toContain('teacherStudents.relationCohort')
    // 批次来源的学生不能出现重置密码/解绑入口
    const actions = view.slice(view.indexOf('class="student-actions"'))
    expect(actions.indexOf('can_manage_account')).toBeLessThan(actions.indexOf('resetPassword(student)'))
  })

  it('页面解释两种关系来源的差别', () => {
    expect(readFrontend('src/views/TeacherStudents.vue')).toContain('teacherStudents.relationNote')
    expect(readFrontend('src/i18n.js')).toContain("'teacherStudents.relationNote'")
  })
})
