import { auth, clearAuth } from './auth'
import { translate } from './i18n'

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
    const requestId = resp.headers.get('x-request-id')
    const error = new Error(requestId ? translate('auth.sessionExpired', { requestId }) : translate('auth.sessionRequired'))
    error.status = 401
    error.requestId = requestId || ''
    throw error
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

async function responseError(resp, fallback) {
  const payload = await resp.json().catch(() => ({}))
  const requestId = resp.headers.get('x-request-id') || ''
  const message = detailMessage(payload, `${fallback} (${resp.status})`)
  const error = new Error(requestId ? `${message}（请求 ID: ${requestId}）` : message)
  error.status = resp.status
  error.requestId = requestId
  return error
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

export async function fetchPolicies() {
  const resp = await api(`${API_BASE}/policies`)
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '获取写作规范失败'))
  }
  return resp.json()
}

export async function fetchPolicy(id) {
  const resp = await api(`${API_BASE}/policies/${id}`)
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '获取写作规范失败'))
  }
  return resp.json()
}

export async function savePolicy(policy, isNew = false) {
  const url = isNew ? `${API_BASE}/policies` : `${API_BASE}/policies/${policy.id}`
  const resp = await api(url, {
    method: isNew ? 'POST' : 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(policy),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '保存写作规范失败'))
  }
  return resp.json()
}

export async function publishPolicy(id) {
  const resp = await api(`${API_BASE}/policies/${id}/publish`, { method: 'POST' })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '发布写作规范失败'))
  }
  return resp.json()
}

export async function archivePolicy(id) {
  const resp = await api(`${API_BASE}/policies/${id}/archive`, { method: 'POST' })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '停用写作规范失败'))
  }
  return resp.json()
}

export async function upgradePolicyProjects(id, payload = {}) {
  const resp = await api(`${API_BASE}/policies/${id}/upgrade-projects`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '批量升级项目规范失败'))
  return resp.json()
}

export async function submitPolicyReview(id) {
  const resp = await api(`${API_BASE}/policies/${id}/submit-review`, { method: 'POST' })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '提交规范审核失败'))
  }
  return resp.json()
}

export async function createPolicyVersion(id, payload) {
  const resp = await api(`${API_BASE}/policies/${id}/versions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '创建规范新版本失败'))
  }
  return resp.json()
}

export async function diffPolicy(id, against = '') {
  const query = against ? `?against=${encodeURIComponent(against)}` : ''
  const resp = await api(`${API_BASE}/policies/${id}/diff${query}`)
  if (!resp.ok) throw new Error('获取规范差异失败')
  return resp.json()
}

export async function addPolicyBinding(id, binding) {
  const resp = await api(`${API_BASE}/policies/${id}/bindings`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(binding),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '添加规范绑定失败'))
  return resp.json()
}

export async function deletePolicyBinding(id, bindingId) {
  const resp = await api(`${API_BASE}/policies/${id}/bindings/${bindingId}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '删除规范绑定失败'))
  return resp.json()
}

export async function uploadPolicyDocument(id, file) {
  const form = new FormData(); form.append('file', file)
  const resp = await api(`${API_BASE}/policies/${id}/documents`, { method: 'POST', body: form })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '上传规范文档失败'))
  return resp.json()
}

export async function importPolicyWithAi(file) {
  const form = new FormData(); form.append('file', file)
  const resp = await api(`${API_BASE}/policies/import`, { method: 'POST', body: form })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), 'AI 导入规范失败'))
  return resp.json()
}

