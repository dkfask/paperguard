import { ref, onMounted } from 'vue'

const API_BASE = '/api'

/** 获取规则包列表 */
export async function fetchPacks() {
  const resp = await fetch(`${API_BASE}/packs`)
  if (!resp.ok) throw new Error('获取规则包失败')
  return resp.json()
}

/** 上传 docx 并检查, 返回报告 JSON */
export async function checkDocx(file, pack) {
  const form = new FormData()
  form.append('file', file)
  form.append('pack', pack)
  const resp = await fetch(`${API_BASE}/check`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.detail || `检查失败 (${resp.status})`)
  }
  return resp.json()
}

/** 上传 docx 并修复, 触发浏览器下载修复后的文件 */
export async function fixDocx(file, pack, filename) {
  const form = new FormData()
  form.append('file', file)
  form.append('pack', pack)
  form.append('filename', filename)
  const resp = await fetch(`${API_BASE}/fix`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.detail || `修复失败 (${resp.status})`)
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// ---------------------------------------------------------------------------
// 模板与写作项目 API
// ---------------------------------------------------------------------------

export async function fetchTemplates() {
  const resp = await fetch(`${API_BASE}/templates`)
  if (!resp.ok) throw new Error('获取模板列表失败')
  return resp.json()
}

export async function fetchTemplate(id) {
  const resp = await fetch(`${API_BASE}/templates/${id}`)
  if (!resp.ok) throw new Error('获取模板失败')
  return resp.json()
}

export async function saveTemplate(tpl, isNew = false) {
  const resp = await fetch(`${API_BASE}/templates${tpl.id && !isNew ? '/' + tpl.id : ''}`, {
    method: isNew ? 'POST' : 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(tpl),
  })
  if (!resp.ok) throw new Error('保存模板失败')
  return resp.json()
}

export async function deleteTemplateApi(id) {
  const resp = await fetch(`${API_BASE}/templates/${id}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error('删除模板失败')
}

export async function fetchProjects() {
  const resp = await fetch(`${API_BASE}/projects`)
  if (!resp.ok) throw new Error('获取项目列表失败')
  return resp.json()
}

export async function fetchProject(id) {
  const resp = await fetch(`${API_BASE}/projects/${id}`)
  if (!resp.ok) throw new Error('获取项目失败')
  return resp.json()
}

export async function createProject(templateId, title = '') {
  const resp = await fetch(`${API_BASE}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template_id: templateId, title }),
  })
  if (!resp.ok) throw new Error('创建项目失败')
  return resp.json()
}

export async function updateProjectSection(projectId, sectionKey, data) {
  const resp = await fetch(`${API_BASE}/projects/${projectId}/section/${sectionKey}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!resp.ok) throw new Error('保存章节失败')
  return resp.json()
}

export async function updateProjectMeta(projectId, meta) {
  const resp = await fetch(`${API_BASE}/projects/${projectId}/meta`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ meta }),
  })
  if (!resp.ok) throw new Error('保存封面信息失败')
  return resp.json()
}

export async function exportProject(projectId) {
  const resp = await fetch(`${API_BASE}/projects/${projectId}/export`, { method: 'POST' })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.detail || '导出失败')
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'paper.docx'
  a.click()
  URL.revokeObjectURL(url)
}

export async function deleteProject(projectId) {
  const resp = await fetch(`${API_BASE}/projects/${projectId}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error('删除项目失败')
}
