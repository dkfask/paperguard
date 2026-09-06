import { auth, clearAuth } from './auth'

const API_BASE = '/api'

/** 带认证头的 fetch; 401 时清除会话并跳转登录页 */
async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (auth.token) headers['Authorization'] = 'Bearer ' + auth.token
  const resp = await fetch(url, { ...options, headers })
  if (resp.status === 401) {
    clearAuth()
    if (!location.pathname.startsWith('/login')) {
      const redirect = encodeURIComponent(location.pathname + location.search + location.hash)
      location.href = `/login?redirect=${redirect}`
    }
    throw new Error('请先登录')
  }
  return resp
}

function detailMessage(payload, fallback) {
  const value = payload?.detail ?? payload
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    if (typeof value.message === 'string') return value.message
    if (typeof value.detail === 'string') return value.detail
    try { return JSON.stringify(value) } catch { return fallback }
  }
  return fallback
}


export async function fetchPacks() {
  const resp = await api(`${API_BASE}/packs`)
  if (!resp.ok) throw new Error('获取规则包失败')
  return resp.json()
}

/** 上传 docx 并检查, 返回报告 JSON */
export async function checkDocx(file, pack) {
  const form = new FormData()
  form.append('file', file)
  form.append('pack', pack)
  const resp = await api(`${API_BASE}/check`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `检查失败 (${resp.status})`))
  }
  return resp.json()
}

export async function checkDocxWithTemplate(file, template) {
  const form = new FormData()
  form.append('file', file)
  form.append('template', template)
  const resp = await api(`${API_BASE}/check-template`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `模板检查失败 (${resp.status})`))
  }
  return resp.json()
}


export async function fixDocx(file, pack, filename) {
  const form = new FormData()
  form.append('file', file)
  form.append('pack', pack)
  form.append('filename', filename)
  const resp = await api(`${API_BASE}/fix`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `修复失败 (${resp.status})`))
  }
  const remaining = Number(resp.headers.get('x-paperguard-remaining-fixes'))
  await downloadBlob(resp, filename)
  return Number.isFinite(remaining) ? remaining : null
}

function filenameFromDisposition(header, fallback) {
  if (!header) return fallback
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (encoded) {
    try { return decodeURIComponent(encoded[1]) } catch { /* fallback */ }
  }
  const plain = header.match(/filename="?([^";]+)"?/i)
  return plain ? plain[1] : fallback
}

async function downloadBlob(resp, fallbackName) {
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filenameFromDisposition(resp.headers.get('content-disposition'), fallbackName)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
export async function logoutApi() {
  const resp = await api(`${API_BASE}/auth/logout`, { method: 'POST' })
  if (!resp.ok) throw new Error('退出登录失败')
}

export async function changePassword(currentPassword, newPassword) {
  const resp = await api(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.detail || '修改密码失败')
  }
}


export async function fetchTemplates() {
  const resp = await api(`${API_BASE}/templates`)
  if (!resp.ok) throw new Error('获取模板列表失败')
  return resp.json()
}

export async function fetchTemplate(id) {
  const resp = await api(`${API_BASE}/templates/${id}`)
  if (!resp.ok) throw new Error('获取模板失败')
  return resp.json()
}

export async function saveTemplate(tpl, isNew = false) {
  const resp = await api(`${API_BASE}/templates${tpl.id && !isNew ? '/' + tpl.id : ''}`, {
    method: isNew ? 'POST' : 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(tpl),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '保存模板失败'))
  }
  return resp.json()
}

export async function deleteTemplateApi(id) {
  const resp = await api(`${API_BASE}/templates/${id}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error('删除模板失败')
}

export async function fetchProjects() {
  const resp = await api(`${API_BASE}/projects`)
  if (!resp.ok) throw new Error('获取项目列表失败')
  return resp.json()
}

export async function fetchProject(id) {
  const resp = await api(`${API_BASE}/projects/${id}`)
  if (!resp.ok) throw new Error('获取项目失败')
  return resp.json()
}

export async function createProject(templateId, title = '') {
  const resp = await api(`${API_BASE}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template_id: templateId, title }),
  })
  if (!resp.ok) throw new Error('创建项目失败')
  return resp.json()
}