export async function deletePolicyDocument(id, documentId) {
  const resp = await api(`${API_BASE}/policies/${id}/documents/${documentId}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '删除规范文档失败'))
  return resp.json()
}

export async function downloadPolicyDocument(id, documentId, fallbackName = 'requirement-document') {
  const resp = await api(`${API_BASE}/policies/${id}/documents/${documentId}`)
  if (!resp.ok) throw new Error('下载规范文档失败')
  await downloadBlob(resp, fallbackName)
}

export async function policyAudit(id) {
  const resp = await api(`${API_BASE}/policies/${id}/audit`)
  if (!resp.ok) throw new Error('获取规范审计记录失败')
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
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `删除模板失败 (${resp.status})`))
  }
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
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '创建项目失败'))
  }
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

export async function restoreProjectSnapshot(projectId, snapshot, baseRevision) {
  const resp = await api(`${API_BASE}/projects/${projectId}/restore-local`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ snapshot, base_revision: baseRevision }),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    const err = new Error(detailMessage(detail, '恢复本地草稿失败'))
    err.status = resp.status
    err.revision = detail.detail?.revision
    throw err
  }
  return resp.json()
}

export async function updateProjectSectionsStructure(projectId, sections, baseRevision = null) {
  const body = { sections }
  if (baseRevision !== null) body.base_revision = baseRevision
  const resp = await api(`${API_BASE}/projects/${projectId}/sections`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    const err = new Error(detailMessage(detail, '修改章节结构失败'))
    err.status = resp.status
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

export async function createProjectExportJob(projectId, force = false) {
  const resp = await api(`${API_BASE}/projects/${projectId}/export-jobs?force=${force}`, { method: 'POST' })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    const err = new Error(detailMessage(detail, '创建导出任务失败'))
    err.missing = Array.isArray(detail.detail?.missing) ? detail.detail.missing : []
    throw err
  }
  return resp.json()
}

export async function fetchProjectExportJobs(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/export-jobs`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取导出任务失败'))
  return resp.json()
}

export async function fetchProjectExportJob(projectId, exportId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/export-jobs/${exportId}`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取导出任务状态失败'))
  return resp.json()
}

export async function downloadProjectExportJob(projectId, exportId, fallbackName = 'paper.docx') {
  const resp = await api(`${API_BASE}/projects/${projectId}/export-jobs/${exportId}/download`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '下载导出文件失败'))
  await downloadBlob(resp, fallbackName)
}

export async function retryProjectExportJob(projectId, exportId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/export-jobs/${exportId}/retry`, { method: 'POST' })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '重试导出任务失败'))
  return resp.json()
}

export async function downloadAuditEvents(filters = {}) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim()) params.set(key, String(value).trim())
  })
  const query = params.toString()
  const resp = await api(`${API_BASE}/audit/export${query ? `?${query}` : ''}`)
  if (!resp.ok) throw await responseError(resp, '导出审计记录失败')
  await downloadBlob(resp, 'audit-events.csv')
}

export async function previewProjectExport(projectId, force = false) {
  const resp = await api(`${API_BASE}/projects/${projectId}/export-preview?force=${force}`, { method: 'POST' })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取导出预览失败'))
  return resp.json()
}

export async function importProjectDocx(projectId, file, apply = false, baseRevision = null) {
  const params = new URLSearchParams()
  if (apply) params.set('apply', 'true')
  if (baseRevision !== null && baseRevision !== undefined) params.set('base_revision', String(baseRevision))
  const form = new FormData()
  form.append('file', file)
  const query = params.toString()
  const resp = await api(`${API_BASE}/projects/${projectId}/import-docx${query ? `?${query}` : ''}`, {
    method: 'POST', body: form,
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    const err = new Error(detailMessage(detail, '导入 Word 正文失败'))
    err.status = resp.status
    err.revision = detail.detail?.revision
    throw err
  }
  return resp.json()
}

export async function checkProjectTemplate(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/template-check`)
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '模板检查失败'))
  }
  return resp.json()
}

export async function refreshProjectTemplate(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/refresh-template`, { method: 'POST' })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '重新套用模板失败'))
  }
  return resp.json()
}

