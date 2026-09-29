import { afterEach, describe, expect, it, vi } from 'vitest'

/**
 * 逐屏审查第三轮（生产复验）发现的错误文案缺陷。
 *
 * 后端 500 或反向代理返回的是纯文本（如 FastAPI 的 Internal Server Error），
 * resp.json() 解析失败后回退成空对象。detailMessage 此前对空对象直接走
 * JSON.stringify，把字面量 "{}" 当作错误信息抛给用户，页面上就出现了一段
 * 无法理解的 "{}"。空结构表示「没解析出错误信息」，应当回退到调用方给的
 * 可读文案。
 */
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function callWithResponse(makeResponse, run) {
  const fetchMock = vi.fn().mockResolvedValue(makeResponse())
  vi.stubGlobal('fetch', fetchMock)
  const { getTeacherStudents } = await import('../src/api.js')
  return run(getTeacherStudents)
}

describe('API 错误文案降级', () => {
  it('非 JSON 的 500 响应回退到可读文案，而不是字面量 {}', async () => {
    const error = await callWithResponse(
      () => new Response('Internal Server Error', { status: 500 }),
      (getTeacherStudents) => getTeacherStudents().catch((e) => e),
    )

    expect(error).toBeInstanceOf(Error)
    expect(error.message).toContain('获取指导学生列表失败')
    expect(error.message).toContain('500')
    expect(error.message).not.toBe('{}')
    expect(error.message).not.toContain('{}')
  })

  it('响应体是空 JSON 对象时同样回退到可读文案', async () => {
    const error = await callWithResponse(
      () => new Response('{}', { status: 502, headers: { 'Content-Type': 'application/json' } }),
      (getTeacherStudents) => getTeacherStudents().catch((e) => e),
    )

    expect(error.message).toContain('获取指导学生列表失败')
    expect(error.message).not.toContain('{}')
  })

  it('仍然优先展示后端给出的真实错误信息', async () => {
    const error = await callWithResponse(
      () => new Response(JSON.stringify({ detail: '指导关系指向的学生账号已被删除' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
      (getTeacherStudents) => getTeacherStudents().catch((e) => e),
    )

    expect(error.message).toBe('指导关系指向的学生账号已被删除')
  })

  it('结构化校验错误（detail 为非空数组）保留可诊断信息', async () => {
    const error = await callWithResponse(
      () => new Response(JSON.stringify({
        detail: [{ loc: ['query', 'cohort_id'], msg: 'field required' }],
      }), { status: 422, headers: { 'Content-Type': 'application/json' } }),
      (getTeacherStudents) => getTeacherStudents().catch((e) => e),
    )

    expect(error.message).toContain('cohort_id')
    expect(error.message).not.toContain('{}')
  })
})