export async function updateProjectSection(projectId, sectionKey, data, baseRevision = null) {
  const body = { ...data }
  if (baseRevision !== null && baseRevision !== undefined) body.base_revision = baseRevision
  const resp = await api(`${API_BASE}/projects/${projectId}/section/${sectionKey}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    const err = new Error(detail.detail?.message || detail.detail || '保存章节失败')
    err.status = resp.status
    err.revision = detail.detail?.revision
    throw err
  }
  return resp.json()
}

export async function updateProjectMeta(projectId, meta, baseRevision = null) {
  const body = { meta }
  if (baseRevision !== null && baseRevision !== undefined) body.base_revision = baseRevision
  const resp = await api(`${API_BASE}/projects/${projectId}/meta`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    const err = new Error(detail.detail?.message || detail.detail || '保存封面信息失败')
    err.status = resp.status
    err.revision = detail.detail?.revision
    throw err
  }
  return resp.json()
}

export async function exportProject(projectId, force = false) {
  const resp = await api(`${API_BASE}/projects/${projectId}/export?force=${force}`, { method: 'POST' })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    const message = detailMessage(detail, '导出失败')
    const err = new Error(message)
    err.missing = Array.isArray(detail.detail?.missing) ? detail.detail.missing : []
    throw err
  }
  await downloadBlob(resp, 'paper.docx')
}

export async function checkProjectTemplate(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/template-check`)
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '模板检查失败'))
  }
  return resp.json()
}


export async function checkProject(projectId, pack = 'GB-T-7713-1-2006') {
  const resp = await api(`${API_BASE}/projects/${projectId}/check?pack=${encodeURIComponent(pack)}`)
  if (!resp.ok) throw new Error('检查失败')
  return resp.json()
}

export async function deleteProject(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error('删除项目失败')
}

export async function stripTemplateComments(file) {
  const form = new FormData()
  form.append('file', file)
  const resp = await api(`${API_BASE}/templates/strip-comments`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '批注清理失败'))
  }
  await downloadBlob(resp, `${file.name.replace(/\.docx$/i, '')}-no-comments.docx`)
  return {
    parts: Number(resp.headers.get('x-paperguard-removed-comment-parts')) || 0,
    anchors: Number(resp.headers.get('x-paperguard-removed-comment-anchors')) || 0,
  }
}


export async function importDocxTemplate(file) {
  const form = new FormData()
  form.append('file', file)
  const resp = await api(`${API_BASE}/templates/import`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.detail || `模板识别失败 (${resp.status})`)
  }
  return resp.json()
}

export async function getPrivateImageUrl(path) {
  const resp = await api(path)
  if (!resp.ok) throw new Error('图片加载失败')
  const blob = await resp.blob()
  return URL.createObjectURL(blob)
}


export async function uploadProjectImage(projectId, file) {
  const form = new FormData()
  form.append('file', file)
  const resp = await api(`${API_BASE}/projects/${projectId}/images`, {
    method: 'POST', body: form,
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.detail || '图片上传失败')
  }
  return resp.json()
}

// ---------- AI 写作助手 ----------

export async function aiStatus() {
  const resp = await api(`${API_BASE}/ai/status`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取 AI 状态失败'))
  return resp.json()
}

export async function getAiConfig() {
  const resp = await api(`${API_BASE}/ai/config`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取 AI 配置失败'))
  return resp.json()
}

export async function saveAiConfig(config) {
  const resp = await api(`${API_BASE}/ai/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `保存 AI 配置失败 (${resp.status})`))
  }
  return resp.json()
}

export async function clearAiConfig() {
  const resp = await api(`${API_BASE}/ai/config`, { method: 'DELETE' })
  if (!resp.ok && resp.status !== 204) throw new Error('清除 AI 配置失败')
}

export async function testAiConfig(config) {
  const resp = await api(`${API_BASE}/ai/config/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!resp.ok) throw new Error(`测试请求失败 (${resp.status})`)
  return resp.json()
}

// ---------- 管理员设置系统默认模型 ----------

export async function getAdminAiConfig() {
  const resp = await api(`${API_BASE}/ai/admin/config`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取系统 AI 配置失败'))
  return resp.json()
}

export async function saveAdminAiConfig(config) {
  const resp = await api(`${API_BASE}/ai/admin/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `保存系统默认 AI 配置失败 (${resp.status})`))
  }
  return resp.json()
}

export async function clearAdminAiConfig() {
  const resp = await api(`${API_BASE}/ai/admin/config`, { method: 'DELETE' })
  if (!resp.ok && resp.status !== 204) throw new Error('清除系统默认 AI 配置失败')
}

export async function testAdminAiConfig(config) {
  const resp = await api(`${API_BASE}/ai/admin/config/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!resp.ok) throw new Error(`测试系统默认模型失败 (${resp.status})`)
  return resp.json()
}

export async function aiAssist(payload) {
  const resp = await api(`${API_BASE}/ai/assist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `AI 调用失败 (${resp.status})`))
  }
  return resp.json()
}
