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

  it('can_manage_account（解绑）只由账号级绑定决定，不得被批次来源置真', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    // 解绑解除的是 users.teacher_id 这条账号绑定，批次指导挂在 supervision 上，
    // 不是这个端点能解除的东西，因此批次来源不能拿到解绑权限。
    expect(teacher).toMatch(/"can_manage_account":\s*has_account_binding/)
  })

  it('can_reset_password 认两种指导来源（账号绑定或批次指导）', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    // 经产品决策：教师本就拿得到这些学生的论文全文与进度，
    // 却管不了初始密码会形成「看得到、管不了」的不一致，故一并授权。
    expect(teacher).toMatch(/"can_reset_password":\s*has_account_binding or student_id in cohort_student_ids/)
  })

  it('重置密码的权限判定统一走 _is_supervised_student，不再直接比对 teacher_id', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    expect(teacher).toContain('def _is_supervised_student(')
    // 两种来源任一成立即视为本人指导
    expect(teacher).toMatch(/if student\.get\("teacher_id"\) == teacher_id:\s*\n\s*return True/)
    expect(teacher).toContain('return student["id"] in supervised_student_ids(store, teacher_id)')
    // 重置密码端点必须调用它
    expect(teacher).toMatch(/not _is_supervised_student\(store, user\["id"\], student\)/)
    // 解绑仍只认账号绑定，不能被同一个判定放宽
    expect(teacher).toMatch(/if user\["role"\] != "admin" and student\.get\("teacher_id"\) != user\["id"\]:\s*\n\s*raise HTTPException/)
  })

  it('指导关系指向已删除账号时跳过，不渲染空行', () => {
    const teacher = readBackend('backend/app/api/teacher.py')
    expect(teacher).toMatch(/if not s:\s*\n\s*#[\s\S]*?continue/)
  })

  it('前端按两个独立权限渲染重置密码与解绑', () => {
    const view = readFrontend('src/views/TeacherStudents.vue')
    expect(view).toContain('teacherStudents.relationAccount')
    expect(view).toContain('teacherStudents.relationCohort')
    const actions = view.slice(view.indexOf('class="student-actions"'), view.indexOf('</td>', view.indexOf('class="student-actions"')))
    // 重置密码认 can_reset_password，解绑认 can_manage_account，二者不得混用
    expect(actions).toContain('v-if="student.can_reset_password"')
    expect(actions).toContain('v-if="student.can_manage_account"')
    const resetAt = actions.indexOf('resetPassword(student)')
    const unbindAt = actions.indexOf('unbind(student)')
    expect(actions.lastIndexOf('can_reset_password', resetAt)).toBeGreaterThan(-1)
    expect(actions.lastIndexOf('can_manage_account', unbindAt)).toBeGreaterThan(-1)
    // 批次来源的兜底提示只应在两个权限都没有时出现
    expect(actions).toContain('v-if="!student.can_reset_password"')
  })

  it('页面解释两种关系来源的差别', () => {
    expect(readFrontend('src/views/TeacherStudents.vue')).toContain('teacherStudents.relationNote')
    expect(readFrontend('src/i18n.js')).toContain("'teacherStudents.relationNote'")
  })
})