export async function switchProjectTemplate(projectId, templateId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/template`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template_id: templateId }),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '更换模板失败'))
  }
  return resp.json()
}


export async function checkProject(projectId, pack = null) {
  const query = pack ? `?pack=${encodeURIComponent(pack)}` : ''
  const resp = await api(`${API_BASE}/projects/${projectId}/check${query}`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '检查失败'))
  return resp.json()
}

export async function fixProject(projectId, baseRevision) {
  const resp = await api(`${API_BASE}/projects/${projectId}/fix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_revision: baseRevision }),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '一键修复失败'))
  return resp.json()
}

export async function listProjectCheckRuns(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/check-runs`)
  if (!resp.ok) throw new Error('获取检查历史失败')
  return resp.json()
}

export async function previewRequirementUpgrade(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/upgrade-preview`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取规范升级预览失败'))
  return resp.json()
}

export async function upgradeRequirements(projectId, payload) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/upgrade`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, '应用规范升级失败'))
  }
  return resp.json()
}

export async function listRequirementWaivers(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/waivers`)
  if (!resp.ok) throw new Error('获取豁免记录失败')
  return resp.json()
}

export async function requestRequirementWaiver(projectId, itemId, payload) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/${itemId}/waiver`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '提交豁免申请失败'))
  return resp.json()
}

export async function decideRequirementWaiver(projectId, waiverId, payload) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/waivers/${waiverId}/decision`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '审核豁免失败'))
  return resp.json()
}

export async function revokeRequirementWaiver(projectId, waiverId, payload) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/waivers/${waiverId}/revoke`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '撤销豁免失败'))
  return resp.json()
}

export async function listRequirementDisputes(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/disputes`)
  if (!resp.ok) throw new Error('获取申诉记录失败')
  return resp.json()
}

export async function requestRequirementDispute(projectId, itemId, payload) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/${itemId}/dispute`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '提交申诉失败'))
  return resp.json()
}

export async function decideRequirementDispute(projectId, disputeId, payload) {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/disputes/${disputeId}/decision`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '审核申诉失败'))
  return resp.json()
}

export async function acknowledgeRequirements(projectId, snapshotHash = '') {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/acknowledge`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ snapshot_hash: snapshotHash }),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '确认规范失败'))
  return resp.json()
}

export async function assistRequirement(projectId, itemId, question = '') {
  const resp = await api(`${API_BASE}/projects/${projectId}/requirements/${itemId}/assist`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), 'AI 辅助检查失败'))
  return resp.json()
}

export async function fetchOrganizations() {
  const resp = await api(`${API_BASE}/organizations`)
  if (!resp.ok) throw new Error('获取组织列表失败')
  return resp.json()
}

export async function saveOrganization(unit, isNew = false) {
  const resp = await api(`${API_BASE}/organizations${isNew ? '' : '/' + unit.id}`, {
    method: isNew ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(unit),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '保存组织失败'))
  return resp.json()
}

export async function deleteOrganization(id) {
  const resp = await api(`${API_BASE}/organizations/${id}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '删除组织失败'))
  return resp.json()
}

export async function updateUserOrganizationContext(userId, organizationContext) {
  const resp = await api(`${API_BASE}/organizations/users/${userId}/context`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ organization_context: organizationContext }),
  })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '设置用户组织作用域失败'))
  return resp.json()
}

// ---------- 论文指导业务闭环 ----------

async function workflowJson(url, options = {}, fallback = '操作失败') {
  const resp = await api(url, options)
  if (!resp.ok) throw await responseError(resp, fallback)
  return resp.json()
}

export async function fetchCohorts() {
  return workflowJson(`${API_BASE}/cohorts`, {}, '获取批次失败')
}

export async function createCohort(payload) {
  return workflowJson(`${API_BASE}/cohorts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '创建批次失败')
}

export async function fetchCohort(cohortId) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}`, {}, '获取批次失败')
}

export async function addCohortMember(cohortId, payload) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/members`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '保存指导关系失败')
}

export async function importCohortMembers(cohortId, rows, dryRun = false) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/members/import`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows, dry_run: dryRun }),
  }, dryRun ? '成员导入预检失败' : '成员导入失败')
}

