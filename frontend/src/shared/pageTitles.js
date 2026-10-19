// A panel has one title in the page, shell breadcrumb and browser tab.
export function workflowPageTitle(panel, user, t) {
  const role = user?.role
  const manager = ['admin', 'college_admin'].includes(role)
  if (panel === 'messages') return t('workflow.messages')
  if (panel === 'reports' && manager) return t('workflow.reports')
  if (['submissions', 'archives'].includes(panel)) return t('workflow.submissionsArchives')
  if (panel === 'review') return t('workspace.reviewDesk')
  if (panel === 'progress') return t(role === 'student' ? 'workflow.myStageTasks' : 'workspace.studentProgress')
  if (panel === 'materials') return t('sidebar.materials')
  return t(manager ? 'workflow.cohortsStages' : role === 'teacher' ? 'workflow.teacherTodo' : 'workflow.myStageTasks')
}

export function pageTitle(route, user, t) {
  if (user?.role === 'teacher') {
    const key = { '/document-projects': 'personalDocuments.title', '/document-packages/publish': 'personalTemplates.title', '/templates': 'sidebar.publishThesisTemplate' }[route.path]
    if (key) return t(key)
  }
  if (route.path === '/workflow') return workflowPageTitle(String(route.query.panel || ''), user, t)
  if (route.path === '/workspace') {
    const key = { student: 'workspace.studentTitle', teacher: 'workspace.teacherTitle', admin: 'workspace.schoolTitle', college_admin: 'workspace.collegeTitle' }[user?.role]
    if (key) return t(key)
  }
  return route.meta.titleKey ? t(route.meta.titleKey) : (route.meta.title || t('shell.currentPage'))
}
