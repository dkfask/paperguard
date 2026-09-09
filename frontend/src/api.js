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