export async function createCohortStage(cohortId, payload) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/stages`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '创建阶段失败')
}

export async function previewCohortStage(stageId) {
  return workflowJson(`${API_BASE}/stages/${stageId}/preview`, { method: 'POST' }, '获取发布预览失败')
}

export async function publishCohortStage(stageId) {
  return workflowJson(`${API_BASE}/stages/${stageId}/publish`, { method: 'POST' }, '发布阶段失败')
}

export async function createStageVersion(stageId, payload) {
  return workflowJson(`${API_BASE}/stages/${stageId}/versions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '创建阶段升级版本失败')
}

export async function publishStageVersion(stageId, configId, payload) {
  return workflowJson(`${API_BASE}/stages/${stageId}/versions/${configId}/publish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '发布阶段升级版本失败')
}

export async function fetchMyTasks() {
  return workflowJson(`${API_BASE}/my-tasks`, {}, '获取阶段任务失败')
}

export async function fetchProcessCatalog() {
  return workflowJson(`${API_BASE}/process-records/catalog`, {}, '获取过程材料目录失败')
}

export async function fetchParticipantProcessRecords(participantId) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/process-records`, {}, '获取过程材料失败')
}

export async function fetchProcessRecordForm(participantId, artifactType) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/process-records/form/${encodeURIComponent(artifactType)}`, {}, '获取材料表单失败')
}

export async function fetchCohortProcessRecordForm(cohortId, artifactType) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/process-records/form/${encodeURIComponent(artifactType)}`, {}, '获取学院材料表单失败')
}

export async function createParticipantProcessRecord(participantId, payload) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/process-records`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '创建过程材料失败')
}

export async function exportProcessRecord(recordId) {
  const resp = await api(`${API_BASE}/process-records/${recordId}/export`, { method: 'POST' })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '导出过程材料失败'))
  await downloadBlob(resp, `${recordId}-filled.docx`)
}

export async function submitProcessRecord(recordId) {
  return workflowJson(`${API_BASE}/process-records/${recordId}/submit`, { method: 'POST' }, '提交过程材料失败')
}

export async function decideProcessRecord(recordId, payload) {
  return workflowJson(`${API_BASE}/process-records/${recordId}/decisions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '处理材料审批失败')
}

export async function uploadProcessRecordAttachment(recordId, file) {
  const form = new FormData()
  form.append('file', file)
  return workflowJson(`${API_BASE}/process-records/${recordId}/attachments`, { method: 'POST', body: form }, '上传材料附件失败')
}

export async function fetchParticipantQualification(participantId) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/qualification`, {}, '获取答辩资格失败')
}

export async function fetchParticipantScoreSummary(participantId) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/score-summary`, {}, '获取成绩汇总失败')
}

export async function fetchCohortProcessRecords(cohortId) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/process-records`, {}, '获取学院管理材料失败')
}

export async function fetchProcessRecordInbox() {
  return workflowJson(`${API_BASE}/process-records/inbox`, {}, '获取材料审批收件箱失败')
}

export async function createCohortProcessRecord(cohortId, payload) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/process-records`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '创建学院管理材料失败')
}

export async function downloadProcessRecordAttachment(fileId) {
  const resp = await api(`${API_BASE}/process-files/${fileId}/download`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '下载材料附件失败'))
  await downloadBlob(resp, fileId)
}

export async function fetchTask(participantId) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}`, {}, '获取任务详情失败')
}

export async function bindTaskProject(participantId, payload = {}) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/project`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '绑定主论文失败')
}

export async function acknowledgeTaskRequirements(participantId, snapshotHash = '') {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/acknowledge`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ snapshot_hash: snapshotHash }),
  }, '确认阶段要求失败')
}

export async function extendTaskDeadline(participantId, payload) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/extension`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '延期失败')
}

