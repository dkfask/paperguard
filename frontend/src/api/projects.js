import { API_BASE, api, responseError } from './client'

export async function fetchProjects() {
  const resp = await api(`${API_BASE}/projects`)
  if (!resp.ok) throw await responseError(resp, '获取论文列表失败')
  return resp.json()
}

export async function fetchProject(id) {
  const resp = await api(`${API_BASE}/projects/${id}`)
  if (!resp.ok) throw await responseError(resp, '加载论文失败')
  return resp.json()
}

export async function createProject(templateId, title = '') {
  const resp = await api(`${API_BASE}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template_id: templateId, title }),
  })
  if (!resp.ok) throw await responseError(resp, '创建论文失败')
  return resp.json()
}

export async function updateProjectSection(projectId, sectionKey, data, baseRevision = null) {
  const body = { ...data }
  if (baseRevision !== null) body.base_revision = baseRevision
  const resp = await api(`${API_BASE}/projects/${projectId}/sections/${encodeURIComponent(sectionKey)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw await responseError(resp, '保存章节失败')
  return resp.json()
}

export async function updateCoverField(projectId, fieldKey, value, baseRevision = null) {
  const body = { value }
  if (baseRevision !== null) body.base_revision = baseRevision
  const resp = await api(`${API_BASE}/projects/${projectId}/cover/${encodeURIComponent(fieldKey)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw await responseError(resp, '保存封面字段失败')
  return resp.json()
}

export async function addProjectSection(projectId, parentKey, label) {
  const resp = await api(`${API_BASE}/projects/${projectId}/sections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parent_key: parentKey, label }),
  })
  if (!resp.ok) throw await responseError(resp, '添加章节失败')
  return resp.json()
}

export async function removeProjectSection(projectId, key) {
  const resp = await api(`${API_BASE}/projects/${projectId}/sections/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  })
  if (!resp.ok) throw await responseError(resp, '删除章节失败')
  return resp.json()
}

export async function moveProjectSection(projectId, key, direction) {
  const resp = await api(`${API_BASE}/projects/${projectId}/sections/${encodeURIComponent(key)}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ direction }),
  })
  if (!resp.ok) throw await responseError(resp, '移动章节失败')
  return resp.json()
}

export async function deleteProjectApi(id) {
  const resp = await api(`${API_BASE}/projects/${id}`, { method: 'DELETE' })
  if (!resp.ok) throw await responseError(resp, '删除论文失败')
}

export async function exportProjectDocx(id, background = false) {
  const query = background ? '?background=true' : ''
  const resp = await api(`${API_BASE}/projects/${id}/export${query}`, { method: 'POST' })
  if (!resp.ok) throw await responseError(resp, '导出失败')
  if (background) return resp.json()
  const blob = await resp.blob()
  return { blob, filename: decodeURIComponent(resp.headers.get('content-disposition')?.match(/filename\*?=['"]?([^'";]+)/)?.[1] || 'export.docx') }
}

export async function changeProjectTemplate(id, newTemplateId) {
  const resp = await api(`${API_BASE}/projects/${id}/change-template`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_template_id: newTemplateId }),
  })
  if (!resp.ok) throw await responseError(resp, '更换模板失败')
  return resp.json()
}

export async function checkProject(id) {
  const resp = await api(`${API_BASE}/projects/${id}/check`, { method: 'POST' })
  if (!resp.ok) throw await responseError(resp, '检查失败')
  return resp.json()
}

export async function fixProject(id) {
  const resp = await api(`${API_BASE}/projects/${id}/fix`, { method: 'POST' })
  if (!resp.ok) throw await responseError(resp, '一键修复失败')
  return resp.json()
}

export async function importDocxToProject(id, file) {
  const form = new FormData()
  form.append('file', file)
  const resp = await api(`${API_BASE}/projects/${id}/import-docx`, {
    method: 'POST',
    body: form,
  })
  if (!resp.ok) throw await responseError(resp, '导入失败')
  return resp.json()
}

export async function previewProjectImport(id, file) {
  const form = new FormData()
  form.append('file', file)
  const resp = await api(`${API_BASE}/projects/${id}/preview-import`, {
    method: 'POST',
    body: form,
  })
  if (!resp.ok) throw await responseError(resp, '预览失败')
  return resp.json()
}

export async function saveProjectAiConfig(id, config) {
  const resp = await api(`${API_BASE}/projects/${id}/ai-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!resp.ok) throw await responseError(resp, '保存 AI 配置失败')
  return resp.json()
}

export async function aiAssistProject(id, sectionKey, blockId, action, promptContext = {}) {
  const resp = await api(`${API_BASE}/projects/${id}/ai-assist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ section_key: sectionKey, block_id: blockId, action, context: promptContext }),
  })
  if (!resp.ok) throw await responseError(resp, 'AI 辅助失败')
  return resp.json()
}
