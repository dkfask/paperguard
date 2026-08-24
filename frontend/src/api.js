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

/** 上传 docx 并修复, 触发浏览器下载修复后的文件 */
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

/** 对写作项目实时跑格式检查 */
export async function checkProject(projectId, pack = 'GB-T-7713-1-2006') {
  const resp = await api(`${API_BASE}/projects/${projectId}/check?pack=${encodeURIComponent(pack)}`)
  if (!resp.ok) throw new Error('检查失败')
  return resp.json()
}

export async function deleteProject(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error('删除项目失败')
}

/** 上传 Word 模板, 识别其中的字体/字号/行距等样式 */
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