export async function createSubmissionCandidate(payload) {
  return workflowJson(`${API_BASE}/submission-candidates`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '准备提交失败')
}

export async function confirmSubmission(payload) {
  return workflowJson(`${API_BASE}/submissions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '确认提交失败')
}

export async function fetchSubmissions() {
  return workflowJson(`${API_BASE}/submissions`, {}, '获取提交历史失败')
}

export async function fetchSubmission(submissionId) {
  return workflowJson(`${API_BASE}/submissions/${submissionId}`, {}, '获取提交详情失败')
}

export async function fetchProjectVersions(projectId) {
  return workflowJson(`${API_BASE}/projects/${projectId}/versions`, {}, '获取论文历史版本失败')
}

export async function compareProjectVersions(projectId, leftVersionId, rightVersionId) {
  const params = new URLSearchParams({ left: leftVersionId, right: rightVersionId })
  return workflowJson(`${API_BASE}/projects/${projectId}/versions/compare?${params}`, {}, '获取版本差异失败')
}

export async function restoreProjectVersion(projectId, versionId, baseRevision = null) {
  return workflowJson(`${API_BASE}/projects/${projectId}/restore-version`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version_id: versionId, ...(baseRevision === null ? {} : { base_revision: baseRevision }) }),
  }, '恢复历史版本失败')
}

export async function createSubmissionComment(submissionId, payload) {
  return workflowJson(`${API_BASE}/submissions/${submissionId}/comments`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '新增批注失败')
}

export async function reviewSubmission(submissionId, payload) {
  return workflowJson(`${API_BASE}/submissions/${submissionId}/reviews`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, '提交审核决定失败')
}

export async function respondSubmissionComment(commentId, body) {
  return workflowJson(`${API_BASE}/comments/${commentId}/response`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body }),
  }, '回复批注失败')
}

export async function updateSubmissionCommentStatus(commentId, status) {
  return workflowJson(`${API_BASE}/comments/${commentId}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status, body: '' }),
  }, '更新批注状态失败')
}

export async function fetchWorkflowNotifications() {
  return workflowJson(`${API_BASE}/notifications`, {}, '获取消息失败')
}

export async function markWorkflowNotificationRead(eventId) {
  return workflowJson(`${API_BASE}/notifications/${eventId}/read`, { method: 'POST' }, '标记消息失败')
}

export async function fetchWorkflowReport(cohortId) {
  return workflowJson(`${API_BASE}/reports/cohorts/${cohortId}`, {}, '获取进度统计失败')
}

export async function createArchive(submissionId) {
  return workflowJson(`${API_BASE}/archives`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ submission_id: submissionId }),
  }, '生成归档失败')
}

export async function fetchArchives() {
  return workflowJson(`${API_BASE}/archives`, {}, '获取归档列表失败')
}

export async function fetchWorkflowJobs() {
  return workflowJson(`${API_BASE}/jobs`, {}, '获取后台任务失败')
}

export async function fetchWorkflowJob(jobId) {
  return workflowJson(`${API_BASE}/jobs/${jobId}`, {}, '获取后台任务状态失败')
}

export async function fetchAuditEvents(filters = {}) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && String(value).trim()) params.set(key, String(value).trim())
  }
  const query = params.toString()
  return workflowJson(`${API_BASE}/audit${query ? `?${query}` : ''}`, {}, '获取审计记录失败')
}

export async function fetchHealth(probe = 'ready') {
  const resp = await api(`${API_BASE}/health/${probe}`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '读取系统状态失败'))
  return resp.json()
}

export async function fetchRetentionPolicy() {
  return workflowJson(`${API_BASE}/operations/retention`, {}, '获取数据保留策略失败')
}

export async function updateRetentionPolicy(policy) {
  return workflowJson(`${API_BASE}/operations/retention`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(policy),
  }, '保存数据保留策略失败')
}

