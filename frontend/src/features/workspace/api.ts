import { fetchPacks } from '../../api.js'
import { projectsApi } from '../projects/api'
import { templatesApi } from '../templates/api'
import type { WorkspaceOverview } from '../../shared/api/types'

export async function fetchWorkspaceOverview(): Promise<WorkspaceOverview> {
  const [packs, templates, projects, deletedProjects] = await Promise.all([
    fetchPacks() as Promise<string[]>,
    templatesApi.list(),
    projectsApi.list(),
    projectsApi.listDeleted(),
  ])
  return { packs, templates, projects, deletedProjects }
}