export async function downloadOperationsBackup() {
  const resp = await api(`${API_BASE}/operations/backup`, { method: 'POST' })
  if (!resp.ok) throw await responseError(resp, '生成备份失败')
  await downloadBlob(resp, 'paperguard-backup.zip')
}

export async function fetchArchive(archiveId) {
  return workflowJson(`${API_BASE}/archives/${archiveId}`, {}, '获取归档状态失败')
}

export async function retryArchive(archiveId, reason = '') {
  return workflowJson(`${API_BASE}/archives/${archiveId}/retry`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }),
  }, '重试归档失败')
}

export async function downloadArchive(archiveId) {
  const resp = await api(`${API_BASE}/archives/${archiveId}/download`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '下载归档失败'))
  await downloadBlob(resp, `${archiveId}.zip`)
}

export async function reopenStageParticipant(participantId, reason) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/reopen`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }),
  }, '重开阶段任务失败')
}

export async function exitStageParticipant(participantId, reason) {
  return workflowJson(`${API_BASE}/stage-participants/${participantId}/exit`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }),
  }, '退出阶段任务失败')
}

export async function closeCohort(cohortId, reason = '', force = false) {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/close`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason, force }),
  }, '关闭批次失败')
}

export async function reopenCohort(cohortId, reason = '') {
  return workflowJson(`${API_BASE}/cohorts/${cohortId}/reopen`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }),
  }, '恢复批次失败')
}

export async function deleteProject(projectId) {
  const resp = await api(`${API_BASE}/projects/${projectId}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '删除项目失败'))
}

export async function fetchDeletedProjects() {
  const resp = await api(`${API_BASE}/projects/trash`)
  if (!resp.ok) throw new Error(detailMessage(await resp.json().catch(() => ({})), '获取回收站失败'))
  return resp.json()
}

export async function restoreDeletedProject(projectId) {
  return workflowJson(`${API_BASE}/projects/${projectId}/restore`, { method: 'POST' }, '恢复论文失败')
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

// ---------- 用户管理与教师认证 (管理员) ----------

export async function getAdminUsers() {
  const resp = await api(`${API_BASE}/auth/admin/users`)
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `获取用户列表失败 (${resp.status})`))
  }
  return resp.json()
}

export async function updateAdminUserRoleStatus(userId, { role, status }) {
  const resp = await api(`${API_BASE}/auth/admin/users/${userId}/role-status`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, status }),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `更新用户权限状态失败 (${resp.status})`))
  }
  return resp.json()
}

// ---------- 教师指导学生与账号批量导入 ----------

export async function getTeacherStudents() {
  const resp = await api(`${API_BASE}/teacher/students`)
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `获取指导学生列表失败 (${resp.status})`))
  }
  return resp.json()
}

export async function bulkImportStudents(payload) {
  const resp = await api(`${API_BASE}/teacher/students/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `批量导入学生账号失败 (${resp.status})`))
  }
  return resp.json()
}

export async function resetTeacherStudentPassword(studentId, payload) {
  const resp = await api(`${API_BASE}/teacher/students/${studentId}/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `重置学生密码失败 (${resp.status})`))
  }
  return resp.json()
}

export async function unbindTeacherStudent(studentId) {
  const resp = await api(`${API_BASE}/teacher/students/${studentId}`, {
    method: 'DELETE',
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detailMessage(detail, `解除指导关系失败 (${resp.status})`))
  }
  return resp.json()
}

// ---------- 通用文档项目 ----------

async function postDocumentCandidate(path, file, fallback) {
  const form = new FormData()
  form.append('file', file, file.name)
  const resp = await api(`${API_BASE}${path}`, { method: 'POST', body: form })
  if (!resp.ok) throw await responseError(resp, fallback)
  return resp.json()
}

export async function parseDocumentTemplate(file) {
  return postDocumentCandidate('/templates/parse', file, '解析 Word 模板失败')
}

export async function getDocumentTemplateCandidates() {
  const resp = await api(`${API_BASE}/templates/parse/candidates`)
  if (!resp.ok) throw await responseError(resp, '获取模板解析候选失败')
  return resp.json()
}

export async function getDocumentTemplateCandidate(candidateId) {
  const resp = await api(`${API_BASE}/templates/parse/candidates/${encodeURIComponent(candidateId)}`)
  if (!resp.ok) throw await responseError(resp, '获取模板解析候选失败')
  return resp.json()
}

export async function confirmDocumentTemplateCandidate(candidate) {
  const resp = await api(`${API_BASE}/templates/parse/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(candidate),
  })
  if (!resp.ok) throw await responseError(resp, '确认 Word 模板失败')
  return resp.json()
}

export async function parseDocumentSpec(file) {
  return postDocumentCandidate('/specs/parse', file, '解析文档规范失败')
}

export async function getDocumentSpecCandidates() {
  const resp = await api(`${API_BASE}/specs/parse/candidates`)
  if (!resp.ok) throw await responseError(resp, '获取规范解析候选失败')
  return resp.json()
}

export async function getDocumentSpecCandidate(candidateId) {
  const resp = await api(`${API_BASE}/specs/parse/candidates/${encodeURIComponent(candidateId)}`)
  if (!resp.ok) throw await responseError(resp, '获取规范解析候选失败')
  return resp.json()
}

export async function confirmDocumentSpecCandidate(candidate) {
  const resp = await api(`${API_BASE}/specs/parse/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(candidate),
  })
  if (!resp.ok) throw await responseError(resp, '确认文档规范失败')
  return resp.json()
}

export async function publishDocumentPackage(payload) {
  const resp = await api(`${API_BASE}/document-packages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) throw await responseError(resp, '发布文档包失败')
  return resp.json()
}

export async function getDocumentPackages() {
  const resp = await api(`${API_BASE}/document-packages`)
  if (!resp.ok) throw await responseError(resp, '获取通用文档模板失败')
  return resp.json()
}

export async function getDocumentProjects() {
  const resp = await api(`${API_BASE}/document-projects`)
  if (!resp.ok) throw await responseError(resp, '获取通用文档项目失败')
  return resp.json()
}

export async function createDocumentProject(payload) {
  const resp = await api(`${API_BASE}/document-projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) throw await responseError(resp, '创建通用文档项目失败')
  return resp.json()
}

export async function getDocumentProjectEditor(projectId) {
  const resp = await api(`${API_BASE}/document-projects/${encodeURIComponent(projectId)}/editor`)
  if (!resp.ok) throw await responseError(resp, '读取通用文档项目失败')
  return resp.json()
}

export async function updateDocumentProject(projectId, payload) {
  const resp = await api(`${API_BASE}/document-projects/${encodeURIComponent(projectId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) throw await responseError(resp, '保存通用文档项目失败')
  return resp.json()
}

export async function uploadDocumentProjectImage(projectId, file) {
  const form = new FormData()
  form.append('file', file, file.name)
  const resp = await api(`${API_BASE}/document-projects/${encodeURIComponent(projectId)}/images`, {
    method: 'POST', body: form,
  })
  if (!resp.ok) throw await responseError(resp, '上传通用文档图片失败')
  return resp.json()
}

export async function validateDocumentProject(projectId) {
  const resp = await api(`${API_BASE}/document-projects/${encodeURIComponent(projectId)}/validate`, {
    method: 'POST',
  })
  if (!resp.ok) throw await responseError(resp, '检查通用文档项目失败')
  return resp.json()
}

export async function renderDocumentProject(projectId, force = false) {
  const query = force ? '?force=true' : ''
  const resp = await api(`${API_BASE}/document-projects/${encodeURIComponent(projectId)}/render${query}`, {
    method: 'POST',
  })
  if (!resp.ok) throw await responseError(resp, '导出通用文档失败')
  await downloadBlob(resp, 'document.docx')
}
